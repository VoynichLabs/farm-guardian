# CLAUDE.md — Farm Guardian

This file provides guidance to AI coding agents working in this repository.

## Related Repositories

This project is part of a two-repo system:

- **[farm-guardian](https://github.com/VoynichLabs/farm-guardian)** (this repo) — Python backend: camera discovery, YOLO detection, deterrence, visit tracking, alerts, REST API, local dashboard. Runs on the Mac Mini.
- **[farm-2026](https://github.com/VoynichLabs/farm-2026)** — Next.js public website at [farm.markbarney.net](https://farm.markbarney.net). Embeds live Guardian camera feeds and detection data via the Cloudflare tunnel at `guardian.markbarney.net`. Deployed on Railway.

The website's Guardian components (`app/components/guardian/`) consume this repo's REST API. Changes to API response shapes in `api.py` or `dashboard.py` must be coordinated with the TypeScript types in `farm-2026/app/components/guardian/types.ts`.
# Mark's Coding Standards
These should be present in the CLAUDE.md file and the agents.md file. 

## Non-negotiables

- No guessing: for unfamiliar or recently changed libraries/frameworks, locate and read docs (or ask for docs) before coding.
- Quality over speed: slow down, think, and get a plan approved before implementation.
- Production-only: no mocks, stubs, placeholders, fake data, or simulated logic shipped in final code.
- SRP/DRY: enforce single responsibility and avoid duplication; search for existing utilities/components before adding new ones.
- Real integration: assume env vars/secrets/external APIs are healthy; if something breaks, treat it as an integration/logic bug to fix.

## Workflow (how work should be done)
1. Deep analysis: understand existing architecture and reuse opportunities before touching code.
2. Plan architecture: define responsibilities and reuse decisions clearly before implementation.
3. Implement modularly: build small, focused modules/components and compose from existing patterns.
4. Verify integration: validate with real services and real flows (no scaffolding).

## Plans (required)
- Create a plan doc in `docs/` named `{DD-Mon-YYYY}-{goal}-plan.md` before substantive edits.
- Plan content must include:
  - Scope: what is in and out.
  - Architecture: responsibilities, modules to reuse, and where new code will live.
  - TODOs: ordered steps, including verification steps.
  - Docs/Changelog touchpoints: what will be updated if behavior changes.
- Seek approval on the plan before implementing.

## File headers (required for TS/JS/Py)
- Every TypeScript, JavaScript, or Python file you create or edit must start with:

  ```
  Author: {Your Model Name}
  Date: {timestamp}
  PURPOSE: Verbose details about functionality, integration points, dependencies
  SRP/DRY check: Pass/Fail - did you verify existing functionality?
  ```

- If you touch a file, update its header metadata.
- Do not add this header to file types that cannot support comments (e.g., JSON, SQL migrations).

## Code quality expectations
- Naming: meaningful names; avoid one-letter variables except tight loops.
- Error handling: exhaustive, user-safe errors; handle failure modes explicitly.
- Comments: explain non-obvious logic and integration boundaries inline (especially streaming and external API glue).
- Reuse: prefer shared helpers and `shadcn/ui` components over custom one-offs.
- Architecture discipline: prefer repositories/services patterns over raw SQL or one-off DB calls.
- Pragmatism: fix root causes; avoid unrelated refactors and avoid over-engineering and under engineering.

## UI/UX expectations (especially streaming)
- State transitions must be clear: when an action starts, collapse/disable prior controls and reveal live streaming states.
- Avoid clutter: do not render huge static lists or "everything at once" views.
- Streaming: keep streams visible until the user confirms they have read them.
- Design: avoid "AI slop" (default fonts, random gradients, over-rounding). Make deliberate typography, color, and motion choices.

## Docs, changelog, and version control
- Any behavior change requires:
  - Updating relevant docs.
  - Updating the top entry of `CHANGELOG.md` (SemVer; what/why/how; include author/model name).
- Commits: do not commit unless explicitly requested; when asked, use descriptive commit messages and follow user instructions exactly.
- Keep technical depth in docs/changelog rather than dumping it into chat.

## Communication style
- Keep responses tight and non-jargony; do not dump chain-of-thought.
- Ask only essential questions after consulting docs first.
- Mention when a web search could surface important, up-to-date information.
- Call out when docs/plans are unclear (and what you checked).
- Pause on errors, think, then request input if truly needed.
- Do not dump details into chat; keep them in docs/changelog.
- What you say to the user in your reply, "Will be forgotten almost instantly." If it is important, it needs to be in the documentation and your commit messages. 
- End completed tasks with "done" (or "next" if awaiting instructions).


## LM Studio — READ BEFORE ADDING ANY CODE THAT TALKS TO IT

This Mac Mini runs LM Studio (`http://localhost:1234`). Guardian
**does not currently call LM Studio** — `vision.py` was removed in
v2.17.0 because over-engineered species refinement wasn't worth the
operational complexity. There is one planned re-introduction (a
standalone, slow-cadence brooder narrator).

Before you write or modify ANY code that opens a connection to LM
Studio, read **`docs/13-Apr-2026-lm-studio-reference.md`** in full.
That doc covers the API, the safe model-load pattern, the locally
available models, and the 2026-04-13 watchdog incident that took the
whole machine down because the previous `vision.py` raced a research
sweep on the same model. The hard rules:

1. Never call `/api/v1/models/load` without first checking what's
   loaded (instances stack — loading the same model twice doubles
   memory).
2. Always pass `context_length` on load (default is the model's max,
   which can be 131k+ tokens and reserves gigabytes of KV cache).
3. Never call `/v1/chat/completions` against a model name that isn't
   already loaded — that endpoint **silently auto-loads** the model,
   which is what crashed the box on 2026-04-13.

The brooder narrator plan
(`docs/13-Apr-2026-brooder-vlm-narrator-plan.md`) is the canonical
example of how to call LM Studio safely from a Guardian-adjacent
tool. Use it as the template for any new integration.

## Heat-lamp orange cast — READ THIS BEFORE "FIXING" THE BROODER COLOR

Every 1–2 weeks an agent sees orange / red brooder frames on `usb-cam`, `mba-cam`, or `s7-cam` and reaches for a new WB algorithm. Boss has been through this loop 4–5 times. Stop. Read **`docs/16-Apr-2026-heat-lamp-orange-cast-investigation.md`** first. It covers: (a) the gray-world + orange-desat code that already exists in `tools/usb-cam-host/usb_cam_host.py` and the S7 `http_startup_gets` settings, (b) the real root cause (**sensor exposure clipping, not WB**), (c) pre-buried wrong theories, (d) the fix path that actually works (exposure control), (e) recovery recipes for S7 settings regression and MBA stale-code drift.

## Hardware Inventory — READ THIS BEFORE TOUCHING ANY CAMERA

The single source of truth for what every camera *is*, what device hosts it, where its frames flow, and the device-not-location naming rule with worked examples lives in **`HARDWARE_INVENTORY.md`** at the repo root. Read it before adding, renaming, or moving any camera. The frontend devs and the next backend agent both depend on it.

## Operational skills — read before working with the S7 or Discord

Two runbooks capture the how-to for cross-agent operations on this repo. Any agent picking up S7 or Discord work should read the relevant one first rather than re-deriving it:

- **`docs/skills-farm-2026-discord-post.md`** — how to post a camera frame from Guardian to the `#farm-2026` Discord channel. Webhook wiring, channel ID, a copy-paste-ready `post.sh`, failure modes, what not to post. **No credentials in the doc** — the webhook URL lives in `.env` (gitignored).
- **`docs/skills-s7-adb-operations.md`** — how to check the Samsung S7's battery, temperature, charging state, screen state, and IP Webcam app activity via ADB on the MacBook Air (where the phone lives). The phone has a specific USB-composite quirk (`adb reconnect offline` is required between commands) and a specific failure mode (IP Webcam on Configuration = server stopped); the runbook pre-buries the dead ends I already walked down on 2026-04-16.
- **`docs/16-Apr-2026-s7-ipwebcam-frozen-incident.md`** — the incident post-mortem those two runbooks reference. 30-second human recovery recipe.

## Project

Farm Guardian — a Python service that watches Reolink security cameras via ONVIF/RTSP, detects predator animals using YOLOv8, automates camera deterrents (spotlight/siren/PTZ), tracks animal visits in SQLite, generates daily intelligence reports, and serves a local web dashboard with REST API. Runs on a Mac Mini M4 Pro (64GB) on the same local network as the cameras.

## Commands

```bash
# Setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run
python guardian.py

# Run with debug logging
python guardian.py --debug
```

No test suite yet. This is a v2 production system (Phases 1-4 complete).

## Recent Changes (17-Apr-2026)

**Yard-diary capture — thrice-daily seasonal stockpile (17-Apr-2026):** `scripts/yard-diary-capture.py` (installed copy lives at `~/bin/yard-diary-capture.py` to dodge TCC on `~/Documents/`) is fired three times a day by `~/Library/LaunchAgents/com.farmguardian.yard-diary-capture.plist` at 07:00 / 12:00 / 16:00 local. Each run pulls a 4K snapshot from the Reolink `house-yard` via the existing `/api/v1/cameras/house-yard/snapshot` endpoint, writes the master to `data/yard-diary/{YYYY-MM-DD}-{morning|noon|evening}.jpg` (gitignored, kept indefinitely on the Mini), renders a 1920px copy with `DD-Mon-YYYY` burned into the lower-right via Pillow, publishes that copy to `farm-2026/public/photos/yard-diary/`, and commits+pushes farm-2026. **Purpose: raw stockpile for a year-end timelapse reel** (cherry bloom → summer green → autumn burn → snow), not curated daily site content. The `/yard` route on farm-2026 is a secondary surface — the primary artifact is the year-end ffmpeg assembly from the 4K masters. If you're about to stop the LaunchAgent, disable the captures, or delete masters, **don't** — the stockpile must keep accruing. Label prefix `com.farmguardian.*` is intentional and piggybacks on the known-working Guardian TCC grant family (see the "LaunchAgent relabeled" note below for the TCC-label-family principle). Plan: `docs/17-Apr-2026-yard-diary-capture-plan.md`. Log: `data/pipeline-logs/yard-diary.log`.

---

## Recent Changes (14-Apr-2026)

**Host-portable `usb-cam` (v2.26.0, 14-Apr-2026):** The generic USB webcam is no longer hardcoded to the Mac Mini. A new FastAPI snapshot service (`tools/usb-cam-host/usb_cam_host.py`) runs on whichever host the camera is physically plugged into and serves `GET /photo.jpg` + `GET /health` on port `8089`. Cross-platform via `cv2.VideoCapture(index)` (no backend flag — OpenCV auto-picks AVFoundation / dshow / V4L2). 15-frame warmup for AE/AWB convergence; no Laplacian burst ranking (Boss distrusts Laplacian-vs-GLM calibration). Guardian's `config.json` and `tools/pipeline/config.json` both flipped `usb-cam` to the HTTP path (`http_url` / `ip_webcam`); zero Guardian or pipeline code changed — reuses `HttpUrlSnapshotSource` (v2.24.0) and `capture_ip_webcam`. Deploy artifacts in `deploy/usb-cam-host/` (launchd plist for macOS, Shawl-wrappable `.bat` for Windows, install guides). Moving the camera later is: new host → install agent → change one URL in each config file. **Plan:** `docs/14-Apr-2026-portable-usb-cam-host-plan.md`. **System state snapshot (live now):** `docs/14-Apr-2026-system-state-snapshot.md`.

**Image archive REST surface (v2.25.0, 14-Apr-2026):** `/api/v1/images/*` — public `/gems`, `/recent`, `/stats`, `/gems/{id}`, `/gems/{id}/image`; private `/review/*` for promote/demote/flag/unflag/delete with an append-only `image_archive_edits` audit table. Public SQL always prefixes `WHERE has_concerns = 0`; public response models omit `concerns`/`has_concerns`/`vlm_json`; review endpoints 503 when `GUARDIAN_REVIEW_TOKEN` is unset. Lazy thumbnails cached under `data/cache/thumbs/`. Plan: `docs/14-Apr-2026-image-archive-api-plan.md`. Layer-2 follow-ups: `docs/14-Apr-2026-followups-post-layer1.md`. **farm-2026 frontend consumes this via the Cloudflare tunnel at `guardian.markbarney.net`.**

**LaunchAgent relabeled to `com.farmguardian.guardian` (16-Apr-2026, fixed).** The old label `com.farm.guardian` had been failing to spawn since the 14-Apr-2026 power outage with `posix_spawn ... Operation not permitted`. The previous writeup guessed the fix was **System Settings → Privacy & Security → App Management** re-approval — that was wrong. App Management does not expose launchd service entries and the venv Python binary is not listable there. Reboot also did not clear it. The real root cause: macOS TCC persists per-label denies; the `com.farm.guardian` label was permanently held in a denied state in the TCC database. The fix was to rename the Label in the plist (and the plist filename) to `com.farmguardian.guardian` — a fresh label carries no TCC history and spawns cleanly. **If a future agent relabel gets denied again, the surgical fix is another label rename, not App Management.** Plist path is now `~/Library/LaunchAgents/com.farmguardian.guardian.plist`. Logs redirected to `/tmp/guardian.out.log` + `/tmp/guardian.err.log` (matching the working `com.farmguardian.usb-cam-host` pattern). Guardian's own internal logger still writes to `guardian.log` in the project directory — that path is fine because the *process* can write there; it's only launchd's `StandardOutPath` redirect that sometimes ran into TCC. The service now starts automatically on boot — no more `nohup` workaround.

---

## Recent Changes (08-Apr-2026)

**Remote camera control API (v2.7.0):** Five new endpoints in `api.py` for full remote camera control over the Cloudflare tunnel: snapshot, position readback, zoom, autofocus, guard control. A remote Claude session can now control the camera from anywhere.

**Step-and-dwell patrol (v2.6.0):** Patrol rewritten. Camera steps through 11 positions at 30° intervals, dwells 8 seconds at each for clean stationary frames. Replaces continuous sweep that produced motion-blurred garbage.

**Cloudflare tunnel live:** Guardian dashboard exposed at `https://guardian.markbarney.net` via Cloudflare tunnel from the Mac Mini. No port forwarding needed.

**Preset save/recall API (v2.8.0):** Three new endpoints — list presets, save current position as preset, recall preset. Camera moves autonomously to saved position with no polling or overshoot. Bypasses reolink_aio validation to send raw `setPos` command.

**Four-camera config (v2.12.0):** GWTC laptop added as 4th camera. Gateway laptop (192.168.0.68) streams its built-in webcam at 1280x720@15fps H.264 via ffmpeg + MediaMTX on port 8554. Uses `rtsp_url_override` — same pattern as the S7. Named `gwtc` (device name, not location). No code changes needed — config-only addition. All four cameras: house-yard (Reolink PTZ), s7-cam (Samsung S7 via IP Webcam RTSP), usb-cam (USB on Mac Mini), gwtc (Gateway laptop webcam via MediaMTX RTSP). Detection disabled on all except house-yard.

**Three-camera config (v2.11.0):** S7 phone restored (was only discharged, not dead). Cameras named by device, not location — locations change.

**USB camera support (v2.9.0):** USB camera added to Mac Mini. Config uses `"source": "usb"`, `"device_index": 0`. Capture, discovery, and guardian.py handle USB cameras via AVFoundation. 1920x1080, no network latency.

**TODO:**
- **Save camera presets** — no presets exist yet. See "Preset Map" below for the positions to save.

---

## Camera Control — Principles

**For all camera-specific technical details, API shapes, endpoints, and procedures, read `AGENTS_CAMERA.md`.** That file is the single source of truth for camera operations.

Durable rules:
- **Never suggest using the Reolink phone app.** We ARE the Reolink app. The camera is an HTTP server. Anything the app can do, we can do with raw JSON commands. If the `reolink_aio` library doesn't expose a feature, bypass it and call `host.send_setting()` directly.
- **Never declare something impossible without reading the full library source.** The `reolink_aio` library is ~5000 lines (`venv/lib/python3.11/site-packages/reolink_aio/api.py`). Skimming it will miss critical capabilities. Read the actual methods, the enums, the body construction. Check both the HTTP API and the Baichuan protocol module.
- **Never trust a GitHub issue as the final word.** An open issue saying "not supported" might mean the library hasn't wired it up, not that the camera can't do it. Verify against the actual firmware behavior.
- **Autofocus wait is non-negotiable.** After any camera movement, trigger autofocus and wait 3 seconds before taking a snapshot. Every blurry image in this project's history was caused by skipping this.
- **Zoom is out of scope.** Camera stays at zoom 0 (widest). Do not add zoom features.

## Architecture

Read `docs/02-Apr-2026-v2-system-plan.md` for the full v2 architecture document with module specifications.

**All plans live in `docs/`:**
- `docs/01-Apr-2026-v1-guardian-plan.md` — Original v1 plan
- `docs/02-Apr-2026-v2-system-plan.md` — Full v2 architecture spec (15 modules)
- `docs/02-Apr-2026-smart-devices-plan.md` — Smart plug deterrent integration (future)
- `docs/04-Apr-2026-full-cleanup-plan.md` — Stabilization & cleanup
- `docs/06-Apr-2026-sweep-patrol-plan.md` — Continuous sweep patrol design
- `docs/06-Apr-2026-s7-nesting-box-camera-setup.md` — S7 phone camera setup plan & findings
- `docs/06-Apr-2026-per-camera-rtsp-transport-plan.md` — Per-camera RTSP transport fix (TCP/UDP)
- `docs/08-Apr-2026-camera-setup-handoff.md` — Camera control handoff (API reference, world model, operational state)
- `docs/08-Apr-2026-absolute-ptz-investigation.md` — **READ THIS** — why absolute PTZ doesn't work, preset approach, speed calibration
- `docs/08-Apr-2026-remote-camera-api-plan.md` — Remote camera control API design (v2.7.0)
- `docs/08-Apr-2026-rtsp-substream-plan.md` — RTSP substream investigation
- `docs/08-Apr-2026-gwtc-webcam-stream-plan.md` — GWTC webcam stream plan
- `docs/17-Apr-2026-gwtc-windows-stabilization-plan.md` — 17-Apr-2026 Debian-wipe reversal record. Historical rationale only; superseded by the 18-Apr doc below.
- `docs/18-Apr-2026-gwtc-current-state-and-install-walkthrough.md` — **READ FIRST FOR GWTC.** Current live state (Windows autologon, `cam` account), the one Windows-Update landmine that breaks it, and the full interactive Debian install walkthrough (SD card is pre-staged in Boss's hands) for when the switch-trigger fires.
- `docs/13-Apr-2026-phase-a-reolink-snapshot-polling-plan.md` — **DONE in v2.18.0** — house-yard switched from RTSP to HTTP snapshot polling (4K JPEG)
- `docs/13-Apr-2026-phase-b-gwtc-snapshot-endpoint-plan.md` — Phase B: stand up an HTTP snapshot service on the Gateway laptop, switch `gwtc` over
- `docs/13-Apr-2026-phase-c-usb-highres-and-motion-bursts-plan.md` — Phase C: `usb-cam` to high-res snapshots + ONVIF motion-event-triggered snapshot bursts on house-yard. **C1 (USB high-res) is effectively delivered by v2.26.0 `usb-cam-host` via a different architecture (HTTP service instead of local AVFoundation adapter) — read the v2.26.0 plan alongside.** C2 (motion bursts) is still open.
- `docs/14-Apr-2026-portable-usb-cam-host-plan.md` — **DONE in v2.26.0** — host-portable `usb-cam` via `tools/usb-cam-host/` HTTP snapshot service; moves cleanly between Mini / MBA / GWTC / any host.
- `docs/14-Apr-2026-image-archive-api-plan.md` — **DONE in v2.25.0** — `/api/v1/images/*` REST surface over the image archive, powers farm-2026's gems/retrospective pages.
- `docs/14-Apr-2026-followups-post-layer1.md` — v2.25.0 layer-2 follow-up list for the frontend dev.
- `docs/14-Apr-2026-modularization-plan.md` — in-progress cleanup plan.
- `docs/14-Apr-2026-audio-triggered-capture-plan.md` — planned: audio-triggered capture on `usb-cam`.
- `docs/14-Apr-2026-system-state-snapshot.md` — **READ FIRST IF YOU'RE A NEW AGENT.** Point-in-time snapshot of every running service, every camera's current wiring, what's committed vs running via `nohup`, known-broken bits (the `com.farm.guardian` agent).
- `docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md` — **READ THIS BEFORE TROUBLESHOOTING THE GATEWAY LAPTOP.** Pre-buries four wrong theories and gives the 30-second diagnostic recipe that actually works.
- `docs/13-Apr-2026-lm-studio-reference.md` — **READ THIS** before adding any LM Studio integration. API surface, locally available models, safe model-load pattern, the 2026-04-13 watchdog incident and what we changed because of it.
- `docs/13-Apr-2026-brooder-vlm-narrator-plan.md` — planned standalone tool: sample brooder snapshots → glm-4.6v-flash → JSONL narrative log. Awaits Boss approval. Will be revised to incorporate "find the best image" rather than blind 5-min sampling.

**Entry point:** `guardian.py` — orchestrates all modules, runs as a foreground process.

**Modules (15 total):**

*Phase 1 — Core pipeline:*
- `discovery.py` — Scans local network for ONVIF cameras. Stores IPs and stream URLs.
- `capture.py` — Frame acquisition. Two parallel modes: (1) `CameraCapture` for RTSP streams (`gwtc`, `mba-cam`); (2) `CameraSnapshotPoller` + `SnapshotSource` adapters for HTTP-snapshot cameras (`house-yard` via `ReolinkSnapshotSource` since v2.18.0; `s7-cam` and **`usb-cam`** via `HttpUrlSnapshotSource` — the latter added in v2.24.0 for the S7 battery path, reused by `usb-cam` in v2.26.0 via the portable `usb-cam-host` service). `UsbSnapshotSource` still exists in the file for anyone who wants to reach AVFoundation directly, but no camera in `config.json` currently dispatches to it. Both modes produce `FrameResult`; the snapshot path also carries the original camera-encoded JPEG for zero-loss display.
- `detect.py` — Runs YOLOv8 inference on frames. Classifies objects. Returns detections with bounding boxes.
- `alerts.py` — Posts Discord messages with snapshots when predator-class animals are detected. Rate-limits alerts.
- `logger.py` — Writes events to SQLite database and legacy JSONL files. Saves snapshots.
- `dashboard.py` — FastAPI web dashboard + API host. Live feeds, PTZ controls, reports, settings. Accessible at `http://macmini:6530`.
- `static/index.html` + `static/app.js` — Dashboard frontend (Tailwind CSS, vanilla JS, no build step).

*Phase 2 — Intelligence foundation:*
- `database.py` — SQLite abstraction layer (8 tables). WAL mode for concurrent reads. Daily backups.
- ~~`vision.py`~~ — **Removed in v2.17.0.** GLM species refinement was over-engineered for this farm. YOLO's class label is now what flows to alerts. Boss directive: "just show me the picture, no classification."
- `tracker.py` — Groups individual detections into animal visit tracks. Used for alert dedup (one Discord post per visit, not one per frame).

*Phase 3 — Deterrence:*
- `camera_control.py` — Reolink camera hardware control via reolink_aio. PTZ move/stop, spotlight, siren, autofocus, guard control, snapshot, position readback. **Does NOT yet support preset save — needs `send_setting()` bypass (see Camera Control section above).**
- `patrol.py` — Step-and-dwell patrol (v2.6.0). 11 positions at 30° intervals, 8-second dwell at each. Replaces continuous sweep. Configurable via `ptz.sweep` in config.
- `deterrent.py` — Automated response engine. 4 escalation levels, per-species rules, cooldowns, effectiveness tracking.
- `ebird.py` — eBird API polling for regional raptor early warning. 30-min intervals during hawk hours.

*Phase 4 — Reporting:*
- `reports.py` — Daily intelligence reports. Species breakdown, deterrent stats, hourly heatmaps, 7-day trends. Exports JSON + Markdown.
- `api.py` — REST API at `/api/v1/`. Endpoints for detections, patterns, camera control, snapshot, position, zoom, autofocus, guard. Exposed via Cloudflare tunnel at `https://guardian.markbarney.net`.

**Config:** `config.json` (copied from `config.example.json`). Contains camera IPs, per-camera RTSP transport (`"tcp"`/`"udp"`), Discord webhook, detection thresholds, deterrent rules, PTZ presets, eBird API key, report settings.

## Network & Machine Access — READ BEFORE TROUBLESHOOTING REACHABILITY

**Two docs are authoritative — read both before you theorize about why something is unreachable:**

- **`~/bubba-workspace/memory/reference/network.md`** — Bubba (this Mac Mini) keeps the master inventory of every machine on the LAN: IPs, MAC addresses (with one known error — see below), SSH keys, users, service ports, the router's admin creds, known quirks.
- **`docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md`** in this repo — full writeup of an afternoon spent misdiagnosing the Gateway laptop. Pre-buries the four wrong theories so you don't repeat them, and gives you the diagnostic recipe that actually works in 30 seconds.

The fast facts you cannot afford to be wrong about:

- **ICMP is blocked between wired and wireless on this router** (TP-Link Archer AX55). Mac Mini on Ethernet ↔ laptop on WiFi will never ping each other regardless of state. Use `nc -z -w 1 <ip> <port>` or direct `ssh`, never `ping`, to test reachability.
- **Windows Firewall is DISABLED on the Gateway laptop.** Don't invent firewall theories — there isn't one to block you. The machine was wiped before being repurposed and has no security suite installed.
- **GWTC's #1 recurring outage is the pre-login WiFi gap, not WSL2.** When GWTC reboots, it sits at the Windows lock screen and **WiFi does not connect until someone types the PIN `5196`**. Every port signature on the /24 comes back empty from the Mac Mini side — not just MediaMTX — because the NIC is literally off the LAN. Earlier writeups blamed a "WSL2 virtual-adapter routing-poisoning bug" for the same symptom; that was a misdiagnosis. The fix is coop-side only: flip on the USB keyboard, type PIN `5196 + Enter`, flip keyboard off. The keyboard is kept off most of the time so turkeys don't mash keys. **Nothing on the Mac Mini side can fix this** — SSH can't reach a machine that's not on the network. Don't script router logins, don't rerun sweeps with different flags. The laptop needs the PIN.
- **GWTC reboots leave ffmpeg in a dshow zombie state — but a watchdog auto-recovers it.** After login, once services are up, the Shawl + ffmpeg + mediamtx services all report `Running`, ffmpeg has a live PID, port 8554 is open — but the `gwtc` RTSP path 404s because ffmpeg is wedged on the dshow camera open and never registers as a publisher. Neither Shawl's `--restart` policy nor the `:loop` retry in `start-camera.bat` triggers (the wedged ffmpeg never exits). **The `farmcam-watchdog` Windows service (deployed 13-Apr-2026) detects this within ~90s and kills the wedged ffmpeg PID; Shawl then respawns it cleanly. You should not need to intervene.** If the watchdog itself is broken (`sc query farmcam-watchdog` not `RUNNING`), fall back to manual recovery: `ssh markb@<gwtc-ip> 'tasklist | findstr ffmpeg'` then `taskkill /F /PID <pid>`. Watchdog code lives at `deploy/gwtc/farm-watchdog.ps1` with install recipe at `deploy/gwtc/install-watchdog.md`. Full failure-mode writeup: `docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md` "Addendum -- Post-Reboot dshow Zombie Pattern" section.
- **The MAC entry for GWTC in the network doc is WRONG.** It lists `FC:6D:77:B8:E8:DB` as GWTC; that MAC actually belongs to the MSI Katana at `.3` (SSH-confirmed 2026-04-13: hostname=MSI, model=Katana 15 HX B14WGK). Don't ARP-hunt for GWTC by that MAC — it'll send you to the wrong host. We don't currently have GWTC's real MAC documented.
- **GWTC does NOT run LM Studio.** Any prior memory or doc claiming GWTC has LM Studio on port 9099 is wrong — that was a cross-wired reference to a different machine. GWTC is a single-purpose chicken-coop camera streamer. Its only distinctive service signature is MediaMTX on port `8554`:
  ```bash
  for i in $(seq 2 254); do (timeout 2 bash -c ":</dev/tcp/192.168.0.$i/8554" 2>/dev/null && echo "192.168.0.$i has MediaMTX (= GWTC)") & done; wait
  ```
  If port 8554 isn't open anywhere on the /24, GWTC is off-network — almost always the pre-login WiFi case above.
- **SSH into GWTC** (once you've found its IP): `ssh -o StrictHostKeyChecking=no markb@<ip>` — Bubba's `id_ed25519` is in `C:\ProgramData\ssh\administrators_authorized_keys` on the laptop.
- **Router admin is read-only by default.** Never change router settings without Boss approval. Terry Kath rule: if you change something that kills connectivity to Bubba, you lose the ability to be told to undo it.

## Multi-Machine Claude Orchestration — USE THIS WHEN A TASK NEEDS HANDS ON ANOTHER BOX

**The default reflex of every agent should be: don't ask Boss to relay a task to another Claude. Spawn one yourself.**

The farm has multiple machines, several of which run Claude Code (Mac Mini "Bubba" — primary; MacBook Air at `192.168.0.50` — `c` alias installed; Windows laptops at `192.168.0.68`/`.194` etc.). Whenever something needs hands at another machine — granting a TCC permission, running a GUI app, reading a local file you can't `scp`, anything where being-on-that-box matters — **invoke a fresh headless Claude on that box over SSH**. Don't ask Boss to copy-paste your prompt into a session he's sitting in front of.

**The pattern (from the Mac Mini, targeting the MacBook Air):**

```bash
ssh -i ~/.ssh/id_ed25519 markb@192.168.0.50 'c -p "Granular task description here. Be self-contained — the remote Claude has no context from this conversation. Tell it what to do, what success looks like, and what to print on completion."'
```

- `c` is the alias on every machine for `claude --dangerously-skip-permissions` — already in `~/.zshrc`/`~/.bash_profile`/`~/.bashrc` on the Air, the Mini, and GWTC.
- `-p` (print mode) runs the prompt non-interactively, prints the result, exits. No TTY needed.
- The remote Claude runs **on that box's filesystem and GUI session** — it can spawn TCC prompts, open Finder, drive AppleScript, read files only on that disk. None of which the calling agent can do over plain SSH.

**Per-machine quick reference:**

| Box | SSH | Claude available? |
|---|---|---|
| Mac Mini (Bubba) | local — you're already here | yes (this is the orchestrator most of the time) |
| MacBook Air | `ssh -i ~/.ssh/id_ed25519 markb@192.168.0.50` | yes — `c` alias, OAuth-logged-in |
| Gateway laptop (GWTC) | `ssh -o StrictHostKeyChecking=no markb@<gwtc-ip>` (find IP via /24 sweep on `:8554`) | yes — pinned `c.cmd` per the GWTC notes |
| Larry's MSI laptop | `ssh -o StrictHostKeyChecking=no user@192.168.0.194` | yes — but Larry is its own thing; see `bubba-workspace/skills/larry-access` |
| Egon's Linode | `ssh -i ~/.ssh/id_ed25519_egon_rescue euclid@172.104.147.157` | yes — see `bubba-workspace/skills/egon-gateway` |

**When you should use this pattern (non-exhaustive):**

- Granting TCC permissions (Camera, Microphone, Accessibility, Screen Recording) — these need a logged-in GUI session and can't be granted over plain SSH. A local Claude can fire the prompt for Boss to click.
- Triggering AppleScript / Automator / `osascript` flows that need to run as the logged-in GUI user.
- Reading or modifying files on a disk you can't mount (e.g., another machine's keychain, login items, browser profiles).
- Running interactive installers, GUI app first-launch dialogs, or `defaults` writes that take effect per-user-session.
- Anything you'd otherwise type out as "ask the Boss to do this on the other machine" — that's the smell that means: spawn a Claude there.

**Caveats:**

- The remote Claude has **no context from your conversation** — your prompt must be self-contained. State the task, the success criteria, where to look for relevant docs (paths on *that* machine, not on yours), and what to print so you can verify completion.
- Don't spawn a Claude on a box for a task you could trivially do over plain SSH (e.g., `tail` a log, `ls` a directory). Use this pattern when *the locality matters*.
- The `--dangerously-skip-permissions` flag is in the `c` alias because every farm Claude runs in trusted-LAN, single-user mode. Don't unset it when invoking — the headless print mode will block on every permission prompt otherwise.
- Output comes back as the SSH command's stdout. If the task needs to ping you back asynchronously, have the remote Claude write a marker file (e.g., `/tmp/<task>-done.flag`) and you poll for it.
- Coordinated edits to the same file from two Claudes simultaneously is not safe. Either serialize, or have one Claude commit and the other pull before editing.

**Cross-reference:** `bubba-workspace/skills/macbook-air/SKILL.md` has the per-machine details for the Air; `bubba-workspace/memory/reference/network.md` has the master device table; `bubba-workspace/skills/larry-access/SKILL.md` and `egon-gateway/SKILL.md` document the Windows and Linode targets respectively.

## Environment

- **Machine:** Mac Mini M4 Pro, 14-core, 64GB RAM, macOS 26.3
- **Python:** 3.13 (Homebrew)
- **Camera 1 (house-yard):** Reolink E1 Outdoor Pro — ONVIF, RTSP, 4K, PTZ, WiFi. IP `192.168.0.88`. **Polls the camera's HTTP `cmd=Snap` endpoint for native 4K JPEGs** (`source: "snapshot"`, `snapshot_method: "reolink"`); we no longer use RTSP for this camera. Snapshot interval 5s for the dashboard, 2s during the night detection window so YOLO has more chances per minute. The RTSP path was abandoned because the lossy WiFi link mangled HEVC reference packets — see CHANGELOG v2.16.0/v2.17.0/v2.18.0 and `docs/13-Apr-2026-phase-a-reolink-snapshot-polling-plan.md`.
- **Camera 2 (s7-cam):** Samsung Galaxy S7 phone running IP Webcam app (RTSP Camera Server). RTSP over WiFi (UDP). IP `192.168.0.249`, port 5554. Stream URL: `rtsp://192.168.0.249:5554/camera`. No auth required. Fixed camera, no PTZ. Uses `rtsp_url_override` — no ONVIF. Detection disabled.
- **Camera 3 (usb-cam):** Generic USB webcam (1920×1080), **host-portable as of v2.26.0**. Frames flow through the `usb-cam-host` FastAPI snapshot service (`tools/usb-cam-host/usb_cam_host.py`) running on whichever machine the camera is plugged into — currently the Mac Mini on port `8089`, `http://192.168.0.71:8089/photo.jpg`. Guardian consumes it via `HttpUrlSnapshotSource` (`snapshot_method: "http_url"`); the pipeline consumes it via `capture_ip_webcam` (`capture_method: "ip_webcam"`). Boss plans to move the camera to the MacBook Air; that's config-only on the Mini side once the service is installed on the new host. Detection disabled.
- **Camera 4 (gwtc):** Gateway laptop (Windows 11) built-in webcam. Streams via ffmpeg → MediaMTX at `rtsp://192.168.0.68:8554/gwtc` (path renamed from `/nestbox` 13-Apr-2026 — see CHANGELOG v2.24.1). 1280x720, 15fps, H.264, ~1 Mbps. Services auto-start via Shawl, with the `farmcam-watchdog` Shawl service handling post-reboot recovery. Uses `rtsp_url_override` in config. Detection disabled. Currently in the chicken coop.
- **Network:** All devices on same local WiFi network

## Key Dependencies

- `opencv-python` — RTSP stream capture and frame processing
- `ultralytics` — YOLOv8 model loading and inference
- `onvif-zeep` — ONVIF camera discovery and control
- `reolink-aio` — Reolink camera control (PTZ, spotlight, siren)
- `aiohttp` — Async HTTP (required by reolink-aio)
- `requests` — Discord webhook and eBird API HTTP posts
- `Pillow` — Image saving and manipulation
- `fastapi` + `uvicorn` — Local web dashboard + REST API
- `python-multipart` — Form support for FastAPI
- `sqlite3` (stdlib) — Structured detection/track/alert storage

---

## Coding Standards (MANDATORY — from the boss)

These standards apply to ALL code in this repository. Non-negotiable.

### Mission & Critical Warnings

- Every Python file you create or edit must start with this header (update it whenever you touch the file):
  ```
  Author: {Your Model Name}
  Date: {DD-Month-YYYY}
  PURPOSE: Verbose details about functionality, integration points, dependencies
  SRP/DRY check: Pass/Fail — did you verify existing functionality?
  ```
- Comment the non-obvious parts of your code; explain integrations inline where logic could confuse future contributors.
- If you edit file headers, update the metadata to reflect your changes; never add headers to formats that do not support comments (JSON, etc.).
- Changing behavior requires updating relevant docs and the top entry of `CHANGELOG.md` (SemVer, what/why/how, include author).
- Never guess about unfamiliar or recently updated libraries/frameworks — ask for docs or locate them yourself.
- Mention when a web search could surface critical, up-to-date information.
- Ask clarifying questions only after checking docs; call out where a plan or docs are unclear.
- The user does not care about speed. Slow down, ultrathink, and secure plan approval before editing.

### Role, User Context & Communication

- You are an elite software architect with 20+ years of experience. Enforce SRP/DRY obsessively.
- The user is a hobbyist / non-technical executive. Keep explanations concise, friendly, and free of jargon.
- The project serves ~4–5 users. Ship pragmatic, production-quality solutions rather than enterprise abstractions.
- **Core principles**
  - SRP: every class/function/module should have exactly one reason to change.
  - DRY: reuse utilities/components; search before creating anything new.
  - Modular reuse: study existing patterns and compose from them.
  - Production readiness only: no stubs, mocks, placeholders, or fake data.
  - Robust naming, strong error handling, and commented complex logic.
- **Design & style guidelines**
  - Avoid "AI slop": no unnecessary abstractions, no over-engineered class hierarchies.
  - Create intentional, high-quality code with purposeful structure.
- **Communication rules**
  - Keep responses tight; never echo chain-of-thought.
  - Ask only essential questions after consulting docs.
  - Pause when errors occur, think, then request input if truly needed.
  - End completed tasks with "done" (or "next" if awaiting instructions).
- **Development context**
  - Small hobby project: consider cost/benefit of every change.
  - Assume environment variables, secrets, and external APIs are healthy; treat issues as your bug to diagnose.

### Workflow, Planning & Version Control

1. **Deep analysis** — Study existing architecture for reuse opportunities before touching code.
2. **Plan architecture** — Create `{date}-{goal}-plan.md` inside `docs/` with scope, objectives, and TODOs; seek user approval.
3. **Implement modularly** — Follow established patterns; keep components/functions focused.
4. **Verify integration** — Use real APIs/services; never rely on mocks or placeholder flows.
5. **Version control discipline** — Update `CHANGELOG.md` at the top (SemVer ordering) with what/why/how and your model name.
6. **Documentation expectations** — Provide architectural explanations, highlight SRP/DRY fixes, point to reused modules.

### File Conventions

- **File headers** — Required for all Python file changes; update the metadata each time you modify a file.
- **Commenting** — Add inline comments when logic, integration points, or failure modes are not obvious.
- **No placeholders** — Ship only real implementations; remove TODO scaffolding before submitting.
- **Naming & structure** — Use consistent naming, exhaustive error handling, and shared helpers/utilities.

### Error Handling

- Camera disconnection → log warning, retry with backoff, don't crash
- YOLO inference failure → log error, skip frame, continue
- Vision model timeout → fall back to YOLO class, log warning
- Deterrent action failure → log error, skip action, don't block pipeline
- eBird API failure → log error, skip poll cycle, retry next interval
- Discord API failure → log error, buffer alert, retry
- SQLite write failure → log error, continue (JSONL fallback still writes)
- Never silently swallow exceptions

### What NOT To Do

- Don't add external/hosted web services — the dashboard is local-network only (Phase 5 will add hosting)
- Don't add cloud APIs for detection — everything runs locally
- Don't add a second database — SQLite is the single data store (Phase 5 adds PostgreSQL sync)
- Don't over-abstract — this has 15 modules, each with one clear responsibility
- Don't create empty placeholder files — every file ships with real code
- Don't add dependencies that aren't in requirements.txt
- Don't ship stubs, mocks, or fake data

### Prohibited Actions


- Never commit secrets, API keys, or credentials
- Never add headers to JSON or other non-comment formats
- Never guess at library behavior — check documentation first
- Never ship placeholder or stub code

---

## Remote Camera Operations

**All camera-specific procedures, API endpoints, shapes, and operational knowledge live in `AGENTS_CAMERA.md`.** Read that file before any camera work. It contains everything a remote assistant needs to operate the camera correctly — learned from real mistakes, not guesses.
