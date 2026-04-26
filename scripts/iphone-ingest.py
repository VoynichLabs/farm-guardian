#!/usr/bin/env python3
# Author: Claude Opus 4.7 (1M context)
# Date: 26-April-2026
# PURPOSE: LaunchAgent entrypoint for the iPhone live-ingest lane. Hourly
#          via com.farmguardian.iphone-ingest. Walks Photos.sqlite for
#          recent additions, runs the standard VLM pipeline on each new
#          one, posts strong-tier to Discord. The reaction-gated downstream
#          lanes (story, carousel) pick them up automatically.
# SRP/DRY check: Pass — argparse + logging shim around tools.iphone_lane.ingest.run.

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.iphone_lane.ingest import run


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Ingest recent iPhone photos into the farm gem lane."
    )
    ap.add_argument("--since-hours", type=float, default=6.0,
                    help="Look at photos added in the last N hours (default 6).")
    ap.add_argument("--max-per-run", type=int, default=8,
                    help="Cap VLM calls per run (default 8). Bursts of phone "
                         "photos drain over multiple runs.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Enumerate + convert but skip VLM/store/Discord.")
    ap.add_argument("--log-level", default="INFO",
                    choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = ap.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    summary = run(
        since_hours=args.since_hours,
        max_per_run=args.max_per_run,
        dry_run=args.dry_run,
        repo_root=REPO_ROOT,
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
