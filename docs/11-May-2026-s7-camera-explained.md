# S7 camera — how it fits into the pipeline

**Audience:** future agents (or future Boss) who open the repo and ask "wait, what *controls* the S7 phone? I don't see anything that drives it." This doc answers that question once so it doesn't get re-derived.

## The S7 is self-driving

The S7 is a Samsung Galaxy S7 phone sitting in the coop, aimed at the nesting box. It runs an Android app called **IP Webcam**. That app turns the phone into a tiny HTTP/RTSP server on the WiFi at `192.168.0.249`:

- HTTP on port `8080` — serves JPEG snapshots at `/photoaf.jpg` (and `/photo.jpg`, and a bunch of `/settings/*` endpoints).
- RTSP on port `5554` — live H.264 stream. Not currently consumed by Guardian (it polls JPEG instead).

The phone is powered by a standalone USB wall brick. **No computer is plugged into the S7.** There is no ADB cable, no tethered host, no remote control of the Android side. If the IP Webcam app crashes or wedges, somebody (Boss) walks to the coop and pokes the screen. That's the recovery path, by design.

## What "controls" it from the Mac Mini side

Three pieces. None of them push commands — they all either pull frames or nudge settings via HTTP GETs.

### 1. `config.json` (Guardian service)

Lines 20–40 of the repo's `config.json`. This defines `s7-cam` as a `snapshot_method: "http_url"` camera and tells Guardian to poll `http://192.168.0.249:8080/photoaf.jpg` at the configured `snapshot_interval`.

The interesting block is `http_startup_gets` — four HTTP GETs that Guardian fires once at startup to set IP Webcam's mode:

```
/settings/focusmode?set=continuous-picture
/settings/whitebalance?set=incandescent
/settings/orientation?set=portrait
/settings/photo_rotation?set=90
```

Those URLs are built into the IP Webcam Android app. Guardian isn't sending a custom protocol — it's just calling the app's own REST surface.

`detection_enabled` is `false` on this camera. YOLO doesn't run on S7 frames; the camera exists for content (nesting box / Birdadette / portrait gem material), not predator detection.

### 2. `tools/pipeline/config.json` (VLM pipeline service)

Separate service, separate config, separate LaunchAgent (`com.farmguardian.pipeline`). The pipeline reads its own S7 entry and pulls frames through `capture_method: "ip_webcam"` on its own cadence. Each captured frame goes to LM Studio for VLM enrichment (caption draft, share_worth, image_quality, individual ID), then into the image archive that feeds the IG gem lane, the Discord `#farm-2026` reaction gate, and the FB cross-post.

Important: **Guardian (`config.json`) and the pipeline (`tools/pipeline/config.json`) read the S7 independently.** Two separate services, two separate poll loops, two separate cadences. Changing one does not change the other.

### 3. `com.farmguardian.s7-settings-watchdog` LaunchAgent

Every 10 minutes, a tiny script re-curls the four `/settings/*` URLs above. The reason: IP Webcam sometimes drops its sticky settings when the app crashes or auto-recovers. Without this watchdog, a crash could leave the S7 broadcasting landscape orientation, daylight white balance, or fixed focus — and stay that way until Guardian was restarted.

The watchdog is the closest thing in the repo to "active control" of the S7. It is literally `curl` in a cron. There is no two-way protocol.

**Known limitation:** if IP Webcam's HTTP server is fully wedged (the documented 2026-05-06 mode — `/photoaf.jpg` returns 0 bytes), the watchdog's four GETs also fail (`wb=0 fm=0 or=0 pr=0` in the log). The watchdog cannot un-wedge a wedged app. Recovery is hands-on at the phone.

## Why it broadcasts the best photos in the fleet

- Real phone-grade CMOS sensor (Sony IMX260, f/1.7) with continuous autofocus.
- Mounted close to the nesting box at a fixed framing — no PTZ drift, no patrol blur.
- Portrait orientation (1080×1920) — content-aligned with IG stories / reels, which are 9:16 native.
- Light polling load — phone isn't overheating or queuing up requests.

The other cameras are either Reolink-WiFi (lossy HEVC, distant subject), USB webcams on laptop hosts (smaller sensors), or RTSP-from-laptop-cams (compressed, weaker optics). The S7 wins on sensor + framing.

## Where to look in the repo

- `config.json` lines 20–40 — Guardian's camera definition.
- `tools/pipeline/config.json` — pipeline's separate S7 entry.
- `capture.py` — `HttpUrlSnapshotSource` is the class that does the JPEG polling. `_apply_exif_rotation` bakes IP Webcam's EXIF Orientation=6 tag into the pixels before downstream consumers see the frame (OpenCV's `cv2.imdecode` ignores EXIF, hence the manual rotation).
- `deploy/` — where the settings watchdog plist lives.
- Related docs:
  - `docs/06-Apr-2026-s7-nesting-box-camera-setup.md` — original setup plan.
  - `docs/16-Apr-2026-s7-ipwebcam-frozen-incident.md` — wedge-mode incident post-mortem.
  - `docs/skills-s7-adb-operations.md` — the GWTC-USB-ADB recovery runbook, currently inapplicable (phone is on standalone power as of 2026-05-06).

## The mental model

**Guardian is a client of the phone, not a controller of it.** The phone runs itself. Guardian (and the pipeline) just show up on a timer and ask "got a picture?" The watchdog occasionally re-asserts a few settings. That's the entire control surface.

If a future agent goes looking for "the S7 driver" or "the S7 controller" — there isn't one. The S7 driver is the IP Webcam Android app running on the phone, and the Mac Mini doesn't have the source.
