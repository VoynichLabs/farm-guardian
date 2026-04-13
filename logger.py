# Author: Claude Opus 4.6 (updated), Cascade (Claude Sonnet 4) (original)
# Date: 13-April-2026 (v2.17.0 — vision refinement removed)
# PURPOSE: Structured event logging for Farm Guardian. Dual-write: persists detection events
#          to both daily-rotated JSONL log files (v1 legacy) and SQLite database (v2) via
#          the database.py module. Saves snapshot images to daily subdirectories under the
#          configured events directory. Each event includes timestamp, camera name, detection
#          class, confidence score, bounding box, snapshot path, and optional track id.
#          Backward-compatible: if no DB instance is provided, behaves exactly as v1.
# SRP/DRY check: Pass — single responsibility is event persistence (log + snapshot + DB).

import json
import logging
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from PIL import Image
import numpy as np

log = logging.getLogger("guardian.logger")


class EventLogger:
    """Persists detection events as structured JSON logs, snapshots, and DB records."""

    def __init__(self, config: dict, db=None):
        """
        Args:
            config: Full guardian config dict.
            db: Optional GuardianDB instance for v2 dual-write. If None, JSONL-only (v1 mode).
        """
        storage = config.get("storage", {})
        self._events_dir = Path(storage.get("events_dir", "events"))
        self._max_days = storage.get("max_days_retained", 30)
        self._save_all = storage.get("save_all_detections", False)
        self._save_predator_snapshots = storage.get("save_predator_snapshots", True)
        self._predator_classes = set(
            config.get("detection", {}).get("predator_classes", [])
        )
        self._db = db

        # Ensure base events directory exists
        self._events_dir.mkdir(parents=True, exist_ok=True)
        mode = "DB + JSONL" if db else "JSONL only"
        log.info("EventLogger initialized — events_dir=%s, mode=%s", self._events_dir, mode)

    def _daily_dir(self, dt: Optional[datetime] = None) -> Path:
        """Return (and create) the daily subdirectory for the given datetime."""
        day_str = (dt or datetime.now()).strftime("%Y-%m-%d")
        day_dir = self._events_dir / day_str
        day_dir.mkdir(parents=True, exist_ok=True)
        return day_dir

    def _log_path(self, day_dir: Path) -> Path:
        """Path to the JSON event log inside a daily directory."""
        return day_dir / "events.jsonl"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_event(
        self,
        camera_name: str,
        detection_class: str,
        confidence: float,
        bbox: tuple,
        frame: Optional[np.ndarray] = None,
        extra: Optional[dict] = None,
        bbox_area_pct: float = 0.0,
        is_predator: Optional[bool] = None,
        track_id: Optional[int] = None,
        model_name: str = "yolov8n",
    ) -> dict:
        """
        Record a detection event. Writes to JSONL (legacy) and SQLite (v2).
        Returns the event dict that was written.

        Args:
            camera_name: which camera produced the detection
            detection_class: YOLO class label
            confidence: model confidence 0-1
            bbox: (x1, y1, x2, y2) pixel coordinates
            frame: numpy array (BGR, from OpenCV) — snapshot is saved from this
            extra: any additional metadata to attach
            bbox_area_pct: bounding box area as percentage of frame
            is_predator: explicit predator flag (if None, checks predator_classes)
            track_id: associated track id from tracker module
            model_name: which model produced this detection
        """
        now = datetime.now()
        day_dir = self._daily_dir(now)

        # Determine predator status
        if is_predator is None:
            is_predator = detection_class in self._predator_classes

        # Determine whether to save a snapshot
        should_save_snapshot = frame is not None and (
            self._save_all or (is_predator and self._save_predator_snapshots)
        )

        snapshot_path: Optional[str] = None
        if should_save_snapshot:
            snapshot_path = self._save_snapshot(frame, day_dir, now, detection_class)

        event = {
            "timestamp": now.isoformat(),
            "camera": camera_name,
            "class": detection_class,
            "confidence": round(confidence, 4),
            "bbox": list(bbox),
            "is_predator": is_predator,
            "snapshot": snapshot_path,
        }
        if track_id is not None:
            event["track_id"] = track_id
        if model_name != "yolov8n":
            event["model"] = model_name
        if extra:
            event["extra"] = extra

        # 1. Append to JSONL log (v1 legacy)
        log_file = self._log_path(day_dir)
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except OSError as exc:
            log.error("Failed to write event log %s: %s", log_file, exc)

        # 2. Write to SQLite database (v2)
        if self._db:
            try:
                db_id = self._db.insert_detection(
                    camera_id=camera_name,
                    detected_at=now.isoformat(),
                    class_name=detection_class,
                    confidence=confidence,
                    bbox=bbox,
                    bbox_area_pct=bbox_area_pct,
                    is_predator=is_predator,
                    track_id=track_id,
                    snapshot_path=snapshot_path,
                    model_name=model_name,
                )
                event["db_id"] = db_id
            except Exception as exc:
                log.error("Failed to write detection to DB: %s", exc)

        log.debug("Logged event: %s %.2f on %s", detection_class, confidence, camera_name)
        return event

    def _save_snapshot(
        self, frame: np.ndarray, day_dir: Path, dt: datetime, label: str
    ) -> Optional[str]:
        """Save a BGR frame as a JPEG snapshot. Returns the relative path or None on failure."""
        ts_str = dt.strftime("%H%M%S_%f")[:-3]  # HH:MM:SS_mmm
        filename = f"{ts_str}_{label}.jpg"
        filepath = day_dir / filename
        try:
            # Convert BGR (OpenCV) -> RGB for Pillow
            rgb = frame[:, :, ::-1]
            img = Image.fromarray(rgb)
            img.save(str(filepath), quality=85)
            log.debug("Snapshot saved: %s", filepath)
            return str(filepath)
        except Exception as exc:
            log.error("Failed to save snapshot %s: %s", filepath, exc)
            return None

    def cleanup_old_events(self) -> int:
        """
        Remove daily subdirectories older than max_days_retained.
        Returns the number of directories removed.
        """
        if self._max_days <= 0:
            return 0

        today = date.today()
        removed = 0
        try:
            for entry in sorted(self._events_dir.iterdir()):
                if not entry.is_dir():
                    continue
                try:
                    dir_date = date.fromisoformat(entry.name)
                except ValueError:
                    continue  # skip non-date directories
                age_days = (today - dir_date).days
                if age_days > self._max_days:
                    _rmtree(entry)
                    removed += 1
                    log.info("Cleaned up old event dir: %s (%d days old)", entry.name, age_days)
        except OSError as exc:
            log.error("Error during event cleanup: %s", exc)
        return removed


def _rmtree(path: Path) -> None:
    """Recursively remove a directory tree. Thin wrapper to keep imports minimal."""
    import shutil
    shutil.rmtree(path, ignore_errors=True)
