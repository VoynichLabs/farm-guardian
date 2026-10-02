# Author: Claude Opus 5.5
# Date: 02-October-2026
# PURPOSE: Tests for the local pixel focus check (focus_check.py) and the two
#          orchestrator helpers that apply its verdict. Synthetic frames only,
#          so the tests do not depend on archive files that retention will
#          eventually delete. Covers: a crisp frame passes; a large defocused
#          patch is caught; a permanently blurry band (the feed bowl) is
#          learned and ignored, and a defocused patch touching it is still
#          caught; a flat in-focus region (white hen) is not mistaken for
#          blur; the judge abstains while warming up; the verdict only ever
#          demotes the VLM's image_quality and caps the score.
# SRP/DRY check: Pass — validates focus_check and its two orchestrator hooks
#                only; no capture, VLM or DB.

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

# Allow running as `python tools/pipeline/test_focus_check.py` or as
# `python -m tools.pipeline.test_focus_check`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.pipeline.focus_check import (  # noqa: E402
    WARMUP_FRAMES, FocusJudge, FocusVerdict,
)
from tools.pipeline.orchestrator import (  # noqa: E402
    _apply_focus_verdict, _cap_score_for_focus, _compute_overall_score,
)

HEIGHT, WIDTH = 1920, 1080
BOWL_TOP = 1560  # the permanently defocused band across the bottom


def _crisp_frame(seed: int) -> np.ndarray:
    """Wood-chip-like texture: hard-edged random blocks, crisp everywhere."""
    rng = np.random.default_rng(seed)
    blocks = rng.integers(40, 215, size=(HEIGHT // 8, WIDTH // 8, 3), dtype=np.uint8)
    return cv2.resize(blocks, (WIDTH, HEIGHT), interpolation=cv2.INTER_NEAREST)


def _defocus(frame: np.ndarray, y1: int, y2: int, x1: int, x2: int) -> None:
    """Paint a defocused object: coarse colour patches (comb, wattle, neck)
    with wide, gradual edges. Blurring the fine texture alone would leave a
    featureless grey slab, which is 'flat', not what a too-close bird looks
    like."""
    rng = np.random.default_rng(y1 * 7919 + x1)
    height, width = y2 - y1, x2 - x1
    patches = rng.integers(30, 225, size=(height // 90 + 1, width // 90 + 1, 3), dtype=np.uint8)
    coarse = cv2.resize(patches, (width, height), interpolation=cv2.INTER_NEAREST)
    frame[y1:y2, x1:x2] = cv2.GaussianBlur(coarse, (0, 0), 12)


def _scene(seed: int, close_bird: bool = False, white_hen: bool = False) -> np.ndarray:
    frame = _crisp_frame(seed)
    _defocus(frame, BOWL_TOP, HEIGHT, 0, WIDTH)          # the bowl, always
    if white_hen:
        frame[500:1100, 300:800] = 235                   # flat, sharp-edged
    if close_bird:
        _defocus(frame, 0, 900, 0, 520)                  # head at the lens
    return frame


def _warmed_judge() -> FocusJudge:
    judge = FocusJudge(state_path=None)
    for seed in range(WARMUP_FRAMES + 5):
        judge.judge(_scene(seed))
    return judge


def test_abstains_while_warming_up():
    judge = FocusJudge(state_path=None)
    assert judge.judge(_scene(0, close_bird=True)).verdict is None


def test_crisp_frame_with_bowl_is_sharp():
    verdict = _warmed_judge().judge(_scene(999))
    assert verdict.verdict == "sharp", verdict


def test_defocused_bird_at_lens_is_caught():
    verdict = _warmed_judge().judge(_scene(999, close_bird=True))
    assert verdict.verdict == "blurred", verdict


def test_defocused_patch_touching_the_bowl_is_caught():
    frame = _scene(999)
    _defocus(frame, 900, BOWL_TOP, 0, 400)               # runs down into the bowl
    verdict = _warmed_judge().judge(frame)
    assert verdict.verdict in {"soft", "blurred"}, verdict


def test_flat_in_focus_bird_is_not_blur():
    verdict = _warmed_judge().judge(_scene(999, white_hen=True))
    assert verdict.verdict == "sharp", verdict


def test_static_map_survives_restart(tmp_dir: Path | None = None):
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        state = Path(folder) / "focus-static-test.npz"
        judge = FocusJudge(state_path=state)
        for seed in range(WARMUP_FRAMES + 10):           # crosses a persist tick
            judge.judge(_scene(seed))
        assert state.exists()
        reborn = FocusJudge(state_path=state)
        assert reborn.judge(_scene(999, close_bird=True)).verdict == "blurred"


def _metadata(**overrides) -> dict:
    metadata = {
        "bird_count": 2, "lighting": "natural-good", "image_quality": "sharp",
        "subject_coverage_pct": 65, "largest_subject_pct": 30,
        "share_worth": "strong", "expression_score": 25, "detail_score": 22,
        "share_reason": "Close rooster, sharp eye, facing camera",
    }
    metadata.update(overrides)
    return metadata


def test_verdict_overrules_vlm_and_caps_score():
    metadata = _metadata()
    assert _apply_focus_verdict("s7-cam", metadata, FocusVerdict("blurred", 11.8))
    _compute_overall_score(metadata)
    _cap_score_for_focus(metadata)
    assert metadata["image_quality"] == "blurred"
    assert metadata["overall_score"] <= 45
    assert metadata["share_worth"] == "decent"
    assert "focus" in metadata["share_reason"].lower()
    assert metadata["focus_check"]["vlm_image_quality"] == "sharp"


def test_sharp_verdict_changes_nothing_and_never_promotes():
    crisp = _metadata()
    assert not _apply_focus_verdict("s7-cam", crisp, FocusVerdict("sharp", 0.4))
    _compute_overall_score(crisp)
    uncapped = crisp["overall_score"]
    _cap_score_for_focus(crisp)
    assert crisp["overall_score"] == uncapped and crisp["share_worth"] == "strong"

    vlm_soft = _metadata(image_quality="soft")
    assert not _apply_focus_verdict("s7-cam", vlm_soft, FocusVerdict("sharp", 0.4))
    assert vlm_soft["image_quality"] == "soft"


def test_abstention_is_a_no_op():
    metadata = _metadata()
    assert not _apply_focus_verdict("s7-cam", metadata, FocusVerdict(None, reason="warming_up"))
    assert not _apply_focus_verdict("s7-cam", metadata, None)
    assert "focus_check" not in metadata and metadata["image_quality"] == "sharp"


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"{len(tests)} passed")
