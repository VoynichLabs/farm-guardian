# Changelog

All notable changes to Farm Guardian are documented here. Follows [Semantic Versioning](https://semver.org/).

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
