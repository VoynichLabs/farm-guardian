#!/usr/bin/env python3
# Author: Claude Opus 4.7 (1M context)
# Date: 22-April-2026
# PURPOSE: LaunchAgent entrypoint for the on-this-day → FB Stories
#          lane. Fires once per day via
#          ~/Library/LaunchAgents/com.farmguardian.on-this-day.plist
#          (09:00 local). Computes today's local calendar date,
#          delegates to tools.on_this_day.post_daily.main() with
#          --publish (story lane). No CLI args — LaunchAgents are not
#          a place for ad-hoc configuration.
#
#          This replaces the expectation that Boss would ever type
#          `python3 -m tools.on_this_day.post_daily --publish` at the
#          terminal. He shouldn't, and now doesn't have to.
#
# SRP/DRY check: Pass — thin shim. All real logic is in
#                tools.on_this_day.post_daily. Mirrors the
#                scripts/ig-*.py pattern so deploy/install flows
#                are identical.

from __future__ import annotations

import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.on_this_day import post_daily  # noqa: E402


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    # Synthesize the argv post_daily.main() expects. --publish (no
    # --carousel / --single flags) → story lane, which is what Boss
    # wants fired daily. --publish-n stays at the module default (8).
    sys.argv = [sys.argv[0], "--publish"]
    return post_daily.main()


if __name__ == "__main__":
    sys.exit(main())
