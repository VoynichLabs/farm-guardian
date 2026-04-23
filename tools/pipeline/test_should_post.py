# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Tests for tools/pipeline/gem_poster.should_post — the Discord
#          auto-post gate. Covers v2.36.4 legacy behavior (still the return
#          value while strict_gate_enabled=False) and the v2.37.0 strict
#          per-camera rules (mba-cam, gwtc) that fire when the flag flips.
# SRP/DRY check: Pass — no test fixtures duplicated elsewhere in the repo;
#                patches the two module-level config loaders so the tests
#                don't read the dev box's live config.json.

from __future__ import annotations

import pytest

from tools.pipeline import gem_poster


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _meta(**overrides) -> dict:
    """Build a schema-conforming VLM metadata dict. Defaults are the
    'obviously postable' frame: sharp, face visible, one bird, not skipped,
    active-enough activity, medium interest."""
    base = {
        "image_quality": "sharp",
        "bird_count": 1,
        "bird_face_visible": True,
        "share_worth": "decent",
        "bird_activity": "alert",
        "scene_interest": "medium",
    }
    base.update(overrides)
    return base


@pytest.fixture
def strict_off(monkeypatch):
    monkeypatch.setattr(gem_poster, "_strict_gate_enabled", lambda: False)
    monkeypatch.setattr(gem_poster, "_get_per_camera_rules",
                        lambda: gem_poster._DEFAULT_PER_CAMERA_RULES)


@pytest.fixture
def strict_on(monkeypatch):
    monkeypatch.setattr(gem_poster, "_strict_gate_enabled", lambda: True)
    monkeypatch.setattr(gem_poster, "_get_per_camera_rules",
                        lambda: gem_poster._DEFAULT_PER_CAMERA_RULES)


# ---------------------------------------------------------------------------
# Legacy behavior (s7-cam, strict_gate_enabled=False)
# ---------------------------------------------------------------------------

def test_s7_sharp_posts(strict_off):
    assert gem_poster.should_post(_meta(), "strong", camera_id="s7-cam") is True


def test_s7_soft_rejects(strict_off):
    assert gem_poster.should_post(
        _meta(image_quality="soft", bird_count=3), "strong", camera_id="s7-cam"
    ) is False


def test_legacy_skip_rejects(strict_off):
    assert gem_poster.should_post(
        _meta(share_worth="skip"), "strong", camera_id="mba-cam"
    ) is False


def test_legacy_no_birds_rejects(strict_off):
    assert gem_poster.should_post(
        _meta(bird_count=0), "strong", camera_id="s7-cam"
    ) is False


def test_legacy_soft_non_s7_with_face_posts(strict_off):
    # v2.36.4 fallback — soft + face OR >=2 birds on non-s7 cameras.
    assert gem_poster.should_post(
        _meta(image_quality="soft", bird_face_visible=True, bird_count=1),
        "strong", camera_id="mba-cam",
    ) is True


# ---------------------------------------------------------------------------
# Strict gate on mba-cam / gwtc
# ---------------------------------------------------------------------------

def test_mba_sleeping_rejects_even_if_sharp_and_face(strict_on):
    # Sleeping is a hard reject regardless of other signals.
    assert gem_poster.should_post(
        _meta(bird_activity="sleeping"), "strong", camera_id="mba-cam"
    ) is False


def test_mba_calling_sharp_face_posts(strict_on):
    assert gem_poster.should_post(
        _meta(bird_activity="calling"), "strong", camera_id="mba-cam"
    ) is True


def test_mba_standing_solo_medium_interest_rejects(strict_on):
    # standing is not in interesting_activities; interest=medium; bc=1 fails
    # the crowd fallback (min_bird_count_if_no_interest=2). Reject.
    assert gem_poster.should_post(
        _meta(bird_activity="standing", scene_interest="medium", bird_count=1),
        "strong", camera_id="mba-cam",
    ) is False


def test_mba_three_standing_medium_interest_posts(strict_on):
    # standing not in interesting list, but bc>=2 and interest!=low satisfies
    # the crowd fallback.
    assert gem_poster.should_post(
        _meta(bird_activity="standing", scene_interest="medium", bird_count=3),
        "strong", camera_id="mba-cam",
    ) is True


def test_mba_soft_quality_rejects(strict_on):
    # Strict gate only allows 'sharp' on mba-cam by default.
    assert gem_poster.should_post(
        _meta(image_quality="soft", bird_activity="calling"),
        "strong", camera_id="mba-cam",
    ) is False


def test_mba_no_face_rejects(strict_on):
    assert gem_poster.should_post(
        _meta(bird_face_visible=False, bird_activity="calling"),
        "strong", camera_id="mba-cam",
    ) is False


def test_gwtc_missing_activity_defaults_unclear_rejects(strict_on):
    # bird_activity omitted → defaults to 'unclear' → rejected.
    meta = _meta()
    meta.pop("bird_activity", None)
    assert gem_poster.should_post(meta, "strong", camera_id="gwtc") is False


def test_gwtc_high_interest_standing_posts(strict_on):
    # standing is not in interesting list, bc=1 fails crowd fallback, but
    # scene_interest='high' saves the frame.
    assert gem_poster.should_post(
        _meta(bird_activity="standing", scene_interest="high", bird_count=1),
        "strong", camera_id="gwtc",
    ) is True


def test_gwtc_low_interest_crowd_rejects(strict_on):
    # Even with bc=3, scene_interest='low' kills the crowd fallback.
    assert gem_poster.should_post(
        _meta(bird_activity="standing", scene_interest="low", bird_count=3),
        "strong", camera_id="gwtc",
    ) is False


# ---------------------------------------------------------------------------
# Shadow-logging / flag semantics
# ---------------------------------------------------------------------------

def test_strict_off_returns_legacy_even_when_strict_would_reject(strict_off):
    # Sleeping mba-cam frame: legacy v2.36.4 accepts (sharp + bc>=1 +
    # share_worth!='skip'); strict would reject. With the flag off, legacy
    # wins.
    assert gem_poster.should_post(
        _meta(bird_activity="sleeping"), "strong", camera_id="mba-cam"
    ) is True


def test_would_post_strict_independent_of_flag(strict_off):
    # would_post_strict runs the strict path regardless of the feature flag.
    assert gem_poster.would_post_strict(
        _meta(bird_activity="sleeping"), "strong", camera_id="mba-cam"
    ) is False
    assert gem_poster.would_post_strict(
        _meta(bird_activity="calling"), "strong", camera_id="mba-cam"
    ) is True


def test_unruled_camera_strict_falls_through_to_permissive(strict_on):
    # usb-cam has no entry in _DEFAULT_PER_CAMERA_RULES → strict path
    # returns True → legacy semantics preserved when strict_gate_enabled
    # is flipped on. (Here: sharp + face + bc=1 on usb-cam = legacy True.)
    assert gem_poster.should_post(
        _meta(), "strong", camera_id="usb-cam"
    ) is True


def test_shadow_logging_emits_both_decisions(strict_off, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="pipeline.gem_poster")
    gem_poster.should_post(
        _meta(bird_activity="sleeping"), "strong", camera_id="mba-cam"
    )
    msgs = [r.getMessage() for r in caplog.records]
    assert any("legacy=True" in m and "strict=False" in m for m in msgs)
    assert any("camera=mba-cam" in m for m in msgs)
