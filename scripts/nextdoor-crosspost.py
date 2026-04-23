#!/usr/bin/env python3
# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: LaunchAgent entry point for the Nextdoor two-lane outbound
#          cross-poster. Thin wrapper around
#          tools.nextdoor.crosspost.main(); auto-infers lane from the
#          local clock when --lane isn't supplied (morning = throwback,
#          afternoon/evening = today).
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
