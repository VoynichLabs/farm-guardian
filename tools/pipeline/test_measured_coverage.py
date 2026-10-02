# Author: Claude Opus 5.5
# Date: 02-October-2026
# PURPOSE: Tests for the measured subject-fill path (v2.75.1):
#          PresenceResult.confident_coverage_pct (union of confident detector
#          boxes) and _compute_overall_score preferring that measurement over
#          the VLM's subject_coverage_pct. The regression case is Boss's
#          02-Oct-2026 frame: one rooster at a tenth of the frame that the VLM
#          reported as coverage 60 and that scored 83.
# SRP/DRY check: Pass — validates the two changed functions only; no capture,
#                detector model, VLM or DB.

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.pipeline.presence import Box, PresenceResult  # noqa: E402
from tools.pipeline.orchestrator import _compute_overall_score  # noqa: E402


def _box(confidence: float, cx: float, cy: float, width: float, height: float) -> Box:
    return Box(class_name="bird", confidence=confidence,
               area_pct=100.0 * width * height, cx=cx, cy=cy, width=width, height=height)


def test_union_does_not_double_count_overlap():
    same_bird_twice = PresenceResult(present=True, boxes=[
        _box(0.9, 0.5, 0.5, 0.5, 0.4), _box(0.6, 0.5, 0.5, 0.5, 0.4)])
    assert abs(same_bird_twice.confident_coverage_pct() - 20.0) < 1.0


def test_low_confidence_boxes_are_ignored():
    # The feed bag: a "bird" at conf 0.09 covering a tenth of the frame.
    result = PresenceResult(present=True, boxes=[
        _box(0.65, 0.86, 0.5, 0.27, 0.38), _box(0.09, 0.17, 0.29, 0.33, 0.29)])
    assert abs(result.confident_coverage_pct() - 10.3) < 1.0


def test_nothing_confident_or_abstained_is_unknown_not_zero():
    assert PresenceResult(present=True, boxes=[_box(0.1, 0.5, 0.5, 0.9, 0.9)]
                          ).confident_coverage_pct() is None
    assert PresenceResult(present=True, abstained=True).confident_coverage_pct() is None
    assert PresenceResult(present=False).confident_coverage_pct() is None


def _vlm_answer(**overrides) -> dict:
    # What the VLM said about Boss's frame (row 3024753).
    metadata = {"bird_count": 3, "lighting": "natural-good", "image_quality": "sharp",
                "subject_coverage_pct": 60, "largest_subject_pct": 45,
                "expression_score": 15, "detail_score": 20}
    metadata.update(overrides)
    return metadata


def test_distant_bird_no_longer_scores_like_a_frame_filler():
    claimed = _vlm_answer()
    _compute_overall_score(claimed)
    assert claimed["overall_score"] == 83

    measured = _vlm_answer(measured_coverage_pct=11)
    _compute_overall_score(measured)
    assert measured["overall_score"] < 60


def test_measured_full_marks_at_forty_percent():
    at_knee = _vlm_answer(measured_coverage_pct=40)
    filler = _vlm_answer(measured_coverage_pct=80)
    _compute_overall_score(at_knee)
    _compute_overall_score(filler)
    assert at_knee["overall_score"] == filler["overall_score"] == 83


def test_without_a_measurement_the_vlm_figure_is_used_unchanged():
    no_measurement = _vlm_answer()
    explicit_none = _vlm_answer(measured_coverage_pct=None)
    _compute_overall_score(no_measurement)
    _compute_overall_score(explicit_none)
    assert no_measurement["overall_score"] == explicit_none["overall_score"] == 83


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")
