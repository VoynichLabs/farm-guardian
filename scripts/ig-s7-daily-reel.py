#!/usr/bin/env python3
# Author: GPT-5.5; Claude Opus 5 28-Jul-2026 — dawn-to-dusk single-day window
# Date: 03-May-2026; 28-Jul-2026 — retimed to 21:00, one calendar day
# PURPOSE: LaunchAgent entry point for the S7 daily dawn-to-dusk Reel.
#          Runs daily at 21:00 local via
#          com.farmguardian.ig-s7-daily-reel.plist. Selects sharp,
#          safe `s7-cam` frames from ONE local calendar day, stitches
#          them into a 9:16 MP4, posts the Reel to IG/FB without an
#          approval gate, then uploads a Discord notice that mentions
#          Mark's user ID. Shared mechanics live in
#          tools.pipeline.daily_reel_runner.
#
#          28-Jul-2026: was 12:00 local on a ROLLING 24h window, which
#          made every reel the back half of yesterday glued to the front
#          half of today. The 22-Jul move to noon was aimed at that
#          straddle, but the hour was never the cause — the rolling
#          window was. Now 21:00 + a single-day window, so the reel
#          covers exactly one day, first light to last.
#
#          Reacted gems get a longer hold on screen than un-reacted
#          filler (per_frame_seconds in reel_stitcher); un-reacted
#          frames still carry the bulk of the reel by design.
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
