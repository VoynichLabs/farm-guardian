# Author: Claude Opus 5
# Date: 07-August-2026
# PURPOSE: Pick the single best frame out of a short burst, so the one 5.2 s
#          Qwen3-VL-4B call the pipeline can afford is spent on the best frame
#          available rather than on whichever frame happened to land on the
#          tick. This is the "clear shot, no photobombers" half of Boss's ask.
#
#          WHY A BURST AT ALL — measured 07-Aug-2026, five consecutive
#          /shot.jpg pulls off the live s7-cam with no AF trigger between them:
#
#              lap=159   lap=264   lap=732   lap=198   lap=876
#
#          A 5.5x sharpness spread across frames seconds apart on a static
#          scene. Every one of those clears the camera's laplacian_floor of 60,
#          so today the pipeline is happily feeding the VLM a lap=159 frame
#          roughly as often as a lap=876 one. Taking N and keeping the best is
#          therefore a large, essentially free quality win — the burst costs
#          network round-trips, not VLM time.
#
#          SCORING. All components are ranked WITHIN the burst (relative, not
#          absolute) because every frame in a burst shows the same scene
#          seconds apart — the question is only "which of these", never "is
#          this good enough". That is still the VLM's job, and then Boss's.
#
#            sharpness   0.45  — laplacian variance, the measured-strongest and
#                                most reliable signal (see above)
#            dominance   0.30  — largest animal box as % of frame; a bird that
#                                fills the frame is the shot Boss wants
#            focus       0.15  — how much of the total animal area belongs to
#                                the LARGEST animal (see PHOTOBOMBERS below)
#            centring    0.10  — subject near the frame centre, mild tiebreak
#
#          PHOTOBOMBERS — the idea that didn't survive contact with data.
#          v1 of this module scored a `focus` component (largest_area /
#          total_animal_area) on the theory that one bird at the lens beats a
#          scattered flock, and that this was the right way to express Boss's
#          "no photobombers" without a bird-count cap. It reasoned well and
#          measured badly: against Boss's own Discord reactions (70 reacted vs
#          67 strong-but-unreacted archived frames, YOLO re-run over each), the
#          reacted set had focus median 0.484 and the unreacted 0.501. No
#          separation, slightly backwards. Removed in v2.68.0.
#
#          What DID separate was plain dominance — reacted median 31.4% of
#          frame against 19.5%. So crowding per se was never the problem; a
#          small distant bird was. There is still no bird-count cap, and for
#          the original reason: should_post records Boss's instruction that a
#          bird at the lens with the flock behind it is a favourite framing.
#          Dominance rewards exactly that shot and needs no help from a
#          count rule.
#
# SRP/DRY check: Pass — single responsibility is ranking N already-captured
#                frames and returning the winner's index. Captures nothing,
#                calls no VLM, writes no DB. Sharpness comes from
#                quality_gate.laplacian_variance and box geometry from
#                presence.PresenceResult; neither is recomputed here. Note
#                capture.py already has two burst-pick helpers, and neither
#                fits: capture_rtsp_burst picks purely on Laplacian (blind to
#                subject), and _pick_representative_jpeg picks the most
#                CENTRAL frame to dodge H.264 artifacts — the opposite of what
#                we want, since the sharpest frame of a burst is by definition
#                an outlier. Hence a third, subject-aware ranker rather than a
#                forced reuse of either.

from __future__ import annotations

import logging
from dataclasses import dataclass

from .presence import PresenceResult

log = logging.getLogger("pipeline.frame_selector")

# v2.68.0 (08-Aug-2026). Two changes, both measured rather than reasoned:
#
#   focus DROPPED. It was largest_area/total_animal_area, added 07-Aug as a
#   photobomber discriminator on the theory that one bird up close beats a
#   scattered flock. Tested against the only human signal available — Boss's
#   Discord reactions — over 70 reacted vs 67 strong-but-unreacted archived
#   frames: reacted median 0.484, unreacted 0.501. No separation, and if
#   anything backwards. It was reasoning, not evidence, and it is gone.
#
#   dominance KEPT and RAISED, even though the same commit removes it from the
#   gem score. Not a contradiction — see _compute_overall_score. The same test
#   showed dominance is Boss's single best-measured preference (reacted median
#   31.4% of frame vs 19.5%). What was wrong was using it to decide IF a frame
#   posts; using it to decide WHICH of three already-captured frames to send
#   costs no volume whatsoever and steers straight at his taste.
WEIGHT_SHARPNESS = 0.50
WEIGHT_DOMINANCE = 0.35
WEIGHT_CENTRING = 0.15

# A bird filling this share of the frame is treated as maximal dominance.
# Mirrors orchestrator._compute_overall_score, which gives full marks at ~50%
# coverage because s7-cam is a wide-angle phone lens and a bird posing right at
# it still only fills 30-40% of the frame.
DOMINANCE_FULL_MARKS_PCT = 50.0

# Laplacian variance is noisy frame-to-frame. Unless the burst's spread exceeds
# this fraction of its maximum, treat every frame as equally sharp and let the
# subject-geometry components decide. See _normalise.
MIN_SHARPNESS_REL_SPREAD = 0.15


@dataclass
class Candidate:
    """One frame in a burst, already captured and already measured.

    `image_bgr` carries the decoded frame so the winner doesn't have to be
    decoded a second time downstream — at ~1.8 MB a frame that is not free.
    """
    jpeg_bytes: bytes
    laplacian_var: float
    presence: PresenceResult
    gate_metrics: dict
    image_bgr: object = None
    # Laplacian variance measured INSIDE the largest animal box. None when
    # there's no box to crop. See subject_laplacian() and the header.
    subject_laplacian: float | None = None


@dataclass
class Selection:
    index: int
    candidate: Candidate
    score: float
    breakdown: dict
    considered: int


def subject_laplacian(image_bgr, presence: PresenceResult) -> float | None:
    """Laplacian variance measured inside the largest animal box, or None when
    there is no usable box.

    WHY THIS EXISTS (measured 08-Aug-2026, after Boss: "some of them are a
    little blurry"). Whole-frame Laplacian variance is the wrong instrument on
    this camera. Checked against the VLM's own image_quality labels over 3 days
    of s7 archive:

        whole-frame lap:  sharp median 1328   soft/blurred median 1391  (0.95x)
        BIRD-BOX lap:     sharp median  855   soft/blurred median  696  (1.23x)

    Whole-frame is worse than useless — it ranks soft frames *higher*, because
    grass, foliage and sensor noise generate far more high-frequency energy
    than a smooth-feathered bird does. That is also why raising
    `laplacian_floor` would backfire: at a floor of 400 it would drop 12.8% of
    sharp frames while still admitting 91.9% of the soft ones. So the floor was
    deliberately left alone and the RANKING moved to the subject instead.

    Neither metric is a strong discriminator against those labels (best
    separation 0.143 vs 0.127), and the labels come from a 4b model that is
    demonstrably stereotyped, so this is a directional improvement rather than
    a solved problem. It is measuring the right pixels, which the whole-frame
    number never was.

    Returns None on any failure so the caller falls back to whole-frame.
    """
    if image_bgr is None or not presence.boxes:
        return None
    try:
        import cv2  # local import: keeps this module importable without cv2

        height, width = image_bgr.shape[:2]
        biggest = max(presence.boxes, key=lambda b: b.area_pct)
        # Box centre/area are frame-relative; recover pixel bounds from them.
        box_area_px = (biggest.area_pct / 100.0) * height * width
        if box_area_px <= 0:
            return None
        # Assume roughly square-ish crop around the centre — we only need a
        # representative patch of the subject, not its exact silhouette.
        half = max(8, int((box_area_px ** 0.5) / 2))
        cx, cy = int(biggest.cx * width), int(biggest.cy * height)
        x1, x2 = max(0, cx - half), min(width, cx + half)
        y1, y2 = max(0, cy - half), min(height, cy + half)
        crop = image_bgr[y1:y2, x1:x2]
        if crop.size < 400:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())
    except Exception:
        return None


def _normalise(values: list[float], min_rel_spread: float = 0.0) -> list[float]:
    """Scale to 0..1 across the burst. An all-equal burst maps to all-1.0 so a
    flat component simply stops contributing rather than injecting noise.

    `min_rel_spread` guards against min-max normalisation amplifying noise into
    a decisive signal. Observed on the first live run: a burst came back with
    laplacian 1518.3 / 1517.9 / 1505.8 — a 0.8% spread, i.e. three equally
    sharp frames — and plain min-max mapped that to 1.0 / 0.97 / 0.0, handing
    the last frame a 0.45 penalty for nothing. When the spread across the burst
    is below this fraction of the maximum, the component is treated as flat and
    the decision falls through to the other components.

    The real signal this needs to preserve is large: the measured burst spread
    that motivated best-of-N in the first place was laplacian 159 to 876, a
    relative spread of 0.82 — far above the 0.15 default used for sharpness.
    """
    if not values:
        return []
    low, high = min(values), max(values)
    if high - low < 1e-9:
        return [1.0] * len(values)
    if min_rel_spread > 0.0 and high > 0.0 and (high - low) / abs(high) < min_rel_spread:
        return [1.0] * len(values)
    return [(v - low) / (high - low) for v in values]


def _centring(presence: PresenceResult) -> float:
    """1.0 when the largest subject sits dead centre, falling off toward the
    corners. Neutral 0.5 when there's nothing to measure."""
    if not presence.boxes:
        return 0.5
    biggest = max(presence.boxes, key=lambda b: b.area_pct)
    # Max distance from centre in normalised coords is ~0.707 (a corner).
    distance = ((biggest.cx - 0.5) ** 2 + (biggest.cy - 0.5) ** 2) ** 0.5
    return max(0.0, 1.0 - distance / 0.707)


def select_best(candidates: list[Candidate]) -> Selection | None:
    """Rank a burst and return the winner. None if the burst is empty.

    A single-candidate burst short-circuits — with nothing to compare against,
    ranking is meaningless and we just return it.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return Selection(
            index=0, candidate=candidates[0], score=1.0,
            breakdown={"reason": "single_candidate"}, considered=1,
        )

    # Rank on SUBJECT sharpness where we have it (see subject_laplacian in the
    # header), falling back to the whole-frame number when there's no box.
    sharp_values = [
        c.subject_laplacian if c.subject_laplacian is not None else c.laplacian_var
        for c in candidates
    ]
    sharp = _normalise(sharp_values, min_rel_spread=MIN_SHARPNESS_REL_SPREAD)
    dominance = [
        min(1.0, c.presence.largest_area_pct / DOMINANCE_FULL_MARKS_PCT)
        for c in candidates
    ]
    centring = [_centring(c.presence) for c in candidates]

    scores = [
        WEIGHT_SHARPNESS * sharp[i]
        + WEIGHT_DOMINANCE * dominance[i]
        + WEIGHT_CENTRING * centring[i]
        for i in range(len(candidates))
    ]
    best = max(range(len(candidates)), key=lambda i: scores[i])

    breakdown = {
        "sharpness": round(sharp[best], 3),
        "dominance": round(dominance[best], 3),
        "centring": round(centring[best], 3),
        "subject_lap": (round(candidates[best].subject_laplacian, 1)
                        if candidates[best].subject_laplacian is not None else None),
        "laplacian_var": round(candidates[best].laplacian_var, 1),
        "largest_area_pct": round(candidates[best].presence.largest_area_pct, 1),
        "animal_count": candidates[best].presence.count,
        "all_scores": [round(s, 3) for s in scores],
        "all_sharpness": [round(v, 1) for v in sharp_values],
    }
    log.debug("burst select: idx=%d/%d score=%.3f %s",
              best, len(candidates), scores[best], breakdown)
    return Selection(
        index=best, candidate=candidates[best], score=scores[best],
        breakdown=breakdown, considered=len(candidates),
    )
