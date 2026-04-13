# S7 Battery-Saving HTTP Snapshot Path

**Author:** Claude Opus 4.6 (1M context)
**Date:** 13-April-2026
**Status:** Mac Mini side implemented in v2.24.0. Phone-side flip pending — S7 was offline all afternoon (dead battery / app killed). Once the phone is back up, Boss (or whoever is at the phone) follows `docs/13-Apr-2026-s7-phone-setup.md` and flips the config entry documented below.

## Problem

The S7 was streaming continuous RTSP via IP Webcam at `rtsp://192.168.0.249:5554/camera`. Two problems:

1. **Battery drain.** RTSP streaming forces the phone into continuous H.264 encoding. The camera ISP, the GPU (hardware encoder), and the radio all run at full power. An S7 with a worn battery (the unit in use here) can't sustain this — it bleeds charge faster than USB charging replaces it, overheats, and the IP Webcam service eventually dies. That's why every port on `192.168.0.249` is closed when the battery is depleted.
2. **Disk fill on the phone.** Not from Guardian (we only pull stream frames, we don't trigger local recordings), but any optional local recording toggle in IP Webcam or the stock camera app will steadily fill 32 GB of internal storage.

Boss directive (paraphrased 13-Apr-2026): *"just take a nice high-quality image every few seconds and send it to the Mac Mini and delete it locally. The only job this phone has is to serve as a camera."*

## Solution — pull HTTP snapshots from IP Webcam, drop RTSP

IP Webcam's HTTP server exposes:

- `GET http://<phone>:8080/photo.jpg` — returns a single JPEG from the current preview frame.
- `GET http://<phone>:8080/focus` — triggers autofocus (best-effort).

Pulling a still once every 5–10 s from the Mac Mini has three effects:

1. The phone's camera HAL can idle between shots — no sustained encoder load. Battery drain drops to roughly what a non-streaming app would use. Empirically, IP Webcam in HTTP-only mode keeps an S7 alive indefinitely off USB charge.
2. No RTSP reconnect churn, no decode-garbage rejection, no HEVC reference-packet loss. The JPEG is a complete image every time.
3. Nothing is written to phone storage. The photo exists only in RAM on the phone until it's served over HTTP; the Mac Mini archives/logs its own copy.

This is the pull-based architecture the codebase has always planned for — see `capture.py` v2.18.0 comment *"Phase B will add HttpUrlSnapshotSource"* and the `snapshot_method == "http_url"` fallthrough error that used to be in `guardian.py::_register_camera_capture`.

### Why pull, not push

The alternative considered was a push flow: intervalometer app on the phone writes JPEGs to internal storage, a sync app (FolderSync, Syncthing) uploads via SFTP to a watch directory on the Mac Mini, original file deleted after upload. Rejected because:

- Android 8 Doze / App Standby aggressively kills background sync services on older hardware. On an S7, a reliable unattended background uploader is a multi-day tuning project.
- Push requires the phone to hold SSH credentials and successfully authenticate to the Mac Mini on every upload. More moving parts, more silent-failure modes.
- Pull lets the Mac Mini drive cadence. If we want to burst during suspected motion later, one config flag on the server handles it; with push, the phone would need the same logic re-implemented locally.

The only thing pull *doesn't* do is take photos through the stock camera app's high-quality pipeline. IP Webcam's preview frame is what it is (720p–1080p, ~50–70% JPEG quality, no HDR). For the S7's role (coop interior observation of chickens and nesting-box visitors), that's fine. If we ever need actual camera-app-quality stills, we revisit — but not before the battery problem is solved and the camera is *reliably online*.

## Mac Mini side — v2.24.0 (shipped in this change)

### `capture.py::HttpUrlSnapshotSource`

New `SnapshotSource` implementing the existing Protocol. Plugs straight into `CameraSnapshotPoller` — same Protocol surface as `ReolinkSnapshotSource` and `UsbSnapshotSource`, so no changes to `CameraSnapshotPoller`, no changes to `FrameCaptureManager`, no changes to downstream detection / dashboard / alert paths.

Constructor parameters:

| Param | Meaning |
|---|---|
| `base_url` | e.g. `http://192.168.0.249:8080` |
| `photo_path` | default `/photo.jpg` |
| `focus_path` / `trigger_focus` / `focus_wait` | optional AF trigger before the photo. Default off — the phone's continuous AF usually handles it. Turn on if the coop shot drifts out of focus. |
| `timeout` | per-request timeout, default 15 s |
| `auth` | `(username, password)` tuple or `None` for no auth |

On each `fetch()`:

1. (If configured) fires the focus endpoint, sleeps `focus_wait` seconds. Swallows focus errors — the photo request still runs.
2. `GET {base_url}{photo_path}` with timeout + auth.
3. Validates non-empty response, JPEG SOI marker (`ff d8`). IP Webcam sometimes returns a 200 HTML error page when the preview isn't ready; this catches that and returns `None` cleanly.
4. Returns the raw JPEG bytes — `CameraSnapshotPoller` then decodes for YOLO *and* carries the original bytes through to the dashboard for zero-loss display (same behavior as the Reolink path).

`requests` is imported lazily inside `fetch()` rather than at module top. Not for correctness (`requests` is already in `requirements.txt` for Discord webhooks) — for style consistency with the rest of the file's deferred-import pattern.

### `guardian.py::_register_camera_capture`

Adds an `elif method == "http_url":` branch. Reads `http_base_url`, `http_photo_path`, `http_focus_path`, `http_trigger_focus`, `http_focus_wait`, `http_timeout` from the camera config. Uses `username`/`password` for basic auth if `username` is set; otherwise `auth=None`.

### `discovery.py::scan`

Short-circuits `snapshot_method == "http_url"` the same way USB is short-circuited: no ONVIF probe, no RTSP URL resolution. The camera is marked `online=True` in the registry and the real reachability check happens on the first `fetch()` in the poller — HTTP failures surface as the existing `consecutive_failures` warnings, not as a discovery-time hang.

## Phone-side flip (deferred — phone is offline)

Config entry to use once the phone is back up and IP Webcam is running with HTTP enabled:

```jsonc
{
  "name": "s7-cam",
  "ip": "192.168.0.249",
  "port": 8080,
  "username": "",          // or whatever is set in IP Webcam's "Login/password"
  "password": "",
  "type": "fixed",
  "source": "snapshot",
  "snapshot_method": "http_url",
  "http_base_url": "http://192.168.0.249:8080",
  "http_photo_path": "/photo.jpg",
  "http_trigger_focus": false,
  "snapshot_interval": 5.0,
  "detection_enabled": false
}
```

Config wasn't flipped in this commit because:

1. The phone was offline when this code was written — the current RTSP config cannot be validated either way.
2. The existing RTSP config fails gracefully (connection errors, not crashes). No user-visible regression from leaving it as-is.
3. Whoever re-seats the phone also needs to (re-)configure IP Webcam app settings (see phone setup doc) — flipping the server config first would just produce connection errors instead of a working camera.

Once the phone is online, the flip procedure is in `docs/13-Apr-2026-s7-phone-setup.md`.

## Scope — what's in, what's out

**In:**

- Generic `HttpUrlSnapshotSource` class (useful for any HTTP-snapshot camera, not just the S7 — GWTC Phase B will reuse it).
- Wiring in `guardian.py` and `discovery.py`.
- Header updates, changelog.
- Phone-side setup documentation.

**Out:**

- Changes to `config.json` (phone is offline; cannot validate).
- Changes to `tools/pipeline/capture.py::capture_ip_webcam` (already exists; the pipeline tool is a separate hot path).
- Intervalometer / native-camera-app / push architecture (rejected above).
- Battery-monitoring alerts (would be nice eventually; not the problem we're solving today).
- `motion_burst` wiring for `http_url` (no motion source on a phone; different class of problem).

## Verification

- `python -c "from capture import HttpUrlSnapshotSource"` — imports clean.
- `python -c "from guardian import GuardianService"` — no reference errors in the new dispatch branch.
- `python -c "from discovery import CameraDiscovery"` — short-circuit compiles.
- End-to-end verification against a live S7 is blocked by the phone being offline; phone-side doc records the manual smoke test to run at first boot.
