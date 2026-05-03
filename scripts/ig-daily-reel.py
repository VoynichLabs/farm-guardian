#!/usr/bin/env python3
# Author: GPT-5.5
# Date: 03-May-2026
# PURPOSE: LaunchAgent entry point for the existing mixed-camera daily
#          Instagram Reel. The scheduled behavior stays approval-gated:
#          build a daily MP4 preview from reacted source gems, post it
#          to Discord, then publish to IG/FB on a later run only if a
#          human reacts to the preview. The shared implementation lives
#          in tools.pipeline.daily_reel_runner so the S7 time-lapse
#          lane can reuse the same stitch/post/notice machinery without
#          duplicating this script.
# SRP/DRY check: Pass - thin script shim only; all lane logic is in
#                tools.pipeline.daily_reel_runner.

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.daily_reel_runner import (  # noqa: E402
    MIXED_DAILY_REEL_LANE,
    main,
)


if __name__ == "__main__":
    sys.exit(main(MIXED_DAILY_REEL_LANE))
