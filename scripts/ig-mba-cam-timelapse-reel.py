#!/usr/bin/env python3
# Author: Claude Sonnet 4.6
# Date: 09-May-2026
# PURPOSE: LaunchAgent entry point for the MBA-cam brooder time-lapse Reel.
#          Runs daily at 20:30 local via
#          com.farmguardian.ig-mba-cam-timelapse-reel.plist. Selects raw-tier
#          mba-cam frames from the last 24h by sharpness, stitches them into
#          a 16:9 MP4 (landscape mode), auto-posts to IG/FB, then sends a
#          Discord notice mentioning Mark. Shared mechanics in
#          tools.pipeline.daily_reel_runner.
# SRP/DRY check: Pass — thin script shim only; all lane logic in
#                tools.pipeline.daily_reel_runner.

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.daily_reel_runner import MBA_CAM_TIMELAPSE_LANE, main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main(MBA_CAM_TIMELAPSE_LANE))
