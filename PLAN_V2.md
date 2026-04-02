# Farm Guardian v2 — Comprehensive System Plan

**Author:** Cascade (Claude Opus 4.6)
**Date:** 02-April-2026
**Status:** Plan — Awaiting review by secondary assistant
**For:** Mark Barney, Hampton CT

---

## 1. Executive Summary

Farm Guardian is an intelligent farm security system that protects chickens from predators at a 13-acre rural property in eastern Connecticut. The system runs entirely on a Mac Mini M4 Pro (64GB) connected to a Reolink E1 Outdoor Pro camera on the local WiFi network.

**What v1 does:** Detects animals via YOLO, sends Discord alerts, logs events to JSONL files, serves a local web dashboard.

**What v2 adds:**
- **SQLite database** for structured tracking, queries, and LLM consumption
- **Animal visit tracking** — groups detections into visits with duration, behavior, and outcomes
- **Active deterrence** — programmatic control of camera spotlight, siren, and PTZ via Reolink API
- **PTZ patrol automation** — camera cycles through preset monitoring positions
- **Daily intelligence reports** — natural language summaries for LLM assistants (OpenClaw)
- **Web-hostable architecture** — local-first, but deployable to `farm.markbarney.net` when ready
- **REST API** for LLM tool access — structured queries over detection history and patterns

---

## 2. Hardware Profile

### Reolink E1 Outdoor Pro (Primary — arriving ~03-April-2026)

| Feature | Specification |
|---------|--------------|
| **Resolution** | 4K (3840×2160), 8MP |
| **Lens** | 3× optical zoom (no detail loss) |
| **PTZ** | 355° pan, 50° tilt, up to 64 preset positions |
| **WiFi** | Dual-band 2.4/5GHz, Wi-Fi 6 (802.11ax) |
| **Night Vision** | IR LEDs (40ft) + color night vision via spotlight |
| **Spotlight** | Warm white LED, adjustable brightness, Time Mode / Night Smart Mode |
| **Siren** | Built-in, customizable alarm sounds, API-triggerable |
| **Audio** | Two-way with built-in microphone + speaker |
| **AI (on-camera)** | Person / vehicle / animal detection (we override with YOLO) |
| **Auto-tracking** | Built-in subject tracking, customizable per detection type |
| **Storage** | microSD up to 512GB, NVR, FTP, NAS compatible |
| **Protocols** | ONVIF, RTSP, Reolink HTTP API |
| **Weather** | IP65 weatherproof, outdoor rated |
| **Placement** | Side of house, facing yard where kills happen |

### Reolink API Access Methods

The E1 Outdoor Pro exposes three control interfaces:

1. **ONVIF** — discovery, RTSP stream URLs, basic PTZ, event subscription
2. **Reolink HTTP API** — full control: PTZ, spotlight, siren, audio alarm, AI state, snapshots, recording
3. **`reolink_aio` Python library** — async wrapper around the HTTP API; supports spotlight (`set_spotlight`), siren (`set_siren`), PTZ, ONVIF event subscription via TCP push

**We will use `reolink_aio`** as the primary control library. It's the same library that powers the official Home Assistant Reolink integration (1,000+ commits, actively maintained). For RTSP frame capture we continue using OpenCV directly.

### Key Reolink HTTP API Commands (reference)

```
POST /api.cgi?cmd=Login            → Authenticate, get token
POST /api.cgi?cmd=PtzCtrl          → Pan, tilt, zoom, go to preset, start/stop patrol
POST /api.cgi?cmd=SetWhiteLed      → Spotlight on/off, brightness (0-100)
POST /api.cgi?cmd=AudioAlarmPlay   → Trigger siren/audio alarm
POST /api.cgi?cmd=GetAiState       → Read camera's built-in AI detection state
GET  /cgi-bin/api.cgi?cmd=Snap     → Capture JPEG snapshot at current position
POST /api.cgi?cmd=SetPtzPreset     → Save current position as a named preset
POST /api.cgi?cmd=GetPtzPreset     → List all saved presets
POST /api.cgi?cmd=SetAiCfg         → Configure auto-tracking behavior
```

### Mac Mini M4 Pro (Processing Hub)

| Feature | Specification |
|---------|--------------|
| **CPU** | 14-core (10P + 4E) |
| **GPU** | 20-core (Metal/MPS — used by YOLO automatically) |
| **RAM** | 64GB unified memory |
| **OS** | macOS 26.3 |
| **Python** | 3.13 (Homebrew) |
| **Network** | Same local WiFi as camera |

---

## 3. The Problem We're Solving (More Precisely)

Hawks and ground predators (fox, raccoon, coyote, possibly fisher cat) are killing chickens during **daytime hours** at the Hampton property. The camera already has motion detection and a spotlight, but it can't tell a chicken from a hawk.

**What we need that the camera alone can't do:**

1. **Species-level classification** — "that's a red-tailed hawk, not a chicken"
2. **Intelligent alerting** — alert on predators only, not every motion event
3. **Active deterrence** — automatically trigger spotlight + siren when a predator is detected (the camera can do this on any motion, but we want it only for predators)
4. **Tracking over time** — which species visit, when, how often, do deterrents work?
5. **Pattern intelligence** — "hawks come between 10am-2pm from the northeast" — feed this to LLM assistants for analysis
6. **Remote monitoring** — dashboard accessible from phone/laptop, eventually from `farm.markbarney.net`

---

## 4. Database Design (SQLite)

### Why SQLite

- Zero configuration, single-file database
- Perfect for a single-writer local service
- Schema designed to be PostgreSQL-compatible for future web hosting migration
- Python `sqlite3` is in the standard library — no additional dependency
- Easily backed up (copy one file), easily queried by LLM tools

### Schema

```sql
-- ============================================================
-- cameras: registered camera hardware
-- ============================================================
CREATE TABLE cameras (
    id              TEXT PRIMARY KEY,          -- "e1-outdoor-yard"
    name            TEXT NOT NULL,             -- "House Yard Camera"
    model           TEXT NOT NULL,             -- "Reolink E1 Outdoor Pro"
    ip              TEXT,
    rtsp_url        TEXT,
    type            TEXT NOT NULL DEFAULT 'ptz',  -- "ptz" | "fixed"
    location        TEXT,                      -- "house-side-facing-yard"
    capabilities    TEXT NOT NULL DEFAULT '[]', -- JSON: ["ptz","spotlight","siren","audio","auto_track"]
    status          TEXT NOT NULL DEFAULT 'offline',
    last_seen_at    TEXT,                      -- ISO 8601
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- detections: every individual YOLO detection
-- ============================================================
CREATE TABLE detections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id       TEXT NOT NULL REFERENCES cameras(id),
    detected_at     TEXT NOT NULL,             -- ISO 8601, millisecond precision
    class_name      TEXT NOT NULL,             -- "hawk", "fox", "chicken", "person", "cat"
    confidence      REAL NOT NULL,
    bbox_x1         REAL NOT NULL,             -- normalized 0.0-1.0
    bbox_y1         REAL NOT NULL,
    bbox_x2         REAL NOT NULL,
    bbox_y2         REAL NOT NULL,
    bbox_area_pct   REAL,                      -- bounding box as % of frame area
    is_predator     INTEGER NOT NULL DEFAULT 0,
    track_id        INTEGER REFERENCES tracks(id),
    snapshot_path   TEXT,                      -- relative path to saved image
    model_name      TEXT DEFAULT 'yolov8n',
    suppressed      INTEGER NOT NULL DEFAULT 0,
    suppression_reason TEXT,                   -- "zone" | "size" | "dwell" | "cooldown" | NULL
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_detections_camera_time ON detections(camera_id, detected_at);
CREATE INDEX idx_detections_class ON detections(class_name);
CREATE INDEX idx_detections_track ON detections(track_id);
CREATE INDEX idx_detections_predator ON detections(is_predator, detected_at);

-- ============================================================
-- tracks: animal visits (groups of related detections)
-- ============================================================
CREATE TABLE tracks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id       TEXT NOT NULL REFERENCES cameras(id),
    class_name      TEXT NOT NULL,
    first_seen_at   TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL,
    duration_sec    REAL,
    detection_count INTEGER NOT NULL DEFAULT 0,
    max_confidence  REAL,
    avg_confidence  REAL,
    is_predator     INTEGER NOT NULL DEFAULT 0,
    outcome         TEXT,                      -- "left" | "deterred" | "attack" | "unknown"
    deterrent_used  TEXT,                      -- JSON: ["spotlight","siren"] or NULL
    ptz_position    TEXT,                      -- JSON: {"pan":180,"tilt":45,"zoom":1} at first detection
    notes           TEXT,                      -- for LLM annotations or manual notes
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_tracks_camera_time ON tracks(camera_id, first_seen_at);
CREATE INDEX idx_tracks_predator ON tracks(is_predator, first_seen_at);
CREATE INDEX idx_tracks_class ON tracks(class_name);

-- ============================================================
-- alerts: every notification sent (Discord, siren, spotlight)
-- ============================================================
CREATE TABLE alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id        INTEGER REFERENCES tracks(id),
    camera_id       TEXT NOT NULL REFERENCES cameras(id),
    alerted_at      TEXT NOT NULL,
    alert_type      TEXT NOT NULL,             -- "discord" | "siren" | "spotlight" | "audio_alarm"
    classes         TEXT NOT NULL,             -- JSON: ["hawk"]
    message         TEXT,
    snapshot_path   TEXT,
    delivered       INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_alerts_time ON alerts(alerted_at);
CREATE INDEX idx_alerts_track ON alerts(track_id);

-- ============================================================
-- deterrent_actions: every time we activated a deterrent
-- ============================================================
CREATE TABLE deterrent_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id        INTEGER REFERENCES tracks(id),
    camera_id       TEXT NOT NULL REFERENCES cameras(id),
    acted_at        TEXT NOT NULL,
    action_type     TEXT NOT NULL,             -- "spotlight_on" | "spotlight_off" | "siren" | "audio_alarm" | "ptz_track"
    duration_sec    REAL,
    result          TEXT,                      -- "animal_left" | "no_effect" | "unknown"
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- ptz_presets: saved camera positions for patrol
-- ============================================================
CREATE TABLE ptz_presets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id       TEXT NOT NULL REFERENCES cameras(id),
    name            TEXT NOT NULL,             -- "yard-center", "coop-approach", "fence-line"
    pan             REAL NOT NULL,
    tilt            REAL NOT NULL,
    zoom            REAL NOT NULL DEFAULT 1.0,
    description     TEXT,
    is_patrol_stop  INTEGER NOT NULL DEFAULT 0,
    patrol_order    INTEGER,                   -- sequence in patrol route
    dwell_sec       INTEGER NOT NULL DEFAULT 30,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- daily_summaries: aggregated daily stats for LLM consumption
-- ============================================================
CREATE TABLE daily_summaries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_date    TEXT NOT NULL UNIQUE,      -- "2026-04-15"
    total_detections    INTEGER NOT NULL DEFAULT 0,
    predator_detections INTEGER NOT NULL DEFAULT 0,
    unique_species      TEXT,                  -- JSON: ["hawk","fox","chicken"]
    alerts_sent         INTEGER NOT NULL DEFAULT 0,
    deterrents_activated INTEGER NOT NULL DEFAULT 0,
    peak_activity_hour  INTEGER,               -- 0-23
    activity_by_hour    TEXT,                   -- JSON: {"6":0,"7":5,"8":12,...}
    species_counts      TEXT,                   -- JSON: {"hawk":3,"chicken":28,"person":12}
    predator_tracks     TEXT,                   -- JSON: array of track summaries
    deterrent_success_rate REAL,               -- 0.0-1.0
    summary_text        TEXT,                   -- natural language summary for LLMs
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_summaries_date ON daily_summaries(summary_date);
```

### Migration Strategy

- v1 JSONL event logs continue to work as a fallback/archive
- New `database.py` module provides a clean abstraction layer using raw SQL (no ORM)
- All queries are standard SQL — SQLite locally, PostgreSQL when hosted
- Daily backup: copy `guardian.db` to `data/backups/guardian-YYYY-MM-DD.db`

---

## 5. Module Architecture (v2)

### File Structure

```
farm-guardian/
├── guardian.py             ← Entry point — orchestrates all modules
├── discovery.py            ← ONVIF camera scanner + registration
├── capture.py              ← RTSP frame grabber (per-camera threads, 4K→1080p)
├── detect.py               ← YOLOv8 inference + false-positive suppression
├── tracker.py              ← NEW: Animal visit tracking + pattern analysis
├── camera_control.py       ← NEW: PTZ, spotlight, siren via reolink_aio
├── deterrent.py            ← NEW: Automated deterrent response engine
├── alerts.py               ← Discord webhook alerts with rate limiting
├── logger.py               ← Event logging (now writes to DB + legacy JSONL)
├── database.py             ← NEW: SQLite abstraction layer
├── reports.py              ← NEW: Daily summaries + LLM-ready exports
├── dashboard.py            ← FastAPI web dashboard (local + deployable)
├── api.py                  ← NEW: REST API for LLM tools (OpenClaw)
├── static/
│   ├── index.html          ← Dashboard UI (Tailwind CSS, vanilla JS)
│   └── app.js              ← Dashboard frontend logic
├── config.json             ← Runtime config (gitignored)
├── config.example.json
├── requirements.txt
├── data/
│   ├── guardian.db          ← SQLite database
│   ├── snapshots/           ← Detection images by date
│   ├── exports/             ← LLM-ready JSON/Markdown exports
│   └── backups/             ← Daily DB backups
├── events/                  ← Legacy JSONL + snapshots (v1 compat)
└── models/                  ← YOLO weights
```

### Data Flow (v2)

```
Reolink E1 Outdoor Pro (WiFi, RTSP)
         │
         ├──[RTSP stream]──→ capture.py ──→ detect.py ──→ tracker.py
         │                                      │              │
         │                                      │         (group into visits,
         │                                      │          track duration,
         │                                      │          assign outcomes)
         │                                      │              │
         │                                      ▼              ▼
         │                                 database.py ◄── logger.py
         │                                      │
         │                                      ├──→ alerts.py ──→ Discord
         │                                      ├──→ deterrent.py ──→ camera_control.py ──┐
         │                                      ├──→ reports.py ──→ data/exports/         │
         │                                      └──→ api.py ──→ LLM tools (OpenClaw)     │
         │                                                                                │
         ◄──[Reolink HTTP API / reolink_aio]──── camera_control.py ◄──────────────────────┘
              (PTZ move, spotlight on/off,          │
               siren trigger, audio alarm,          └──→ dashboard.py ──→ Browser
               snap, preset management)                  (http://macmini:8080 local)
                                                         (https://farm.markbarney.net future)
```

### New Module Descriptions

#### `database.py` — Data Layer

Single responsibility: all SQLite reads/writes go through this module.

- Thread-safe connection management (one writer, multiple readers)
- Schema creation and migration on startup
- Insert/query methods for each table
- Daily backup routine
- Export utilities (JSON, CSV) for LLM consumption
- No ORM — raw SQL with parameterized queries for portability

#### `tracker.py` — Animal Visit Tracking

Converts a stream of individual detections into meaningful **tracks** (visits).

- **Track creation:** New track starts when a class appears that wasn't in any active track
- **Track merging:** If same class reappears within `track_timeout_seconds` (default 60s), it joins the existing track rather than starting a new one
- **Track completion:** Track ends after `track_timeout_seconds` of no matching detections
- **Metrics calculated:** duration, detection count, max/avg confidence, approach direction (based on bbox movement across frames)
- **Outcome tracking:** after a deterrent fires, monitor whether the animal leaves (detection disappears) or persists
- **Pattern detection:** time-of-day preferences per species, frequency trends

#### `camera_control.py` — Reolink Camera Hardware Control

Uses `reolink_aio` library for async communication with the E1 Outdoor Pro.

- **Authentication:** login/token management with auto-refresh
- **PTZ control:**
  - Continuous move (pan/tilt/zoom)
  - Go to preset by name or index
  - Save current position as preset
  - Start/stop patrol route
  - Get current position
- **Spotlight:**
  - On/off with configurable brightness (0-100)
  - Timed activation (auto-off after N seconds)
- **Siren:**
  - Trigger with configurable duration
  - Custom alarm patterns
- **Audio alarm:** play warning sound through camera speaker
- **Snapshot:** capture JPEG directly from camera (independent of RTSP stream)
- **Status:** query camera health, storage, AI state
- **Event subscription:** TCP push events from camera for real-time motion notifications

#### `deterrent.py` — Automated Response Engine

Decides what deterrent actions to take when a predator is detected.

**Escalation levels:**

| Level | Trigger | Actions | Use Case |
|-------|---------|---------|----------|
| 0 | Any detection | Log only | Passive monitoring, data collection |
| 1 | Low-threat predator | Spotlight on | Cat near coop |
| 2 | Medium-threat predator | Spotlight + audio alarm | Hawk overhead |
| 3 | High-threat predator | Spotlight + siren + audio alarm | Fox/coyote approaching |

**Per-species response rules** (configurable):

```json
{
  "hawk":    { "level": 2, "actions": ["spotlight", "audio_alarm"] },
  "fox":     { "level": 3, "actions": ["spotlight", "siren", "audio_alarm"] },
  "coyote":  { "level": 3, "actions": ["spotlight", "siren", "audio_alarm"] },
  "raccoon": { "level": 2, "actions": ["spotlight", "audio_alarm"] },
  "cat":     { "level": 1, "actions": ["spotlight"] },
  "dog":     { "level": 2, "actions": ["spotlight", "audio_alarm"] },
  "bear":    { "level": 3, "actions": ["spotlight", "siren", "audio_alarm"] }
}
```

**Deterrent behavior:**
- Cooldown period between activations (default 5 min per species)
- Maximum siren duration (default 10s — don't annoy the neighbors)
- Spotlight auto-off after configurable timeout (default 2 min)
- Effectiveness tracking: did the animal leave within 60s of deterrent activation?
- All actions logged to `deterrent_actions` table

#### `reports.py` — Intelligence Reports

Generates daily summaries for human review and LLM consumption.

- Runs automatically at end of day (configurable time, default 23:59)
- Also callable on-demand via dashboard or API
- Outputs to `data/exports/` in both JSON and Markdown formats

**Daily report includes:**
- Total detections, predator detections, unique species seen
- Per-species breakdown with counts and time patterns
- Predator visit summaries (when, how long, what happened)
- Deterrent activation count and success rate
- Peak activity hours
- Natural language summary paragraph

#### `api.py` — REST API for LLM Tools

Structured API that LLM assistants (via OpenClaw) can query.

```
GET  /api/v1/status                          → Service health + camera status
GET  /api/v1/summary/today                   → Today's summary
GET  /api/v1/summary/{date}                  → Summary for specific date
GET  /api/v1/detections?class=hawk&days=7    → Query detections with filters
GET  /api/v1/tracks?predator=true&days=30    → Query animal visits
GET  /api/v1/patterns/{class_name}           → Species-specific patterns
GET  /api/v1/deterrents/effectiveness        → Deterrent success rates
GET  /api/v1/export/{date}                   → Full daily export (JSON)
POST /api/v1/cameras/{id}/ptz               → Control PTZ position
POST /api/v1/cameras/{id}/spotlight         → Toggle spotlight
POST /api/v1/cameras/{id}/siren            → Trigger siren
```

All endpoints return structured JSON. Authentication via API key header for hosted mode.

---

## 6. PTZ Patrol Strategy

The E1 Outdoor Pro supports up to 64 preset positions. We define a patrol route that cycles through key monitoring positions.

### Suggested Presets (to be calibrated after camera install)

| Preset | Name | Purpose | Dwell Time |
|--------|------|---------|------------|
| 1 | `yard-center` | Wide-angle default — covers main kill zone | 45s |
| 2 | `coop-approach` | Path from tree line to chicken area | 30s |
| 3 | `fence-line` | Where ground predators typically enter | 30s |
| 4 | `sky-watch` | Tilted upward for aerial predators (hawks) | 20s |
| 5 | `driveway` | Secondary monitoring, catch approaching animals | 20s |

### Patrol Behavior

**Normal operation:**
1. Camera cycles through patrol presets in order
2. Dwells at each position for configured time
3. YOLO runs on every frame regardless of position
4. Full 360° coverage achieved over ~2.5 minute patrol cycle

**On predator detection:**
1. Patrol pauses immediately
2. If predator is at edge of frame → PTZ centers on it
3. Optical zoom increases to 2-3× for better classification
4. High-res zoomed snapshot captured and saved
5. Deterrent engine evaluates and fires appropriate response
6. Camera tracks animal until it leaves frame or 60s timeout
7. After timeout → return to patrol from where it left off

**Dashboard controls:**
- Manual PTZ joystick (pan/tilt/zoom)
- Go to any preset with one click
- Start/stop patrol
- Save current position as new preset
- Edit patrol route (reorder, add/remove presets, change dwell times)

---

## 7. Animal Tracking Intelligence

### How Tracks Work

A **track** represents one animal's continuous visit to the monitored area.

```
Timeline:
  10:23:01  hawk detected (confidence 0.72) → NEW Track #47 created
  10:23:02  hawk detected (confidence 0.81) → added to Track #47
  10:23:03  hawk detected (confidence 0.85) → added to Track #47
  10:23:03  deterrent fires: spotlight + audio alarm
  10:23:15  hawk detected (confidence 0.68) → still in Track #47
  10:23:45  no hawk detected
  10:24:45  60s timeout → Track #47 closed
            duration: 1m44s, detections: 4, outcome: "deterred"
```

### Pattern Analysis

Over time, the system builds a profile per species:

```json
{
  "species": "hawk",
  "total_visits": 47,
  "total_duration_minutes": 82,
  "avg_visit_duration_seconds": 105,
  "typical_hours": [10, 11, 12, 13, 14],
  "peak_hour": 11,
  "visits_by_day_of_week": {"Mon": 8, "Tue": 6, "Wed": 9, ...},
  "deterrent_success_rate": 0.89,
  "avg_time_to_leave_after_deterrent_seconds": 23,
  "approach_directions": ["northeast", "east"],
  "last_seen": "2026-04-15T11:23:45",
  "trend": "increasing"  // compared to previous 7-day window
}
```

This data is what gets fed to LLM assistants for analysis and recommendations.

---

## 8. LLM Integration (OpenClaw)

### Data Format for LLM Consumption

Daily exports written to `data/exports/YYYY-MM-DD.json`:

```json
{
  "date": "2026-04-15",
  "farm": "Hampton CT",
  "camera": "e1-outdoor-yard",
  "monitoring_hours": 12.5,
  "frames_processed": 45000,
  "summary": "Moderate activity day. 3 hawk sightings between 10am-2pm, all deterred by spotlight + audio alarm within 30 seconds. 1 raccoon at dusk, left on its own before deterrent fired. Zero chicken casualties. Chickens active in yard 7am-6pm.",
  "predator_visits": [
    {
      "species": "hawk",
      "time": "10:23",
      "duration_seconds": 104,
      "max_confidence": 0.85,
      "deterrent": ["spotlight", "audio_alarm"],
      "outcome": "deterred",
      "time_to_leave_seconds": 23,
      "snapshot": "snapshots/2026-04-15/102301_hawk_t47.jpg"
    }
  ],
  "stats": {
    "total_detections": 312,
    "predator_detections": 8,
    "species_counts": {"hawk": 3, "chicken": 198, "person": 45, "cat": 12, "raccoon": 1},
    "alerts_sent": 3,
    "deterrents_fired": 3,
    "deterrent_success_rate": 1.0,
    "peak_activity_hour": 11,
    "chicken_casualties": 0
  },
  "patterns": {
    "hawk_typical_hours": "10:00-14:00",
    "hawk_trend_7d": "stable",
    "new_species_this_week": []
  }
}
```

### Natural Language Daily Report

Also exported as `data/exports/YYYY-MM-DD.md`:

```markdown
## Farm Guardian Daily Report — April 15, 2026

### Activity Summary
Monitored for 12.5 hours (6:00 AM – 6:30 PM). Processed 45,000 frames.

### Predator Activity
- **3 hawk sightings** between 10:00 AM and 2:00 PM
  - All approaches from the east/northeast (tree line direction)
  - Average visit duration: 1 minute 45 seconds
  - All deterred by spotlight + audio alarm
  - Average time to leave after deterrent: 23 seconds
- **1 raccoon** at 6:15 PM — departed before deterrent threshold

### Livestock
- Chickens active in yard: 7:00 AM – 6:00 PM
- No casualties detected

### Deterrent Effectiveness
- Activated 3 times today (all for hawks)
- Success rate: 100%
- Average response time: 1.8 seconds (detection → deterrent)

### 7-Day Trends
- Hawk visits: stable (avg 3/day this week vs 3/day last week)
- No new species observed
- Deterrent success rate: 93% (week average)
```

### Queryable API for LLM Tools

LLM assistants query the REST API to answer questions like:
- "How many hawks visited this week?"
- "What time do predators usually appear?"
- "Are the deterrents working?"
- "Show me the last fox sighting"
- "What's the trend in predator activity?"

---

## 9. Web Hosting Architecture

### Phase 1: Local Only (Current)

```
Mac Mini (192.168.x.x:8080)
├── FastAPI serves dashboard + API
├── SQLite database
├── MJPEG live streams from camera
├── No authentication (trusted local network)
└── Accessible from any device on home WiFi
```

### Phase 2: Hosted at farm.markbarney.net

```
Mac Mini (local)                    farm.markbarney.net (cloud)
├── Guardian service                ├── FastAPI (dashboard + API)
├── Camera streams (RTSP)           ├── PostgreSQL (synced data)
├── YOLO detection                  ├── Daily reports + charts
├── SQLite (primary DB)             ├── Historical data browser
├── Local dashboard                 ├── REST API (authenticated)
├── Data sync agent ─────────────── ├── WebSocket live events
│   (pushes summaries,              └── Static snapshot gallery
│    alerts, key snapshots)
└── Full local operation continues
    even if cloud is offline
```

**What gets synced to cloud:**
- Detection summaries (not every frame — bandwidth)
- Alert records
- Daily summaries and reports
- Predator snapshots only (not every chicken detection)
- Track/visit data

**What stays local only:**
- Raw RTSP streams (too much bandwidth)
- Full detection history (queryable locally via API)
- Camera control (PTZ, spotlight, siren — local network only for security)

**Tech for hosted version:**
- Same FastAPI codebase, different config
- PostgreSQL instead of SQLite (one-line config change with our abstraction layer)
- Authentication: API key for LLM tools, session auth for dashboard
- WebSocket for real-time event push (replaces MJPEG polling)
- Deployment: Railway, Fly.io, or VPS (user's choice)
- Domain: `farm.markbarney.net` (user already owns this)

---

## 10. Configuration (v2)

```json
{
  "cameras": [
    {
      "id": "e1-outdoor-yard",
      "name": "House Yard Camera",
      "model": "Reolink E1 Outdoor Pro",
      "ip": "192.168.1.XXX",
      "username": "admin",
      "password": "YOUR_PASSWORD",
      "onvif_port": 80,
      "type": "ptz",
      "location": "house-side-facing-yard",
      "capabilities": ["ptz", "spotlight", "siren", "audio", "auto_track"],
      "stream": {
        "profile": "main",
        "resolution": "4k",
        "capture_fps": 1
      }
    }
  ],
  "detection": {
    "model_path": "yolov8n.pt",
    "confidence_threshold": 0.45,
    "frame_interval_seconds": 1.0,
    "predator_classes": ["bird", "cat", "dog", "bear"],
    "ignore_classes": ["person", "car", "truck", "bicycle"],
    "class_confidence_thresholds": {
      "bird": 0.50,
      "cat": 0.40,
      "dog": 0.40
    },
    "bird_min_bbox_width_pct": 8,
    "min_dwell_frames": 3,
    "no_alert_zones": []
  },
  "tracking": {
    "track_timeout_seconds": 60,
    "min_detections_for_track": 2,
    "merge_iou_threshold": 0.3
  },
  "deterrent": {
    "enabled": true,
    "response_delay_seconds": 1.5,
    "response_rules": {
      "hawk":    { "level": 2, "actions": ["spotlight", "audio_alarm"] },
      "fox":     { "level": 3, "actions": ["spotlight", "siren", "audio_alarm"] },
      "coyote":  { "level": 3, "actions": ["spotlight", "siren", "audio_alarm"] },
      "raccoon": { "level": 2, "actions": ["spotlight", "audio_alarm"] },
      "cat":     { "level": 1, "actions": ["spotlight"] },
      "dog":     { "level": 2, "actions": ["spotlight", "audio_alarm"] },
      "bear":    { "level": 3, "actions": ["spotlight", "siren", "audio_alarm"] }
    },
    "spotlight_brightness": 100,
    "spotlight_duration_seconds": 120,
    "siren_duration_seconds": 10,
    "cooldown_seconds": 300,
    "effectiveness_window_seconds": 60
  },
  "ptz": {
    "patrol_enabled": true,
    "patrol_interval_seconds": 45,
    "detection_zoom_level": 2.0,
    "track_timeout_seconds": 60,
    "return_to_patrol_delay_seconds": 10,
    "presets": [
      { "name": "yard-center",    "pan": 180, "tilt": 25, "zoom": 1.0, "dwell": 45, "patrol": true },
      { "name": "coop-approach",  "pan": 220, "tilt": 30, "zoom": 1.5, "dwell": 30, "patrol": true },
      { "name": "fence-line",     "pan": 120, "tilt": 20, "zoom": 1.0, "dwell": 30, "patrol": true },
      { "name": "sky-watch",      "pan": 180, "tilt": 10, "zoom": 1.0, "dwell": 20, "patrol": true },
      { "name": "driveway",       "pan": 300, "tilt": 25, "zoom": 1.0, "dwell": 20, "patrol": true }
    ]
  },
  "alerts": {
    "discord_webhook_url": "YOUR_WEBHOOK_URL",
    "include_snapshot": true,
    "cooldown_seconds": 300
  },
  "database": {
    "path": "data/guardian.db",
    "backup_daily": true,
    "backup_dir": "data/backups",
    "retention_days": 365
  },
  "storage": {
    "snapshots_dir": "data/snapshots",
    "exports_dir": "data/exports",
    "max_days_retained": 90,
    "save_predator_snapshots": true,
    "save_zoomed_snapshots": true
  },
  "reports": {
    "daily_summary_time": "23:59",
    "export_formats": ["json", "markdown"],
    "discord_daily_report": false,
    "daily_report_webhook_url": ""
  },
  "dashboard": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 8080
  },
  "hosting": {
    "mode": "local",
    "sync_enabled": false,
    "remote_url": "",
    "api_key": ""
  },
  "logging": {
    "level": "INFO",
    "file": "guardian.log"
  }
}
```

---

## 11. Dependencies (v2)

| Package | Purpose | New? |
|---------|---------|------|
| `opencv-python` | RTSP capture, frame processing, image encoding | Existing |
| `ultralytics` | YOLOv8 model loading and inference | Existing |
| `onvif-zeep` | ONVIF camera discovery | Existing |
| `requests` | Discord webhook HTTP posts | Existing |
| `Pillow` | Image format conversion and saving | Existing |
| `fastapi` | Dashboard + API web framework | Existing |
| `uvicorn` | ASGI server | Existing |
| `python-multipart` | Form support for FastAPI | Existing |
| `reolink-aio` | Reolink camera control (PTZ, spotlight, siren) | **NEW** |
| `aiohttp` | Async HTTP (required by reolink-aio) | **NEW** |

**No new heavy dependencies.** `reolink-aio` is the only significant addition, and it's the battle-tested library used by Home Assistant's official Reolink integration.

SQLite uses Python's built-in `sqlite3` module — no additional package needed.

---

## 12. Implementation Phases

### Phase 1: Foundation (v1 — COMPLETE)
- [x] ONVIF camera discovery (`discovery.py`)
- [x] RTSP frame capture with reconnection (`capture.py`)
- [x] YOLOv8 detection with false-positive suppression (`detect.py`)
- [x] Discord webhook alerts with rate limiting (`alerts.py`)
- [x] JSONL event logging with snapshots (`logger.py`)
- [x] Service orchestration with graceful shutdown (`guardian.py`)
- [x] Local web dashboard with live feeds (`dashboard.py`, `static/`)

### Phase 2: Database + Tracking
- [ ] `database.py` — SQLite abstraction layer, schema creation, migrations
- [ ] Update `logger.py` — write to both DB and legacy JSONL
- [ ] `tracker.py` — animal visit tracking, track lifecycle management
- [ ] Update `guardian.py` — wire tracker into detection pipeline
- [ ] Update dashboard — event browser reads from DB, filterable

### Phase 3: Camera Control + Deterrence
- [ ] `camera_control.py` — reolink_aio integration for PTZ/spotlight/siren
- [ ] `deterrent.py` — automated response engine with escalation levels
- [ ] PTZ preset management (save, list, go-to, patrol)
- [ ] PTZ patrol automation loop
- [ ] Detection-triggered PTZ tracking + zoom
- [ ] Update dashboard — PTZ joystick, preset buttons, deterrent controls

### Phase 4: Intelligence + Reporting
- [ ] `reports.py` — daily summary generation (JSON + Markdown)
- [ ] Pattern analysis (per-species time profiles, trends)
- [ ] Deterrent effectiveness scoring
- [ ] `api.py` — REST API for LLM tool queries
- [ ] Update dashboard — charts, trend visualization, report viewer

### Phase 5: Web Hosting Prep
- [ ] Database abstraction supports PostgreSQL connection string
- [ ] Data sync agent (local SQLite → remote PostgreSQL)
- [ ] Authentication layer (API keys, session auth)
- [ ] WebSocket real-time event stream
- [ ] Deploy to `farm.markbarney.net`
- [ ] Public daily report pages

### Phase 6: Refinement
- [ ] Custom YOLO model fine-tuned on local species (hawk vs chicken distinction)
- [ ] Weather context integration (OpenWeather API — hawks hunt more on clear days)
- [ ] Weekly trend reports (Discord + export)
- [ ] Time-lapse compilation of daily activity

---

## 13. What Success Looks Like

### Week 1 (camera arrives)
- Camera discovered, RTSP streaming, YOLO detecting animals
- Discord alerts when hawks spotted
- Dashboard shows live feed and detection timeline
- Detections writing to SQLite database

### Week 2
- Patrol route calibrated to the actual yard layout
- Spotlight + siren firing automatically on predator detection
- Starting to build species activity profiles
- Daily reports generating

### Month 1
- Enough data to see patterns: "hawks come at 11am from the east"
- Deterrent effectiveness measured: "spotlight works 90% of the time"
- LLM assistants can query the API for insights
- Zero (or reduced) chicken losses

### Month 3
- Rich historical dataset for LLM analysis
- Possibly a fine-tuned YOLO model with better hawk/chicken distinction
- Dashboard hosted at `farm.markbarney.net` for remote monitoring
- System runs unattended 24/7 with daily reports to Discord

---

## 14. Open Questions for Review

1. **Custom YOLO training priority:** Should we start collecting training data from day one to eventually fine-tune a hawk-vs-chicken model? The generic YOLO "bird" class won't distinguish between them.

2. **Siren neighbor impact:** The E1 Outdoor Pro has a built-in siren. At a 13-acre property, neighbors may not hear it — but worth confirming before enabling auto-siren.

3. **Night operation:** Hawks are daytime predators, but foxes/raccoons are nocturnal. The camera has IR night vision. Do we want 24/7 monitoring or daytime only?

4. **PTZ vs. fixed second camera:** When adding the second camera, a fixed Lumus-style camera at the coop could provide continuous coverage while the E1 Pro does PTZ patrol elsewhere. Worth considering vs. two PTZ cameras.

5. **OpenClaw integration specifics:** What format does OpenClaw expect for tool definitions? We can shape the API to match exactly.

6. **farm.markbarney.net hosting stack:** What's currently running there? (Static site? CMS? What host?) This affects how we deploy the dashboard alongside it.

---

*This plan is ready for review by the secondary assistant. After notes come back, we implement Phase 2.*
