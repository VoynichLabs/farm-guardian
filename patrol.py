# Author: Claude Opus 4.6
# Date: 08-April-2026
# PURPOSE: Step-and-dwell patrol for Farm Guardian. Moves the PTZ camera through a
#          sequence of pan positions, stopping at each one long enough for the camera
#          to settle, autofocus, and capture multiple clean frames for detection.
#          Replaces the continuous sweep which was too fast for 1fps detection — every
#          frame was motion-blurred and useless.
#
#          The patrol generates evenly-spaced pan positions across the useful range
#          (skipping the dead zone around the mounting post), visits each one in order,
#          then reverses. At each position the camera is completely stationary for the
#          dwell period, producing sharp frames that YOLO can actually process.
#
#          Integrates with the deterrent system's pause/resume mechanism.
#          On startup, disables PTZ guard (auto-return-to-home) and enables autofocus.
#
#          Reolink E1 Outdoor Pro coordinate system:
#            Pan:  0–7200 (20 units per degree, 360° total)
#            Pan=0 / Pan=7200: the camera's mounting post (home/default)
#            Dead zone [6800, 440]: ~340° through 0° to ~22° (mount obstruction)
#            Tilt: readback broken on this model (always returns 945 at some angles)
#
# SRP/DRY check: Pass — single responsibility is step-and-dwell patrol scheduling.

import logging
import threading
import time
from typing import Optional

from camera_control import CameraController

log = logging.getLogger("guardian.patrol")

# Reolink E1 pan range: 0–7200 (20 units per degree)
_PAN_UNITS_PER_DEGREE = 20


class SweepPatrol:
    """Step-and-dwell patrol — stops at each position for clean frame capture."""

    def __init__(self, camera_ctrl: CameraController, camera_id: str, config: dict):
        self._ctrl = camera_ctrl
        self._camera_id = camera_id

        sweep_cfg = config.get("ptz", {}).get("sweep", {})

        # Step-and-dwell settings
        self._step_degrees = sweep_cfg.get("step_degrees", 30)
        self._dwell_seconds = sweep_cfg.get("dwell_seconds", 8)
        self._move_speed = sweep_cfg.get("move_speed", 8)
        self._settle_seconds = sweep_cfg.get("settle_seconds", 3)

        # Dead zone (mounting post area to skip)
        self._dead_zone = sweep_cfg.get("dead_zone_pan", None)

        # Positioning precision
        self._positioning_tolerance = sweep_cfg.get("positioning_tolerance", 300)
        self._poll_interval = sweep_cfg.get("position_poll_interval", 0.3)

        # Build the list of patrol positions (pan values in Reolink units)
        self._positions = self._build_positions()

        # Direction: 1 = forward through positions, -1 = backward
        self._direction = 1
        self._current_index = 0

        log.info(
            "StepDwell patrol configured — camera='%s', %d positions, "
            "step=%d°, dwell=%ds, move_speed=%d, settle=%ds, dead_zone=%s",
            camera_id, len(self._positions), self._step_degrees,
            self._dwell_seconds, self._move_speed, self._settle_seconds,
            self._dead_zone,
        )
        log.info(
            "Patrol positions (degrees): %s",
            [f"{p / _PAN_UNITS_PER_DEGREE:.0f}°" for p in self._positions],
        )

    def _build_positions(self) -> list[int]:
        """Generate evenly-spaced pan positions across the useful range.

        Skips any position that falls in the dead zone. Positions are in
        Reolink pan units (0–7200).
        """
        step_units = self._step_degrees * _PAN_UNITS_PER_DEGREE
        positions = []

        # Generate positions across the full 360° range
        pan = 0
        while pan < 7200:
            if not self._in_dead_zone(pan):
                positions.append(int(pan))
            pan += step_units

        if not positions:
            # Fallback: if everything is dead zone (shouldn't happen), use center
            positions = [3600]
            log.warning("No valid patrol positions outside dead zone — using center (180°)")

        return positions

    def run(self, shutdown_event: threading.Event,
            pause_event: Optional[threading.Event] = None) -> None:
        """
        Run the step-and-dwell patrol loop. Blocks until shutdown_event is set.
        Call from a dedicated thread.

        pause_event: when set, patrol pauses (deterrent holding camera on a target).
        """
        log.info("Step-and-dwell patrol starting for '%s'", self._camera_id)

        # Log current position for diagnostics
        self._log_position_diagnostic()

        # Disable PTZ guard (auto-return-to-home) so the camera stays at each
        # dwell position instead of snapping back to pan=0 (mounting post)
        if self._ctrl.is_guard_enabled(self._camera_id):
            log.info("PTZ guard is enabled — disabling to prevent auto-return to mount post")
            self._ctrl.disable_guard(self._camera_id)
        else:
            log.info("PTZ guard already disabled — camera will hold position")

        # Set zoom wide and enable autofocus
        self._ctrl.set_zoom(self._camera_id, 0)
        time.sleep(1)
        self._ctrl.ensure_autofocus(self._camera_id)

        # Main patrol loop — visit each position, dwell, move to next
        while not shutdown_event.is_set():
            # Handle pause (deterrent active)
            if pause_event and pause_event.is_set():
                self._ctrl.ptz_stop(self._camera_id)
                log.info("Patrol paused — deterrent active")
                while pause_event.is_set() and not shutdown_event.is_set():
                    time.sleep(0.5)
                if shutdown_event.is_set():
                    break
                log.info("Patrol resumed — re-enabling autofocus")
                self._ctrl.set_zoom(self._camera_id, 0)
                time.sleep(0.5)
                self._ctrl.trigger_autofocus(self._camera_id)
                time.sleep(self._settle_seconds)

            # Get target position
            target_pan = self._positions[self._current_index]
            target_deg = target_pan / _PAN_UNITS_PER_DEGREE

            # Move to position
            log.info("Patrol: moving to position %d/%d — pan=%d (%.0f°)",
                     self._current_index + 1, len(self._positions),
                     target_pan, target_deg)

            if self._move_to_position(target_pan, shutdown_event, pause_event):
                if shutdown_event.is_set():
                    break
                if pause_event and pause_event.is_set():
                    continue  # Re-enter loop to handle pause

                # Settle — let the camera stabilize and autofocus lock
                self._ctrl.trigger_autofocus(self._camera_id)
                self._wait(self._settle_seconds, shutdown_event, pause_event)

                if shutdown_event.is_set():
                    break

                # Dwell — camera is stationary, capturing clean frames for detection
                pos = self._ctrl.get_position(self._camera_id)
                actual_pan = pos[0] if pos else target_pan
                log.info("Patrol: dwelling at pan=%d (%.0f°) for %ds",
                         actual_pan, actual_pan / _PAN_UNITS_PER_DEGREE,
                         self._dwell_seconds)
                self._wait(self._dwell_seconds, shutdown_event, pause_event)

            # Advance to next position
            self._advance_position()

        self._ctrl.ptz_stop(self._camera_id)
        log.info("Step-and-dwell patrol stopped for '%s'", self._camera_id)

    def _advance_position(self) -> None:
        """Move to the next position in the sequence, reversing at boundaries."""
        next_idx = self._current_index + self._direction
        if next_idx >= len(self._positions):
            self._direction = -1
            next_idx = self._current_index - 1
            log.debug("Patrol: reached end — reversing direction")
        elif next_idx < 0:
            self._direction = 1
            next_idx = 1 if len(self._positions) > 1 else 0
            log.debug("Patrol: reached start — reversing direction")
        self._current_index = max(0, min(next_idx, len(self._positions) - 1))

    def _move_to_position(self, target_pan: int,
                          shutdown_event: threading.Event,
                          pause_event: Optional[threading.Event]) -> bool:
        """Move the camera to a target pan position. Returns True on success."""
        max_seconds = 30
        start = time.time()

        while not shutdown_event.is_set() and (time.time() - start) < max_seconds:
            if pause_event and pause_event.is_set():
                self._ctrl.ptz_stop(self._camera_id)
                return False

            pos = self._ctrl.get_position(self._camera_id)
            if pos is None:
                time.sleep(0.5)
                continue

            current_pan, _ = pos
            diff = target_pan - current_pan

            # Close enough — stop and settle
            if abs(diff) < self._positioning_tolerance:
                self._ctrl.ptz_stop(self._camera_id)
                return True

            # Move toward target at controlled speed
            pan_dir = 1 if diff > 0 else -1
            self._ctrl.ptz_move(
                self._camera_id, pan=pan_dir, tilt=0, speed=self._move_speed
            )
            time.sleep(self._poll_interval)

        self._ctrl.ptz_stop(self._camera_id)
        log.warning("Patrol: positioning to pan=%d timed out after %ds", target_pan, max_seconds)
        return True  # Continue anyway — partial positioning is better than skipping

    def _log_position_diagnostic(self) -> None:
        """Log the camera's current position for diagnostics on startup."""
        pos = self._ctrl.get_position(self._camera_id)
        zoom = self._ctrl.get_zoom(self._camera_id)

        if pos is not None:
            pan, tilt = pos
            log.info(
                "Patrol diagnostic — camera='%s', pan=%d (%.1f°), tilt=%d, zoom=%s",
                self._camera_id, pan, pan / _PAN_UNITS_PER_DEGREE, tilt,
                zoom if zoom is not None else "unknown",
            )
            if self._dead_zone and self._in_dead_zone(pan):
                log.warning("Camera is currently in the dead zone (mounting post area)")
        else:
            log.warning("Patrol diagnostic — could not read position for '%s'", self._camera_id)

    def _in_dead_zone(self, pan: float) -> bool:
        """Check if the given pan position falls within the dead zone.

        Dead zone is defined as [start, end] in Reolink pan units (0–7200).
        Handles wrap-around: [6800, 440] means pan 6800→7200→0→440, which is
        the mounting post area (≈340° through 0° to ≈22°).
        """
        if not self._dead_zone or len(self._dead_zone) != 2:
            return False

        start, end = self._dead_zone
        if start <= end:
            return start <= pan <= end
        else:
            # Wraps around 0/7200: e.g., [6800, 440]
            return pan >= start or pan <= end

    def _wait(self, duration: float, shutdown_event: threading.Event,
              pause_event: Optional[threading.Event] = None) -> None:
        """Sleep for duration, breaking early on shutdown or pause."""
        waited = 0.0
        while waited < duration:
            if shutdown_event.is_set():
                return
            if pause_event and pause_event.is_set():
                return
            step = min(0.5, duration - waited)
            time.sleep(step)
            waited += step
