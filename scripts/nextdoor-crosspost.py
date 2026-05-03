#!/usr/bin/env python3
# Author: GPT-5.5
# Date: 03-May-2026
# PURPOSE: LaunchAgent entry point for the Nextdoor two-lane outbound
#          cross-poster. Thin wrapper around
#          tools.nextdoor.crosspost.main(); auto-infers lane from the
#          local clock when --lane isn't supplied. Morning still maps
#          to throwback, but tools.nextdoor.crosspost now fail-closes
#          that lane unless FARM_NEXTDOOR_THROWBACK_ENABLED=1.
#
# SRP/DRY check: Pass — just a launcher.

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools" / "nextdoor"))

import crosspost  # noqa: E402

if __name__ == "__main__":
    sys.exit(crosspost.main(sys.argv[1:]))
