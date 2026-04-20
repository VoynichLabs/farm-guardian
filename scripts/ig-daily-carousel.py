#!/usr/bin/env python3
# Author: Claude Opus 4.7 (1M context)
# Date: 20-April-2026
# PURPOSE: LaunchAgent entry point for the daily Instagram carousel.
#          Runs once a day at 18:00 local via
#          com.farmguardian.ig-daily-carousel.plist. Selects today's
#          strong+sharp brooder gems (UTC day window), applies
#          diversity filter, builds a caption from the top-scoring
#          gem's VLM caption_draft + hashtag library, posts as a
#          CAROUSEL via Graph API.
#
#          No-action paths exit 0 so LaunchAgent doesn't flood retries:
#            - Zero candidates for today (pipeline had a bad day).
#            - Fewer than min_items candidates after diversity filter.
#            - Already posted today (defensive: ig_permalink check
#              via the SELECT's NULL guard).
#
#          Real failures exit 1 (and LaunchAgent's ThrottleInterval
#          prevents spin-looping).
#
# SRP/DRY check: Pass — single responsibility is "assemble and post
#                today's carousel." Selection logic lives in
#                tools/pipeline/ig_selection.py; posting in
#                tools/pipeline/ig_poster.post_carousel_to_ig. This
#                script is the glue + logging + exit-code policy.

"""
ig-daily-carousel.py — post today's strong+sharp brooder gems as one
carousel to @pawel_and_pawleen.

Invocation:
  $LaunchAgent cadence: once at 18:00 local (see
  deploy/launchd/com.farmguardian.ig-daily-carousel.plist).

  Manual invocation (testing):
    venv/bin/python scripts/ig-daily-carousel.py [--dry-run]

Exit codes:
  0 — posted successfully, OR no-action (nothing to post today)
  1 — runtime failure (DB, git, Graph API)
  3 — credentials missing
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
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


def _build_caption(gems: list[dict]) -> str:
    """Pick the highest-scoring gem's VLM caption_draft as the journal
    body; append hashtags from the library (brooder-scene defaults)
    via pick_hashtags. If no gem has a usable caption_draft, fall
    back to a generic day-summary line."""
    from tools.pipeline.ig_poster import build_caption, pick_hashtags, _load_hashtag_library

    # Pick the representative gem for the caption — highest bird_count
    # in the set (richer scenes -> better caption material).
    representative = max(gems, key=lambda g: g.get("bird_count") or 0)
    meta = {}
    try:
        meta = json.loads(representative.get("vlm_json") or "{}") or {}
    except json.JSONDecodeError:
        meta = {}

    journal = (meta.get("caption_draft") or "").strip()
    if not journal:
        n = len(gems)
        journal = (
            f"A day at the brooder — {n} moments from today's watch."
            if n > 1 else "A moment from today's brooder watch."
        )

    # Hashtags from the library, scene-driven.
    library = _load_hashtag_library(REPO_ROOT / "tools" / "pipeline" / "hashtags.yml")
    tags = pick_hashtags(vlm_metadata=meta, library=library, last_n_tags_used=[])

    return build_caption(journal_body=journal, hashtags=tags)


def _fetch_gem_rows(db_path: Path, gem_ids: list[int]) -> list[dict]:
    import sqlite3
    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        rows = []
        for gid in gem_ids:
            r = c.execute(
                "SELECT * FROM image_archive WHERE id = ?", (gid,),
            ).fetchone()
            if r:
                rows.append(dict(r))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Post today's strong+sharp brooder gems as one IG carousel.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Select + build caption but skip git push + Graph API.",
    )
    args = parser.parse_args(argv)
    _setup_logging()
    log = logging.getLogger("ig-daily-carousel")

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

    from tools.pipeline.ig_selection import select_daily_carousel_gems
    from tools.pipeline.ig_poster import post_carousel_to_ig, IGPosterError

    gem_ids = select_daily_carousel_gems(db_path=db_path, cfg=sched_cfg)
    if not gem_ids:
        log.info("daily-carousel: no candidates today; skipping slot")
        return 0

    gems = _fetch_gem_rows(db_path, gem_ids)
    if len(gems) != len(gem_ids):
        log.error(
            "daily-carousel: fetched %d rows but selected %d gem_ids",
            len(gems), len(gem_ids),
        )
        return 1

    try:
        caption = _build_caption(gems)
    except Exception as e:
        log.exception("daily-carousel: caption build failed: %s", e)
        return 1

    log.info(
        "daily-carousel: posting %d gems (dry_run=%s)\nCaption:\n%s",
        len(gem_ids), args.dry_run, caption,
    )

    try:
        result = post_carousel_to_ig(
            gem_ids=gem_ids,
            full_caption=caption,
            db_path=db_path,
            farm_2026_repo_path=farm_2026,
            dry_run=args.dry_run,
        )
    except IGPosterError as e:
        log.error("daily-carousel: credentials missing: %s", e)
        return 3

    if result.get("error"):
        log.error("daily-carousel: post failed: %s", result["error"])
        return 1

    if args.dry_run:
        log.info("daily-carousel: dry-run OK. Would have posted %d raw URLs.", len(result["raw_urls"]))
        return 0

    log.info(
        "daily-carousel: posted %d gems -> %s",
        len(gem_ids), result.get("permalink"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
