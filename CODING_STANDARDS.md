# Coding Standards — Farm Guardian

**Author:** The User (aka YOUR BOSS!!)
**Date:** 02-April-2026
**Purpose:** Mandatory coding standards for AI agents and contributors working in this repository.
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

---

## 1. Mission

Farm Guardian is a Python service that watches Reolink security cameras via ONVIF/RTSP, detects predator animals with YOLOv8, sends Discord alerts, and serves a local web dashboard for monitoring and control. It runs on a Mac Mini M4 Pro (64GB) on the same local network as the cameras. No cloud. No subscriptions. Dashboard is local-only.

**Read these files before touching code:**
- `PLAN.md` — Full architecture, design decisions, v1 scope
- `CLAUDE.md` — Agent-specific instructions, commands, environment details
- `config.example.json` — All configurable parameters

---

## 2. Role & Communication

- You are an elite software architect. Enforce SRP/DRY obsessively.
- The user is a hobbyist / non-technical executive. Keep explanations concise, friendly, jargon-free.
- This serves ~4–5 users. Ship pragmatic, production-quality code — not enterprise abstractions.
- Keep responses tight. Never echo chain-of-thought.
- Ask only essential questions after consulting docs first.
- End completed tasks with "done" (or "next" if awaiting instructions).
- The user does not care about speed. Slow down, think, and get plan approval before editing.

---

## 3. Architecture

```
farm-guardian/
├── guardian.py       ← Entry point — orchestrates all modules
├── discovery.py      ← ONVIF camera scanner + RTSP URL resolution
├── capture.py        ← RTSP frame grabber (per-camera threads, 4K→1080p)
├── detect.py         ← YOLOv8 inference + false-positive suppression
├── alerts.py         ← Discord webhook alerts with rate limiting
├── logger.py         ← JSONL event logs + snapshot images
├── dashboard.py      ← FastAPI web dashboard (local network only)
├── static/
│   ├── index.html    ← Dashboard UI (Tailwind CSS, vanilla JS)
│   └── app.js        ← Dashboard frontend logic
├── config.json       ← Runtime config (gitignored — copy from example)
├── config.example.json
├── requirements.txt
├── events/           ← Daily snapshot dirs (gitignored)
└── models/           ← YOLO weights (gitignored)
```

**Data flow:**
```
Camera (RTSP) → capture.py → detect.py → alerts.py → Discord
                    │                   → logger.py → events/YYYY-MM-DD/
                    │
                    └→ dashboard.py → Browser (http://macmini:8080)
```

Each module has exactly one job. Don't merge responsibilities. Don't add modules without a clear reason.

**Dashboard (`dashboard.py`):** FastAPI app served on the local network. Provides live MJPEG camera feeds, detection timeline, alert history, camera start/stop/rescan controls, detection threshold tuning, zone masking config, and Discord alert testing. All controls apply immediately — config changes are saved to `config.json` and applied live where possible.

---

## 4. Core Principles

- **SRP:** Every class, function, and module has exactly one reason to change.
- **DRY:** Search before creating anything new. Reuse existing utilities.
- **No stubs:** Ship only real, working implementations. No placeholders, mocks, or fake data.
- **No over-engineering:** This has 6 modules, not 60. Don't add abstraction layers, factories, or registries.
- **Robust error handling:** Every external boundary (camera, network, Discord, filesystem) must handle failures gracefully.

---

## 5. File Headers

Every Python file must start with this comment header. Update it each time you modify the file:

```python
# Author: {Your Model Name}
# Date: {DD-Month-YYYY}
# PURPOSE: {Verbose description of what this file does, its integration points,
#          and its dependencies on other modules}
# SRP/DRY check: Pass/Fail — did you verify no existing module already does this?
```

**Never** add headers to JSON, `.gitignore`, or other non-comment formats.

---

## 6. Error Handling

These are non-negotiable. The service must never crash from a recoverable error.

| Failure | Response |
|---------|----------|
| Camera disconnection | Log warning, retry with exponential backoff, continue |
| RTSP frame read failure | Log warning, reconnect, continue |
| YOLO inference failure | Log error, skip frame, continue |
| Discord webhook failure | Log error, buffer alert, retry up to 3 times |
| Config file missing | Log error, exit with clear message |
| Filesystem write failure | Log error, continue without snapshot |

Never silently swallow exceptions. Always log with context (camera name, error type, what was attempted).

---

## 7. Detection & Alert Pipeline

### V1 False-Positive Suppression (all implemented in `detect.py`)

1. **Confidence threshold** — Per-class minimums (default 0.45). Configurable in `config.json`.
2. **Size filter** — `bird` class requires bbox width >= 8% of frame width. Suppresses background chickens.
3. **Zone masking** — Configurable polygon no-alert zone (e.g. coop area). Detections centered inside are suppressed.
4. **Dwell time** — Animal must appear in 3+ consecutive frames before alert fires. One-frame blips are ignored.

### Alert Cooldown (implemented in `alerts.py`)

- Once an alert fires for a class, no repeat for that class for 5 minutes (configurable).
- Failed alerts are buffered and retried. Dropped after 3 failures.

### YOLO Limitations (v1 — known, documented)

- COCO-80 only: `bird`, `cat`, `dog`, `bear` are the predator classes. No hawk, fox, raccoon, or deer.
- `bird` fires on chickens too. The size filter mitigates but does not eliminate this.
- Custom model for hawk/fox/chicken distinction is a v2 goal.

---

## 8. Config

All runtime behavior is controlled by `config.json`. **Never hardcode** values that belong in config.

Key sections:
- `cameras[]` — IP, credentials, ONVIF port, type (ptz/fixed)
- `discovery` — Rescan interval
- `detection` — Model path, thresholds, predator/ignore classes, size filter, dwell frames, zone masking
- `alerts` — Discord webhook URL, snapshot toggle, cooldown
- `storage` — Events directory, retention days, what to save
- `logging` — Level, log file path

When adding a new tunable parameter: add it to `config.example.json` with a sensible default, read it in the relevant module's `__init__`, and document it in this file.

---

## 9. Dependencies

All dependencies live in `requirements.txt`. Do not add packages without updating this file.

| Package | Purpose |
|---------|---------|
| `opencv-python` | RTSP capture, frame processing, image encoding |
| `ultralytics` | YOLOv8 model loading and inference |
| `onvif-zeep` | ONVIF camera discovery and control |
| `requests` | Discord webhook HTTP posts |
| `Pillow` | Image format conversion and saving |
| `fastapi` | Dashboard web API and static file serving |
| `uvicorn` | ASGI server for FastAPI dashboard |
| `python-multipart` | Form/file upload support for FastAPI |

No cloud SDKs. No databases. No ORMs.

---

## 10. Platform

- **OS:** macOS (Apple Silicon)
- **Python:** 3.13 (Homebrew)
- **GPU:** MPS (Metal Performance Shaders) — used by YOLOv8 automatically on Apple Silicon
- **Network:** Camera and Mac Mini on same local WiFi/Ethernet

Commands:
```bash
# Setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run
python guardian.py

# Run with debug logging
python guardian.py --debug

# Run with custom config path
python guardian.py --config /path/to/config.json
```

---

## 11. Workflow

1. **Read first** — Study `PLAN.md`, `CLAUDE.md`, and existing modules before changing anything.
2. **Plan** — Get user approval before architectural changes. For significant work, create a plan doc.
3. **Implement** — Follow existing patterns. Match the style of adjacent code.
4. **Verify** — Test with real camera streams when available. No mock/simulated verification.
5. **Document** — Update `CHANGELOG.md` (SemVer, what/why/how, author name). Update this file if standards change.

---

## 12. What NOT To Do

- Don't add external/hosted web services — the dashboard is local-only
- Don't add cloud APIs — all detection runs locally
- Don't add a database — JSON logs and filesystem only
- Don't add new dependencies without justification and `requirements.txt` update
- Don't create empty placeholder files — every file ships with real code
- Don't ship stubs, mocks, simulated data, or TODO scaffolding
- Don't over-abstract — if a function is used in one place, don't make it a class
- Don't commit `config.json`, API keys, or credentials (`.gitignore` handles this)

---

## 13. Prohibited Actions

- Never push directly to `main` without review
- Never commit secrets, API keys, webhook URLs, or camera passwords
- Never add comment headers to JSON or other non-comment formats
- Never guess at library behavior — check documentation or source first
- Never ship placeholder or stub code
- Never give time estimates or premature celebration
- Never take shortcuts that compromise reliability

---

## 14. Future Scope (Not V1 — Don't Build These Yet)

- Second camera (Reolink Lumus Pro) with coordinated siren trigger
- Custom YOLO model trained to distinguish hawk/fox/chicken
- Time-lapse compilation of daily activity
- Weekly predator report
- Integration with farm website diary
- Solar cameras for the pasture

These are documented in `PLAN.md` under "Future Expansion." Do not implement any of them without explicit user approval.

---

**Final reminder:** This is a small hobby project protecting real chickens. Quality matters more than speed. Think before you code, reuse what exists, and keep it simple.
