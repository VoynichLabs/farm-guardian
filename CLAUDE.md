# CLAUDE.md — Farm Guardian

This file provides guidance to AI coding agents working in this repository.
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


## Project

Farm Guardian — a Python service that watches Reolink security cameras via ONVIF/RTSP, detects predator animals using YOLOv8 + GLM vision model, automates camera deterrents (spotlight/siren/PTZ), tracks animal visits in SQLite, generates daily intelligence reports, and serves a local web dashboard with REST API. Runs on a Mac Mini M4 Pro (64GB) on the same local network as the cameras.

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

## Recent Changes (08-Apr-2026)

**Remote camera control API (v2.7.0):** Five new endpoints in `api.py` for full remote camera control over the Cloudflare tunnel: snapshot, position readback, zoom, autofocus, guard control. A remote Claude session can now control the camera from anywhere.

**Step-and-dwell patrol (v2.6.0):** Patrol rewritten. Camera steps through 11 positions at 30° intervals, dwells 8 seconds at each for clean stationary frames. Replaces continuous sweep that produced motion-blurred garbage.

**Cloudflare tunnel live:** Guardian dashboard exposed at `https://guardian.markbarney.net` via Cloudflare tunnel from the Mac Mini. No port forwarding needed.

**TODO:**
- Implement preset save/recall API endpoints (see "Camera Control" section below)
- Front camera mirror mode for hatched chick — switch RTSP Camera Server to front camera on the S7 screen so the chick can see herself (enrichment)

---

## Camera Control — Critical Knowledge

**READ THIS BEFORE TOUCHING PTZ CODE.** Previous assistants have gotten this wrong multiple times.

### The Reolink E1 Outdoor Pro does NOT support absolute pan/tilt positioning

There is no "go to pan=3600, tilt=28" command. The Reolink HTTP API only supports:
- **Directional move/stop** — start moving in a direction, poll position, send stop. Unreliable over the internet.
- **Preset recall** — `PtzCtrl` with `op: "ToPos"` + `id: N` jumps to a saved preset. Instant and precise.
- **Preset save** — `PtzCtrl` with `op: "setPos"` + `id: N` + `name: "house"` saves the current position as a preset.

This is a firmware limitation confirmed by the `reolink_aio` library maintainer ([issue #147](https://github.com/starkillerOG/reolink_aio/issues/147)). Do not waste time trying to send absolute coordinates.

### The reolink_aio library is a partial wrapper, not the full API

The library validates PTZ commands against `PtzEnum` which only has directional commands. It blocks commands like `"setPos"` for saving presets. **To access the full camera API, bypass the library and call `host.send_setting()` directly with raw JSON bodies.** The camera accepts anything its firmware supports — the library is just a middleman with incomplete coverage.

**Key principle:** Anything the Reolink phone app can do, we can do. The app is just an HTTP client hitting the same API. If the library doesn't expose a feature, send the raw command.

### Preset-based positioning (the correct approach)

Save named presets at key positions, then recall them instantly:

```python
# Save current position as preset (bypasses library validation)
body = [{"cmd": "PtzCtrl", "action": 0, "param": {"channel": 0, "op": "setPos", "id": 0, "name": "house"}}]
await host.send_setting(body)

# Recall preset (library supports this)
ctrl.ptz_goto_preset(camera_id, preset_index=0)
```

### Remote PTZ speed warning

Even speed 5 moves at ~85°/second. Over the Cloudflare tunnel, network latency makes move/stop cycles unreliable — you will overshoot. For remote sessions, use presets whenever possible. If you must nudge manually, use very short bursts (0.3s move, stop, check position, repeat).

### Autofocus

After any movement, trigger autofocus and wait 2-3 seconds before taking a snapshot. Without this wait, images are blurry. This is non-negotiable.

### Zoom is out of scope

Do not add zoom features. The camera is always at zoom 0 (widest). Autofocus handles everything at that setting. Zoom adds complexity for zero benefit right now.

### World model (what the camera sees)

| Pan (degrees) | Pan (raw) | Location | Notes |
|---------------|-----------|----------|-------|
| 0° / 360° | 0 / 7200 | DEAD ZONE — mounting post | Post blocks 40% of frame. Skip. |
| ~90° | ~1800 | Yard / hillside | Green slope, fire pit, treeline |
| ~180° | ~3600 | **THE HOUSE** | Chickens, coop, truck, primary monitoring view |
| ~270° | ~5400 | Old stable foundation | Property edge, Rose of Sharon bushes, corn field neighbor |

Coordinate system: Pan 0–7200 (20 units per degree). Pan right = increasing values. Tilt readback is broken at many angles.

See `docs/08-Apr-2026-absolute-ptz-investigation.md` for full investigation details and `docs/08-Apr-2026-camera-setup-handoff.md` for operational procedures.

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

**Entry point:** `guardian.py` — orchestrates all modules, runs as a foreground process.

**Modules (15 total):**

*Phase 1 — Core pipeline:*
- `discovery.py` — Scans local network for ONVIF cameras. Stores IPs and stream URLs.
- `capture.py` — Connects to camera RTSP streams. Grabs frames at configurable intervals (~1fps).
- `detect.py` — Runs YOLOv8 inference on frames. Classifies objects. Returns detections with bounding boxes.
- `alerts.py` — Posts Discord messages with snapshots when predator-class animals are detected. Rate-limits alerts.
- `logger.py` — Writes events to SQLite database and legacy JSONL files. Saves snapshots.
- `dashboard.py` — FastAPI web dashboard + API host. Live feeds, PTZ controls, reports, settings. Accessible at `http://macmini:6530`.
- `static/index.html` + `static/app.js` — Dashboard frontend (Tailwind CSS, vanilla JS, no build step).

*Phase 2 — Intelligence foundation:*
- `database.py` — SQLite abstraction layer (8 tables). WAL mode for concurrent reads. Daily backups.
- `vision.py` — GLM vision model species refinement via LM Studio. Distinguishes hawk/chicken, bobcat/house-cat.
- `tracker.py` — Groups individual detections into animal visit tracks. Duration, confidence, outcome tracking.

*Phase 3 — Deterrence:*
- `camera_control.py` — Reolink camera hardware control via reolink_aio. PTZ move/stop, spotlight, siren, autofocus, guard control, snapshot, position readback. **Does NOT yet support preset save — needs `send_setting()` bypass (see Camera Control section above).**
- `patrol.py` — Step-and-dwell patrol (v2.6.0). 11 positions at 30° intervals, 8-second dwell at each. Replaces continuous sweep. Configurable via `ptz.sweep` in config.
- `deterrent.py` — Automated response engine. 4 escalation levels, per-species rules, cooldowns, effectiveness tracking.
- `ebird.py` — eBird API polling for regional raptor early warning. 30-min intervals during hawk hours.

*Phase 4 — Reporting:*
- `reports.py` — Daily intelligence reports. Species breakdown, deterrent stats, hourly heatmaps, 7-day trends. Exports JSON + Markdown.
- `api.py` — REST API at `/api/v1/`. Endpoints for detections, patterns, camera control, snapshot, position, zoom, autofocus, guard. Exposed via Cloudflare tunnel at `https://guardian.markbarney.net`.

**Config:** `config.json` (copied from `config.example.json`). Contains camera IPs, per-camera RTSP transport (`"tcp"`/`"udp"`), Discord webhook, detection thresholds, deterrent rules, PTZ presets, eBird API key, report settings.

## Environment

- **Machine:** Mac Mini M4 Pro, 14-core, 64GB RAM, macOS 26.3
- **Python:** 3.13 (Homebrew)
- **Camera 1 (house-yard):** Reolink E1 Outdoor Pro — ONVIF, RTSP, 4K, PTZ, WiFi. IP `192.168.0.88`. Needs TCP RTSP transport (HEVC over WiFi/UDP drops packets).
- **Camera 2 (nesting-box):** Samsung Galaxy S7 (SM-G930F, Android 8.0.0, arm64-v8a) running RTSP Camera Server (com.miv.rtspcamera). IP `192.168.0.249`, RTSP port 5554. Needs UDP RTSP transport (only transport this app supports). Phone is factory-reset, bloatware disabled, kiosk mode (always-on, max brightness, no screen timeout). Connected via USB and WiFi.
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

## Remote Camera Assistant Playbook

**This section is for assistants running remotely (via Claude Code web, Railway, or any session NOT on the Mac Mini).** If you are running locally on the Mac Mini, you can use direct Python instead — see `docs/08-Apr-2026-camera-setup-handoff.md`.

### Prerequisites

- Guardian must be running on the Mac Mini (`python guardian.py --debug`)
- The Cloudflare tunnel must be active (it runs as a LaunchAgent, should be automatic)
- Base URL: `https://guardian.markbarney.net/api/v1`

### How to take a clean snapshot

This is the single most common operation. Do it exactly this way every time:

```bash
# 1. Trigger autofocus
curl -s -X POST https://guardian.markbarney.net/api/v1/cameras/house-yard/autofocus

# 2. WAIT 3 seconds — non-negotiable, the lens needs time to settle
sleep 3

# 3. Take the snapshot
curl -s https://guardian.markbarney.net/api/v1/cameras/house-yard/snapshot \
  --output /tmp/snap_descriptive_name.jpg

# 4. Read the image (you'll see it, the user won't)
# Use the Read tool on the .jpg file

# 5. Describe what you see to the user in detail
```

**You cannot display images to the user in chat.** You can only describe what you see. Be specific — mention landmarks, objects, animals, changes from previous snapshots.

### How to read camera position

```bash
curl -s https://guardian.markbarney.net/api/v1/cameras/house-yard/position
# Returns: {"camera_id":"house-yard","pan":3600,"pan_degrees":180.0,"tilt":28,"zoom":0}
```

Use the world model table (in "Camera Control" section above) to understand what the camera is looking at based on pan degrees.

### How to move the camera (once presets are implemented)

**Preferred — use presets:**
```bash
# Go to a named position (instant, reliable, no overshooting)
curl -s -X POST https://guardian.markbarney.net/api/v1/cameras/house-yard/preset/goto \
  -H "Content-Type: application/json" -d '{"preset": "house"}'
```

**Fallback — manual nudge (use only if presets aren't available):**
```bash
# Short burst: move, wait 0.3-0.5s, stop, check position, repeat
curl -s -X POST https://guardian.markbarney.net/api/v1/cameras/house-yard/ptz \
  -H "Content-Type: application/json" -d '{"action":"move","pan":-1,"tilt":0,"speed":5}'
sleep 0.4
curl -s -X POST https://guardian.markbarney.net/api/v1/cameras/house-yard/ptz \
  -H "Content-Type: application/json" -d '{"action":"stop"}'
curl -s https://guardian.markbarney.net/api/v1/cameras/house-yard/position
# Check position, repeat if needed
```

**WARNING:** Each 0.5s burst at speed 5 moves ~43°. Do NOT sleep longer than 0.5s before stopping — you will overshoot. Always stop, check, then move again.

### How to respond to Mark's commands

Mark will message from his phone while outside. He expects immediate action, not questions.

| Mark says | You do |
|-----------|--------|
| "pan left" / "pan right" | Short PTZ burst in that direction, report new position |
| "look at the house" | Go to preset "house" (~180°) or nudge to ~3600 pan |
| "what do you see?" | Take snapshot (with autofocus wait), describe in detail |
| "tilt up" / "tilt down" | Short tilt burst, report new position |
| "is it in focus?" | Take snapshot, evaluate sharpness, report honestly |
| "stop" | Send PTZ stop command immediately |

### Monitoring task (if requested)

Mark may ask you to check the camera periodically. Since remote sessions cannot schedule cron jobs, do a check with every message he sends:

1. Read position
2. Trigger autofocus, wait 3 seconds
3. Take snapshot with descriptive filename: `snap_NNN_HHMM_panXXXdeg_tiltYY.jpg`
4. Read and describe the image
5. Note changes from previous check
6. Append observations to `/tmp/camera_observations.md`

### Patrol conflict

If Guardian's step-and-dwell patrol is running, it will override your manual PTZ commands every ~8 seconds. You cannot win this fight. If Mark wants manual control, Guardian's patrol must be stopped first (Mark or Bubba needs to do this on the Mac Mini).

### Lessons learned (from 08-Apr-2026 session)

- **Don't declare things impossible without reading the full source.** The reolink_aio library is ~5000 lines. Skimming it and trusting a GitHub issue led to a wrong conclusion about preset saving.
- **The Reolink camera is just an HTTP server.** Anything the Reolink phone app can do, we can do with raw JSON commands via `send_setting()`. The library is a convenience wrapper, not a capability boundary.
- **Speed 5 is not slow.** It moves at ~85°/second. The handoff doc's advice was calibrated for local 0.3s polling, not remote control over Cloudflare.
- **Always wait for autofocus.** 3 seconds minimum. Every blurry snapshot in this project's history was caused by skipping this wait.
