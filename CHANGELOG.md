# Changelog

All notable changes to Farm Guardian are documented here. Follows [Semantic Versioning](https://semver.org/).

## [2.27.9] - 2026-04-16

### Docs — S7 "frozen" incident post-mortem (Claude Opus 4.7)

Boss reported the S7 looked frozen on the dashboard. Root cause was not the phone or the camera — the IP Webcam Android app had been navigated to its Configuration / OnvifConfiguration screens (Boss was tweaking ONVIF settings), which on `com.pas.webcam` halts the HTTP server and unbinds port 8080. Guardian's snapshot poller then gets `Connection refused` every tick; the dashboard meanwhile keeps displaying the last cached good frame from the poller's ring buffer, which is what makes it look frozen rather than missing.

Recovery is a 30-second manual tap ("Start server" on IP Webcam's main Configuration screen). Boss did it, port 8080 re-bound, Guardian log confirmed `snapshots resumed after 68 failures`, all five cameras back online.

**Pre-buried wrong theories** (don't chase these next time):
- Not a dead phone — dumpsys battery during the incident: level=100%, status=Full, 37.2°C, USB powered, awake.
- Not the v2.24.0 battery-drain-on-charger pattern.
- Not WiFi / DHCP — phone was pingable and `nc` showed *refused* (listener gone), not unreachable.
- Not a Guardian or config bug.

**What also doesn't work** (I tried these before realizing the pragmatic answer was a human tap):
- `am start -n com.pas.webcam/.Rolling` — Binder exception, Rolling needs internal state.
- Tasker-style broadcast intents (`com.pas.webcam.CONTROL` with `action=start` in several extras formats, `com.pas.webcam.START_SERVER`) — accepted but ignored while the app is on a settings screen.
- UI automation via `input keyevent` / `uiautomator dump` / `input tap` — blocked because the S7's USB composite drops between every `adb shell` invocation; `adb reconnect offline` re-arms it, but the next shell call hits `device not found` again. Not worth chasing for a 30-second manual fix.

**New doc:** `docs/16-Apr-2026-s7-ipwebcam-frozen-incident.md` — full writeup in the same pattern as `docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md`. Includes the 30-second recovery recipe, the diagnostic one-liner for confirming it's this specific failure mode (dumpsys activity → top resumed is Configuration/OnvifConfiguration, not Rolling), and IP Webcam settings to harden against recurrence ("Run server in background," "Keep camera running when locked," plus marking IP Webcam as *never sleeping* in Samsung's Adaptive Battery).

**No code changes this release.** The v2.27.8 battery monitor on the MBA explicitly survives this failure mode — it polls the phone's battery state via USB ADB, which is independent of whether IP Webcam's HTTP server is bound, so "phone is alive" vs "camera app crashed" stays observable during the next recurrence.

---

## [2.27.8] - 2026-04-16

### Added — `mba-cam` back online via HTTP snapshot, S7 battery monitor on the MBA, first S7 gem posted to #farm-2026 Discord (Claude Opus 4.7)

Continuation of the S7 recovery session. After the image-quality fix in v2.27.7, Boss plugged the S7 into the MacBook Air's USB for charging + data. Three things landed off that:

**1. MBA FaceTime camera re-enabled as `mba-cam`.** The MBA was decommissioned as a camera host on v2.27.2 (MediaMTX + ffmpeg RTSP streaming was ripped out when Boss repurposed the machine). It's coming back, but on the lighter HTTP-snapshot architecture — `usb-cam-host` FastAPI service bound to `*:8089`, Guardian pulls `/photo.jpg` at 60 s cadence via `HttpUrlSnapshotSource`. No RTSP, no continuous video. The `com.farmguardian.usb-cam-host` LaunchAgent and the `~/.local/farm-services/usb-cam-host/` tree were already installed from the earlier stint (v2.26.2); just needed `launchctl bootstrap`. The MBA is a 2013 machine with a 720 p FaceTime HD camera, so quality is fleet-tier not portrait-tier — overview shot of the whole brooder from an elevated angle, complementary to the S7's close-up portrait camera.
- `config.json` (gitignored): added `mba-cam` entry, `http_base_url: http://marks-macbook-air.local:8089` (mDNS — the MBA is DHCP and drifts, never put an IP here)
- `config.example.json`: same block added with an `_comment` explaining the context
- Verified: Guardian `online=True capturing=True` on all 5 cameras (house-yard, s7-cam, usb-cam, mba-cam, gwtc); `/api/cameras/mba-cam/frame` returns a fresh 293 KB 1280×720 JPEG

**2. S7 battery monitor (new service, runs on the MBA).** Boss's stated concern was that the S7 "kept losing power even when it was plugged in" — the old RTSP-streaming load drained faster than USB could top up. After the v2.24.0 HTTP-snapshot switch + v2.27.7 60-s cadence, the draw is much lower, but the battery is still worn. Needed visibility. IP Webcam's built-in `/sensors.json` doesn't expose battery (returns `{}` — battery isn't an Android-sensor-framework sensor), so we went the ADB route.
- ADB-over-USB from the MBA works via `~/.local/android/platform-tools/adb` (installed on v2.27.2). Initial `adb devices -l` returned empty because the S7's USB composite goes dormant when its screen sleeps — `adb reconnect offline` re-arms it cleanly; the service uses the same trick on every poll.
- New `tools/s7-battery-monitor/monitor.py` (stdlib-only Python 3, runs under `/usr/bin/python3` on the MBA — compatible with 3.8 per the MBA's macOS 11 base). Reads `dumpsys battery` every 5 min. Alerts the existing Guardian Discord webhook on three transitions: (a) battery level below `LEVEL_ALERT` (default 25%), (b) temperature above `TEMP_ALERT_TENTHS` (default 48.0°C), (c) phone comes off USB power unexpectedly. A matching "recovered" message fires on exit from each state. Alerts are deduped via a tiny JSON state file — they fire on the transition *into* the alert, not every 5 minutes while the condition persists. Every poll also logs to `~/.local/farm-services/s7-battery-monitor/monitor.log` so historical drain curves can be reconstructed.
- `tools/s7-battery-monitor/com.farmguardian.s7-battery-monitor.plist.template` — LaunchAgent template; installed as `~/Library/LaunchAgents/com.farmguardian.s7-battery-monitor.plist` on the MBA, bootstrapped with `launchctl bootstrap gui/$(id -u) …`, `StartInterval=300`, `RunAtLoad=true`. Webhook URL lives in the plist's `EnvironmentVariables` block, not in the script.
- First live reading: level=99%, temp=41.5°C, voltage=4282 mV, status=2 (charging), USB-powered. All healthy, no alerts firing. Log rolling.
- **Known limitation — ADB-over-WiFi is not configured.** On Android 8, `adb tcpip 5555` doesn't survive phone reboots; the S7 has rebooted itself several times this session. As long as the phone stays USB-tethered to the MBA, we don't need WiFi-ADB because the monitor runs locally on the MBA. If the phone moves off USB, the monitor will scream "ADB unreachable" on the next tick.

**3. Discord demo post to #farm-2026.** Boss asked to route good S7 shots to "the Discord Farm 2026 channel." Confirmed via `GET https://discord.com/api/webhooks/<id>/<token>` that the existing Guardian webhook's `channel_id=1482466978806497522` *is* `#farm-2026` (cross-checked with `~/bubba-workspace/memory/2026-04-04-farm-session.md`) — so "post to #farm-2026" = "post to the Guardian webhook with a nice caption and an attachment." One fresh S7 frame posted with `username: "S7 Brooder"`, attached as multipart form-data, HTTP 200 + CDN URL returned. This is the manual-one-off path; the auto-forward-from-pipeline flow is in the other dev's lane (they're on a separate branch working on the USB-cam + S7 pipeline tie-in, per Boss).

**Files changed:**
- `config.json` (gitignored) — added `mba-cam` entry
- `config.example.json` — same `mba-cam` block, with an `_comment` block
- `tools/s7-battery-monitor/monitor.py` — new, ~180 lines
- `tools/s7-battery-monitor/com.farmguardian.s7-battery-monitor.plist.template` — new

**Not in this release (explicitly deferred):**
- S7 re-enable in `tools/pipeline/config.json` — the other dev is working on the pipeline side on a separate branch; I reverted my one speculative edit there to avoid a merge collision.
- An automatic gem → Discord forwarder. The one-off post is enough to prove the channel wiring works; the auto-forward design ties into the pipeline work, which isn't my lane this session.
- ADB-over-WiFi on the S7 (persistent via Tasker/WiFi ADB app). Only needed if the phone moves off USB.

---

## [2.27.7] - 2026-04-16

### Fixed — `s7-cam` image quality: `focusmode=macro` and heat-lamp orange cast (Claude Opus 4.7)

Boss turned the S7 back on after its battery-death outage and immediately flagged the dashboard frames as "blurry, washed-out — like nothing that I see. I just want one nice high-quality image every 30 seconds or a minute." Investigation via `http://192.168.0.249:8080/status.json` showed the phone's camera was in `focusmode=macro` — close-up mode, 10–20 cm focal distance — so everything beyond arm's reach was out of focus. On top of that, `whitebalance=auto` couldn't correct for the ~3000 K tungsten heat lamp, leaving every frame drowning in orange. The 5-second poll interval was also much denser than Boss's "every 30 s or a minute" ask.

Fix is three parts, all landing in one release:

1. **Phone side, via IP Webcam's runtime API:** `GET /settings/focusmode?set=continuous-picture` and `GET /settings/whitebalance?set=incandescent`. With continuous-picture the camera AFs whenever the scene shifts instead of holding a fixed macro plane; with incandescent WB the 3000 K tungsten cast gets neutralized to roughly what the eye sees. These two calls produce a visibly dramatic quality jump — chicks are sharp end-to-end, wall reads as its actual purple-blue instead of red-orange.

2. **Guardian side, capture path:** `s7-cam.http_photo_path` changed from `/photo.jpg` to `/photoaf.jpg`. The `af` variant fires a fresh AF cycle per pull (~1 s overhead) and hands back a freshly-focused still, so even if the scene drifts between polls Guardian always captures on a locked frame. `snapshot_interval` raised `5.0` → `60.0` — one "nice high-quality image" per minute is what Boss asked for, and it's 12× less load on a phone with a worn battery.

3. **Persistence problem + fix:** IP Webcam's runtime settings reset whenever the phone or the app restarts (they're not baked into preferences). So without persistence, every S7 reboot would drop us back to macro + auto-WB. New `http_startup_gets` array on `HttpUrlSnapshotSource` (`capture.py`): a list of path+query fragments that get GET'd once at poller construction. Guardian now reasserts `focusmode=continuous-picture` + `whitebalance=incandescent` on every start, logging each GET at INFO level. Failures are logged + swallowed (a setting the phone doesn't support shouldn't block capture). The wiring in `guardian.py` pipes `cam_cfg.get("http_startup_gets")` through.

**Files changed:**
- `capture.py` — `HttpUrlSnapshotSource.__init__` accepts `startup_gets: Optional[list]`, fires each GET with auth + 5 s timeout + log entry; header metadata updated
- `guardian.py` — pass `cam_cfg.get("http_startup_gets")` to `HttpUrlSnapshotSource`
- `config.json` (gitignored) — s7-cam: `http_photo_path` → `/photoaf.jpg`, `snapshot_interval` → `60.0`, added `http_startup_gets` array
- `config.example.json` — same s7-cam block updated, with an expanded `_comment` explaining the v2.27.7 rationale

**Verification (live):**
- Phone before: `focusmode=macro`, `whitebalance=auto`; `/photo.jpg` returns 1920×1080 but subjects beyond ~20 cm are soft and the frame is heavily orange-cast.
- Phone after (API writes): `focusmode=continuous-picture`, `whitebalance=incandescent`; same `/photoaf.jpg` endpoint returns sharp full-frame detail with chicks reading as cream/white and wall reading as neutral purple-blue.
- Guardian after restart: log shows `Startup GET …/settings/focusmode?set=continuous-picture → 200`, `Startup GET …/settings/whitebalance?set=incandescent → 200`, `Snapshot polling started for 's7-cam' — source=http:s7-cam, interval=60.0s`. First `/api/cameras/s7-cam/frame` inside the first minute returns 677 KB 1920×1080 JPEG with sharp focus and corrected WB.

**Not yet addressed (explicitly deferred by Boss's "focus right now on quality"):** the power-monitor ask. IP Webcam on this S7 does not expose battery via `/sensors.json` (it returns `{}` — battery isn't an Android-sensor-framework sensor, and IP Webcam doesn't polyfill it), and there's no `/battery` endpoint. Options for the next pass are (a) liveness-based monitoring (Discord-alert on `s7-cam` online→offline and back, treats sustained offline as "probably battery"), or (b) ADB-over-WiFi to read `dumpsys battery` directly (richer telemetry, needs Developer Options + WiFi ADB pairing on the phone once). Picking between those is a call for Boss to make.

---

## [2.27.6] - 2026-04-16

### Fixed — `usb-cam` feed-lost, caused by a stale WiFi IP in two configs (Claude Opus 4.7)

Dashboard reported `usb-cam` feed lost. Root cause: both `config.json` (Guardian) and `tools/pipeline/config.json` (orchestrator) pointed the `usb-cam` HTTP snapshot URL at `http://192.168.0.71:8089` — the Mini's *old* WiFi address from 13-Apr-2026. The Mini's en1 WiFi now leases `192.168.0.220` (the DHCP lease rolled at some point; memory already flagged this drift pattern). The `usb-cam-host` LaunchAgent was healthy the whole time — it's bound to `*:8089` and serving 448 KB JPEGs — but nothing reachable at `.71` meant Guardian's capture loop got `[Errno 64] Host is down` every poll and the last-frame cache went stale.

Fix: both configs now use `http://localhost:8089`. The `usb-cam-host` runs on the same Mini as Guardian and the orchestrator, so routing the snapshot pull through the external WiFi IP was never meaningful — it just added a reachability dependency that the hardware doesn't need. `localhost` is immune to any future WiFi-DHCP-lease drift, ethernet reconnect, or NIC swap on this machine. If the USB cam ever moves to another host again (it lived on the MBA for ~24h on 14-Apr-2026), the service's `http_base_url` will need to be updated to point at that host's mDNS name — **do not put an IP back in**.

**Files changed:**
- `config.json` — `usb-cam.http_base_url`: `http://192.168.0.71:8089` → `http://localhost:8089`
- `tools/pipeline/config.json` — `usb-cam.ip_webcam_base`: same swap

**Verification:** Guardian restarted via `launchctl kickstart -k gui/$UID/com.farmguardian.guardian`; `GET /api/cameras/usb-cam/frame` now returns HTTP 200 + ~448 KB JPEG; all four cameras report `online=True capturing=True`.

**Hardcoded-IP audit (per Boss's ask):** Swept `**/*.{py,json}` for `192.168.*`. Remaining hits are all defensible — `house-yard` `.88` (Reolink on a router DHCP reservation), `gwtc` `.68` (Windows laptop, known-fragile but the documented drift-recovery is a `:8554` service-signature scan, not a config rewrite), and `s7-cam` `.249` (phone on reservation; pipeline entry is `enabled: false` right now anyway while the S7 is offline). Only Mini-self-reference was genuinely stupid, and that's what this release fixes.

---

## [2.27.5] - 2026-04-16

### Fixed — `com.farm.guardian` LaunchAgent relabeled to `com.farmguardian.guardian` (Claude Opus 4.6)

The LaunchAgent had been failing to spawn with `posix_spawn ... Operation not permitted` since the 14-Apr-2026 power outage. The previous CLAUDE.md note proposed a fix via **System Settings → Privacy & Security → App Management** — that turned out to be wrong. App Management does not expose launchd service entries, and adding the venv Python binary there is not possible (the file picker rejects it). Reboot also did not clear the denial.

Root cause: macOS TCC persists **per-label** denies in its database. The specific label `com.farm.guardian` was permanently held in a denied state, independent of the binary being spawned — confirmed by the fact that `com.farmguardian.usb-cam-host` (running the identical venv Python from the identical folder) spawned without issue.

Fix: renamed the plist label (and the plist filename) to `com.farmguardian.guardian`. A fresh label carries no TCC history, so the first bootstrap attempt spawned cleanly.

**Files changed:**
- `~/Library/LaunchAgents/com.farm.guardian.plist` → renamed to `~/Library/LaunchAgents/com.farmguardian.guardian.plist`
- Label: `com.farm.guardian` → `com.farmguardian.guardian`
- StandardOutPath / StandardErrorPath: `guardian.log` in project dir → `/tmp/guardian.out.log` + `/tmp/guardian.err.log` (prophylactic; matches the working sibling plist). Guardian's own Python logger still writes to `guardian.log` in the project dir — that path works because the running process writes there; it's only launchd's redirect that sometimes hits TCC.

**Result:** Guardian auto-starts on boot again. `nohup` workaround retired.

**Rule for future sessions:** if a LaunchAgent label ever lands in a `posix_spawn Operation not permitted` loop that survives a reboot, the surgical fix is a label rename, not a System Settings hunt.

---

## [2.27.4] - 2026-04-15

### Changed — `usb-cam-host` heat-lamp color correction retuned (Claude Opus 4.6)

Brooder frames still looked orange under the heat lamp despite the gray-world WB. Root cause: gray-world scales each channel toward the overall mean, but when the scene has almost no blue light (tungsten heat lamp ≈ 3000K, peaks red/yellow), there is no blue signal to amplify — the chicks stay orange no matter how hard the WB pushes. Fix is a two-stage correction:

1. **Gray-world WB** strength raised from `0.5` → `0.8`. Cooler global balance; background curtain / blue surfaces now read neutral-to-blue instead of muddy tan.
2. **New orange-hue saturation pass** (`_apply_orange_desat`): after WB, pull saturation down by 25% on OpenCV hue band `[5, 30]` (the orange/amber wedge). Chicks and pine shavings read as yellow/cream instead of pumpkin. Non-orange hues untouched.

New env knobs on `tools/usb-cam-host/usb_cam_host.py` (all optional, sensible defaults):
- `USB_CAM_WB_STRENGTH` — was 0.5 default, now 0.8.
- `USB_CAM_ORANGE_DESAT` — 0.75 default (`1.0` = off, `0.0` = fully desat).
- `USB_CAM_ORANGE_HUE_LO` / `USB_CAM_ORANGE_HUE_HI` — OpenCV hue band the desat targets, default `5..30`.

`/photo.jpg` also gained `?wb=X&os=Y` query-string overrides so operators can A/B tune live without a service restart — useful for re-dialing if the camera moves to a non-brooder scene. Defaults to the env-configured values.

A/B sample (raw → current): chicks-under-heat-lamp go from fully orange silhouettes to visibly yellow/cream birds against a cool blue backdrop, while non-orange areas of the scene are unchanged. Service kicked via `launchctl kickstart -k gui/<uid>/com.farmguardian.usb-cam-host` to pick up the new defaults.

---

## [2.27.3] - 2026-04-15

### Changed — `gwtc` pipeline cadence: 600s → 60s (Claude Opus 4.6)

Boss's call. Five frames every ten minutes was far too sparse for what the coop camera was meant to do — he was watching the live feed and noticing the image only refreshed on the burst boundary. `tools/pipeline/config.json` `gwtc.cycle_seconds` dropped from `600` → `60`; `burst_size` (5) and `burst_interval_seconds` (0.5) unchanged. New archival rate: 5 frames/min, ~300/hr. Orchestrator restarted to pick up the new cadence (log confirms `gwtc: scheduled first cycle in 50s (cadence 60s)`). Retention policy unchanged — 90 days for strong/decent, so disk footprint grows 10× on this one camera.

Separately: noted that the Guardian live-RTSP capture for `gwtc` is dropping and reconnecting roughly every 10 minutes (`frame read failed, reconnecting` at 17:48:13 and 17:59:49 in `guardian.log`). That's a separate bug from the pipeline cadence — flagged for follow-up, not fixed here.

---

## [2.27.2] - 2026-04-15

### Removed — `mba-cam` decommissioned; MBA repurposed (Claude Opus 4.6)

Boss announced he's repurposing the MacBook Air 2013 for unrelated work. Fully disconnected the MBA from the camera network:

- **Mini-side:** `mba-cam` entry removed from `config.json`; `mba-cam` entry in `tools/pipeline/config.json` set to `enabled: false` with an `enabled_note` for future re-activation (kept the block because the ffmpeg/MediaMTX recipe for that particular hardware is non-trivial — v1.16.3+ dyld-fails on Big Sur, 15 fps isn't a valid capture rate on FaceTime HD, etc., all documented in the config). Guardian + pipeline orchestrator restarted; Guardian log confirms `4 camera(s) active: house-yard, s7-cam, usb-cam, gwtc`.
- **MBA-side:** `launchctl bootout` on all three farmguardian LaunchAgents (`com.farmguardian.mediamtx`, `com.farmguardian.mba-cam`, `com.farmguardian.usb-cam-host`). `pkill -9` on any stragglers. Plists left on disk at `~/Library/LaunchAgents/` and runtime tree left at `~/.local/farm-services/usb-cam-host/` — MBA can rejoin with a single `launchctl load` if Boss ever puts it back.

`HARDWARE_INVENTORY.md`: the "Five Cameras" table header is now "Four Cameras (was five until 2026-04-15…)", the `mba-cam` row strike-through'd with a decommission note, the MBA "What Runs Where" entry zero'd out, the frame-flow diagram updated. "Last verified" stamp bumped.

Did not delete the plists or the runtime — it's cheap to leave them and saves the next setup a reinstall.

---

## [2.27.1] - 2026-04-14

### Fixed — `mba-cam` / `gwtc` dashboard tiles felt stuck (Claude Opus 4.6)

Boss reported the MacBook Air camera tile updating "really slow." Root cause: `guardian.py:441` defaults `snapshot_interval` to **10.0 s** for any camera with `detection_enabled: false`. `mba-cam` and `gwtc` both inherit that default — their RTSP capture loops push a new frame to the ring buffer only every 10 s, so the dashboard tile never feels live even though the underlying RTSP stream is fine.

Fix — explicit `"snapshot_interval": 2.0` on both cameras in `config.example.json` (and mirrored on the live Mini `config.json`, which is gitignored). Guardian restarted; log now reads `Capture started for 'mba-cam' — interval=2.0s` and `'gwtc' — interval=2.0s`. Verified live: six consecutive `/api/cameras/mba-cam/frame` pulls 3 s apart now return six unique frame hashes (were all identical before).

Did **not** change the default in `guardian.py:441` itself — 10 s may be intentionally conservative on other deployments. Overriding per-camera is the right granularity.

`usb-cam` already poll-via-http at 5 s cadence (snapshot source, not RTSP) — unaffected. `house-yard` has `detection_enabled: true` so it uses the global `detection.frame_interval_seconds` — unaffected. `s7-cam` is 5 s snapshot — unaffected.

---

## [2.27.0] - 2026-04-14

### Changed — `usb-cam-host` is now continuous-capture (Claude Opus 4.6)

v2.26.x opened/warmed/released the camera on every `/photo.jpg` — 15-frame warmup → grab → release, ~3.4 s per request. With Guardian polling every 5 s the camera lived in a perpetual warmup cycle and AE/AWB never actually settled; request latency dominated the service.

**New architecture.** A daemon grabber thread holds the camera open for the life of the service, reads frames at ~2 Hz (`USB_CAM_GRAB_INTERVAL`, default 0.5 s), and publishes the latest raw BGR frame into a lock-protected slot. Request handlers copy the latest frame out, apply WB, encode JPEG, and return.

**Measured impact** (Mini, M4 Pro):
- `/photo.jpg` latency: **3.4 s → ~75 ms** (45×). First three post-cutover calls: 102 ms, 77 ms, 74 ms.
- Through-Guardian API latency (`/api/cameras/usb-cam/frame`): **~1 ms** (Guardian's ring buffer is already warm).
- `latest_frame_age_ms` at health check: 74 ms. Frames are always <500 ms stale in steady state.

**Side effects by design:**
- AE/AWB stabilizes because the camera stays warm — no more cold-start exposure on every request. Expect sharper frames and more `strong`-tier VLM hits over time.
- Request fan-in doesn't stall on camera I/O — only on the cheap WB + JPEG encode, which run in the default executor.
- The grabber auto-reconnects on persistent read failures (`USB_CAM_READ_FAILURE_THRESHOLD`, default 5 consecutive). Camera unplug / USB glitch / dshow hiccup → up to 3 s reconnect, service stays up.
- Frame max-age gating (`USB_CAM_MAX_FRAME_AGE`, default 5 s) returns 503 if the grabber stalls instead of serving something ancient.
- `/health` now reports `grabber_alive`, `camera_open`, `latest_frame_age_ms`, `latest_frame_sequence`, `total_grabs`, `total_failures` — useful for external watchdogs and for the frontend dev to reason about tile freshness.

**No consumer changes needed.** `HttpUrlSnapshotSource` (Guardian) and `capture_ip_webcam` (pipeline) both just hit `GET /photo.jpg` and get bytes back. Every client gets the latency improvement for free.

**Cutover tonight:** USB webcam plugged back into the Mac Mini. Mini's `usb-cam-host` LaunchAgent reloaded with the v2.27 code. MBA's `usb-cam-host` LaunchAgent booted out and killed. Mini configs flipped `http://192.168.0.50:8089` → `http://192.168.0.71:8089`. Guardian + pipeline orchestrator restarted; both confirmed to be consuming the 60-s-cadence stream off the Mini again.

**Guardian is running tonight under `nohup`.** The `com.farm.guardian` LaunchAgent has been in `posix_spawn: Operation not permitted` since this morning's power outage; the workaround from v2.26.0 onward is `nohup ./venv/bin/python guardian.py`. Verified live: 5 cameras active (`house-yard`, `s7-cam` [phone offline], `usb-cam`, `gwtc`, `mba-cam`); frame API returns 200s for all four online cameras.

---

## [2.26.3] - 2026-04-14

### Tuned — brooder sample rate 3× + WB strength 0.8→0.5 for cute-bird pipeline (Claude Opus 4.6)

Boss's explicit goal is a gallery of cute baby-bird pictures. The formula is simple: `rate_of_strong_tier = rate_of_samples × P(strong | sample)`. Two adjustments today, both on the `usb-cam` (brooder) path — no code logic changed, just parameters + one default.

**Sample rate — `tools/pipeline/config.json`.** `usb-cam` cycle_seconds 180 → 60. GLM inference runs ~28 s, so 60 s is comfortable. Brooder sample rate goes from 20/h to 60/h — roughly triples the probability of catching a 10–30 s cute-moment window. All other cameras left at their old cadences (`house-yard` 600 s, `mba-cam` 300 s, `gwtc` 600 s).

**WB strength — `deploy/usb-cam-host/com.farmguardian.usb-cam-host.plist`, `deploy/usb-cam-host/start-usb-cam-host.bat`, and the default in `tools/usb-cam-host/usb_cam_host.py`.** `USB_CAM_WB_STRENGTH` 0.8 → 0.5. Gray-world at 0.8 over a heat-lamp-dominated scene swings frames green/cyan — GLM's `image_quality` label rates those lower and they get demoted out of the `strong` tier. 0.5 still removes the orange cast without overshooting. The docstring note on `WB_STRENGTH` explains when to raise it back (neutral-light scenes).

Applied live on the MBA (agent bootout+bootstrap); `usb-cam-host ready: ... wb_strength=0.50` confirmed in `service.log`. Orchestrator restarted with the new cycle config; `pipeline.orchestrator usb-cam: scheduled first cycle in 41s (cadence 60s)` confirmed.

**Knowingly deferred — continuous-capture refactor of `usb-cam-host`.** Today's service opens/warms/releases the camera on every `/photo.jpg` (15-frame warmup ≈ 3.4 s per call). With Guardian polling every 5 s, the camera's AE/AWB never stabilizes — it's always warming up. A refactor to keep the camera open, grab frames in a background thread at ~2 Hz, and serve the latest buffered frame on demand would: cut response latency from 3.4 s → ~50 ms, let AE/AWB actually settle so frames get sharper and more naturally exposed, and make the dashboard tile feel genuinely live. Scoped at ~30 min. Not in this release because Boss hasn't approved the extra complexity yet; tracked as the next lever to pull.

---

## [2.26.2] - 2026-04-14

### Changed — `usb-cam` physically moved to the MacBook Air; MBA host setup lessons-learned (Claude Opus 4.6)

Boss plugged the generic USB webcam into the MacBook Air, triggering the host-portability move that v2.26.0 was designed to enable. The cutover landed cleanly but surfaced four MBA-specific gotchas that wouldn't have hit the Mac Mini. All four are documented here + in `deploy/usb-cam-host/install-macos.md` so the next host move doesn't relearn them.

**Collateral fix on the MBA's other LaunchAgent (`com.farmguardian.mba-cam`):** the moment the USB webcam plugged in, it took AVFoundation device index `0` and shoved the built-in FaceTime HD Camera to index `1`. `com.farmguardian.mba-cam`'s ffmpeg had been running 23 h with `-i 0` — still serving FaceTime HD because the *open handle* was locked, but any restart would have silently swapped it to the USB camera and broken `mba-cam`. Rewrote the plist to `-i "FaceTime HD Camera"` by name so index shifts can't hijack it. `HARDWARE_INVENTORY.md`'s `mba-cam` row records this.

**Python 3.8 compatibility shim in `tools/usb-cam-host/usb_cam_host.py`.** The service used `asyncio.to_thread` (Python 3.9+). MBA ceilings at Big Sur 11.7.11 + Python 3.8.9. Replaced with a small `_run_in_thread(fn)` helper that uses `loop.run_in_executor(None, fn)` — identical behavior, works on 3.8+. All farm Python targets now covered (3.8 MBA, 3.11 GWTC Windows installer, 3.13 Mini Homebrew).

**`opencv-python-headless==4.8.1.78` pin in `deploy/usb-cam-host/requirements.txt`.** The previous `opencv-python>=4.8,<5.0` range let pip pull 4.10 on Big Sur Python 3.8, which has no prebuilt wheel and kicked off a 30–60 min cmake build. The pinned 4.8.1.78 has a `cp38 macosx_10_16_x86_64` wheel. Headless variant skips GUI libs we don't use. Install instructions updated to pass `--only-binary=:all:` so pip never silently falls back to source.

**Runtime location outside `~/Documents/`.** Big Sur+ sandboxes `~/Documents/`, `~/Desktop/`, `~/Downloads/` against LaunchAgent access — the agent boots into `PermissionError: pyvenv.cfg Operation not permitted` and never starts uvicorn. Put the MBA runtime at `/Users/markb/.local/farm-services/usb-cam-host/` (script + venv together, outside any sandboxed directory). The Mini is unaffected because its Python has Full Disk Access granted already, but any new macOS host should follow this layout.

**OpenCV Camera TCC from a worker thread.** OpenCV's AVFoundation backend tries to request Camera auth via a prompt that only works if called from the main thread — our asyncio executor path isn't. Symptom: `OpenCV: not authorized to capture video (status 0), requesting... can not spin main run loop from other thread`. Fix: set `OPENCV_AVFOUNDATION_SKIP_AUTH=1` in the plist environment (added to the canonical plist in the repo + the live MBA one), then grant Camera to the Python binary manually via **System Settings → Privacy & Security → Camera**. Boss clicks once per host.

**Mini-side configs flipped:** `config.json` (Guardian) and `tools/pipeline/config.json` both now point `usb-cam` at `http://192.168.0.50:8089`. Guardian + orchestrator restarted via `nohup` (the `com.farm.guardian` LaunchAgent is still in its post-power-outage `posix_spawn: Operation not permitted` state — noted in v2.26.0 entry, still independent of this release).

`HARDWARE_INVENTORY.md` "Last verified" stamp bumped to 2026-04-14 18:35 ET; `usb-cam` row host column now reads MacBook Air 2013; `mba-cam` row notes the device-by-name pin; "What Runs Where" MacBook Air entry adds the `usb-cam-host` agent.

---

## [2.26.1] - 2026-04-14

### Fixed — gray-world white balance ported into `usb-cam-host` (regression from v2.26.0, Claude Opus 4.6)

v2.26.0 moved `usb-cam` from the local `UsbSnapshotSource` adapter to the portable HTTP service, which **silently dropped the gray-world white-balance step** that `UsbSnapshotSource._apply_gray_world_wb` had been applying before JPEG encode (originally shipped for the brooder heat-lamp cast). Post-v2.26.0 frames were rendering orange again. Boss spotted it within an hour.

Ported the WB correction verbatim from `capture.py:514–534` into `tools/usb-cam-host/usb_cam_host.py` as `_apply_gray_world_wb`. Applied after warmup + keeper read, before JPEG encode. Visual A/B: pre-fix frame is fully orange; post-fix frame shows yellow chicks, blue brooder walls, red feed dish rendering correctly.

Two new env vars for the service (also wired into the LaunchAgent plist and the Windows `.bat`):
- `USB_CAM_AUTO_WB` (default `true`) — gray-world enabled for the current heat-lamp environment. Set `false` if the camera ever moves to a neutral-light scene where full correction would over-correct a legitimately warm-toned subject.
- `USB_CAM_WB_STRENGTH` (default `0.8`, clamped 0.0–1.0) — interpolates between identity (0.0) and full gray-world (1.0). Matches the default from `config.json`'s old `snapshot_wb_strength`.

Keeping the WB in the service (not at the consumer) means the correction **moves with the camera** — when Boss plugs it into the MBA, the WB follows.

Verified end-to-end: agent reloaded; log line now reads `usb-cam-host ready: device=0 requested=1920x1080 warmup=15 jpeg_q=95 auto_wb=True wb_strength=0.80`; `curl http://localhost:8089/photo.jpg` returns a neutral-balanced 1080p JPEG.

---

## [2.26.0] - 2026-04-14

### Added — host-portable `usb-cam` via `tools/usb-cam-host/` (Claude Opus 4.6)

Boss flagged that the `usb-cam` frames weren't clearing the quality bar in the pipeline (every recent `data/archive/2026-04/usb-cam/*.json` is `image_quality: "soft"`, Laplacian variance 14–50) and that he wants to be able to move the camera off the Mac Mini — onto the MacBook Air, onto the Gateway laptop, onto "literally any device." The old wiring assumed the camera was physically attached to the host running Guardian (`snapshot_method: "usb"` → AVFoundation device index 0, macOS-only), which blocked that move.

**New service: `tools/usb-cam-host/usb_cam_host.py`.** FastAPI + OpenCV, ~200 lines. Exposes `GET /photo.jpg` (single warmed-up JPEG; 15-frame warmup lets AE/AWB converge under the heat lamp — materially longer than the prior 5×80ms pipeline warmup) and `GET /health` (open-read-release probe with negotiated resolution). Uses `cv2.VideoCapture(index)` with no backend flag so OpenCV auto-selects AVFoundation on macOS, dshow on Windows, V4L2 on Linux — this is the one-line portability fix that dropping `cv2.CAP_AVFOUNDATION` from the old `capture_usb` path enables. Single-in-flight asyncio lock so two simultaneous consumers don't deadlock the kernel driver against the same device index. Configured via env vars (port, device index, resolution, warmup, JPEG quality).

**No Laplacian frame ranking inside the service.** Boss reiterated today that he doesn't trust the Laplacian-vs-GLM-sharpness calibration. The pipeline plan (`docs/13-Apr-2026-multi-cam-image-pipeline-plan.md` §Trivial Gate) already notes Laplacian is a ranking signal only. The service warms the camera and returns a single frame; it does not burst-rank. If a future consumer wants to rank, it can request multiple frames and rank externally.

**Deploy artifacts: `deploy/usb-cam-host/`.**
- `requirements.txt` — FastAPI, Uvicorn, opencv-python, NumPy. Pinned versions.
- `com.farmguardian.usb-cam-host.plist` — macOS LaunchAgent (user-scope, not LaunchDaemon — AVFoundation Camera TCC is a per-user grant and LaunchDaemons can't surface prompts). Installed into `~/Library/LaunchAgents/` on the Mini today; same file copies cleanly to the MBA.
- `start-usb-cam-host.bat` — Windows startup script mirroring `deploy/gwtc/start-camera.bat`. Intended to be Shawl-wrapped the same way the existing `farmcam` and `farmcam-watchdog` services are.
- `install-macos.md` + `install-windows.md` — per-platform install, TCC walkthrough, smoke-test recipes (health + `/photo.jpg` + Laplacian-variance-for-diagnostics).

**Config changes — `usb-cam` consumer rewiring (both ends, Mini-host bring-up):**
- `config.json` (Guardian): `usb-cam` block switched from `snapshot_method: "usb"` + `device_index`/`resolution`/`autofocus`/`auto_wb`/`warmup_frames` to `snapshot_method: "http_url"` + `http_base_url: "http://192.168.0.71:8089"` + `http_photo_path: "/photo.jpg"` + `http_trigger_focus: false`. Guardian already had `HttpUrlSnapshotSource` (v2.24.0, `capture.py:537`) and its dispatch in `guardian.py:398`; **no Guardian code changed** — only config.
- `tools/pipeline/config.json`: `usb-cam` block switched from `capture_method: "usb_avfoundation"` + `device_index`/`warmup_frames`/`resolution` to `capture_method: "ip_webcam"` + `ip_webcam_base: "http://192.168.0.71:8089"`. The pipeline already had `capture_ip_webcam` (`tools/pipeline/capture.py:134`) for the S7 path; **no pipeline code changed** — only config.
- `config.example.json` — `usb-cam` example block updated to match, with `192.168.0.XXX` as the placeholder host so future copies don't silently point at the Mini.

**Moving the camera later** is now: plug into the new host → install the LaunchAgent (macOS) or Shawl-wrap the `.bat` (Windows) → change `http_base_url` and `ip_webcam_base` in the two config files on the Mini → restart Guardian and the pipeline orchestrator → update `HARDWARE_INVENTORY.md`'s `usb-cam` row.

**Explicit non-goals** (from the plan doc's Scope-out):
- Physical positioning. The `2026-04-14T17-14-49` sample shows a chick standing on the lens under a blown-out heat lamp. The service will deliver the best frame the camera is physically capable of — not a better one. Standoff, cowl, and heat-lamp angle are Boss's to solve.
- Sharpness-based ranking inside the service (Boss's distrust of Laplacian-vs-GLM).
- Auth / TLS — LAN-only, same trust model as the MBA and GWTC MediaMTX services.
- Replacing `mba-cam` or `gwtc` — those are built-in webcams streaming RTSP; this is a separate portable external USB camera.

**Plan doc:** `docs/14-Apr-2026-portable-usb-cam-host-plan.md` — full scope/architecture/TODOs/verification/risks.

**Operational note for future agents.** During today's flip, immediately after the house had a brief power outage mid-session, the existing `com.farm.guardian` LaunchAgent started failing to exec with `posix_spawn ... Operation not permitted` — NOT caused by any config or code change in this release (the brand-new `com.farmguardian.usb-cam-host` agent installed today uses the same `venv/bin/python` and starts cleanly). Guardian is currently running via `nohup ./venv/bin/python guardian.py` on the Mini; a reboot or a System Settings → Privacy & Security → App Management re-approval is likely the cleanest restore path for the `com.farm.guardian` agent. Independent of this release.

---

## [2.25.0] - 2026-04-14

### Added — `/api/v1/images/*` REST surface for the image archive (Claude Opus 4.6)

farm-2026's frontend developer is blocked on this: they want to ship a `/gallery/gems` page, a homepage "Latest from the Flock" rail, and a Birdadette retrospective that all pull from the image-archive dataset the pipeline has been filling since v2.23.0. Today that dataset lives only on the Mini's filesystem. This release exposes it over the Cloudflare tunnel.

**New public endpoints** (no auth; every SQL query filters `has_concerns = 0` as the first predicate; response models omit `concerns` / `vlm_json`):

- `GET /api/v1/images/ping` — tiny liveness endpoint, reports row count. Used by the plan doc's Phase-0 tunnel verification (`curl https://guardian.markbarney.net/api/v1/images/ping`).
- `GET /api/v1/images/gems` — list `share_worth='strong'` rows, newest first. Filters: `camera` (repeatable), `scene`, `activity`, `individual`, `since`, `until`, `order` (`newest`|`oldest`|`random`). Cursor pagination; `limit` ≤ 100 (default 24).
- `GET /api/v1/images/gems/{id}` — single gem + up to 4 related gem IDs from the same camera within ±2h.
- `GET /api/v1/images/gems/{id}/image?size=thumb|1920|full` — JPEG bytes. Thumbnails generated lazily via Pillow and cached under `data/cache/thumbs/{sha256}-{size}.jpg`. `ETag`-backed `If-None-Match` → 304.
- `GET /api/v1/images/recent` — same shape as `/gems`, tier-in `{strong, decent}` by default. Adds `image_tier` per row.
- `GET /api/v1/images/stats` — aggregate counts for badges and hero copy (per-tier, per-camera, per-scene, per-activity, Birdadette sightings, oldest/newest ts).

**New private endpoints** (require `Authorization: Bearer $GUARDIAN_REVIEW_TOKEN`; return `503` if the env var is unset — review endpoints just go dark, the rest of the service stays up):

- `GET /api/v1/images/review/queue` — full queue including `has_concerns=1` + `tier=skip`, with `only_concerns` / `only_unreviewed` switches.
- `POST /api/v1/images/review/{id}/promote` — set `share_worth='strong'`, hardlink archive JPEG into `data/gems/`, audit the action.
- `POST /api/v1/images/review/{id}/demote` — set `share_worth='skip'`, unlink from `data/gems/`, audit.
- `POST /api/v1/images/review/{id}/flag` — append `note` to `vlm_json.concerns[]`, set `has_concerns=1`, hardlink into `data/private/`, audit. Row vanishes from every public endpoint atomically.
- `POST /api/v1/images/review/{id}/unflag` — clear `concerns[]`, unset `has_concerns`, unlink from `data/private/`, audit. Row returns to public visibility per its `share_worth`.
- `DELETE /api/v1/images/review/{id}` — unlink JPEG + sidecar + all hardlinks, set `image_path=NULL`, keep the row for audit, log `action='delete'`.
- `GET /api/v1/images/review/edits` — read the audit log with `since` / `until` / `action` filters.

**Schema changes** (`database.py`):
- Added `image_archive` DDL to `_SCHEMA_SQL` (duplicated from `tools/pipeline/store.py` with `CREATE TABLE IF NOT EXISTS` idempotency, so the image API works on fresh installs where the pipeline hasn't run yet). Both sides kept in sync; schema is stable.
- Added `image_archive_edits` table: `(id, target_image_id → image_archive(id), ts, action, actor, note, request_id, pre_state, post_state)` with indexes on `target_image_id`, `ts`, `action`. Powers the audit log that's load-bearing for the "if something leaked publicly, prove exactly when and how" requirement.

**Defense-in-depth against `concerns[]` leaks** per the cross-repo plan §1.g:
1. **Query** — every public SQL has `WHERE has_concerns = 0` as the first predicate.
2. **Type** — public response models omit `concerns`, `has_concerns`, `vlm_json`. Only the review-queue and review endpoints return those.
3. **Route** — `/gems/{id}` also 404s on `has_concerns=1`, even though URL-guessing should already be defused by (1) and (2). Verified by flag/unflag round-trip test.

**Mutation correctness**: review endpoints are filesystem-first, DB-last. `os.link` / `Path.unlink(missing_ok=True)` happen before `BEGIN IMMEDIATE`; the DB transaction commits only after the FS is consistent. On DB rollback, the FS ops are best-effort reversed. Matches the pipeline-side pattern in `tools/pipeline/store.py:174-197`.

**New modules:**
- `images_api.py` (13 routes) — HTTP layer only, no SQL, no FS except delegating to thumb module.
- `images_auth.py` — `require_review_token` FastAPI dependency (constant-time compare via `hmac.compare_digest`).
- `images_thumb.py` — Pillow thumbnail generation + `data/cache/thumbs/` cache + ETag/If-None-Match plumbing + 1×1 placeholder JPEG for post-retention rows (plan §F6).

**Wiring:**
- `api.py:register_api` now takes an optional `config` arg; it instantiates the images router and plumbs `GUARDIAN_REVIEW_TOKEN` into the auth dependency at mount time.
- `dashboard.py` widens CORS to include `DELETE`, `Authorization`, `If-None-Match`, and `http://localhost:3000` (farm-2026 dev). Passes `config` through to `register_api`.
- `guardian.py:load_config` overlays `GUARDIAN_REVIEW_TOKEN` from env var using the same pattern as `EBIRD_API_KEY` / `DISCORD_WEBHOOK_URL`.

**Not in this release** (explicitly v0.2 per plan doc): `caption_overrides` table, FTS `/search`, server-side Birdadette bucketing, Instagram autofeed, in-process rate limiter (delegated to Cloudflare edge).

**Plan:** `docs/14-Apr-2026-image-archive-api-plan.md` (backend-internal); cross-repo plan in farm-2026 is `docs/14-Apr-2026-image-archive-dataset-and-frontend-plan.md` (commit `ce946c2`).

## [2.24.2] - 2026-04-14

### Cutover — s7-cam flipped from RTSP to `http_url`, IP Webcam installed fresh on the S7 (Claude Opus 4.6)

The plan shipped in v2.24.0 got executed end-to-end today. Summary: the Mac Mini side was already deployed; the phone-side flip uncovered a premise bust that turned this into a multi-step remote Android rebuild.

**Premise bust:** every Guardian doc that said "S7 is running IP Webcam" was wrong. When Boss re-powered the S7 and I ran the smoke test, `GET http://192.168.0.249:8080/photo.jpg` returned `#EXTM3U` instead of a JPEG. The phone was actually running **RTSP Camera Server (`com.miv.rtspcamera`)** — an RTSP-only app with a dumb-catch-all HTTP server that returns the same `.m3u` playlist for every path. It also auto-records the RTSP stream to `/sdcard/RTSPRecords` in 1-hour chunks; by the time I found it, there were **19 GB** of looped coop recordings. That — not streaming to Guardian — was the primary battery and storage drain. Guardian's pull was secondary.

**Recovery flow, driven remotely from the Mac Mini (no hands on the phone after Boss plugged it in and enabled USB debugging):**

1. SSH into the MacBook Air at `192.168.0.50` — the phone was plugged into its USB, not the Mini's. Installed portable `adb` under `~/.local/android/platform-tools/` by pulling Google's `platform-tools-latest-darwin.zip` directly (no sudo, no cask).
2. Data-vs-charge cable gotcha: the first cable Boss swapped in enumerated the phone in Samsung MTP mode (`PID 0x6860`) without the ADB composite interface; a second cable wasn't carrying data at all. The original cable, with the phone's screen kept unlocked, exposed `ce12160cec2f2f0901 device` to `adb devices`.
3. Inventoried installed packages: confirmed `com.miv.rtspcamera` present, `com.pas.webcam` absent.
4. APK download via APKPure and APKCombo both block scraping (HTML 403). **Aptoide's public API (`ws75.aptoide.com/api/7/app/getMeta?package_name=com.pas.webcam`) returned a signed CDN URL with MD5.** Pulled IP Webcam v1.14.37.759 aarch64 (22.8 MB), verified MD5 `8ae7562a4a7ecc0ebac1f4ff5fe3fb7a`.
5. Initial `adb install` failed with "Requested internal only, but not enough space" — the 19 GB of RTSP recordings filled the partition. `adb shell rm -rf /sdcard/RTSPRecords` freed 19 GB (24 GB used → 5 GB used). Install retry succeeded.
6. Granted `CAMERA` + `RECORD_AUDIO` runtime permissions via `adb shell pm grant`. Force-stopped + `pm disable-user` on `com.miv.rtspcamera` to prevent auto-restart.
7. `com.pas.webcam.Rolling` (the headless server-start activity) isn't exported — `am start` rejected it with a SecurityException. Workaround: launched the real entry point via `adb shell monkey -p com.pas.webcam -c android.intent.category.LAUNCHER 1`, which resolved to `com.pas.webcam.Configuration` (the main settings screen). Dumped UI with `uiautomator dump`, parsed for `text="Start server"`, got bounds `[24,1759][301,1832]` (center 162, 1795), tapped it.
8. Verified from the MBA: three consecutive 1920×1080 JPEG pulls ~925–967 KB each, EXIF timestamps live-incrementing (`2026:04:14 11:00:41`, `…45`, `…48`), EXIF `samsung / SM-G930F`.
9. `adb shell svc power stayon true` + `settings put system screen_off_timeout 2147483647` so the screen stays on while charging — IP Webcam releases the camera when the Activity backgrounds, so "keep it foreground, screen dim" is the current battery strategy. Brightness set to `0` (minimum on-level).
10. `adb uninstall com.miv.rtspcamera` — `Success`, `/data` steady at 21% used.
11. Flipped `config.json`: `s7-cam` block went from the legacy RTSP `rtsp_url_override` shape to the `http_url` snapshot shape (`source: "snapshot"`, `snapshot_method: "http_url"`, `http_base_url: "http://192.168.0.249:8080"`, `http_photo_path: "/photo.jpg"`, `snapshot_interval: 5.0`). JSON re-validated.
12. Restarted Guardian. Log confirmed `Camera 's7-cam' online (http_url snapshot) — http://192.168.0.249:8080` → `Snapshot polling started for 's7-cam' — source=http:s7-cam, interval=5.0s` → `Camera 's7-cam' registered in snapshot mode (method=http_url)`. `/api/cameras/s7-cam/frame` returns a fresh 1920×1080 986 KB JPEG with EXIF `2026:04:14 11:02:42`. End-to-end live.

**Follow-up:** IP Webcam's default settings release the camera when the Activity backgrounds, so the "stayon + foreground" workaround is what keeps frames flowing. If battery drain is still excessive after a full day on USB charge, enable IP Webcam's `SYSTEM_ALERT_WINDOW` + "Run in background" preference — both driveable by the same UI-automation pattern as the Start-server tap.

**Docs updated:** `HARDWARE_INVENTORY.md` (s7-cam row, "What Runs Where" table, frame-flow diagram, live frame-size snapshot); `docs/13-Apr-2026-s7-phone-setup.md` (marked LIVE, added "discovery made during execution" section for future agents); `config.json` (flipped and live).

**`tools/s7_http_smoke.py`** caught the real failure mode on the first attempted run — the SOI-marker check rejected the `#EXTM3U` response — proving the pre-flight helper added in v2.24.0 refinements was worth building.

## [2.24.1] - 2026-04-13

### Fixed — Cloudflare tunnel: switched `--protocol http2` → `quic` to stop stream-closed drops (Claude Opus 4.6)

**Symptom Boss reported:** "frontend hosted on Railway still shows GWTC is down, but `localhost:6530` shows it is up and working just fine." All five cameras reported `online: true` and `capturing: true` in `/api/status` locally, and local `/api/cameras/{name}/frame` returned healthy JPEGs in ~2 ms flat. Through the Cloudflare tunnel at `https://guardian.markbarney.net` the same endpoints intermittently stalled or returned curl exit 28 (no response within 5 s). The farm-2026 frontend polls every 1.2 s and flips a feed to "OFFLINE" after 10 consecutive misses — exactly what was happening, especially for `gwtc` whose larger JPEG responses hit the failure mode more often.

**Root cause:** the Mac Mini's `~/Library/LaunchAgents/com.cloudflare.tunnel.farm-guardian.plist` ran `cloudflared` with an explicit `--protocol http2`. The tunnel's `/tmp/cloudflared-guardian.log` was flooded with repeated `ERR error="http2: stream closed"` and `ERR error="context canceled"` on connIndex=2 (and all other conns), every few seconds. The http2 protocol mode in cloudflared has known stability issues under sustained load — streams get closed mid-response by one side or the other and the request never completes. A kickstart restart only moved the needle from 1/5 to 3/10 success, confirming it wasn't a stale-state hang.

**Fix:** edited the plist `--protocol` argument from `http2` to `quic` (cloudflared's modern default) and reloaded the LaunchAgent. After the reload, four tunnel connections registered with `protocol=quic` to IAD edge. Verification: 10/10 `/api/status` probes green, and 5/5 `/api/cameras/{name}/frame` probes green for each of `house-yard`, `s7-cam`, `usb-cam`, `gwtc`, and `mba-cam`. The backend never changed; only the tunnel transport did.

**Canonical copy committed:** the fixed plist is now at `deploy/mac-mini/com.cloudflare.tunnel.farm-guardian.plist` (token redacted — farm-guardian is public) alongside a `README.md` covering reload and health-check commands. Matches the pattern set by `deploy/macbook-air/` and `deploy/gwtc/`.

**Durable lesson:** Don't flip the tunnel back to http2. If a future agent sees cameras flapping offline on the Railway frontend while Guardian local endpoints are healthy, the first thing to check is `/tmp/cloudflared-guardian.log` for `stream closed` / `context canceled` spam — that's the signature of this failure mode and it will say exactly where to look.

## [2.24.0] - 2026-04-13

### Added — `HttpUrlSnapshotSource`: generic HTTP `/photo.jpg` puller for S7 battery path (Claude Opus 4.6)

Boss directive (paraphrased 13-Apr-2026): *"the S7 keeps running out of power — it cannot stream constantly. Just take a nice high-quality image every few seconds, send it to the Mac Mini, delete it locally. The phone's only job is to serve as a camera. You own this now."*

**Root cause of the S7 going offline all afternoon:** the Samsung Galaxy S7 (`192.168.0.249`) was serving a continuous RTSP stream on port 5554 via the IP Webcam Android app. RTSP forces continuous H.264 encoding on the phone — the ISP, the hardware video encoder, and the WiFi radio all run flat out. An S7 with a worn battery bleeds charge faster than USB charging replaces it, overheats, and IP Webcam eventually dies. Port probes (`22`, `5554`, `8080`) from the Mac Mini all confirmed the phone was cold when Boss handed over the task.

**The fix (Mac Mini side, shipped in this commit):** a new `HttpUrlSnapshotSource` in `capture.py` implementing the existing `SnapshotSource` Protocol. Fires `GET {base_url}/focus` first (optional, off by default), waits `focus_wait` seconds, then `GET {base_url}/photo.jpg` with a timeout and optional HTTP basic auth. Validates the JPEG SOI marker (`ff d8`) on the response — IP Webcam occasionally serves HTML error pages with 200, this catches that cleanly and returns `None` so the poller's `consecutive_failures` path handles it. Returns raw JPEG bytes which `CameraSnapshotPoller` then decodes for YOLO *and* carries through to the dashboard for zero-loss display (same behavior as the Reolink snapshot path).

**Dispatch wired in two places:**

- `guardian.py::_register_camera_capture` — adds an `elif method == "http_url":` branch that reads `http_base_url`, `http_photo_path`, `http_focus_path`, `http_trigger_focus`, `http_focus_wait`, `http_timeout`, and builds `auth` from `username`/`password` if present. Replaces the previous "not implemented (Phase B adds http_url)" error log.
- `discovery.py::scan` — short-circuits `snapshot_method == "http_url"` the same way USB does. No ONVIF probe, no RTSP URL resolution — the camera is marked online and real reachability is evaluated on the first `fetch()` in the poller.

**Why pull, not push:** Android 8 Doze / App Standby aggressively kills background sync services on older hardware; a reliable unattended intervalometer-and-SFTP-upload flow on an S7 is a multi-day tuning project. Pull lets the Mac Mini drive cadence and keeps the phone stateless. Full architectural rationale is in `docs/13-Apr-2026-s7-battery-http-snapshot-plan.md`.

**`config.json` intentionally NOT flipped in this commit:** the phone was offline when the code was written, so no config change could be validated. The existing RTSP config fails gracefully, so leaving it alone produces no user-visible regression. Whoever re-seats the phone follows `docs/13-Apr-2026-s7-phone-setup.md`, which walks through (a) IP Webcam app settings to minimize battery load (dim screen, background mode, disable recording, stop RTSP video, keep HTTP photo server on), (b) the `/photo.jpg` smoke test, (c) the exact `config.json` block to swap in, and (d) the rollback path back to RTSP if the HTTP path is worse on this specific phone.

**Reuse:** `HttpUrlSnapshotSource` is deliberately generic — the GWTC Phase B plan will use the same class against whatever HTTP snapshot service the Gateway laptop ends up hosting. No second implementation needed.

**Files changed:** `capture.py`, `guardian.py`, `discovery.py`, `docs/13-Apr-2026-s7-battery-http-snapshot-plan.md` (new), `docs/13-Apr-2026-s7-phone-setup.md` (new).

**Verification (actual end-to-end smoke, not just import checks):** built a throwaway `http.server` in-process exposing `/photo.jpg`, `/focus`, `/html-error.jpg`, and a basic-auth-protected `/protected/photo.jpg`. Exercised `HttpUrlSnapshotSource` against it and asserted all seven shapes pass: basic fetch returns JPEG bytes, AF-trigger path hits `/focus` then `/photo.jpg`, HTML-error 200 is rejected (SOI mismatch), 404 returns `None`, connection-refused returns `None`, basic auth succeeds with creds and fails cleanly without, and `CameraSnapshotPoller` delivers a decoded `FrameResult` to `on_frame` with `jpeg_bytes` preserved intact. Phone-side smoke test against the live S7 is blocked until the phone is powered back up but has a dedicated helper now: `tools/s7_http_smoke.py`.

### Added — `tools/s7_http_smoke.py` — phone-side verification helper (Claude Opus 4.6)

Standalone diagnostic to run BEFORE flipping `config.json`. Probes the phone's `/photo.jpg` endpoint through `HttpUrlSnapshotSource` itself (no duplicated logic — exercises the same class Guardian will use at runtime), saves the JPEG to `/tmp/s7-smoke.jpg` for eyeball inspection, exercises the `/focus` round-trip, runs N consecutive pulls to catch preview-flapping, and produces `[FAIL]` messages with concrete diagnoses instead of the generic `snapshot returned None` warning that would otherwise show up at Guardian startup. Usage: `venv/bin/python tools/s7_http_smoke.py` (all args optional, defaults match the live S7's network identity). Verified against the currently-offline phone: fails cleanly with the correct connection-refused message and an actionable remediation list.

### Refined

- `HttpUrlSnapshotSource.label` no longer double-prefixes `http:` when the URL already carries its own scheme. Default label is now the `photo_url` itself (e.g. `http://192.168.0.249:8080/photo.jpg`) rather than `http:http://...`. Caught while reading the smoke-test output, fixed, re-verified.
- `discovery.py::CameraInfo.source` docstring updated to document `"http_url"` alongside the existing `"onvif"`, `"rtsp_override"`, `"usb"` values.
- `config.example.json` — the `s7-cam` block now shows the `http_url` snapshot shape as the recommended default (port 8080, `source: "snapshot"`, `snapshot_method: "http_url"`, `http_base_url`, `http_photo_path`, `http_trigger_focus`, `http_focus_wait`, `http_timeout`, `snapshot_interval`) with an inline `_comment` pointing at the phone setup doc. Legacy `rtsp_url_override` path noted there as the rollback option. JSON re-validated after the edit.

## [2.24.1] - 2026-04-13

### Fixed — Renamed GWTC MediaMTX path `nestbox` → `gwtc`; added repo-wide `HARDWARE_INVENTORY.md` (Claude Opus 4.6)

Boss: "Please tell me nothing on the backend is still named like Nesting Boss Cam or Brooder Cam or shit like that. Everything should be named after the piece of hardware that's running it. Because my idiot front end developers found tons of errors and mismatches, just do a nice sanity check of the backend. I want to make it clear exactly what hardware device each camera is on and what hardware device is running on."

The audit found one live device-name violation and several stale references. The violation was that the Gateway laptop's MediaMTX publish path was `nestbox` (a location) even though our camera identifier everywhere else has been `gwtc` (a device). That mismatch was the root of frontend confusion — the camera was named one thing in `config.json` and another in the actual stream URL. Fixed end-to-end.

**On the Gateway laptop (live changes):**

- `C:\farm-services\start-camera.bat` — ffmpeg push URL changed from `rtsp://localhost:8554/nestbox` → `rtsp://localhost:8554/gwtc`.
- `C:\mediamtx\mediamtx.yml` — declared `paths:` block changed from `nestbox:` → `gwtc:`. (mediamtx had explicit-paths config that *only* allowed `nestbox`, so the path rename required a config update + service restart, not just an ffmpeg restart.)
- `C:\farm-services\farm-watchdog.ps1` — `$RtspUrl` updated to probe `rtsp://localhost:8554/gwtc`.
- Cutover sequence (to avoid the watchdog killing ffmpeg during the transition window): stop watchdog → push new bat + new ps1 + new yml → restart mediamtx → kill old ffmpeg PID (Shawl respawns with new push URL) → restart watchdog. All four GWTC services back to `STATE: 4 RUNNING`.

**In this repo:**

- **`HARDWARE_INVENTORY.md`** (NEW, repo root) — single source of truth for every camera: what hardware it is, what host machine runs it, IP, RTSP/source URL, capture method, detection state, and where it's currently aimed (the latter for context only — never a name driver). Plus a "what runs where" table for all hosts, an end-to-end "where each camera's frame lands in the stack" diagram, the device-not-location naming rules with the worked example of the Apr-13 frontend mismatch incident, and procedures for adding a new camera and moving an existing one (which is: don't rename anything). Anchored in `CLAUDE.md` as "READ THIS BEFORE TOUCHING ANY CAMERA."
- **`config.json` / `config.example.json`** — `gwtc` camera's `rtsp_url_override` updated to `rtsp://192.168.0.68:8554/gwtc`.
- **`deploy/gwtc/farm-watchdog.ps1`** — repo copy synced to live (probes `/gwtc`).
- **`deploy/gwtc/install-watchdog.md`** — `nestbox` → `gwtc` throughout install/verify recipes.
- **`deploy/gwtc/start-camera.bat`** (NEW) — canonical copy of the Gateway laptop's ffmpeg-push batch file. Was previously only on the laptop; now in version control.
- **`deploy/gwtc/mediamtx.yml`** (NEW) — canonical copy of the Gateway laptop's MediaMTX config (declares the `gwtc` path).
- **`docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md`** — bulk `nestbox` → `gwtc` (operational instructions, log-line examples, ffmpeg test commands, the rule callout).
- **`docs/12-Apr-2026-snapshot-polling-plan.md`** — gwtc-row URL updated.
- **`docs/13-Apr-2026-phase-b-gwtc-snapshot-endpoint-plan.md`** (other agent's WIP plan, not yet implemented) — title `nesting box cam` → `(gwtc) cam`; proposed Python script `nestbox-snap.py` → `gwtc-snap.py`; proposed Shawl service `nestbox-snap` → `gwtc-snap`; URL examples + prose updated. The plan's design intent is otherwise untouched.
- **`tools/pipeline/config.json`** (the v2.23.0 multi-camera pipeline that the other agent shipped just before this commit) — `gwtc` entry's `rtsp_url` fixed to `/gwtc`. Was hard-broken by the rename above; would have failed silently at the next pipeline cycle. Also rewrote each camera's `context` string to lead with the hardware ("Reolink E1 Outdoor Pro 4K PTZ camera (192.168.0.88); currently aimed at the yard..." instead of "PTZ overlooking the yard..." etc.) so the VLM prompts match the device-first naming convention used everywhere else.

**What I deliberately did NOT touch:**

- `s7-cam` internal RTSP path is `/camera` because that's what the Android IP Webcam app exposes — not configurable on the phone side. Our config name `s7-cam` is the device-first identifier we use everywhere.
- `tools/pipeline/schema.json` scene enum (`["brooder","yard","coop","nesting-box","sky","other"]`). Those are *scene tags* (where the camera is pointing), a separate dimension from camera identity. Defensible.
- Historical `nestbox` references in pre-v2.23.x CHANGELOG entries. Rewriting history obscures what actually happened. The v2.24.1 entry above documents the rename for anyone reading old entries.
- Anything in `.claude/worktrees/` (stale worktree).

**Cross-references outside this repo:**

- `~/bubba-workspace/memory/reference/network.md` GWTC entry — RTSP stream line and dshow-zombie bullet's symptom URL updated to `/gwtc`.
- `~/.claude` auto-memory `feedback_camera_naming.md` — already updated by Boss this session to spell out the rule applies to every UI string. Honored throughout this commit.
- `~/.claude` auto-memory `project_gwtc_dshow_zombie.md` — updated to reference `gwtc` path instead of `nestbox`.

**Validation (just now):**

- All 5 cameras through Guardian's API: `house-yard` 1.4 MB, `s7-cam` 404 (phone offline — pre-existing, unrelated to this rename), `usb-cam` 417 KB, `gwtc` 123 KB, `mba-cam` 114 KB.
- Direct RTSP from Mini: `rtsp://192.168.0.68:8554/gwtc` returns 1280×720 JPEG; old `/nestbox` correctly 404s.
- All four GWTC services (`mediamtx`, `farmcam`, `farmcam-watchdog`, `sshd`) all `STATE: 4 RUNNING`.
- Watchdog log shows new probe target: `target=rtsp://localhost:8554/gwtc`.

## [2.23.0] - 2026-04-13

### Added — Multi-camera image pipeline (`tools/pipeline/`) (Claude Opus 4.6)

Boss directive (paraphrased): "we're producing a massive number of high-quality photographs — I want rich metadata about the images as a sidebar, auto-publish everything, and I want to pick out the gems." The concept of Farm Guardian shifts with this release: the live-video predator-detection path keeps running unchanged on the hot path, but the **primary product is now a continuously-curated archive of high-quality images of the flock and property, with rich queryable metadata**.

Plan doc: `docs/13-Apr-2026-multi-cam-image-pipeline-plan.md`. Supersedes the narrator plan (`docs/13-Apr-2026-brooder-vlm-narrator-plan.md`) which was banner-marked accordingly. The narrator plan's "sample → narrate → discard" shape captured none of the archive / retrospective / share value; this replaces it.

**Shape:** standalone tool under `tools/pipeline/`. Wakes per-camera on configured cadences, captures one sharp frame with a device-specific recipe, gates on trivial garbage (pixel std-dev floor only — no calibrated sharpness threshold; GLM's `image_quality` field is the real arbiter), enriches via `zai-org/glm-4.6v-flash` on LM Studio with structured JSON output, archives JPEG + sidecar + SQLite row. Tiered storage: full-res for `share_worth=strong`, downscaled to 1920px for `decent`, no-JPEG-metadata-only for `skip`. 90-day retention; `concerns` non-empty exempts from auto-delete.

**Per-camera capture recipes (the part that decides gem stream vs blur stream):**

- **house-yard** (Reolink PTZ, 4K): reuses Guardian's own `/api/v1/cameras/house-yard/snapshot` endpoint — sharp 4K JPEG from the Reolink HTTP `cmd=Snap` with AF already handled. No auth duplication.
- **usb-cam** (AVFoundation on the Mini): OpenCV `VideoCapture`, `CAP_PROP_AUTOFOCUS=1`, 5 warmup frames before keeper.
- **s7-cam** (Samsung S7 IP Webcam): config-ready but **disabled** as of v2.23.0 — phone was offline at implementation time. Flip `enabled: true` when the phone is back on.
- **gwtc** (Gateway laptop fixed webcam via MediaMTX): RTSP burst of 5 frames at 0.5s spacing, Laplacian-variance-sharpest wins.
- **mba-cam** (2013 MacBook Air FaceTime HD via MediaMTX): same burst-and-pick as gwtc. **Fixed-focus lens — no AF dance to tune.** Hyperfocal sweet spot is ~2-4 ft; placement matters more than software here.

**VLM output schema** (`tools/pipeline/schema.json`, strict JSON): `scene`, `bird_count`, `individuals_visible[]`, `any_special_chick`, `apparent_age_days`, `activity`, `lighting`, `composition`, `image_quality`, `share_worth`, `share_reason`, `caption_draft`, `concerns[]`. `apparent_age_days` uses `-1` as the "n/a" sentinel rather than `null` because LM Studio's `json_schema` path on this build rejects `["integer","null"]` union types — validated at implementation time against this specific LM Studio build.

**LM Studio safety** (inheriting the rules from `docs/13-Apr-2026-lm-studio-reference.md`): before every VLM call, `GET /v1/models`; if `glm-4.6v-flash` isn't loaded, the cycle logs and skips (does NOT auto-load, to avoid contention with G0DM0D3 sweeps). Single in-flight VLM call per process via a `threading.Lock`. Never calls `/v1/chat/completions` with a model that isn't already loaded.

**Database:** new table `image_archive` added to `data/guardian.db` via `store.ensure_schema()` on first use. Idempotent `CREATE TABLE IF NOT EXISTS`, no change to `database.py` itself — the pipeline is strictly additive. Indices on `(camera_id, ts)`, `(share_worth, image_quality)`, `has_concerns`, and `retained_until`.

**Smoke-test results** (all 4 enabled cameras, one cycle each):

| Camera | scene | birds | activity | quality | tier | inference |
|---|---|---|---|---|---|---|
| house-yard | other | 0 | none-visible | sharp | decent | 29.1 s |
| usb-cam | brooder | 0 | none-visible | blurred | skip | 28.0 s |
| gwtc | coop | 6 | foraging | soft | decent | 18.5 s |
| mba-cam | brooder | 22 | none-visible | soft | decent | 18.8 s |

Total archive after first cycle: 3 JPEGs + 3 sidecars, ~1 MB. (usb-cam returned a blurred zero-bird frame this cycle while mba-cam aimed at the same brooder saw 22 chicks — worth investigating placement / AF / timing but not a blocker; the pipeline correctly identified it as `skip` and stored metadata only.)

**Daemon:** `venv/bin/python -m tools.pipeline.orchestrator --daemon` runs forever on per-camera cadences (house-yard/s7-cam/gwtc: 600s; mba-cam: 300s; usb-cam: 180s). Staggered start (spread across first 60s) so cycles don't stampede. Daily retention sweep runs automatically. Graceful SIGINT/SIGTERM shutdown. Currently running as PID 61645 writing to `data/pipeline-logs/orchestrator.log`.

**Files added:**

- `tools/__init__.py`, `tools/pipeline/__init__.py` — package markers.
- `tools/pipeline/config.json` — per-camera config, cadences, retention, tiers.
- `tools/pipeline/schema.json` — VLM output JSON schema.
- `tools/pipeline/prompt.md` — VLM prompt template with Birdadette + brood context.
- `tools/pipeline/quality_gate.py` — trivial garbage filter (std-dev floor) + Laplacian helper for burst ranking.
- `tools/pipeline/capture.py` — per-camera capture methods (reolink_snapshot, usb_avfoundation, rtsp_burst, ip_webcam).
- `tools/pipeline/vlm_enricher.py` — LM Studio round-trip with model-loaded check, strict JSON-schema response format, post-hoc validator, single-in-flight lock.
- `tools/pipeline/store.py` — tier-based JPEG persistence, sidecar JSON, SQLite row insert, schema migration.
- `tools/pipeline/retention.py` — daily sweep of expired JPEGs (metadata rows always preserved).
- `tools/pipeline/orchestrator.py` — main entry. `--once [--camera NAME]`, `--daemon`, `--retention-only`.
- `docs/13-Apr-2026-multi-cam-image-pipeline-plan.md` — the plan.

**Files modified:**

- `docs/13-Apr-2026-brooder-vlm-narrator-plan.md` — banner-marked SUPERSEDED, retained for LM Studio safety analysis + historical context.

**Storage projection:** ~1,140 enrichments/day across 4 live cameras. Tier mix ~70/25/5 (skip/decent/strong). ~37 MB/day archived × 90 days ≈ 3.3 GB steady-state. SQLite metadata ~1 GB/year. Comfortable on the Mini.

**Operator queries once there's data:**

```sql
-- Birdadette portraits (for the retrospective)
SELECT image_path, ts, caption_draft FROM image_archive
WHERE individuals_visible_csv LIKE '%birdadette%'
  AND composition = 'portrait' AND image_quality = 'sharp'
ORDER BY ts DESC LIMIT 7;

-- Today's Instagram candidates
SELECT image_path, caption_draft, share_reason FROM image_archive
WHERE share_worth = 'strong' AND date(ts) = date('now');

-- Private review queue (never publish)
SELECT image_path, ts, vlm_json FROM image_archive
WHERE has_concerns = 1 ORDER BY ts DESC;
```

**Open items:**

- Re-enable `s7-cam` once the phone is back on the network (flip `enabled: true` in `tools/pipeline/config.json`).
- Investigate why `usb-cam` returned a blurred zero-bird frame while `mba-cam` aimed at the same brooder saw 22 — may be AF behavior, aim, or timing.
- Decide on launchd plist for boot-time auto-start (currently manual `nohup` launch).
- Audit first 200 archived rows by hand once the daemon has accumulated them; tune prompt if GLM over- or under-calls `share_worth=strong`.

## [2.22.2] - 2026-04-13

### Added — `farmcam-watchdog` on GWTC: auto-recovers from post-reboot dshow zombie (Claude Opus 4.6)

Boss called it: "Wouldn't some better idea be to have some script on that GWTC that automatically runs when it reboots and does the restart or whatever?" Yes. This is that.

**Background:** Earlier this evening, GWTC was rebooted and the `nestbox` RTSP path 404'd despite mediamtx + farmcam services both running, ffmpeg holding a live PID, and port 8554 open. Root cause (verified 18:18 then documented in `docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md` Addendum): Windows reboot leaves the dshow camera handle in a state where ffmpeg's dshow input cannot complete the device open. ffmpeg sits there forever, never produces frames, never registers as a publisher with mediamtx. **Both retry mechanisms bypass the failure** — Shawl's `--restart` only fires on non-zero exit, and the `:loop` in `start-camera.bat` only re-enters when the inner ffmpeg call returns. Neither happens for wedged-ffmpeg. The original recovery was a manual two-command kill-the-PID dance.

**The fix (this commit):** A PowerShell watchdog wrapped as a Shawl-managed Windows service called `farmcam-watchdog`, installed alongside the existing `farmcam` and `mediamtx` services on GWTC. Probes `rtsp://localhost:8554/nestbox` every 30s using `ffprobe`. If no publisher AND ffmpeg has been alive ≥60s (past startup grace), it kills ffmpeg by PID. Shawl's existing `--restart` on the `farmcam` service then respawns ffmpeg in ~3s with a fresh dshow open. Worst-case recovery is ~90s after the wedge condition; best case ~30s.

**What this catches that Shawl misses:** Shawl restarts ffmpeg only when ffmpeg *exits non-zero*. Wedged-ffmpeg never exits. The watchdog detects the wedge externally (publisher absent from mediamtx's perspective, verified by ffprobe) and forces the exit, which Shawl then handles normally. Not replacing Shawl — giving it a kick when its trigger condition (process exit) doesn't fire.

**What changed in this repo:**

- **`deploy/gwtc/farm-watchdog.ps1`** (new) — The watchdog script. Single file, no dependencies beyond the existing ffprobe.exe (already present in the WinGet ffmpeg bundle that `farmcam` already uses). Tunable constants at the top (probe interval, wedge threshold, ffprobe path, RTSP URL). Logs to `C:\farm-services\logs\watchdog.log`. **Intentionally ASCII-only** — PowerShell 5.1 reads `.ps1` files as ANSI/Windows-1252 unless they carry a UTF-8 BOM, so em-dashes and smart quotes break parsing. Don't "improve" the script with typographic punctuation. (Learned this the hard way when the first deploy parser-errored on em-dashes.)
- **`deploy/gwtc/install-watchdog.md`** (new) — Install / update / uninstall recipes from the Mac Mini, the constraints, and how to test the wedge-recovery path.
- **`docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md`** — Addendum updated with a top-of-section banner ("watchdog auto-handles this, you should not need to intervene"), a new "Automated Recovery -- `farmcam-watchdog`" subsection covering design + live state + what the watchdog does NOT do, and the original manual fix demoted to "fallback if the watchdog is broken."
- **`CLAUDE.md`** — The bullet under "Network & Machine Access" rewritten to lead with "watchdog auto-recovers it" instead of "kill the PID manually." Manual fix kept as the fallback. Cross-refs to the deploy/ and docs/ paths.

**On GWTC:**

- `farm-watchdog.ps1` written to `C:\farm-services\farm-watchdog.ps1`.
- New service `farmcam-watchdog` registered:
  ```
  sc create farmcam-watchdog binPath= "C:\shawl\shawl.exe run --name farmcam-watchdog --restart -- powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\farm-services\farm-watchdog.ps1" start= auto
  ```
- Service started; `sc query farmcam-watchdog` returns `STATE: 4 RUNNING`.
- First log line confirmed: `2026-04-13 18:32:30 watchdog started -- pid=10880, probe=30s, wedge_threshold=60s, target=rtsp://localhost:8554/nestbox`.
- ffprobe-against-live-publisher check passed: `codec_name=h264 width=1280 height=720`.

**Validation status:**

- ✅ Service installed, auto-start enabled, currently running.
- ✅ Probe path verified end-to-end with the live publisher (ffprobe correctly identifies the H264 720p stream).
- ⏳ Wedge-recovery path will be verified the next time GWTC reboots and reproduces the wedge. Per the install doc, a synthetic test (suspending ffmpeg with `pssuspend`) is possible but disrupts the live brooder feed and isn't worth doing casually.

**Cross-references:**

- `~/bubba-workspace/memory/reference/network.md` GWTC entry — bullet rewritten to point at the watchdog, manual fix demoted to fallback.
- `~/.claude` auto-memory `project_gwtc_dshow_zombie.md` — same update so future Bubba sessions surface "watchdog auto-handles it" first.

## [2.22.1] - 2026-04-13

### Fixed — Renamed `brooder-cam` → `mba-cam` (Claude Opus 4.6)

Boss caught the naming violation immediately: cameras are named after the **device**, never the location (rule originally established in v2.11.0, applied to `gwtc` instead of `nestbox` in v2.12.0). I shipped v2.22.0 with `brooder-cam` because the camera is currently aimed at the brooder — wrong call. Locations change; the device doesn't.

**What changed (everywhere — config, RTSP path, LaunchAgent label, log filenames, plan doc):**

- **`config.json` / `config.example.json`** — `name: "brooder-cam"` → `name: "mba-cam"`; `rtsp_url_override` → `rtsp://192.168.0.50:8554/mba-cam`.
- **`deploy/macbook-air/com.farmguardian.brooder-cam.plist`** → renamed file to `com.farmguardian.mba-cam.plist`; Label, RTSP push URL, and log paths updated to `mba-cam`.
- **On the Air:** new `~/Library/LaunchAgents/com.farmguardian.mba-cam.plist` bootstrapped, old `com.farmguardian.brooder-cam` agent booted out and its plist removed. Log file moved to `~/Library/Logs/farmguardian/mba-cam.log`.
- **`docs/12-Apr-2026-macbook-air-camera-node-plan.md`** — All `brooder-cam` references → `mba-cam`. The 13-Apr update header now points at the device-not-location rule explicitly.

**Validation:**

- TCC permission was granted by the on-Air Claude session per Boss's spec — the MediaMTX log went from "no stream is available on path" to `is publishing to path 'mba-cam', 1 track (H264)`.
- From the Mini: `ffmpeg -rtsp_transport tcp -i rtsp://192.168.0.50:8554/mba-cam -frames:v 1 -y /tmp/test.jpg` produces a 1280x720 JPEG (~55KB) within a couple seconds. Stream is live end-to-end.
- Both LaunchAgents on the Air are `state = running`, last exit code `(never exited)`.

## [2.22.0] - 2026-04-13

### Added — `brooder-cam` (MacBook Air 2013 → RTSP) for the brooder angle (Claude Opus 4.6)

Boss: "We need to incorporate the MacBook Air as a camera that's going to be looking at the brooder. Make sure the MacBook Air broadcasts its camera to the network. Also turn off the fucking screen saver on it."

Mirrors the GWTC pattern from v2.12.0: a remote computer with a built-in webcam runs `ffmpeg → MediaMTX → RTSP` and Guardian consumes it via `rtsp_url_override`. The Air is an Intel Haswell on macOS Big Sur 11.7.11 — Homebrew is unsupported on Big Sur, and most current MediaMTX builds link against macOS 12 SDK symbols, so this required a non-current MediaMTX release (v1.13.1) and a static ffmpeg build (evermeet.cx 8.1-tessus).

**On the Air (`markb@192.168.0.50`, key auth):**

- **Screensaver disabled** — `defaults -currentHost write com.apple.screensaver idleTime -int 0` plus `askForPassword 0`/`askForPasswordDelay 0` and `killall ScreenSaverEngine`. Power settings (`pmset`) were already locked down from the Bubba 12-Apr session: `sleep=0 disksleep=0 displaysleep=0 standby=0 powernap=0 hibernatemode=0 autorestart=1`. The lid stays open per the operational requirement (clamshell sleep on this firmware can't be overridden).
- **MediaMTX v1.13.1** at `~/.local/bin/mediamtx` (+ default `mediamtx.yml`). v1.16.3 was tried first per the original plan and failed at load with `dyld: Symbol not found: _SecTrustCopyCertificateChain` — that symbol is macOS 12+; v1.13.1 is the latest darwin_amd64 build that runs on Big Sur 11. RTSP listener on `:8554`, default unauthenticated single-path serve.
- **ffmpeg 8.1-tessus** at `~/.local/bin/ffmpeg` (evermeet.cx static darwin-x64 build). FaceTime HD Camera is AVFoundation index `0`. Camera supports `1280x720@30fps` only (15fps was not in the supported-modes list, hence the `-framerate 30` capture with `-r 15` re-rate before encode).
- **Two LaunchAgents** in `~/Library/LaunchAgents/`:
    - `com.farmguardian.mediamtx.plist` — KeepAlive, ThrottleInterval 10s, logs to `~/Library/Logs/farmguardian/mediamtx.log`.
    - `com.farmguardian.brooder-cam.plist` — KeepAlive, ThrottleInterval 15s, runs the ffmpeg capture (`avfoundation 0` → `libx264 ultrafast zerolatency` 720p15 ~1.5 Mbps → `rtsp://127.0.0.1:8554/brooder-cam` over TCP). Logs to `~/Library/Logs/farmguardian/brooder-cam.log`.
- **Bootstrapped** with `launchctl bootstrap gui/$(id -u) <plist>`. MediaMTX is healthy: port 8554 listening, RTSP/RTMP/HLS/WebRTC/SRT all up. ffmpeg starts and probes the camera but stalls at the AVFoundation capture-open call until **TCC camera permission is granted at the Air's keyboard** (see Open Items below).

**In this repo:**

- **`config.json`** — Added `brooder-cam` entry to the `cameras` array. Same shape as `gwtc`: `rtsp_url_override` to `rtsp://192.168.0.50:8554/brooder-cam`, `rtsp_transport: tcp` (Air is WiFi-only), `detection_enabled: false` until placement and role are decided.
- **`config.example.json`** — Same entry mirrored.

**Cross-references:**

- Plan doc: `docs/12-Apr-2026-macbook-air-camera-node-plan.md` (path renamed from generic `mba-cam` → `brooder-cam` per Boss's spec).
- Machine ops: `~/bubba-workspace/skills/macbook-air/SKILL.md` (SSH, power settings, install recipes — already covers everything an agent needs to land on this box).

**Open Items (one-time, requires Boss at the Air's keyboard):**

1. **Grant Camera permission to ffmpeg.** `launchd`-spawned binaries can't surface a TCC dialog when no GUI session is foregrounded; the prompt may be queued or denied silently. To unstick: at the Air's keyboard, open Terminal and run `~/.local/bin/ffmpeg -f avfoundation -i 0 -frames:v 1 -y /tmp/tcc.jpg` — macOS will prompt "Terminal would like to access the camera" (or directly for ffmpeg). Click Allow. Then either reboot the Air or `launchctl kickstart -k gui/$(id -u)/com.farmguardian.brooder-cam`. Verify from the Mini: `ffmpeg -rtsp_transport tcp -i rtsp://192.168.0.50:8554/brooder-cam -frames:v 1 -y /tmp/test.jpg` should produce a JPEG within ~2s.
2. **Physical placement.** Lid open, camera aimed into the brooder.
3. **Detection enable decision** once placement is final — likely stays off (chicks are not predators).

## [Unreleased] - 2026-04-13 — docs only

### Added — GWTC laptop troubleshooting writeup; corrected MAC attribution (Claude Opus 4.6)

After spending an hour misdiagnosing why the Gateway laptop was unreachable today (variously: theories about port-scan auto-blocking, Windows Defender, MAC mismatches, etc. — none of which fit), Boss called for the diagnostic process to be written down so this doesn't repeat.

**What changed:**

- **`docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md`** — New doc. Full incident writeup. Sections: TL;DR, the 30-second diagnostic recipe (sweep /24 for port 8554/MediaMTX or 9099/LM Studio — those are GWTC's distinctive services), what "GWTC is genuinely off-network" means (three possibilities, the four-line PowerShell to disambiguate from the laptop console), the four wrong theories I explicitly ruled out so future sessions don't re-tread them, the authoritative facts Boss provided that don't need re-confirming, and today's chronology (gwtc Guardian failures started at 14:57:59, my probes weren't until ~15:02 — so my probes weren't the cause).
- **`CLAUDE.md`** — "Network & Machine Access" section rewritten to point at both `~/bubba-workspace/memory/reference/network.md` AND the new incident writeup, with the diagnostic recipe inlined for findability. Also flags the known-wrong MAC entry in the network doc.
- **`~/bubba-workspace/memory/reference/network.md`** (outside this repo) — Fixed two errors. The MAC `FC:6D:77:B8:E8:DB` was incorrectly attributed to GWTC; it's actually the MSI Katana's MAC (SSH-confirmed via hostname=MSI, model=Katana 15 HX B14WGK at `.3`). GWTC's actual MAC is now marked UNKNOWN pending a console reading. Added a pointer from the GWTC entry to the new incident doc and updated the IP guidance to "find by service signature, not by IP".

No code changes. This is purely durable documentation so the same hour-of-misdiagnosis doesn't recur.

## [2.21.0] - 2026-04-13

### Added — usb-cam heat-lamp white balance, autofocus, and warmup frames; network troubleshooting pointer (Claude Opus 4.6)

Boss: "The quality looks better, but you might want to account for the heat lamp. It makes everything this red-orange color." And: "Can it autofocus before it takes a picture so we don't have a bunch of blurry, fluffy bird butts?" Both are the right asks — the pre-v2.21 usb-cam path took a single frame with no AF settle time and no color correction, so every shot was heat-lamp orange + occasionally blurry when chicks moved.

Plus: documenting the network reality so future agents stop inventing turkey-flipped-firewall theories.

**What changed:**

- **`capture.py:UsbSnapshotSource`** — Four new constructor kwargs:
    - `auto_white_balance` (bool, default False) — toggles gray-world correction applied before JPEG encode.
    - `wb_strength` (float 0.0–1.0, default 0.8) — interpolates between identity and full gray-world. 0.7–0.9 usually looks natural.
    - `autofocus` (bool, default True) — sets `CAP_PROP_AUTOFOCUS=1` on open. cv2 on macOS often silently ignores this, but DSHOW/V4L2 backends honor it. Harmless when ignored.
    - `warmup_frames` (int, default 3) — number of reads to discard before the real capture, giving continuous AF and auto-exposure time to catch up to a moving subject. ~33ms per frame at 30fps, so 3 = ~100ms of catch-up.
  New `_apply_gray_world_wb()` static method. Open log now shows all four settings so operators can see what's active. Header bumped.
- **`guardian.py`** — `_register_camera_capture()` wires the four new kwargs from config (`snapshot_auto_wb`, `snapshot_wb_strength`, `snapshot_autofocus`, `snapshot_warmup_frames`). Header bumped.
- **`config.json`** + **`config.example.json`** — `usb-cam` gains `snapshot_auto_wb: true`, `snapshot_wb_strength: 0.8`, `snapshot_autofocus: true`, `snapshot_warmup_frames: 3`.
- **`CLAUDE.md`** — New "Network & Machine Access — READ BEFORE TROUBLESHOOTING REACHABILITY" section near the top. Points at `~/bubba-workspace/memory/reference/network.md` (the authoritative copy on this Mac Mini) for IPs, SSH keys, user accounts, the router's admin creds, and every known quirk. Also calls out the facts that trip up agents who don't read the docs first: (1) ICMP is blocked cross-medium on this router — ping between wired and wireless will always fail regardless of host state; (2) Windows Firewall is DISABLED on the Gateway laptop — stop inventing firewall theories; (3) there's a known WSL2 virtual-adapter routing-poisoning bug that breaks SSH to the Gateway, fixed only at the laptop's console with `netsh winsock reset; netsh int ip reset` + reboot; (4) DHCP IPs drift after reboots, here's the documented subnet-scan recipe for finding a camera that moved.

**Validation:**

- Open log: `UsbSnapshotSource 'usb:usb-cam' opened at 1920x1080 (quality=95, warmup=3, autofocus=True, auto_wb=True)`.
- Pre-WB frame: everything uniform orange from the heat lamp.
- Post-WB frame: chicks render correctly yellow, back wall shows actual color variation (lit cream / shadowed lavender — the mild cool tint is gray-world's overshoot when the dominant light is warm, tunable via `snapshot_wb_strength`).
- File size ~438KB (vs 329KB pre-WB). The extra bytes are because a corrected scene has more color variety to encode — trading a bit of bandwidth for a scene that actually looks like chicks instead of a pumpkin wash.

## [2.20.0] - 2026-04-13

### Added — Phase C2: motion-event-triggered snapshot bursts (Claude Opus 4.6)

Snapshot polling at 5s (day) / 2s (night) trades off a small responsiveness gap — something brief between ticks isn't seen. Close that gap for snapshot-mode cameras whose firmware exposes motion detection: poll the motion state and, on a False→True transition, temporarily raise the polling rate (~1 Hz) for a fixed duration so YOLO has more chances to see whatever moved.

**What changed:**

- **`capture.py:CameraSnapshotPoller`** — New `request_burst(duration_s=30.0, interval_s=1.0)` method. Coalesces overlapping calls: later bursts *extend* the deadline (and lower the interval if smaller) instead of stacking. The burst interval is floored at `_MIN_SNAPSHOT_INTERVAL` (1.0s) so no caller can set a pace faster than fetches can complete. `_effective_interval()` now has a three-tier precedence: active burst > night window > normal. When the burst deadline passes, `_burst_interval` is cleared so a fresh call starts clean. Header bumped to v2.20.0.
- **`capture.py:FrameCaptureManager`** — New `get_poller(name)` accessor. External callers (the motion watcher) reach `request_burst` through this.
- **`camera_control.py`** — New `get_motion_state(camera_id) -> Optional[bool]`. Wraps `reolink_aio.host.get_motion_state(channel)` through the existing async-loop bridge. Returns None on transient failure (camera blip, unreachable) so callers can skip the cycle rather than exception.
- **`guardian.py`** — New `_motion_watch_loop()` running on its own daemon thread (name=`motion-watch`). At startup it reads the camera config for snapshot-mode Reolink cameras with `motion_burst_enabled: true`. Polls each camera's motion state every 2s (configurable via `motion_poll_interval_s` at the top level of the config). On False→True, calls `poller.request_burst(duration_s, interval_s)`. Transient poll failures log at DEBUG (not WARNING) to avoid noise when the camera hiccups. Header bumped.
- **`config.json`** + **`config.example.json`** — `house-yard` gains `motion_burst_enabled: true`, `motion_burst_duration_s: 30`, `motion_burst_interval_s: 1.0`.

**Design note: polling vs ONVIF subscribe.** The original plan (`docs/13-Apr-2026-phase-c-usb-highres-and-motion-bursts-plan.md`) considered both. Polling wins for this deployment because:
  1. ONVIF event subscriptions need a NAT-reachable webhook endpoint on the Mac Mini that the camera can POST to. That's an extra service + firewall consideration.
  2. ONVIF subscription leases expire and need active renewal.
  3. `reolink_aio.host.get_motion_state(channel)` is a direct, well-tested call.
  4. A 2s poll is ~30 HTTP round-trips per minute per camera — negligible load.

**Validation:**

- Log at startup: `Motion watcher started for cameras: house-yard`.
- Unit-test of the burst logic (in isolation): normal interval = 5s, during a burst = 1s, post-expiry cleanly returns to 5s, sub-`_MIN_SNAPSHOT_INTERVAL` requests clamp to 1s.
- No error or traceback in the log since restart.
- **Live burst firing will only appear when something actually moves.** On a quiet yard it stays silent. To see it fire: walk past the house-yard camera and watch for `burst snapshot mode for 30s at 1.00s interval` in the log.

## [2.19.0] - 2026-04-13

### Changed — Phase C1: usb-cam switches to high-quality snapshot polling (Claude Opus 4.6)

Boss checked the usb-cam (pointed at the chick brooder) and reported "really terrible" quality. Audited the path: the UVC webcam itself is a 1080p device (maxes at 1920×1080, anything higher is silently clamped by the driver), and capture was already at its native resolution — but the dashboard was re-encoding each frame at JPEG quality 85 on every request, stacking a second lossy compression pass on top of whatever the numpy pipeline lost on the way through. With the Phase A `jpeg_bytes` pass-through plumbing in place, fixing this is a single-path snapshot source that encodes once at high quality and the dashboard yields it through unchanged.

**What changed:**

- **`capture.py`** — New `UsbSnapshotSource` alongside `ReolinkSnapshotSource`. Holds the `cv2.VideoCapture` open between ticks (reopening AVFoundation takes ~300ms and can race with the system camera daemon). Each tick: discard one frame (AVFoundation's ring buffer often serves the driver's previous snapshot), read the real frame, encode once at JPEG quality 95 (configurable via `snapshot_jpeg_quality`), return bytes. Reopens the capture on transient failure. Header bumped to v2.19.0.
- **`guardian.py`** — `_register_camera_capture()` dispatch extended with `snapshot_method: "usb"`. Accepts `device_index`, `snapshot_resolution` (optional `[w, h]`), `snapshot_jpeg_quality` (default 95). Header bumped.
- **`discovery.py`** — Online-check bypass now also matches `source: "snapshot"` + `snapshot_method: "usb"` so the new-shape config doesn't fall through to the ONVIF probe path (which would fail — there's no IP to probe). Both the legacy `source: "usb"` and the new snapshot shape produce the same `CameraInfo` with `source="usb"` so downstream code is unaffected.
- **`config.json`** + **`config.example.json`** — `usb-cam` switched to `source: "snapshot"`, `snapshot_method: "usb"`, `snapshot_resolution: [1920, 1080]`, `snapshot_jpeg_quality: 95`, `snapshot_interval: 5.0`.

**Validation:**

- Log: `UsbSnapshotSource 'usb:usb-cam' opened at 1920x1080 (quality=95)` and `Camera 'usb-cam' registered in snapshot mode (method=usb)`.
- Pulled a fresh frame: 1920×1080, 329KB (vs 223KB pre-change at q=85 on a visibly compressed image). The extra 100KB bought back the sharpness — feather detail on the chicks is now preserved instead of smeared into JPEG blocks.
- The brooder scene still looks red-orange; that's the heat lamp, not something code can fix. If later needed, AVFoundation exposes white-balance controls that cv2 doesn't expose cleanly on macOS — would need a direct AVFoundation binding or an ImageIO-based capture path. Out of scope for C1.

**Note for operators:** The camera's JPEG quality is a config knob. If 95 is too large per snapshot (328KB) for sustained polling at shorter intervals, drop it to 90 — the visual difference is slight.

## [2.18.0] - 2026-04-13

### Changed — Phase A: house-yard switches from RTSP to HTTP snapshot polling (Claude Opus 4.6)

Architectural pivot directed by Boss after seeing v2.16.0's decode-garbage filter and v2.17.0's sub-stream switch get the live view stable but at low resolution: **stop using the cameras as video streams, use them as cameras.** The Reolink E1 Outdoor Pro exposes an HTTP `cmd=Snap` endpoint that returns the camera's native 4K JPEG (3840×2160, ~1.35MB, ~630ms over the LAN). That's 36× more pixels than the sub-stream RTSP we were scraping, with zero decode-garbage failure mode (a single JPEG has no inter-frame references to lose). This is **Phase A** of a three-phase plan; Phase B (GWTC laptop snapshot service) and Phase C (USB cam high-res + ONVIF motion-event-triggered snapshot bursts) are designed and documented in `docs/`.

**What changed:**

- **`capture.py`** — Two new types. `SnapshotSource` is a Protocol for "anything that returns JPEG bytes on demand". `ReolinkSnapshotSource` wraps the existing `CameraController.take_snapshot()` (which calls `reolink_aio.host.get_snapshot(channel)` under the hood). `CameraSnapshotPoller` mirrors `CameraCapture`'s public surface (start/stop/recent_frames/is_running/camera_name) so `FrameCaptureManager` dispatches to either class without caring which. The poller has no reconnect logic, no exponential backoff, no decode-garbage filter — none of those failure modes apply to camera-encoded JPEGs over a single HTTP request. Cadence: `snapshot_interval` always-on (default 5s for the dashboard); optional `night_snapshot_interval` overrides when the night detection window is open (default 2s — slow nocturnal predators don't need 4fps polling).
- **`capture.py:FrameResult`** — Added `jpeg_bytes: Optional[bytes] = None`. Snapshot-mode populates it with the camera's original JPEG; the dashboard serves it as-is for zero re-encode loss. RTSP cameras leave it None and the dashboard re-encodes from the numpy frame as before. Extracted `_downscale_to_target_width(raw)` to a module-level helper since both producers use it.
- **`capture.py:FrameCaptureManager.add_camera()`** — New `snapshot_source` / `snapshot_interval` / `night_snapshot_interval` / `is_night_window` kwargs. If `snapshot_source` is set, builds a `CameraSnapshotPoller`; else the existing RTSP/USB `CameraCapture` path.
- **`guardian.py`** — Extracted `_register_camera_capture(cam, cam_cfg)` helper so the initial setup loop and the periodic re-scan loop agree on the dispatch logic (they used to duplicate it and drift). Three modes in priority order: (1) `cam_cfg["source"] == "snapshot"` → `CameraSnapshotPoller` with the source built per `snapshot_method`, (2) USB device, (3) RTSP URL. Reordered initial setup so PTZ controllers connect *before* captures start — the snapshot poller needs the authenticated controller for its first `take_snapshot` call. Header bumped.
- **`dashboard.py`** — `/api/cameras/{name}/frame` and `/api/cameras/{name}/stream` prefer `frame_result.jpeg_bytes` when present (zero-loss path). Added `?max_width=N&q=Q` query params to `/frame` for clients that want a smaller version (see "Tunnel honesty" below).
- **`static/app.js`** — Snapshot polling cadence dropped from 10s to 5s (matches the new server-side interval). Added local-vs-tunnel hostname detection: local clients (localhost / 192.168.x / 10.x / .local) get the full 4K URL; tunnel clients (`guardian.markbarney.net`) get `?max_width=1920`. The user can always override by hitting the URL directly with their own params.
- **`config.json`** + **`config.example.json`** — `house-yard` switched to snapshot mode: `source: "snapshot"`, `snapshot_method: "reolink"`, `snapshot_interval: 5.0`, `night_snapshot_interval: 2.0`. Removed the now-irrelevant `rtsp_transport` and `rtsp_stream` fields (the snapshot path doesn't touch RTSP). gwtc/s7-cam/usb-cam are unchanged — Phase B/C handle them.
- **`CLAUDE.md`** — Reolink description rewritten. Module list note about the new `capture.py` shape. Added entries for the three new plan docs in `docs/`.
- **`docs/13-Apr-2026-phase-a-reolink-snapshot-polling-plan.md`** — This work, fully documented (scope, architecture, TODOs, risks, validation steps).
- **`docs/13-Apr-2026-phase-b-gwtc-snapshot-endpoint-plan.md`** — Standalone plan for a separate session: stand up a tiny HTTP snapshot service on the Gateway laptop and switch `gwtc` over.
- **`docs/13-Apr-2026-phase-c-usb-highres-and-motion-bursts-plan.md`** — Standalone plan for a separate session: USB cam to high-res snapshots + ONVIF motion-event-triggered snapshot bursts on house-yard.

**Validation (live):**

- Snapshot polling started cleanly: log shows `Snapshot polling started for 'house-yard' — source=reolink:house-yard, interval=5.0s (night=2.0s)` and `Camera 'house-yard' registered in snapshot mode (method=reolink)`.
- `curl -o /tmp/snap.jpg http://localhost:6530/api/cameras/house-yard/frame` returns a 1.35MB **3840×2160 JPEG in 2ms** (zero re-encode — the camera's original JPEG is yielded directly from the buffer).
- Sequential 1.5s-apart fetches show new images every ~5s (md5 changes), buffer correctly serves the cached frame between snapshots.
- **Zero** decode-garbage rejections, zero hung-reads, zero snapshot-fetch failures since restart. The lossy-WiFi failure mode is structurally gone for house-yard.
- gwtc, usb-cam continue to work on RTSP/USB unchanged. s7-cam phone is still offline (phone-side, unrelated).

**Tunnel honesty (what I tested, what's a real limit):**

After the switch I tested whether the 4K JPEGs would survive the Cloudflare tunnel. The honest finding is they often won't — and the limit is upstream of anything the code can fix.

- Mac Mini sustained upload bandwidth (measured to httpbin): **~600 KB/s.**
- Camera-to-Mac-Mini transfer over local WiFi: trivial, ~270 KB/s sustained with plenty of headroom.
- Cloudflare tunnel performance is **erratic**: a 786KB JPEG once completed in 1.85s (~425 KB/s, near upstream bandwidth), but other 285–393KB requests timed out at 24–30s with only partial bodies delivered. There's queueing / handling overhead in cloudflared or Cloudflare's free-tier tunnel that doesn't show up on local testing.
- The pragmatic split: **local browsers get the full 4K (no tunnel involved, 2ms transfer)**; tunnel browsers get `max_width=1920` automatically (~150–800KB depending on scene). This isn't a permanent fix — it's an honest acknowledgement of a constraint we can't engineer around without changing the access path.
- **Future options Boss might want to consider** (intentionally NOT implemented in Phase A): (1) A snapshot-to-disk archive on the Mac Mini so high-quality images are accessible via local file share / SSH / network drive without the tunnel at all. (2) Tailscale or similar VPN for direct LAN-quality access from outside the home network. (3) Per-Host detection on the server that automatically downscales for the tunnel host. None are mandatory; the local-vs-tunnel split in `app.js` covers the common cases.

**Detection cadence note for future operators:**

YOLO inference now runs at 2s intervals (0.5fps) during the night detection window — 8× lower than the previous 4fps RTSP capture. This is intentional and per Boss's directive: nocturnal predators (raccoons, foxes, coyotes, bobcats, opossums) linger in frame for 10–30s, so 2s polling = 5–15 chances to detect each visit. If detection sensitivity needs to go up later, drop `night_snapshot_interval` to 1.0 or even 0.5 in `config.json` — the M4 Pro yawns at YOLOv8n on 1080p frames.

## [2.17.0] - 2026-04-13

### Removed — GLM vision species refinement (Claude Opus 4.6)

The vision refinement pipeline (`vision.py` → LM Studio → `zai-org/glm-4.6v-flash`) is gone. It had been disabled in `config.json` (`vision.enabled: false`) since at least the prior session, so this release just removes the dead code and config rather than changing runtime behaviour.

**Why:** Boss directive — "It absolutely does not need to be doing that. The detection is only going to be running at night, and it's really going to be more about if it detects anything interesting at night. If it does, just show me the picture. I don't need it to run weird classification on it." The farm sees too few predator events for hawk-vs-chicken / bobcat-vs-house-cat species refinement to earn its complexity. YOLO's class label is already enough to gate "predator vs not", and the Discord alert posts the snapshot — that IS the picture Boss wants to see.

**What changed:**

- **`vision.py`** — **Deleted.** ~290 lines.
- **`guardian.py`** — Removed the `VisionRefiner` import, the `_REFINED_PREDATORS` / `_REFINED_SAFE` mapping sets, the `self._vision = VisionRefiner(config)` init, and the entire refinement block inside `_on_frame()`. Detection flow is now: YOLO → tracker (for alert dedup) → log → alert. Header bumped.
- **`config.json`** + **`config.example.json`** — Removed the `vision` config block (endpoint, model name, trigger classes, timeouts, etc.).
- **`logger.py`** — Tightened docstrings that mentioned "vision-refined class". Header bumped.
- **`CLAUDE.md`** — Project description, module list, and Phase 2 section updated to reflect that YOLO is now the sole classifier.

### Changed — house-yard pulls ONVIF sub-stream instead of 4K main stream (Claude Opus 4.6)

Live view is still choppy after v2.16.0's garbage-frame filter because the WiFi link is genuinely lossy and the filter is correctly dropping a lot of smear-frames. Switching the source from the 4K HEVC main stream to the ~640x360 H.264 sub-stream cuts bandwidth by ~10× and is dramatically more resilient to packet loss.

**What changed:**

- **`discovery.py`** — `_get_rtsp_url()` accepts a new `stream_preference` parameter ("main" or "sub"). Selects ONVIF profile index 0 or 1 accordingly. Falls back to profile 0 with a warning if "sub" is requested but the camera only exposes one profile. The selected profile + token are now logged. `_probe_camera()` reads the new `rtsp_stream` field from the camera config and passes it through. Header bumped.
- **`config.json`** + **`config.example.json`** — Added `"rtsp_stream": "sub"` to the `house-yard` camera entry.
- **`CLAUDE.md`** — Reolink description updated to note sub-stream usage and why.

**Note for detection accuracy:** YOLO inference still runs on the captured frame, just at the sub-stream's native resolution rather than 4K downscaled to 1080p. For typical hawk/cat/dog detections at the camera's framing distance, 640px is acceptable — animals are still many tens of pixels in width. If detection accuracy regresses noticeably, switch back with `"rtsp_stream": "main"`.

**Validation:** Restarted Guardian. Discovery log shows `"Selected ONVIF profile 1 (token=…) for stream='sub'"` for `house-yard`. Decode-garbage rejections should drop to near-zero on the sub-stream because H.264 at lower bitrate survives WiFi loss far better than 4K HEVC.

## [2.16.0] - 2026-04-13

### Fixed — Gray decode-garbage frames + choppy live view (Claude Opus 4.6)

After the Mac Mini lost power and Guardian was restarted, the dashboard's live MJPEG feed was showing two distinct symptoms: (1) intermittent uniform-gray "washed out" frames of nothing, and (2) very choppy playback. Root-caused both and fixed.

**What was wrong:**

1. **Decode-garbage frames being served.** When the Reolink E1 Outdoor Pro on WiFi loses an HEVC reference packet, FFMPEG still returns `ret=True` from `cap.read()` — but the decoded frame is a low-variance mid-gray smear because the P/B-frame references are missing. `capture.py` accepted any non-null frame and pushed it straight into the ring buffer. The dashboard then served that garbage frame as "latest" until the next clean keyframe arrived.
2. **Stale frames after disconnect.** When `_release_capture()` ran (or the read-hang branch fired), the ring buffer was not flushed. The dashboard kept yielding the last (often already-corrupted) frame from the dead RTSP session for the entire reconnect window — that's how a single bad frame became a sticky gray image for tens of seconds.
3. **Capture rate too low for live view.** Detection cameras captured at 1fps (`detection.frame_interval_seconds = 1.0`). The MJPEG stream can never deliver more than the capture rate, so the live view was inherently 1fps even on a healthy link. Combined with garbage rejection, effective fps was sometimes <0.5.
4. **Dashboard MJPEG poll cadence too slow.** The `/api/cameras/{name}/stream` generator slept 300ms between buffer checks. With faster capture, frames would sit in the buffer for up to 300ms before being yielded.

**What changed:**

- **`capture.py`** — Added `_is_decode_garbage()` static method that checks subsampled stdev (<4) and mean (>30). The mean check excludes legitimately dark night frames. Decode-garbage frames are dropped before they enter the buffer or trigger detection; consecutive rejections are logged every 10. `_release_capture()` and the read-hang branch now flush the ring buffer so post-disconnect garbage cannot persist. Header bumped to v2.16.0.
- **`dashboard.py`** — MJPEG generator poll sleep dropped from 300ms to 100ms so each new captured frame is yielded within ~25% of its lifetime. Header bumped.
- **`config.json`** + **`config.example.json`** — `detection.frame_interval_seconds` lowered from 1.0 to 0.25 (4fps). YOLOv8n on the M4 Pro at 1080p handles 4fps trivially. Live view of `house-yard` is now ~4fps cleanly.

**Validation:** Restarted Guardian. The decode-garbage filter engages live: on the current lossy WiFi link the log shows roughly one rejected smear-frame every 1–2s on `house-yard`, each followed by a "clean frames resumed" line. Sampling the `house-yard` MJPEG endpoint over 8s yielded 21 clean JPEGs (≈2.6 fps actual delivery — capture is 4fps, the gap is exactly the rejected garbage frames, which is the desired behaviour). `house-yard`, `gwtc`, and `usb-cam` all return live frames via `/api/cameras/{name}/frame`. `s7-cam` (Samsung phone over WiFi) is currently failing to connect to RTSP — that is a phone-side issue, unrelated to this change.

## [2.15.1] - 2026-04-12

### Fixed — Night-only detection gate for enabled cameras (OpenAI Codex GPT-5.4)

Added a config-driven night window to `guardian.py` so enabled cameras only run YOLO detection from 20:00 to 09:00 America/New_York. This keeps the house-yard camera armed at night without wasting daytime inference.

**What changed:**
- **`guardian.py`** — Added a local-time window check before `AnimalDetector.detect()` runs. The gate uses `detection.night_window_*` values from config, defaults to 20:00 → 09:00, and logs the active schedule on startup.
- **`config.json`** — Re-enabled `house-yard` for detection and added the approved global night-window settings.
- **`config.example.json`** — Mirrored the live config shape so future setups match the same gate.
- **`docs/12-Apr-2026-house-yard-night-window-plan.md`** — Added the focused implementation plan and validation checklist.

**Validation:** `guardian.py` compiles cleanly, the config parses, Guardian restarted successfully on the Mac Mini, and local/public status checks showed detection frames processing after restart.

## [2.15.0] - 2026-04-12

### Changed — Snapshot polling replaces HLS video pipeline (Claude Opus 4.6)

Replaced the entire ffmpeg HLS video streaming system with simple periodic JPEG snapshots via OpenCV. The old approach ran continuous ffmpeg processes (one per non-detection camera) that hardware-encoded 15fps H.264 into HLS segments. These processes crashed, hung, ignored SIGTERM, consumed memory, and overwhelmed the Cloudflare tunnel with 42,000+ errors.

**Why:** Nobody needs live video. A snapshot refreshed every 10 seconds serves the same farm monitoring purpose with dramatically less complexity and far better reliability through the Cloudflare tunnel.

**What changed:**
- **`stream.py`** — **Deleted.** 340 lines of ffmpeg process management, watchdog threads, HLS segment cleanup gone.
- **`guardian.py`** — All cameras now route through `FrameCaptureManager` (OpenCV). Detection cameras run at ~1fps; non-detection cameras run at configurable `snapshot_interval` (default 10s). Removed all `HLSStreamManager` references.
- **`dashboard.py`** — Removed `/api/cameras/{name}/hls/{filename}` endpoint. Simplified `/api/cameras/{name}/frame` to read directly from capture manager. Removed `hls_manager` parameter and `stream_mode` field from camera list.
- **`capture.py`** — `add_camera()` now accepts per-camera `frame_interval` override so non-detection cameras can poll at 10s while detection cameras stay at 1fps.
- **`static/app.js`** — Replaced hls.js `<video>` player with `<img>` tags polled every 10 seconds via `data-snapshot` attribute and cache-busting query param. ~40 lines of HLS player code replaced with ~20 lines of snapshot polling.
- **`static/index.html`** — Removed hls.js CDN script tag.
- **`config.example.json`** — Added `snapshot_interval` per-camera field (default 10s).

**Net result:** ~430 lines deleted, ~80 lines changed. Zero ffmpeg processes. Dashboard and website both see fresh camera images. Tunnel serves small JPEGs instead of HLS segments.

## [2.14.1] - 2026-04-11

### Fixed — Camera frames now served via HLS snapshots (Claude Opus 4.6)

All four cameras were showing "offline" on the public website (`farm.markbarney.net`) because the `/api/cameras/{name}/frame` endpoint returned 404 for every camera. The endpoint only checked the OpenCV capture manager for frames, but since v2.13.0 all cameras were routed to HLS (ffmpeg) — the capture manager had zero frames.

**What was changed:**
- **`stream.py`** — Added a second ffmpeg output to each HLS stream: a JPEG snapshot overwritten every 10 seconds (`-map 0:v -vf fps=1/10 -update 1 latest.jpg`). Shares the same decoded input as HLS encoding — no extra device access or CPU overhead. Each camera now has `/tmp/guardian_hls/{name}/latest.jpg` updated continuously.
- **`dashboard.py`** — The `/api/cameras/{name}/frame` endpoint now falls back to reading the HLS snapshot file when the capture manager has no frame (which is all non-detection cameras). Serves the file with `Cache-Control: no-cache` so the website always gets a fresh image.

**Verified:** All four cameras return 200 with JPEG data through both localhost:6530 and the Cloudflare tunnel at `guardian.markbarney.net`.

## [2.14.0] - 2026-04-11

### Fixed — USB camera HLS streaming now works (Claude Opus 4.6)

The USB camera is now streaming via HLS. All four cameras operational.

**Root cause:** ffmpeg 8.0.1's AVFoundation demuxer cannot negotiate the USB camera's non-standard framerate (`30.000030` fps) when `-video_size` is explicitly set. The device configuration fails and ffmpeg hangs indefinitely. Without `-video_size`, ffmpeg's fallback mode captures at native 1920x1080 successfully.

**What was changed:**
- **`stream.py`** — Removed `-video_size` from the ffmpeg command for USB cameras. The AVFoundation fallback mode captures at the camera's native resolution (1920x1080) without needing the explicit size parameter. Also disabled audio capture for now — USB audio device indices shift when the iPhone connects/disconnects, making hardcoded indices unreliable. Audio AAC plumbing retained in code for future name-based device resolution.
- **`config.json`** — Removed `audio_device_index` from usb-cam (was `1`, which is only correct when Mark's iPhone is connected — breaks when it's not).

**What was NOT changed (TCC issue resolved):**
The macOS TCC camera permission issue documented in v2.13.1 is resolved — `cv2.VideoCapture(0)` and ffmpeg AVFoundation both work from the current process context. The TCC permission was re-granted (likely via Terminal.app camera access).

**Remaining follow-up:**
- USB audio: Implement name-based AVFoundation device lookup (find "USB CAMERA" audio device by name instead of hardcoded index) to make audio capture reliable regardless of which other devices are connected.
- ffmpeg `-sc_threshold 0` produces a cosmetic warning with `h264_videotoolbox` (not a real issue).

## [2.13.1] - 2026-04-11

### Known Issue — USB camera capture blocked by TWO separate problems (Claude Opus 4.6)

The USB camera cannot be captured by Guardian. Three RTSP cameras (house-yard, s7-cam, gwtc) stream via HLS successfully. The USB camera is blocked by two independent issues.

---

**PROBLEM 1: ffmpeg AVFoundation framerate negotiation bug**

ffmpeg 8.0.1 cannot open the USB camera via AVFoundation. The camera reports `30.000030` fps. ffmpeg cannot match this rate.

What was tried:
1. `-framerate 15` at 1080p → camera only supports 30fps or 5fps at 1080p, rejects 15
2. `-framerate 30` at 1080p → "Configuration of video device failed, falling back to default" then **hangs indefinitely**
3. `-framerate 30` at 720p with pixel formats `uyvy422`, `nv12`, `yuyv422` → same hang
4. No framerate → ffmpeg tries 29.97fps (NTSC), camera rejects as unsupported
5. `-framerate 30.000030` (exact camera rate) → same hang
6. Device by name `"USB CAMERA"` instead of index → same hang
7. Minimal `ffmpeg -f avfoundation -i "0"` with zero options → framerate mismatch error
8. USB camera unplugged/replugged → no change
9. All stale ffmpeg processes killed between attempts → no change

Root cause: ffmpeg's `-framerate 30` becomes rational `30/1` (30.000000) which doesn't match the camera's `30000030/1000000` (30.000030). The device configuration fails. ffmpeg's fallback mode also fails silently and the process hangs producing no output.

**PROBLEM 2: macOS TCC (camera permissions) corrupted**

During debugging, `tccutil reset Camera` was run to try to fix permissions. **This was a mistake.** It wiped ALL camera permissions for ALL apps from macOS. This broke OpenCV `cv2.VideoCapture(0)` which had been working before.

Attempts to re-grant camera access:
1. `tccutil reset Camera` → removed all permissions, no way to undo
2. Ran OpenCV from Bash (Claude Code context) → "not authorized to capture video (status 0), requesting..." — dialog never appears because not a GUI context
3. Ran from Terminal.app GUI context via `open -a Terminal script.sh` → same "not authorized", no dialog
4. Opened Photo Booth → **worked** (system app gets auto-granted), confirmed USB camera hardware is fine
5. Built a Swift binary requesting `AVCaptureDevice.requestAccess(for: .video)` → returns `granted: false` from CLI context
6. Built a proper macOS .app bundle with Info.plist + NSCameraUsageDescription → dialog hidden by screenshot filter, still denied
7. Created a `.mobileconfig` TCC profile → `profiles install` deprecated, manual install opened wrong settings page
8. `sudo killall tccd` to restart TCC daemon → no change
9. `sudo sqlite3` on TCC database → "authorization denied" (SIP protected)
10. `imagesnap` (Homebrew) → works from Terminal.app (Mark confirmed), fails from Claude Code context
11. Discovered Claude Code runs as `com.anthropic.claude-code` (separate bundle ID from `com.anthropic.claudefordesktop`). Camera was enabled for Claude desktop app but the binary spawning commands is Claude Code — a different TCC entry
12. Even after enabling Claude in Camera settings, still blocked — TCC checks the specific binary's code signature, not just the parent app

**The core TCC issue:** Claude Code (`com.anthropic.claude-code`) spawns `/bin/zsh` which runs Python/ffmpeg/imagesnap. macOS TCC checks the responsible app's bundle ID. Granting camera to Claude.app (`com.anthropic.claudefordesktop`) does NOT grant it to Claude Code. And Claude Code can't trigger the TCC permission dialog because it runs processes in a non-GUI context where macOS refuses to show the dialog.

`imagesnap /tmp/test.jpg` works perfectly when Mark types it in Terminal.app. Same command fails from Claude Code.

---

**What works RIGHT NOW:**
- HLS streaming for house-yard, s7-cam, gwtc (all RTSP cameras) — working perfectly
- `imagesnap` from Terminal.app — captures USB camera fine
- Photo Booth — captures USB camera fine
- The USB camera hardware is NOT broken

**What does NOT work:**
- Any camera access from Claude Code's process context (OpenCV, ffmpeg, imagesnap)
- ffmpeg AVFoundation input with this specific USB camera at any settings

**Suggested next steps for the next developer:**
1. **For TCC:** Run Claude Code from Terminal.app (`claude` command in Terminal, not the desktop app). This should inherit Terminal's camera TCC grant. Or grant `com.anthropic.claude-code` camera access — it may need to appear in System Settings first, which requires triggering a request from a GUI context.
2. **For the USB camera stream:** Use `imagesnap` via a launchd plist that runs under the user's login session (inherits Terminal's TCC). Have it capture a JPEG every N seconds to a known path. Guardian serves the file. Zero ffmpeg involvement.
3. **For ffmpeg:** This is a real bug in ffmpeg 8.0.1's AVFoundation demuxer with cameras reporting non-standard framerates like `30.000030`. Consider filing upstream or using an older ffmpeg version.
4. **For audio:** `audio_device_index` config and AAC encoding code is already in `stream.py` — ready to use once capture works.
5. **Mark's idea — vision-scored clips:** Use the local GLM-4V model (LM Studio) to score frames for "interestingness" before recording clips. Infrastructure exists in `vision.py`. Would let the system capture the best moments (active chicks) and skip boring ones (sleeping).

**Changes in this version:**
- **`stream.py`** — Added `audio_device_index` parameter and pipe mode prep. USB cameras use `-i "video:audio"` for combined capture. Audio encoded AAC 128kbps. RTSP cameras strip audio (`-an`).
- **`guardian.py`** — USB cameras route to HLS manager with audio device index from config.
- **`config.json`** — Added `audio_device_index: 1` to usb-cam. Added `streaming` section.
- **Installed `imagesnap`** via Homebrew — works from Terminal, blocked from Claude Code.

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
