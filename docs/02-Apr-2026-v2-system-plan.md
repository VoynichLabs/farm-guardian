# Farm Guardian v2 — Comprehensive System Plan

**Author:** Cascade (Claude Opus 4.6)
**Date:** 02-April-2026
**Status:** Plan — Reviewed by Bubba (Claude Sonnet 4.6), 03-April-2026
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
- **Vision model species refinement** — YOLO detects "bird", local GLM vision model confirms "hawk" vs "chicken"
- **Daily intelligence reports** — natural language summaries for local LLM assistants
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

Aerial and ground predators are killing chickens at the Hampton property. The camera has motion detection and a spotlight, but it can't distinguish a chicken from a hawk.

### Known Local Animals

| Animal | Category | Threat Level | Notes |
|--------|----------|-------------|-------|
| **Chickens** | Livestock | N/A | The birds we're protecting |
| **Small dogs** | Regular visitor | None | Neighbor dogs, frequent visitors |
| **Small birds** | Wildlife | None | Songbirds, sparrows, etc. |
| **Large raptor (hawk)** | Aerial predator | **HIGH** | Swoops from sky — primary threat |
| **Bobcat** | Ground predator | **HIGH** | Large dog-like, stalks from cover |
| **Raccoon** | Ground predator | **MEDIUM** | Nocturnal, opportunistic |
| **Possum** | Ground nuisance | **LOW** | Nocturnal, occasional egg thief |

No bears, cows, or large livestock in the area.

### What we need that the camera alone can't do:

1. **Species-level classification** — "that's a hawk, not a chicken" — YOLO detects "bird", then our local **GLM vision model** (`zai-org/glm-4.6v-flash`) confirms the species
2. **Intelligent alerting** — alert on predators only, not every motion event
3. **Active deterrence** — automatically trigger spotlight + siren when a predator is detected (the camera can do this on any motion, but we want it only for predators)
4. **Tracking over time** — which species visit, when, how often, do deterrents work?
5. **Pattern intelligence** — "hawks come between 10am-2pm" — feed this to local LLM for analysis
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
├── vision.py               ← NEW: GLM vision model species refinement
├── tracker.py              ← NEW: Animal visit tracking + pattern analysis
├── camera_control.py       ← NEW: PTZ, spotlight, siren via reolink_aio
├── deterrent.py            ← NEW: Automated deterrent response engine
├── alerts.py               ← Discord webhook alerts with rate limiting
├── logger.py               ← Event logging (now writes to DB + legacy JSONL)
├── database.py             ← NEW: SQLite abstraction layer
├── reports.py              ← NEW: Daily summaries + LLM-ready exports
├── dashboard.py            ← FastAPI web dashboard (local + deployable)
├── api.py                  ← NEW: REST API for LLM tools
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
         ├──[RTSP stream]──→ capture.py ──→ detect.py ──→ vision.py ──→ tracker.py
         │                                      │            │                │
         │                                      │     (YOLO says "bird"?   (group into visits,
         │                                      │      ask GLM: hawk or     track duration,
         │                                      │      chicken?)             assign outcomes)
         │                                      │            │                │
         │                                      ▼            ▼                ▼
         │                                 database.py ◄── logger.py
         │                                      │
         │                                      ├──→ alerts.py ──→ Discord
         │                                      ├──→ deterrent.py ──→ camera_control.py ──┐
         │                                      ├──→ reports.py ──→ data/exports/         │
         │                                      └──→ api.py ──→ LLM tools (local)        │
         │                                                                                │
         ◄──[Reolink HTTP API / reolink_aio]──── camera_control.py ◄──────────────────────┘
              (PTZ move, spotlight on/off,          │
               siren trigger, audio alarm,          └──→ dashboard.py ──→ Browser
               snap, preset management)                  (http://macmini:8080 local)
                                                         (https://farm.markbarney.net future)
```

### New Module Descriptions

#### `vision.py` — GLM Vision Model Species Refinement

Uses the locally-running `zai-org/glm-4.6v-flash` vision model (via LM Studio at `http://127.0.0.1:1234`) as a **second-pass species identifier**.

**The problem:** YOLO detects "bird" but can't distinguish a hawk from a chicken. Both are critical to get right — one is the predator, the other is the livestock.

**The solution:** When YOLO detects a "bird" class, we crop the bounding box region, encode it as JPEG, and send it to the GLM vision model with a structured prompt:

```
You are a wildlife identification system on a chicken farm.
Look at this image and identify the bird species.
Is this a: (a) chicken, (b) hawk/raptor, (c) small songbird, (d) other bird?
Respond with ONLY the category letter and species name.
```

**Behavior:**
- Only triggered for ambiguous YOLO classes ("bird") — not for every detection
- Also used when YOLO detects a large "cat" or "dog" to distinguish: is it a bobcat or a house cat? A neighbor's dog or something wild?
- Response cached per track — don't re-query for same animal in same visit
- Timeout: 3 seconds max — if vision model is slow/offline, fall back to YOLO class
- All vision refinements logged to `detections` table (`model_name` = `glm-4.6v-flash`)

**LM Studio API format** (OpenAI-compatible):
```python
POST http://127.0.0.1:1234/v1/chat/completions
{
  "model": "zai-org/glm-4.6v-flash",
  "messages": [
    {
      "role": "user",
      "content": [
        { "type": "text", "text": "Identify this bird on a chicken farm..." },
        { "type": "image_url", "image_url": { "url": "data:image/jpeg;base64,..." } }
      ]
    }
  ],
  "max_tokens": 50,
  "temperature": 0.1
}
```

This is a huge advantage — we get species-level classification without needing to custom-train a YOLO model, using hardware we already have.

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
| 0 | Any detection | Log only | Passive monitoring (chickens, small birds, small dogs) |
| 1 | Low-threat animal | Spotlight on | Possum near coop |
| 2 | Medium-threat predator | Spotlight + audio alarm | Raccoon approaching, hawk overhead |
| 3 | High-threat predator | Spotlight + siren + audio alarm | Bobcat stalking |

**Per-species response rules** (configurable):

```json
{
  "hawk":    { "level": 2, "actions": ["spotlight", "audio_alarm"] },
  "bobcat":  { "level": 3, "actions": ["spotlight", "siren", "audio_alarm"] },
  "raccoon": { "level": 2, "actions": ["spotlight", "audio_alarm"] },
  "possum":  { "level": 1, "actions": ["spotlight"] },
  "small_dog": { "level": 0, "actions": [] },
  "chicken": { "level": 0, "actions": [] },
  "small_bird": { "level": 0, "actions": [] }
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

Structured API that local LLM assistants can query.

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

## 8. LLM Integration (Local LM Studio)

The Mac Mini runs **LM Studio** locally, serving LLM inference on ports 1-4 with an OpenAI-compatible API. The vision model `zai-org/glm-4.6v-flash` runs at `http://127.0.0.1:1234` and is used for real-time species refinement (see `vision.py` above).

Beyond real-time classification, the LLMs can also be used for:
- **Daily report generation** — feed structured data, get natural language summary
- **Pattern analysis** — "what's different about this week vs. last week?"
- **Alert enrichment** — add context to Discord messages ("this hawk has visited 3 times today")
- **Queryable assistant** — answer ad-hoc questions about farm activity via the REST API

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

Local LLM assistants query the REST API to answer questions like:
- "How many hawks visited this week?"
- "What time do predators usually appear?"
- "Are the deterrents working?"
- "Show me the last raccoon sighting"
- "What's the trend in predator activity?"

---

## 9. External Hawk Tracking — eBird Early Warning System

*Added by Bubba (Claude Sonnet 4.6), 03-April-2026*

> **Context:** Hawks are fast and come from above. On-camera detection fires AFTER the hawk is already in the yard. For aerial predators, we want advance warning before they arrive — ideally enough time to bring the flock inside or ramp up deterrence posture.

### The Problem

Ground-level YOLO detection fires when the hawk is within camera view — typically 1-3 seconds before it could strike. That's not enough time for human intervention. The rooster helps (natural alarm caller), but we need a software layer that gives 15-60 minutes of early warning.

### Solution: eBird Recent Observations API

Cornell Lab's eBird maintains a real-time database of bird sightings submitted by birders. The API is free (API key required, no cost). We can poll for raptor sightings near Hampton CT every 30 minutes and alert when hawks are active in the area.

**Key facts about Hampton CT:**
- Located in the Connecticut River Valley corridor — major raptor migration route
- Red-tailed Hawk, Cooper's Hawk, Sharp-shinned Hawk, and Red-shouldered Hawk are all common
- Peak hawk activity: March–May (northbound migration) and September–November (southbound)
- Daily pattern: hawks most active 9am–3pm on clear days with light winds

### eBird API Integration

**Endpoint:**
```
GET https://api.ebird.org/v2/data/obs/geo/recent
    ?lat=41.7943&lng=-72.0591
    &dist=15
    &back=2
    &cat=species
    &key=YOUR_EBIRD_KEY
```

Parameters:
- `lat/lng` — Hampton CT coordinates (41.7943, -72.0591)
- `dist=15` — 15km radius (~9 miles)
- `back=2` — observations within last 2 hours
- `cat=species` — species sightings only

**Raptor species to watch:**
```python
RAPTOR_SPECIES = {
    "rethaw": "Red-tailed Hawk",      # HIGH — primary chicken killer
    "coohaw": "Cooper's Hawk",        # HIGH — specialist bird/poultry hunter
    "shshaw": "Sharp-shinned Hawk",   # MEDIUM — smaller, takes bantams
    "reshaw": "Red-shouldered Hawk",  # MEDIUM
    "osfreh": "Osprey",               # LOW — fish-eater, rarely threatens chickens
    "amerke": "American Kestrel",     # LOW — too small for adult chickens
    "merlin": "Merlin",               # LOW
    "pefafa": "Peregrine Falcon",     # MEDIUM — opportunistic
    "norbob": "Northern Harrier",     # MEDIUM
    "baleag": "Bald Eagle",           # LOW — rare visitor, prefers fish
}

HIGH_THREAT_RAPTORS = {"rethaw", "coohaw", "shshaw"}
```

### New Module: `ebird.py`

```python
# ebird.py
# Author: Bubba (Claude Sonnet 4.6)
# Date: 03-April-2026
# PURPOSE: eBird API polling for regional raptor activity near Hampton CT.
# Provides early warning when hawks are reported within 15km of the farm.
# SRP/DRY check: Pass — single responsibility: external raptor intelligence.

import requests
import json
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path


HAMPTON_CT_LAT = 41.7943
HAMPTON_CT_LNG = -72.0591
SEARCH_RADIUS_KM = 15
LOOKBACK_HOURS = 2

RAPTOR_SPECIES = {
    "rethaw": ("Red-tailed Hawk", "HIGH"),
    "coohaw": ("Cooper's Hawk", "HIGH"),
    "shshaw": ("Sharp-shinned Hawk", "MEDIUM"),
    "reshaw": ("Red-shouldered Hawk", "MEDIUM"),
    "pefafa": ("Peregrine Falcon", "MEDIUM"),
    "norbob": ("Northern Harrier", "MEDIUM"),
    "osfreh": ("Osprey", "LOW"),
    "amerke": ("American Kestrel", "LOW"),
    "merlin": ("Merlin", "LOW"),
    "baleag": ("Bald Eagle", "LOW"),
}


def get_recent_raptors(api_key: str) -> list[dict]:
    """
    Poll eBird for raptor sightings within 15km of Hampton CT.
    Returns list of sighting dicts with species, location, distance, threat level.
    """
    url = "https://api.ebird.org/v2/data/obs/geo/recent"
    params = {
        "lat": HAMPTON_CT_LAT,
        "lng": HAMPTON_CT_LNG,
        "dist": SEARCH_RADIUS_KM,
        "back": LOOKBACK_HOURS,
        "cat": "species",
    }
    headers = {"X-eBirdApiToken": api_key}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        observations = resp.json()
    except requests.RequestException as e:
        return []  # fail silently — don't crash guardian on eBird outage

    raptors = []
    for obs in observations:
        species_code = obs.get("speciesCode", "")
        if species_code in RAPTOR_SPECIES:
            name, threat = RAPTOR_SPECIES[species_code]
            raptors.append({
                "species_code": species_code,
                "common_name": name,
                "threat_level": threat,
                "location_name": obs.get("locName", "Unknown location"),
                "lat": obs.get("lat"),
                "lng": obs.get("lng"),
                "observed_at": obs.get("obsDt"),
                "count": obs.get("howMany", 1),
                "observer_count": obs.get("numObservers", 1),
            })

    return raptors


def format_ebird_alert(raptors: list[dict]) -> Optional[str]:
    """Build a Discord alert message for regional raptor activity."""
    if not raptors:
        return None

    high = [r for r in raptors if r["threat_level"] == "HIGH"]
    medium = [r for r in raptors if r["threat_level"] == "MEDIUM"]
    all_others = [r for r in raptors if r["threat_level"] == "LOW"]

    lines = ["🦅 **Regional Raptor Alert** — eBird sightings within 15km of farm"]
    if high:
        lines.append("**HIGH THREAT:**")
        for r in high:
            lines.append(f"  • {r['common_name']} — {r['location_name']} ({r['observed_at']})")
    if medium:
        lines.append("**MEDIUM THREAT:**")
        for r in medium:
            lines.append(f"  • {r['common_name']} — {r['location_name']}")
    if all_others:
        lines.append(f"*Low-threat raptors: {', '.join(r['common_name'] for r in all_others)}*")

    lines.append("\n⚠️ Consider bringing flock inside or increasing yard supervision.")
    return "\n".join(lines)
```

### Polling Schedule

New cron-style task in `guardian.py`:
- Poll eBird every **30 minutes** during hawk hours (8am–4pm)
- Only alert if HIGH or MEDIUM threat raptors found AND we haven't alerted in last 2 hours
- Log all raptor sightings to `ebird_sightings` table (new table, schema below)
- Suppress duplicate alerts for same species within cooldown window

### New DB Table: `ebird_sightings`

```sql
CREATE TABLE ebird_sightings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    species_code    TEXT NOT NULL,
    common_name     TEXT NOT NULL,
    threat_level    TEXT NOT NULL,        -- "HIGH" | "MEDIUM" | "LOW"
    location_name   TEXT,
    lat             REAL,
    lng             REAL,
    observed_at     TEXT,                 -- eBird observation datetime
    polled_at       TEXT NOT NULL,        -- when we retrieved this
    count           INTEGER DEFAULT 1,
    alert_sent      INTEGER DEFAULT 0,    -- 1 if Discord alert fired
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_ebird_time ON ebird_sightings(polled_at);
CREATE INDEX idx_ebird_threat ON ebird_sightings(threat_level, polled_at);
```

### Config Addition

```json
"ebird": {
  "enabled": true,
  "api_key": "YOUR_EBIRD_KEY",
  "poll_interval_seconds": 1800,
  "poll_hours_start": 8,
  "poll_hours_end": 16,
  "alert_on_threat_levels": ["HIGH", "MEDIUM"],
  "alert_cooldown_seconds": 7200,
  "radius_km": 15,
  "lookback_hours": 2
}
```

### eBird API Key

Free at https://ebird.org/api/keygen — requires eBird account. No cost.

### Limitations

- eBird data depends on birder submissions — a hawk in the area may not be reported if no birder saw it
- 30-min poll interval means sighting could be 30-90 minutes old by the time alert fires
- Not a substitute for on-camera detection — this is **advance warning**, not ground truth

### Integration Into Guardian Daily Reports

Daily report adds a new section:

```
### Regional Hawk Activity (eBird)
- 2 Cooper's Hawk sightings reported within 15km (10am, 1pm)
- 1 Red-tailed Hawk reported at Goodwin Forest, 8km away (11:30am)
- 3 alerts sent to Discord
- Correlation with on-camera detections: 2/3 eBird alerts preceded a camera detection within 45 min
```

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
    "predator_classes": ["bird", "cat"],
    "safe_classes": ["chicken", "small_bird", "small_dog"],
    "ignore_classes": ["person", "car", "truck", "bicycle"],
    "class_confidence_thresholds": {
      "bird": 0.50,
      "cat": 0.40
    },
    "bird_min_bbox_width_pct": 8,
    "min_dwell_frames": 3,
    "no_alert_zones": []
  },
  "vision": {
    "enabled": true,
    "endpoint": "http://127.0.0.1:1234/v1/chat/completions",
    "model": "zai-org/glm-4.6v-flash",
    "timeout_seconds": 3,
    "trigger_classes": ["bird", "cat", "dog"],
    "max_tokens": 50,
    "temperature": 0.1,
    "fallback_on_error": true
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
      "hawk":      { "level": 2, "actions": ["spotlight", "audio_alarm"] },
      "bobcat":    { "level": 3, "actions": ["spotlight", "siren", "audio_alarm"] },
      "raccoon":   { "level": 2, "actions": ["spotlight", "audio_alarm"] },
      "possum":    { "level": 1, "actions": ["spotlight"] },
      "small_dog": { "level": 0, "actions": [] },
      "chicken":   { "level": 0, "actions": [] },
      "small_bird":{ "level": 0, "actions": [] }
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

### Phase 2: Database + Vision + Tracking (COMPLETE)
- [x] `database.py` — SQLite abstraction layer, schema creation, migrations
- [x] `vision.py` — GLM vision model integration for species refinement
- [x] Update `logger.py` — write to both DB and legacy JSONL
- [x] `tracker.py` — animal visit tracking, track lifecycle management
- [x] Update `guardian.py` — wire vision + tracker into detection pipeline
- [x] Update dashboard — event browser reads from DB, filterable

### Phase 3: Camera Control + Deterrence (COMPLETE)
- [x] `camera_control.py` — reolink_aio integration for PTZ/spotlight/siren
- [x] `deterrent.py` — automated response engine with escalation levels
- [x] PTZ preset management (save, list, go-to, patrol)
- [x] PTZ patrol automation loop
- [x] Detection-triggered PTZ tracking + zoom
- [x] Update dashboard — PTZ joystick, preset buttons, deterrent controls

### Phase 4: Intelligence + Reporting (COMPLETE)
- [x] `reports.py` — daily summary generation (JSON + Markdown)
- [x] Pattern analysis (per-species time profiles, trends)
- [x] Deterrent effectiveness scoring
- [x] `api.py` — REST API for LLM tool queries
- [x] Update dashboard — charts, trend visualization, report viewer

### Phase 5: Web Hosting Prep
- [ ] Database abstraction supports PostgreSQL connection string
- [ ] Data sync agent (local SQLite → remote PostgreSQL)
- [ ] Authentication layer (API keys, session auth)
- [ ] WebSocket real-time event stream
- [ ] Deploy to `farm.markbarney.net`
- [ ] Public daily report pages

### Phase 6: Refinement
- [ ] Fine-tune vision model prompts based on real detection accuracy
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

1. **Siren neighbor impact:** The E1 Outdoor Pro has a built-in siren. At a 13-acre property, neighbors likely won't hear it — but worth confirming before enabling auto-siren.

2. **Night operation:** Hawks are daytime predators, but raccoons and possums are nocturnal. The camera has IR night vision. Do we want 24/7 monitoring or daytime only?

3. **Vision model latency:** The GLM vision model at `127.0.0.1:1234` needs to respond within ~3s for real-time use. If it's too slow on M4 Pro while also serving other LLM tasks on ports 1-4, we may need to adjust the pipeline (e.g., fire deterrent on YOLO "bird" immediately, then refine species async for logging).

4. **PTZ vs. fixed second camera:** When adding a second camera, a fixed camera at the coop could provide continuous coverage while the E1 Pro does PTZ patrol elsewhere.

5. **LM Studio port allocation:** User runs LM Studio on ports 1-4. Need to confirm which port serves the text LLM (for report generation) vs. the vision model (confirmed at 1234). Are these separate model instances?

6. **farm.markbarney.net hosting stack:** What's currently running there? (Static site? CMS? What host?) This affects how we deploy the dashboard alongside it.

---

*This plan is ready for review by the secondary assistant. After notes come back, we implement Phase 2.*
