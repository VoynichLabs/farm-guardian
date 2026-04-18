# Author: Claude Opus 4.7 (1M context)
# Date: 17-April-2026
# PURPOSE: Pre-VLM frame filtering for the multi-cam image pipeline. Three
#          gates, cheap and composable:
#            1. Trivial gate  — rejects all-black/all-white/lens-cap frames
#                               (pixel std_dev floor, existing behaviour).
#            2. Exposure gate — rejects near-black, blown-out, and low-
#                               contrast frames that the VLM would just
#                               label "skip" anyway. Saves ~43 s of VLM
#                               inference per rejection.
#            3. Motion gate   — per-camera opt-in. Holds a 64x64 grayscale
#                               thumbnail of the last captured frame; a new
#                               frame is accepted only if the mean absolute
#                               pixel delta exceeds motion_delta_threshold.
#                               Used on house-yard + gwtc where 90%+ of
#                               frames show no change and return share=skip.
#          Also exposes the Laplacian variance helper used by capture.py to
#          pick the sharpest frame within a burst (internal ranking, not a
#          threshold).
# SRP/DRY check: Pass — single responsibility is pixel-level frame sanity
#                and cheap pre-VLM filtering. No VLM calls here.

from __future__ import annotations

import threading

import cv2
import numpy as np


def pixel_std_dev(image_bgr: np.ndarray) -> float:
    """Standard deviation of luminance. Below ~5 on a 0-255 scale means the
    frame is effectively uniform — lens cap, all-black night, solid white,
    a codec-dropped frame. Above ~20 means real content."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    return float(gray.std())


def laplacian_variance(image_bgr: np.ndarray) -> float:
    """Used to pick the sharpest frame within a burst. Higher = sharper.
    This is a ranking signal only — no absolute threshold."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def exposure_p50(image_bgr: np.ndarray) -> float:
    """Median luminance; logged for later analysis but not gated on."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    return float(np.median(gray))


def passes_trivial_gate(image_bgr: np.ndarray, std_dev_floor: float = 5.0) -> tuple[bool, dict]:
    """Returns (ok, metrics). `ok=True` means the frame has real content and is
    worth sending to the VLM. `metrics` dict carries sharpness / exposure / std
    for logging regardless of pass/fail."""
    std = pixel_std_dev(image_bgr)
    lap = laplacian_variance(image_bgr)
    p50 = exposure_p50(image_bgr)
    ok = std >= std_dev_floor
    return ok, {"std_dev": std, "laplacian_var": lap, "exposure_p50": p50}


def passes_exposure_gate(
    metrics: dict,
    p50_floor: float = 25.0,
    p50_ceiling: float = 230.0,
    std_floor: float = 15.0,
) -> tuple[bool, str | None]:
    """Reject near-black, blown-out, and washed-out frames before the VLM
    sees them. Reuses the metrics already computed by passes_trivial_gate
    so this is effectively free.

    Returns (ok, reason). reason is None on pass, or a short tag
    ("too_dark" / "too_bright" / "too_flat") on fail — logged by the
    orchestrator so we can tune thresholds from real data."""
    p50 = metrics.get("exposure_p50", 128.0)
    std = metrics.get("std_dev", 0.0)
    if p50 < p50_floor:
        return False, f"too_dark (p50={p50:.1f} < {p50_floor})"
    if p50 > p50_ceiling:
        return False, f"too_bright (p50={p50:.1f} > {p50_ceiling})"
    if std < std_floor:
        return False, f"too_flat (std={std:.1f} < {std_floor})"
    return True, None


class MotionGate:
    """Per-camera frame-to-frame delta gate. Holds a 64x64 grayscale
    thumbnail of the last *accepted* frame per camera. A new frame is
    accepted iff the mean absolute pixel delta against that thumbnail
    exceeds `threshold` on the 0-255 scale.

    First frame per camera always accepts (no baseline to compare to).

    Opt-in per camera: the orchestrator only calls this for cameras whose
    config block has `motion_gate: true`. Brooder cameras leave it off
    because chicks move continuously and we want the VLM on every frame
    regardless.

    Thread-safe around the baseline dict — the pipeline is single-
    threaded today, but the lock is cheap and future-proofs against a
    later async rewrite."""

    _THUMB_SIZE = (64, 64)

    def __init__(self, threshold: float = 3.0):
        self._threshold = float(threshold)
        self._last: dict[str, np.ndarray] = {}
        self._lock = threading.Lock()

    def accept(self, camera_id: str, image_bgr: np.ndarray) -> tuple[bool, dict]:
        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        thumb = cv2.resize(gray, self._THUMB_SIZE, interpolation=cv2.INTER_AREA)
        with self._lock:
            prev = self._last.get(camera_id)
            if prev is None:
                # First frame — no baseline, accept and store.
                self._last[camera_id] = thumb
                return True, {"motion_delta": None, "baseline": "first_frame"}
            delta = float(np.mean(cv2.absdiff(thumb, prev)))
            ok = delta >= self._threshold
            if ok:
                # Only refresh the baseline when we accept — otherwise a
                # slow drift (gradual lighting change) would never trip
                # the threshold because each individual tick stays under.
                self._last[camera_id] = thumb
            return ok, {"motion_delta": round(delta, 2), "threshold": self._threshold}


if __name__ == "__main__":
    import sys
    for path in sys.argv[1:]:
        img = cv2.imread(path)
        if img is None:
            print(f"{path}: UNREADABLE")
            continue
        ok, m = passes_trivial_gate(img)
        verdict = "pass" if ok else "REJECT"
        exp_ok, exp_reason = passes_exposure_gate(m)
        exp_verdict = "pass" if exp_ok else f"REJECT ({exp_reason})"
        print(f"{path}: trivial={verdict}  exposure={exp_verdict}  "
              f"std={m['std_dev']:.1f}  lap={m['laplacian_var']:.1f}  "
              f"p50={m['exposure_p50']:.1f}")
