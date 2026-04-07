# Author: Claude Opus 4.6
# Date: 06-April-2026
# PURPOSE: Continuous sweep patrol for Farm Guardian. Drives the PTZ camera through a
#          serpentine raster scan — slow pan across full range, shift tilt, reverse —
#          so the camera views everything it can physically see. Uses continuous movement
#          commands (start/stop) with position polling since reolink_aio has no absolute
#          pan/tilt positioning. Supports a configurable dead zone to skip the camera's
#          own mounting point, and integrates with the deterrent system's pause/resume
#          mechanism so the camera holds position when tracking a predator.
#          Runs as a blocking loop on a dedicated thread, same pattern as the old
#          preset-based patrol in camera_control.py.
# SRP/DRY check: Pass — single responsibility is sweep patrol scheduling and movement.

import logging
import threading
import time
from typing import Optional

from camera_control import CameraController

log = logging.getLogger("guardian.patrol")


class SweepPatrol:
    """Continuous serpentine sweep patrol for a PTZ camera."""

    def __init__(self, camera_ctrl: CameraController, camera_id: str, config: dict):
        self._ctrl = camera_ctrl
        self._camera_id = camera_id

        sweep_cfg = config.get("ptz", {}).get("sweep", {})
        self._pan_speed = sweep_cfg.get("pan_speed", 15)
        self._tilt_speed = sweep_cfg.get("tilt_speed", 10)
        self._tilt_steps = sweep_cfg.get("tilt_steps", 3)
        self._tilt_min = sweep_cfg.get("tilt_min", 5)
        self._tilt_max = sweep_cfg.get("tilt_max", 60)
        self._poll_interval = sweep_cfg.get("position_poll_interval", 1.0)
        self._stall_threshold = sweep_cfg.get("stall_threshold", 3)
        self._dead_zone = sweep_cfg.get("dead_zone_pan", None)
        self._dead_zone_skip_speed = sweep_cfg.get("dead_zone_skip_speed", 60)
        self._dwell_at_edge = sweep_cfg.get("dwell_at_edge", 2.0)

        # Direction state: 1 = panning right, -1 = panning left
        self._pan_direction = 1
        # Tilt row index and direction
        self._tilt_row = 0
        self._tilt_direction = 1  # 1 = moving down through rows, -1 = moving up

        log.info(
            "SweepPatrol configured — camera='%s', pan_speed=%d, tilt_steps=%d, "
            "tilt_range=[%d, %d], poll=%.1fs, stall=%d, dead_zone=%s",
            camera_id, self._pan_speed, self._tilt_steps,
            self._tilt_min, self._tilt_max, self._poll_interval,
            self._stall_threshold, self._dead_zone,
        )

    def run(self, shutdown_event: threading.Event,
            pause_event: Optional[threading.Event] = None) -> None:
        """
        Run the sweep patrol loop. Blocks until shutdown_event is set.
        Call from a dedicated thread.

        pause_event: when set, patrol pauses (deterrent holding camera on a target).
        """
        log.info("Sweep patrol starting for '%s'", self._camera_id)

        # Reset zoom to wide angle for sweep
        self._ctrl.set_zoom(self._camera_id, 0)
        time.sleep(0.5)

        # Move tilt to starting row before first pan sweep
        self._move_tilt_to_row(self._tilt_row)

        while not shutdown_event.is_set():
            # Handle pause (deterrent active)
            if pause_event and pause_event.is_set():
                self._ctrl.ptz_stop(self._camera_id)
                log.debug("Sweep paused — deterrent active")
                while pause_event.is_set() and not shutdown_event.is_set():
                    time.sleep(0.5)
                if shutdown_event.is_set():
                    break
                log.debug("Sweep resumed")
                # Re-zoom wide after deterrent may have changed zoom
                self._ctrl.set_zoom(self._camera_id, 0)
                time.sleep(0.3)

            # Execute one pan sweep across the current tilt row
            stalled = self._sweep_pan(shutdown_event, pause_event)

            if shutdown_event.is_set():
                break

            # Brief dwell at the edge before changing direction
            self._wait(self._dwell_at_edge, shutdown_event, pause_event)

            # Reverse pan direction
            self._pan_direction *= -1

            # Advance to next tilt row
            self._advance_tilt_row()
            self._move_tilt_to_row(self._tilt_row)

        self._ctrl.ptz_stop(self._camera_id)
        log.info("Sweep patrol stopped for '%s'", self._camera_id)

    def _sweep_pan(self, shutdown_event: threading.Event,
                   pause_event: Optional[threading.Event]) -> bool:
        """
        Pan in the current direction until we stall (hit limit) or enter dead zone.
        Returns True if we stalled, False if interrupted.
        """
        pan_val = 1 if self._pan_direction > 0 else -1
        in_dead_zone = False
        stall_count = 0
        last_pan_pos = None

        # Start panning
        self._ctrl.ptz_move(
            self._camera_id, pan=pan_val, tilt=0, speed=self._pan_speed
        )
        log.debug(
            "Sweep: panning %s at speed %d, tilt row %d",
            "right" if pan_val > 0 else "left", self._pan_speed, self._tilt_row,
        )

        while not shutdown_event.is_set():
            # Check pause
            if pause_event and pause_event.is_set():
                self._ctrl.ptz_stop(self._camera_id)
                return False

            time.sleep(self._poll_interval)

            # Read current position
            pos = self._ctrl.get_position(self._camera_id)
            if pos is None:
                log.warning("Sweep: position read failed — continuing blind")
                stall_count += 1
                if stall_count >= self._stall_threshold * 2:
                    # Too many failed reads — stop and move on
                    self._ctrl.ptz_stop(self._camera_id)
                    return True
                continue

            current_pan, current_tilt = pos
            log.debug("Sweep position: pan=%.1f, tilt=%.1f", current_pan, current_tilt)

            # Dead zone check — skip through it fast
            if self._dead_zone and self._in_dead_zone(current_pan):
                if not in_dead_zone:
                    log.debug("Sweep: entering dead zone at pan=%.1f — skipping", current_pan)
                    in_dead_zone = True
                    # Speed up to rush through
                    self._ctrl.ptz_move(
                        self._camera_id, pan=pan_val, tilt=0,
                        speed=self._dead_zone_skip_speed,
                    )
            elif in_dead_zone:
                # Exited dead zone — resume normal speed
                log.debug("Sweep: exited dead zone at pan=%.1f — resuming", current_pan)
                in_dead_zone = False
                self._ctrl.ptz_move(
                    self._camera_id, pan=pan_val, tilt=0, speed=self._pan_speed
                )

            # Stall detection — if pan position hasn't changed, we've hit a limit
            if last_pan_pos is not None and abs(current_pan - last_pan_pos) < 0.5:
                stall_count += 1
                if stall_count >= self._stall_threshold:
                    log.debug(
                        "Sweep: pan stalled at %.1f after %d polls — reversing",
                        current_pan, stall_count,
                    )
                    self._ctrl.ptz_stop(self._camera_id)
                    return True
            else:
                stall_count = 0

            last_pan_pos = current_pan

        self._ctrl.ptz_stop(self._camera_id)
        return False

    def _move_tilt_to_row(self, row: int) -> None:
        """
        Tilt the camera to the target row position using timed continuous movement.

        Since we can't set absolute tilt, we move tilt up/down based on current
        position vs target. We compute the target tilt angle from the row index
        and move toward it using short bursts + position polling.
        """
        if self._tilt_steps <= 1:
            target_tilt = (self._tilt_min + self._tilt_max) / 2
        else:
            step_size = (self._tilt_max - self._tilt_min) / (self._tilt_steps - 1)
            target_tilt = self._tilt_min + (row * step_size)

        log.debug("Sweep: moving tilt to row %d (target=%.1f)", row, target_tilt)

        # Poll-and-nudge loop to reach target tilt
        max_attempts = 20
        for _ in range(max_attempts):
            pos = self._ctrl.get_position(self._camera_id)
            if pos is None:
                time.sleep(0.5)
                continue

            _, current_tilt = pos
            diff = target_tilt - current_tilt

            if abs(diff) < 3.0:
                # Close enough
                self._ctrl.ptz_stop(self._camera_id)
                log.debug("Sweep: tilt reached %.1f (target %.1f)", current_tilt, target_tilt)
                return

            # Move toward target
            tilt_val = 1 if diff > 0 else -1
            self._ctrl.ptz_move(
                self._camera_id, pan=0, tilt=tilt_val, speed=self._tilt_speed
            )
            time.sleep(0.5)
            self._ctrl.ptz_stop(self._camera_id)
            time.sleep(0.3)

        log.warning("Sweep: tilt positioning timed out after %d attempts", max_attempts)

    def _advance_tilt_row(self) -> None:
        """Move to the next tilt row, reversing direction at the limits."""
        next_row = self._tilt_row + self._tilt_direction

        if next_row >= self._tilt_steps or next_row < 0:
            # Reverse tilt direction
            self._tilt_direction *= -1
            next_row = self._tilt_row + self._tilt_direction

        self._tilt_row = max(0, min(next_row, self._tilt_steps - 1))
        log.debug("Sweep: advancing to tilt row %d (direction=%d)",
                  self._tilt_row, self._tilt_direction)

    def _in_dead_zone(self, pan: float) -> bool:
        """Check if the given pan angle falls within the dead zone.

        Dead zone is defined as [start, end] in degrees. Handles wrap-around
        (e.g., [340, 20] means 340° through 0° to 20°).
        """
        if not self._dead_zone or len(self._dead_zone) != 2:
            return False

        start, end = self._dead_zone

        if start <= end:
            # Normal range: e.g., [90, 120]
            return start <= pan <= end
        else:
            # Wraps around 360°: e.g., [340, 20] means 340-360 and 0-20
            return pan >= start or pan <= end

    def _wait(self, duration: float, shutdown_event: threading.Event,
              pause_event: Optional[threading.Event]) -> None:
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
