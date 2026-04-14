# Author: Claude Opus 4.6 (1M context)
# Date: 14-April-2026
# PURPOSE: Cross-platform HTTP snapshot service for the generic USB webcam.
#          Exposes GET /photo.jpg returning a single warmed-up JPEG from
#          the locally attached camera, and GET /health for liveness probes.
#          Lets `usb-cam` move between any farm host (Mac Mini, MacBook Air,
#          Gateway laptop) without touching Guardian or pipeline code — the
#          consumers already speak HTTP snapshot via HttpUrlSnapshotSource
#          (capture.py:537) and capture_ip_webcam (tools/pipeline/capture.py:134).
#          Uses cv2.VideoCapture(index) with no backend flag so OpenCV
#          auto-selects AVFoundation (macOS), dshow (Windows), V4L2 (Linux).
#          Single-in-flight requests via an asyncio lock — opening the
#          camera from two handlers at once deadlocks the kernel driver
#          on macOS. No Laplacian ranking (Boss flagged GLM calibration
#          distrust in today's discussion); warmup-then-grab a single frame.
# SRP/DRY check: Pass — single responsibility is "turn a local camera into
#          a JPEG HTTP endpoint". No archiving, no detection, no consumer
#          logic. Full plan: docs/14-Apr-2026-portable-usb-cam-host-plan.md

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
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
# 15 frames at ~15 fps ≈ 1 s — materially longer than the old pipeline's 5×80ms.
# Heat-lamp-lit brooder scenes need that long for AE/AWB to converge.
WARMUP_FRAMES = int(os.environ.get("USB_CAM_WARMUP", "15"))
JPEG_QUALITY = int(os.environ.get("USB_CAM_JPEG_QUALITY", "95"))
# Sleep between warmup reads so the camera driver actually services each grab
# rather than returning the same buffered frame repeatedly.
WARMUP_FRAME_SLEEP_S = float(os.environ.get("USB_CAM_WARMUP_SLEEP", "0.06"))
# Open-camera timeout. On cold TCC denials AVFoundation can hang the open()
# call indefinitely; we cap it via asyncio.wait_for in the handler.
OPEN_TIMEOUT_S = float(os.environ.get("USB_CAM_OPEN_TIMEOUT", "10"))

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
# Single-in-flight lock
# ---------------------------------------------------------------------------
# AVFoundation (macOS) and dshow (Windows) both serialize access to a given
# device index at the driver level; two simultaneous open() calls against the
# same index reliably deadlock or return I/O errors. We serialize at the
# application layer to give a clean 503 instead of a hung request.

_camera_lock: Optional[asyncio.Lock] = None


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    global _camera_lock
    _camera_lock = asyncio.Lock()
    log.info(
        "usb-cam-host ready: device=%d requested=%dx%d warmup=%d jpeg_q=%d",
        DEVICE_INDEX, REQUESTED_WIDTH, REQUESTED_HEIGHT, WARMUP_FRAMES, JPEG_QUALITY,
    )
    yield
    log.info("usb-cam-host shutting down")


app = FastAPI(title="usb-cam-host", version="1.0.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Core capture — runs in the default threadpool via asyncio.to_thread
# ---------------------------------------------------------------------------

class CaptureError(RuntimeError):
    pass


def _capture_one_jpeg() -> tuple[bytes, int, int]:
    """Blocking capture: open, warmup, grab, encode, release. Returns
    (jpeg_bytes, negotiated_width, negotiated_height). Raises CaptureError
    on failure. Caller is responsible for serialization via the asyncio lock."""
    # No backend flag: let OpenCV pick the per-OS default. macOS → AVFoundation,
    # Windows → dshow, Linux → V4L2. This is the portability fix.
    cap = cv2.VideoCapture(DEVICE_INDEX)
    if not cap.isOpened():
        raise CaptureError(
            f"failed to open camera at device index {DEVICE_INDEX} "
            "(likely: camera unplugged, TCC/Camera permission not granted, "
            "or another process holds the device)"
        )
    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, REQUESTED_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, REQUESTED_HEIGHT)

        # Warmup reads — throw these away. Lets AE/AWB (and AF, on cameras
        # that support it) converge. We don't trigger AF explicitly because
        # UVC AF is unreliable on generic webcams; wall-clock convergence is
        # the only knob that consistently helps.
        frame: Optional[np.ndarray] = None
        for i in range(WARMUP_FRAMES):
            ok, frame = cap.read()
            if not ok:
                raise CaptureError(f"warmup read {i+1}/{WARMUP_FRAMES} failed")
            if WARMUP_FRAME_SLEEP_S > 0:
                time.sleep(WARMUP_FRAME_SLEEP_S)

        # Keeper
        ok, frame = cap.read()
        if not ok or frame is None:
            raise CaptureError("keeper read failed after warmup")

        h, w = frame.shape[:2]

        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise CaptureError("cv2.imencode JPEG failed on keeper frame")
        return bytes(buf), w, h
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    # Cheap health probe: open, read one frame (no warmup loop), close. This
    # verifies the camera is actually present and readable, not just that the
    # process is alive. Still serialized via the camera lock.
    assert _camera_lock is not None

    async def _probe():
        def _blocking():
            cap = cv2.VideoCapture(DEVICE_INDEX)
            if not cap.isOpened():
                raise CaptureError("camera not openable")
            try:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, REQUESTED_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, REQUESTED_HEIGHT)
                ok, frame = cap.read()
                if not ok or frame is None:
                    raise CaptureError("probe read failed")
                h, w = frame.shape[:2]
                return w, h
            finally:
                cap.release()

        return await asyncio.to_thread(_blocking)

    try:
        async with _camera_lock:
            w, h = await asyncio.wait_for(_probe(), timeout=OPEN_TIMEOUT_S)
    except asyncio.TimeoutError:
        log.warning("health: open timed out after %.1fs", OPEN_TIMEOUT_S)
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "device_index": DEVICE_INDEX,
                "error": f"open timed out after {OPEN_TIMEOUT_S}s",
            },
        )
    except CaptureError as exc:
        log.warning("health: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"ok": False, "device_index": DEVICE_INDEX, "error": str(exc)},
        )
    return {
        "ok": True,
        "device_index": DEVICE_INDEX,
        "resolution": [w, h],
        "requested_resolution": [REQUESTED_WIDTH, REQUESTED_HEIGHT],
        "warmup_frames": WARMUP_FRAMES,
        "jpeg_quality": JPEG_QUALITY,
    }


@app.get("/photo.jpg")
async def photo():
    assert _camera_lock is not None
    t0 = time.monotonic()
    try:
        async with _camera_lock:
            jpeg_bytes, w, h = await asyncio.wait_for(
                asyncio.to_thread(_capture_one_jpeg),
                timeout=OPEN_TIMEOUT_S + (WARMUP_FRAMES * (WARMUP_FRAME_SLEEP_S + 0.15)),
            )
    except asyncio.TimeoutError:
        log.warning("/photo.jpg: capture timed out")
        raise HTTPException(status_code=504, detail="capture timed out")
    except CaptureError as exc:
        log.warning("/photo.jpg: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    log.info("/photo.jpg served: %dx%d, %d bytes, %d ms", w, h, len(jpeg_bytes), elapsed_ms)
    return Response(
        content=jpeg_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Capture-Ms": str(elapsed_ms),
            "X-Capture-Resolution": f"{w}x{h}",
        },
    )


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
