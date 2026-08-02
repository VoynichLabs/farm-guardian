#!/usr/bin/env python3
# Author: Claude Opus 5
# Date: 02-August-2026
# PURPOSE: LaunchAgent entry point for the jieli-dashcam time-lapse Reel. Runs
#          daily at 21:30 local via
#          com.farmguardian.ig-jieli-dashcam-timelapse-reel.plist — an end-of-day
#          slot, after the 20:00 daylight capture window closes, so one run covers
#          the whole day. The 24h look-back covers it either way; capture is
#          daylight-only (06:00-20:00).
#          Quota: REELS TAKE PRIORITY OVER GEMS (Boss, 02-Aug-2026). The gem lane
#          is capped below Instagram's real limit (tools/social/config.json ::
#          publisher_daily_cap) precisely so reel lanes always have slots. This
#          lane is not expected to be starved; if it ever logs "no slots free",
#          that cap has been raised and should be put back. Selects raw-tier
#          jieli-dashcam frames from the last 24h by sharpness, stitches them into
#          a 16:9 MP4 (the dashcam shoots 1280x720), auto-posts to IG/FB, then
#          sends a Discord notice mentioning Mark. All lane mechanics live in
#          tools.pipeline.daily_reel_runner.
#          CAVEAT worth knowing before debugging a strange-looking reel: unlike
#          every other time-lapse lane, this camera is NOT stationary — it gets
#          re-aimed often — so a day in which it moved yields a reel that cuts
#          between unrelated scenes. That is known and deferred, not a bug; see
#          docs/02-Aug-2026-dashcam-daily-reel-plan.md. It still skips cleanly
#          (no error, no post) if fewer than the configured min_frames qualify,
#          which is the normal outcome when the camera lost its USB hub power.
# SRP/DRY check: Pass — thin script shim only; all lane logic in
#                tools.pipeline.daily_reel_runner (mirrors ig-duo2-timelapse-reel.py).

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.daily_reel_runner import JIELI_DASHCAM_TIMELAPSE_LANE, main  # noqa: E402


if __name__ == "__main__":
    sys.exit(main(JIELI_DASHCAM_TIMELAPSE_LANE))
