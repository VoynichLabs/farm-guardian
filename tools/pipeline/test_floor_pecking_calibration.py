# Author: GPT-5.5 Codex; Claude Opus 4.8 (Bubba) (12-Jul-2026 — cap assertions + inputs rescaled to 0-100, v2.45.0)
# Date: 08-May-2026
# PURPOSE: Focused synthetic tests for orchestrator post-VLM calibration that
#          demotes routine brooder/coop floor-pecking frames before storage or
#          Discord posting. Covers the boss-flagged water-bowl / feeder scatter
#          false positive and a clean portrait non-regression.
# SRP/DRY check: Pass — this file only validates the deterministic calibration
#                helper and does not duplicate the full pipeline.

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python tools/pipeline/test_floor_pecking_calibration.py`
# or as `python -m tools.pipeline.test_floor_pecking_calibration`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.pipeline.orchestrator import _calibrate_static_floor_pecking_score  # noqa: E402


BAD_WATER_BOWL_CAPTION = (
    "A white chick stands alert near a water bowl while a brown-and-white "
    "speckled chick pecks at the ground nearby. In the background, a "
    "blue-grey bird walks past a feeder, and two other small birds forage "
    "in the shadows under the fence line."
)


def _base_metadata(**overrides) -> dict:
    metadata = {
        "scene": "coop",
        "bird_count": 5,
        "individuals_visible": ["chick"],
        "any_special_chick": True,
        "apparent_age_days": 28,
        "activity": "alert",
        "lighting": "natural-good",
        "composition": "group",
        "image_quality": "sharp",
        "bird_face_visible": True,
        "subject_coverage_pct": 38,
        "largest_subject_pct": 18,
        "share_worth": "strong",
        "overall_score": 55,
        "share_reason": "Several chicks are scattered around the coop floor.",
        "caption_draft": BAD_WATER_BOWL_CAPTION,
        "concerns": [],
    }
    metadata.update(overrides)
    return metadata


def _expect(label: str, condition: bool) -> int:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}")
    return 0 if condition else 1


def run_synthetic_cases() -> int:
    print("=== floor-pecking calibration synthetic cases ===")
    fails = 0

    bad = _base_metadata()
    changed = _calibrate_static_floor_pecking_score("gwtc", bad)
    fails += _expect("boss-flagged water-bowl scatter is calibrated", changed)
    fails += _expect("boss-flagged score capped to 30", bad["overall_score"] == 30)
    fails += _expect("boss-flagged frame demoted to skip", bad["share_worth"] == "skip")

    portrait = _base_metadata(
        scene="brooder",
        bird_count=4,
        activity="alert",
        composition="portrait",
        subject_coverage_pct=70,
        largest_subject_pct=48,
        overall_score=85,
        share_worth="strong",
        share_reason="One chick has a sharp face and direct eye contact.",
        caption_draft=(
            "A speckled chick stares straight into the lens while three "
            "siblings move softly behind it."
        ),
    )
    before = portrait.copy()
    changed = _calibrate_static_floor_pecking_score("usb-cam", portrait)
    fails += _expect("clean close portrait is not calibrated", not changed)
    fails += _expect("clean close portrait metadata stays unchanged", portrait == before)

    print(f"synthetic: {fails} failure(s)")
    return fails


if __name__ == "__main__":
    sys.exit(1 if run_synthetic_cases() else 0)
