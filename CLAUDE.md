# CLAUDE.md — Farm Guardian

This file provides guidance to AI coding agents working in this repository.

## Project

Farm Guardian — a Python service that watches Reolink security cameras via ONVIF/RTSP, detects predator animals using YOLOv8, and sends Discord alerts. Runs on a Mac Mini M4 Pro (64GB) on the same local network as the cameras.

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

**Config:** `config.json` (copied from `config.example.json`). Contains camera IPs, Discord webhook URL, detection thresholds, alert settings.

## Coding Standards

### File Headers
Every Python file must start with:
```python
# Author: {Model Name}
# Date: {DD-Month-YYYY}
# PURPOSE: Verbose description of functionality, integration points, dependencies
# SRP/DRY check: Pass/Fail — did you verify existing functionality before creating this?
```

### Core Principles
- **SRP** — every class/function/module has exactly one reason to change
- **DRY** — search before creating; reuse existing utilities and patterns
- **No placeholders** — ship real implementations only. No stubs, mocks, or fake data.
- **No AI slop** — no unnecessary abstractions, no over-engineered class hierarchies for a 6-file project
- **Production quality** — error handling, logging, graceful shutdown

### Code Style
- Python 3.13 target
- Type hints on all function signatures
- Docstrings on all public functions
- Use `logging` module, not `print()`
- Constants at module level, UPPER_SNAKE_CASE
- Use pathlib for file paths

### Error Handling
- Camera disconnection → log warning, retry with backoff, don't crash
- YOLO inference failure → log error, skip frame, continue
- Discord API failure → log error, buffer alert, retry
- Never silently swallow exceptions

### What NOT To Do
- Don't add a web UI — this is a background service
- Don't add cloud APIs for detection — everything runs locally
- Don't add a database — JSON logs and filesystem storage only
- Don't over-abstract — this has 6 modules, not 60
- Don't create empty placeholder files — every file ships with real code
- Don't add dependencies that aren't in requirements.txt

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
