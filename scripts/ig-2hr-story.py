#!/usr/bin/env python3
# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026 (rewritten for all-reacted-gems FIFO loop)
# PURPOSE: LaunchAgent entry point for the reacted-gem story lane.
#          Runs every 2 hours via com.farmguardian.ig-2hr-story.plist.
#          Finds EVERY gem with a Discord reaction that has not yet
#          been posted as a Story (no time window — backlog-aware),
#          and posts each one as a Story (24h ephemeral, 9:16
#          center-crop, no caption) on IG + FB.
#
#          This is the gem lane, parallel to the archive lane at
#          tools/on_this_day/ which posts historical iPhone photos.
#          They share fb_poster + git_helper + ig_poster helpers and
#          run simultaneously without interference.
#
#          Boss directive 2026-04-23: every reacted gem is worthy.
#          No limit per tick, no "best only" filter — post them all.
#          The previous one-winner-per-window behaviour silently
#          dropped reacted gems that didn't score highest in their
#          window; that's been replaced with FIFO-all-reacted.
#
#          No-action paths exit 0 (nothing reacted but not yet posted).
#          Real failures exit 1. Credential-missing exits 3.
#          Partial success (some posted, some failed) exits 1 AFTER
#          logging every result so ops can see what went through.
#
# SRP/DRY check: Pass — orchestration only. Selection in
#                ig_selection.select_all_unposted_story_gems; posting
#                (9:16 prep + STORIES media_type + DB writeback +
#                FB dual-post) lives in ig_poster.post_gem_to_story.

"""
ig-2hr-story.py — post every reacted gem that hasn't been
storied yet to @pawel_and_pawleen (+ linked FB Page).

Invocation:
  LaunchAgent cadence: every 2 hours (see
  deploy/ig-scheduled/com.farmguardian.ig-2hr-story.plist).

  Manual invocation (testing):
    venv/bin/python scripts/ig-2hr-story.py [--dry-run]

Exit codes:
  0 — all reacted gems posted (or nothing to post this tick)
  1 — runtime failure on at least one gem
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
        help="Select the gem but skip 9:16 prep + local hosting + Graph API.",
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
        log.warning(
            "farm_2026 repo not found but Story posting now uses local Guardian hosting: %s",
            farm_2026,
        )

    from tools.pipeline.ig_selection import select_all_unposted_story_gems
    from tools.pipeline.ig_poster import post_gem_to_story, IGPosterError

    # Instagram Graph API caps Business publishes at 25 per rolling
    # 24h window (media + stories, combined). Above that the API
    # returns a (code=4) rate-limit error and the rest of the batch
    # fails. Cap here so we drain large backlogs cleanly over successive
    # ticks instead of burning the whole day's quota in one burst.
    # Override via config.instagram.scheduled.story_max_per_tick.
    max_per_tick = int(sched_cfg.get("story_max_per_tick", 25))

    all_gem_ids = select_all_unposted_story_gems(db_path=db_path, cfg=sched_cfg)
    if not all_gem_ids:
        log.info("2hr-story: no unposted reacted gems; slot idle")
        return 0

    gem_ids = all_gem_ids[:max_per_tick]
    if len(all_gem_ids) > max_per_tick:
        log.info(
            "2hr-story: %d reacted gem(s) awaiting publish; posting oldest %d "
            "this tick (cap=%d). Remaining %d will drain on subsequent ticks.",
            len(all_gem_ids), len(gem_ids), max_per_tick,
            len(all_gem_ids) - max_per_tick,
        )
    else:
        log.info(
            "2hr-story: %d reacted gem(s) to publish (dry_run=%s)",
            len(gem_ids), args.dry_run,
        )

    any_error = False
    posted_count = 0
    for gem_id in gem_ids:
        try:
            result = post_gem_to_story(
                gem_id=gem_id,
                db_path=db_path,
                farm_2026_repo_path=farm_2026,
                dry_run=args.dry_run,
            )
        except IGPosterError as e:
            # Credentials missing is a hard stop — the remaining gems
            # won't post either, and we shouldn't keep retrying.
            log.error("2hr-story: credentials missing (stopping batch): %s", e)
            return 3
        except Exception as e:
            log.exception("2hr-story: unexpected failure on gem %s", gem_id)
            any_error = True
            continue

        # Detect IG's 25-publish-per-24h rate limit (shared across
        # every lane hitting @pawel_and_pawleen — this gem pipeline AND
        # the on_this_day archive story pipeline). Graph returns
        # HTTP 403 on container-status polls once the quota is burned.
        # Continuing the batch would waste compute hitting the wall
        # for every remaining gem; stop cleanly, next 2h tick picks
        # up where we left off (quota is rolling, slots free up as
        # the oldest publishes age past 24h).
        if result.get("error") and ("403" in str(result["error"]) or
                                    "rate" in str(result["error"]).lower() or
                                    "limit" in str(result["error"]).lower()):
            log.warning(
                "2hr-story: IG 24h publish quota exhausted after %d gem(s); "
                "stopping batch. %d gem(s) remain queued for next tick.",
                posted_count, len(gem_ids) - posted_count - 1,
            )
            break

        if result.get("error"):
            log.error("2hr-story: gem %s post failed: %s", gem_id, result["error"])
            any_error = True
            continue

        if args.dry_run:
            log.info(
                "2hr-story: dry-run OK gem %s -> would post %s",
                gem_id, result.get("raw_url"),
            )
        else:
            log.info(
                "2hr-story: posted gem %s -> story_id=%s permalink=%s",
                gem_id, result.get("story_id"), result.get("permalink"),
            )
            posted_count += 1

    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())
