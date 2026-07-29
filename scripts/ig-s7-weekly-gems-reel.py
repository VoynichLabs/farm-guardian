#!/usr/bin/env python3
# Author: Claude Sonnet 4.6; Claude Opus 5 28-Jul-2026 — backlog drain converted to weekly gems Reel
# Date: 07-May-2026; 28-Jul-2026 — weekly cadence, 7-day window
# PURPOSE: LaunchAgent entry point for the S7 weekly gems Reel. Runs Sundays
#          at 10:30 local via com.farmguardian.ig-s7-weekly-gems-reel.plist.
#
#          Each run picks the week's best Discord-reacted portrait s7-cam
#          gems, stitches them into a 9:16 portrait Reel, posts to IG+FB
#          without approval, marks those gems consumed so they leave the
#          story queue and cannot recur in a later weekly Reel, and sends a
#          Discord notice mentioning Mark.
#
#          HISTORY: this was the 4x/day s7-backlog drain (09/13/17/20). That
#          backlog is finished — 1,746 gems consumed, 15 left, under the
#          lane's own minimum — and it had degraded into an irregular gems
#          reel firing every 1-2 days. Converted in place rather than adding
#          a fifth S7 posting slot, and re-scoped from an unbounded
#          oldest-first pool to a sliding 7-day window: eligible gems arrive
#          at ~112/week, so any cap below that would have silently re-grown
#          the very backlog this lane had just cleared.
#
#          When fewer than 20 eligible gems exist in the window the script
#          exits 0 cleanly (quiet week — skip the slot).
#
#          Quality gate: discord_reactions >= 1 (Boss's own Discord
#          reactions). VLM share_worth is NOT used — Boss already curated.
#
#          Shared mechanics: tools.pipeline.daily_reel_runner.S7_WEEKLY_GEMS_REEL_LANE
#          Selector:         tools.pipeline.ig_selection.select_s7_weekly_gems_reel_gems
#          Post-publish mark: tools.pipeline.ig_selection.mark_gems_used_in_backlog_reel
#                             (marker string deliberately unchanged — 1,746
#                             historical rows carry it)
# SRP/DRY check: Pass — thin shim only; all lane logic is in daily_reel_runner.

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.daily_reel_runner import S7_WEEKLY_GEMS_REEL_LANE, main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(S7_WEEKLY_GEMS_REEL_LANE))
