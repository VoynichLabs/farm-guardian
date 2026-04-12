# 12-Apr-2026 — Replace HLS Video with Snapshot Polling

**Author:** Claude Opus 4.6  
**Status:** Draft — awaiting approval  
**Version target:** v2.15.0

---

## Problem

Non-detection cameras (usb-cam, gwtc, s7-cam, house-yard when detection is off) currently run continuous **ffmpeg HLS pipelines**: each camera spawns an ffmpeg process that hardware-encodes 15fps H.264, segments it into 3-second HLS chunks, manages a playlist, and writes a periodic snapshot JPEG. The dashboard plays these via hls.js `<video>` tags.

This is over-engineered for the actual need:

1. **ffmpeg processes hang and crash.** The house-yard ffmpeg froze overnight (no output for 1+ hour), ignored SIGTERM, required SIGKILL. The watchdog didn't recover because the process was alive but stalled.
2. **Resource waste.** Three ffmpeg processes doing hardware H.264 encode for video nobody scrubs or rewinds.
3. **Tunnel bandwidth.** HLS segments are large; Cloudflare tunnel choked with 42,906 errors in the log. The tunnel works fine for small JPEG responses.
4. **Complexity.** 340 lines of ffmpeg process management (`stream.py`), watchdog threads, segment cleanup, HLS playlist serving, hls.js initialization in the frontend.

**Nobody needs live video.** A snapshot refreshed every 10–30 seconds serves the same monitoring purpose — you see what the camera sees, with an acceptable delay. This is especially true for the remote website through the Cloudflare tunnel where bandwidth matters.

---

## Scope

### In scope
- Replace HLS video pipeline with periodic JPEG snapshot polling for all non-detection cameras
- Simplify dashboard frontend: `<img>` tags with timed refresh instead of `<video>` + hls.js
- Simplify backend: remove HLS serving endpoints, keep `/api/cameras/{name}/frame`
- Remove `stream.py` (HLSStreamManager, HLSStream, ffmpeg orchestration)
- Update guardian.py to use the new snapshot approach instead of HLS

### Out of scope
- Detection pipeline (unchanged — still uses OpenCV capture when detection is enabled)
- Camera control, PTZ, alerts, reports, deterrence (unchanged)
- farm-2026 website changes (it already consumes `/api/cameras/{name}/frame` — will just work)

---

## Architecture

### Current flow (HLS)
```
RTSP/USB → ffmpeg (continuous) → HLS segments + latest.jpg → hls.js <video>
                                                            → /api/cameras/{name}/frame reads latest.jpg
```

### New flow (snapshot polling)
```
RTSP/USB → OpenCV grab (every N seconds) → in-memory JPEG → /api/cameras/{name}/frame
                                                           → <img> tag polls endpoint
```

### Snapshot capture strategy per camera type

| Camera | Source | Method |
|--------|--------|--------|
| **house-yard** (Reolink) | HTTP API | `camera_control.take_snapshot()` — already exists, returns JPEG bytes via `reolink_aio`. No RTSP needed. |
| **s7-cam** (RTSP) | RTSP single grab | OpenCV `VideoCapture.read()` — open, grab one frame, release. Or keep a persistent connection with long interval. |
| **usb-cam** (USB) | AVFoundation | OpenCV `VideoCapture(device_index)` — already proven in `capture.py`. Grab one frame per interval. |
| **gwtc** (RTSP) | RTSP single grab | Same as s7-cam — OpenCV single frame grab from `rtsp://192.168.0.68:8554/nestbox`. |

### Key design decision: reuse FrameCaptureManager vs. new SnapshotPoller

**Option A — Reuse `capture.py` FrameCaptureManager.** It already handles RTSP and USB cameras with per-camera threads, reconnection backoff, and frame buffering. Non-detection cameras would just run at a slower interval (e.g., 10s instead of 1s). The `/api/cameras/{name}/frame` endpoint already reads from it.

**Option B — New lightweight SnapshotPoller.** A simpler class — one thread per camera, grabs a single JPEG on a timer, stores it in memory. No ring buffer, no downscaling, no detection callback. Cleaner separation but duplicates some capture logic.

**Recommendation: Option A.** The FrameCaptureManager already does everything we need. We'd just:
- Start non-detection cameras in capture mode with a longer `frame_interval` (10s)
- Remove the HLS branch from guardian.py's startup logic
- The existing `/api/cameras/{name}/frame` endpoint already reads from the capture manager first

For house-yard specifically (Reolink PTZ with HTTP snapshot API), we could use `camera_control.take_snapshot()` instead of RTSP — it's lighter weight and doesn't hold an RTSP connection open. But this is an optimization, not a requirement. RTSP single-frame grabs work fine too.

---

## Changes by file

### `guardian.py` (~30 lines changed)
- In the camera startup loop (around line 189–220): remove the HLS branch. Start **all** cameras in the FrameCaptureManager, detection or not. Non-detection cameras get `frame_interval=10` (configurable).
- Remove `from stream import HLSStreamManager` import.
- Remove `self._hls_manager` creation and all references.
- Remove HLS manager from `start_dashboard()` call.
- Update `stop()` to not call `hls_manager.stop_all()`.

### `dashboard.py` (~50 lines changed)
- Remove `_hls_manager` module-level variable and all HLS references.
- Remove `/api/cameras/{name}/hls/{filename}` endpoint entirely.
- Simplify `/api/cameras/{name}/frame`: just read from capture manager (remove HLS fallback).
- In `/api/cameras` list: remove `stream_mode` field (everything is now snapshot-based) or set it to `"snapshot"` for all non-detection cameras.
- Remove `hls_manager` parameter from `start_dashboard()`.

### `static/app.js` (~40 lines changed)
- Remove `initHLSPlayers()` function entirely.
- Replace HLS `<video>` rendering with `<img>` tag pointing to `/api/cameras/{name}/frame`.
- Add a simple refresh timer: every 10 seconds, update each camera `<img>` src with a cache-busting query param (`?t=Date.now()`).
- Remove hls.js destroy/cleanup code.

### `static/index.html` (~1 line changed)
- Remove the hls.js CDN `<script>` tag.

### `stream.py` — DELETE
- Entire file removed. No longer needed.

### `capture.py` (~5 lines changed)
- Accept an optional `frame_interval` override per camera (currently hardcoded from config global). This lets non-detection cameras poll at 10s while detection cameras stay at 1s.

### `config.json` / `config.example.json`
- Add optional `snapshot_interval` per camera (default 10s).
- Remove `streaming` section (HLS config: `hls_output_dir`, `segment_duration`, etc.).

---

## TODOs (ordered)

1. **Update `capture.py`** — Allow per-camera `frame_interval` override when adding a camera.
2. **Update `guardian.py`** — Start all cameras via FrameCaptureManager. Non-detection cameras use `snapshot_interval` (default 10s). Remove all HLS manager code.
3. **Update `dashboard.py`** — Remove HLS endpoints and references. Simplify frame endpoint. Remove `hls_manager` parameter.
4. **Update `static/app.js`** — Replace HLS video player with polling `<img>` tags. Remove hls.js code.
5. **Update `static/index.html`** — Remove hls.js script tag.
6. **Delete `stream.py`** — No longer needed.
7. **Update `config.example.json`** — Remove `streaming` section, add per-camera `snapshot_interval`.
8. **Update CHANGELOG.md** — v2.15.0 entry.
9. **Test** — Verify all 4 cameras show frames on the local dashboard. Verify frames serve through the Cloudflare tunnel. Verify the farm-2026 website still loads camera images.

---

## What the farm-2026 website needs to know

The website already polls `/api/cameras/{name}/frame` for JPEG snapshots — that endpoint doesn't change. The HLS endpoints go away, but the website's Guardian components in `app/components/guardian/` should be checked for any HLS references. If the website was also using HLS, it would need to switch to `<img>` polling too. That's a separate PR in the farm-2026 repo.

---

## Verification

1. Start guardian.py — all 4 cameras should show "capturing=True" in `/api/cameras`.
2. Open `http://localhost:6530` — all camera tiles should show snapshot images refreshing every ~10 seconds.
3. `curl https://guardian.markbarney.net/api/cameras/{name}/frame` — should return fresh JPEG in <2 seconds for all cameras.
4. No ffmpeg processes running (`ps aux | grep ffmpeg` should be empty).
5. No `/tmp/guardian_hls/` directory activity.

---

## Risk

- **USB camera TCC.** OpenCV capturing the USB camera requires macOS Camera permission for the Terminal/claude-code process. This already works for ffmpeg via the current HLS path. Verify it still works for OpenCV `VideoCapture(0)`.
- **RTSP connection management.** Holding RTSP connections open for 10s intervals may waste resources vs. connect-grab-disconnect. Monitor and adjust. The capture manager's existing reconnection logic handles drops.
- **House-yard during detection-off.** When detection is disabled on house-yard, it's now in the capture manager at 10s intervals instead of ffmpeg HLS. If detection gets re-enabled, the interval switches back to 1s automatically. No conflict.
