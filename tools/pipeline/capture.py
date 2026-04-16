# Author: Claude Opus 4.6 (1M context)
# Date: 13-April-2026
# PURPOSE: Per-camera high-quality frame capture for the multi-cam image
#          pipeline. Each camera has a different focus/capture reality; this
#          module encodes those differences in one place so the orchestrator
#          stays dumb. Methods supported:
#            - reolink_snapshot: hits Guardian's own /api/v1/cameras/.../snapshot
#              (which internally calls the Reolink HTTP cmd=Snap and returns
#              a sharp 4K JPEG — reuses Guardian's auth, no duplication)
#            - usb_avfoundation: OpenCV VideoCapture with autofocus warmup
#            - rtsp_burst: ffmpeg one-shot burst, Laplacian-ranked winner
#            - ip_webcam: HTTP /photo.jpg with optional AF trigger
# SRP/DRY check: Pass — single responsibility is turning a camera + recipe
#                into sharp JPEG bytes in memory. No archiving, no VLM, no DB.

from __future__ import annotations

import io
import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import requests

from .quality_gate import laplacian_variance

log = logging.getLogger("pipeline.capture")


class CaptureError(Exception):
    pass


# ---------------------------------------------------------------------------
# Method 1: Reolink via Guardian's own snapshot API (reuses auth)
# ---------------------------------------------------------------------------

def capture_via_guardian_api(camera_name: str, guardian_base: str = "http://localhost:6530", timeout: int = 15) -> bytes:
    # /api/cameras/<name>/frame returns the latest good frame from Guardian's
    # per-camera ring buffer — works for every camera type (RTSP, HTTP-snapshot,
    # IP Webcam, etc). The v1 /cameras/<name>/snapshot endpoint triggers an
    # active snapshot and 500s on cameras that don't have a Reolink-style
    # snapshot capability (confirmed 500 on gwtc 2026-04-16).
    url = f"{guardian_base}/api/cameras/{camera_name}/frame"
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    if not r.content or r.content[:2] != b"\xff\xd8":
        raise CaptureError(f"guardian api returned non-JPEG for {camera_name}")
    return r.content


def _pick_representative_jpeg(jpegs: list[bytes]) -> bytes:
    """Pick the frame with smallest mean-absolute-difference to its peers —
    i.e. the most central frame in the burst. Corrupted H.264 frames are
    outliers (their vertical-stripe smear makes them look wildly different
    from the surrounding clean frames), so picking the 'median' frame
    robustly dodges transient decode artifacts without relying on Laplacian
    variance (which the stripes *fool* — they generate fake high-frequency
    edges). Requires N >= 2."""
    if len(jpegs) == 1:
        return jpegs[0]
    grays = []
    for jb in jpegs:
        arr = np.frombuffer(jb, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if img is None:
            grays.append(None)
            continue
        # downscale for fast comparison — identity of the image is
        # preserved at 320x180, artifacts still visible.
        grays.append(cv2.resize(img, (320, 180)).astype(np.int16))
    valid_indices = [i for i, g in enumerate(grays) if g is not None]
    if not valid_indices:
        raise CaptureError("burst: no decodable frames")
    # sum of MAD (mean-abs-diff) to every other valid frame; lower = more central.
    scores = {}
    for i in valid_indices:
        total = 0.0
        for j in valid_indices:
            if i == j:
                continue
            total += float(np.mean(np.abs(grays[i] - grays[j])))
        scores[i] = total
    winner = min(scores, key=scores.get)
    log.debug("burst-api representative pick: idx=%d scores=%s",
              winner, {i: round(s, 1) for i, s in scores.items()})
    return jpegs[winner]


def capture_via_guardian_api_burst(
    camera_name: str,
    burst_size: int = 3,
    burst_interval_seconds: float = 0.4,
    guardian_base: str = "http://localhost:6530",
    timeout: int = 15,
) -> bytes:
    """Pulls N frames from Guardian's snapshot API with short spacing,
    returns the 'representative' frame (min mean-abs-diff to peers).
    Purpose: dodge transient H.264 decode artifacts that Guardian's
    ring-buffer serves without complaint — when the encoder recovers
    the next frame looks normal, and the corrupted one is the outlier
    in the burst. A single pull might catch the bad frame; a 3-burst
    almost always has a clean majority."""
    if burst_size <= 1:
        return capture_via_guardian_api(camera_name, guardian_base, timeout)
    jpegs: list[bytes] = []
    last_err: Exception | None = None
    for i in range(burst_size):
        try:
            jpegs.append(capture_via_guardian_api(camera_name, guardian_base, timeout))
        except Exception as e:
            last_err = e
            log.debug("burst-api %s: frame %d/%d failed: %s",
                      camera_name, i + 1, burst_size, e)
        if i < burst_size - 1:
            time.sleep(burst_interval_seconds)
    if not jpegs:
        raise CaptureError(f"burst-api {camera_name}: zero frames collected ({last_err})")
    return _pick_representative_jpeg(jpegs)


# ---------------------------------------------------------------------------
# Method 2: USB camera via OpenCV / AVFoundation
# ---------------------------------------------------------------------------

def capture_usb(device_index: int = 0, resolution: tuple[int, int] = (1920, 1080),
                warmup_frames: int = 5, jpeg_quality: int = 92) -> bytes:
    # AVFoundation backend on macOS. Autofocus is controlled per-device; many
    # USB webcams expose continuous AF via CAP_PROP_AUTOFOCUS=1. Warmup frames
    # let the autoexposure/whitebalance/AF converge before we grab the keeper.
    cap = cv2.VideoCapture(device_index, cv2.CAP_AVFOUNDATION)
    if not cap.isOpened():
        raise CaptureError(f"USB device {device_index} failed to open")
    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])
        cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
        frame = None
        for _ in range(warmup_frames):
            ok, frame = cap.read()
            if not ok:
                raise CaptureError(f"USB device {device_index} read failed during warmup")
            time.sleep(0.08)
        # One more read after warmup — this is the keeper
        ok, frame = cap.read()
        if not ok or frame is None:
            raise CaptureError(f"USB device {device_index} keeper read failed")
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        if not ok:
            raise CaptureError("JPEG encode failed")
        return bytes(buf)
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# Method 3: RTSP burst via ffmpeg
# ---------------------------------------------------------------------------

def _ffmpeg_single_frame(rtsp_url: str, transport: str, out_path: Path, timeout: int) -> bool:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-rtsp_transport", transport,
        "-i", rtsp_url,
        "-frames:v", "1",
        str(out_path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0
    except subprocess.TimeoutExpired:
        return False


def capture_rtsp_burst(rtsp_url: str, burst_size: int = 5, burst_interval_seconds: float = 0.5,
                       transport: str = "tcp", per_grab_timeout: int = 15) -> bytes:
    """Pulls N frames at spacing, returns the Laplacian-variance-sharpest as
    JPEG bytes. This is the recipe for fixed-focus cameras (gwtc, mba-cam)
    where we can't trigger AF — we rely on motion/light-variation between
    frames to give us at least one sharp sample."""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        candidates: list[tuple[float, bytes]] = []
        for i in range(burst_size):
            out = td_path / f"frame_{i}.jpg"
            if _ffmpeg_single_frame(rtsp_url, transport, out, per_grab_timeout):
                img = cv2.imread(str(out))
                if img is not None:
                    lap = laplacian_variance(img)
                    candidates.append((lap, out.read_bytes()))
            if i < burst_size - 1:
                time.sleep(burst_interval_seconds)
        if not candidates:
            raise CaptureError(f"RTSP burst yielded zero frames: {rtsp_url}")
        candidates.sort(key=lambda t: t[0], reverse=True)
        log.debug("burst sharpness ranking: %s", [f"{c[0]:.1f}" for c in candidates])
        return candidates[0][1]


# ---------------------------------------------------------------------------
# Method 4: IP Webcam (Samsung S7) via HTTP /photo.jpg
# ---------------------------------------------------------------------------

def capture_ip_webcam(base_url: str, photo_path: str = "/photo.jpg",
                      trigger_focus: bool = True, focus_wait: float = 1.5,
                      timeout: int = 15) -> bytes:
    # /photoaf.jpg on IP Webcam fires AF server-side (slower, sharper); in that
    # case skip the separate /focus trigger since the endpoint handles it.
    if trigger_focus and photo_path == "/photo.jpg":
        try:
            requests.get(f"{base_url}/focus", timeout=5)
            time.sleep(focus_wait)
        except Exception as e:
            log.warning("AF trigger failed, continuing: %s", e)
    r = requests.get(f"{base_url}{photo_path}", timeout=timeout)
    r.raise_for_status()
    if not r.content or r.content[:2] != b"\xff\xd8":
        raise CaptureError(f"IP Webcam returned non-JPEG: {base_url}{photo_path}")
    return r.content


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def capture_camera(camera_name: str, camera_cfg: dict, global_cfg: dict) -> bytes:
    method = camera_cfg.get("capture_method")
    if method == "reolink_snapshot":
        burst = int(camera_cfg.get("burst_size", 1))
        if burst > 1:
            return capture_via_guardian_api_burst(
                camera_name,
                burst_size=burst,
                burst_interval_seconds=float(camera_cfg.get("burst_interval_seconds", 0.4)),
                guardian_base="http://localhost:6530",
            )
        return capture_via_guardian_api(camera_name, guardian_base="http://localhost:6530")
    if method == "usb_avfoundation":
        return capture_usb(
            device_index=camera_cfg.get("device_index", 0),
            resolution=tuple(camera_cfg.get("resolution", (1920, 1080))),
            warmup_frames=camera_cfg.get("warmup_frames", 5),
        )
    if method == "rtsp_burst":
        return capture_rtsp_burst(
            rtsp_url=camera_cfg["rtsp_url"],
            burst_size=camera_cfg.get("burst_size", 5),
            burst_interval_seconds=camera_cfg.get("burst_interval_seconds", 0.5),
            transport=camera_cfg.get("rtsp_transport", "tcp"),
        )
    if method == "ip_webcam":
        return capture_ip_webcam(
            base_url=camera_cfg["ip_webcam_base"],
            photo_path=camera_cfg.get("photo_path", "/photo.jpg"),
            trigger_focus=camera_cfg.get("trigger_focus", True),
        )
    raise CaptureError(f"unknown capture_method: {method} for {camera_name}")


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO)
    cfg_path = Path(__file__).parent / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cam = sys.argv[1] if len(sys.argv) > 1 else "usb-cam"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(f"/tmp/{cam}-capture.jpg")
    data = capture_camera(cam, cfg["cameras"][cam], cfg)
    out.write_bytes(data)
    print(f"{cam}: {out} ({len(data)} bytes)")
