# Author: Cascade (Claude Sonnet 4)
# Date: 01-April-2026
# PURPOSE: Structured JSON event logging for Farm Guardian. Writes detection events
#          to daily-rotated JSON log files and saves snapshot images to daily subdirectories
#          under the configured events directory. Each event includes timestamp, camera name,
#          detection class, confidence score, bounding box, and snapshot path.
# SRP/DRY check: Pass — single responsibility is event persistence (log + snapshot).

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
    """Persists detection events as structured JSON logs and snapshot images."""

    def __init__(self, config: dict):
        storage = config.get("storage", {})
        self._events_dir = Path(storage.get("events_dir", "events"))
        self._max_days = storage.get("max_days_retained", 30)
        self._save_all = storage.get("save_all_detections", False)
        self._save_predator_snapshots = storage.get("save_predator_snapshots", True)
        self._predator_classes = set(
            config.get("detection", {}).get("predator_classes", [])
        )

        # Ensure base events directory exists
        self._events_dir.mkdir(parents=True, exist_ok=True)
        log.info("EventLogger initialized — events_dir=%s", self._events_dir)

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
    ) -> dict:
        """
        Record a detection event. Saves a structured JSON line and optionally
        a snapshot image. Returns the event dict that was written.

        Args:
            camera_name: which camera produced the detection
            detection_class: YOLO class label (e.g. "bird", "cat")
            confidence: model confidence 0-1
            bbox: (x1, y1, x2, y2) pixel coordinates
            frame: numpy array (BGR, from OpenCV) — snapshot is saved from this
            extra: any additional metadata to attach
        """
        now = datetime.now()
        day_dir = self._daily_dir(now)

        # Determine whether to save a snapshot
        is_predator = detection_class in self._predator_classes
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
        if extra:
            event["extra"] = extra

        # Append to JSONL log
        log_file = self._log_path(day_dir)
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
        except OSError as exc:
            log.error("Failed to write event log %s: %s", log_file, exc)

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
