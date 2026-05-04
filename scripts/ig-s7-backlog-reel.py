#!/usr/bin/env python3
# Author: Claude Sonnet 4.6
# Date: 04-May-2026
# PURPOSE: LaunchAgent entry point for the S7 backlog Reel lane. Runs daily
#          at 12:00 local via com.farmguardian.ig-s7-backlog-reel.plist.
#
#          Each run finds the oldest calendar date that has >= min_frames
#          unprocessed reacted s7-cam gems, stitches them into a 9:16
#          time-lapse Reel, posts it to IG/FB without an approval gate, marks
#          those gems 'used-in-backlog-reel' so they leave the story queue,
#          and sends a Discord notice mentioning Mark.
#
#          When the backlog is empty (no eligible date found) the script exits
#          0 cleanly. The LaunchAgent keeps firing daily but does nothing —
#          cheap and self-terminating once the backlog drains.
#
#          Shared mechanics: tools.pipeline.daily_reel_runner.S7_BACKLOG_REEL_LANE
#          Selector:         tools.pipeline.ig_selection.select_s7_backlog_reel_gems
#          Post-publish mark: tools.pipeline.ig_selection.mark_gems_used_in_backlog_reel
# SRP/DRY check: Pass — thin shim only; all lane logic is in daily_reel_runner.

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.daily_reel_runner import S7_BACKLOG_REEL_LANE, run_lane, setup_logging  # noqa: E402

log = logging.getLogger("ig-s7-backlog-reel")


def _find_next_target_date(db_path: Path, min_frames: int, state_dir: Path) -> str | None:
    """Return the oldest calendar date with >= min_frames unprocessed s7-cam gems.

    Skips dates that already have a posted state file in state_dir.
    Returns None when the backlog is empty.
    """
    with sqlite3.connect(str(db_path)) as c:
        rows = c.execute(
            """
            SELECT DATE(ts) as day, COUNT(*) as cnt
              FROM image_archive
             WHERE camera_id = 's7-cam'
               AND discord_reactions >= 1
               AND (has_concerns = 0 OR has_concerns IS NULL)
               AND image_path IS NOT NULL
               AND (ig_story_skip_reason IS NULL
                    OR (ig_story_skip_reason NOT LIKE 'story-permanent-skip:%'
                        AND ig_story_skip_reason NOT LIKE 'used-in-backlog-reel:%'))
             GROUP BY day
             ORDER BY day ASC
            """,
        ).fetchall()

    for day, cnt in rows:
        if cnt < min_frames:
            log.info("skip %s: only %d eligible gems (need >=%d)", day, cnt, min_frames)
            continue
        posted_file = state_dir / f"{day}.json"
        if posted_file.exists():
            log.info("skip %s: already posted (%s exists)", day, posted_file.name)
            continue
        return day

    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=S7_BACKLOG_REEL_LANE.description)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build locally; skip Discord upload, IG post, and ledger writes.",
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help="Force a specific target date instead of auto-detecting the oldest.",
    )
    args = parser.parse_args(argv)
    setup_logging()

    # Load config to resolve db path and frame floor
    cfg_path = REPO_ROOT / "tools" / "pipeline" / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    db_path = REPO_ROOT / cfg["guardian_db_path"]
    if not db_path.exists():
        log.error("guardian db not found: %s", db_path)
        return 1

    sched_cfg = (cfg.get("instagram") or {}).get("scheduled") or {}
    min_frames = int(sched_cfg.get("s7_backlog_reel_min_frames", 10))
    reels_cfg = (cfg.get("instagram") or {}).get("reels") or {}
    state_root = REPO_ROOT / reels_cfg.get("output_root", "data/reels") / "s7-backlog" / "posted"
    state_root.mkdir(parents=True, exist_ok=True)

    target_date = args.date or _find_next_target_date(db_path, min_frames, state_root)

    if not target_date:
        log.info("s7 backlog is empty — nothing to do")
        return 0

    log.info("targeting backlog date: %s", target_date)
    return run_lane(
        lane=S7_BACKLOG_REEL_LANE,
        dry_run=args.dry_run,
        target_date=target_date,
    )


if __name__ == "__main__":
    sys.exit(main())
