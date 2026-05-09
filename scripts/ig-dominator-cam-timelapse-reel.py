#!/usr/bin/env python3
# Author: Claude Sonnet 4.6
# Date: 09-May-2026
# PURPOSE: LaunchAgent entry point for the Dominator-cam time-lapse Reel.
#          Runs daily at 21:15 local via
#          com.farmguardian.ig-dominator-cam-timelapse-reel.plist. Selects
#          raw-tier dominator-cam frames from the last 24h by sharpness,
#          stitches them into a 16:9 MP4 (landscape mode), auto-posts to
#          IG/FB, then sends a Discord notice mentioning Mark. Shared
#          mechanics in tools.pipeline.daily_reel_runner.
#          NOTE: Dominator-cam is manually started by Boss. When the camera
#          is off all day, the selector returns empty and the reel is skipped
#          cleanly (no error, no post).
# SRP/DRY check: Pass — thin script shim only; all lane logic in
#                tools.pipeline.daily_reel_runner.

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.daily_reel_runner import DOMINATOR_CAM_TIMELAPSE_LANE, main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main(DOMINATOR_CAM_TIMELAPSE_LANE))
