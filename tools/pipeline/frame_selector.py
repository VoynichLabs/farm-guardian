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
#          PHOTOBOMBERS — the subtle bit, and the reason this is not a bird
#          count cap. gem_poster.should_post's docstring records Boss's
#          explicit instruction that a bird posing close to the lens with the
#          rest of the flock behind it is a FAVOURITE framing, and that capping
#          bird_count would kill it. The archive agrees that crowds are
#          generally worse (strong-frame yield falls 4-5% at 1-5 birds to 0.5%
#          at 10) — but count alone cannot tell those two cases apart.
#
#          So the `focus` component uses largest_area / total_animal_area
#          instead. One bird at the lens with six specks behind it scores near
#          1.0 and is rewarded; eight evenly-scattered mid-distance birds score
#          near 0.125 and are penalised. Same bird count, opposite verdicts,
#          which is exactly the distinction a count cap cannot express.
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

WEIGHT_SHARPNESS = 0.45
WEIGHT_DOMINANCE = 0.30
WEIGHT_FOCUS = 0.15
WEIGHT_CENTRING = 0.10

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


@dataclass
class Selection:
    index: int
    candidate: Candidate
    score: float
    breakdown: dict
    considered: int


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


def _focus_ratio(presence: PresenceResult) -> float:
    """largest animal area / total animal area — see PHOTOBOMBERS in the header.
    No animals (or an abstaining gate) yields a neutral 0.5 so this component
    can neither reward nor punish a frame it knows nothing about."""
    if not presence.boxes:
        return 0.5
    total = sum(b.area_pct for b in presence.boxes)
    if total <= 0:
        return 0.5
    return presence.largest_area_pct / total


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

    sharp = _normalise([c.laplacian_var for c in candidates],
                       min_rel_spread=MIN_SHARPNESS_REL_SPREAD)
    dominance = [
        min(1.0, c.presence.largest_area_pct / DOMINANCE_FULL_MARKS_PCT)
        for c in candidates
    ]
    focus = [_focus_ratio(c.presence) for c in candidates]
    centring = [_centring(c.presence) for c in candidates]

    scores = [
        WEIGHT_SHARPNESS * sharp[i]
        + WEIGHT_DOMINANCE * dominance[i]
        + WEIGHT_FOCUS * focus[i]
        + WEIGHT_CENTRING * centring[i]
        for i in range(len(candidates))
    ]
    best = max(range(len(candidates)), key=lambda i: scores[i])

    breakdown = {
        "sharpness": round(sharp[best], 3),
        "dominance": round(dominance[best], 3),
        "focus": round(focus[best], 3),
        "centring": round(centring[best], 3),
        "laplacian_var": round(candidates[best].laplacian_var, 1),
        "largest_area_pct": round(candidates[best].presence.largest_area_pct, 1),
        "animal_count": candidates[best].presence.count,
        "all_scores": [round(s, 3) for s in scores],
        "all_laplacian": [round(c.laplacian_var, 1) for c in candidates],
    }
    log.debug("burst select: idx=%d/%d score=%.3f %s",
              best, len(candidates), scores[best], breakdown)
    return Selection(
        index=best, candidate=candidates[best], score=scores[best],
        breakdown=breakdown, considered=len(candidates),
    )
