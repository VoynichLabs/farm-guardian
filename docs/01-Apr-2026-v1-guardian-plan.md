# Guardian System v1 — Farm Security Camera Software

**Author:** Bubba (Claude Opus 4.6)
**Date:** 01-April-2026
**Status:** Plan — Awaiting sub-agent assignment
**Hardware ETA:** ~03-April-2026

---

## Context

The boss is losing chickens to daytime predators (hawks, foxes) at the Hampton CT property. He ordered a **Reolink E1 Outdoor Pro** (4K WiFi PTZ camera with spotlight and auto-tracking) — arriving in ~2 days. It mounts on the side of the house facing the yard where kills happen.

The camera connects to the home WiFi network and exposes a video stream via ONVIF (an open standard that lets any software pull the camera's video without needing the manufacturer's app). The Mac Mini sits on the same network.

## Goal

Build a Python service that runs on the Mac Mini (M4 Pro, 64GB) and:

1. **Discovers** Reolink cameras on the local network automatically
2. **Pulls live video** from each camera via ONVIF/RTSP
3. **Detects animals** in the frame using a lightweight vision model (YOLO or similar — runs locally on the M4 Pro, no cloud)
4. **Sends alerts** to Discord (#farm-2026 channel) when a predator-type animal is detected
5. **Logs events** with timestamps and snapshots to disk

## What The Camera Already Does (No Software Needed)

- Spotlight turns on when motion is detected
- Camera physically rotates to track the moving object
- Records to local microSD card (if inserted)
- Accessible via Reolink app on phone

## What Our Software Adds

- **Intelligent classification** — the camera knows "something moved," our software knows "that's a hawk vs. a chicken vs. a person"
- **Discord alerts** — boss gets a notification with a snapshot on his phone through Discord, not just the Reolink app
- **Event history** — searchable log of what was detected, when, with images
- **Future: coordinated response** — when we add the Lumus Pro (has siren), the software could trigger the siren on a different camera when the PTZ camera spots something

## Technical Architecture

```
┌──────────────┐     WiFi      ┌──────────────┐
│  E1 Outdoor  │◄─────────────►│   Home       │
│  Pro (PTZ)   │   ONVIF/RTSP  │   Router     │
└──────────────┘               └──────┬───────┘
                                      │ Ethernet
                               ┌──────┴───────┐
                               │   Mac Mini   │
                               │  (guardian)  │
                               │              │
                               │ ┌──────────┐ │
                               │ │ Camera   │ │
                               │ │ Discovery│ │
                               │ └────┬─────┘ │
                               │      │       │
                               │ ┌────▼─────┐ │
                               │ │ Frame    │ │
                               │ │ Capture  │ │
                               │ └────┬─────┘ │
                               │      │       │
                               │ ┌────▼─────┐ │
                               │ │ Animal   │ │
                               │ │ Detect   │ │
                               │ │ (YOLO)   │ │
                               │ └────┬─────┘ │
                               │      │       │
                               │ ┌────▼─────┐ │
                               │ │ Alert    │ │
                               │ │ Manager  │ │
                               │ └──────────┘ │
                               └──────────────┘
                                      │
                                Discord API
                                      │
                               ┌──────▼───────┐
                               │  #farm-2026  │
                               │  channel     │
                               └──────────────┘
```

## Modules To Build

### 1. Camera Discovery (`discovery.py`)
- Scan local network for ONVIF-compatible cameras
- Store camera IPs, credentials, stream URLs
- Re-scan periodically in case cameras reconnect

### 2. Frame Capture (`capture.py`)
- Connect to camera RTSP stream (the video feed URL)
- Grab frames at a configurable interval (e.g., 1 frame per second — not every frame, that would be wasteful)
- Buffer recent frames for event context

### 3. Animal Detection (`detect.py`)
- Load a YOLO model (runs on the Mac Mini's CPU/GPU — no cloud needed)
- Classify detected objects: person, vehicle, chicken, hawk, fox, raccoon, cat, dog, deer, etc.
- Confidence threshold to avoid false positives
- Tag detections with bounding boxes

### 4. Alert Manager (`alerts.py`)
- When a predator-class animal is detected:
  - Save snapshot to `projects/guardian/events/`
  - Post to Discord #farm-2026 with image + what was detected + timestamp
  - Rate-limit alerts (don't spam — one alert per event, not one per frame)
- Configurable alert classes (which animals trigger alerts vs. just log)

### 5. Event Logger (`logger.py`)
- Write structured JSON event log
- Each event: timestamp, camera, detection class, confidence, snapshot path
- Daily rotation, searchable

### 6. Main Service (`guardian.py`)
- Orchestrates all modules
- Runs as a background process on the Mac Mini
- Configurable via a simple JSON config file
- Graceful start/stop

## Design Decisions (resolved 01-Apr-2026)

### 1. YOLO bird classification — honest v1 limitations

**COCO-80 animal classes available:** bird, cat, dog, horse, sheep, cow, elephant, bear, zebra, giraffe. That's it. No hawk, no fox, no raccoon, no deer — those don't exist as COCO classes.

**V1 alert classes:** `bird`, `cat`, `dog`, `bear` — everything else is out of scope without a custom model.

**The chicken problem (known, unsolved in v1):** `bird` fires on chickens too. COCO makes no distinction between a hawk and a hen. V1 will be noisy. Mark needs to know this upfront — alerts will fire when chickens move through frame unless suppressed by the size filter below. The custom model that distinguishes hawk/fox/chicken is v2.

**V1 mitigation — size filter:** A hawk diving at the yard appears much larger in frame than a chicken walking past the camera at normal distance. For `bird` class only: require minimum bounding box area of 8% of frame width before alerting. This suppresses most background chicken detections while catching large raptors in the foreground. Threshold is tunable in `config.json` and needs calibration once the camera is live.

**False-positive suppression strategy (v1):**
- **Zone masking** — configurable polygon in `config.json` defines a "no-alert zone" covering the coop area. Detections whose bounding box center falls inside the zone are suppressed.
- **Minimum dwell time** — an animal must be detected in 3+ consecutive frames before an alert fires. One-frame blips don't trigger.
- **Confidence threshold** — minimum 0.45 confidence. Tunable per class in `config.json`.
- **Alert cooldown** — once an alert fires for a given animal class, no repeat alert for that class for 5 minutes.

### 3. ONVIF verification (pre-build step)
The Reolink E1 Outdoor Pro *should* support ONVIF but Reolink's implementation is inconsistent across models. **Before building the ONVIF event trigger path:** verify the camera actually sends `MotionAlarm` events via ONVIF when it arrives (~Apr 3). Test with `onvif-zeep` or the ONVIF Device Manager app. If ONVIF events don't work on this unit, fallback is Reolink's own HTTP API (it has a motion detection polling endpoint) or OpenCV frame differencing. Do not build the full ONVIF path without first confirming it works on this specific camera.

### 2. Motion trigger
**ONVIF motion events** are the primary trigger — the Reolink E1 Outdoor Pro supports ONVIF event subscriptions. The camera pushes a `MotionAlarm` event when its onboard motion detection fires; we wake up and grab frames only then. Fallback: if ONVIF events are unreliable, OpenCV frame differencing (background subtraction via MOG2) activates continuous 1 FPS processing. Motion source is configurable in `config.json`.

### 3. Mac Mini resource usage
Ultralytics YOLOv8 on Apple Silicon uses **MPS (Metal Performance Shaders)** by default — not the Neural Engine. YOLOv8n (nano) at 640px input on M4 Pro MPS: ~8ms per inference. At 1 FPS on motion events only, sustained CPU/GPU load is negligible (<5%). We downscale 4K→1080p before inference. A quick benchmark will be run during scaffold phase to confirm before going live.

### 4. Discord alerts
**Discord webhook** — standalone, no OpenClaw dependency. Webhook URL stored in `config.json` (not in code). The guardian service runs independently of whether the OpenClaw gateway is up. Alert format: embed with snapshot image, detection class, confidence, timestamp, camera name.

---

## Tech Stack

- **Python 3.13** (Homebrew)
- **OpenCV** — frame capture from RTSP streams + optional frame-diff motion fallback
- **ultralytics/YOLOv8n** — animal detection (MPS on Apple Silicon, no cloud)
- **onvif-zeep** — ONVIF camera discovery + motion event subscription
- **Discord webhook** — alerts (standalone, no OpenClaw dependency)

## File Structure

```
projects/guardian/
├── guardian.py          ← main service entry point
├── config.json          ← camera IPs, alert settings, detection thresholds
├── discovery.py         ← ONVIF camera scanner
├── capture.py           ← RTSP frame grabber
├── detect.py            ← YOLO animal detection
├── alerts.py            ← Discord alert manager
├── logger.py            ← event logging
├── events/              ← snapshot images + event logs
│   └── YYYY-MM-DD/      ← daily subdirectories
├── models/              ← YOLO model weights
└── requirements.txt     ← pip dependencies
```

## What We Need Before The Camera Arrives

1. **Python venv** set up at `projects/guardian/` with dependencies installed
2. **YOLO model downloaded** — YOLOv8 can detect ~80 object classes including birds, cats, dogs, bears out of the box. Custom training for "hawk" and "fox" specifically can come later.
3. **Basic scaffolding** — the module files created with the structure above
4. **Discord webhook** configured for #farm-2026 alerts

## What We Do When The Camera Arrives (~03-April-2026)

1. Power it up, connect to WiFi via Reolink app
2. Find its IP address on the network
3. Test ONVIF/RTSP connection from Mac Mini
4. Point guardian service at it
5. Start detecting

## Constraints

- Everything runs locally — no cloud APIs for detection
- No subscription services
- Must not interfere with the Reolink app (camera supports multiple simultaneous viewers)
- Low CPU usage when idle — only process frames when motion is flagged
- Alerts must include useful information, not just "motion detected"

## Future Expansion (Not v1)

- Second camera (Lumus Pro at coop) with coordinated siren trigger
- Time-lapse compilation of daily activity
- Weekly predator report
- Integration with farm-2026 website diary
- Solar cameras for the pasture

---

**Assigned to:** Sub-agent (Bubba to spawn when approved)
**Code location:** `~/bubba-workspace/projects/guardian/`
**Coordination:** This document in `swarm-coordination/plans/`
