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

## Architecture

Read `docs/02-Apr-2026-v2-system-plan.md` for the full v2 architecture document with module specifications.

**All plans live in `docs/`:**
- `docs/01-Apr-2026-v1-guardian-plan.md` — Original v1 plan
- `docs/02-Apr-2026-v2-system-plan.md` — Full v2 architecture spec (15 modules)
- `docs/02-Apr-2026-smart-devices-plan.md` — Smart plug deterrent integration (future)
- `docs/04-Apr-2026-full-cleanup-plan.md` — Current: stabilization & cleanup

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
- `camera_control.py` — Reolink camera hardware control via reolink_aio. PTZ, spotlight, siren, patrol with pause/resume.
- `deterrent.py` — Automated response engine. 4 escalation levels, per-species rules, cooldowns, effectiveness tracking.
- `ebird.py` — eBird API polling for regional raptor early warning. 30-min intervals during hawk hours.

*Phase 4 — Reporting:*
- `reports.py` — Daily intelligence reports. Species breakdown, deterrent stats, hourly heatmaps, 7-day trends. Exports JSON + Markdown.
- `api.py` — REST API at `/api/v1/` for LLM tool queries. 14 endpoints for detections, patterns, camera control.

**Config:** `config.json` (copied from `config.example.json`). Contains camera IPs, Discord webhook, detection thresholds, deterrent rules, PTZ presets, eBird API key, report settings.

## Environment

- **Machine:** Mac Mini M4 Pro, 14-core, 64GB RAM, macOS 26.3
- **Python:** 3.13 (Homebrew)
- **Camera:** Reolink E1 Outdoor Pro (ONVIF, RTSP, 4K, PTZ, WiFi)
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
