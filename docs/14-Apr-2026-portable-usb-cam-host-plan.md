# Author: Claude Opus 4.6 (1M context)
# Date: 14-April-2026
# PURPOSE: Plan for making the generic USB webcam (`usb-cam`) host-portable
#          across any machine on the farm LAN. Today `usb-cam` is wired
#          directly into AVFoundation on the Mac Mini — `snapshot_method: usb`
#          assumes the camera is physically plugged into the same box that
#          runs Guardian. Boss wants to move the camera to the MacBook Air
#          (and, in principle, to any machine), which the current wiring
#          can't support. The plan stands up a tiny cross-platform snapshot
#          service that exposes the same camera over plain HTTP, so moving
#          the camera becomes: plug in, run the service, change one URL in
#          `config.json`.
# SRP/DRY check: Pass — reuses existing `HttpUrlSnapshotSource` (Guardian)
#          and existing `ip_webcam` capture method (pipeline). The new code
#          is one small host-side service; nothing in Guardian needs a new
#          code path.

# Portable USB Camera Host — v1 plan

## Scope (in)

- A small HTTP snapshot service (`tools/usb-cam-host/`) that runs on whichever machine physically hosts the generic USB webcam and exposes `GET /photo.jpg` returning a high-quality JPEG from the locally attached camera.
- Cross-platform: runs on macOS (Mac Mini "Bubba", MacBook Air 2013) and Windows (GWTC) without per-OS code forks. OpenCV's `cv2.VideoCapture(index)` auto-selects AVFoundation on macOS, dshow on Windows, V4L2 on Linux; the service leans on that.
- Deploy scaffolding for the two macOS targets (launchd LaunchAgent) and the Windows target (Shawl-wrapped batch file, same pattern as `deploy/gwtc/`).
- Switch both consumers of `usb-cam` off their local-AVFoundation paths and onto the existing HTTP snapshot paths:
  - **Guardian (`capture.py`)** — change `config.json`'s `usb-cam` block from `snapshot_method: "usb"` to `snapshot_method: "http_url"`. `HttpUrlSnapshotSource` already exists (v2.24.0, `capture.py:537`) and is already dispatched by `guardian.py:398`. Zero new code in Guardian.
  - **Image pipeline (`tools/pipeline/capture.py`)** — change `tools/pipeline/config.json`'s `usb-cam` block from `capture_method: "usb_avfoundation"` to `capture_method: "ip_webcam"`. `capture_ip_webcam` already exists (`tools/pipeline/capture.py:134`). Zero new code in the pipeline.
- `HARDWARE_INVENTORY.md` row updates and `CHANGELOG.md` top entry (SemVer minor — additive capability, no breaking change until the camera is physically moved).

## Scope (out)

- **Physical camera positioning.** The 2026-04-14 review of `data/archive/2026-04/usb-cam/*.json` shows every capture in the last hour is `image_quality: "soft"` with Laplacian variance 14–50. The sample frame at `2026-04-14T17-14-49-decent.jpg` shows a chick standing on the lens, well inside the camera's fixed-focus minimum distance, under a saturated heat lamp. No amount of warmup, sharpening, or frame ranking recovers that image. Standoff (10–30 cm), a cowl to keep pecking chicks off the lens, and a less-direct angle to the heat lamp are physical fixes Boss owns. The service will deliver the best frame the camera is physically capable of — not a better one.
- **Sharpness-based frame ranking inside the service.** The pipeline plan (`13-Apr-2026-multi-cam-image-pipeline-plan.md` §Trivial Gate) already calls out that Laplacian variance is a ranking signal only and not calibrated against GLM's own "sharp / soft" verdict. Boss reiterated the distrust today. The service will therefore **not** run a burst + Laplacian-pick; it warms the camera and returns a single frame. If a future consumer wants burst selection it can request multiple frames and rank them itself — this keeps the service's contract simple and keeps the Laplacian quirk out of the hot path.
- **Auth / TLS.** The snapshot service is LAN-only, same trust model as MediaMTX on GWTC and the MBA. Boss explicitly scoped the farm LAN as single-user trusted. No HTTPS, no basic auth.
- **Replacing `mba-cam` or `gwtc`.** Those cameras are their hosts' *built-in* webcams streaming over RTSP. They're a separate conversation. This plan only touches `usb-cam` — the generic external USB webcam that physically moves.
- **Multiple simultaneous cameras on one host.** The service is single-camera (device index 0 by default, configurable). If Boss later wants to plug a second USB cam into the same machine, we'll bump the service to take `device_index` on the URL (`/photo.jpg?device=1`) — out of scope for v1.

## Architecture

### Today (Mac Mini only)

```
┌──────────────────────────────────┐
│  Mac Mini                         │
│                                   │
│  USB webcam  ──► AVFoundation 0   │
│                   │               │
│                   ▼               │
│  guardian.py (UsbSnapshotSource)  │
│  pipeline/capture.py (capture_usb)│
└──────────────────────────────────┘
```

Both consumers open AVFoundation directly. Moving the camera breaks both.

### v1 (any host)

```
┌──────────────────────────────────┐        ┌──────────────────────────────┐
│  Host with USB camera             │        │  Mac Mini                     │
│  (Mini / MBA / GWTC / other)      │        │                               │
│                                   │        │                               │
│  USB webcam                       │        │  guardian.py                  │
│     │                             │        │   └─► HttpUrlSnapshotSource   │
│     ▼                             │  HTTP  │                               │
│  usb-cam-host service  ◄──────────┼────────┤  tools/pipeline/capture.py    │
│     (cv2.VideoCapture)            │        │   └─► capture_ip_webcam       │
│                                   │        │                               │
│     GET /photo.jpg  ──► JPEG      │        │                               │
└──────────────────────────────────┘        └──────────────────────────────┘
```

One service, two consumers, both using existing code paths.

### Service design (`tools/usb-cam-host/usb_cam_host.py`)

- FastAPI + Uvicorn. ~80 lines. Endpoints:
  - `GET /photo.jpg` → `image/jpeg`. Opens the camera, reads `warmup_frames` throwaway frames (AE/AWB/AF convergence under a heat lamp is slow — default 15 frames ≈ 1 s at 15 fps, materially longer than the current pipeline's 5 frames at 80 ms), grabs the keeper, encodes JPEG at quality 95, releases the capture, returns bytes.
  - `GET /health` → `{"ok": true, "device_index": N, "resolution": [W, H]}` — for deploy smoke tests and future monitoring.
- Configuration via env vars (no config file — single-service, small surface): `USB_CAM_DEVICE_INDEX` (default 0), `USB_CAM_WIDTH` (1920), `USB_CAM_HEIGHT` (1080), `USB_CAM_WARMUP` (15), `USB_CAM_JPEG_QUALITY` (95), `USB_CAM_PORT` (8089 — unused on all three hosts).
- Concurrency: one request at a time (FastAPI lifespan lock). Opening AVFoundation/dshow simultaneously from two handlers deadlocks the kernel driver on some boxes; serialization is safer and our request rate is low (Guardian pulls every 5 s, pipeline every 3 min).
- **No backend flag.** `cv2.VideoCapture(index)` alone — let OpenCV pick AVFoundation/dshow/V4L2 per-OS. The macOS-only `cv2.CAP_AVFOUNDATION` in today's `capture_usb` is the portability blocker; dropping it is the portability fix.
- **No Laplacian ranking.** Single warmed frame. Per Boss's distrust of the Laplacian-vs-GLM-sharpness calibration and the pipeline plan's existing note that Laplacian is a ranking signal only.
- **No autofocus trigger.** UVC AF on generic USB cams is unreliable and the current `CAP_PROP_AUTOFOCUS=1` flag has not produced measurably sharper frames (today's archive is uniformly soft). Warmup time alone is what we rely on.

### Service deploy artifacts (`deploy/usb-cam-host/`)

- `requirements.txt` — `fastapi`, `uvicorn[standard]`, `opencv-python`, `numpy`. Same versions the Mini already has; frozen for the MBA's Big Sur + Python 3.9 constraint.
- `com.farmguardian.usb-cam-host.plist` — macOS LaunchAgent (runs as the logged-in user, not root, because AVFoundation camera access is a per-user TCC grant). KeepAlive + RunAtLoad. `StandardOutPath`/`StandardErrorPath` under `/tmp/`. Installed to `~/Library/LaunchAgents/` on both the Mini and the MBA.
- `start-usb-cam-host.bat` — Windows startup script, same shape as GWTC's `start-camera.bat`.
- `install-macos.md` — step-by-step install, including the Camera TCC prompt approval (Boss needs to click once per host the first time the service opens the camera; the prompt fires on behalf of the logged-in user, which is why LaunchAgent > LaunchDaemon).
- `install-windows.md` — Shawl wrap instructions + firewall rule (Windows Firewall is DISABLED on GWTC per `network.md`, but noted for any future Windows host).

### TCC on macOS

The MBA will prompt for Camera access the first time the service opens AVFoundation. Because the LaunchAgent runs as the logged-in GUI user, the prompt will appear on-screen. Boss approves once. The grant persists. The install doc pre-warns about this so Boss knows what click he'll see. If prompt gets dismissed before approval, the service logs `AVFoundation device failed to open` and `/photo.jpg` returns 503 — not a silent failure.

## Why `http_url` / `ip_webcam` (not a new capture method)

`HttpUrlSnapshotSource` in `capture.py:537` and `capture_ip_webcam` in `tools/pipeline/capture.py:134` both already do exactly the right shape: HTTP GET a JPEG-serving endpoint, optional AF pre-trigger, sanity-check the JPEG SOI marker, return bytes. Adding a `http_snapshot_service` capture method just to call a different URL would be duplication with no behavior difference. Keep DRY — the new thing on the wire is the service; the consumer code stays the same.

The `trigger_focus` flag in `HttpUrlSnapshotSource` stays `False` for `usb-cam` — the service doesn't expose a focus endpoint and the camera doesn't honor AF anyway.

## Config changes

**`config.json`** (Guardian) — replace the `usb-cam` block:

```jsonc
{
  "name": "usb-cam",
  "type": "fixed",
  "source": "snapshot",
  "snapshot_method": "http_url",
  "http_base_url": "http://<host-ip>:8089",
  "http_photo_path": "/photo.jpg",
  "http_trigger_focus": false,
  "snapshot_interval": 5.0,
  "detection_enabled": false
}
```

`<host-ip>` starts as `192.168.0.71` (Mini) during bring-up, changes to `192.168.0.50` (MBA) when Boss moves the camera, and in principle could change to any LAN IP.

**`tools/pipeline/config.json`** — replace `usb-cam`'s capture block:

```jsonc
"usb-cam": {
  "cycle_seconds": 180,
  "capture_method": "ip_webcam",
  "ip_webcam_base": "http://<host-ip>:8089",
  "context": "Generic USB webcam, host-portable via usb-cam-host service; currently aimed at the brooder, heat-lamp lit",
  "burst_size": 1,
  "enabled": true
}
```

The `context` string drops the "plugged into the Mac Mini" claim because that's now a moving target — the `HARDWARE_INVENTORY.md` row is the single source of truth for "where it is today."

## TODOs (ordered)

1. **Bring-up on the Mac Mini (the camera's current host).** This is the no-change smoke test: the service runs on the same box the camera is already on, serves the same frames Guardian and the pipeline were seeing via local AVFoundation. If anything regresses here, it regresses against the current baseline and is easy to diagnose.
   - Write `tools/usb-cam-host/usb_cam_host.py` (the service).
   - Write `deploy/usb-cam-host/com.farmguardian.usb-cam-host.plist`.
   - Write `deploy/usb-cam-host/requirements.txt` + `install-macos.md` + `install-windows.md` + `start-usb-cam-host.bat`.
   - Install the LaunchAgent on the Mini, start it, verify `curl http://localhost:8089/health` and `curl http://localhost:8089/photo.jpg -o /tmp/u.jpg && file /tmp/u.jpg` (expect `JPEG ... 1920x1080`).
2. **Flip Guardian.** Edit `config.json`'s `usb-cam` block to `http_url` with the Mini's LAN IP. Restart Guardian. Verify `/api/cameras/usb-cam/frame` still returns a frame and the dashboard still shows it. Verify the pipeline still archives to `data/archive/2026-04/usb-cam/`.
3. **Flip the pipeline.** Edit `tools/pipeline/config.json`'s `usb-cam` block to `ip_webcam`. Restart the pipeline orchestrator. Verify the next cycle lands a new `*-decent.jpg` + `*-decent.json` pair.
4. **Confirm AVFoundation is no longer opened directly by Guardian/pipeline.** `lsof | grep "VDC.plugin"` on the Mini should show only the service's process, not `python` / `guardian.py` / the orchestrator.
5. **Docs — still on the Mini, camera still physically there.** Update `HARDWARE_INVENTORY.md`'s `usb-cam` row (capture method, source URL, "Currently aimed at" unchanged). Update `README.md` camera list if it enumerates methods. Top entry in `CHANGELOG.md` as `v2.26.0`. `"Last verified end-to-end"` stamp gets today's date.
6. **Boss moves the camera to the MBA.** When ready — no code change needed at this point:
   - SSH into MBA, install the LaunchAgent (`install-macos.md`), approve the Camera TCC prompt on the MBA GUI once.
   - Verify `curl http://192.168.0.50:8089/health` from the Mini.
   - Change `http_base_url` and `ip_webcam_base` in the two config files to `http://192.168.0.50:8089`. Restart Guardian + pipeline.
   - Update the `HARDWARE_INVENTORY.md` row: host machine = MBA, source URL IP = `.50`, "Currently aimed at" reflects wherever the camera is now pointed.
   - CHANGELOG line under the existing v2.26.0 entry: "camera physically relocated to MBA YYYY-MM-DD."

## Verification steps

Per step above — inline, real endpoints, no mocks:

- `curl -sS http://<host-ip>:8089/health | jq` → `{"ok": true, ...}`
- `curl -sS http://<host-ip>:8089/photo.jpg -o /tmp/u.jpg && file /tmp/u.jpg` → `JPEG ... 1920x1080` (or whatever the device reports — the health endpoint prints the negotiated resolution)
- `python3 -c "import cv2; print(cv2.Laplacian(cv2.cvtColor(cv2.imread('/tmp/u.jpg'), cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())"` — for a before/after sharpness comparison against the existing archive samples (ranking signal only per the pipeline plan; absolute values are diagnostic, not gating).
- After the Guardian flip: `curl -sS http://localhost:6530/api/cameras/usb-cam/frame -o /tmp/g.jpg && file /tmp/g.jpg` → `JPEG ... 1920x1080`, byte-close to the service response.
- After the pipeline flip: `ls -lt data/archive/2026-04/usb-cam/ | head` — new entry within one cycle.

## Docs / CHANGELOG touchpoints

- `CHANGELOG.md` — new top entry `## [2.26.0] - 2026-04-14`, describes: (1) new `tools/usb-cam-host/` service + `deploy/usb-cam-host/`, (2) `usb-cam` switched from `snapshot_method: "usb"` to `"http_url"` (Guardian) and `capture_method: "usb_avfoundation"` to `"ip_webcam"` (pipeline), (3) host-portability rationale, (4) explicit non-goals (Laplacian ranking, physical positioning).
- `HARDWARE_INVENTORY.md` — `usb-cam` row: host machine cell gets a note that it's now portable ("any host running `usb-cam-host`; currently Mac Mini"), `Source URL` becomes the service URL, `Capture method` becomes `snapshot_method: http_url` — and the "Adding a new camera" checklist gets a one-line pointer to `docs/14-Apr-2026-portable-usb-cam-host-plan.md` for future host-portable cameras.
- `README.md` — if it enumerates methods per camera, mirror the above.

## Risks / things to watch

1. **AVFoundation double-open.** If the LaunchAgent and Guardian's old `snapshot_method: "usb"` path ever run simultaneously against the same device index, the second one will fail with `Input/output error`. Mitigation: the flip in step 2 happens AFTER the service is verified in step 1; Guardian's old `UsbSnapshotSource` dispatch goes cold the moment `snapshot_method` flips to `http_url` because `guardian.py:383` is an `elif`.
2. **Request serialization vs. Guardian's 5 s cadence + pipeline's 3 min cycle + dashboard pulls.** Three concurrent consumers against a single-in-flight service. The warmup-then-grab is ~1.1 s per request; at worst two requests queue briefly. Acceptable — the alternative (concurrent AVFoundation opens) crashes the driver.
3. **MBA CPU headroom.** The 2013 Haswell i5 is already running `ffmpeg` + `MediaMTX` for `mba-cam` and is warm. Adding the snapshot service is cheap per-request (15 frames decoded then released) but each service open spins the webcam HAL. If Boss ends up running both `mba-cam` (continuous RTSP push) AND `usb-cam-host` on the MBA, watch `top` for sustained >70% CPU. Worst case: back off `usb-cam`'s Guardian interval from 5 s to 15 s.
4. **Windows deploy is untested in v1.** We ship the `.bat` + install doc because the architecture demands it, but we won't validate it until Boss asks to move the camera to GWTC or another Windows box. The install doc should be written assuming the first person to run it is a future Claude.
5. **If the physical image is still soft after all this**, that's the physical-positioning issue flagged under Scope (out) — it is NOT a regression in the service. The `data/archive/2026-04/usb-cam/` baseline today is already uniformly soft; anything at or above that bar is parity, and the only way to improve beyond parity is standoff + cowl + heat-lamp angle.
