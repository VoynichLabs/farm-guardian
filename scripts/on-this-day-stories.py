#!/usr/bin/env python3
# Author: GPT-5.5
# Date: 03-May-2026
# PURPOSE: DISABLED LaunchAgent entrypoint for the on-this-day FB/IG
#          lane. Boss rejected the current throwback/on-this-day
#          selection quality on 03-May-2026; it was surfacing irrelevant
#          old photos and polluting daily Reel material. This script now
#          exits successfully unless FARM_ON_THIS_DAY_STORIES_ENABLED=1
#          is explicitly set.
#
#          Future TODO: redesign as exact-date-only "on this day"
#          sourcing, e.g. May 3 2025 / May 3 2024 for May 3, with
#          strict date provenance and better captions before re-enabling.
#
# SRP/DRY check: Pass - thin fail-closed shim. All real on-this-day
#                logic remains in tools.on_this_day.post_daily for a
#                future redesign.

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.on_this_day import post_daily  # noqa: E402

_ENABLE_ENV = "FARM_ON_THIS_DAY_STORIES_ENABLED"


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if os.environ.get(_ENABLE_ENV) != "1":
        logging.getLogger("on-this-day-stories").warning(
            "on-this-day stories disabled; set %s=1 only after exact-date "
            "selection is redesigned",
            _ENABLE_ENV,
        )
        return 0

    # Historical behavior when explicitly re-enabled: --auto-story runs
    # one cycle and publishes a candidate as FB + IG Story. Do not set
    # the enable env until exact-date-only selection has been redesigned.
    sys.argv = [sys.argv[0], "--auto-story"]
    return post_daily.main()


if __name__ == "__main__":
    sys.exit(main())
