# Author: Claude Opus 4.7 (edits 21-April-2026 — bake EXIF Orientation into ip_webcam returns so rotated S7 frames aren't sideways downstream); Claude Sonnet 4.6 (edits 27-April-2026 — allow_stale threaded through Guardian API capture, v2.37.13); Claude Opus 4.8 (edits 05-July-2026 — mirror live guardian capture.py: add force_portrait dimension fallback to _apply_exif_rotation and thread it through the ip_webcam path so S7 landscape/Orientation=1 frames are rotated to portrait, fixing sideways reel/IG images)
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
#            - ip_webcam: HTTP /photo.jpg with optional AF trigger; bakes
#              any EXIF Orientation tag into the pixels before returning,
#              since downstream consumers decode via cv2 (EXIF-unaware).
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

def capture_via_guardian_api(
    camera_name: str,
    guardian_base: str = "http://localhost:6530",
    timeout: int = 15,
    allow_stale: bool = True,
) -> bytes:
    # /api/cameras/<name>/frame returns the latest good frame from Guardian's
    # per-camera ring buffer — works for every camera type (RTSP, HTTP-snapshot,
    # IP Webcam, etc). The v1 /cameras/<name>/snapshot endpoint triggers an
    # active snapshot and 500s on cameras that don't have a Reolink-style
    # snapshot capability (confirmed 500 on gwtc 2026-04-16).
    #
    # `allow_stale=True` keeps the current pipeline architecture working for
    # RTSP-backed cameras like GWTC: during a brief Guardian reconnect window
    # the live buffer may be empty, but the last good frame is still good
    # enough for an occasional still-image pipeline run.
    url = f"{guardian_base}/api/cameras/{camera_name}/frame"
    params = {"allow_stale": 1} if allow_stale else None
    r = requests.get(url, params=params, timeout=timeout)
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
    allow_stale: bool = True,
) -> bytes:
    """Pulls N frames from Guardian's snapshot API with short spacing,
    returns the 'representative' frame (min mean-abs-diff to peers).
    Purpose: dodge transient H.264 decode artifacts that Guardian's
    ring-buffer serves without complaint — when the encoder recovers
    the next frame looks normal, and the corrupted one is the outlier
    in the burst. A single pull might catch the bad frame; a 3-burst
    almost always has a clean majority."""
    if burst_size <= 1:
        return capture_via_guardian_api(
            camera_name,
            guardian_base=guardian_base,
            timeout=timeout,
            allow_stale=allow_stale,
        )
    jpegs: list[bytes] = []
    last_err: Exception | None = None
    for i in range(burst_size):
        try:
            jpegs.append(
                capture_via_guardian_api(
                    camera_name,
                    guardian_base=guardian_base,
                    timeout=timeout,
                    allow_stale=allow_stale,
                )
            )
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
                      timeout: int = 15, force_portrait: bool = False) -> bytes:
    # If trigger_focus is opted in, fire /focus before the fetch. Originally
    # this was gated to /photo.jpg only on the theory that /photoaf.jpg's
    # server-side AF was sufficient; in practice (S7 brooder, 26-Apr-2026)
    # /photoaf.jpg's AF drifts in continuous-picture mode and frames go soft.
    # An explicit pre-fetch /focus + 1.5s wait reliably re-locks. Double-AF
    # when path is /photoaf.jpg is harmless — the second AF no-ops if locked.
    if trigger_focus:
        try:
            requests.get(f"{base_url}/focus", timeout=5)
            time.sleep(focus_wait)
        except Exception as e:
            log.warning("AF trigger failed, continuing: %s", e)
    r = requests.get(f"{base_url}{photo_path}", timeout=timeout)
    r.raise_for_status()
    if not r.content or r.content[:2] != b"\xff\xd8":
        raise CaptureError(f"IP Webcam returned non-JPEG: {base_url}{photo_path}")
    return _apply_exif_rotation(r.content, force_portrait=force_portrait)


def _apply_exif_rotation(jpeg_bytes: bytes, force_portrait: bool = False) -> bytes:
    """Physically bake orientation into the JPEG pixels so every downstream
    consumer (cv2-decoded numpy array AND the preserved JPEG bytes) gets
    upright pixels. This is a faithful mirror of the live Guardian
    `capture.py::_apply_exif_rotation` — the two copies must behave
    identically. (Mirror rather than import: the root module is a top-level
    `capture` and this package already owns a `capture` module, so importing
    across that boundary means sys.path games and a name collision.)

    Two correction paths:

    1. EXIF Orientation. IP Webcam on the S7 emits sensor-native 1920×1080
       pixels and encodes portrait via an EXIF Orientation tag (6 = rotate
       90° CW). cv2.imdecode discards EXIF, so without baking it in the whole
       pipeline sees a sideways frame.

    2. `force_portrait` dimension fallback. The S7 reverts its
       `photo_rotation=90` setting whenever the phone reboots/reconnects, and
       then `/photo.jpg` starts returning sensor-native LANDSCAPE tagged
       Orientation=1 — which path (1) correctly leaves alone. For a
       portrait-mounted camera a landscape frame is always wrong: rotate it
       90° CW (matching the Orientation=6 case) regardless of EXIF.

    No-op when there's nothing to do — avoids a needless re-encode. Catches all
    exceptions and returns the original bytes on failure, since an orientation
    bug must never kill the capture path.
    """
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(jpeg_bytes))
        orient = im.getexif().get(274, 1)  # 274 is the EXIF Orientation tag
        needs_exif_rot = orient != 1
        if needs_exif_rot:
            im = ImageOps.exif_transpose(im)
        needs_force_rot = force_portrait and im.width > im.height
        if needs_force_rot:
            im = im.rotate(-90, expand=True)  # -90 in PIL == 90° CW
        if not needs_exif_rot and not needs_force_rot:
            return jpeg_bytes  # nothing to correct — avoid a needless re-encode
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=95)
        return out.getvalue()
    except Exception:
        return jpeg_bytes


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
            focus_wait=camera_cfg.get("focus_wait", 1.5),
            force_portrait=camera_cfg.get("force_portrait", False),
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
