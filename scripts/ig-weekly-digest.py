#!/usr/bin/env python3
# Author: Claude Fable 5
# Date: 16-Jul-2026
# PURPOSE: LaunchAgent entry point for the weekly Instagram performance
#          digest (Part B2 of farm-2026's 16-Jul-2026 Birdcatraz-era
#          refresh plan). Runs once a week, Sunday 20:00 local, via
#          com.farmguardian.ig-weekly-digest.plist. Delegates to
#          tools.pipeline.ig_insights.build_weekly_digest()/
#          post_weekly_digest(), which read the last 7 days of
#          ig_media_insights + ig_posted_captions, compute best/worst
#          post, posts-by-surface, and follower delta, and post a short
#          readable summary to the #farm-2026 Discord webhook
#          (informational, no @mention).
#
#          No-action path exits 0: zero posts in the window still
#          produces (and posts) a "no posts recorded this week" message
#          rather than silently doing nothing — Boss should notice a
#          quiet week, not wonder whether the job ran.
#
# SRP/DRY check: Pass — single responsibility is "build this week's
#                digest and post it." Query/compute/format logic lives
#                in tools/pipeline/ig_insights.py; this script is the
#                LaunchAgent glue + logging + exit-code policy, matching
#                scripts/ig-daily-carousel.py's shape.

"""
ig-weekly-digest.py — weekly Instagram performance recap to #farm-2026.

Invocation:
  LaunchAgent cadence: once weekly, Sunday 20:00 local (see
  deploy/ig-scheduled/com.farmguardian.ig-weekly-digest.plist).

  Manual invocation (testing):
    venv/bin/python scripts/ig-weekly-digest.py [--dry-run]

  --dry-run builds and prints the digest (real DB reads) but skips the
  Discord POST — no network call, no need for DISCORD_WEBHOOK_URL.

Exit codes:
  0 — digest built (and posted, unless --dry-run)
  1 — runtime failure (DB, or the Discord POST itself failed)
  3 — DISCORD_WEBHOOK_URL missing (non-dry-run only)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
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


def _load_env() -> None:
    from tools.pipeline.gem_poster import load_dotenv

    load_dotenv(REPO_ROOT / ".env")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post the weekly IG performance digest to Discord.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and print the digest; skip the Discord POST.",
    )
    args = parser.parse_args(argv)
    _setup_logging()
    log = logging.getLogger("ig-weekly-digest")
    _load_env()

    cfg = _load_config()
    db_path = REPO_ROOT / cfg["guardian_db_path"]
    if not db_path.exists():
        log.error("guardian db not found: %s", db_path)
        return 1

    from tools.pipeline.ig_insights import build_weekly_digest, post_weekly_digest

    try:
        message = build_weekly_digest(db_path)
    except Exception as e:
        log.exception("ig-weekly-digest: digest build failed: %s", e)
        return 1

    if args.dry_run:
        print(message)
        return 0

    if not os.environ.get("DISCORD_WEBHOOK_URL"):
        log.error("DISCORD_WEBHOOK_URL missing from environment")
        return 3

    ok = post_weekly_digest(db_path)
    if not ok:
        log.error("ig-weekly-digest: Discord post failed")
        return 1

    log.info("ig-weekly-digest: posted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
