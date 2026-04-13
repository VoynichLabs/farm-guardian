# Author: Claude Opus 4.6
# Date: 13-April-2026 (v2.24.0 — HttpUrlSnapshotSource for generic /photo.jpg cameras, S7 battery path)
# PURPOSE: Frame acquisition for Farm Guardian. Two parallel acquisition modes share the
#          same FrameResult/ring-buffer/dispatch surface so FrameCaptureManager can treat
#          them interchangeably:
#            (1) CameraCapture — RTSP or local USB via OpenCV. Used for cameras whose only
#                interface is a live video stream. Includes decode-garbage rejection
#                (v2.16.0) for HEVC streams that hand back uniform-gray smear frames when
#                reference packets are lost on lossy WiFi, and ring-buffer flush on
#                disconnect so a stale corrupt frame never persists across a reconnect.
#            (2) CameraSnapshotPoller — periodic HTTP snapshot polling. Used for cameras
#                that expose a high-quality JPEG-on-demand endpoint (Reolink cmd=Snap
#                returns the camera's native 4K JPEG). Each tick: SnapshotSource.fetch()
#                returns JPEG bytes, we decode + downscale for YOLO, push a FrameResult
#                that also carries the original JPEG bytes for zero-loss display.
#          The SnapshotSource Protocol decouples the poller from any one camera vendor:
#            - ReolinkSnapshotSource wraps CameraController for the Reolink cmd=Snap path.
#            - UsbSnapshotSource opens an AVFoundation device locally.
#            - HttpUrlSnapshotSource (v2.24.0) pulls JPEG bytes from an arbitrary HTTP URL.
#              Used for phone cameras running IP Webcam (`http://<phone>:8080/photo.jpg`)
#              and for the Gateway laptop's planned HTTP snapshot endpoint (Phase B).
#              The battery-critical reason to prefer HTTP snapshot over RTSP streaming on
#              a phone: RTSP forces continuous H.264 encoding which cooks the SoC; HTTP
#              photo pulls only wake the camera HAL for each shot.
# SRP/DRY check: Pass — each class has one responsibility (RTSP capture, snapshot polling,
#          source adapter, manager dispatch). The downscale helper is shared.

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol, TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from camera_control import CameraController

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
    """A captured and pre-processed frame with metadata.

    `frame` is BGR numpy (potentially downscaled to _TARGET_WIDTH for YOLO).
    `jpeg_bytes` is the camera's original JPEG when the source provided one
    (snapshot mode); the dashboard prefers it for zero-loss display. RTSP
    capture leaves it as None — the dashboard re-encodes from the numpy array
    in that case.
    """
    frame: np.ndarray
    camera_name: str
    timestamp: float
    original_width: int
    original_height: int
    jpeg_bytes: Optional[bytes] = None


def _downscale_to_target_width(raw_frame: np.ndarray) -> np.ndarray:
    """Downscale to _TARGET_WIDTH preserving aspect ratio. Pass-through if already small."""
    h, w = raw_frame.shape[:2]
    if w <= _TARGET_WIDTH:
        return raw_frame
    scale = _TARGET_WIDTH / w
    new_w = _TARGET_WIDTH
    new_h = int(h * scale)
    return cv2.resize(raw_frame, (new_w, new_h), interpolation=cv2.INTER_AREA)


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
        return FrameResult(
            frame=_downscale_to_target_width(raw_frame),
            camera_name=self._camera_name,
            timestamp=time.time(),
            original_width=w,
            original_height=h,
        )


# ---------------------------------------------------------------------------
# Snapshot polling — Phase A (v2.18.0)
#
# An alternative acquisition mode for cameras that expose a high-quality JPEG
# endpoint. Pulls a still on a configurable interval instead of holding open a
# continuous video stream. No reconnect logic needed (each fetch is a single
# request), no decode-garbage failure mode (the JPEG is camera-encoded).
# ---------------------------------------------------------------------------

# Minimum allowed snapshot interval. Going below ~1s risks fetches overlapping
# the previous one (a 4K Reolink snap takes ~1.1s through reolink_aio).
_MIN_SNAPSHOT_INTERVAL = 1.0


class SnapshotSource(Protocol):
    """A source that returns JPEG bytes on demand. Plug-in for CameraSnapshotPoller.

    Implementations must be safe to call from a worker thread. They should
    return None (not raise) on transient failure — the poller will log and
    retry on the next interval.
    """

    @property
    def label(self) -> str: ...

    def fetch(self) -> Optional[bytes]: ...


class ReolinkSnapshotSource:
    """SnapshotSource backed by the existing CameraController.take_snapshot path,
    which uses reolink_aio's host.get_snapshot(channel) under the hood. That maps
    to Reolink's HTTP `cmd=Snap` endpoint and returns the camera's native 4K JPEG.
    """

    def __init__(self, controller: "CameraController", camera_id: str):
        self._controller = controller
        self._camera_id = camera_id

    @property
    def label(self) -> str:
        return f"reolink:{self._camera_id}"

    def fetch(self) -> Optional[bytes]:
        return self._controller.take_snapshot(self._camera_id)


class UsbSnapshotSource:
    """SnapshotSource for a local USB camera (AVFoundation on macOS).

    Holds the VideoCapture open between fetches — reopening on every tick is
    too slow (~300ms) and can race with the system camera daemon. Each fetch
    reads two frames (the first is often stale from the driver's ring buffer)
    and encodes the second at a high JPEG quality so the dashboard can pass
    it through without a lossy re-encode.

    Thread safety: the snapshot poller calls fetch() from a single worker
    thread, so no cross-poller contention. The internal lock only guards
    against the re-open branch happening mid-read.
    """

    def __init__(
        self,
        device_index: int,
        target_resolution: Optional[tuple] = None,
        jpeg_quality: int = 95,
        label: Optional[str] = None,
        auto_white_balance: bool = False,
        wb_strength: float = 0.8,
        autofocus: bool = True,
        warmup_frames: int = 3,
    ):
        self._device_index = device_index
        self._target_resolution = target_resolution
        self._jpeg_quality = jpeg_quality
        self._label = label or f"usb:{device_index}"
        self._auto_wb = auto_white_balance
        # Clamp so misconfig can't nuke the image.
        self._wb_strength = max(0.0, min(1.0, wb_strength))
        self._autofocus = autofocus
        # At 30fps each warmup frame costs ~33ms. 3 frames = ~100ms, enough
        # for continuous AF to chase a moving subject (chicks).
        self._warmup_frames = max(0, int(warmup_frames))
        self._cap: Optional[cv2.VideoCapture] = None
        self._lock = threading.Lock()

    @property
    def label(self) -> str:
        return self._label

    def _open(self) -> bool:
        cap = cv2.VideoCapture(self._device_index)
        if not cap.isOpened():
            try:
                cap.release()
            except Exception:
                pass
            return False
        if self._target_resolution is not None:
            w, h = self._target_resolution
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # AVFoundation via cv2 often ignores these silently — cv2.get() will
        # return 0.0 afterwards either way — but setting them is harmless and
        # UVC backends that honor them (DSHOW, V4L2) will engage continuous AF.
        if self._autofocus:
            cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        log.info(
            "UsbSnapshotSource '%s' opened at %dx%d (quality=%d, warmup=%d, autofocus=%s, auto_wb=%s)",
            self._label, actual_w, actual_h, self._jpeg_quality,
            self._warmup_frames, self._autofocus, self._auto_wb,
        )
        self._cap = cap
        return True

    def fetch(self) -> Optional[bytes]:
        with self._lock:
            if self._cap is None and not self._open():
                return None
            # Warmup reads before the real capture. AVFoundation's ring buffer
            # often serves the driver's previous snapshot on the first read
            # after idle, AND the camera's continuous autofocus/auto-exposure
            # need a beat to re-acquire a moving subject (chicks). Each read
            # is ~33ms at 30fps; 3 reads is ~100ms of catch-up.
            for _ in range(self._warmup_frames):
                self._cap.read()
            ret, frame = self._cap.read()
            if not ret or frame is None:
                # One reopen attempt, then give up for this tick
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
                if not self._open():
                    return None
                ret, frame = self._cap.read()
                if not ret or frame is None:
                    return None
            if self._auto_wb:
                frame = self._apply_gray_world_wb(frame, self._wb_strength)
            ok, buf = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality]
            )
            if not ok:
                return None
            return buf.tobytes()

    @staticmethod
    def _apply_gray_world_wb(frame: np.ndarray, strength: float) -> np.ndarray:
        """Gray-world auto white balance. Scales each BGR channel so the
        per-channel means converge on the overall mean, removing a uniform
        color cast (e.g. the orange from a chick-brooder heat lamp).

        `strength` interpolates between identity (0.0) and full correction
        (1.0). Values around 0.7–0.9 usually look natural — full correction
        over a scene that legitimately has dominant warm or cool content
        can swing the image too cool.
        """
        if strength <= 0.0:
            return frame
        avg = frame.reshape(-1, 3).mean(axis=0).astype(np.float32)  # B,G,R
        overall = float(avg.mean())
        if overall < 1.0:
            return frame  # pitch-black frame, nothing meaningful to correct
        scales = overall / np.maximum(avg, 1.0)
        # Interpolate: identity scales are all 1.0; strength controls blend.
        scales = 1.0 + strength * (scales - 1.0)
        corrected = frame.astype(np.float32) * scales.reshape(1, 1, 3)
        return np.clip(corrected, 0, 255).astype(np.uint8)


class HttpUrlSnapshotSource:
    """SnapshotSource that pulls JPEG bytes from an arbitrary HTTP endpoint.

    Primary use case: phone cameras running IP Webcam (Samsung S7 in the
    coop). IP Webcam exposes `http://<phone>:<port>/photo.jpg` which returns
    a single JPEG from the live preview, and `http://<phone>:<port>/focus`
    to trigger autofocus. Pulling stills at a 5–10s cadence is dramatically
    cheaper on phone battery than holding open an RTSP stream, which forces
    continuous H.264 encoding. Secondary use case: the planned GWTC HTTP
    snapshot service (Phase B) — same class, different URL.

    `trigger_focus=True` fires an AF request and waits `focus_wait`
    seconds before the actual photo GET. Defaults are tuned for IP Webcam
    on an older Android phone (S7): AF takes ~1.5s to settle after any
    meaningful subject/lighting change. The focus endpoint is optional —
    GET failures on it are logged and swallowed so the main /photo.jpg
    still runs. Auth is basic-auth via `(username, password)` or None.
    """

    def __init__(
        self,
        base_url: str,
        photo_path: str = "/photo.jpg",
        focus_path: Optional[str] = "/focus",
        trigger_focus: bool = False,
        focus_wait: float = 1.5,
        timeout: float = 15.0,
        auth: Optional[tuple] = None,
        label: Optional[str] = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._photo_url = self._base_url + photo_path
        self._focus_url = (self._base_url + focus_path) if (focus_path and trigger_focus) else None
        self._trigger_focus = trigger_focus and focus_path is not None
        self._focus_wait = max(0.0, float(focus_wait))
        self._timeout = float(timeout)
        self._auth = auth
        # The URL already carries its own scheme; don't prefix "http:" again.
        self._label = label or self._photo_url

    @property
    def label(self) -> str:
        return self._label

    def fetch(self) -> Optional[bytes]:
        # Deferred import — keep `requests` out of capture.py's import-time
        # graph for cameras that don't use this source.
        import requests

        if self._trigger_focus and self._focus_url is not None:
            try:
                requests.get(self._focus_url, timeout=min(5.0, self._timeout), auth=self._auth)
                if self._focus_wait > 0:
                    time.sleep(self._focus_wait)
            except Exception as exc:
                # AF is best-effort; the photo may still be fine without it.
                log.debug("AF trigger failed for %s: %s", self._label, exc)

        try:
            resp = requests.get(self._photo_url, timeout=self._timeout, auth=self._auth)
        except Exception as exc:
            log.warning("HTTP snapshot fetch failed for %s: %s", self._label, exc)
            return None

        if resp.status_code != 200:
            log.warning(
                "HTTP snapshot non-200 for %s: status=%d", self._label, resp.status_code,
            )
            return None

        body = resp.content
        # Sanity check: verify the JPEG SOI marker. IP Webcam will occasionally
        # serve an HTML error page (auth redirect, server not ready) with 200 —
        # catching it here saves a cv2.imdecode failure downstream.
        if not body or body[:2] != b"\xff\xd8":
            log.warning(
                "HTTP snapshot non-JPEG response for %s (%d bytes, first_bytes=%r)",
                self._label, len(body), body[:8] if body else b"",
            )
            return None
        return body


class CameraSnapshotPoller:
    """Periodic snapshot poller. Mirrors CameraCapture's public surface
    (start/stop/recent_frames/is_running/camera_name) so FrameCaptureManager
    can dispatch to either class without caring which.

    Cadence:
      - Default `snapshot_interval` between fetches (e.g. 5.0s for dashboard).
      - If `night_snapshot_interval` and `is_night_window()` are both provided
        and the callable returns True, the interval drops to that value (e.g.
        2.0s during the night detection window so YOLO has more chances per
        minute to see a slow-moving nocturnal predator).
    """

    def __init__(
        self,
        camera_name: str,
        source: SnapshotSource,
        snapshot_interval: float = 5.0,
        night_snapshot_interval: Optional[float] = None,
        is_night_window: Optional[Callable[[], bool]] = None,
        on_frame: Optional[Callable[[FrameResult], None]] = None,
        buffer_size: int = 10,
    ):
        self._camera_name = camera_name
        self._source = source
        self._snapshot_interval = max(_MIN_SNAPSHOT_INTERVAL, snapshot_interval)
        if snapshot_interval < _MIN_SNAPSHOT_INTERVAL:
            log.warning(
                "Camera '%s' snapshot_interval=%.2fs clamped to %.2fs to avoid overlapping fetches",
                camera_name, snapshot_interval, _MIN_SNAPSHOT_INTERVAL,
            )
        if night_snapshot_interval is not None:
            self._night_snapshot_interval = max(_MIN_SNAPSHOT_INTERVAL, night_snapshot_interval)
            if night_snapshot_interval < _MIN_SNAPSHOT_INTERVAL:
                log.warning(
                    "Camera '%s' night_snapshot_interval=%.2fs clamped to %.2fs",
                    camera_name, night_snapshot_interval, _MIN_SNAPSHOT_INTERVAL,
                )
        else:
            self._night_snapshot_interval = None
        self._is_night_window = is_night_window
        self._on_frame = on_frame

        self._buffer: deque[FrameResult] = deque(maxlen=buffer_size)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._consecutive_failures = 0

        # Burst mode (v2.20.0 — Phase C2). When something interesting happens
        # (Reolink reports motion), the guardian-side watcher calls
        # request_burst() to raise the polling rate for a short window so
        # YOLO has more chances to see whatever moved.
        self._burst_lock = threading.Lock()
        self._burst_deadline = 0.0           # monotonic timestamp
        self._burst_interval: Optional[float] = None

    @property
    def camera_name(self) -> str:
        return self._camera_name

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def recent_frames(self) -> list[FrameResult]:
        with self._lock:
            return list(self._buffer)

    def request_burst(self, duration_s: float = 30.0, interval_s: float = 1.0) -> None:
        """Temporarily raise the polling rate. Coalesces: overlapping calls
        extend the deadline (or lower the interval) rather than stack.
        Safe to call from any thread.
        """
        # Never allow a burst interval below the source's safe floor.
        interval_s = max(_MIN_SNAPSHOT_INTERVAL, interval_s)
        with self._burst_lock:
            new_deadline = time.monotonic() + duration_s
            if new_deadline > self._burst_deadline:
                self._burst_deadline = new_deadline
            if self._burst_interval is None or interval_s < self._burst_interval:
                self._burst_interval = interval_s
            log.info(
                "Camera '%s' — burst snapshot mode for %.0fs at %.2fs interval",
                self._camera_name, duration_s, interval_s,
            )

    def start(self) -> None:
        if self.is_running:
            log.warning("Snapshot poller already running for '%s'", self._camera_name)
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name=f"snapshot-{self._camera_name}",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "Snapshot polling started for '%s' — source=%s, interval=%.1fs%s",
            self._camera_name, self._source.label, self._snapshot_interval,
            f" (night={self._night_snapshot_interval:.1f}s)" if self._night_snapshot_interval else "",
        )

    def stop(self) -> None:
        if not self.is_running:
            return
        self._stop_event.set()
        self._thread.join(timeout=10)
        log.info("Snapshot polling stopped for '%s'", self._camera_name)

    def _effective_interval(self) -> float:
        # Precedence: active burst > night window > normal.
        with self._burst_lock:
            now = time.monotonic()
            if now < self._burst_deadline and self._burst_interval is not None:
                return self._burst_interval
            # Burst expired — clear the cached interval so a fresh burst
            # can set it cleanly.
            if now >= self._burst_deadline and self._burst_interval is not None:
                self._burst_interval = None
        if (self._night_snapshot_interval is not None
                and self._is_night_window is not None
                and self._is_night_window()):
            return self._night_snapshot_interval
        return self._snapshot_interval

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            interval = self._effective_interval()
            t_start = time.monotonic()

            jpeg = None
            try:
                jpeg = self._source.fetch()
            except Exception as exc:
                log.warning(
                    "Snapshot fetch raised for '%s' (source=%s): %s",
                    self._camera_name, self._source.label, exc,
                )

            if jpeg is None:
                self._consecutive_failures += 1
                # Log once on the first failure, then every 10 to avoid spam.
                if self._consecutive_failures == 1 or self._consecutive_failures % 10 == 0:
                    log.warning(
                        "Camera '%s' — snapshot returned None (consecutive=%d), will retry in %.1fs",
                        self._camera_name, self._consecutive_failures, interval,
                    )
                self._wait_remaining(t_start, interval)
                continue

            arr = np.frombuffer(jpeg, np.uint8)
            raw = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if raw is None:
                self._consecutive_failures += 1
                log.warning(
                    "Camera '%s' — JPEG decode failed (%d bytes) — skipping",
                    self._camera_name, len(jpeg),
                )
                self._wait_remaining(t_start, interval)
                continue

            if self._consecutive_failures:
                log.info(
                    "Camera '%s' — snapshots resumed after %d failures",
                    self._camera_name, self._consecutive_failures,
                )
                self._consecutive_failures = 0

            h, w = raw.shape[:2]
            result = FrameResult(
                frame=_downscale_to_target_width(raw),
                camera_name=self._camera_name,
                timestamp=time.time(),
                original_width=w,
                original_height=h,
                jpeg_bytes=jpeg,
            )

            with self._lock:
                self._buffer.append(result)

            if self._on_frame:
                try:
                    self._on_frame(result)
                except Exception as exc:
                    log.error("Frame callback error for '%s': %s", self._camera_name, exc)

            self._wait_remaining(t_start, interval)

    def _wait_remaining(self, t_start: float, interval: float) -> None:
        """Sleep so the loop fires at `interval` seconds from t_start, accounting
        for fetch latency. If the fetch took longer than the interval, fire again
        immediately.
        """
        remaining = interval - (time.monotonic() - t_start)
        if remaining > 0:
            self._stop_event.wait(remaining)


class FrameCaptureManager:
    """Manages capture/poller threads for multiple cameras."""

    def __init__(self, config: dict, on_frame: Optional[Callable[[FrameResult], None]] = None):
        detection_cfg = config.get("detection", {})
        self._frame_interval = detection_cfg.get("frame_interval_seconds", 1.0)
        self._on_frame = on_frame
        self._captures: dict[str, object] = {}  # CameraCapture or CameraSnapshotPoller

    def add_camera(
        self,
        camera_name: str,
        rtsp_url: Optional[str] = None,
        rtsp_transport: Optional[str] = None,
        device_index: Optional[int] = None,
        frame_interval: Optional[float] = None,
        snapshot_source: Optional[SnapshotSource] = None,
        snapshot_interval: Optional[float] = None,
        night_snapshot_interval: Optional[float] = None,
        is_night_window: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Register and start capturing from a camera.

        Three acquisition modes (mutually exclusive — first one matched wins):
          - snapshot_source set → CameraSnapshotPoller (Phase A+ snapshot mode)
          - device_index set    → CameraCapture (USB via AVFoundation)
          - rtsp_url set        → CameraCapture (RTSP)

        Args:
            frame_interval: Override the global detection interval for the
                RTSP/USB path. Non-detection RTSP/USB cameras use longer
                intervals (e.g. 10s) for snapshot polling.
            snapshot_interval / night_snapshot_interval / is_night_window:
                Forwarded to CameraSnapshotPoller when snapshot_source is set.
        """
        if camera_name in self._captures:
            log.warning("Camera '%s' already registered — skipping", camera_name)
            return

        if snapshot_source is not None:
            cap: object = CameraSnapshotPoller(
                camera_name=camera_name,
                source=snapshot_source,
                snapshot_interval=snapshot_interval if snapshot_interval is not None else 5.0,
                night_snapshot_interval=night_snapshot_interval,
                is_night_window=is_night_window,
                on_frame=self._on_frame,
            )
        else:
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

    def get_poller(self, camera_name: str):
        """Return the underlying capture/poller object for a camera, or None.

        External callers use this to reach CameraSnapshotPoller.request_burst
        (v2.20.0). Returning `object` typing because the manager intentionally
        dispatches to either CameraCapture or CameraSnapshotPoller — duck-typed.
        """
        return self._captures.get(camera_name)

    @property
    def active_cameras(self) -> list[str]:
        """Return names of cameras currently being captured."""
        return [name for name, cap in self._captures.items() if cap.is_running]
