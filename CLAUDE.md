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
- `docs/13-Apr-2026-phase-a-reolink-snapshot-polling-plan.md` — **DONE in v2.18.0** — house-yard switched from RTSP to HTTP snapshot polling (4K JPEG)
- `docs/13-Apr-2026-phase-b-gwtc-snapshot-endpoint-plan.md` — Phase B: stand up an HTTP snapshot service on the Gateway laptop, switch `gwtc` over
- `docs/13-Apr-2026-phase-c-usb-highres-and-motion-bursts-plan.md` — Phase C: `usb-cam` to high-res snapshots + ONVIF motion-event-triggered snapshot bursts on house-yard
- `docs/13-Apr-2026-lm-studio-reference.md` — **READ THIS** before adding any LM Studio integration. API surface, locally available models, safe model-load pattern, the 2026-04-13 watchdog incident and what we changed because of it.
- `docs/13-Apr-2026-brooder-vlm-narrator-plan.md` — planned standalone tool: sample brooder snapshots → glm-4.6v-flash → JSONL narrative log. Awaits Boss approval. Will be revised to incorporate "find the best image" rather than blind 5-min sampling.

**Entry point:** `guardian.py` — orchestrates all modules, runs as a foreground process.

**Modules (15 total):**

*Phase 1 — Core pipeline:*
- `discovery.py` — Scans local network for ONVIF cameras. Stores IPs and stream URLs.
- `capture.py` — Frame acquisition. Two parallel modes: (1) `CameraCapture` for RTSP/USB OpenCV streams (gwtc, s7-cam, usb-cam); (2) `CameraSnapshotPoller` + `SnapshotSource` adapters for HTTP-snapshot cameras (house-yard via `ReolinkSnapshotSource` since v2.18.0). Both produce `FrameResult`; the snapshot path also carries the original camera-encoded JPEG for zero-loss display.
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

**If you think a camera or the Gateway laptop is "offline" — STOP and read `~/bubba-workspace/memory/reference/network.md` first.** Bubba (this Mac Mini) keeps a complete reference of every machine on the LAN there: IPs, MAC addresses, SSH keys, users, service ports, the router's admin creds, known quirks. Things that will save you (and everyone else) from embarrassing misdiagnoses:

- **ICMP is blocked between wired and wireless on this router** (TP-Link Archer AX55). Mac Mini on Ethernet ↔ laptop on WiFi will never ping each other regardless of state. Use `nc -z -w 1 <ip> <port>` or direct `ssh`, never `ping`, to test reachability.
- **Windows Firewall is DISABLED on the Gateway laptop.** Don't invent firewall theories to explain reachability issues — there isn't one to block you.
- **The Gateway laptop has a known WSL2 virtual-adapter routing-poisoning bug.** If SSH to it stops working, the fix (per that doc) is: `netsh winsock reset; netsh int ip reset` then reboot, done at the console. Nothing on the Mac Mini side can cause or fix this. Not the chickens. Not port scans. Not Guardian restarts.
- **IPs are DHCP and can change after a reboot or a long WiFi disassociation.** When GWTC isn't at `192.168.0.68` any more, do the documented subnet scan for SSH (port 22) or MediaMTX (port 8554):
  ```bash
  for i in $(seq 2 254); do (nc -z -w 1 192.168.0.$i 22 2>/dev/null && echo "192.168.0.$i SSH OPEN") & done; wait
  ```
- **SSH into GWTC:** `ssh -o StrictHostKeyChecking=no markb@<ip>` — Bubba's `id_ed25519` is in `C:\ProgramData\ssh\administrators_authorized_keys` on the laptop.
- **Router admin is read-only by default.** Never change router settings without Boss approval. The Terry Kath rule: if you change something that kills connectivity to Bubba, you lose the ability to be told to undo it.

`~/bubba-workspace/memory/reference/network.md` is the authoritative copy — don't duplicate it here, read it there.

## Environment

- **Machine:** Mac Mini M4 Pro, 14-core, 64GB RAM, macOS 26.3
- **Python:** 3.13 (Homebrew)
- **Camera 1 (house-yard):** Reolink E1 Outdoor Pro — ONVIF, RTSP, 4K, PTZ, WiFi. IP `192.168.0.88`. **Polls the camera's HTTP `cmd=Snap` endpoint for native 4K JPEGs** (`source: "snapshot"`, `snapshot_method: "reolink"`); we no longer use RTSP for this camera. Snapshot interval 5s for the dashboard, 2s during the night detection window so YOLO has more chances per minute. The RTSP path was abandoned because the lossy WiFi link mangled HEVC reference packets — see CHANGELOG v2.16.0/v2.17.0/v2.18.0 and `docs/13-Apr-2026-phase-a-reolink-snapshot-polling-plan.md`.
- **Camera 2 (s7-cam):** Samsung Galaxy S7 phone running IP Webcam app (RTSP Camera Server). RTSP over WiFi (UDP). IP `192.168.0.249`, port 5554. Stream URL: `rtsp://192.168.0.249:5554/camera`. No auth required. Fixed camera, no PTZ. Uses `rtsp_url_override` — no ONVIF. Detection disabled.
- **Camera 3 (usb-cam):** USB camera connected directly to the Mac Mini. AVFoundation device index 0. 1920x1080. No network dependency — captured locally via OpenCV. Detection disabled.
- **Camera 4 (gwtc):** Gateway laptop built-in webcam. Streams via ffmpeg → MediaMTX at `rtsp://192.168.0.68:8554/nestbox`. 1280x720, 15fps, H.264, ~1 Mbps. Windows 11, services auto-start via Shawl. Uses `rtsp_url_override` in config. Detection disabled. Destined for the chicken coop.
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
