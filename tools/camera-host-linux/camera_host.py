"""
Author: Claude Opus 5
Date: 05-August-2026
PURPOSE: Linux camera host for the Birdcatraz Raspberry Pi 5. Serves ONE camera
    over HTTP as `GET /photo.jpg` (JPEG) and `GET /health` (JSON), matching the
    wire contract of the macOS/Windows `tools/usb-cam-host/usb_cam_host.py`, so
    Guardian's `HttpUrlSnapshotSource` and the pipeline's `capture_ip_webcam`
    need nothing but a new URL.

    THE ENTIRE POINT OF THIS FILE is that identity is structural, not inferred.
    It opens exactly one device path — a `/dev/v4l/by-id/usb-<VENDOR>_<SERIAL>-…`
    symlink derived from the camera's own USB serial number — and if that path is
    absent it serves 503 and says so. There is no index fallback, no device-name
    substring match, no unique-resolution probe, no picture-comparison test and no
    PREFER_EXTERNAL. Those five successive identity heuristics on the macOS host
    exist because macOS makes the serial awkward to reach from OpenCV; udev hands
    it to us directly. Two services cannot land on one camera when each opens a
    distinct, serial-derived path, which is the 04/05-Aug-2026 collision class
    eliminated by construction rather than patched.

    DO NOT add an index fallback "just in case the path is missing". A missing
    path means the camera is missing. Guessing is the bug this replaces.

    ⛔ NO IMAGE PROCESSING. Boss directive 05-Aug-2026: the frame is encoded and
    served exactly as the sensor delivered it. No gray-world white balance, no
    orange desaturation, no highlight roll-off, no unsharp mask — all of which
    exist on the macOS host and have each, at some point, been suspected of
    wrecking a picture (see docs/16-Apr-2026-heat-lamp-orange-cast-investigation.md).
    None of it is coming back here. If a picture looks wrong on this host, the
    cause is the camera, the lens, or the light — there is no processing layer
    left to blame, and that is the entire point. Camera-side V4L2 controls
    (gain, exposure) are NOT processing and are configured via FARMCAM_V4L2_CTRLS.

INTEGRATION POINTS:
    - Consumed by Guardian `capture.py::HttpUrlSnapshotSource` (`snapshot_method:
      "http_url"`) and by `tools/pipeline` `capture_ip_webcam`
      (`capture_method: "ip_webcam"`). Both just GET /photo.jpg.
    - Supervised by systemd `farmcam@<camera-id>.service`, configured by
      `/etc/farmcam/<camera-id>.env`. systemd replaces the bespoke watchdogs the
      Windows/macOS hosts needed (`Restart=always`).
    - `/health`'s `acquire_stalled_s` is the field the farm's 30-second triage
      table keys on: climbing = process wedged (it restarts itself), 0.0 while the
      camera is missing = camera genuinely absent, hands-on required.

DEPENDENCIES: python3-opencv (apt, provides V4L2 capture), numpy, fastapi,
    uvicorn, v4l-utils. No ffmpeg — capture is OpenCV/V4L2 only, so there is no
    dshow-zombie equivalent to watchdog.

SRP/DRY check: Pass — deliberately a separate file from `usb_cam_host.py` rather
    than a fourth platform branch in it. That file is ~1,500 lines, the large
    majority of it identity heuristics and platform branches this hardware does
    not need; inheriting them would import the exact complexity this
    rearchitecture exists to delete.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("farmcam")


def _env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# --- Configuration -----------------------------------------------------------
# DEVICE_PATH is the only identity input and is REQUIRED. It should be a
# /dev/v4l/by-id/ symlink so it is keyed on the USB serial; a bare /dev/videoN is
# accepted but is position-based and will betray you the moment plug order
# changes, so the service warns loudly about it at startup.
CAMERA_ID = _env_str("FARMCAM_ID", "camera")
DEVICE_PATH = _env_str("FARMCAM_DEVICE_PATH")
PORT = _env_int("FARMCAM_PORT", 8089)
REQ_W = _env_int("FARMCAM_WIDTH", 1280)
REQ_H = _env_int("FARMCAM_HEIGHT", 720)
GRAB_INTERVAL_S = _env_float("FARMCAM_GRAB_INTERVAL_S", 0.5)
MAX_FRAME_AGE_S = _env_float("FARMCAM_MAX_FRAME_AGE_S", 5.0)
JPEG_QUALITY = _env_int("FARMCAM_JPEG_QUALITY", 95)
WARMUP_FRAMES = _env_int("FARMCAM_WARMUP", 15)
# ACQUIRE_STALL_S: how long we tolerate failing to acquire a camera that IS
# present before exiting so systemd restarts us. Mirrors v2.61.0 on macOS. Only
# armed when the device path EXISTS — an absent camera is hardware, and
# restarting in a loop against it would be noise, not a fix.
ACQUIRE_STALL_S = _env_float("FARMCAM_ACQUIRE_STALL_S", 300.0)

# Camera-side V4L2 controls, applied after open. NOT image processing — these ask
# the sensor for a better frame rather than editing a bad one. GAIN is here
# because the 1080p webcam (serial 240725172848) was found on 05-Aug-2026 with
# gain pinned at 0 against a default of 32, which produces a black frame on any
# host and matches its entire history of black output.
V4L2_CTRLS = _env_str("FARMCAM_V4L2_CTRLS")  # e.g. "gain=32,auto_exposure=3"

# FOURCC: "MJPG" forces the camera's MJPEG stream; "auto"/empty leaves the format
# to OpenCV. Both Birdcatraz cameras advertise ONLY MJPG, so in practice this
# changes nothing for them — it exists for a future camera that offers a choice.
#
# ⛔ DO NOT reach for this to fix the dashcam's daylight overexposure. Measured
# 06-Aug-2026 on the live endpoint: MJPG, auto and an explicit YUYV request all
# produced mean ~220 with ~41% of pixels clipped white. An earlier note in this
# file claimed a libv4l/YUYV path fixed it (mean 114, 0.9% clipped) — that was a
# MEASUREMENT ERROR and is retracted. `v4l2-ctl --set-fmt-video=pixelformat=YUYV`
# on a camera that only advertises MJPG does not yield clean YUYV; the captured
# file was not a whole multiple of a YUYV frame, so treating it as a Y plane was
# really averaging compressed JPEG bytes, which land near 114 by coincidence.
# See the dashcam exposure note in docs/05-Aug-2026-birdcatraz-pi5-bringup-log.md.
FOURCC = _env_str("FARMCAM_FOURCC", "MJPG")


@dataclass
class Frame:
    image: np.ndarray
    width: int
    height: int
    sequence: int
    monotonic_ts: float


# --- Grabber -----------------------------------------------------------------

_latest: Optional[Frame] = None
_latest_lock = threading.Lock()
_stop = threading.Event()
_grabber_thread: Optional[threading.Thread] = None
_grabber_opened_at: Optional[float] = None
_total_grabs = 0
_total_failures = 0
_acquire_stall_since: Optional[float] = None


def _device_present() -> bool:
    """The camera is present iff its path resolves. That is the whole test."""
    return bool(DEVICE_PATH) and os.path.exists(DEVICE_PATH)


def _apply_v4l2_controls() -> None:
    """Best-effort: a control this camera does not implement must not stop it
    serving frames."""
    if not V4L2_CTRLS:
        return
    for pair in V4L2_CTRLS.split(","):
        pair = pair.strip()
        if not pair:
            continue
        try:
            subprocess.run(
                ["v4l2-ctl", "-d", DEVICE_PATH, f"--set-ctrl={pair}"],
                capture_output=True, timeout=10, check=False,
            )
            log.info("grabber: applied v4l2 control %s", pair)
        except Exception as exc:  # noqa: BLE001 - never fatal
            log.warning("grabber: v4l2 control %r failed: %s", pair, exc)


def _open() -> Optional[cv2.VideoCapture]:
    if not _device_present():
        return None
    cap = cv2.VideoCapture(DEVICE_PATH, cv2.CAP_V4L2)
    if not cap.isOpened():
        cap.release()
        return None
    # See the FOURCC comment above — on the dashcam this is a correctness knob,
    # not a performance one. Only force a fourcc when one is configured.
    if FOURCC and FOURCC.lower() not in {"auto", "none"}:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*FOURCC[:4]))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, REQ_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, REQ_H)
    _apply_v4l2_controls()
    # Warm-up: the first frames arrive before auto-exposure and auto-WB converge
    # and read as black or wildly mis-exposed. Discarding them is why /photo.jpg
    # does not serve a black frame from a perfectly good camera.
    for _ in range(max(0, WARMUP_FRAMES)):
        cap.read()
    return cap


def _grabber_loop() -> None:
    global _latest, _grabber_opened_at, _total_grabs, _total_failures
    global _acquire_stall_since
    cap: Optional[cv2.VideoCapture] = None
    consecutive_failures = 0
    sequence = 0

    while not _stop.is_set():
        if cap is None:
            present = _device_present()
            cap = _open()
            if cap is None:
                if present:
                    # Present but unacquirable — this process is the problem.
                    if _acquire_stall_since is None:
                        _acquire_stall_since = time.monotonic()
                    stalled = time.monotonic() - _acquire_stall_since
                    log.warning(
                        "grabber: %s IS present at %s but could not be opened "
                        "(stalled %ds) — retrying. This is NOT an absent camera.",
                        CAMERA_ID, DEVICE_PATH, int(stalled),
                    )
                    if stalled >= ACQUIRE_STALL_S:
                        # os._exit, NOT sys.exit: sys.exit unwinds only this
                        # thread and would leave uvicorn serving a camera-less
                        # 503 forever, which is the silent failure this avoids.
                        log.error(
                            "grabber: stalled %ds on a present camera — exiting "
                            "so systemd restarts us.", int(stalled),
                        )
                        os._exit(1)
                else:
                    # Absent is hardware. Do not arm the stall timer; restarting
                    # against an unplugged camera is noise, not a fix.
                    _acquire_stall_since = None
                    log.warning(
                        "grabber: %s is ABSENT — %s does not resolve. Serving 503. "
                        "This needs hands on the hardware, not a restart.",
                        CAMERA_ID, DEVICE_PATH,
                    )
                _stop.wait(3.0)
                continue
            _grabber_opened_at = time.monotonic()
            _acquire_stall_since = None
            consecutive_failures = 0
            log.info(
                "grabber: opened %s at %s (%dx%d requested)",
                CAMERA_ID, DEVICE_PATH, REQ_W, REQ_H,
            )

        ok, image = cap.read()
        if not ok or image is None:
            consecutive_failures += 1
            _total_failures += 1
            log.warning("grabber: read failed (consec=%d)", consecutive_failures)
            if consecutive_failures >= 5:
                log.warning("grabber: 5 consecutive read failures — reopening")
                cap.release()
                cap = None
                _grabber_opened_at = None
                _stop.wait(3.0)
            else:
                _stop.wait(1.0)
            continue

        consecutive_failures = 0
        _total_grabs += 1
        sequence += 1
        h, w = image.shape[:2]
        with _latest_lock:
            _latest = Frame(image, w, h, sequence, time.monotonic())
        _stop.wait(GRAB_INTERVAL_S)

    if cap is not None:
        cap.release()
    log.info("grabber: stopped (grabs=%d failures=%d)", _total_grabs, _total_failures)


# --- HTTP --------------------------------------------------------------------

app = FastAPI(title=f"farmcam:{CAMERA_ID}")


@app.on_event("startup")
def _startup() -> None:
    global _grabber_thread
    if not DEVICE_PATH:
        log.error("FARMCAM_DEVICE_PATH is required and is not set — refusing to start.")
        os._exit(2)
    if "/by-id/" not in DEVICE_PATH:
        log.warning(
            "FARMCAM_DEVICE_PATH=%r is not a /dev/v4l/by-id/ path. That identifies "
            "a camera BY POSITION, which this service exists to stop doing. Use the "
            "by-id symlink (it carries the USB serial).",
            DEVICE_PATH,
        )
    log.info(
        "farmcam ready: id=%s device=%s port=%d %dx%d interval=%.2fs jpeg_q=%d "
        "processing=NONE",
        CAMERA_ID, DEVICE_PATH, PORT, REQ_W, REQ_H, GRAB_INTERVAL_S, JPEG_QUALITY,
    )
    _grabber_thread = threading.Thread(target=_grabber_loop, name="grabber", daemon=True)
    _grabber_thread.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    _stop.set()


@app.get("/health")
def health():
    with _latest_lock:
        latest = _latest
    grabber_alive = _grabber_thread is not None and _grabber_thread.is_alive()
    stalled = (
        round(time.monotonic() - _acquire_stall_since, 1)
        if _acquire_stall_since is not None else 0.0
    )
    base = {
        "camera_id": CAMERA_ID,
        "device_path": DEVICE_PATH,
        "device_present": _device_present(),
        "resolved_device_name": os.path.basename(DEVICE_PATH) if DEVICE_PATH else None,
        "grabber_alive": grabber_alive,
        "camera_open": _grabber_opened_at is not None,
        "acquire_stalled_s": stalled,
        "total_grabs": _total_grabs,
        "total_failures": _total_failures,
        "processing": "none",
    }
    if latest is None:
        return JSONResponse(status_code=503, content={
            **base, "ok": False, "error": "no frame grabbed yet",
        })
    age = time.monotonic() - latest.monotonic_ts
    stale = age > MAX_FRAME_AGE_S
    return JSONResponse(status_code=503 if stale else 200, content={
        **base,
        "ok": not stale,
        "resolution": [latest.width, latest.height],
        "requested_resolution": [REQ_W, REQ_H],
        "latest_frame_age_ms": int(age * 1000),
        "latest_frame_sequence": latest.sequence,
        "grab_interval_s": GRAB_INTERVAL_S,
        "max_frame_age_s": MAX_FRAME_AGE_S,
        "jpeg_quality": JPEG_QUALITY,
        "v4l2_ctrls": V4L2_CTRLS or None,
        "fourcc": FOURCC or "auto",
    })


@app.get("/photo.jpg")
def photo():
    """Encode and serve the newest frame exactly as the sensor delivered it."""
    t0 = time.monotonic()
    with _latest_lock:
        latest = _latest
    if latest is None:
        raise HTTPException(
            status_code=503,
            detail=(
                f"{CAMERA_ID}: no frame available. device_present="
                f"{_device_present()} path={DEVICE_PATH}"
            ),
        )
    age = time.monotonic() - latest.monotonic_ts
    if age > MAX_FRAME_AGE_S:
        raise HTTPException(
            status_code=503,
            detail=f"{CAMERA_ID}: latest frame is {age:.1f}s old (max {MAX_FRAME_AGE_S}s)",
        )

    ok, buf = cv2.imencode(".jpg", latest.image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise HTTPException(status_code=500, detail="cv2.imencode JPEG failed")
    jpeg_bytes = bytes(buf)

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if latest.sequence % 20 == 0:
        log.info(
            "/photo.jpg served: seq=%d %dx%d %d bytes age=%dms elapsed=%dms",
            latest.sequence, latest.width, latest.height,
            len(jpeg_bytes), int(age * 1000), elapsed_ms,
        )
    return Response(
        content=jpeg_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Capture-Ms": str(elapsed_ms),
            "X-Capture-Resolution": f"{latest.width}x{latest.height}",
            "X-Frame-Age-Ms": str(int(age * 1000)),
            "X-Frame-Sequence": str(latest.sequence),
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
