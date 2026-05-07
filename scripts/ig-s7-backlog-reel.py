#!/usr/bin/env python3
# Author: Claude Sonnet 4.6
# Date: 07-May-2026
# PURPOSE: LaunchAgent entry point for the S7 backlog Reel lane. Runs 4x/day
#          (09:00, 13:00, 17:00, 20:00 local) via
#          com.farmguardian.ig-s7-backlog-reel.plist.
#
#          Each run pulls the oldest 25 Discord-reacted portrait s7-cam gems
#          from the unposted pool, stitches them into a 9:16 portrait Reel,
#          posts to IG+FB without approval, marks those gems consumed so they
#          leave the story queue, and sends a Discord notice mentioning Mark.
#
#          When fewer than 20 eligible gems remain the script exits 0 cleanly.
#          The LaunchAgent keeps firing but does nothing — self-terminating once
#          the backlog drains.
#
#          Quality gate: discord_reactions >= 1 (Boss's own Discord reactions).
#          VLM share_worth is NOT used — Boss already curated these.
#
#          Shared mechanics: tools.pipeline.daily_reel_runner.S7_BACKLOG_REEL_LANE
#          Selector:         tools.pipeline.ig_selection.select_s7_backlog_reel_gems
#          Post-publish mark: tools.pipeline.ig_selection.mark_gems_used_in_backlog_reel
# SRP/DRY check: Pass — thin shim only; all lane logic is in daily_reel_runner.

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.daily_reel_runner import S7_BACKLOG_REEL_LANE, main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(S7_BACKLOG_REEL_LANE))
