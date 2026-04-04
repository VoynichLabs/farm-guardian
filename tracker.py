# Author: Claude Opus 4.6
# Date: 04-April-2026
# PURPOSE: Animal visit tracking for Farm Guardian v2. Converts a stream of individual
#          YOLO detections into meaningful "tracks" (visits). A track represents one
#          animal's continuous presence in the monitored area. New tracks open when a
#          class appears that isn't in any active track; detections merge into existing
#          tracks within a timeout window (default 60s). Tracks close when the timeout
#          expires with no matching detections. Calculates duration, detection count,
#          max/avg confidence per track. Writes track lifecycle events to the DB.
#          Provides hooks for deterrent outcome tracking (Phase 3).
# SRP/DRY check: Pass — single responsibility is detection-to-track aggregation.

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from database import GuardianDB
from detect import Detection

log = logging.getLogger("guardian.tracker")


@dataclass
class ActiveTrack:
    """In-memory representation of an active (open) animal visit."""
    track_id: int                    # DB row id
    camera_id: str
    class_name: str
    first_seen: float                # monotonic time
    last_seen: float                 # monotonic time
    first_seen_at: str               # ISO 8601 for DB
    last_seen_at: str                # ISO 8601 for DB
    detection_count: int = 0
    max_confidence: float = 0.0
    confidence_sum: float = 0.0      # for computing avg
    is_predator: bool = False

    @property
    def avg_confidence(self) -> float:
        return self.confidence_sum / self.detection_count if self.detection_count else 0.0

    @property
    def duration_sec(self) -> float:
        return self.last_seen - self.first_seen


class AnimalTracker:
    """Groups individual detections into animal visit tracks."""

    def __init__(self, config: dict, db: GuardianDB):
        tracking_cfg = config.get("tracking", {})
        self._timeout = tracking_cfg.get("track_timeout_seconds", 60)
        self._min_detections = tracking_cfg.get("min_detections_for_track", 2)
        self._db = db

        # Active tracks keyed by (camera_id, class_name)
        # Only one track per class per camera at a time
        self._active: dict[tuple[str, str], ActiveTrack] = {}
        self._lock = threading.Lock()

        log.info(
            "AnimalTracker initialized — timeout=%ds, min_detections=%d",
            self._timeout, self._min_detections,
        )

    def process_detection(
        self,
        camera_id: str,
        detection: Detection,
        detected_at: Optional[str] = None,
    ) -> Optional[ActiveTrack]:
        """
        Process a new detection and assign it to a track.

        Creates a new track if no active track exists for this (camera, class),
        or merges into the existing active track if within the timeout window.

        Returns the ActiveTrack, or None if tracking failed.
        """
        now_mono = time.monotonic()
        now_iso = detected_at or datetime.now().isoformat()

        # Close expired tracks first
        self._close_expired_tracks(now_mono)

        key = (camera_id, detection.class_name)

        with self._lock:
            track = self._active.get(key)

            if track:
                # Merge into existing track
                track.last_seen = now_mono
                track.last_seen_at = now_iso
                track.detection_count += 1
                track.max_confidence = max(track.max_confidence, detection.confidence)
                track.confidence_sum += detection.confidence

                # Update DB
                try:
                    self._db.update_track(
                        track_id=track.track_id,
                        last_seen_at=now_iso,
                        detection_count=track.detection_count,
                        max_confidence=track.max_confidence,
                        avg_confidence=track.avg_confidence,
                        duration_sec=track.duration_sec,
                    )
                except Exception as exc:
                    log.error("Failed to update track %d: %s", track.track_id, exc)

                log.debug(
                    "Track %d updated — %s on %s (count=%d, %.1fs)",
                    track.track_id, detection.class_name, camera_id,
                    track.detection_count, track.duration_sec,
                )
                return track

            else:
                # Create new track
                try:
                    track_id = self._db.insert_track(
                        camera_id=camera_id,
                        class_name=detection.class_name,
                        first_seen_at=now_iso,
                        is_predator=detection.is_predator,
                        max_confidence=detection.confidence,
                    )
                except Exception as exc:
                    log.error("Failed to create track for %s: %s", detection.class_name, exc)
                    return None

                track = ActiveTrack(
                    track_id=track_id,
                    camera_id=camera_id,
                    class_name=detection.class_name,
                    first_seen=now_mono,
                    last_seen=now_mono,
                    first_seen_at=now_iso,
                    last_seen_at=now_iso,
                    detection_count=1,
                    max_confidence=detection.confidence,
                    confidence_sum=detection.confidence,
                    is_predator=detection.is_predator,
                )
                self._active[key] = track

                log.info(
                    "New track %d — %s on %s (predator=%s)",
                    track_id, detection.class_name, camera_id, detection.is_predator,
                )
                return track

    def _close_expired_tracks(self, now_mono: float) -> list[ActiveTrack]:
        """Close any tracks that have exceeded the timeout. Returns closed tracks."""
        closed = []
        with self._lock:
            expired_keys = [
                key for key, track in self._active.items()
                if (now_mono - track.last_seen) > self._timeout
            ]
            for key in expired_keys:
                track = self._active.pop(key)
                closed.append(track)

        # Finalize closed tracks in DB (outside lock to avoid holding it during I/O)
        for track in closed:
            # Skip ghost tracks — single-frame detections that never met the dwell
            # threshold. These are typically false positives (bear/dog flickers) that
            # pollute the DB with 0.0s duration, 1-detection tracks.
            if track.detection_count < self._min_detections:
                log.debug(
                    "Ghost track %d discarded — %s on %s (detections=%d < min=%d)",
                    track.track_id, track.class_name, track.camera_id,
                    track.detection_count, self._min_detections,
                )
                try:
                    self._db.delete_track(track.track_id)
                except Exception as exc:
                    log.error("Failed to delete ghost track %d: %s", track.track_id, exc)
                continue

            outcome = "left"  # default — animal left on its own
            try:
                self._db.close_track(track.track_id, outcome=outcome)
            except Exception as exc:
                log.error("Failed to close track %d: %s", track.track_id, exc)

            log.info(
                "Track %d closed — %s on %s (duration=%.1fs, detections=%d, outcome=%s)",
                track.track_id, track.class_name, track.camera_id,
                track.duration_sec, track.detection_count, outcome,
            )

        return closed

    def get_active_tracks(self, camera_id: Optional[str] = None) -> list[ActiveTrack]:
        """Return all currently active tracks, optionally filtered by camera."""
        # Expire stale tracks first
        self._close_expired_tracks(time.monotonic())

        with self._lock:
            if camera_id:
                return [
                    t for t in self._active.values()
                    if t.camera_id == camera_id
                ]
            return list(self._active.values())

    def get_track_for_detection(
        self, camera_id: str, class_name: str
    ) -> Optional[ActiveTrack]:
        """Get the active track for a specific camera + class, if one exists."""
        with self._lock:
            return self._active.get((camera_id, class_name))

    def set_track_outcome(
        self, track_id: int, outcome: str, deterrent_used: Optional[list] = None
    ) -> None:
        """Set the outcome of a track (called by deterrent module in Phase 3)."""
        try:
            self._db.update_track(
                track_id=track_id,
                last_seen_at=datetime.now().isoformat(),
                detection_count=0,  # won't overwrite — see update_track SQL
                max_confidence=0.0,
                avg_confidence=0.0,
                outcome=outcome,
                deterrent_used=deterrent_used,
            )
        except Exception as exc:
            log.error("Failed to set outcome for track %d: %s", track_id, exc)

    def close_all(self) -> None:
        """Close all active tracks on shutdown."""
        with self._lock:
            tracks = list(self._active.values())
            self._active.clear()

        for track in tracks:
            try:
                self._db.close_track(track.track_id, outcome="shutdown")
            except Exception as exc:
                log.error("Failed to close track %d on shutdown: %s", track.track_id, exc)

        if tracks:
            log.info("Closed %d active tracks on shutdown", len(tracks))
