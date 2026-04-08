# Author: Claude Opus 4.6
# Date: 08-April-2026
# PURPOSE: Continuous sweep patrol for Farm Guardian. Drives the PTZ camera through a
#          serpentine raster scan — slow pan across full range, shift tilt, reverse —
#          so the camera views everything it can physically see. Uses continuous movement
#          commands (start/stop) with position polling since reolink_aio has no absolute
#          pan/tilt positioning. Supports a configurable dead zone to skip the camera's
#          own mounting point, and integrates with the deterrent system's pause/resume
#          mechanism so the camera holds position when tracking a predator.
#          On startup, disables the Reolink PTZ guard (auto-return-to-home) feature
#          which otherwise snaps the camera back to pan=0 (the mounting post) during
#          any gap in PTZ commands. Also triggers autofocus after zoom/movement changes.
#          Runs as a blocking loop on a dedicated thread.
#
#          Reolink E1 Outdoor Pro coordinate system:
#            Pan:  0–7200 (20 units per degree, 360° total)
#            Pan=0 / Pan=7200: the camera's mounting post (home/default)
#            Tilt: readback is broken on this model (always returns 945),
#                  so we use timed bursts instead of position-based tilt control
#
# SRP/DRY check: Pass — single responsibility is sweep patrol scheduling and movement.

import logging
import threading
import time
from typing import Optional

from camera_control import CameraController

log = logging.getLogger("guardian.patrol")

# Reolink E1 pan range: 0–7200 (20 units per degree)
_PAN_UNITS_PER_DEGREE = 20


class SweepPatrol:
    """Continuous serpentine sweep patrol for a PTZ camera."""

    def __init__(self, camera_ctrl: CameraController, camera_id: str, config: dict):
        self._ctrl = camera_ctrl
        self._camera_id = camera_id

        sweep_cfg = config.get("ptz", {}).get("sweep", {})
        self._pan_speed = sweep_cfg.get("pan_speed", 15)
        self._tilt_speed = sweep_cfg.get("tilt_speed", 10)
        self._tilt_burst_seconds = sweep_cfg.get("tilt_burst_seconds", 1.5)
        self._poll_interval = sweep_cfg.get("position_poll_interval", 1.0)
        self._stall_threshold = sweep_cfg.get("stall_threshold", 3)
        self._start_pan = sweep_cfg.get("start_pan", None)
        self._dead_zone = sweep_cfg.get("dead_zone_pan", None)
        self._dead_zone_skip_speed = sweep_cfg.get("dead_zone_skip_speed", 60)
        self._dwell_at_edge = sweep_cfg.get("dwell_at_edge", 2.0)

        # Positioning config (previously magic numbers)
        self._positioning_tolerance = sweep_cfg.get("positioning_tolerance", 200)
        self._positioning_speed = sweep_cfg.get("positioning_speed", 40)

        # Direction state: 1 = panning right, -1 = panning left
        self._pan_direction = 1
        # Tilt direction alternates each full pan sweep: 1 = nudge up, -1 = nudge down
        self._tilt_direction = -1

        log.info(
            "SweepPatrol configured — camera='%s', pan_speed=%d, "
            "tilt_burst=%.1fs, poll=%.1fs, stall=%d, start_pan=%s, dead_zone=%s",
            camera_id, self._pan_speed,
            self._tilt_burst_seconds, self._poll_interval,
            self._stall_threshold, self._start_pan, self._dead_zone,
        )

    def run(self, shutdown_event: threading.Event,
            pause_event: Optional[threading.Event] = None) -> None:
        """
        Run the sweep patrol loop. Blocks until shutdown_event is set.
        Call from a dedicated thread.

        pause_event: when set, patrol pauses (deterrent holding camera on a target).
        """
        log.info("Sweep patrol starting for '%s'", self._camera_id)

        # Log current position for diagnostics
        self._log_position_diagnostic()

        # Disable PTZ guard (auto-return-to-home) so the camera stays where
        # patrol leaves it instead of snapping back to pan=0 (mounting post)
        # during dwells or pauses.
        if self._ctrl.is_guard_enabled(self._camera_id):
            log.info("PTZ guard is enabled — disabling to prevent auto-return to mount post")
            self._ctrl.disable_guard(self._camera_id)
        else:
            log.info("PTZ guard already disabled — camera will hold position")

        # Reset zoom to wide angle for maximum coverage
        self._ctrl.set_zoom(self._camera_id, 0)
        time.sleep(0.5)

        # Trigger autofocus so the wide-angle view is sharp
        self._ctrl.ensure_autofocus(self._camera_id)
        self._ctrl.trigger_autofocus(self._camera_id)
        time.sleep(1.0)  # Give the lens time to settle

        # Move to start position (away from the mounting post)
        # start_pan=3620 ≈ 181° = directly opposite the mount, facing the yard
        if self._start_pan is not None:
            self._pan_to_position(self._start_pan, shutdown_event)

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
                # Re-apply wide zoom and autofocus after deterrent released camera
                self._ctrl.set_zoom(self._camera_id, 0)
                time.sleep(0.3)
                self._ctrl.trigger_autofocus(self._camera_id)
                time.sleep(0.5)

            # Execute one pan sweep
            self._sweep_pan(shutdown_event, pause_event)

            if shutdown_event.is_set():
                break

            # Brief dwell at the edge before changing direction
            self._wait(self._dwell_at_edge, shutdown_event, pause_event)

            # Reverse pan direction
            self._pan_direction *= -1

            # Nudge tilt between sweeps (tilt readback is broken on the Reolink E1,
            # always returns 945, so we use short timed bursts to shift the view
            # slightly between pan sweeps)
            self._tilt_direction *= -1
            self._tilt_burst(self._tilt_direction)

        self._ctrl.ptz_stop(self._camera_id)
        log.info("Sweep patrol stopped for '%s'", self._camera_id)

    def _log_position_diagnostic(self) -> None:
        """Log the camera's current position for diagnostic purposes on startup."""
        pos = self._ctrl.get_position(self._camera_id)
        zoom = self._ctrl.get_zoom(self._camera_id)

        if pos is not None:
            pan, tilt = pos
            pan_degrees = pan / _PAN_UNITS_PER_DEGREE
            log.info(
                "Patrol diagnostic — camera='%s', pan=%d (%.1f°), tilt=%d, zoom=%s",
                self._camera_id, pan, pan_degrees, tilt,
                zoom if zoom is not None else "unknown",
            )
            # Check if starting at the mount post
            if self._dead_zone and self._in_dead_zone(pan):
                log.warning(
                    "Camera is currently in the dead zone (mounting post area). "
                    "Will move to start_pan=%s",
                    self._start_pan,
                )
        else:
            log.warning("Patrol diagnostic — could not read position for '%s'", self._camera_id)

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
            "Sweep: panning %s at speed %d",
            "right" if pan_val > 0 else "left", self._pan_speed,
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
            log.debug("Sweep position: pan=%.1f (%.1f°), tilt=%.1f",
                      current_pan, current_pan / _PAN_UNITS_PER_DEGREE, current_tilt)

            # Dead zone check — skip through it fast
            if self._dead_zone and self._in_dead_zone(current_pan):
                if not in_dead_zone:
                    log.info("Sweep: entering dead zone at pan=%.1f (%.1f°) — skipping fast",
                             current_pan, current_pan / _PAN_UNITS_PER_DEGREE)
                    in_dead_zone = True
                    # Speed up to rush through the mount post area
                    self._ctrl.ptz_move(
                        self._camera_id, pan=pan_val, tilt=0,
                        speed=self._dead_zone_skip_speed,
                    )
            elif in_dead_zone:
                # Exited dead zone — resume normal speed
                log.info("Sweep: exited dead zone at pan=%.1f (%.1f°) — resuming normal speed",
                         current_pan, current_pan / _PAN_UNITS_PER_DEGREE)
                in_dead_zone = False
                self._ctrl.ptz_move(
                    self._camera_id, pan=pan_val, tilt=0, speed=self._pan_speed
                )

            # Stall detection — if pan position hasn't changed, we've hit a limit
            if last_pan_pos is not None and abs(current_pan - last_pan_pos) < 0.5:
                stall_count += 1
                if stall_count >= self._stall_threshold:
                    log.debug(
                        "Sweep: pan stalled at %.1f (%.1f°) after %d polls — reversing",
                        current_pan, current_pan / _PAN_UNITS_PER_DEGREE, stall_count,
                    )
                    self._ctrl.ptz_stop(self._camera_id)
                    return True
            else:
                stall_count = 0

            last_pan_pos = current_pan

        self._ctrl.ptz_stop(self._camera_id)
        return False

    def _tilt_burst(self, direction: int) -> None:
        """
        Nudge the tilt with a short timed burst. Direction: 1=up, -1=down.

        Tilt position readback is broken on the Reolink E1 Outdoor Pro (always
        returns 945), so we can't poll-and-nudge to a target. Instead we send a
        brief movement command to shift the view slightly between pan sweeps.
        """
        tilt_val = 1 if direction > 0 else -1
        log.debug("Sweep: tilt burst %s for %.1fs",
                  "up" if direction > 0 else "down", self._tilt_burst_seconds)
        self._ctrl.ptz_move(
            self._camera_id, pan=0, tilt=tilt_val, speed=self._tilt_speed
        )
        time.sleep(self._tilt_burst_seconds)
        self._ctrl.ptz_stop(self._camera_id)
        time.sleep(0.3)

    def _pan_to_position(self, target_pan: float,
                         shutdown_event: threading.Event) -> None:
        """
        Move the camera to a target pan position using continuous movement
        with position polling. Used to reach the start position on patrol boot.

        Uses positioning_tolerance (default 200 = ~10° in Reolink units) to
        determine "close enough" and positioning_speed for movement.
        """
        target_degrees = target_pan / _PAN_UNITS_PER_DEGREE
        log.info("Sweep: moving to start position (pan=%d, %.1f°)", target_pan, target_degrees)
        max_seconds = 30
        start = time.time()

        while not shutdown_event.is_set() and (time.time() - start) < max_seconds:
            pos = self._ctrl.get_position(self._camera_id)
            if pos is None:
                time.sleep(1)
                continue

            current_pan, _ = pos
            diff = target_pan - current_pan

            # Close enough — positioning_tolerance ~10° in Reolink units
            if abs(diff) < self._positioning_tolerance:
                self._ctrl.ptz_stop(self._camera_id)
                log.info("Sweep: reached start position (pan=%d, %.1f°)",
                         current_pan, current_pan / _PAN_UNITS_PER_DEGREE)
                return

            # Pan toward target
            pan_val = 1 if diff > 0 else -1
            self._ctrl.ptz_move(
                self._camera_id, pan=pan_val, tilt=0, speed=self._positioning_speed
            )
            time.sleep(self._poll_interval)

        self._ctrl.ptz_stop(self._camera_id)
        log.warning("Sweep: start positioning timed out after %ds", max_seconds)

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
            # Normal range: e.g., [2000, 3000]
            return start <= pan <= end
        else:
            # Wraps around 0/7200: e.g., [6800, 440] means 6800–7200 and 0–440
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
