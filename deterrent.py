# Author: Claude Opus 4.6
# Date: 03-April-2026
# PURPOSE: Automated deterrent response engine for Farm Guardian v2 (Phase 3).
#          Decides what actions to take when a predator is detected, based on per-species
#          escalation rules in config.json. Four levels: 0=log-only, 1=spotlight,
#          2=spotlight+audio, 3=spotlight+siren+audio. Enforces cooldown between
#          activations per species. Tracks effectiveness — did the animal leave within
#          the configured window? Pauses PTZ patrol during active deterrence and resumes
#          after. Logs all actions to the deterrent_actions DB table.
# SRP/DRY check: Pass — single responsibility is deterrent decision-making and execution.

import logging
import threading
import time
from datetime import datetime
from typing import Optional

from camera_control import CameraController
from database import GuardianDB
from tracker import ActiveTrack

log = logging.getLogger("guardian.deterrent")

# Default response rules if not in config
_DEFAULT_RULES = {
    "hawk":       {"level": 2, "actions": ["spotlight", "audio_alarm"]},
    "bobcat":     {"level": 3, "actions": ["spotlight", "siren", "audio_alarm"]},
    "coyote":     {"level": 3, "actions": ["spotlight", "siren", "audio_alarm"]},
    "fox":        {"level": 2, "actions": ["spotlight", "audio_alarm"]},
    "raccoon":    {"level": 2, "actions": ["spotlight", "audio_alarm"]},
    "possum":     {"level": 1, "actions": ["spotlight"]},
    "wild_cat":   {"level": 2, "actions": ["spotlight", "audio_alarm"]},
    "small_dog":  {"level": 0, "actions": []},
    "chicken":    {"level": 0, "actions": []},
    "small_bird": {"level": 0, "actions": []},
    "house_cat":  {"level": 0, "actions": []},
}


class DeterrentEngine:
    """Evaluates detections and fires camera deterrents based on escalation rules."""

    def __init__(self, config: dict, camera_ctrl: CameraController, db: GuardianDB,
                 patrol_pause_event: Optional[threading.Event] = None):
        det_cfg = config.get("deterrent", {})
        self._enabled = det_cfg.get("enabled", True)
        self._rules = det_cfg.get("response_rules", _DEFAULT_RULES)
        self._response_delay = det_cfg.get("response_delay_seconds", 1.5)
        self._spotlight_brightness = det_cfg.get("spotlight_brightness", 100)
        self._spotlight_duration = det_cfg.get("spotlight_duration_seconds", 120)
        self._siren_duration = det_cfg.get("siren_duration_seconds", 10)
        self._cooldown = det_cfg.get("cooldown_seconds", 300)
        self._effectiveness_window = det_cfg.get("effectiveness_window_seconds", 60)

        self._camera_ctrl = camera_ctrl
        self._db = db
        self._patrol_pause = patrol_pause_event

        # Track last deterrent time per species to enforce cooldown
        self._last_deterrent: dict[str, float] = {}
        self._lock = threading.Lock()

        # Active deterrent tracking — species → (track_id, fire_time)
        self._active_deterrents: dict[str, tuple[int, float]] = {}

        log.info(
            "DeterrentEngine initialized — enabled=%s, cooldown=%ds, spotlight=%ds, siren=%ds",
            self._enabled, self._cooldown, self._spotlight_duration, self._siren_duration,
        )

    def evaluate(self, track: ActiveTrack, camera_id: str) -> list[str]:
        """
        Evaluate a track and fire appropriate deterrents if needed.
        Returns list of actions taken (e.g. ["spotlight", "siren"]).
        Returns empty list if no action taken (level 0, cooldown, or disabled).
        """
        if not self._enabled:
            return []

        if not track.is_predator:
            return []

        species = track.class_name
        rule = self._rules.get(species)
        if not rule:
            # Unknown predator — default to level 1 (spotlight)
            rule = {"level": 1, "actions": ["spotlight"]}

        level = rule.get("level", 0)
        actions = rule.get("actions", [])

        if level == 0 or not actions:
            return []

        # Enforce cooldown
        if not self._check_cooldown(species):
            log.debug("Deterrent cooldown active for '%s' — skipping", species)
            return []

        # Pause patrol so camera stays on the predator
        if self._patrol_pause:
            self._patrol_pause.set()

        # Brief delay before firing (avoid false positive triggers)
        if self._response_delay > 0:
            time.sleep(self._response_delay)

        # Fire deterrent actions
        fired = []
        now_iso = datetime.now().isoformat()

        for action in actions:
            success = self._execute_action(action, camera_id)
            if success:
                fired.append(action)
                # Log to DB
                try:
                    self._db.insert_deterrent_action(
                        track_id=track.track_id,
                        camera_id=camera_id,
                        acted_at=now_iso,
                        action_type=action,
                        duration_sec=(
                            self._spotlight_duration if action == "spotlight"
                            else self._siren_duration if action == "siren"
                            else 5.0
                        ),
                    )
                except Exception as exc:
                    log.error("Failed to log deterrent action: %s", exc)

        if fired:
            with self._lock:
                self._last_deterrent[species] = time.time()
                self._active_deterrents[species] = (track.track_id, time.time())

            log.info(
                "Deterrent fired for '%s' (level %d) on '%s' — actions: %s",
                species, level, camera_id, ", ".join(fired),
            )

            # Schedule patrol resume after effectiveness window
            self._schedule_patrol_resume(species)

        return fired

    def check_effectiveness(self, species: str, track_still_active: bool) -> Optional[str]:
        """
        Check if a previously fired deterrent was effective.
        Called after effectiveness_window expires.
        Returns outcome: "deterred", "no_effect", or None if no active deterrent.
        """
        with self._lock:
            entry = self._active_deterrents.pop(species, None)

        if not entry:
            return None

        track_id, fire_time = entry
        elapsed = time.time() - fire_time

        if elapsed < self._effectiveness_window:
            return None  # too early to judge

        if track_still_active:
            outcome = "no_effect"
        else:
            outcome = "deterred"

        # Update deterrent action result in DB
        try:
            self._db.update_deterrent_result(track_id, outcome)
        except Exception as exc:
            log.error("Failed to update deterrent result for track %d: %s", track_id, exc)

        log.info("Deterrent effectiveness for '%s': %s (%.0fs elapsed)", species, outcome, elapsed)
        return outcome

    def _check_cooldown(self, species: str) -> bool:
        """Return True if species is NOT in cooldown (ok to fire)."""
        with self._lock:
            last = self._last_deterrent.get(species, 0)
        return (time.time() - last) >= self._cooldown

    def _execute_action(self, action: str, camera_id: str) -> bool:
        """Execute a single deterrent action on the camera."""
        if action == "spotlight":
            return self._camera_ctrl.spotlight_timed(
                camera_id, self._spotlight_duration, self._spotlight_brightness
            )
        elif action == "siren":
            return self._camera_ctrl.siren_timed(camera_id, self._siren_duration)
        elif action == "audio_alarm":
            # Audio alarm uses the siren with shorter duration as a warning tone
            return self._camera_ctrl.siren_timed(camera_id, duration=3.0)
        else:
            log.warning("Unknown deterrent action: '%s'", action)
            return False

    def _schedule_patrol_resume(self, species: str) -> None:
        """Resume patrol after the effectiveness window if no more threats."""
        def _resume():
            time.sleep(self._effectiveness_window)
            with self._lock:
                still_active = species in self._active_deterrents
            if not still_active and self._patrol_pause:
                self._patrol_pause.clear()
                log.debug("Patrol resumed after deterrent for '%s'", species)

        threading.Thread(
            target=_resume, name=f"patrol-resume-{species}", daemon=True
        ).start()

    @property
    def active_deterrents(self) -> dict[str, tuple[int, float]]:
        """Return currently active deterrents (species → (track_id, fire_time))."""
        with self._lock:
            return dict(self._active_deterrents)
