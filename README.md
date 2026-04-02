# Farm Guardian 🦅🚨

**Intelligent farm security for Hampton, CT — predator detection, deterrent automation, and real-time alerts.**

Farm Guardian is a Python service that runs on a Mac Mini (M4 Pro, 64GB) and watches Reolink security cameras on the local network. When it spots a predator approaching the chicken coop or yard, it sends a Discord alert with a snapshot and logs the event. A local web dashboard lets you monitor cameras, browse detections, and control everything from your phone or browser.

No cloud services. No subscriptions. No data leaves the local network except Discord notifications.

## Why

Hawks and ground predators (fox, raccoon, coyote) are killing chickens during the day at a 13-acre rural property in eastern Connecticut. The cameras have built-in spotlight and motion detection, but they can't tell a chicken from a hawk. This software adds that intelligence.

## Hardware

| Camera | Model | Placement | Features |
|--------|-------|-----------|----------|
| Reolink E1 Outdoor Pro | B0C27ZY3R5 | Side of house | 4K, WiFi, PTZ (rotates 355°), spotlight, auto-tracking |
| Reolink Lumus Pro (planned) | B0DCZGNQXB | Chicken coop | 4K, WiFi, fixed, spotlight + siren |

## Architecture

```
Camera (WiFi) → Mac Mini (Guardian service) → Discord alerts
                     │
              YOLO detection (local)
                     │
              Event log + snapshots
                     │
              Web dashboard (http://macmini:8080)
```

1. Camera streams video over WiFi via ONVIF/RTSP
2. Guardian grabs frames at ~1fps
3. YOLOv8 identifies animals in each frame (runs locally on the M4 Pro)
4. Predator detected → Discord alert with snapshot to #farm-2026
5. Events logged with timestamps and images
6. Dashboard shows live feeds, detection timeline, and full controls

## Quick Start

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Download YOLO model (first run only)
python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

# Configure cameras
cp config.example.json config.json
# Edit config.json with your camera IPs

# Run
python guardian.py
```

## Project Structure

```
farm-guardian/
├── README.md
├── CLAUDE.md              ← Agent coding instructions
├── PLAN.md                ← Detailed architecture + module specs
├── CODING_STANDARDS.md    ← Mandatory coding standards
├── requirements.txt
├── config.example.json    ← Template config (copy to config.json)
├── guardian.py             ← Main service entry point
├── discovery.py            ← ONVIF camera scanner
├── capture.py              ← RTSP frame grabber
├── detect.py               ← YOLO animal detection
├── alerts.py               ← Discord alert manager
├── logger.py               ← Event logging
├── dashboard.py            ← Web dashboard (FastAPI, local only)
├── static/
│   ├── index.html          ← Dashboard UI
│   └── app.js              ← Dashboard frontend logic
├── models/                 ← YOLO model weights (gitignored)
└── events/                 ← Snapshots + logs (gitignored)
```

## Tech Stack

- **Python 3.13** 
- **OpenCV** — RTSP video stream capture
- **ultralytics/YOLOv8** — object detection (animals, people, vehicles)
- **onvif-zeep** — ONVIF camera discovery and control
- **Discord webhook** — alert delivery
- **FastAPI + Uvicorn** — local web dashboard

## Dashboard

Open `http://macmini.local:8080` (or the Mac Mini's IP) on any device on the local network.

- **Live camera feeds** — MJPEG streams from all connected cameras
- **Detection timeline** — what was detected, when, with confidence scores
- **Alert history** — every Discord alert sent, with status
- **Camera controls** — start/stop capture, rescan network for new cameras
- **Settings** — tune detection thresholds, manage predator classes, configure alerts, edit no-alert zones
- **Test alert** — verify Discord webhook is working

## Alerts

When a predator-class animal is detected:
- Snapshot saved to `events/YYYY-MM-DD/`
- Discord message posted to #farm-2026 with image, species, confidence, timestamp
- Rate-limited: one alert per event, not one per frame

## License

Private — VoynichLabs internal project.
