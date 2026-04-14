# Author: Claude Opus 4.6 (1M context)
# Date: 14-April-2026
# PURPOSE: SQLite abstraction layer for Farm Guardian v2. All database reads/writes
#          go through this module. Creates and manages the guardian.db schema with
#          tables for cameras, detections, tracks, alerts, deterrent actions, PTZ presets,
#          daily summaries, eBird sightings, and (v2.25.0) the image_archive +
#          image_archive_edits tables backing the /api/v1/images/* REST surface.
#          Uses WAL mode for concurrent read access from dashboard/API while the
#          detection pipeline + image pipeline both write. Provides daily backup
#          to data/backups/. No ORM — raw parameterized SQL for portability to PostgreSQL.
# SRP/DRY check: Pass — single responsibility is structured data persistence.
#   The image_archive DDL is duplicated (with IF NOT EXISTS) from
#   tools/pipeline/store.py so the image REST API can query the table on fresh
#   installs where the pipeline hasn't run yet. Schema is stable; drift risk is low.

import json
import logging
import shutil
import sqlite3
import threading
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("guardian.database")


# ---------------------------------------------------------------------------
# Schema DDL — matches PLAN_V2 section 4 exactly
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
-- cameras: registered camera hardware
CREATE TABLE IF NOT EXISTS cameras (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    model           TEXT NOT NULL,
    ip              TEXT,
    rtsp_url        TEXT,
    type            TEXT NOT NULL DEFAULT 'ptz',
    location        TEXT,
    capabilities    TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'offline',
    last_seen_at    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- detections: every individual YOLO detection
CREATE TABLE IF NOT EXISTS detections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id       TEXT NOT NULL REFERENCES cameras(id),
    detected_at     TEXT NOT NULL,
    class_name      TEXT NOT NULL,
    confidence      REAL NOT NULL,
    bbox_x1         REAL NOT NULL,
    bbox_y1         REAL NOT NULL,
    bbox_x2         REAL NOT NULL,
    bbox_y2         REAL NOT NULL,
    bbox_area_pct   REAL,
    is_predator     INTEGER NOT NULL DEFAULT 0,
    track_id        INTEGER REFERENCES tracks(id),
    snapshot_path   TEXT,
    model_name      TEXT DEFAULT 'yolov8n',
    suppressed      INTEGER NOT NULL DEFAULT 0,
    suppression_reason TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_detections_camera_time ON detections(camera_id, detected_at);
CREATE INDEX IF NOT EXISTS idx_detections_class ON detections(class_name);
CREATE INDEX IF NOT EXISTS idx_detections_track ON detections(track_id);
CREATE INDEX IF NOT EXISTS idx_detections_predator ON detections(is_predator, detected_at);

-- tracks: animal visits (groups of related detections)
CREATE TABLE IF NOT EXISTS tracks (
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
    outcome         TEXT,
    deterrent_used  TEXT,
    ptz_position    TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tracks_camera_time ON tracks(camera_id, first_seen_at);
CREATE INDEX IF NOT EXISTS idx_tracks_predator ON tracks(is_predator, first_seen_at);
CREATE INDEX IF NOT EXISTS idx_tracks_class ON tracks(class_name);

-- alerts: every notification sent (Discord, siren, spotlight)
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id        INTEGER REFERENCES tracks(id),
    camera_id       TEXT NOT NULL REFERENCES cameras(id),
    alerted_at      TEXT NOT NULL,
    alert_type      TEXT NOT NULL,
    classes         TEXT NOT NULL,
    message         TEXT,
    snapshot_path   TEXT,
    delivered       INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts(alerted_at);
CREATE INDEX IF NOT EXISTS idx_alerts_track ON alerts(track_id);

-- deterrent_actions: every time we activated a deterrent
CREATE TABLE IF NOT EXISTS deterrent_actions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id        INTEGER REFERENCES tracks(id),
    camera_id       TEXT NOT NULL REFERENCES cameras(id),
    acted_at        TEXT NOT NULL,
    action_type     TEXT NOT NULL,
    duration_sec    REAL,
    result          TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ptz_presets: saved camera positions for patrol
CREATE TABLE IF NOT EXISTS ptz_presets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id       TEXT NOT NULL REFERENCES cameras(id),
    name            TEXT NOT NULL,
    pan             REAL NOT NULL,
    tilt            REAL NOT NULL,
    zoom            REAL NOT NULL DEFAULT 1.0,
    description     TEXT,
    is_patrol_stop  INTEGER NOT NULL DEFAULT 0,
    patrol_order    INTEGER,
    dwell_sec       INTEGER NOT NULL DEFAULT 30,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- daily_summaries: aggregated daily stats for LLM consumption
CREATE TABLE IF NOT EXISTS daily_summaries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_date    TEXT NOT NULL UNIQUE,
    total_detections    INTEGER NOT NULL DEFAULT 0,
    predator_detections INTEGER NOT NULL DEFAULT 0,
    unique_species      TEXT,
    alerts_sent         INTEGER NOT NULL DEFAULT 0,
    deterrents_activated INTEGER NOT NULL DEFAULT 0,
    peak_activity_hour  INTEGER,
    activity_by_hour    TEXT,
    species_counts      TEXT,
    predator_tracks     TEXT,
    deterrent_success_rate REAL,
    summary_text        TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_summaries_date ON daily_summaries(summary_date);

-- ebird_sightings: regional raptor observations from eBird API
CREATE TABLE IF NOT EXISTS ebird_sightings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    species_code    TEXT NOT NULL,
    common_name     TEXT NOT NULL,
    threat_level    TEXT NOT NULL,
    location_name   TEXT,
    lat             REAL,
    lng             REAL,
    observed_at     TEXT,
    polled_at       TEXT NOT NULL,
    count           INTEGER DEFAULT 1,
    alert_sent      INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ebird_time ON ebird_sightings(polled_at);
CREATE INDEX IF NOT EXISTS idx_ebird_threat ON ebird_sightings(threat_level, polled_at);

-- image_archive: one row per captured + VLM-enriched frame.
-- Canonical DDL lives in tools/pipeline/store.py:_SCHEMA_SQL; duplicated here
-- (with IF NOT EXISTS, so idempotent) so the image REST API can query the
-- table on fresh installs where the pipeline hasn't run yet. If you evolve
-- the schema, update BOTH places.
CREATE TABLE IF NOT EXISTS image_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    image_path TEXT,
    image_tier TEXT NOT NULL,
    sha256 TEXT,
    width INT, height INT, bytes INT,
    std_dev REAL, laplacian_var REAL, exposure_p50 REAL,
    vlm_model TEXT, vlm_inference_ms INT, vlm_prompt_hash TEXT,
    vlm_json TEXT NOT NULL,
    scene TEXT, bird_count INT, activity TEXT, lighting TEXT,
    composition TEXT, image_quality TEXT, share_worth TEXT,
    any_special_chick INT, apparent_age_days INT, has_concerns INT,
    individuals_visible_csv TEXT,
    retained_until TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_archive_camera_ts ON image_archive(camera_id, ts);
CREATE INDEX IF NOT EXISTS idx_archive_share    ON image_archive(share_worth, image_quality);
CREATE INDEX IF NOT EXISTS idx_archive_concerns ON image_archive(has_concerns);
CREATE INDEX IF NOT EXISTS idx_archive_retain   ON image_archive(retained_until);

-- image_archive_edits: audit log for every promote / demote / flag / unflag /
-- delete / purge action performed on an image_archive row via the
-- /api/v1/images/review/* endpoints. We snapshot the row's relevant fields
-- before + after each edit so we can reconstruct what changed and when — the
-- load-bearing concern is "if something concerning leaked publicly, prove
-- exactly when and how its state changed."
CREATE TABLE IF NOT EXISTS image_archive_edits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_image_id INTEGER NOT NULL REFERENCES image_archive(id),
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    action TEXT NOT NULL,    -- 'promote' | 'demote' | 'flag' | 'unflag' | 'delete' | 'purge'
    actor TEXT,              -- 'boss' for v0.1; future: user id
    note TEXT,
    request_id TEXT,
    pre_state TEXT,          -- JSON snapshot of relevant fields before the edit
    post_state TEXT          -- JSON snapshot after
);
CREATE INDEX IF NOT EXISTS idx_edits_target ON image_archive_edits(target_image_id);
CREATE INDEX IF NOT EXISTS idx_edits_ts     ON image_archive_edits(ts);
CREATE INDEX IF NOT EXISTS idx_edits_action ON image_archive_edits(action);
"""


class GuardianDB:
    """Thread-safe SQLite database layer for Farm Guardian."""

    def __init__(self, config: dict):
        db_cfg = config.get("database", {})
        self._db_path = Path(db_cfg.get("path", "data/guardian.db"))
        self._backup_daily = db_cfg.get("backup_daily", True)
        self._backup_dir = Path(db_cfg.get("backup_dir", "data/backups"))
        self._retention_days = db_cfg.get("retention_days", 365)

        # Ensure directories exist
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_dir.mkdir(parents=True, exist_ok=True)

        # Single connection with WAL mode for concurrent reads
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

        self._init_db()
        log.info("GuardianDB initialized — %s", self._db_path)

    def _init_db(self) -> None:
        """Create schema and enable WAL mode."""
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()
        log.info("GuardianDB closed")

    # ------------------------------------------------------------------
    # Cameras
    # ------------------------------------------------------------------

    def get_or_create_camera(
        self,
        camera_id: str,
        name: str,
        model: str = "unknown",
        ip: Optional[str] = None,
        rtsp_url: Optional[str] = None,
        cam_type: str = "ptz",
        location: Optional[str] = None,
        capabilities: Optional[list] = None,
    ) -> dict:
        """Insert or update a camera record. Returns the camera row as a dict."""
        now = datetime.now().isoformat()
        caps_json = json.dumps(capabilities or [])

        with self._lock:
            # Check if camera exists
            row = self._conn.execute(
                "SELECT * FROM cameras WHERE id = ?", (camera_id,)
            ).fetchone()

            if row:
                self._conn.execute(
                    """UPDATE cameras
                       SET ip = COALESCE(?, ip),
                           rtsp_url = COALESCE(?, rtsp_url),
                           status = 'online',
                           last_seen_at = ?
                       WHERE id = ?""",
                    (ip, rtsp_url, now, camera_id),
                )
            else:
                self._conn.execute(
                    """INSERT INTO cameras (id, name, model, ip, rtsp_url, type, location,
                                           capabilities, status, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'online', ?)""",
                    (camera_id, name, model, ip, rtsp_url, cam_type, location, caps_json, now),
                )
            self._conn.commit()

        return self._get_camera(camera_id)

    def update_camera_status(self, camera_id: str, status: str) -> None:
        """Update a camera's online/offline status."""
        now = datetime.now().isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE cameras SET status = ?, last_seen_at = ? WHERE id = ?",
                (status, now, camera_id),
            )
            self._conn.commit()

    def _get_camera(self, camera_id: str) -> dict:
        """Fetch a single camera row as a dict."""
        row = self._conn.execute(
            "SELECT * FROM cameras WHERE id = ?", (camera_id,)
        ).fetchone()
        return dict(row) if row else {}

    # ------------------------------------------------------------------
    # Detections
    # ------------------------------------------------------------------

    def insert_detection(
        self,
        camera_id: str,
        detected_at: str,
        class_name: str,
        confidence: float,
        bbox: tuple,
        bbox_area_pct: float = 0.0,
        is_predator: bool = False,
        track_id: Optional[int] = None,
        snapshot_path: Optional[str] = None,
        model_name: str = "yolov8n",
        suppressed: bool = False,
        suppression_reason: Optional[str] = None,
    ) -> int:
        """Insert a detection record. Returns the new row id."""
        x1, y1, x2, y2 = bbox
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO detections
                   (camera_id, detected_at, class_name, confidence,
                    bbox_x1, bbox_y1, bbox_x2, bbox_y2, bbox_area_pct,
                    is_predator, track_id, snapshot_path, model_name,
                    suppressed, suppression_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    camera_id, detected_at, class_name, round(confidence, 4),
                    x1, y1, x2, y2, round(bbox_area_pct, 2),
                    int(is_predator), track_id, snapshot_path, model_name,
                    int(suppressed), suppression_reason,
                ),
            )
            self._conn.commit()
            return cursor.lastrowid

    def get_recent_detections(
        self, camera_id: Optional[str] = None, minutes: int = 60, limit: int = 200
    ) -> list[dict]:
        """Fetch recent detections, optionally filtered by camera."""
        cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat()
        if camera_id:
            rows = self._conn.execute(
                """SELECT * FROM detections
                   WHERE camera_id = ? AND detected_at >= ?
                   ORDER BY detected_at DESC LIMIT ?""",
                (camera_id, cutoff, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM detections
                   WHERE detected_at >= ?
                   ORDER BY detected_at DESC LIMIT ?""",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_detection_track(self, detection_id: int, track_id: int) -> None:
        """Link a detection to a track after the track is created."""
        with self._lock:
            self._conn.execute(
                "UPDATE detections SET track_id = ? WHERE id = ?",
                (track_id, detection_id),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Tracks
    # ------------------------------------------------------------------

    def insert_track(
        self,
        camera_id: str,
        class_name: str,
        first_seen_at: str,
        is_predator: bool = False,
        max_confidence: float = 0.0,
    ) -> int:
        """Create a new track. Returns the new track id."""
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO tracks
                   (camera_id, class_name, first_seen_at, last_seen_at,
                    detection_count, max_confidence, avg_confidence, is_predator)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
                (
                    camera_id, class_name, first_seen_at, first_seen_at,
                    round(max_confidence, 4), round(max_confidence, 4),
                    int(is_predator),
                ),
            )
            self._conn.commit()
            return cursor.lastrowid

    def update_track(
        self,
        track_id: int,
        last_seen_at: str,
        detection_count: int,
        max_confidence: float,
        avg_confidence: float,
        duration_sec: Optional[float] = None,
        outcome: Optional[str] = None,
        deterrent_used: Optional[list] = None,
    ) -> None:
        """Update a track with latest detection data."""
        deterrent_json = json.dumps(deterrent_used) if deterrent_used else None
        with self._lock:
            self._conn.execute(
                """UPDATE tracks
                   SET last_seen_at = ?,
                       detection_count = ?,
                       max_confidence = ?,
                       avg_confidence = ?,
                       duration_sec = ?,
                       outcome = COALESCE(?, outcome),
                       deterrent_used = COALESCE(?, deterrent_used)
                   WHERE id = ?""",
                (
                    last_seen_at, detection_count,
                    round(max_confidence, 4), round(avg_confidence, 4),
                    round(duration_sec, 2) if duration_sec is not None else None,
                    outcome, deterrent_json, track_id,
                ),
            )
            self._conn.commit()

    def close_track(self, track_id: int, outcome: str = "unknown") -> None:
        """Finalize a track with its outcome."""
        with self._lock:
            row = self._conn.execute(
                "SELECT first_seen_at, last_seen_at FROM tracks WHERE id = ?",
                (track_id,),
            ).fetchone()
            if row:
                first = datetime.fromisoformat(row["first_seen_at"])
                last = datetime.fromisoformat(row["last_seen_at"])
                duration = (last - first).total_seconds()
                self._conn.execute(
                    "UPDATE tracks SET duration_sec = ?, outcome = ? WHERE id = ?",
                    (round(duration, 2), outcome, track_id),
                )
                self._conn.commit()

    def delete_track(self, track_id: int) -> None:
        """Remove a ghost track (single-frame false positive) from the database.

        Detections, alerts, and deterrent_actions may reference this track via
        foreign key, so null out those references before deleting the track row.
        """
        with self._lock:
            self._conn.execute("UPDATE detections SET track_id = NULL WHERE track_id = ?", (track_id,))
            self._conn.execute("UPDATE alerts SET track_id = NULL WHERE track_id = ?", (track_id,))
            self._conn.execute("UPDATE deterrent_actions SET track_id = NULL WHERE track_id = ?", (track_id,))
            self._conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
            self._conn.commit()

    def get_tracks(
        self,
        camera_id: Optional[str] = None,
        predator_only: bool = False,
        days: int = 7,
        limit: int = 100,
    ) -> list[dict]:
        """Fetch tracks with optional filters."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conditions = ["first_seen_at >= ?"]
        params: list = [cutoff]

        if camera_id:
            conditions.append("camera_id = ?")
            params.append(camera_id)
        if predator_only:
            conditions.append("is_predator = 1")

        where = " AND ".join(conditions)
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM tracks WHERE {where} ORDER BY first_seen_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def insert_alert(
        self,
        camera_id: str,
        alerted_at: str,
        alert_type: str,
        classes: list[str],
        track_id: Optional[int] = None,
        message: Optional[str] = None,
        snapshot_path: Optional[str] = None,
        delivered: bool = False,
        error_message: Optional[str] = None,
    ) -> int:
        """Insert an alert record. Returns the new row id."""
        classes_json = json.dumps(classes)
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO alerts
                   (track_id, camera_id, alerted_at, alert_type, classes,
                    message, snapshot_path, delivered, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    track_id, camera_id, alerted_at, alert_type, classes_json,
                    message, snapshot_path, int(delivered), error_message,
                ),
            )
            self._conn.commit()
            return cursor.lastrowid

    def get_recent_alerts(self, days: int = 7, limit: int = 50) -> list[dict]:
        """Fetch recent alerts."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """SELECT * FROM alerts WHERE alerted_at >= ?
               ORDER BY alerted_at DESC LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Deterrent actions
    # ------------------------------------------------------------------

    def insert_deterrent_action(
        self,
        track_id: int,
        camera_id: str,
        acted_at: str,
        action_type: str,
        duration_sec: Optional[float] = None,
        result: Optional[str] = None,
    ) -> int:
        """Insert a deterrent action record. Returns the new row id."""
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO deterrent_actions
                   (track_id, camera_id, acted_at, action_type, duration_sec, result)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (track_id, camera_id, acted_at, action_type, duration_sec, result),
            )
            self._conn.commit()
            return cursor.lastrowid

    def update_deterrent_result(self, track_id: int, result: str) -> None:
        """Update the result field on deterrent actions for a given track."""
        with self._lock:
            self._conn.execute(
                "UPDATE deterrent_actions SET result = ? WHERE track_id = ? AND result IS NULL",
                (result, track_id),
            )
            self._conn.commit()

    def get_deterrent_actions(self, days: int = 7, limit: int = 100) -> list[dict]:
        """Fetch recent deterrent actions."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """SELECT * FROM deterrent_actions WHERE acted_at >= ?
               ORDER BY acted_at DESC LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_deterrent_effectiveness(self, days: int = 30) -> dict:
        """Calculate deterrent success rate over the given period."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """SELECT result, COUNT(*) as cnt FROM deterrent_actions
               WHERE acted_at >= ? AND result IS NOT NULL
               GROUP BY result""",
            (cutoff,),
        ).fetchall()
        counts = {row["result"]: row["cnt"] for row in rows}
        total = sum(counts.values())
        deterred = counts.get("deterred", 0)
        return {
            "total_actions": total,
            "deterred": deterred,
            "no_effect": counts.get("no_effect", 0),
            "success_rate": round(deterred / total, 2) if total > 0 else 0.0,
            "period_days": days,
        }

    # ------------------------------------------------------------------
    # Detections — aggregation queries for reports
    # ------------------------------------------------------------------

    def get_detection_counts_by_class(
        self, target_date: Optional[str] = None, days: int = 1
    ) -> dict[str, int]:
        """Count detections grouped by class for a date range."""
        if target_date:
            start = f"{target_date}T00:00:00"
            end = f"{target_date}T23:59:59"
        else:
            end_dt = datetime.now()
            start_dt = end_dt - timedelta(days=days)
            start = start_dt.isoformat()
            end = end_dt.isoformat()

        rows = self._conn.execute(
            """SELECT class_name, COUNT(*) as cnt FROM detections
               WHERE detected_at BETWEEN ? AND ? AND suppressed = 0
               GROUP BY class_name ORDER BY cnt DESC""",
            (start, end),
        ).fetchall()
        return {row["class_name"]: row["cnt"] for row in rows}

    def get_detections_by_hour(
        self, target_date: Optional[str] = None
    ) -> dict[int, int]:
        """Count detections grouped by hour for a given date."""
        if not target_date:
            target_date = date.today().isoformat()
        start = f"{target_date}T00:00:00"
        end = f"{target_date}T23:59:59"

        rows = self._conn.execute(
            """SELECT CAST(SUBSTR(detected_at, 12, 2) AS INTEGER) as hour,
                      COUNT(*) as cnt
               FROM detections
               WHERE detected_at BETWEEN ? AND ? AND suppressed = 0
               GROUP BY hour ORDER BY hour""",
            (start, end),
        ).fetchall()
        return {row["hour"]: row["cnt"] for row in rows}

    def get_predator_tracks_for_date(self, target_date: str) -> list[dict]:
        """Fetch predator tracks for a specific date with deterrent info."""
        start = f"{target_date}T00:00:00"
        end = f"{target_date}T23:59:59"
        rows = self._conn.execute(
            """SELECT t.*, GROUP_CONCAT(DISTINCT da.action_type) as deterrent_actions_list
               FROM tracks t
               LEFT JOIN deterrent_actions da ON da.track_id = t.id
               WHERE t.is_predator = 1 AND t.first_seen_at BETWEEN ? AND ?
               GROUP BY t.id
               ORDER BY t.first_seen_at""",
            (start, end),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_species_pattern(self, class_name: str, days: int = 30) -> dict:
        """Build a species activity pattern from track history."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        tracks = self._conn.execute(
            """SELECT * FROM tracks WHERE class_name = ? AND first_seen_at >= ?
               ORDER BY first_seen_at""",
            (class_name, cutoff),
        ).fetchall()

        if not tracks:
            return {"species": class_name, "total_visits": 0}

        tracks = [dict(t) for t in tracks]
        total_visits = len(tracks)
        durations = [t["duration_sec"] for t in tracks if t["duration_sec"]]
        avg_duration = sum(durations) / len(durations) if durations else 0

        # Hour distribution
        hour_counts: dict[int, int] = {}
        for t in tracks:
            try:
                hour = int(t["first_seen_at"][11:13])
                hour_counts[hour] = hour_counts.get(hour, 0) + 1
            except (ValueError, IndexError):
                pass

        peak_hour = max(hour_counts, key=hour_counts.get) if hour_counts else None
        typical_hours = sorted(
            h for h, c in hour_counts.items() if c >= max(hour_counts.values()) * 0.3
        ) if hour_counts else []

        # Day-of-week distribution
        dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        dow_counts: dict[str, int] = {d: 0 for d in dow_names}
        for t in tracks:
            try:
                dt = datetime.fromisoformat(t["first_seen_at"])
                dow_counts[dow_names[dt.weekday()]] += 1
            except (ValueError, IndexError):
                pass

        # Deterrent stats
        deterred = sum(1 for t in tracks if t.get("outcome") == "deterred")
        with_deterrent = sum(1 for t in tracks if t.get("deterrent_used"))

        # Last seen
        last_seen = tracks[-1]["first_seen_at"] if tracks else None

        # Trend: compare last 7 days vs previous 7 days
        now = datetime.now()
        week_ago = (now - timedelta(days=7)).isoformat()
        two_weeks_ago = (now - timedelta(days=14)).isoformat()
        recent_count = sum(1 for t in tracks if t["first_seen_at"] >= week_ago)
        prev_count = sum(
            1 for t in tracks
            if two_weeks_ago <= t["first_seen_at"] < week_ago
        )
        if recent_count > prev_count * 1.2:
            trend = "increasing"
        elif recent_count < prev_count * 0.8:
            trend = "decreasing"
        else:
            trend = "stable"

        return {
            "species": class_name,
            "total_visits": total_visits,
            "total_duration_minutes": round(sum(durations) / 60, 1),
            "avg_visit_duration_seconds": round(avg_duration, 1),
            "typical_hours": typical_hours,
            "peak_hour": peak_hour,
            "visits_by_day_of_week": dow_counts,
            "deterrent_success_rate": round(deterred / with_deterrent, 2) if with_deterrent else 0.0,
            "last_seen": last_seen,
            "trend": trend,
            "period_days": days,
        }

    # ------------------------------------------------------------------
    # Daily summaries
    # ------------------------------------------------------------------

    def get_daily_summary(self, summary_date: str) -> Optional[dict]:
        """Fetch a daily summary by date string (YYYY-MM-DD)."""
        row = self._conn.execute(
            "SELECT * FROM daily_summaries WHERE summary_date = ?",
            (summary_date,),
        ).fetchone()
        return dict(row) if row else None

    def insert_daily_summary(
        self,
        summary_date: str,
        total_detections: int = 0,
        predator_detections: int = 0,
        unique_species: Optional[list] = None,
        alerts_sent: int = 0,
        deterrents_activated: int = 0,
        peak_activity_hour: Optional[int] = None,
        activity_by_hour: Optional[dict] = None,
        species_counts: Optional[dict] = None,
        predator_tracks: Optional[list] = None,
        deterrent_success_rate: Optional[float] = None,
        summary_text: Optional[str] = None,
    ) -> int:
        """Insert or replace a daily summary. Returns the row id."""
        with self._lock:
            cursor = self._conn.execute(
                """INSERT OR REPLACE INTO daily_summaries
                   (summary_date, total_detections, predator_detections,
                    unique_species, alerts_sent, deterrents_activated,
                    peak_activity_hour, activity_by_hour, species_counts,
                    predator_tracks, deterrent_success_rate, summary_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    summary_date, total_detections, predator_detections,
                    json.dumps(unique_species) if unique_species else None,
                    alerts_sent, deterrents_activated,
                    peak_activity_hour,
                    json.dumps(activity_by_hour) if activity_by_hour else None,
                    json.dumps(species_counts) if species_counts else None,
                    json.dumps(predator_tracks) if predator_tracks else None,
                    deterrent_success_rate, summary_text,
                ),
            )
            self._conn.commit()
            return cursor.lastrowid

    # ------------------------------------------------------------------
    # eBird sightings
    # ------------------------------------------------------------------

    def insert_ebird_sighting(
        self,
        species_code: str,
        common_name: str,
        threat_level: str,
        polled_at: str,
        location_name: Optional[str] = None,
        lat: Optional[float] = None,
        lng: Optional[float] = None,
        observed_at: Optional[str] = None,
        count: int = 1,
    ) -> int:
        """Insert an eBird raptor sighting. Returns the row id."""
        with self._lock:
            cursor = self._conn.execute(
                """INSERT INTO ebird_sightings
                   (species_code, common_name, threat_level, polled_at,
                    location_name, lat, lng, observed_at, count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    species_code, common_name, threat_level, polled_at,
                    location_name, lat, lng, observed_at, count,
                ),
            )
            self._conn.commit()
            return cursor.lastrowid

    def get_recent_ebird_sightings(self, days: int = 7, limit: int = 50) -> list[dict]:
        """Fetch recent eBird raptor sightings."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """SELECT * FROM ebird_sightings WHERE polled_at >= ?
               ORDER BY polled_at DESC LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_ebird_alert_sent(self, species_code: str, polled_at: str) -> None:
        """Mark eBird sightings as alerted for a given species and poll time."""
        with self._lock:
            self._conn.execute(
                """UPDATE ebird_sightings SET alert_sent = 1
                   WHERE species_code = ? AND polled_at = ?""",
                (species_code, polled_at),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------

    def backup(self) -> Optional[str]:
        """Copy the database file to the backup directory. Returns backup path or None."""
        today_str = date.today().isoformat()
        backup_path = self._backup_dir / f"guardian-{today_str}.db"

        if backup_path.exists():
            log.debug("Backup already exists for today: %s", backup_path)
            return str(backup_path)

        try:
            # Use SQLite backup API for a consistent copy
            backup_conn = sqlite3.connect(str(backup_path))
            with self._lock:
                self._conn.backup(backup_conn)
            backup_conn.close()
            log.info("Database backed up to %s", backup_path)
            return str(backup_path)
        except Exception as exc:
            log.error("Database backup failed: %s", exc)
            return None

    def cleanup_old_backups(self) -> int:
        """Remove backup files older than retention_days. Returns count removed."""
        if self._retention_days <= 0:
            return 0

        cutoff = date.today() - timedelta(days=self._retention_days)
        removed = 0
        for f in self._backup_dir.glob("guardian-*.db"):
            try:
                # Extract date from filename: guardian-YYYY-MM-DD.db
                date_str = f.stem.replace("guardian-", "")
                file_date = date.fromisoformat(date_str)
                if file_date < cutoff:
                    f.unlink()
                    removed += 1
                    log.info("Removed old backup: %s", f.name)
            except (ValueError, OSError) as exc:
                log.warning("Could not process backup file %s: %s", f.name, exc)
        return removed

    # ------------------------------------------------------------------
    # Image archive (v2.25.0) — read + mutate helpers backing /api/v1/images/*.
    # Public read helpers always filter has_concerns = 0; the `_review` variants
    # expose everything for the Boss-only review UI. Mutations take an
    # `_editing=True` path that writes both the target row and an audit row
    # inside a single BEGIN IMMEDIATE transaction.
    # ------------------------------------------------------------------

    _IMG_PUBLIC_COLS = (
        "id, camera_id, ts, image_path, image_tier, sha256, width, height, "
        "scene, bird_count, activity, lighting, composition, image_quality, "
        "share_worth, any_special_chick, apparent_age_days, "
        "individuals_visible_csv, vlm_json"
    )

    def _img_row_to_dict(self, row: sqlite3.Row) -> dict:
        """Shape a raw image_archive row into the API's public row dict.
        Parses caption_draft + share_reason + individuals_visible out of
        vlm_json; normalizes apparent_age_days sentinel -1 → None; always
        omits concerns[] even if present in vlm_json (defense-in-depth #3)."""
        d = dict(row)
        vlm_raw = d.pop("vlm_json", None) or "{}"
        try:
            vlm = json.loads(vlm_raw)
        except (ValueError, TypeError):
            vlm = {}
        # Never surface concerns on a public shape. caption_draft + share_reason
        # are safe per plan §2.a JSON contract (surface on list) but the caller
        # can strip share_reason too if they want §1.c's stricter reading.
        d["caption_draft"] = vlm.get("caption_draft", "") or ""
        d["share_reason"] = vlm.get("share_reason", "") or ""
        # Normalize individuals_visible: prefer denormalized CSV, fall back to
        # the JSON list if CSV is missing. Empty string → empty list.
        csv = d.pop("individuals_visible_csv", None) or ""
        if csv:
            d["individuals_visible"] = [s for s in csv.split(",") if s]
        else:
            d["individuals_visible"] = list(vlm.get("individuals_visible", []) or [])
        # Sentinel: -1 means "n/a"; normalize to None for the type contract.
        if d.get("apparent_age_days") == -1:
            d["apparent_age_days"] = None
        # any_special_chick is 0/1 in the DB; publish as bool.
        d["any_special_chick"] = bool(d.get("any_special_chick", 0))
        return d

    def _img_row_to_review_dict(self, row: sqlite3.Row) -> dict:
        """Shape a raw image_archive row into the review UI's full dict.
        Includes concerns[], vlm_json (raw), has_concerns. Only ever returned
        behind bearer auth."""
        d = self._img_row_to_dict(row)
        raw = dict(row)
        try:
            vlm = json.loads(raw.get("vlm_json") or "{}")
        except (ValueError, TypeError):
            vlm = {}
        d["concerns"] = list(vlm.get("concerns", []) or [])
        d["has_concerns"] = bool(raw.get("has_concerns", 0))
        d["vlm_json"] = raw.get("vlm_json")
        d["retained_until"] = raw.get("retained_until")
        return d

    @staticmethod
    def _individuals_clause(values: list[str]) -> tuple[str, list[str]]:
        """Build a safe CSV-contains OR-joined WHERE clause for
        individuals_visible_csv. Uses the ',csv,' LIKE '%,name,%' idiom so a
        name like 'adult' doesn't collide with 'adult-survivor'."""
        if not values:
            return "", []
        parts = ["',' || IFNULL(individuals_visible_csv,'') || ',' LIKE '%,' || ? || ',%'"] * len(values)
        return "(" + " OR ".join(parts) + ")", list(values)

    def query_images(
        self,
        *,
        tiers: Optional[list[str]] = None,         # e.g. ['strong'] or ['strong','decent']
        cameras: Optional[list[str]] = None,
        scenes: Optional[list[str]] = None,
        activities: Optional[list[str]] = None,
        individuals: Optional[list[str]] = None,
        since_iso: Optional[str] = None,
        until_iso: Optional[str] = None,
        include_concerns: bool = False,            # True only for /review/queue
        only_concerns: bool = False,               # /review/queue with switch
        only_unreviewed: bool = False,             # /review/queue with switch
        require_image_path: bool = True,           # public endpoints exclude NULL paths
        order: str = "newest",                     # 'newest' | 'oldest' | 'random'
        cursor_ts: Optional[str] = None,
        cursor_id: Optional[int] = None,
        limit: int = 24,
    ) -> list[sqlite3.Row]:
        """Single parameterized query backing /gems, /recent, /review/queue."""
        wheres: list[str] = []
        params: list = []

        if tiers:
            wheres.append("image_tier IN (" + ",".join("?" * len(tiers)) + ")")
            params.extend(tiers)
        if cameras:
            wheres.append("camera_id IN (" + ",".join("?" * len(cameras)) + ")")
            params.extend(cameras)
        if scenes:
            wheres.append("scene IN (" + ",".join("?" * len(scenes)) + ")")
            params.extend(scenes)
        if activities:
            wheres.append("activity IN (" + ",".join("?" * len(activities)) + ")")
            params.extend(activities)
        if individuals:
            clause, vals = self._individuals_clause(individuals)
            if clause:
                wheres.append(clause)
                params.extend(vals)
        if since_iso:
            wheres.append("ts >= ?")
            params.append(since_iso)
        if until_iso:
            wheres.append("ts <= ?")
            params.append(until_iso)
        if require_image_path:
            wheres.append("image_path IS NOT NULL")
        if not include_concerns:
            wheres.append("has_concerns = 0")
        elif only_concerns:
            wheres.append("has_concerns = 1")
        if only_unreviewed:
            wheres.append("id NOT IN (SELECT target_image_id FROM image_archive_edits)")

        # Cursor pagination — (ts, id) lexicographic compare.
        if cursor_ts is not None and cursor_id is not None and order in ("newest", "oldest"):
            cmp = "<" if order == "newest" else ">"
            wheres.append(f"(ts {cmp} ? OR (ts = ? AND id {cmp} ?))")
            params.extend([cursor_ts, cursor_ts, cursor_id])

        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        if order == "random":
            order_sql = "ORDER BY RANDOM()"
        elif order == "oldest":
            order_sql = "ORDER BY ts ASC, id ASC"
        else:
            order_sql = "ORDER BY ts DESC, id DESC"

        sql = f"""SELECT * FROM image_archive {where_sql} {order_sql} LIMIT ?"""
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return rows

    def count_images(
        self,
        *,
        tiers: Optional[list[str]] = None,
        cameras: Optional[list[str]] = None,
        scenes: Optional[list[str]] = None,
        activities: Optional[list[str]] = None,
        individuals: Optional[list[str]] = None,
        since_iso: Optional[str] = None,
        until_iso: Optional[str] = None,
        include_concerns: bool = False,
        only_concerns: bool = False,
        only_unreviewed: bool = False,
        require_image_path: bool = True,
        cap: int = 10000,
    ) -> tuple[int, bool]:
        """Cheap count bounded at `cap`. Returns (count, estimated).
        estimated=True means the true count is >= cap and we stopped
        counting to keep the hot path fast."""
        wheres: list[str] = []
        params: list = []
        if tiers:
            wheres.append("image_tier IN (" + ",".join("?" * len(tiers)) + ")")
            params.extend(tiers)
        if cameras:
            wheres.append("camera_id IN (" + ",".join("?" * len(cameras)) + ")")
            params.extend(cameras)
        if scenes:
            wheres.append("scene IN (" + ",".join("?" * len(scenes)) + ")")
            params.extend(scenes)
        if activities:
            wheres.append("activity IN (" + ",".join("?" * len(activities)) + ")")
            params.extend(activities)
        if individuals:
            clause, vals = self._individuals_clause(individuals)
            if clause:
                wheres.append(clause)
                params.extend(vals)
        if since_iso:
            wheres.append("ts >= ?")
            params.append(since_iso)
        if until_iso:
            wheres.append("ts <= ?")
            params.append(until_iso)
        if require_image_path:
            wheres.append("image_path IS NOT NULL")
        if not include_concerns:
            wheres.append("has_concerns = 0")
        elif only_concerns:
            wheres.append("has_concerns = 1")
        if only_unreviewed:
            wheres.append("id NOT IN (SELECT target_image_id FROM image_archive_edits)")
        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = f"SELECT COUNT(*) FROM (SELECT 1 FROM image_archive {where_sql} LIMIT ?) t"
        params.append(cap)
        (n,) = self._conn.execute(sql, params).fetchone()
        return int(n), int(n) >= cap

    def get_image(self, image_id: int) -> Optional[sqlite3.Row]:
        """Fetch one row by id; returns None if missing. Caller applies
        has_concerns / share_worth / image_path NULL checks per endpoint."""
        row = self._conn.execute(
            "SELECT * FROM image_archive WHERE id = ?", (image_id,)
        ).fetchone()
        return row

    def get_related_gems(self, image_id: int, limit: int = 4) -> list[sqlite3.Row]:
        """Up to `limit` other strong-tier gem IDs from the same camera
        within a ±2h window, excluding image_id itself. Powers /gems/{id}.related."""
        base = self.get_image(image_id)
        if base is None:
            return []
        rows = self._conn.execute(
            """SELECT * FROM image_archive
               WHERE camera_id = ?
                 AND id <> ?
                 AND share_worth = 'strong'
                 AND has_concerns = 0
                 AND image_path IS NOT NULL
                 AND ts BETWEEN datetime(?, '-2 hours') AND datetime(?, '+2 hours')
               ORDER BY ABS(julianday(ts) - julianday(?)) ASC
               LIMIT ?""",
            (base["camera_id"], image_id, base["ts"], base["ts"], base["ts"], limit),
        ).fetchall()
        return rows

    def get_image_stats(self, since_iso: str, until_iso: str) -> dict:
        """Aggregate counts for the /stats endpoint. All counts filter
        has_concerns = 0 (these are public stats, not the full dataset)."""
        base = "FROM image_archive WHERE has_concerns = 0 AND ts >= ? AND ts <= ?"
        p = (since_iso, until_iso)
        c = self._conn.execute
        total = c(f"SELECT COUNT(*) {base}", p).fetchone()[0]
        by_tier = {r[0]: r[1] for r in c(f"SELECT image_tier, COUNT(*) {base} GROUP BY image_tier", p).fetchall()}
        by_camera = {r[0]: r[1] for r in c(f"SELECT camera_id, COUNT(*) {base} GROUP BY camera_id", p).fetchall()}
        by_scene = {r[0]: r[1] for r in c(f"SELECT scene, COUNT(*) {base} GROUP BY scene", p).fetchall() if r[0]}
        by_activity = {r[0]: r[1] for r in c(f"SELECT activity, COUNT(*) {base} GROUP BY activity", p).fetchall() if r[0]}
        birdadette = c(
            f"SELECT COUNT(*) {base} AND ',' || IFNULL(individuals_visible_csv,'') || ',' LIKE '%,birdadette,%'",
            p,
        ).fetchone()[0]
        # oldest / newest ignore the since/until bounds — they describe the
        # dataset as a whole so the caller can tell whether the pipeline is alive.
        oldest = c("SELECT MIN(ts) FROM image_archive WHERE has_concerns = 0").fetchone()[0]
        newest = c("SELECT MAX(ts) FROM image_archive WHERE has_concerns = 0").fetchone()[0]
        return {
            "range": {"since": since_iso, "until": until_iso},
            "total_rows": int(total),
            "by_tier": by_tier,
            "by_camera": by_camera,
            "by_scene": by_scene,
            "by_activity": by_activity,
            "birdadette_sightings": int(birdadette),
            "oldest_ts": oldest,
            "newest_ts": newest,
        }

    def count_all_images(self) -> int:
        """Total rows (unfiltered) — used only by /ping for liveness signal."""
        return int(self._conn.execute("SELECT COUNT(*) FROM image_archive").fetchone()[0])

    def get_edits(
        self,
        *,
        since_iso: Optional[str] = None,
        until_iso: Optional[str] = None,
        action: Optional[str] = None,
        cursor_ts: Optional[str] = None,
        cursor_id: Optional[int] = None,
        limit: int = 50,
    ) -> list[dict]:
        wheres: list[str] = []
        params: list = []
        if since_iso:
            wheres.append("ts >= ?"); params.append(since_iso)
        if until_iso:
            wheres.append("ts <= ?"); params.append(until_iso)
        if action:
            wheres.append("action = ?"); params.append(action)
        if cursor_ts is not None and cursor_id is not None:
            wheres.append("(ts < ? OR (ts = ? AND id < ?))")
            params.extend([cursor_ts, cursor_ts, cursor_id])
        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        sql = f"""SELECT * FROM image_archive_edits {where_sql}
                  ORDER BY ts DESC, id DESC LIMIT ?"""
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def apply_review_action(
        self,
        *,
        image_id: int,
        action: str,               # 'promote' | 'demote' | 'flag' | 'unflag' | 'delete'
        actor: str = "boss",
        note: Optional[str] = None,
        request_id: Optional[str] = None,
        new_share_worth: Optional[str] = None,
        new_has_concerns: Optional[int] = None,
        new_vlm_json: Optional[str] = None,
        new_image_path_null: bool = False,
    ) -> dict:
        """Apply one review mutation to the target row + write an audit row,
        both inside a single BEGIN IMMEDIATE transaction. Returns {pre, post}
        dicts so the caller can report what changed (and reverse FS ops on a
        partial failure).

        The caller is responsible for hardlink / unlink FS work BEFORE calling
        this, so that if the DB commit fails the FS and DB can be reconciled.
        This method does NOT touch the filesystem."""
        with self._lock:
            pre_row = self._conn.execute(
                "SELECT id, share_worth, has_concerns, image_path, vlm_json "
                "FROM image_archive WHERE id = ?",
                (image_id,),
            ).fetchone()
            if pre_row is None:
                raise KeyError(f"image {image_id} not found")

            pre_state = {
                "share_worth": pre_row["share_worth"],
                "has_concerns": pre_row["has_concerns"],
                "image_path": pre_row["image_path"],
            }
            post_state = dict(pre_state)

            sets: list[str] = []
            params: list = []
            if new_share_worth is not None:
                sets.append("share_worth = ?"); params.append(new_share_worth)
                post_state["share_worth"] = new_share_worth
            if new_has_concerns is not None:
                sets.append("has_concerns = ?"); params.append(new_has_concerns)
                post_state["has_concerns"] = new_has_concerns
            if new_vlm_json is not None:
                sets.append("vlm_json = ?"); params.append(new_vlm_json)
            if new_image_path_null:
                sets.append("image_path = NULL")
                post_state["image_path"] = None

            try:
                self._conn.execute("BEGIN IMMEDIATE")
                if sets:
                    self._conn.execute(
                        f"UPDATE image_archive SET {', '.join(sets)} WHERE id = ?",
                        (*params, image_id),
                    )
                self._conn.execute(
                    """INSERT INTO image_archive_edits
                       (target_image_id, action, actor, note, request_id,
                        pre_state, post_state)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        image_id, action, actor, note, request_id,
                        json.dumps(pre_state), json.dumps(post_state),
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            return {"pre": pre_state, "post": post_state}
