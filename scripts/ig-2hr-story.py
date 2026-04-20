#!/usr/bin/env python3
# Author: Claude Opus 4.7 (1M context)
# Date: 20-April-2026
# PURPOSE: LaunchAgent entry point for the 2-hour Instagram story slot.
#          Runs every 2 hours via com.farmguardian.ig-2hr-story.plist.
#          Finds the single best strong-or-decent sharp-or-soft gem
#          from the last 2 hours that isn't already a story; posts
#          it as a Story (24h ephemeral, 9:16 center-crop, no caption).
#
#          No-action paths exit 0:
#            - No qualifying gem in the 2h window (cameras were quiet,
#              VLM rejected everything, etc.) — skip slot cleanly.
#
#          Real failures exit 1. Credential-missing exits 3.
#
# SRP/DRY check: Pass — single responsibility is "pick the best gem
#                in the window and post it as a story." Selection in
#                ig_selection.select_best_story_gem; posting in
#                ig_poster.post_gem_to_story. 9:16 prep + STORIES
#                media_type + DB writeback all live there.

"""
ig-2hr-story.py — post the best gem from the last 2 hours to
@pawel_and_pawleen as a Story.

Invocation:
  $LaunchAgent cadence: every 2 hours (see
  deploy/launchd/com.farmguardian.ig-2hr-story.plist).

  Manual invocation (testing):
    venv/bin/python scripts/ig-2hr-story.py [--dry-run]

Exit codes:
  0 — posted successfully, OR no-action (nothing to post this window)
  1 — runtime failure
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
        description="Post the best gem from the last 2 hours as an IG Story.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Select the gem but skip 9:16 prep + git push + Graph API.",
    )
    args = parser.parse_args(argv)
    _setup_logging()
    log = logging.getLogger("ig-2hr-story")

    cfg = _load_config()
    ig_cfg = cfg.get("instagram") or {}
    sched_cfg = ig_cfg.get("scheduled") or {}
    db_path = REPO_ROOT / cfg["guardian_db_path"]
    farm_2026 = Path(ig_cfg.get("farm_2026_repo_path", "")).expanduser()

    if not db_path.exists():
        log.error("guardian db not found: %s", db_path)
        return 1
    if not farm_2026.exists():
        log.error("farm_2026 repo not found: %s", farm_2026)
        return 1

    from tools.pipeline.ig_selection import select_best_story_gem
    from tools.pipeline.ig_poster import post_gem_to_story, IGPosterError

    gem_id = select_best_story_gem(db_path=db_path, cfg=sched_cfg)
    if gem_id is None:
        log.info("2hr-story: no candidates in window; skipping slot")
        return 0

    log.info("2hr-story: posting gem_id=%s (dry_run=%s)", gem_id, args.dry_run)

    try:
        result = post_gem_to_story(
            gem_id=gem_id,
            db_path=db_path,
            farm_2026_repo_path=farm_2026,
            dry_run=args.dry_run,
        )
    except IGPosterError as e:
        log.error("2hr-story: credentials missing: %s", e)
        return 3

    if result.get("error"):
        log.error("2hr-story: post failed: %s", result["error"])
        return 1

    if args.dry_run:
        log.info("2hr-story: dry-run OK. Would have posted -> %s", result.get("raw_url"))
        return 0

    log.info(
        "2hr-story: posted gem %s -> story_id=%s permalink=%s",
        gem_id, result.get("story_id"), result.get("permalink"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
