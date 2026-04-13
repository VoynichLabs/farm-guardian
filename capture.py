# Author: Claude Opus 4.6
# Date: 13-April-2026 (v2.16.0 — decode-garbage rejection + buffer flush on disconnect)
# PURPOSE: Frame capture for Farm Guardian. Connects to camera streams (RTSP or local USB)
#          via OpenCV, grabs frames at a configurable interval, and downscales 4K frames to
#          1080p before passing them to detection. Used for ALL cameras: detection cameras
#          run at ~1fps for real-time inference, non-detection cameras run at longer intervals
#          (e.g. 10s) for snapshot polling — replacing the old ffmpeg HLS pipeline.
#          Maintains a small ring buffer of recent frames for event context. Handles stream
#          disconnection with exponential backoff reconnection. Each camera gets its own
#          capture thread. USB cameras are opened via AVFoundation device index (no RTSP/FFMPEG).
#          v2.16.0: Rejects HEVC decode-garbage frames (the "gray washed-out frame of nothing"
#          that FFMPEG returns when reference packets are lost on lossy WiFi). Flushes the ring
#          buffer on disconnect so the dashboard never serves a stale post-disconnect frame.
# SRP/DRY check: Pass — single responsibility is frame acquisition from camera streams.

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

# Decode-garbage rejection thresholds.
# When HEVC reference packets are lost on a lossy link, FFMPEG still hands back
# a frame from cap.read() with ret=True, but it's a uniform mid-gray smear with
# almost no pixel variance. We reject those before they enter the buffer.
# A legitimately dark night frame has low stdev too, but ALSO low mean — those
# are accepted. The garbage frames sit at mean ~80–180 with stdev <4.
_DECODE_GARBAGE_STDEV_MAX = 4.0
_DECODE_GARBAGE_MEAN_MIN = 30.0
# Log every Nth consecutive rejection so we don't spam the log.
_GARBAGE_LOG_EVERY = 10

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
        rtsp_url: Optional[str] = None,
        frame_interval: float = 1.0,
        buffer_size: int = 10,
        on_frame: Optional[Callable[[FrameResult], None]] = None,
        rtsp_transport: Optional[str] = None,
        device_index: Optional[int] = None,
    ):
        self._camera_name = camera_name
        self._rtsp_url = rtsp_url
        self._frame_interval = frame_interval
        self._on_frame = on_frame
        self._rtsp_transport = rtsp_transport  # "tcp", "udp", or None (auto)
        self._device_index = device_index  # AVFoundation index for local USB cameras

        self._buffer: deque[FrameResult] = deque(maxlen=buffer_size)
        self._cap: Optional[cv2.VideoCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._consecutive_garbage = 0

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
                    # Flush the buffer so the dashboard doesn't keep serving the
                    # last (likely already-corrupted) frame from this dead session.
                    log.warning("Camera '%s' — frame read hung >10s, reconnecting", self._camera_name)
                    self._cap = None
                    with self._lock:
                        self._buffer.clear()
                    continue

                ret, raw_frame = read_result
                if not ret or raw_frame is None:
                    log.warning("Camera '%s' — frame read failed, reconnecting", self._camera_name)
                    self._release_capture()
                    continue

                # HEVC decode-failure rejection. On lossy WiFi the decoder will
                # happily return a uniform-gray smear when reference packets were
                # dropped. Skip these — don't push to buffer, don't run detection
                # on them. The next keyframe will deliver a clean frame.
                if self._is_decode_garbage(raw_frame):
                    self._consecutive_garbage += 1
                    if self._consecutive_garbage == 1 or self._consecutive_garbage % _GARBAGE_LOG_EVERY == 0:
                        log.warning(
                            "Camera '%s' — rejected decode-garbage frame (consecutive=%d)",
                            self._camera_name, self._consecutive_garbage,
                        )
                    continue

                if self._consecutive_garbage:
                    log.info(
                        "Camera '%s' — clean frames resumed after %d garbage frames",
                        self._camera_name, self._consecutive_garbage,
                    )
                    self._consecutive_garbage = 0

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
        """Open the camera stream (RTSP or USB) if not already connected. Returns True if ready."""
        if self._cap is not None and self._cap.isOpened():
            return True

        self._release_capture()

        try:
            # USB camera: open by AVFoundation device index, no RTSP/FFMPEG involved
            if self._device_index is not None:
                log.debug("Opening USB device %d for '%s'", self._device_index, self._camera_name)
                cap = cv2.VideoCapture(self._device_index)

                if not cap.isOpened():
                    cap.release()
                    return False

                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                self._cap = cap
                log.info("Connected to USB camera for '%s' (device %d)", self._camera_name, self._device_index)
                return True

            # RTSP camera: set per-camera transport before creating VideoCapture.
            # The env var is process-global, so we hold a lock to prevent
            # concurrent capture threads from clobbering each other's transport.
            log.debug("Connecting to RTSP stream for '%s'", self._camera_name)
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
            log.error("Failed to open camera '%s': %s", self._camera_name, exc)
            return False

    def _release_capture(self) -> None:
        """Safely release the OpenCV capture and flush the frame buffer.

        Buffer flush matters: without it, the dashboard would keep serving the
        last (often corrupted) frame from a dead RTSP session for the entire
        reconnect window — that's how a single bad frame becomes a sticky gray
        image in the UI for tens of seconds.
        """
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        with self._lock:
            self._buffer.clear()
        self._consecutive_garbage = 0

    @staticmethod
    def _is_decode_garbage(raw_frame: np.ndarray) -> bool:
        """True if the frame looks like an HEVC decode-failure smear.

        Subsamples by 8 in each axis (cheap: ~256x144 stats on a 4K frame)
        and checks two things:
          - stdev < 4: pixel values are nearly uniform (no real scene texture)
          - mean   > 30: it's NOT a legitimate dark night frame

        A real outdoor scene — even foggy or snow-covered — has stdev > 10
        once you include any structural edges. A decode-failure frame with a
        missing reference is a true monochromatic blob.
        """
        sub = raw_frame[::8, ::8]
        return float(sub.std()) < _DECODE_GARBAGE_STDEV_MAX and float(sub.mean()) > _DECODE_GARBAGE_MEAN_MIN

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

    def add_camera(
        self,
        camera_name: str,
        rtsp_url: Optional[str] = None,
        rtsp_transport: Optional[str] = None,
        device_index: Optional[int] = None,
        frame_interval: Optional[float] = None,
    ) -> None:
        """Register and start capturing from a camera (RTSP or USB).

        Args:
            frame_interval: Override the global detection interval for this camera.
                            Non-detection cameras use longer intervals (e.g. 10s) for
                            snapshot polling instead of the default ~1fps detection rate.
        """
        if camera_name in self._captures:
            log.warning("Camera '%s' already registered — skipping", camera_name)
            return

        interval = frame_interval if frame_interval is not None else self._frame_interval

        cap = CameraCapture(
            camera_name=camera_name,
            rtsp_url=rtsp_url,
            frame_interval=interval,
            on_frame=self._on_frame,
            rtsp_transport=rtsp_transport,
            device_index=device_index,
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
