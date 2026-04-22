#!/usr/bin/env python3
# Author: Claude Opus 4.7 (1M context)
# Date: 22-April-2026
# PURPOSE: LaunchAgent entrypoint for FB Page engager-harvest. Fires
#          every 4 hours via
#          ~/Library/LaunchAgents/com.farmguardian.reciprocate.plist
#          to collect likes/reactions/comments across recent Page
#          posts and Stories, write the canonical engagers JSON, and
#          post a Discord summary to #farm-2026 so Boss sees a
#          clickable worklist of humans to reciprocate with.
#
# SRP/DRY check: Pass — thin shim around
#                tools.on_this_day.reciprocate.main(). Lives here to
#                match the deploy/install pattern the other
#                com.farmguardian.* LaunchAgents use.

from __future__ import annotations

import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.on_this_day import reciprocate  # noqa: E402


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    # No CLI args — the harvester's defaults are what we want on the
    # LaunchAgent cadence (2-day lookback, Discord notify on).
    sys.argv = [sys.argv[0]]
    return reciprocate.main()


if __name__ == "__main__":
    sys.exit(main())
