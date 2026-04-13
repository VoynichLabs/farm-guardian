# Phase B — Gateway laptop (gwtc) cam: HTTP snapshot endpoint replaces MediaMTX RTSP

**Author:** Claude Opus 4.6
**Date:** 13-April-2026
**Goal:** Stop using the Gateway laptop's MediaMTX RTSP stream as the source for the `gwtc` camera. Stand up a tiny HTTP snapshot service on the laptop side that returns a single high-resolution JPEG on demand, and switch Guardian's `gwtc` ingestion to poll it. This is Phase B of the three-phase shift to "use the cameras as cameras, not as video streams."

This plan is self-contained — a separate Claude session can pick it up and execute end-to-end.

**Depends on Phase A being merged** (it adds the `SnapshotSource` Protocol and `CameraSnapshotPoller` infrastructure that this phase plugs into).

---

## Why

The Gateway laptop (`gwtc`, `192.168.0.68`) is a Windows 11 machine with a built-in webcam, currently sitting in the chicken coop. Currently it runs `ffmpeg → MediaMTX` and exposes an RTSP stream at `rtsp://192.168.0.68:8554/gwtc` at 1280×720 H.264 ~1Mbps. Boss called the RTSP video quality "pretty awful" and observed correctly that the underlying webcam can do better as a still camera — most laptop webcams support stills at higher resolutions than they stream video at, and the JPEG encoder bypasses all the motion-compression artifacts that show up in the RTSP feed.

Phase A builds the `CameraSnapshotPoller` + `SnapshotSource` abstraction that makes plugging in a new HTTP snapshot source trivial on the Guardian side. The work in this phase is split:

1. **Laptop side (Windows):** stand up a tiny HTTP server that captures a fresh frame from the webcam at maximum stills resolution and returns it as JPEG.
2. **Guardian side (Mac Mini):** add an `HttpUrlSnapshotSource` adapter and switch `gwtc` config to use it.

---

## Verified facts going in

(From CLAUDE.md and the running config.)

- Gateway laptop IP: `192.168.0.68`. Windows 11. MediaMTX currently runs on port 8554 via Shawl service auto-start.
- Guardian config for `gwtc` today:
  ```jsonc
  {
    "name": "gwtc",
    "ip": "192.168.0.68",
    "port": 8554,
    "username": "",
    "password": "",
    "type": "fixed",
    "rtsp_transport": "tcp",
    "rtsp_url_override": "rtsp://192.168.0.68:8554/gwtc",
    "detection_enabled": false
  }
  ```
- Webcam max stills resolution is **not yet determined** — needs a one-time probe on the laptop side. Common built-in webcams support 1080p stills (1920×1080) or higher. The probe is part of this phase's TODOs.

**You will need physical/RDP access to the Gateway laptop to deploy the snapshot service.** Coordinate with Boss before assuming you can SSH in.

---

## Scope

**In:**

- A small Python HTTP service running on the Gateway laptop. Single endpoint: `GET /snap.jpg` returns JPEG bytes. Optional query param `?max_width=N` to clamp resolution if needed.
- The service runs as a Windows service via Shawl (same pattern MediaMTX already uses).
- New `HttpUrlSnapshotSource` adapter in `capture.py` that fetches a JPEG from an arbitrary URL.
- Update `gwtc` config to: `source: "snapshot"`, `snapshot_method: "http_url"`, `snapshot_url: "http://192.168.0.68:8555/snap.jpg"`, `snapshot_interval: 5.0`.
- Decommission MediaMTX on the laptop **only after** the snapshot service has been verified for at least 24h. Until then, run both side-by-side.
- CHANGELOG entry, CLAUDE.md updates, plan doc (this file).

**Out:**

- Reolink-specific anything (Phase A).
- USB-cam local on the Mac Mini (Phase C).
- Motion-triggered burst snapshots (Phase C).
- Replacing the laptop with different hardware. The webcam is what it is.

---

## Architecture

### Laptop side: `gwtc-snap.py`

```python
"""
Author: <model name>
Date: <timestamp>
PURPOSE: Tiny HTTP snapshot service for the Gateway laptop's built-in webcam.
         Holds a single OpenCV VideoCapture handle open at max stills resolution
         and returns a fresh JPEG on each GET /snap.jpg request. Replaces the
         ffmpeg→MediaMTX RTSP pipeline for the Guardian project — the chicken
         farm only needs stills, not video, and the webcam's stills mode produces
         better-quality images than its video mode.
SRP/DRY check: Pass — single responsibility is "expose webcam as JPEG over HTTP".
"""

import threading, time
from io import BytesIO
import cv2
from flask import Flask, Response, request

# Probe and pick the max stills resolution at startup.
# OpenCV uses MSMF backend on Windows by default. Some webcams need DSHOW.
_DEVICE_INDEX = 0
_TARGET_RESOLUTIONS = [(3840, 2160), (2560, 1440), (1920, 1080), (1280, 720)]

class WebcamHolder:
    def __init__(self):
        self._cap = None
        self._lock = threading.Lock()
        self._open()

    def _open(self):
        cap = cv2.VideoCapture(_DEVICE_INDEX, cv2.CAP_DSHOW)  # DSHOW is more reliable on Win11
        for w, h in _TARGET_RESOLUTIONS:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if (actual_w, actual_h) == (w, h):
                print(f"Opened webcam at {w}x{h}")
                break
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._cap = cap

    def grab(self):
        with self._lock:
            # Two reads — the first may be stale on some drivers
            self._cap.read()
            ret, frame = self._cap.read()
            if not ret or frame is None:
                # Try one reopen
                self._cap.release()
                self._open()
                ret, frame = self._cap.read()
            return frame if ret else None

holder = WebcamHolder()
app = Flask(__name__)

@app.get("/snap.jpg")
def snap():
    max_w = int(request.args.get("max_width", "0"))
    quality = int(request.args.get("q", "92"))
    frame = holder.grab()
    if frame is None:
        return Response("camera unavailable", status=503)
    if max_w and frame.shape[1] > max_w:
        scale = max_w / frame.shape[1]
        new_h = int(frame.shape[0] * scale)
        frame = cv2.resize(frame, (max_w, new_h), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return Response("encode failed", status=500)
    return Response(buf.tobytes(), mimetype="image/jpeg",
                    headers={"Cache-Control": "no-cache, no-store"})

@app.get("/health")
def health():
    return {"ok": True}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8555, threaded=True)
```

Run as a Windows service via Shawl, just like MediaMTX. Bind on port 8555 to coexist with MediaMTX on 8554 during the verification window.

### Mac Mini side: `HttpUrlSnapshotSource`

In `capture.py`, alongside `ReolinkSnapshotSource`:

```python
class HttpUrlSnapshotSource:
    """Fetches a JPEG from an arbitrary HTTP URL. Used for cameras that expose
    a snapshot endpoint over HTTP — the Gateway laptop's `gwtc` cam, IP webcams,
    etc. The fetch is synchronous (requests.get) and runs on the poller thread.
    """
    def __init__(self, url: str, timeout: float = 5.0, label: Optional[str] = None):
        self._url = url
        self._timeout = timeout
        self._label = label or url

    @property
    def label(self) -> str:
        return self._label

    def fetch(self) -> Optional[bytes]:
        try:
            r = requests.get(self._url, timeout=self._timeout)
            r.raise_for_status()
            ct = r.headers.get("Content-Type", "")
            if not ct.startswith("image/"):
                log.warning("HttpUrlSnapshotSource '%s' got non-image Content-Type=%r", self._label, ct)
                return None
            return r.content
        except requests.RequestException as exc:
            log.warning("HttpUrlSnapshotSource '%s' fetch failed: %s", self._label, exc)
            return None
```

### `guardian.py` dispatch

In the snapshot-mode branch added by Phase A, extend the `snapshot_method` switch:

```python
if method == "reolink":
    snap_src = ReolinkSnapshotSource(self._camera_ctrl, cam.name)
elif method == "http_url":
    url = cam_cfg.get("snapshot_url")
    if not url:
        log.error("Camera '%s' snapshot_method=http_url but no snapshot_url — skipping", cam.name)
        continue
    snap_src = HttpUrlSnapshotSource(url, label=f"http_url:{cam.name}")
else:
    log.error("Camera '%s' has unknown snapshot_method=%r — skipping", cam.name, method)
    continue
```

### Config change

```jsonc
{
  "name": "gwtc",
  "ip": "192.168.0.68",
  "type": "fixed",
  "source": "snapshot",
  "snapshot_method": "http_url",
  "snapshot_url": "http://192.168.0.68:8555/snap.jpg",
  "snapshot_interval": 5.0,
  "detection_enabled": false
}
```

Note `rtsp_url_override` is removed since RTSP is no longer the ingress.

---

## TODOs (ordered, with verification)

### Laptop side first (cannot be tested from Mac Mini until done)

1. **Coordinate access with Boss.** RDP / physical / scripted-deploy. Confirm Python 3.10+ is installed on the laptop and confirm OpenCV (`pip show opencv-python`).

2. **Probe the webcam's max stills resolution** via a one-shot Python script. Walk through the `_TARGET_RESOLUTIONS` list and report which actually opens. Save the result so the service config matches.

3. **Write `gwtc-snap.py`** (template above) with the verified resolution targets at the top. Test locally by running it and curling `http://localhost:8555/snap.jpg`. Verify dimensions are what you expected.

4. **Install Flask** (`pip install flask`) on the laptop if missing.

5. **Deploy as a Shawl service** alongside MediaMTX. Name it `gwtc-snap`. Configure auto-start. Verify it survives a reboot.

6. **From the Mac Mini, verify reachability:** `curl -o /tmp/gwtc.jpg http://192.168.0.68:8555/snap.jpg && python -c 'from PIL import Image; print(Image.open("/tmp/gwtc.jpg").size)'`. Confirm dimensions match the laptop probe.

### Guardian side

7. **Add `HttpUrlSnapshotSource`** in `capture.py`. Update file header.

8. **Extend the snapshot-mode dispatch** in `guardian.py` to handle `snapshot_method: "http_url"` (both initial setup loop and the periodic re-scan path — there are two call sites). Update header.

9. **Add `gwtc` snapshot config** in `config.json` (live) and `config.example.json` (template). Keep the old MediaMTX config commented out for ~24h as a safety net (or just trust git history).

10. **Restart Guardian.** Verify:
    - `pgrep -fl guardian.py` shows one process.
    - `curl -o /tmp/g.jpg http://localhost:6530/api/cameras/gwtc/frame` returns the high-res JPEG.
    - Dimensions match laptop probe.
    - No "frame read failed" entries on `gwtc` (those are RTSP-specific; should disappear entirely).

11. **24h soak.** Watch logs for snapshot fetch failures. Some are expected (laptop sleeps, network blips) — the poller logs and retries. Failure rate should be sub-1%.

12. **Decommission MediaMTX on the laptop.** Stop the Shawl service, disable auto-start. Remove the ffmpeg launcher. Document in CHANGELOG.

13. **Update `CLAUDE.md`** — `gwtc` description should now say "HTTP snapshot service on port 8555" instead of "MediaMTX RTSP on port 8554".

14. **CHANGELOG entry** with version bump (likely v2.19.0 if Phase A merged as v2.18.0). Cite this plan doc.

15. **Commit + push.**

---

## Risks / things to think about

- **Webcam reliability after long idle.** Some Windows webcams disable themselves to save power. The `WebcamHolder.grab()` retry-by-reopen handles this, but watch for it during the 24h soak.

- **Concurrent access from other apps.** Windows treats webcams as exclusive devices. If MediaMTX is also bound to the same camera, the snapshot service will fail. **MediaMTX must be stopped before the snapshot service can claim the webcam** — plan the cutover accordingly. There's no truly side-by-side period; you can leave MediaMTX *installed* but stop the service before starting `gwtc-snap`.

- **DSHOW vs MSMF backend.** If `cv2.CAP_DSHOW` doesn't pick the right device or set resolution correctly, try `cv2.CAP_MSMF`. Document whichever works.

- **Compression artifacts at high res.** If the JPEG quality default of 92 produces files Boss thinks are too large, drop to 85. The RestrictedDashboard transmits these via the Cloudflare tunnel, so file size matters slightly.

- **Snapshot latency.** A 1080p webcam still takes ~50–150ms to capture and encode locally. Add the network hop (LAN, ~5ms) and you're well under 200ms. Polling at 5s leaves enormous headroom.

- **Authentication.** None. Local network only. If we ever expose this beyond the LAN, add a shared-secret query param or HTTP basic auth.

- **Service lifecycle.** Shawl is the same pattern MediaMTX uses (per CLAUDE.md). Use the same install/start/auto-start commands so the operator (Boss or another agent) can manage them uniformly.

- **Detection on gwtc.** Today `detection_enabled: false`. That stays false — `gwtc` is the in-coop angle, not a predator-lookout. Just snapshots for Boss to peek at the chickens.

---

## Docs / Changelog touchpoints

- `CHANGELOG.md` — new top entry.
- `CLAUDE.md` — `gwtc` description, module-list updates if a new file appears.
- `docs/` — this plan stays as the historical record.
- `config.example.json` — `gwtc` example updated to snapshot mode.
- A short README in the laptop's deployment directory (next to `gwtc-snap.py`) describing how to install/start/restart the Shawl service.
