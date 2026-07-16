#!/usr/bin/env python3
# Author: Claude Fable 5
# Date: 16-Jul-2026
# PURPOSE: LaunchAgent entry point for the consolidated "camera of the day"
#          time-lapse Reel (D10, farm-2026's
#          docs/16-Jul-2026-birdcatraz-era-refresh-plan.md). Runs daily at
#          20:15 local via com.farmguardian.ig-camera-of-the-day-reel.plist.
#          Picks one lane from daily_reel_runner.CAMERA_OF_THE_DAY_POOL
#          deterministically by day-of-year
#          (daily_reel_runner.pick_camera_of_the_day), then runs it through
#          the exact same build/stitch/post/notify path as every other
#          timelapse lane script — this file is a thin shim, same pattern
#          as scripts/ig-mba-cam-timelapse-reel.py and its siblings, just
#          with the lane chosen at runtime instead of hardcoded.
#
#          This sits ALONGSIDE the existing per-camera timelapse lanes and
#          plists (mba/gwtc/usb-cam/dominator-cam/duo2/house-yard) — it does
#          not replace or disable any of them. Because each lane's posted-
#          state file is keyed by lane_id + date
#          (daily_reel_runner._build_publish_and_notify's `posted_file`),
#          if this rotation and a picked lane's own standalone plist both
#          fire on the same day, whichever runs first posts and the second
#          is a no-op ("already posted; skipping") — no double-post risk
#          while both are live.
# SRP/DRY check: Pass - thin script shim only, mirrors the existing
#                per-camera timelapse wrapper scripts exactly; all lane
#                logic and the rotation function live in
#                tools.pipeline.daily_reel_runner.

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.daily_reel_runner import main, pick_camera_of_the_day  # noqa: E402


if __name__ == "__main__":
    sys.exit(main(pick_camera_of_the_day()))
