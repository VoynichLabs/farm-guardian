#!/usr/bin/env python3
# Author: Claude Fable 5
# Date: 16-Jul-2026
# PURPOSE: LaunchAgent entry point for the nightly Instagram insights
#          fetch (Part B1 of farm-2026's 16-Jul-2026 Birdcatraz-era
#          refresh plan). Runs once nightly at 23:30 local via
#          com.farmguardian.ig-insights-fetch.plist. Delegates to
#          tools.pipeline.ig_insights.run_nightly_fetch(), which pulls
#          per-media likes/comments/reach/saved/plays (and one
#          once-per-run follower-count snapshot) for every media_id
#          recorded in ig_posted_captions over the last `--lookback-days`
#          days, inserting a fresh ig_media_insights row per media
#          (time series — engagement grows after posting, so this is
#          never an upsert).
#
#          No-action paths exit 0 so LaunchAgent doesn't flood retries:
#            - Zero media_id rows in ig_posted_captions within the
#              lookback window (quiet week, or ledger just created).
#
#          Real failures exit 1. Credential-missing exits 3.
#
# SRP/DRY check: Pass — single responsibility is "run tonight's insights
#                fetch and log the outcome." Fetch/parse/schema logic
#                lives in tools/pipeline/ig_insights.py; this script is
#                the LaunchAgent glue + logging + exit-code policy,
#                matching scripts/ig-daily-carousel.py's shape.

"""
ig-insights-fetch.py — nightly pull of Instagram Graph API insights
(likes/comments/reach/saved/plays) for every media_id posted in the
lookback window, plus one follower-count snapshot per run.

Invocation:
  LaunchAgent cadence: once nightly at 23:30 local (see
  deploy/ig-scheduled/com.farmguardian.ig-insights-fetch.plist).

  Manual invocation (testing):
    venv/bin/python scripts/ig-insights-fetch.py [--dry-run] [--lookback-days N]

  --dry-run does NOT loop the full lookback window — it probes
  credentials plus at most one real recent media_id (from
  ig_posted_captions, if one exists) and logs the result without
  inserting anything. See tools/pipeline/ig_insights.dry_run_probe().

Exit codes:
  0 — fetch completed (including the zero-media no-op case)
  1 — runtime failure (DB, Graph API infra)
  3 — credentials missing
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def _load_config() -> dict:
    cfg_path = REPO_ROOT / "tools" / "pipeline" / "config.json"
    return json.loads(cfg_path.read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Nightly fetch of IG Graph API insights for recently posted media.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Probe credentials + one real media_id (if any) and log the "
            "result; skip the full lookback loop and all DB inserts."
        ),
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=14,
        help="How many days back (by ig_posted_captions.posted_at) to (re-)fetch insights for. Default 14.",
    )
    args = parser.parse_args(argv)
    _setup_logging()
    log = logging.getLogger("ig-insights-fetch")

    cfg = _load_config()
    db_path = REPO_ROOT / cfg["guardian_db_path"]
    if not db_path.exists():
        log.error("guardian db not found: %s", db_path)
        return 1

    from tools.pipeline.ig_insights import dry_run_probe, run_nightly_fetch
    from tools.pipeline.ig_poster import IGPosterError

    try:
        if args.dry_run:
            result = dry_run_probe(db_path)
        else:
            result = run_nightly_fetch(db_path, lookback_days=args.lookback_days)
    except IGPosterError as e:
        log.error("ig-insights-fetch: credentials missing: %s", e)
        return 3
    except Exception as e:
        log.exception("ig-insights-fetch: runtime failure: %s", e)
        return 1

    log.info("ig-insights-fetch: result -> %s", json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
