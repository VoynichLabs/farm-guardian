# Changelog

All notable changes to Farm Guardian are documented here. Follows [Semantic Versioning](https://semver.org/).

## [2.13.0] - 2026-04-11

### Added — HLS buffered streaming for non-detection cameras (Claude Opus 4.6)

- **`stream.py`** (new) — HLS stream manager. Runs ffmpeg subprocesses that capture RTSP/USB input, re-encode via VideoToolbox hardware H.264, and output HLS segments to `/tmp/guardian_hls/`. Each camera gets its own ffmpeg process monitored by a watchdog thread with exponential backoff. Segments auto-delete (only last 5 kept = ~15s buffer). Zero disk bloat.

- **`dashboard.py`** — Added `/api/cameras/{name}/hls/{filename}` endpoint to serve HLS playlists (.m3u8) and segments (.ts). Camera list now includes `stream_mode` field (`"hls"` or `"mjpeg"`) so the frontend knows which player to use.

- **`static/index.html`** — Added hls.js CDN script tag for browser HLS playback.

- **`static/app.js`** — Camera grids (dashboard and cameras page) now render `<video>` tags with hls.js for HLS cameras and `<img>` tags with MJPEG for detection cameras. HLS player auto-retries on connection failure. Cleans up hls.js instances on grid rebuild.

- **`guardian.py`** — Non-detection cameras (`detection_enabled: false`) now route to `HLSStreamManager` instead of OpenCV `FrameCaptureManager`. No double-connection — each camera uses one path. HLS manager cleaned up on shutdown.

- **`config.json`** — Added `streaming` section: `hls_output_dir`, `segment_duration`, `buffer_segments`, `video_bitrate`, `framerate`, `prefer_hw_encode`.

**Why:** The S7, USB, and GWTC camera live feeds had poor quality — frame loss, packet drops, artifacts from raw RTSP over WiFi. These cameras don't run detection, so latency doesn't matter. ffmpeg re-encodes into clean H.264 HLS segments with ~10s delay, producing smooth video. VideoToolbox hardware encoding on the M4 Pro means near-zero CPU cost. The Reolink (house-yard) keeps its existing MJPEG stream since it runs detection and needs low latency.

## [2.12.0] - 2026-04-10

### Added — GWTC laptop camera (4th camera) (Claude Opus 4.6)

- **`config.json`** — Added `gwtc` camera entry: the Gateway laptop (192.168.0.68) streams its built-in webcam over RTSP via ffmpeg + MediaMTX on port 8554. Config uses `rtsp_url_override` to connect to `rtsp://192.168.0.68:8554/nestbox`. Named `gwtc` (device name, not location) per project naming convention — this laptop will physically move to the chicken coop but may be repositioned later. Detection disabled initially. The S7 phone camera remains as a separate camera — both are active.

- **`config.example.json`** — Added example `gwtc` camera entry showing the RTSP-override pattern for MediaMTX-served webcam streams.

- **`CLAUDE.md`** — Environment section updated to document Camera 4 (GWTC). Architecture note updated for four-camera config.

**Why:** The GWTC (Gateway laptop) is being repurposed as a dedicated coop camera. Its built-in webcam streams 1280x720 @ 15fps H.264 (~1 Mbps) via ffmpeg → MediaMTX → RTSP. No code changes were needed — the existing `discovery.py` RTSP-override path, `capture.py` frame pipeline, and `guardian.py` dynamic camera iteration already support adding cameras purely via config. The dashboard frontend (`app.js`) dynamically renders any number of cameras from the `/api/cameras` endpoint.

## [2.11.0] - 2026-04-09

### Changed — Three-camera config: S7 restored, USB kept, cameras named by device (Claude Opus 4.6)

- **`config.json`** — Three cameras: house-yard (Reolink PTZ, ONVIF), s7-cam (Samsung S7 via IP Webcam RTSP override, UDP), usb-cam (local USB, AVFoundation device index 0). The S7 was not dead — it just needed charging. Cameras renamed from location-based names (nesting-box) to device-based names (s7-cam, usb-cam) so configs don't break when cameras move.

- **`config.example.json`** — Updated to show all three camera types with the new naming convention.

- **`CLAUDE.md`** — Environment section updated to reflect three cameras. Recent changes updated.

**Why:** The v2.9.0 release incorrectly assumed the S7 was dead and replaced it. It was only discharged. All cameras should be available — more cameras means more coverage. Device-based naming prevents config churn when cameras are repositioned.

## [2.10.0] - 2026-04-09

### Changed — 4K alert snapshots + sky-watch startup mode (Claude Opus 4.6)

- **`alerts.py`** — Alert images now prefer the camera's HTTP snapshot API (4K, sharp, focused) over RTSP buffer frames (1080p, often blurry from autofocus lag or HEVC decode artifacts). New `_capture_http_snapshot()` method fetches a JPEG via `camera_control.take_snapshot()`, scales detection bounding boxes from 1080p to 4K resolution (dynamic scale factor based on actual dimensions), and annotates with thicker lines/text for the higher resolution. Falls back to the existing RTSP frame path if the HTTP snapshot is unavailable. `AlertManager` now accepts an optional `camera_controller` parameter.

- **`guardian.py`** — `CameraController` initialization moved before `AlertManager` so it can be passed in for HTTP snapshot support. Added sky-watch startup: when `sky_watch.enabled` is true in config, the camera moves to a saved preset on startup and holds position (no patrol). Designed for fixed-position hawk surveillance — camera covers both yard and sky from one angle.

- **`config.json`** — Added `sky_watch` config section with `enabled`, `camera`, and `preset_id` fields. Disabled by default — requires a preset to be saved on the camera first via the preset API.

**Why:** Birdadette (Speckled Sussex hen) was taken by a hawk on 08-April. Alert images posted to Discord were consistently blurry — the RTSP stream delivers whatever the sensor sees, including out-of-focus frames during/after PTZ movement. The camera's HTTP snapshot API produces sharp 4K images every time. Patrol mode is excessive for a fixed homestead — a single well-aimed position gives continuous sky coverage for hawk detection.

## [2.9.0] - 2026-04-08

### Changed — Nesting box camera: USB replaces dead S7 phone (Claude Opus 4.6)

- **`config.json`** — Nesting box camera switched from Samsung Galaxy S7 RTSP stream (`192.168.0.249:5554`) to a local USB camera attached to the Mac Mini (`"source": "usb"`, `"device_index": 0`). The S7 died and will not come back.

- **`capture.py`** — Added USB camera support alongside RTSP. When `device_index` is provided, opens the camera via `cv2.VideoCapture(index)` using the native AVFoundation backend instead of RTSP/FFMPEG. Same frame processing pipeline (downscale, ring buffer, callbacks).

- **`discovery.py`** — Added USB source type. Cameras with `"source": "usb"` are marked online immediately during scan — no ONVIF probe or network check needed. New `source` and `device_index` fields on `CameraInfo`.

- **`guardian.py`** — Startup and re-scan loops now handle USB cameras: pass `device_index` to capture manager instead of RTSP URL.

**Why:** The Samsung Galaxy S7 used as the nesting box camera died. A USB camera is now physically connected to the Mac Mini pointing into the brooder box. The USB camera captures at 1920x1080 via AVFoundation — no network latency, no RTSP flakiness.

## [2.8.0] - 2026-04-08

### Added — PTZ preset save/recall via API (Claude Opus 4.6)

- **`camera_control.py`** — Added `ptz_save_preset(camera_id, preset_id, name)` that bypasses reolink_aio's `set_ptz_command()` validation (which rejects the `"setPos"` op) and calls `host.send_setting()` directly with the raw `PtzCtrl` body. Added `get_presets(camera_id)` to list saved presets. The Reolink E1 supports up to 64 presets — once saved, recall is instant and autonomous (no polling/overshooting).

- **`api.py`** — Three new endpoints:
  - `GET /cameras/{id}/presets` — list saved presets
  - `POST /cameras/{id}/preset/save` — save current position as preset: `{"id": 0, "name": "house"}`
  - `POST /cameras/{id}/preset/goto` — recall preset: `{"id": 0}` — camera moves autonomously

**Why:** Remote camera control via move/stop commands is unreliable over the internet (latency causes overshoot). Investigation by remote session confirmed absolute pan/tilt positioning is a Reolink firmware limitation (reolink_aio issue #147). Presets are the correct solution — save a position once, recall it instantly from anywhere. The old `ptz_save_preset` stub was removed in v2.5.0 because it failed validation; this version bypasses that validation layer.

## [2.7.0] - 2026-04-08

### Added — Remote camera control API endpoints (Claude Opus 4.6)

- **`api.py`** — Five new endpoints for full remote camera control:
  - `GET /api/v1/cameras/{id}/snapshot` — take JPEG snapshot, returns image bytes
  - `GET /api/v1/cameras/{id}/position` — read current pan (with degrees), tilt, zoom
  - `POST /api/v1/cameras/{id}/zoom` — set absolute zoom level (0–33)
  - `POST /api/v1/cameras/{id}/autofocus` — trigger autofocus cycle
  - `POST /api/v1/cameras/{id}/guard` — enable/disable PTZ guard (auto-return-to-home)
  - Removed dead `save_preset` action from PTZ endpoint (called non-existent method)

**Why:** A remote Claude session (via Railway/Cloudflare) needs to control the camera over the internet for setup and monitoring. The existing API had PTZ move/stop but was missing snapshot, position readback, zoom, autofocus, and guard control — the exact operations needed for remote camera setup.

## [2.6.0] - 2026-04-08

### Changed — Step-and-dwell patrol replaces continuous sweep (Claude Opus 4.6)

- **`patrol.py`** — Complete rewrite of patrol behavior. Instead of continuously panning at ~70°/s (which produced motion-blurred garbage frames), the camera now steps through 11 evenly-spaced positions (every 30°), stopping at each for 8 seconds of clean, stationary frame capture. Moves between positions at speed 8 with 0.3s position polling for precise placement. 3-second settle + autofocus at each stop. Dead zone positions (mounting post) are automatically skipped. Full patrol cycle takes ~2 minutes instead of 5 seconds.

- **`config.example.json`** — New `ptz.sweep` settings: `step_degrees`, `dwell_seconds`, `move_speed`, `settle_seconds`. Removed obsolete continuous sweep settings (`pan_speed`, `tilt_speed`, `tilt_burst_seconds`, `stall_threshold`, `start_pan`, `dead_zone_skip_speed`, `dwell_at_edge`).

**Why:** The continuous sweep moved so fast that every captured frame was motion-blurred. YOLO detection was running on useless images. The camera needs to be stationary to produce frames worth analyzing.

## [2.5.1] - 2026-04-08

### Fixed — Debug logging and file logging were silently broken (Claude Opus 4.6)

- **`guardian.py`** — `logging.basicConfig()` was called twice without `force=True`. The second call (which sets debug level and adds the file handler) was silently ignored by Python's logging module, so `--debug` never actually enabled DEBUG output and `guardian.log` was never written to after the first session. Added `force=True` to replace the bootstrap handler.

**Why:** Zero DEBUG messages were reaching logs, making it impossible to monitor patrol position data. The `guardian.log` file was stale from the first-ever session.

## [2.5.0] - 2026-04-08

### Fixed — Camera autofocus, PTZ guard disable, patrol cleanup (Claude Opus 4.6)

- **`camera_control.py`** — Added `ensure_autofocus()`, `trigger_autofocus()` to enable and force autofocus after zoom/movement changes. Added `is_guard_enabled()`, `disable_guard()`, `set_guard_position()` to control the Reolink PTZ guard (auto-return-to-home) feature. Removed dead `ptz_save_preset()` stub that logged a warning and did nothing.

- **`patrol.py`** — On patrol startup, disables PTZ guard so the camera stops auto-returning to pan=0 (the mounting post) during gaps in PTZ commands. Triggers autofocus after setting zoom on startup and after resuming from deterrent pause. Added startup position diagnostic logging (pan in degrees, dead zone check). Removed dead `tilt_steps` config variable that was read but never used. Replaced magic numbers (tolerance=200, speed=40) with config-driven `positioning_tolerance` and `positioning_speed`. Added Reolink E1 coordinate system documentation in comments. Upgraded dead zone entry/exit logging from DEBUG to INFO for visibility.

- **`config.example.json`** — Added full `ptz.sweep` block with all settings including new `positioning_tolerance` and `positioning_speed`. Added `patrol_mode` field.

**Why:** The Reolink E1's PTZ guard feature was returning the camera to pan=0 (the mounting post) every time there was a gap in PTZ commands — during dwells, tilt bursts, and pauses. The camera was spending most of its time staring at a wooden post instead of surveilling the yard. Additionally, the camera never refocused after zoom changes, producing blurry frames that made detection useless.

## [2.4.1] - 2026-04-07

### Fixed — Sweep patrol calibrated for Reolink E1 Outdoor Pro (Claude Opus 4.6)

- **`patrol.py`** — Calibrated sweep for Reolink's coordinate system (0–7240 units, not degrees). Patrol now pans to a configurable `start_pan` position on boot (opposite the mounting post). Tilt positioning replaced with timed bursts — the Reolink E1's tilt position readback is broken (always returns 945), so the old poll-and-nudge approach never converged. Dead zone support now active.

- **`config.json`** — Sweep config updated: `start_pan: 3620` (faces away from the mounting post at pan=0), `dead_zone_pan: [6800, 440]` (skips the narrow band behind the post where pan wraps), `tilt_burst_seconds: 1.5`. Removed `tilt_min`/`tilt_max` (useless without working tilt readback).

**Why:** The post the camera is mounted on sits directly behind the mount at pan=0. The patrol was starting there and staring at a blurry wooden post. Tilt positioning was spinning forever because it relied on position feedback that doesn't exist on this camera model.

## [2.4.0] - 2026-04-07

### Changed — Dashboard dual-feed layout, per-camera detection toggle (Claude Opus 4.6)

- **`static/index.html`** — Dashboard shows both camera feeds side-by-side at equal size. Removed PTZ controls from the dashboard (camera is on automated patrol). Compact status strip shows patrol/deterrent state.

- **`static/app.js`** — `renderDashboardFeed()` drives both feed panels from the cameras API response.

- **`guardian.py`** — Per-camera `detection_enabled` config flag. Cameras with `detection_enabled: false` skip YOLO inference entirely (feed-only mode). Rescan loop now starts sweep patrol and connects PTZ hardware when a PTZ camera comes online after initial startup.

- **`config.json`** — Nesting-box camera set to `detection_enabled: false` (monitoring chick, not detecting predators).

**Why:** Dashboard was hardcoded to one camera with PTZ controls. The nesting-box camera monitors a hatching chick — running predator detection on it wastes inference cycles and would generate false alerts. Patrol only started during initial boot — if the Reolink was slow to respond on first scan, patrol never kicked in.

## [2.3.2] - 2026-04-07

### Fixed — Lazy YOLO import unblocks Guardian startup (Claude Opus 4.6)

- **`detect.py`** — Moved `from ultralytics import YOLO` from module-level to inside `_load_model()`. The module-level import pulled in PyTorch (~60s on cold start) at import time, before `main()` or `start()` ever ran — making the v2.3.1 dashboard-before-discovery fix dead on arrival.

- **`guardian.py`** — Deferred `AnimalDetector` creation from `__init__()` to `start()`, after the dashboard is already serving. Added null guard in `_on_frame()` for the edge case where a dashboard API call starts capture before the detector finishes loading.

**Why:** The v2.3.1 fix moved `start_dashboard()` before camera discovery in `start()`, but `from detect import AnimalDetector` at the top of `guardian.py` triggered the full PyTorch import chain at module load — 60+ seconds before `start()` was ever called. Dashboard, API, and streams were all blocked. Now the dashboard comes up immediately, YOLO loads after, then cameras connect.

## [2.3.1] - 2026-04-07

### Fixed — Dashboard starts before camera discovery (Claude Opus 4.6)

- **`guardian.py`** — Moved `start_dashboard()` call to before camera discovery. ONVIF discovery can hang for 30+ seconds when the Reolink is slow to respond over WiFi, which blocked the entire API and stream endpoints from coming up. Dashboard now starts immediately after signal handlers, so the API is available while cameras connect in the background.

**Why:** Guardian appeared dead on the website because the dashboard/API wouldn't start until after ONVIF discovery completed (or timed out). With two cameras — one ONVIF, one manual RTSP — the discovery phase got even slower.

## [2.3.0] - 2026-04-06

### Fixed — Per-Camera RTSP Transport (Claude Opus 4.6)

- **`guardian.py`** — Removed global `rtsp_transport;tcp` from `OPENCV_FFMPEG_CAPTURE_OPTIONS`. Transport is now set per-camera in `capture.py` before each `VideoCapture()` call.

- **`capture.py`** — `CameraCapture` accepts `rtsp_transport` param (`"tcp"`, `"udp"`, or `None` for auto). Uses a module-level thread lock to safely swap the env var before creating each `VideoCapture`, preventing concurrent capture threads from clobbering each other's transport setting.

- **`dashboard.py`** — Rescan and start-capture endpoints now pass per-camera transport through to the capture manager.

- **`config.json` / `config.example.json`** — Added `rtsp_transport` field to each camera entry: `"tcp"` for Reolink (HEVC/WiFi needs TCP), `"udp"` for S7 (RTSP Camera Server only supports UDP).

**Why:** The Reolink needs TCP (HEVC over WiFi/UDP drops packets) but the S7's RTSP Camera Server only supports UDP. The old global TCP forced both cameras to use the same transport, blocking the S7 from connecting. Both cameras now connect simultaneously with their correct transport.

## [2.2.0] - 2026-04-06

### Added — Continuous Sweep Patrol (Claude Opus 4.6)

- **`patrol.py`** (new) — Continuous serpentine sweep patrol. The PTZ camera slowly pans across its full range, shifts tilt, reverses, and repeats — covering everything it can physically see. Uses continuous movement commands with position polling (reolink_aio has no absolute pan/tilt positioning). Configurable dead zone to skip the camera's own mounting point. Integrates with deterrent pause/resume.

- **`camera_control.py`** — Added position-readback methods: `get_pan_position()`, `get_tilt_position()`, `get_position()`, `get_zoom()`, and `set_zoom()`. These poll the camera's current PTZ state, enabling the sweep patrol to track where the camera is pointing.

- **`guardian.py`** — New `patrol_mode` config switch: `"sweep"` (default) for continuous sweep patrol, `"preset"` for the legacy preset-hopping patrol.

- **`config.json`** — New `ptz.sweep` configuration block with tunable pan/tilt speeds, tilt range, stall detection threshold, dead zone, and edge dwell time.

**Why:** The old preset-hopping patrol watched 5 fixed spots in rotation, leaving gaps between them. The sweep patrol scans everything the camera can see — no blind spots.

### Added — Manual RTSP Camera Support (Claude Opus 4.6)

- **`discovery.py`** — Cameras with `rtsp_url_override` in config now skip ONVIF discovery and go online immediately with the provided URL. Enables non-ONVIF cameras (phones, software encoders) to integrate as fixed cameras.

- **`config.json`** — Added `nesting-box` camera entry pointing to Samsung Galaxy S7 running RTSP Camera Server (com.miv.rtspcamera) at `rtsp://192.168.0.249:5554/camera`.

**Why:** Repurposed a Samsung Galaxy S7 (SM-G930F, Android 8.0.0) as a dedicated nesting box camera for incubator chick monitoring. Phone was factory-reset, Samsung bloatware disabled, configured for always-on kiosk mode (max brightness, stay awake on power, no screen timeout).

## [2.1.1] - 2026-04-05

### Added — CORS for Farm Website (Claude Opus 4.6)

- **`dashboard.py`** — Added `CORSMiddleware` allowing `https://farm.markbarney.net` to make GET and POST requests to the Guardian API. Enables PTZ controls, spotlight, and siren from the farm website's live Guardian dashboard page.

**Why:** The farm website (`farm.markbarney.net`) now has an interactive Guardian dashboard that needs to POST to the Guardian API for camera controls. Browser preflight (OPTIONS) requests were failing without CORS headers.

## [2.1.0] - 2026-04-04

### Fixed — Stabilization & Cleanup (Claude Opus 4.6)

- **config.json** — Reset `confidence_threshold` from 0.99 → 0.45, restoring detection.
  Scrubbed real camera password and Discord webhook from tracked config (now `.gitignore`d).

- **`.gitignore`** — Added `.claude/` to existing ignore rules.

- **`discovery.py`** — Camera RTSP URLs containing credentials are now masked in log
  output (`rtsp://admin:***@...`). Previously, the plaintext password was logged every
  5 minutes during camera rescans.

- **`capture.py`** — Switched RTSP transport from UDP to TCP via
  `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp`. HEVC over WiFi/UDP was dropping
  every ~30 seconds due to packet loss and MTU fragmentation. Also set 5-second read
  timeout (down from 30s default) to speed up reconnection after stream drops.

- **`tracker.py`** — Ghost tracks (single-frame false positives) are now deleted from
  the DB when they close with fewer detections than `min_detections_for_track` (default 2).
  Previously, bear/dog flickers created 0.0s-duration, 1-detection tracks that polluted
  the database.

- **`database.py`** — Added `delete_track()` method for ghost track cleanup.

- **`guardian.py`** — Added `python-dotenv` integration. Secrets (camera password,
  Discord webhook, eBird API key) are now loaded from `.env` and overlaid onto
  `config.json` at startup. Env vars take precedence over config file values,
  so `config.json` can stay sanitized in git while `.env` holds real credentials.

- **`.env` / `.env.example`** — Created `.env` for local secrets (camera password,
  Discord webhook, eBird API key, Cloudflare Tunnel token). `.env.example` committed
  as a template. Both `.env` and `config.json` are `.gitignore`d.

- **`requirements.txt`** — Added `python-dotenv>=1.0.0`.

- **Cloudflare Tunnel** — Fixed by switching from QUIC (UDP 7844, blocked by router/ISP)
  to HTTP/2 (TCP 443). Tunnel now stable — 4 connections to Cloudflare IAD, `guardian.markbarney.net`
  returns 200. LaunchAgent loaded and persists across reboots.

- **`capture.py`** — RTSP stream stability overhaul. Replaced OpenCV's built-in 30-second
  read timeout (hardcoded in the FFMPEG backend, not configurable) with a threaded 10-second
  manual timeout on `cap.read()`. When a read hangs, the old VideoCapture is abandoned (not
  released — releasing while read() is blocking in native code causes a segfault) and a fresh
  connection is created. Result: stream runs continuously, reconnects in 10s instead of 30s,
  no process crashes.

- **`guardian.py`** — Set `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp` at the top of
  the file (before any `cv2` import) to force RTSP over TCP. HEVC over WiFi/UDP was dropping
  due to packet loss and MTU fragmentation.

### Phase C — WIP Commit Review

- Reviewed all 969 lines across 7 files in the WIP commit (edab3c5). All changes are
  complete and production-ready: dashboard redesign (Bloomberg-terminal aesthetic),
  camera_control port fix, dashboard DB queries, CHANGELOG entry. No reverts needed.

## [2.0.1] - 2026-04-03

### Fixed — PTZ + Dashboard Overhaul (Claude Sonnet 4.6 / Opus 4.6)

- **camera_control.py** — Fixed port from 443 → 80 for Reolink HTTP API. Fixed `connect_camera()`
  to construct `Host()` inside the async event loop thread (resolves "no running event loop" error).
  Fixed `ptz_move()` to use reolink_aio direction strings (Left/Right/Up/Down/ZoomInc/ZoomDec)
  instead of pan/tilt/zoom float params. Added speed fallback — camera "FarmGuardian1" doesn't
  support speed parameter, retry without it.

- **dashboard.py** — `get_status()` now queries DB directly for `detections_today` and `alerts_today`
  instead of relying on in-memory buffers that reset to 0 on restart. `/api/detections/recent`
  falls back to DB when in-memory buffer is empty.

- **static/index.html + app.js** — Full dashboard redesign: killed fat stat cards, replaced with
  compact single-line status bar (uptime, frames, detections, last detection). Camera feed (63%)
  + PTZ d-pad panel (37%) side by side. Compact detections table below feed (12 rows max).
  Sidebar collapsed to 44px icon-only. Bloomberg-terminal aesthetic. All info on one screen.

### Operational Note
- Detection paused at 22:08 EDT 03-April-2026 (confidence set to 0.99) while camera is
  temporarily placed in nesting box inside coop. Re-enable by setting confidence back to 0.45.

## [2.0.0-beta] - 2026-04-03

### Added — Phase 3: Camera Control + Deterrence (Claude Opus 4.6)

- **`camera_control.py` (enhanced)** — Added timed spotlight/siren helpers (`spotlight_timed`,
  `siren_timed`) for auto-off after duration. Patrol loop now accepts a `pause_event` so
  the deterrent engine can pause patrol during active predator tracking and resume after.

- **`deterrent.py`** — Automated deterrent response engine with 4 escalation levels:
  Level 0 (log only), Level 1 (spotlight), Level 2 (spotlight + audio alarm),
  Level 3 (spotlight + siren + audio alarm). Per-species response rules configurable in
  config.json. Enforces cooldown between activations per species (default 5 min).
  Tracks effectiveness — monitors whether animal leaves within 60s of deterrent.
  Pauses PTZ patrol during active deterrence. All actions logged to deterrent_actions table.

- **`ebird.py`** — eBird API polling for regional raptor activity near Hampton CT.
  Polls Cornell Lab's eBird Recent Observations API every 30 minutes during hawk hours
  (8am-4pm). Sends Discord alerts for HIGH/MEDIUM threat raptors with 2-hour cooldown.
  Logs all sightings to ebird_sightings table. Tracks 10 raptor species with threat levels.

### Added — Phase 4: Intelligence + Reporting (Claude Opus 4.6)

- **`reports.py`** — Daily intelligence report generator. Queries SQLite for detection
  counts, species breakdown, predator visit summaries, deterrent effectiveness, hourly
  activity distribution, and 7-day trends. Exports to data/exports/ as JSON and Markdown.
  Can run on-demand via API/dashboard or automatically at end of day.

- **`api.py`** — REST API at /api/v1/ for LLM tool access. Endpoints: status, daily
  summaries, detection queries, track queries, species patterns, deterrent effectiveness,
  eBird sightings, camera PTZ/spotlight/siren control, and report export. Mounted on
  the same FastAPI app as the dashboard.

### Changed

- **`guardian.py`** — Wires all Phase 3+4 modules into the service lifecycle. Connects
  camera hardware control on startup, starts PTZ patrol thread with pause support, starts
  eBird polling thread, registers API router on dashboard. Detection callback now fires
  deterrents on predator tracks. Generates end-of-day report on shutdown.

- **`dashboard.py`** — Added PTZ control endpoints (move, stop, preset, spotlight, siren),
  deterrent status endpoint, active tracks endpoint, report endpoints (list dates, load,
  generate on-demand). Updated start_dashboard to accept db/reports and register API router.

- **`database.py`** — Added deterrent action CRUD (insert, update result, get actions,
  effectiveness stats). Added detection aggregation queries (counts by class, by hour,
  predator tracks for date). Added species pattern analysis query. Added eBird sighting
  queries and alert marking.

- **`static/index.html`** — Added PTZ Control page (manual directional pad, zoom, preset
  buttons, spotlight/siren controls, deterrent status, active tracks). Added Reports page
  (date picker, generate button, summary cards, species bar chart, predator visit table,
  hourly activity histogram).

- **`static/app.js`** — Added PTZ control functions (move, stop, zoom, presets, spotlight,
  siren). Added Reports page functions (date loading, report rendering with charts).

- **`config.example.json`** — Added `deterrent`, `ptz`, `ebird`, and `reports` config
  sections with all configurable parameters.

### Why

Phase 3 enables the camera to actively deter predators (not just detect them) with
automated spotlight, siren, and PTZ response. Phase 4 provides intelligence reporting
so the system tracks patterns over time and exposes structured data for LLM queries.

## [2.0.0-alpha] - 2026-04-03

### Added — Phase 2: Database + Vision + Tracking (Claude Opus 4.6)

- **`database.py`** — SQLite abstraction layer with WAL mode for concurrent read access.
  Schema includes 8 tables: cameras, detections, tracks, alerts, deterrent_actions,
  ptz_presets, daily_summaries, ebird_sightings. Thread-safe with parameterized SQL.
  Daily backup to `data/backups/` using SQLite backup API. No ORM — raw SQL for
  PostgreSQL portability.

- **`vision.py`** — GLM vision model species refinement via LM Studio (OpenAI-compatible
  API at 127.0.0.1:1234). When YOLO detects an ambiguous class (bird/cat/dog), crops
  the bounding box and sends to `zai-org/glm-4.6v-flash` for species identification.
  Distinguishes hawk from chicken, bobcat from house cat, coyote from dog. Per-track
  caching prevents redundant queries. 3-second timeout with graceful fallback.

- **`tracker.py`** — Animal visit tracking. Groups individual detections into "tracks"
  (visits) with duration, detection count, confidence stats, and outcome tracking.
  Tracks open on first detection, merge within a 60s timeout window, and close
  automatically when the animal leaves. Provides hooks for deterrent outcome tracking
  (Phase 3).

### Changed

- **`logger.py`** — Now dual-writes to SQLite database (v2) and JSONL files (v1 legacy).
  Accepts optional `db` parameter; backward-compatible when no DB provided. Passes
  track_id, model_name, and bbox_area_pct to DB for richer detection records.

- **`guardian.py`** — Wires new v2 modules into the detection pipeline. After YOLO
  detection, optionally refines species via vision model, then tracks the detection
  as part of an animal visit. Registers cameras in DB after discovery. Daily DB backup
  added to cleanup loop. Graceful shutdown closes tracker and database.

- **`config.example.json`** — Added `vision`, `tracking`, and `database` config sections.

- **`requirements.txt`** — Added `reolink-aio>=0.9.0` and `aiohttp>=3.9.0` (Phase 3 prep).

### Why

Camera arriving 03-April-2026. Phase 2 establishes the structured data foundation so
every detection from day one is stored in SQLite with species refinement and visit
tracking — not just flat JSONL. This enables the intelligence features (reports, patterns,
LLM queries) planned for Phase 4.

## [1.0.0] - 2026-04-02

### Added — v1: Core System (Cascade, Claude Sonnet 4)

- ONVIF camera discovery and RTSP URL resolution (`discovery.py`)
- RTSP frame capture with downscaling and reconnection (`capture.py`)
- YOLOv8 detection with false-positive suppression filters (`detect.py`)
- Discord webhook alerting with rate limiting and retry (`alerts.py`)
- Structured JSONL event logging with snapshots (`logger.py`)
- Service orchestration with graceful shutdown (`guardian.py`)
- Local web dashboard with live feeds and controls (`dashboard.py`, `static/`)
