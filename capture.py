# Author: Claude Opus 4.6
# Date: 06-April-2026
# PURPOSE: RTSP frame capture for Farm Guardian. Connects to camera RTSP streams via
#          OpenCV, grabs frames at a configurable interval (default 1 fps), and downscales
#          4K frames to 1080p before passing them to detection. Maintains a small ring buffer
#          of recent frames for event context. Handles stream disconnection with exponential
#          backoff reconnection. Each camera gets its own capture thread.
# SRP/DRY check: Pass — single responsibility is frame acquisition from RTSP streams.

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

import cv2
import numpy as np

log = logging.getLogger("guardian.capture")

# Downscale target: 1080p width (preserving aspect ratio)
_TARGET_WIDTH = 1920

# Reconnection backoff parameters
_BACKOFF_BASE = 2.0
_BACKOFF_MAX = 60.0
_BACKOFF_MULTIPLIER = 2.0

# Lock protecting OPENCV_FFMPEG_CAPTURE_OPTIONS env var during VideoCapture creation.
# Each camera may need a different rtsp_transport (tcp vs udp), but the env var is
# process-global. We hold this lock while setting the var and calling VideoCapture().
_env_lock = threading.Lock()


@dataclass
class FrameResult:
    """A captured and pre-processed frame with metadata."""
    frame: np.ndarray
    camera_name: str
    timestamp: float
    original_width: int
    original_height: int


class CameraCapture:
    """Captures frames from a single camera's RTSP stream."""

    def __init__(
        self,
        camera_name: str,
        rtsp_url: str,
        frame_interval: float = 1.0,
        buffer_size: int = 10,
        on_frame: Optional[Callable[[FrameResult], None]] = None,
        rtsp_transport: Optional[str] = None,
    ):
        self._camera_name = camera_name
        self._rtsp_url = rtsp_url
        self._frame_interval = frame_interval
        self._on_frame = on_frame
        self._rtsp_transport = rtsp_transport  # "tcp", "udp", or None (auto)

        self._buffer: deque[FrameResult] = deque(maxlen=buffer_size)
        self._cap: Optional[cv2.VideoCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._consecutive_failures = 0

    @property
    def camera_name(self) -> str:
        return self._camera_name

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def recent_frames(self) -> list[FrameResult]:
        """Return a copy of the recent frame buffer (oldest first)."""
        with self._lock:
            return list(self._buffer)

    def start(self) -> None:
        """Start the capture thread. No-op if already running."""
        if self.is_running:
            log.warning("Capture already running for '%s'", self._camera_name)
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"capture-{self._camera_name}",
            daemon=True,
        )
        self._thread.start()
        log.info("Capture started for '%s' — interval=%.1fs", self._camera_name, self._frame_interval)

    def stop(self) -> None:
        """Signal the capture thread to stop and wait for it to finish."""
        if not self.is_running:
            return
        self._stop_event.set()
        self._thread.join(timeout=10)
        self._release_capture()
        log.info("Capture stopped for '%s'", self._camera_name)

    def _capture_loop(self) -> None:
        """Main loop: connect, grab frames at interval, reconnect on failure."""
        while not self._stop_event.is_set():
            if not self._ensure_connected():
                # Backoff before retrying connection
                delay = min(
                    _BACKOFF_BASE * (_BACKOFF_MULTIPLIER ** self._consecutive_failures),
                    _BACKOFF_MAX,
                )
                log.warning(
                    "Camera '%s' — connection failed, retrying in %.0fs (attempt %d)",
                    self._camera_name,
                    delay,
                    self._consecutive_failures + 1,
                )
                self._consecutive_failures += 1
                self._stop_event.wait(delay)
                continue

            # Connected — grab a frame with a 5-second manual timeout.
            # OpenCV's FFMPEG backend has a hardcoded 30s interrupt callback
            # that can't be overridden via CAP_PROP. We run the blocking
            # read() in a disposable thread and bail if it hangs.
            try:
                read_result = [None, None]

                def _do_read():
                    try:
                        read_result[0], read_result[1] = self._cap.read()
                    except Exception:
                        pass

                reader = threading.Thread(target=_do_read, daemon=True)
                reader.start()
                reader.join(timeout=10.0)

                if reader.is_alive():
                    # read() hung — abandon this cap and create a fresh one.
                    # Do NOT call cap.release() here: the reader thread is still
                    # blocking inside cap.read() in native code, and releasing
                    # the cap while read() is active causes a segfault.
                    # The old cap + daemon thread will be garbage collected.
                    log.warning("Camera '%s' — frame read hung >10s, reconnecting", self._camera_name)
                    self._cap = None
                    continue

                ret, raw_frame = read_result
                if not ret or raw_frame is None:
                    log.warning("Camera '%s' — frame read failed, reconnecting", self._camera_name)
                    self._release_capture()
                    continue

                self._consecutive_failures = 0
                result = self._process_frame(raw_frame)

                with self._lock:
                    self._buffer.append(result)

                # Dispatch to callback (detection pipeline)
                if self._on_frame:
                    try:
                        self._on_frame(result)
                    except Exception as exc:
                        log.error("Frame callback error for '%s': %s", self._camera_name, exc)

            except cv2.error as exc:
                log.error("OpenCV error on '%s': %s", self._camera_name, exc)
                self._release_capture()
            except Exception as exc:
                log.error("Unexpected capture error on '%s': %s", self._camera_name, exc)
                self._release_capture()

            # Wait for next frame interval (interruptible)
            self._stop_event.wait(self._frame_interval)

    def _ensure_connected(self) -> bool:
        """Open the RTSP stream if not already connected. Returns True if ready."""
        if self._cap is not None and self._cap.isOpened():
            return True

        self._release_capture()
        log.debug("Connecting to RTSP stream for '%s'", self._camera_name)

        try:
            # Set per-camera RTSP transport before creating VideoCapture.
            # The env var is process-global, so we hold a lock to prevent
            # concurrent capture threads from clobbering each other's transport.
            with _env_lock:
                env_key = "OPENCV_FFMPEG_CAPTURE_OPTIONS"
                saved = os.environ.get(env_key, "")
                if self._rtsp_transport:
                    os.environ[env_key] = f"rtsp_transport;{self._rtsp_transport}|stimeout;5000000"
                # else: leave the default stimeout-only value set by guardian.py
                cap = cv2.VideoCapture(self._rtsp_url, cv2.CAP_FFMPEG)
                os.environ[env_key] = saved

            if not cap.isOpened():
                cap.release()
                return False

            # Low buffer = low latency — we want live frames, not stale ones
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            # Override OpenCV's 30-second interrupt callback timeout.
            # On WiFi stalls, we want to detect and reconnect in 5s, not 30s.
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
            self._cap = cap
            transport_label = self._rtsp_transport or "auto"
            log.info("Connected to RTSP stream for '%s' (transport=%s)", self._camera_name, transport_label)
            return True

        except Exception as exc:
            log.error("Failed to open RTSP for '%s': %s", self._camera_name, exc)
            return False

    def _release_capture(self) -> None:
        """Safely release the OpenCV capture."""
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def _process_frame(self, raw_frame: np.ndarray) -> FrameResult:
        """Downscale 4K frame to 1080p width for efficient inference."""
        h, w = raw_frame.shape[:2]

        if w > _TARGET_WIDTH:
            scale = _TARGET_WIDTH / w
            new_w = _TARGET_WIDTH
            new_h = int(h * scale)
            frame = cv2.resize(raw_frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
        else:
            frame = raw_frame

        return FrameResult(
            frame=frame,
            camera_name=self._camera_name,
            timestamp=time.time(),
            original_width=w,
            original_height=h,
        )


class FrameCaptureManager:
    """Manages capture threads for multiple cameras."""

    def __init__(self, config: dict, on_frame: Optional[Callable[[FrameResult], None]] = None):
        detection_cfg = config.get("detection", {})
        self._frame_interval = detection_cfg.get("frame_interval_seconds", 1.0)
        self._on_frame = on_frame
        self._captures: dict[str, CameraCapture] = {}

    def add_camera(self, camera_name: str, rtsp_url: str, rtsp_transport: Optional[str] = None) -> None:
        """Register and start capturing from a camera."""
        if camera_name in self._captures:
            log.warning("Camera '%s' already registered — skipping", camera_name)
            return

        cap = CameraCapture(
            camera_name=camera_name,
            rtsp_url=rtsp_url,
            frame_interval=self._frame_interval,
            on_frame=self._on_frame,
            rtsp_transport=rtsp_transport,
        )
        self._captures[camera_name] = cap
        cap.start()

    def remove_camera(self, camera_name: str) -> None:
        """Stop and remove a camera capture."""
        cap = self._captures.pop(camera_name, None)
        if cap:
            cap.stop()

    def stop_all(self) -> None:
        """Stop all camera captures."""
        for name, cap in self._captures.items():
            log.info("Stopping capture for '%s'", name)
            cap.stop()
        self._captures.clear()

    def get_latest_frame(self, camera_name: str) -> Optional[FrameResult]:
        """Return the most recent frame for a camera, or None if unavailable."""
        cap = self._captures.get(camera_name)
        if cap and cap.recent_frames:
            return cap.recent_frames[-1]
        return None

    @property
    def active_cameras(self) -> list[str]:
        """Return names of cameras currently being captured."""
        return [name for name, cap in self._captures.items() if cap.is_running]
