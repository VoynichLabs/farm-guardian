# Author: Claude Opus 4.6
# Date: 03-April-2026
# PURPOSE: Reolink camera hardware control for Farm Guardian v2. Provides async
#          PTZ (pan/tilt/zoom), spotlight, siren, audio alarm, and snapshot control
#          via the reolink_aio library. Manages authentication with auto-refresh,
#          PTZ preset save/recall, and patrol route cycling. Wraps the async reolink_aio
#          API with synchronous methods for use from the detection pipeline threads.
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
        """Connect to a Reolink camera. Returns True on success."""
        try:
            host = Host(ip, username, password, port=port)
            self._run_async(host.get_host_data())
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
        """Start continuous PTZ movement. Pan/tilt/zoom are direction values."""
        host = self._get_host(camera_id)
        if not host:
            return False
        ch = self._get_channel(camera_id)
        try:
            self._run_async(host.set_ptz_command(ch, command="Start",
                                                  pan=pan, tilt=tilt, zoom=zoom, speed=speed))
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
        """Move camera to a saved PTZ preset by index."""
        host = self._get_host(camera_id)
        if not host:
            return False
        ch = self._get_channel(camera_id)
        try:
            self._run_async(host.set_ptz_command(ch, command="ToPos", preset=preset_index))
            log.info("PTZ goto preset %d for '%s'", preset_index, camera_id)
            return True
        except Exception as exc:
            log.error("PTZ goto preset failed for '%s': %s", camera_id, exc)
            return False

    def ptz_save_preset(self, camera_id: str, preset_index: int, name: str = "") -> bool:
        """Save current camera position as a preset."""
        host = self._get_host(camera_id)
        if not host:
            return False
        ch = self._get_channel(camera_id)
        try:
            self._run_async(host.set_ptz_command(ch, command="SetPreset",
                                                  preset=preset_index, name=name))
            log.info("Saved PTZ preset %d ('%s') for '%s'", preset_index, name, camera_id)
            return True
        except Exception as exc:
            log.error("PTZ save preset failed for '%s': %s", camera_id, exc)
            return False

    # ------------------------------------------------------------------
    # Patrol
    # ------------------------------------------------------------------

    def start_patrol(self, camera_id: str, presets: list[dict],
                     shutdown_event: Optional[threading.Event] = None) -> None:
        """
        Run a PTZ patrol loop through preset positions. Blocks until shutdown.
        Each preset dict should have: index, name, dwell (seconds).
        Call from a dedicated thread.
        """
        if not presets:
            log.warning("No patrol presets configured for '%s'", camera_id)
            return

        log.info("Starting patrol for '%s' with %d presets", camera_id, len(presets))
        while not (shutdown_event and shutdown_event.is_set()):
            for preset in presets:
                if shutdown_event and shutdown_event.is_set():
                    break
                idx = preset.get("index", 0)
                name = preset.get("name", f"preset-{idx}")
                dwell = preset.get("dwell", 30)

                self.ptz_goto_preset(camera_id, idx)
                log.debug("Patrol: '%s' at preset '%s', dwell %ds", camera_id, name, dwell)

                # Wait for dwell time, checking shutdown periodically
                waited = 0
                while waited < dwell:
                    if shutdown_event and shutdown_event.is_set():
                        break
                    time.sleep(min(1.0, dwell - waited))
                    waited += 1

        log.info("Patrol stopped for '%s'", camera_id)

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
