#!/usr/bin/env python3
# Author: Claude Sonnet 5 Extra
# Date: 03-Aug-2026
# PURPOSE: LaunchAgent entry point for the duo2 MONTHLY daylight panoramic
#          time-lapse Reel (~30 days, permanent keyframe-tier source — see
#          docs/03-Aug-2026-multi-day-timelapse-reels-plan.md). Shared
#          mechanics in tools.pipeline.daily_reel_runner.
# SRP/DRY check: Pass — thin script shim only; all lane logic in
#                tools.pipeline.daily_reel_runner (mirrors
#                ig-duo2-timelapse-reel.py).

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.daily_reel_runner import DUO2_MONTHLY_TIMELAPSE_LANE, main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main(DUO2_MONTHLY_TIMELAPSE_LANE))
