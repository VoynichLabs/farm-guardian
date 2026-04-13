# Author: Claude Opus 4.6 (1M context)
# Date: 13-April-2026
# PURPOSE: Trivial garbage filter for the multi-cam image pipeline. Rejects
#          only obviously broken frames (all-black, all-white, lens-cap,
#          dropped-frame artifacts) via a pixel std-dev floor. Deliberately
#          does NOT gate on sharpness — GLM 4.6v returns image_quality in the
#          structured output, and a soft frame still has archive-worthy
#          metadata (activity, lighting, counts). Also exposes the Laplacian
#          variance helper used by capture.py to pick the sharpest frame
#          within a burst (internal ranking, not a threshold).
# SRP/DRY check: Pass — single responsibility is pixel-level frame sanity.

from __future__ import annotations

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


if __name__ == "__main__":
    import sys
    for path in sys.argv[1:]:
        img = cv2.imread(path)
        if img is None:
            print(f"{path}: UNREADABLE")
            continue
        ok, m = passes_trivial_gate(img)
        verdict = "pass" if ok else "REJECT"
        print(f"{path}: {verdict}  std={m['std_dev']:.1f}  lap={m['laplacian_var']:.1f}  p50={m['exposure_p50']:.1f}")
