# Author: Claude Opus 4.6 (1M context)
# Date: 14-April-2026 (v2.27.0 — continuous-capture architecture)
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
#          Applies gray-world white balance before JPEG encode so the
#          brooder's heat-lamp cast doesn't render the whole frame orange.
#          Toggle via USB_CAM_AUTO_WB / USB_CAM_WB_STRENGTH; keeping the
#          correction in the service means it moves with the camera to
#          any host.
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
from fastapi import FastAPI, HTTPException
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
# full gray-world (1.0). 0.5 is tuned for a heat-lamp-lit brooder scene:
# it removes the orange cast without swinging the frame cold. 0.8 over-
# corrects — chicks go green. Raise back to 0.8 if the camera moves to
# neutral light.
AUTO_WB = os.environ.get("USB_CAM_AUTO_WB", "true").lower() in ("1", "true", "yes", "on")
WB_STRENGTH = max(0.0, min(1.0, float(os.environ.get("USB_CAM_WB_STRENGTH", "0.5"))))

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
        "usb-cam-host ready (v2.27 continuous): device=%d requested=%dx%d "
        "grab_interval=%.2fs max_age=%.1fs jpeg_q=%d auto_wb=%s wb_strength=%.2f",
        DEVICE_INDEX, REQUESTED_WIDTH, REQUESTED_HEIGHT, GRAB_INTERVAL_S,
        MAX_FRAME_AGE_S, JPEG_QUALITY, AUTO_WB, WB_STRENGTH,
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
        },
    )


@app.get("/photo.jpg")
async def photo():
    t0 = time.monotonic()
    frame, w, h, age, sequence = _snapshot_latest()

    # WB + encode are CPU-bound; run off the event loop so concurrent requests
    # don't serialize on them. (The grabber thread is still the sole camera
    # owner; this only affects request fan-in.)
    def _process() -> bytes:
        out = _apply_gray_world_wb(frame, WB_STRENGTH) if AUTO_WB else frame
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
