#!/usr/bin/env python3
# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: LaunchAgent entrypoint for the unified social publisher.
#          Fires every 60 min via com.farmguardian.social-publisher.
#          Decides whether to post a reacted gem or fall back to an
#          archive photo, respecting the rolling 24h IG publish cap.
#          All real work is in tools/social/publisher.py.
#
# SRP/DRY check: Pass — thin shim, same pattern as ig-2hr-story.py
#                and on-this-day-stories.py.

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.social import publisher  # noqa: E402


if __name__ == "__main__":
    sys.exit(publisher.main())
