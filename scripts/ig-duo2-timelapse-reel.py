#!/usr/bin/env python3
# Author: Claude Opus 4.7
# Date: 22-June-2026
# PURPOSE: LaunchAgent entry point for the duo2 (Reolink Duo 2 WiFi) time-lapse
#          Reel. Runs daily at 21:20 local via
#          com.farmguardian.ig-duo2-timelapse-reel.plist. Selects raw-tier duo2
#          frames from the last 24h by sharpness, stitches them into a 16:9 MP4
#          (landscape mode; the Duo 2's 1920x720 panoramic letterboxes inside the
#          frame), auto-posts to IG/FB, then sends a Discord notice mentioning
#          Mark. Shared mechanics in tools.pipeline.daily_reel_runner. duo2 is a
#          stationary outdoor camera that captures all day, so unlike the
#          opportunistic dominator-cam lane the selector normally has plenty of
#          frames; it still skips cleanly (no error, no post) if fewer than the
#          configured min_frames qualify.
# SRP/DRY check: Pass — thin script shim only; all lane logic in
#                tools.pipeline.daily_reel_runner (mirrors ig-dominator-cam-timelapse-reel.py).

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.daily_reel_runner import DUO2_TIMELAPSE_LANE, main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main(DUO2_TIMELAPSE_LANE))
