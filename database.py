# Author: Claude Opus 4.6
# Date: 03-April-2026
# PURPOSE: SQLite abstraction layer for Farm Guardian v2. All database reads/writes
#          go through this module. Creates and manages the guardian.db schema with
#          tables for cameras, detections, tracks, alerts, deterrent actions, PTZ presets,
#          daily summaries, and eBird sightings. Uses WAL mode for concurrent read access
#          from dashboard/API while the detection pipeline writes. Provides daily backup
#          to data/backups/. No ORM — raw parameterized SQL for portability to PostgreSQL.
# SRP/DRY check: Pass — single responsibility is structured data persistence.

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
