# CLAUDE.md — Farm Guardian

This file provides guidance to AI coding agents working in this repository.

## Project

Farm Guardian — a Python service that watches Reolink security cameras via ONVIF/RTSP, detects predator animals using YOLOv8, sends Discord alerts, and serves a local web dashboard for monitoring and control. Runs on a Mac Mini M4 Pro (64GB) on the same local network as the cameras.

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

No test suite yet. This is a v1 prototype.

## Architecture

Read `PLAN.md` for the full architecture document with module specifications.

**Entry point:** `guardian.py` — orchestrates all modules, runs as a foreground process.

**Modules:**
- `discovery.py` — Scans local network for ONVIF cameras. Stores IPs and stream URLs.
- `capture.py` — Connects to camera RTSP streams. Grabs frames at configurable intervals (~1fps).
- `detect.py` — Runs YOLOv8 inference on frames. Classifies objects. Returns detections with bounding boxes.
- `alerts.py` — Posts Discord messages with snapshots when predator-class animals are detected. Rate-limits alerts.
- `logger.py` — Writes structured JSON event logs. Saves snapshot images to `events/YYYY-MM-DD/`.
- `dashboard.py` — FastAPI web dashboard served locally. Live camera feeds, detection timeline, alert history, full config controls. Accessible at `http://macmini:8080`.
- `static/index.html` + `static/app.js` — Dashboard frontend (Tailwind CSS, vanilla JS, no build step).

**Config:** `config.json` (copied from `config.example.json`). Contains camera IPs, Discord webhook URL, detection thresholds, alert settings.

## Environment

- **Machine:** Mac Mini M4 Pro, 14-core, 64GB RAM, macOS 26.3
- **Python:** 3.13 (Homebrew)
- **Camera:** Reolink E1 Outdoor Pro (ONVIF, RTSP, 4K, PTZ, WiFi)
- **Network:** All devices on same local WiFi network

## Key Dependencies

- `opencv-python` — RTSP stream capture and frame processing
- `ultralytics` — YOLOv8 model loading and inference
- `onvif-zeep` — ONVIF camera discovery and control
- `requests` — Discord webhook HTTP posts
- `Pillow` — Image saving and manipulation
- `fastapi` + `uvicorn` — Local web dashboard
- `python-multipart` — Form support for FastAPI

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
- Discord API failure → log error, buffer alert, retry
- Never silently swallow exceptions

### What NOT To Do

- Don't add external/hosted web services — the dashboard is local-network only
- Don't add cloud APIs for detection — everything runs locally
- Don't add a database — JSON logs and filesystem storage only
- Don't over-abstract — this has 6 modules, not 60
- Don't create empty placeholder files — every file ships with real code
- Don't add dependencies that aren't in requirements.txt
- Don't ship stubs, mocks, or fake data

### Prohibited Actions

- Never push directly to `main` without review
- Never commit secrets, API keys, or credentials
- Never add headers to JSON or other non-comment formats
- Never guess at library behavior — check documentation first
- Never ship placeholder or stub code
