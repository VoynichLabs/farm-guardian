# Per-Camera RTSP Transport — Connect S7 to Farm Guardian

**Date:** 06-Apr-2026
**Author:** Claude Opus 4.6
**Status:** Pending approval

## Problem

`guardian.py:17` globally forces `rtsp_transport;tcp` via `OPENCV_FFMPEG_CAPTURE_OPTIONS`. The Reolink needs TCP (HEVC/WiFi/UDP drops packets). The S7's RTSP Camera Server only supports UDP (TCP gives "Nonmatching transport in server reply"). Both cameras can't connect at the same time.

## Scope

**In scope:**
- Per-camera RTSP transport selection (TCP/UDP)
- Config, capture, guardian, dashboard changes to support it
- Both cameras online and streaming to the dashboard

**Out of scope:**
- Dashboard layout changes (cameras page already renders all cameras dynamically)
- S7 phone setup (already done — streaming on `rtsp://192.168.0.249:5554/camera`)
- Detection tuning for nesting-box (separate task after both cameras are live)

## What's Already Done (no changes needed)
- `config.json` — nesting-box entry with `rtsp_url_override` already present
- `discovery.py` — `rtsp_url_override` path skips ONVIF, marks camera online
- `dashboard.py` — `/api/cameras` returns all cameras, MJPEG stream endpoint works for any camera
- `static/app.js` — Cameras page renders a dynamic grid of all cameras with live feeds

## Architecture

OpenCV reads `OPENCV_FFMPEG_CAPTURE_OPTIONS` each time `cv2.VideoCapture()` is called (not just once at init). We set the env var with the correct transport before each connection, protected by a thread lock so simultaneous capture threads don't clobber each other.

## TODOs

### 1. `config.json` — add `rtsp_transport` per camera
- `house-yard`: `"rtsp_transport": "tcp"`
- `nesting-box`: `"rtsp_transport": "udp"`

### 2. `guardian.py:17` — remove global TCP transport
```
Before: os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;5000000"
After:  os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "stimeout;5000000"
```

### 3. `capture.py` — per-camera RTSP transport
- Module-level `_env_lock = threading.Lock()` for thread-safe env var mutation
- `CameraCapture.__init__` — accept `rtsp_transport` param (`"tcp"`, `"udp"`, or `None`)
- `_ensure_connected()` — acquire lock, set env var with camera's transport, create VideoCapture, restore env var, release lock
- `FrameCaptureManager.add_camera()` — accept and pass through `rtsp_transport`

### 4. `guardian.py` — pass transport to capture manager
- In `start()`: read `rtsp_transport` from per-camera config, pass to `add_camera()`
- In `_rescan_loop()`: same pattern for reconnected cameras

### 5. `dashboard.py` — pass transport on rescan/start
- `rescan_cameras` and `start_capture` endpoints look up per-camera config for transport

### 6. `config.example.json` — add `rtsp_transport` field to camera examples

### 7. `CHANGELOG.md` — v2.3.0 entry

## Docs/Changelog Touchpoints
- `CHANGELOG.md` — new version entry
- `CLAUDE.md` — remove the "RTSP transport fix — BLOCKING" TODO section after implementation

## Verification
1. `python guardian.py --debug`
2. Logs show both cameras online: house-yard (ONVIF/TCP) and nesting-box (manual RTSP/UDP)
3. Dashboard cameras page at `http://macmini:6530` shows both live feeds
4. Detections appear for both cameras in the detection timeline
