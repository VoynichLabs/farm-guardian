# Author: Claude Opus 4.7 (1M context)
# Date: 18-April-2026 (adds manual exposure + manual focus plumbing for
#        the brooder heat-lamp clipping problem per
#        docs/16-Apr-2026-heat-lamp-orange-cast-investigation.md)
# PURPOSE: Cross-platform HTTP snapshot service for the generic USB webcam.
#          Exposes GET /photo.jpg returning the latest frame from a warm,
#          continuously-running camera, and GET /health for liveness probes.
#          Lets `usb-cam` move between any farm host (Mac Mini, MacBook Air,
#          Gateway laptop) without touching Guardian or pipeline code — the
#          consumers already speak HTTP snapshot via HttpUrlSnapshotSource
#          (capture.py:537) and capture_ip_webcam (tools/pipeline/capture.py:134).
#          Uses cv2.VideoCapture(index) with no backend flag so OpenCV
#          auto-selects AVFoundation (macOS), dshow (Windows), V4L2 (Linux).
#
#          v2.27.0 architecture: a daemon grabber thread holds the camera
#          open for the life of the process, reads frames at ~2 Hz, and
#          publishes the latest raw BGR frame to a lock-protected slot.
#          Request handlers copy the latest frame, apply WB, encode, and
#          return — ~50 ms per request instead of the 3.4 s open/warmup/
#          release churn of v2.26.x. Camera AE/AWB actually stabilizes
#          because the camera stays warm, which produces visibly sharper
#          frames under the heat lamp. The grabber auto-reconnects on
#          read failures (camera unplug, USB glitches, dshow hiccups).
#          Frame max-age gating (default 5 s) keeps us from serving stale
#          frames if the grabber ever hangs — handler returns 503 instead.
#
#          Two-stage color correction before JPEG encode for heat-lamp
#          brooder scenes: (1) gray-world WB at USB_CAM_WB_STRENGTH (default
#          0.8) cools the global cast; (2) targeted orange-hue desaturation
#          at USB_CAM_ORANGE_DESAT (default 0.75) pulls red out of the chicks
#          specifically, because gray-world alone cannot recover blue light
#          the heat lamp never emitted. /photo.jpg also accepts ?wb=X&os=Y
#          query overrides for live A/B tuning without a service restart.
# SRP/DRY check: Pass — single responsibility is "turn a local camera into
#          a JPEG HTTP endpoint". No archiving, no detection, no consumer
#          logic. Full plan: docs/14-Apr-2026-portable-usb-cam-host-plan.md

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response

# ---------------------------------------------------------------------------
# Config (env vars — single-service, small surface, no config file)
# ---------------------------------------------------------------------------

DEVICE_INDEX = int(os.environ.get("USB_CAM_DEVICE_INDEX", "0"))
REQUESTED_WIDTH = int(os.environ.get("USB_CAM_WIDTH", "1920"))
REQUESTED_HEIGHT = int(os.environ.get("USB_CAM_HEIGHT", "1080"))
JPEG_QUALITY = int(os.environ.get("USB_CAM_JPEG_QUALITY", "95"))

# Target interval between grabs. The camera may run faster; this is the
# application-side rate cap. 0.5 s ≈ 2 Hz is enough for Guardian's 5 s poll
# cadence and the pipeline's 60 s cadence, and it lets the camera's AE/AWB
# settle between reads rather than being hammered.
GRAB_INTERVAL_S = float(os.environ.get("USB_CAM_GRAB_INTERVAL", "0.5"))

# If the grabber hasn't produced a frame in this many seconds, requests
# return 503 instead of serving something stale. 5 s is comfortable at
# 2 Hz — normal latest-frame age is ~0.25 s.
MAX_FRAME_AGE_S = float(os.environ.get("USB_CAM_MAX_FRAME_AGE", "5.0"))

# Grabber reconnect backoff when open() fails or reads fail persistently.
RECONNECT_BACKOFF_S = float(os.environ.get("USB_CAM_RECONNECT_BACKOFF", "3.0"))
READ_FAILURE_THRESHOLD = int(os.environ.get("USB_CAM_READ_FAILURE_THRESHOLD", "5"))

# Gray-world white balance — removes the brooder heat-lamp's orange cast.
# AUTO_WB=true enables; STRENGTH 0.0–1.0 blends between identity (0.0) and
# full gray-world (1.0). 0.8 is the current brooder tune: pairs with the
# orange-hue desaturation pass below. Gray-world alone at 0.8 leaves chicks
# looking orange because the scene's incident light really is tungsten-red —
# the desat pass finishes the job by pulling orange saturation down on
# specifically the orange-hue range so the chicks read as yellow/cream.
AUTO_WB = os.environ.get("USB_CAM_AUTO_WB", "true").lower() in ("1", "true", "yes", "on")
WB_STRENGTH = max(0.0, min(1.0, float(os.environ.get("USB_CAM_WB_STRENGTH", "0.8"))))

# Targeted orange-hue desaturation — gray-world can't recover blue light that
# isn't there, so after WB we pull saturation on the orange hue band
# (OpenCV H=5..30) down by this factor. 1.0 = off, 0.0 = fully desaturated.
# 0.75 is tuned to chicks-under-heat-lamp: takes the "pumpkin" look off the
# birds while leaving non-orange hues untouched.
ORANGE_DESAT = max(0.0, min(1.0, float(os.environ.get("USB_CAM_ORANGE_DESAT", "0.75"))))
ORANGE_HUE_LO = int(os.environ.get("USB_CAM_ORANGE_HUE_LO", "5"))
ORANGE_HUE_HI = int(os.environ.get("USB_CAM_ORANGE_HUE_HI", "30"))

# Highlight roll-off — the UVC webcam's internal auto-exposure clips the
# red channel to 255 under the heat lamp and macOS AVFoundation gives us
# no way to lower the shutter from userland (tested 18-Apr-2026: every
# CAP_PROP_* .set() returns False; setExposureModeCustom is iOS-only;
# jtfrey/uvc-util segfaults on modern macOS; no maintained uvc-util fork
# exists for Sequoia+). Since we can't prevent the clip at capture time,
# we apply a local-tone-map-ish roll-off after capture: pixels above
# USB_CAM_HIGHLIGHT_KNEE get their luminance compressed toward the knee,
# which reduces the "nuclear white" blown-out look on chicks. It cannot
# recover data that was clipped at 255, but it stops the blown regions
# from dominating the frame visually. 1.0 = off.
HIGHLIGHT_KNEE = max(0.0, min(1.0, float(os.environ.get("USB_CAM_HIGHLIGHT_KNEE", "0.75"))))
HIGHLIGHT_STRENGTH = max(0.0, min(1.0, float(os.environ.get("USB_CAM_HIGHLIGHT_STRENGTH", "0.6"))))

# Unsharp-mask sharpening — fixed-focus UVC webcams serving a brooder
# scene often read as "soft" from the VLM. A mild unsharp mask recovers
# perceived edge detail without introducing halos. 0.0 = off.
# amount ≈ 0.6–1.0 is mild-to-moderate; above 1.5 gets crunchy.
SHARPEN_AMOUNT = max(0.0, float(os.environ.get("USB_CAM_SHARPEN_AMOUNT", "0.8")))
SHARPEN_RADIUS = max(1, int(os.environ.get("USB_CAM_SHARPEN_RADIUS", "3")))

# Manual exposure + focus clamp — per
# docs/16-Apr-2026-heat-lamp-orange-cast-investigation.md, the brooder's
# real problem is that auto-exposure clips the red channel to 255 under
# the heat lamp. Gray-world WB then amplifies the un-clipped channels past
# 255 → nuclear pink. Clamping the camera's exposure BELOW the clipping
# point gives the WB pipeline real color data to work with.
#
# "Don't touch if unset" semantics on every knob: if the env var is
# absent, OpenCV's auto-exposure / auto-focus stays in charge. This
# matters because bad manual values produce worse frames than auto does,
# and AVFoundation / dshow / V4L2 interpret CAP_PROP_EXPOSURE values
# differently. Tuning recipe lives in the investigation doc, but briefly:
#
# - AVFoundation (macOS): CAP_PROP_AUTO_EXPOSURE=0.25 = manual, 0.75 = auto.
#   CAP_PROP_EXPOSURE is 2^x seconds; -7 = 1/128s, -6 = 1/64s, etc.
# - V4L2 (Linux): CAP_PROP_AUTO_EXPOSURE=1 = manual, 3 = auto.
#   CAP_PROP_EXPOSURE is in 100 µs units.
# - DirectShow (Windows): similar to V4L2 but vendor-dependent.
#
# AUTO_EXPOSURE accepts "manual" / "auto" / a raw float (for non-standard
# backends). Same for AUTOFOCUS: "on" / "off" / raw int.
AUTO_EXPOSURE = os.environ.get("USB_CAM_AUTO_EXPOSURE", "").strip().lower()
EXPOSURE_VALUE_RAW = os.environ.get("USB_CAM_EXPOSURE", "").strip()
AUTOFOCUS = os.environ.get("USB_CAM_AUTOFOCUS", "").strip().lower()
FOCUS_VALUE_RAW = os.environ.get("USB_CAM_FOCUS", "").strip()


def _resolve_auto_exposure_value() -> Optional[float]:
    """Map a semantic env value to the backend-specific float OpenCV wants.
    Returns None if unset (= don't touch)."""
    if not AUTO_EXPOSURE:
        return None
    if AUTO_EXPOSURE in ("manual", "off", "false", "0"):
        # AVFoundation uses 0.25 for manual; V4L2 uses 1. The AVFoundation
        # value happens to also work on most V4L2 drivers (gets rounded),
        # but if a Linux install sees no effect, override with
        # USB_CAM_AUTO_EXPOSURE=1 directly.
        return 0.25 if sys.platform == "darwin" else 1.0
    if AUTO_EXPOSURE in ("auto", "on", "true"):
        return 0.75 if sys.platform == "darwin" else 3.0
    try:
        return float(AUTO_EXPOSURE)
    except ValueError:
        log.warning("USB_CAM_AUTO_EXPOSURE=%r is not recognized; leaving auto-exposure untouched", AUTO_EXPOSURE)
        return None


def _resolve_exposure_value() -> Optional[float]:
    if not EXPOSURE_VALUE_RAW:
        return None
    try:
        return float(EXPOSURE_VALUE_RAW)
    except ValueError:
        log.warning("USB_CAM_EXPOSURE=%r is not a number; leaving exposure untouched", EXPOSURE_VALUE_RAW)
        return None


def _resolve_autofocus_value() -> Optional[float]:
    if not AUTOFOCUS:
        return None
    if AUTOFOCUS in ("off", "manual", "false", "0"):
        return 0.0
    if AUTOFOCUS in ("on", "auto", "true", "1"):
        return 1.0
    try:
        return float(AUTOFOCUS)
    except ValueError:
        log.warning("USB_CAM_AUTOFOCUS=%r is not recognized; leaving autofocus untouched", AUTOFOCUS)
        return None


def _resolve_focus_value() -> Optional[float]:
    if not FOCUS_VALUE_RAW:
        return None
    try:
        return float(FOCUS_VALUE_RAW)
    except ValueError:
        log.warning("USB_CAM_FOCUS=%r is not a number; leaving focus untouched", FOCUS_VALUE_RAW)
        return None

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=os.environ.get("USB_CAM_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("usb-cam-host")


# ---------------------------------------------------------------------------
# Latest-frame slot (shared between grabber thread and request handlers)
# ---------------------------------------------------------------------------

@dataclass
class _LatestFrame:
    frame: np.ndarray   # BGR, raw from the camera, NO WB applied yet
    width: int
    height: int
    monotonic_ts: float  # time.monotonic() at grab
    wall_ts: float       # time.time() at grab — logged for diagnostics
    sequence: int        # monotonically increasing counter per grab


_latest: Optional[_LatestFrame] = None
_latest_lock = threading.Lock()
_grabber_stop = threading.Event()
_grabber_thread: Optional[threading.Thread] = None
_grabber_opened_at: Optional[float] = None  # when cap last successfully opened
_grabber_total_grabs = 0
_grabber_total_failures = 0


def _grabber_loop() -> None:
    """Runs in a daemon thread. Keeps the camera open, reads frames at
    ~GRAB_INTERVAL_S cadence, publishes the latest to `_latest`. Auto-
    reconnects on persistent read failures. Exits when `_grabber_stop` is set."""
    global _latest, _grabber_opened_at, _grabber_total_grabs, _grabber_total_failures

    cap: Optional[cv2.VideoCapture] = None
    consecutive_failures = 0
    sequence = 0

    def _open() -> Optional[cv2.VideoCapture]:
        # No backend flag — let OpenCV pick AVFoundation / dshow / V4L2.
        c = cv2.VideoCapture(DEVICE_INDEX)
        if not c.isOpened():
            return None
        c.set(cv2.CAP_PROP_FRAME_WIDTH, REQUESTED_WIDTH)
        c.set(cv2.CAP_PROP_FRAME_HEIGHT, REQUESTED_HEIGHT)

        # Manual exposure / focus — only touched when env vars are set.
        # Order matters: disable auto-mode first, then set the fixed value.
        # Some backends ignore the value knob while auto is still on.
        ae = _resolve_auto_exposure_value()
        if ae is not None:
            ok = c.set(cv2.CAP_PROP_AUTO_EXPOSURE, ae)
            log.info("grabber: CAP_PROP_AUTO_EXPOSURE=%.3f set_ok=%s", ae, ok)
        ev = _resolve_exposure_value()
        if ev is not None:
            ok = c.set(cv2.CAP_PROP_EXPOSURE, ev)
            log.info("grabber: CAP_PROP_EXPOSURE=%.3f set_ok=%s", ev, ok)
        af = _resolve_autofocus_value()
        if af is not None:
            ok = c.set(cv2.CAP_PROP_AUTOFOCUS, af)
            log.info("grabber: CAP_PROP_AUTOFOCUS=%.3f set_ok=%s", af, ok)
        fv = _resolve_focus_value()
        if fv is not None:
            ok = c.set(cv2.CAP_PROP_FOCUS, fv)
            log.info("grabber: CAP_PROP_FOCUS=%.3f set_ok=%s", fv, ok)
        return c

    try:
        while not _grabber_stop.is_set():
            if cap is None:
                cap = _open()
                if cap is None:
                    log.warning(
                        "grabber: open failed (device=%d). Retrying in %.1fs. "
                        "Likely: camera unplugged, TCC/Camera permission not "
                        "granted, or another process holds the device.",
                        DEVICE_INDEX, RECONNECT_BACKOFF_S,
                    )
                    _grabber_stop.wait(RECONNECT_BACKOFF_S)
                    continue
                _grabber_opened_at = time.monotonic()
                consecutive_failures = 0
                log.info("grabber: camera opened (device=%d)", DEVICE_INDEX)

            ok, frame = cap.read()
            if not ok or frame is None:
                consecutive_failures += 1
                _grabber_total_failures += 1
                log.warning("grabber: read failed (consec=%d)", consecutive_failures)
                if consecutive_failures >= READ_FAILURE_THRESHOLD:
                    log.warning(
                        "grabber: %d consecutive read failures — releasing "
                        "camera and reopening after %.1fs backoff",
                        consecutive_failures, RECONNECT_BACKOFF_S,
                    )
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    _grabber_opened_at = None
                    _grabber_stop.wait(RECONNECT_BACKOFF_S)
                else:
                    # Brief wait between read retries — don't hot-loop.
                    _grabber_stop.wait(0.1)
                continue

            consecutive_failures = 0
            sequence += 1
            _grabber_total_grabs += 1
            h, w = frame.shape[:2]
            now_m = time.monotonic()
            now_w = time.time()

            # Publish atomically. The frame is the raw camera frame; WB is
            # applied on the read-side (per-request) so we don't waste CPU
            # pre-processing frames nobody asks for.
            with _latest_lock:
                _latest = _LatestFrame(
                    frame=frame, width=w, height=h,
                    monotonic_ts=now_m, wall_ts=now_w, sequence=sequence,
                )

            # Target interval pacing. _grabber_stop.wait returns True if
            # signaled, so this exits cleanly on shutdown.
            if _grabber_stop.wait(GRAB_INTERVAL_S):
                break
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        log.info(
            "grabber: stopped (total_grabs=%d total_failures=%d)",
            _grabber_total_grabs, _grabber_total_failures,
        )


def _start_grabber() -> None:
    global _grabber_thread
    _grabber_stop.clear()
    _grabber_thread = threading.Thread(
        target=_grabber_loop, name="usb-cam-grabber", daemon=True,
    )
    _grabber_thread.start()


def _stop_grabber(timeout: float = 3.0) -> None:
    _grabber_stop.set()
    if _grabber_thread is not None:
        _grabber_thread.join(timeout=timeout)


# ---------------------------------------------------------------------------
# FastAPI lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    log.info(
        "usb-cam-host ready: device=%d requested=%dx%d grab_interval=%.2fs "
        "max_age=%.1fs jpeg_q=%d auto_wb=%s wb_strength=%.2f "
        "auto_exposure=%r exposure=%r autofocus=%r focus=%r",
        DEVICE_INDEX, REQUESTED_WIDTH, REQUESTED_HEIGHT, GRAB_INTERVAL_S,
        MAX_FRAME_AGE_S, JPEG_QUALITY, AUTO_WB, WB_STRENGTH,
        AUTO_EXPOSURE or "untouched", EXPOSURE_VALUE_RAW or "untouched",
        AUTOFOCUS or "untouched", FOCUS_VALUE_RAW or "untouched",
    )
    _start_grabber()
    try:
        yield
    finally:
        log.info("usb-cam-host shutting down — signaling grabber")
        _stop_grabber()


app = FastAPI(title="usb-cam-host", version="2.27.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Frame processing
# ---------------------------------------------------------------------------

def _apply_gray_world_wb(frame: np.ndarray, strength: float) -> np.ndarray:
    """Gray-world auto white balance. Scales each BGR channel so the per-channel
    means converge on the overall mean, removing a uniform color cast (chick
    brooder heat-lamp orange). Ported from
    capture.py:UsbSnapshotSource._apply_gray_world_wb so the two paths stay
    interchangeable. `strength` interpolates between identity (0.0) and full
    correction (1.0)."""
    if strength <= 0.0:
        return frame
    avg = frame.reshape(-1, 3).mean(axis=0).astype(np.float32)  # B,G,R
    overall = float(avg.mean())
    if overall < 1.0:
        return frame  # pitch-black frame, nothing meaningful to correct
    scales = overall / np.maximum(avg, 1.0)
    scales = 1.0 + strength * (scales - 1.0)
    corrected = frame.astype(np.float32) * scales.reshape(1, 1, 3)
    return np.clip(corrected, 0, 255).astype(np.uint8)


def _apply_highlight_rolloff(frame: np.ndarray, knee: float, strength: float) -> np.ndarray:
    """Soft-clip the highlights. Pixels above `knee` (0..1 fraction of full
    scale) get their values compressed toward knee * 255 with a cubic
    falloff scaled by `strength` (0..1). Cannot recover data clipped at
    255 by the sensor, but reduces the visual dominance of blown regions.
    No-op when strength <= 0."""
    if strength <= 0.0:
        return frame
    knee_255 = knee * 255.0
    f = frame.astype(np.float32)
    over = np.maximum(f - knee_255, 0.0)
    headroom = max(1.0, 255.0 - knee_255)
    # Cubic ease-out pulls values strongly toward the knee when far above it,
    # lightly when just above. strength scales how aggressively we pull.
    t = np.clip(over / headroom, 0.0, 1.0)
    compressed = knee_255 + headroom * (1.0 - strength * (1.0 - (1.0 - t) ** 3))
    out = np.where(f > knee_255, compressed, f)
    return np.clip(out, 0, 255).astype(np.uint8)


def _apply_unsharp_mask(frame: np.ndarray, amount: float, radius: int) -> np.ndarray:
    """Standard unsharp mask: frame + amount * (frame - gaussian_blur(frame)).
    Cheap, ~3-5 ms on a 1080p frame. No-op when amount <= 0."""
    if amount <= 0.0:
        return frame
    k = max(1, radius | 1)  # kernel size must be odd
    blurred = cv2.GaussianBlur(frame, (k, k), 0)
    sharpened = cv2.addWeighted(frame, 1.0 + amount, blurred, -amount, 0)
    return sharpened


def _apply_orange_desat(frame: np.ndarray, factor: float, hue_lo: int, hue_hi: int) -> np.ndarray:
    """Desaturate pixels whose hue falls in the orange band (OpenCV H in
    [hue_lo, hue_hi], where full range is 0–180). Used after gray-world to
    deal with heat-lamp scenes where the incident light is physically red:
    gray-world can't synthesize blue that wasn't there, so the fix is to
    pull saturation on the orange hues instead. factor=1.0 is a no-op;
    factor=0.0 fully desaturates orange pixels to gray at their own value."""
    if factor >= 1.0:
        return frame
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.float32)
    h = hsv[:, :, 0]
    mask = (h >= hue_lo) & (h <= hue_hi)
    hsv[:, :, 1][mask] *= factor
    return cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)


def _snapshot_latest() -> tuple[np.ndarray, int, int, float, int]:
    """Atomically copy out the latest frame. Raises HTTPException(503) if
    there is no frame yet or the frame is older than MAX_FRAME_AGE_S."""
    with _latest_lock:
        latest = _latest
    if latest is None:
        raise HTTPException(
            status_code=503,
            detail="no frame grabbed yet — grabber is still starting or the "
            "camera hasn't opened (check /health for details)",
        )
    age = time.monotonic() - latest.monotonic_ts
    if age > MAX_FRAME_AGE_S:
        raise HTTPException(
            status_code=503,
            detail=f"latest frame is {age:.1f}s old (max {MAX_FRAME_AGE_S}s) "
            "— the grabber has stalled or lost the camera",
        )
    # Deep copy the numpy array so the grabber can overwrite its slot without
    # racing the handler's WB + encode.
    return latest.frame.copy(), latest.width, latest.height, age, latest.sequence


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    with _latest_lock:
        latest = _latest
    grabber_alive = _grabber_thread is not None and _grabber_thread.is_alive()
    opened_at = _grabber_opened_at

    if latest is None:
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "device_index": DEVICE_INDEX,
                "grabber_alive": grabber_alive,
                "camera_open": opened_at is not None,
                "error": "no frame grabbed yet",
                "total_grabs": _grabber_total_grabs,
                "total_failures": _grabber_total_failures,
            },
        )

    age = time.monotonic() - latest.monotonic_ts
    stale = age > MAX_FRAME_AGE_S
    return JSONResponse(
        status_code=503 if stale else 200,
        content={
            "ok": not stale,
            "device_index": DEVICE_INDEX,
            "resolution": [latest.width, latest.height],
            "requested_resolution": [REQUESTED_WIDTH, REQUESTED_HEIGHT],
            "grabber_alive": grabber_alive,
            "camera_open": opened_at is not None,
            "latest_frame_age_ms": int(age * 1000),
            "latest_frame_sequence": latest.sequence,
            "total_grabs": _grabber_total_grabs,
            "total_failures": _grabber_total_failures,
            "grab_interval_s": GRAB_INTERVAL_S,
            "max_frame_age_s": MAX_FRAME_AGE_S,
            "jpeg_quality": JPEG_QUALITY,
            "auto_wb": AUTO_WB,
            "wb_strength": WB_STRENGTH,
            "auto_exposure": AUTO_EXPOSURE or None,
            "exposure": EXPOSURE_VALUE_RAW or None,
            "autofocus": AUTOFOCUS or None,
            "focus": FOCUS_VALUE_RAW or None,
            "highlight_knee": HIGHLIGHT_KNEE,
            "highlight_strength": HIGHLIGHT_STRENGTH,
            "sharpen_amount": SHARPEN_AMOUNT,
            "sharpen_radius": SHARPEN_RADIUS,
        },
    )


@app.get("/photo.jpg")
async def photo(wb: Optional[float] = None, os_: Optional[float] = Query(default=None, alias="os")):
    t0 = time.monotonic()
    frame, w, h, age, sequence = _snapshot_latest()

    # Explicit ?wb=X and ?os=Y overrides win; otherwise env-configured
    # defaults. Lets operators A/B tune live without a service restart.
    # Both clamped to [0.0, 1.0].
    if wb is not None:
        effective_wb = max(0.0, min(1.0, wb))
        apply_wb = effective_wb > 0.0
    else:
        effective_wb = WB_STRENGTH
        apply_wb = AUTO_WB

    effective_os = max(0.0, min(1.0, os_)) if os_ is not None else ORANGE_DESAT

    # WB + encode are CPU-bound; run off the event loop so concurrent requests
    # don't serialize on them. (The grabber thread is still the sole camera
    # owner; this only affects request fan-in.)
    def _process() -> bytes:
        # Processing order: highlight roll-off FIRST (compresses clipped
        # whites before WB amplifies them), then gray-world WB, then
        # orange desaturation (brings chicks back toward neutral), then
        # unsharp mask (sharpens the result, last so it doesn't
        # amplify color noise from the earlier passes).
        out = _apply_highlight_rolloff(frame, HIGHLIGHT_KNEE, HIGHLIGHT_STRENGTH)
        out = _apply_gray_world_wb(out, effective_wb) if apply_wb else out
        out = _apply_orange_desat(out, effective_os, ORANGE_HUE_LO, ORANGE_HUE_HI)
        out = _apply_unsharp_mask(out, SHARPEN_AMOUNT, SHARPEN_RADIUS)
        ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise CaptureError("cv2.imencode JPEG failed")
        return bytes(buf)

    loop = asyncio.get_event_loop()
    try:
        jpeg_bytes = await loop.run_in_executor(None, _process)
    except CaptureError as exc:
        log.warning("/photo.jpg: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    # Only log one-in-20 served frames to keep the log readable under 5 s
    # polling — otherwise log.info fires every 5 s per consumer.
    if sequence % 20 == 0:
        log.info(
            "/photo.jpg served: seq=%d %dx%d %d bytes age=%dms elapsed=%dms",
            sequence, w, h, len(jpeg_bytes), int(age * 1000), elapsed_ms,
        )
    return Response(
        content=jpeg_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Capture-Ms": str(elapsed_ms),
            "X-Capture-Resolution": f"{w}x{h}",
            "X-Frame-Age-Ms": str(int(age * 1000)),
            "X-Frame-Sequence": str(sequence),
        },
    )


class CaptureError(RuntimeError):
    """Retained for the WB/encode path."""


# ---------------------------------------------------------------------------
# CLI entry point (for launchd / Shawl / manual runs)
# ---------------------------------------------------------------------------

def main() -> None:
    import uvicorn
    port = int(os.environ.get("USB_CAM_PORT", "8089"))
    host = os.environ.get("USB_CAM_HOST", "0.0.0.0")
    log.info("starting uvicorn on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
