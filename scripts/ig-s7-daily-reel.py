#!/usr/bin/env python3
# Author: GPT-5.5
# Date: 03-May-2026
# PURPOSE: LaunchAgent entry point for the S7 daily time-lapse Reel.
#          Runs daily at 21:00 local via
#          com.farmguardian.ig-s7-daily-reel.plist. Selects sharp,
#          safe `s7-cam` frames from the last 24 hours, stitches them
#          into a 9:16 MP4, posts the Reel to IG/FB without an approval
#          gate, then uploads a Discord notice that mentions Mark's
#          user ID. Shared mechanics live in
#          tools.pipeline.daily_reel_runner.
# SRP/DRY check: Pass - thin script shim only; all lane logic is in
#                tools.pipeline.daily_reel_runner.

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.daily_reel_runner import S7_DAILY_REEL_LANE, main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main(S7_DAILY_REEL_LANE))
