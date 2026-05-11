# Author: OpenAI GPT-5.5
# Date: 10-May-2026
# PURPOSE: Regression coverage for raw time-lapse selector daylight filtering.
#          Ensures GWTC-style outdoor reels can exclude overnight frames while
#          preserving the historical all-hours behavior for non-daylight lanes.
# SRP/DRY check: Pass — tests only the selector contract, reusing the production
#                select_timelapse_gems helper and a minimal SQLite archive.

from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Allow running as `python tools/pipeline/test_ig_selection_timelapse.py`
# or as `python -m tools.pipeline.test_ig_selection_timelapse`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.pipeline.ig_selection import select_timelapse_gems  # noqa: E402


def _make_db(path: Path) -> None:
    with sqlite3.connect(path) as c:
        c.execute(
            """
            CREATE TABLE image_archive (
                id INTEGER PRIMARY KEY,
                camera_id TEXT NOT NULL,
                ts TEXT NOT NULL,
                image_path TEXT,
                image_tier TEXT NOT NULL,
                laplacian_var REAL
            )
            """
        )
        c.executemany(
            """
            INSERT INTO image_archive (id, camera_id, ts, image_path, image_tier, laplacian_var)
            VALUES (?, ?, ?, ?, 'raw', ?)
            """,
            [
                # 01:00 EDT — should be excluded for GWTC daylight-only reels.
                (1, "gwtc", "2026-05-10T05:00:00+00:00", "night.jpg", 100.0),
                # 06:00 EDT — inclusive start.
                (2, "gwtc", "2026-05-10T10:00:00+00:00", "morning.jpg", 90.0),
                # 13:00 EDT — normal daylight frame.
                (3, "gwtc", "2026-05-10T17:00:00+00:00", "midday.jpg", 80.0),
                # 20:00 EDT — exclusive end.
                (4, "gwtc", "2026-05-11T00:00:00+00:00", "evening.jpg", 70.0),
                # Non-daylight camera keeps all hours unless explicitly configured.
                (5, "mba-cam", "2026-05-10T05:00:00+00:00", "mba-night.jpg", 60.0),
                (6, "mba-cam", "2026-05-10T17:00:00+00:00", "mba-day.jpg", 50.0),
            ],
        )


def test_gwtc_timelapse_defaults_to_daylight_hours(tmp_path: Path) -> None:
    db_path = tmp_path / "guardian.db"
    _make_db(db_path)

    ids = select_timelapse_gems(
        "gwtc",
        db_path,
        {
            "timelapse_reel_window_hours": 48,
            "timelapse_reel_bucket_minutes": 60,
            "timelapse_reel_max_frames": 10,
            "timelapse_reel_min_frames": 1,
        },
        now=datetime(2026, 5, 11, 1, 0, tzinfo=timezone.utc),
    )

    assert ids == [2, 3]


def test_non_daylight_camera_keeps_all_hours_by_default(tmp_path: Path) -> None:
    db_path = tmp_path / "guardian.db"
    _make_db(db_path)

    ids = select_timelapse_gems(
        "mba-cam",
        db_path,
        {
            "timelapse_reel_window_hours": 48,
            "timelapse_reel_bucket_minutes": 60,
            "timelapse_reel_max_frames": 10,
            "timelapse_reel_min_frames": 1,
        },
        now=datetime(2026, 5, 11, 1, 0, tzinfo=timezone.utc),
    )

    assert ids == [5, 6]

def run_synthetic_cases() -> int:
    fails = 0
    with tempfile.TemporaryDirectory() as tmp:
        try:
            test_gwtc_timelapse_defaults_to_daylight_hours(Path(tmp))
            print("  [PASS] GWTC daylight-only filter excludes overnight frames")
        except AssertionError as exc:
            fails += 1
            print(f"  [FAIL] GWTC daylight-only filter: {exc}")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            test_non_daylight_camera_keeps_all_hours_by_default(Path(tmp))
            print("  [PASS] non-daylight cameras keep all-hours behavior")
        except AssertionError as exc:
            fails += 1
            print(f"  [FAIL] non-daylight camera behavior: {exc}")
    return fails


if __name__ == "__main__":
    sys.exit(1 if run_synthetic_cases() else 0)
