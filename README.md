# Farm Guardian

**Intelligent farm security for Hampton, CT -- predator detection, automated deterrence, and real-time alerts.**

Farm Guardian is a Python service that runs on a Mac Mini (M4 Pro, 64GB) and watches Reolink security cameras on the local network. It detects predator animals using YOLOv8 + a local vision model for species-level identification, automatically fires camera deterrents (spotlight, siren, audio alarm), tracks animal visits over time, and sends Discord alerts with snapshots. A local web dashboard provides live camera feeds, PTZ controls, daily intelligence reports, and full configuration.

No cloud services. No subscriptions. No data leaves the local network except Discord notifications.

## Why

Hawks and ground predators (fox, raccoon, bobcat, coyote) are killing chickens at a 13-acre rural property in eastern Connecticut. The cameras have built-in spotlight and motion detection, but they can't tell a chicken from a hawk. This software adds that intelligence -- and automates the response.

## Hardware

| Component | Model | Purpose |
|-----------|-------|---------|
| Camera | Reolink E1 Outdoor Pro | 4K, WiFi 6, PTZ (355 pan), spotlight, siren, auto-tracking |
| Processing | Mac Mini M4 Pro (64GB) | YOLO inference, vision model, all local AI |
| Vision Model | GLM-4.6V-Flash via LM Studio | Species refinement (hawk vs chicken) at localhost:1234 |

## Architecture (v2)

```
Reolink E1 Outdoor Pro (WiFi, RTSP)
         |
         +--[RTSP stream]--> capture.py --> detect.py --> vision.py --> tracker.py
         |                                     |             |              |
         |                                     |      (YOLO: "bird"?    (group into
         |                                     |       GLM: hawk or      visits, track
         |                                     |       chicken?)         outcomes)
         |                                     v             v              v
         |                                database.py <-- logger.py
         |                                     |
         |                                     +---> alerts.py --> Discord
         |                                     +---> deterrent.py --> camera_control.py --+
         |                                     +---> reports.py --> data/exports/         |
         |                                     +---> api.py --> LLM tools (local)        |
         |                                                                               |
         <--[Reolink HTTP API]---- camera_control.py <-------------------------------+
              (PTZ, spotlight,          |
               siren, snapshot)         +---> dashboard.py --> Browser (http://macmini:6530)
```

## Quick Start

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Download YOLO model (first run only)
python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

# Configure
cp config.example.json config.json
# Edit config.json: camera IP/password, Discord webhook, eBird API key

# Run
python guardian.py

# Run with debug logging
python guardian.py --debug
```

Dashboard available at `http://macmini:6530` (or the Mac Mini's IP on your local network).

## Project Structure

```
farm-guardian/
+-- guardian.py             <-- Entry point: orchestrates all modules
+-- discovery.py            <-- ONVIF camera scanner + registration
+-- capture.py              <-- RTSP frame grabber (4K -> 1080p, reconnection)
+-- detect.py               <-- YOLOv8 inference + false-positive suppression
+-- vision.py               <-- GLM vision model species refinement
+-- tracker.py              <-- Animal visit tracking + pattern analysis
+-- camera_control.py       <-- PTZ, spotlight, siren via reolink_aio
+-- deterrent.py            <-- Automated deterrent response engine
+-- ebird.py                <-- eBird API: regional raptor early warning
+-- alerts.py               <-- Discord webhook alerts with rate limiting
+-- logger.py               <-- Event logging (SQLite + legacy JSONL)
+-- database.py             <-- SQLite abstraction layer (8 tables)
+-- reports.py              <-- Daily intelligence reports (JSON + Markdown)
+-- api.py                  <-- REST API at /api/v1/ for LLM tool queries
+-- dashboard.py            <-- FastAPI web dashboard (local)
+-- static/
|   +-- index.html          <-- Dashboard UI (Tailwind CSS, vanilla JS)
|   +-- app.js              <-- Dashboard frontend logic
+-- config.example.json     <-- Template config (copy to config.json)
+-- requirements.txt
+-- data/
|   +-- guardian.db          <-- SQLite database (created at runtime)
|   +-- exports/             <-- Daily reports (JSON + Markdown)
|   +-- backups/             <-- Daily DB backups
+-- events/                  <-- Legacy JSONL logs + snapshots
+-- PLAN_V2.md              <-- Full v2 architecture specification
+-- CHANGELOG.md
```

## Key Features

### Detection Pipeline
- **YOLOv8** detects animals in every frame (~1fps)
- **GLM vision model** refines ambiguous classes: "bird" becomes "hawk" or "chicken"
- **False-positive suppression**: minimum bounding box size, dwell time, confidence thresholds, zone masking

### Automated Deterrence (Phase 3)
- **4 escalation levels**: log only, spotlight, spotlight + audio, spotlight + siren + audio
- **Per-species rules**: hawks get spotlight + audio alarm, bobcats get the full siren
- **Cooldowns**: 5 min between activations per species (neighbor-friendly)
- **Effectiveness tracking**: monitors if animal leaves within 60s of deterrent

### PTZ Patrol
- **5 preset positions**: yard-center, coop-approach, fence-line, sky-watch, driveway
- **Auto-patrol**: cycles through presets with configurable dwell times
- **Pause-on-predator**: patrol halts during active deterrence, resumes after

### eBird Early Warning
- Polls Cornell Lab's eBird API every 30 min during hawk hours (8am-4pm)
- Alerts when HIGH/MEDIUM threat raptors reported within 15km
- Gives 15-60 minutes advance warning before hawks arrive on camera

### Intelligence Reports (Phase 4)
- Daily summaries: species breakdown, predator visits, deterrent success rates
- Exported as JSON + Markdown to `data/exports/`
- Hourly activity heatmaps and 7-day trend comparisons
- Queryable via REST API at `/api/v1/`

### REST API for LLM Tools
```
GET  /api/v1/status                      Service health
GET  /api/v1/summary/today               Today's report
GET  /api/v1/summary/{date}              Report for any date
GET  /api/v1/detections?class=hawk&days=7  Query detections
GET  /api/v1/tracks?predator=true&days=30  Query animal visits
GET  /api/v1/patterns/{class_name}       Species activity patterns
GET  /api/v1/deterrents/effectiveness    Deterrent success rates
GET  /api/v1/ebird/recent               Regional raptor sightings
POST /api/v1/cameras/{id}/ptz           Control PTZ
POST /api/v1/cameras/{id}/spotlight     Toggle spotlight
POST /api/v1/cameras/{id}/siren         Trigger siren
```

### Dashboard
- **Live MJPEG feeds** from all connected cameras
- **PTZ controls**: directional pad, zoom, preset buttons, spotlight/siren
- **Detection timeline**: real-time detection table with predator highlighting
- **Reports viewer**: daily summaries with species bar charts, hourly histograms, predator visit tables
- **Alert history**: every Discord alert with delivery status
- **Settings**: tune all detection thresholds, alert config, zone masking

## Tech Stack

- **Python 3.13** (Homebrew)
- **OpenCV** -- RTSP stream capture and frame processing
- **ultralytics/YOLOv8** -- object detection (MPS acceleration on Apple Silicon)
- **reolink_aio** -- Reolink camera control (PTZ, spotlight, siren)
- **onvif-zeep** -- ONVIF camera discovery
- **FastAPI + Uvicorn** -- web dashboard + REST API
- **SQLite** -- structured detection/track/alert storage (WAL mode)
- **Discord webhook** -- alert delivery
- **eBird API** -- regional raptor early warning

## Configuration

All config lives in `config.json` (copy from `config.example.json`). Key sections:

| Section | Purpose |
|---------|---------|
| `cameras` | Camera IPs, credentials, type (ptz/fixed) |
| `detection` | YOLO thresholds, predator classes, zone masking |
| `vision` | GLM vision model endpoint and trigger classes |
| `deterrent` | Per-species response rules, escalation levels, cooldowns |
| `ptz` | Patrol presets, dwell times, zoom levels |
| `ebird` | API key, poll interval, threat levels |
| `alerts` | Discord webhook URL, cooldown, snapshot toggle |
| `reports` | Daily summary time, export formats |
| `database` | SQLite path, backup schedule, retention |
| `dashboard` | Host, port, enable/disable |

## Implementation Status

| Phase | Status | What |
|-------|--------|------|
| 1. Foundation | Done | Core detection, alerting, logging, dashboard |
| 2. Database + Vision + Tracking | Done | SQLite, GLM species ID, visit tracking |
| 3. Camera Control + Deterrence | Done | PTZ patrol, spotlight/siren automation, eBird |
| 4. Intelligence + Reporting | Done | Daily reports, REST API, pattern analysis |
| 5. Web Hosting | Planned | PostgreSQL sync, Railway deploy, farm.markbarney.net |

## License

Private -- VoynichLabs internal project.
