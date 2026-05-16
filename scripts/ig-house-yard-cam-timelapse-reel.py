#!/usr/bin/env python3
# PURPOSE: LaunchAgent entry point for the house-yard Reolink time-lapse Reel.
#          Selects raw-tier house-yard frames from the last 24h by sharpness,
#          stitches them into a 16:9 MP4 (landscape mode), posts a Discord
#          approval preview, and on thumbs-up auto-posts to IG/FB. Shared
#          mechanics in tools.pipeline.daily_reel_runner.
# SRP/DRY check: Pass — thin script shim only; all lane logic in
#                tools.pipeline.daily_reel_runner.

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.daily_reel_runner import HOUSE_YARD_CAM_TIMELAPSE_LANE, main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main(HOUSE_YARD_CAM_TIMELAPSE_LANE))
