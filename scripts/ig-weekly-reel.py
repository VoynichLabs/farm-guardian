#!/usr/bin/env python3
# Author: Claude Opus 4.7 (1M context)
# Date: 20-April-2026
# PURPOSE: LaunchAgent entry point for the weekly Instagram Reel.
#          Runs Sundays at 19:00 local via
#          com.farmguardian.ig-weekly-reel.plist. Picks the strongest
#          strong+sharp gems from the past 7 days (one per 6-hour
#          bucket for temporal spread), stitches them into a 9:16
#          MP4 via reel_stitcher, posts as media_type=REELS.
#
#          No-action paths exit 0:
#            - Fewer than 2 qualifying gems in the past week (quiet
#              week — skip the slot).
#
#          Real failures (ffmpeg exit, Graph API rejection) exit 1.
#          Credential-missing exits 3.
#
# SRP/DRY check: Pass — single responsibility is "assemble and post
#                the weekly reel." Selection in ig_selection.select_
#                weekly_reel_gems; stitching in reel_stitcher.stitch_
#                gems_to_reel; posting in ig_poster.post_reel_to_ig.

"""
ig-weekly-reel.py — stitch the past week's best brooder gems into a
Reel and post to @pawel_and_pawleen.

Invocation:
  $LaunchAgent cadence: Sundays at 19:00 local (see
  deploy/launchd/com.farmguardian.ig-weekly-reel.plist).

  Manual invocation (testing):
    venv/bin/python scripts/ig-weekly-reel.py [--dry-run]

Exit codes:
  0 — posted successfully, OR no-action (nothing to post this week)
  1 — runtime failure (ffmpeg, git, Graph API)
  3 — credentials missing
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
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


def _fetch_gem_row(db_path: Path, gem_id: int) -> dict:
    import sqlite3
    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        r = c.execute(
            "SELECT * FROM image_archive WHERE id = ?", (gem_id,),
        ).fetchone()
    return dict(r) if r else {}


def _build_reel_caption(db_path: Path, gem_ids: list[int]) -> str:
    """Reel caption: journal body from the highest-bird-count gem's
    VLM caption_draft (falling back to a generic week-summary line),
    plus hashtags from the library."""
    from tools.pipeline.ig_poster import build_caption, pick_hashtags, _load_hashtag_library

    best_gem = None
    best_meta = {}
    best_birds = -1
    for gid in gem_ids:
        row = _fetch_gem_row(db_path, gid)
        try:
            meta = json.loads(row.get("vlm_json") or "{}") or {}
        except json.JSONDecodeError:
            meta = {}
        birds = meta.get("bird_count") or 0
        if birds > best_birds:
            best_birds = birds
            best_gem = row
            best_meta = meta

    journal = (best_meta.get("caption_draft") or "").strip()
    if not journal:
        journal = "A week at the brooder — moments from the flock."

    library = _load_hashtag_library(REPO_ROOT / "tools" / "pipeline" / "hashtags.yml")
    tags = pick_hashtags(vlm_metadata=best_meta, library=library, last_n_tags_used=[])

    return build_caption(journal_body=journal, hashtags=tags)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stitch + post the weekly best-of brooder reel.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Stitch the MP4 locally but skip git push + Graph API.",
    )
    args = parser.parse_args(argv)
    _setup_logging()
    log = logging.getLogger("ig-weekly-reel")

    cfg = _load_config()
    ig_cfg = cfg.get("instagram") or {}
    sched_cfg = ig_cfg.get("scheduled") or {}
    reels_cfg = ig_cfg.get("reels") or {}
    db_path = REPO_ROOT / cfg["guardian_db_path"]
    farm_2026 = Path(ig_cfg.get("farm_2026_repo_path", "")).expanduser()

    if not db_path.exists():
        log.error("guardian db not found: %s", db_path)
        return 1
    if not farm_2026.exists():
        log.error("farm_2026 repo not found: %s", farm_2026)
        return 1

    from tools.pipeline.ig_selection import select_weekly_reel_gems
    from tools.pipeline.reel_stitcher import stitch_gems_to_reel, ReelStitcherError
    from tools.pipeline.ig_poster import post_reel_to_ig, IGPosterError

    gem_ids = select_weekly_reel_gems(db_path=db_path, cfg=sched_cfg)
    if not gem_ids:
        log.info("weekly-reel: no candidates this week; skipping slot")
        return 0

    log.info("weekly-reel: stitching %d gems (dry_run=%s)", len(gem_ids), args.dry_run)

    # Resolve output path using repo root as reference so relative
    # output_root in config doesn't bite when the script's CWD differs.
    output_root = Path(reels_cfg.get("output_root", "data/reels"))
    if not output_root.is_absolute():
        output_root = REPO_ROOT / output_root
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    out_dir = output_root / ym
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    slug = uuid.uuid4().hex[:8]
    mp4_path = out_dir / f"reel-weekly-{stamp}-{slug}.mp4"

    try:
        stitch_gems_to_reel(
            gem_ids=gem_ids,
            db_path=db_path,
            config=reels_cfg,
            output_path=mp4_path,
        )
    except ReelStitcherError as e:
        log.error("weekly-reel: stitch failed: %s", e)
        return 1

    try:
        caption = _build_reel_caption(db_path, gem_ids)
    except Exception as e:
        log.exception("weekly-reel: caption build failed: %s", e)
        return 1

    log.info(
        "weekly-reel: posting mp4 %s (%d bytes)\nCaption:\n%s",
        mp4_path, mp4_path.stat().st_size, caption,
    )

    try:
        result = post_reel_to_ig(
            reel_mp4_path=mp4_path,
            caption=caption,
            db_path=db_path,
            farm_2026_repo_path=farm_2026,
            associated_gem_ids=gem_ids,
            dry_run=args.dry_run,
        )
    except IGPosterError as e:
        log.error("weekly-reel: credentials missing: %s", e)
        return 3

    if result.get("error"):
        log.error("weekly-reel: post failed: %s", result["error"])
        return 1

    if args.dry_run:
        log.info("weekly-reel: dry-run OK. Would have posted -> %s", result.get("raw_url"))
        return 0

    log.info(
        "weekly-reel: posted reel from %d gems -> %s",
        len(gem_ids), result.get("permalink"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
