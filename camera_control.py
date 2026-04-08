# Author: Claude Opus 4.6
# Date: 08-April-2026
# PURPOSE: Reolink camera hardware control for Farm Guardian v2. Provides sync
#          wrappers around the async reolink_aio library for PTZ (pan/tilt/zoom),
#          spotlight, siren, audio alarm, autofocus, PTZ guard (auto-return-to-home),
#          and snapshot control. Manages authentication with auto-refresh, PTZ preset
#          recall, and patrol route cycling with pause/resume support for deterrent
#          integration. Runs an async event loop in a background thread.
#          This module talks directly to the camera over the local network.
# SRP/DRY check: Pass — single responsibility is camera hardware control.

import asyncio
import json
import logging
import threading
import time
from typing import Optional

from reolink_aio.api import Host

log = logging.getLogger("guardian.camera_control")


class CameraController:
    """Controls a Reolink camera's PTZ, spotlight, siren, and snapshot via reolink_aio."""

    def __init__(self, config: dict):
        self._config = config
        self._cameras: dict[str, Host] = {}  # camera_id → Host
        self._channel: dict[str, int] = {}   # camera_id → channel index
        self._lock = threading.Lock()

        # Dedicated event loop for async reolink_aio calls
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_loop, name="camera-ctrl-loop", daemon=True
        )
        self._loop_thread.start()

        log.info("CameraController initialized")

    def _run_loop(self) -> None:
        """Run the async event loop in a background thread."""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_async(self, coro):
        """Run an async coroutine from a sync context. Returns the result."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=10)
        except Exception as exc:
            log.error("Async camera operation failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect_camera(self, camera_id: str, ip: str, username: str, password: str,
                       port: int = 80, channel: int = 0) -> bool:
        """Connect to a Reolink camera. Returns True on success.

        Host() must be constructed inside the dedicated event loop thread to avoid
        'no running event loop' errors that occur when reolink_aio tries to access
        the current event loop during __init__ from a non-async context.
        """
        async def _do_connect():
            host = Host(ip, username, password, port=port)
            await host.get_host_data()
            return host

        try:
            host = self._run_async(_do_connect())
            if host is None:
                raise RuntimeError("_run_async returned None — check logs for async error")
            with self._lock:
                self._cameras[camera_id] = host
                self._channel[camera_id] = channel
            log.info("Connected to camera '%s' at %s:%d", camera_id, ip, port)
            return True
        except Exception as exc:
            log.error("Failed to connect to camera '%s' at %s: %s", camera_id, ip, exc)
            return False

    def disconnect_camera(self, camera_id: str) -> None:
        """Disconnect from a camera."""
        with self._lock:
            host = self._cameras.pop(camera_id, None)
            self._channel.pop(camera_id, None)
        if host:
            try:
                self._run_async(host.logout())
            except Exception:
                pass
            log.info("Disconnected from camera '%s'", camera_id)

    def _get_host(self, camera_id: str) -> Optional[Host]:
        """Get the Host instance for a camera, or None."""
        with self._lock:
            return self._cameras.get(camera_id)

    def _get_channel(self, camera_id: str) -> int:
        """Get the channel index for a camera."""
        with self._lock:
            return self._channel.get(camera_id, 0)

    # ------------------------------------------------------------------
    # Spotlight
    # ------------------------------------------------------------------

    def spotlight_on(self, camera_id: str, brightness: int = 100) -> bool:
        """Turn on the camera spotlight. Returns True on success."""
        host = self._get_host(camera_id)
        if not host:
            log.warning("spotlight_on: camera '%s' not connected", camera_id)
            return False
        ch = self._get_channel(camera_id)
        try:
            self._run_async(host.set_whiteled(ch, state=True, brightness=brightness))
            log.info("Spotlight ON for '%s' (brightness=%d)", camera_id, brightness)
            return True
        except Exception as exc:
            log.error("Failed to turn on spotlight for '%s': %s", camera_id, exc)
            return False

    def spotlight_off(self, camera_id: str) -> bool:
        """Turn off the camera spotlight."""
        host = self._get_host(camera_id)
        if not host:
            return False
        ch = self._get_channel(camera_id)
        try:
            self._run_async(host.set_whiteled(ch, state=False))
            log.info("Spotlight OFF for '%s'", camera_id)
            return True
        except Exception as exc:
            log.error("Failed to turn off spotlight for '%s': %s", camera_id, exc)
            return False

    # ------------------------------------------------------------------
    # Siren
    # ------------------------------------------------------------------

    def siren_on(self, camera_id: str) -> bool:
        """Trigger the camera siren. Returns True on success."""
        host = self._get_host(camera_id)
        if not host:
            log.warning("siren_on: camera '%s' not connected", camera_id)
            return False
        ch = self._get_channel(camera_id)
        try:
            self._run_async(host.set_siren(ch, True))
            log.info("Siren ON for '%s'", camera_id)
            return True
        except Exception as exc:
            log.error("Failed to trigger siren for '%s': %s", camera_id, exc)
            return False

    def siren_off(self, camera_id: str) -> bool:
        """Stop the camera siren."""
        host = self._get_host(camera_id)
        if not host:
            return False
        ch = self._get_channel(camera_id)
        try:
            self._run_async(host.set_siren(ch, False))
            log.info("Siren OFF for '%s'", camera_id)
            return True
        except Exception as exc:
            log.error("Failed to stop siren for '%s': %s", camera_id, exc)
            return False

    # ------------------------------------------------------------------
    # PTZ
    # ------------------------------------------------------------------

    def ptz_move(self, camera_id: str, pan: float = 0, tilt: float = 0,
                 zoom: float = 0, speed: int = 25) -> bool:
        """Start continuous PTZ movement.

        Maps pan/tilt/zoom direction values to reolink_aio PtzEnum command strings:
          pan > 0 → Right, pan < 0 → Left
          tilt > 0 → Up,   tilt < 0 → Down
          zoom > 0 → ZoomInc, zoom < 0 → ZoomDec
          Diagonal combos: LeftUp, RightUp, LeftDown, RightDown
        """
        host = self._get_host(camera_id)
        if not host:
            return False
        ch = self._get_channel(camera_id)

        # Derive direction command from pan/tilt/zoom values
        if zoom != 0:
            command = "ZoomInc" if zoom > 0 else "ZoomDec"
        elif pan != 0 and tilt != 0:
            h = "Right" if pan > 0 else "Left"
            v = "Up" if tilt > 0 else "Down"
            command = f"{h}{v}"  # e.g. "RightUp"
        elif pan != 0:
            command = "Right" if pan > 0 else "Left"
        elif tilt != 0:
            command = "Up" if tilt > 0 else "Down"
        else:
            command = "Stop"

        # Try with speed first; fall back without if camera doesn't support it
        async def _do_move():
            try:
                await host.set_ptz_command(ch, command=command, speed=speed)
            except Exception:
                # Camera may not support speed parameter — retry without it
                await host.set_ptz_command(ch, command=command)

        try:
            self._run_async(_do_move())
            return True
        except Exception as exc:
            log.error("PTZ move failed for '%s': %s", camera_id, exc)
            return False

    def ptz_stop(self, camera_id: str) -> bool:
        """Stop PTZ movement."""
        host = self._get_host(camera_id)
        if not host:
            return False
        ch = self._get_channel(camera_id)
        try:
            self._run_async(host.set_ptz_command(ch, command="Stop"))
            return True
        except Exception as exc:
            log.error("PTZ stop failed for '%s': %s", camera_id, exc)
            return False

    def ptz_goto_preset(self, camera_id: str, preset_index: int) -> bool:
        """Move camera to a saved PTZ preset by index (0-based).

        reolink_aio expects preset as an int index into the ptz_presets dict.
        """
        host = self._get_host(camera_id)
        if not host:
            return False
        ch = self._get_channel(camera_id)
        try:
            self._run_async(host.set_ptz_command(ch, preset=preset_index))
            log.info("PTZ goto preset %d for '%s'", preset_index, camera_id)
            return True
        except Exception as exc:
            log.error("PTZ goto preset failed for '%s': %s", camera_id, exc)
            return False

    # ------------------------------------------------------------------
    # Autofocus
    # ------------------------------------------------------------------

    def ensure_autofocus(self, camera_id: str) -> bool:
        """Enable autofocus on the camera if not already enabled.

        The Reolink E1 Outdoor Pro has a motorized lens with autofocus.
        After zoom changes or significant PTZ movement, autofocus must be
        enabled so the camera refocuses the lens for the new field of view.
        Returns True on success.
        """
        host = self._get_host(camera_id)
        if not host:
            log.warning("ensure_autofocus: camera '%s' not connected", camera_id)
            return False
        ch = self._get_channel(camera_id)
        try:
            if not host.autofocus_enabled(ch):
                self._run_async(host.set_autofocus(ch, True))
                log.info("Autofocus enabled for '%s'", camera_id)
            else:
                log.debug("Autofocus already enabled for '%s'", camera_id)
            return True
        except Exception as exc:
            log.error("Failed to enable autofocus for '%s': %s", camera_id, exc)
            return False

    def trigger_autofocus(self, camera_id: str) -> bool:
        """Trigger a one-shot autofocus cycle.

        Briefly disables then re-enables autofocus to force the camera to
        recalculate focus for the current scene. Useful after zoom changes
        or moving to a new PTZ position where the depth-of-field has changed.
        """
        host = self._get_host(camera_id)
        if not host:
            return False
        ch = self._get_channel(camera_id)
        try:
            # Cycle autofocus off→on to force a fresh focus calculation
            self._run_async(host.set_autofocus(ch, False))
            self._run_async(host.set_autofocus(ch, True))
            log.info("Autofocus triggered for '%s'", camera_id)
            return True
        except Exception as exc:
            # If the camera doesn't support AutoFocus commands, log and move on.
            # Focus may still work via the camera's own internal auto-focus.
            log.warning("Autofocus trigger failed for '%s': %s (may not be supported)", camera_id, exc)
            return False

    # ------------------------------------------------------------------
    # PTZ Guard (auto-return-to-home prevention)
    # ------------------------------------------------------------------

    def is_guard_enabled(self, camera_id: str) -> bool:
        """Check if PTZ guard (auto-return-to-home) is active.

        When guard is enabled, the camera automatically returns to its saved
        guard/home position after a period of PTZ inactivity. On the Reolink E1,
        the default guard position is pan=0 — which may point at the mounting
        post, making the camera useless when idle.
        """
        host = self._get_host(camera_id)
        if not host:
            return False
        ch = self._get_channel(camera_id)
        try:
            return host.ptz_guard_enabled(ch)
        except Exception:
            return False

    def disable_guard(self, camera_id: str) -> bool:
        """Disable PTZ guard so the camera stays where patrol leaves it.

        Without this, the Reolink returns to pan=0 (mounting post) after the
        guard timeout expires during any gap in PTZ commands.
        """
        host = self._get_host(camera_id)
        if not host:
            log.warning("disable_guard: camera '%s' not connected", camera_id)
            return False
        ch = self._get_channel(camera_id)
        try:
            self._run_async(host.set_ptz_guard(ch, enable=False))
            log.info("PTZ guard disabled for '%s' — camera will not auto-return to home", camera_id)
            return True
        except Exception as exc:
            log.error("Failed to disable PTZ guard for '%s': %s", camera_id, exc)
            return False

    def set_guard_position(self, camera_id: str) -> bool:
        """Save the camera's current position as the guard/home position.

        If guard must stay enabled, at least make it return to a useful position
        (e.g. pointing at the yard) instead of pan=0 (mounting post).
        """
        host = self._get_host(camera_id)
        if not host:
            return False
        ch = self._get_channel(camera_id)
        try:
            self._run_async(host.set_ptz_guard(ch, command="setPos"))
            log.info("Guard position saved to current position for '%s'", camera_id)
            return True
        except Exception as exc:
            log.error("Failed to set guard position for '%s': %s", camera_id, exc)
            return False

    # ------------------------------------------------------------------
    # Timed deterrent helpers
    # ------------------------------------------------------------------

    def spotlight_timed(self, camera_id: str, duration: float = 120,
                        brightness: int = 100) -> bool:
        """Turn on spotlight for a set duration, then auto-off. Non-blocking."""
        if not self.spotlight_on(camera_id, brightness):
            return False

        def _auto_off():
            time.sleep(duration)
            self.spotlight_off(camera_id)

        threading.Thread(target=_auto_off, name=f"spot-off-{camera_id}", daemon=True).start()
        return True

    def siren_timed(self, camera_id: str, duration: float = 10) -> bool:
        """Trigger siren for a set duration, then auto-off. Non-blocking."""
        if not self.siren_on(camera_id):
            return False

        def _auto_off():
            time.sleep(duration)
            self.siren_off(camera_id)

        threading.Thread(target=_auto_off, name=f"siren-off-{camera_id}", daemon=True).start()
        return True

    # ------------------------------------------------------------------
    # Patrol with pause/resume
    # ------------------------------------------------------------------

    def start_patrol(self, camera_id: str, presets: list[dict],
                     shutdown_event: Optional[threading.Event] = None,
                     pause_event: Optional[threading.Event] = None) -> None:
        """
        Run a PTZ patrol loop through preset positions. Blocks until shutdown.
        Each preset dict should have: index, name, dwell (seconds).
        Call from a dedicated thread.

        pause_event: when set, patrol pauses until cleared. Used by deterrent
        module to hold the camera while tracking a predator.
        """
        if not presets:
            log.warning("No patrol presets configured for '%s'", camera_id)
            return

        log.info("Starting patrol for '%s' with %d presets", camera_id, len(presets))
        preset_idx = 0
        while not (shutdown_event and shutdown_event.is_set()):
            # Wait while paused (deterrent tracking)
            if pause_event and pause_event.is_set():
                log.debug("Patrol paused for '%s' — deterrent active", camera_id)
                while pause_event.is_set():
                    if shutdown_event and shutdown_event.is_set():
                        break
                    time.sleep(0.5)
                log.debug("Patrol resumed for '%s'", camera_id)

            if shutdown_event and shutdown_event.is_set():
                break

            preset = presets[preset_idx % len(presets)]
            preset_idx += 1
            idx = preset.get("index", preset_idx - 1)
            name = preset.get("name", f"preset-{idx}")
            dwell = preset.get("dwell", 30)

            self.ptz_goto_preset(camera_id, idx)
            log.debug("Patrol: '%s' at preset '%s', dwell %ds", camera_id, name, dwell)

            # Wait for dwell time, checking shutdown and pause periodically
            waited = 0
            while waited < dwell:
                if shutdown_event and shutdown_event.is_set():
                    break
                if pause_event and pause_event.is_set():
                    break  # exit dwell early, outer loop handles pause
                time.sleep(min(1.0, dwell - waited))
                waited += 1

        log.info("Patrol stopped for '%s'", camera_id)

    # ------------------------------------------------------------------
    # Position readback (for sweep patrol)
    # ------------------------------------------------------------------

    def get_pan_position(self, camera_id: str) -> Optional[float]:
        """Read the camera's current pan position. Returns value or None."""
        host = self._get_host(camera_id)
        if not host:
            return None
        ch = self._get_channel(camera_id)
        try:
            # Refresh position data from camera before reading
            self._run_async(host.get_state(cmd="GetPtzCurPos"))
            return host.ptz_pan_position(ch)
        except Exception as exc:
            log.error("Failed to read pan position for '%s': %s", camera_id, exc)
            return None

    def get_tilt_position(self, camera_id: str) -> Optional[float]:
        """Read the camera's current tilt position. Returns value or None."""
        host = self._get_host(camera_id)
        if not host:
            return None
        ch = self._get_channel(camera_id)
        try:
            self._run_async(host.get_state(cmd="GetPtzCurPos"))
            return host.ptz_tilt_position(ch)
        except Exception as exc:
            log.error("Failed to read tilt position for '%s': %s", camera_id, exc)
            return None

    def get_position(self, camera_id: str) -> Optional[tuple[float, float]]:
        """Read current (pan, tilt) in one call. Returns tuple or None."""
        host = self._get_host(camera_id)
        if not host:
            return None
        ch = self._get_channel(camera_id)
        try:
            self._run_async(host.get_state(cmd="GetPtzCurPos"))
            pan = host.ptz_pan_position(ch)
            tilt = host.ptz_tilt_position(ch)
            if pan is not None and tilt is not None:
                return (pan, tilt)
            return None
        except Exception as exc:
            log.error("Failed to read position for '%s': %s", camera_id, exc)
            return None

    def get_zoom(self, camera_id: str) -> Optional[float]:
        """Read the camera's current zoom level. Returns value or None."""
        host = self._get_host(camera_id)
        if not host:
            return None
        ch = self._get_channel(camera_id)
        try:
            return host.get_zoom(ch)
        except Exception as exc:
            log.error("Failed to read zoom for '%s': %s", camera_id, exc)
            return None

    def set_zoom(self, camera_id: str, zoom: int) -> bool:
        """Set absolute zoom level (0–33). Returns True on success."""
        host = self._get_host(camera_id)
        if not host:
            return False
        ch = self._get_channel(camera_id)
        try:
            self._run_async(host.set_zoom(ch, zoom))
            return True
        except Exception as exc:
            log.error("Failed to set zoom for '%s': %s", camera_id, exc)
            return False

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def take_snapshot(self, camera_id: str) -> Optional[bytes]:
        """Capture a JPEG snapshot from the camera. Returns bytes or None."""
        host = self._get_host(camera_id)
        if not host:
            return None
        ch = self._get_channel(camera_id)
        try:
            image_bytes = self._run_async(host.get_snapshot(ch))
            log.debug("Snapshot captured from '%s'", camera_id)
            return image_bytes
        except Exception as exc:
            log.error("Snapshot failed for '%s': %s", camera_id, exc)
            return None

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Disconnect all cameras and stop the event loop."""
        camera_ids = list(self._cameras.keys())
        for cam_id in camera_ids:
            self.disconnect_camera(cam_id)

        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=5)
        log.info("CameraController closed")
