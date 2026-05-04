# Author: GPT-5.5
# Date: 03-May-2026
# PURPOSE: One-decider orchestrator for IG+FB story publishing across
#          the gem lane and the archive lane. Fires every 60 min via
#          com.farmguardian.social-publisher (see
#          deploy/ig-scheduled/com.farmguardian.social-publisher.plist).
#
#          Priority rule (locked 2026-04-23, per Boss decisions in
#          docs/23-Apr-2026-smart-publishing-queue-plan.md):
#
#            1. Count publishes in the rolling 24h window from the
#               shared ledger. If quota exhausted, exit cleanly.
#            2. Drain the gem queue FIFO (oldest reacted gem first)
#               until either the queue is empty OR we've posted
#               max_per_tick OR quota is tight. Every reacted gem
#               is eligible forever — no stale cutoff.
#            3. Only if archive fallback is enabled, the gem queue is
#               EMPTY, and slots_free >= archive_reserve_floor, post one
#               from the archive lane. This guarantees gem-priority:
#               even a single queued gem blocks the archive fallback.
#            4. Stop on 403 (platform-side quota disagreement with
#               our ledger — rare, happens when carousel/reel lanes
#               or other agents publish outside this process).
#
#          The actual publish side-effects (9:16 prep, farm-2026
#          commit, Graph API publish, DB writeback) are delegated to
#          the existing ig_poster.post_gem_to_story and
#          on_this_day.post_daily._publish_one_story helpers — this
#          module does not re-implement them.
#
#          2026-05-03 priority fix: archive fallback is gated by gem
#          queue depth, not by gem posted count. A non-empty reacted-gem
#          queue blocks archive posting even when the oldest attempted
#          gem rows fail. The drain loop may look ahead through a bounded
#          number of queued rows to avoid one bad old row stalling later
#          reacted gems, while still capping successful posts per tick.
#          File/path-style permanent failures are marked in
#          image_archive.ig_story_skip_reason with the
#          story-permanent-skip prefix so the selector stops retrying
#          dead rows; transient API/git failures are not marked.
#
#          2026-05-03 throwback deactivation: archive/on-this-day
#          fallback is disabled unless tools/social/config.json sets
#          archive_fallback_enabled=true. Boss rejected the current
#          throwback selection quality after old winter photos leaked
#          into daily Reel material.
#
# SRP/DRY check: Pass — decision logic only. Publish helpers are
#                imported; ledger in tools.social.ledger. No Graph
#                API calls in this file.

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.social import ledger  # noqa: E402

log = logging.getLogger("tools.social.publisher")

CONFIG_PATH = Path(__file__).parent / "config.json"

# Error substrings that mean "stop — quota hit, not an auth or
# transient problem." 403 covers the Meta rate-limit response; the
# "rate"/"limit" keywords catch the human-readable wrappers.
_QUOTA_MARKERS = ("403", "rate limit", "rate-limit", "application request limit")
_GEM_LOOKAHEAD_MULTIPLIER = 3
_PERMANENT_GEM_ERROR_MARKERS = (
    "filenotfounderror",
    "no such file or directory",
    "_prepare_story_image",
    "resolve_gem_image_path",
    "missing source",
    "not found in image_archive",
)


def _load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _looks_like_quota_error(result: dict) -> bool:
    err = str(result.get("error") or "").lower()
    return any(marker in err for marker in _QUOTA_MARKERS)


def _looks_like_permanent_gem_error(result: dict) -> bool:
    err = str(result.get("error") or "").lower()
    return any(marker in err for marker in _PERMANENT_GEM_ERROR_MARKERS)


def _mark_story_permanent_skip(db_path: Path, gem_id: int, error: str) -> None:
    reason = f"story-permanent-skip:{error}"[:500]
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            UPDATE image_archive
               SET ig_story_skip_reason = ?
             WHERE id = ?
            """,
            (reason, gem_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Gem lane: drain from image_archive
# ---------------------------------------------------------------------------


def _load_guardian_config() -> dict:
    cfg_path = REPO_ROOT / "tools" / "pipeline" / "config.json"
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def _post_gem(gem_id: int, db_path: Path, farm_2026_repo: Path, dry_run: bool) -> dict:
    """Delegate to ig_poster.post_gem_to_story. Returns the result
    dict unchanged (never raises for non-credential errors)."""
    from tools.pipeline.ig_poster import post_gem_to_story
    return post_gem_to_story(
        gem_id=gem_id,
        db_path=db_path,
        farm_2026_repo_path=farm_2026_repo,
        dry_run=dry_run,
    )


def _drain_gem_queue(
    db_path: Path,
    farm_2026_repo: Path,
    slots_remaining: int,
    max_per_tick: int,
    ledger_path: Path,
    dry_run: bool,
) -> tuple[int, bool, int, int]:
    """Post gems FIFO up to min(slots_remaining, max_per_tick) successes.
    Returns (posted_count, hit_quota_403, queue_depth, attempted_count).
    hit_quota_403=True means
    the platform returned 403 mid-batch — publisher should exit the
    whole tick regardless of remaining gems.

    If gems remain after max_per_tick, they are not lost — the next
    tick picks them up. Backlog is eventual-consistency, not per-tick.
    """
    from tools.pipeline.ig_selection import select_all_unposted_story_gems

    sched_cfg = (_load_guardian_config().get("instagram") or {}).get("scheduled") or {}
    all_gem_ids = select_all_unposted_story_gems(db_path=db_path, cfg=sched_cfg)
    queue_depth = len(all_gem_ids)
    if not all_gem_ids:
        log.info("publisher: gem queue empty")
        return 0, False, 0, 0

    success_cap = min(slots_remaining, max_per_tick)
    if success_cap <= 0:
        log.info(
            "publisher: gem queue has %d but no slots are available this tick",
            queue_depth,
        )
        return 0, False, queue_depth, 0

    attempt_cap = min(
        queue_depth,
        max(success_cap, success_cap * _GEM_LOOKAHEAD_MULTIPLIER),
    )
    to_attempt = all_gem_ids[:attempt_cap]
    log.info(
        "publisher: gem queue has %d; attempting up to %d to post %d this tick "
        "(slots_free=%d, max_per_tick=%d)",
        queue_depth, len(to_attempt), success_cap, slots_remaining, max_per_tick,
    )

    posted = 0
    attempted = 0
    for gem_id in to_attempt:
        if posted >= success_cap:
            break
        attempted += 1
        result = _post_gem(gem_id, db_path, farm_2026_repo, dry_run)
        if result.get("error"):
            if _looks_like_quota_error(result):
                log.warning(
                    "publisher: gem %s got quota-style error; stopping batch (%s)",
                    gem_id, result["error"],
                )
                return posted, True, queue_depth, attempted
            if _looks_like_permanent_gem_error(result):
                if dry_run:
                    log.error(
                        "publisher: gem %s has permanent Story failure; "
                        "dry-run would mark skip: %s",
                        gem_id, result["error"],
                    )
                else:
                    _mark_story_permanent_skip(db_path, gem_id, str(result["error"]))
                    log.error(
                        "publisher: gem %s has permanent Story failure; marked skip: %s",
                        gem_id, result["error"],
                    )
                continue
            log.error("publisher: gem %s post failed: %s", gem_id, result["error"])
            continue

        if dry_run:
            log.info("publisher: dry-run OK gem %s -> %s", gem_id, result.get("raw_url"))
            posted += 1
            continue

        log.info(
            "publisher: posted gem %s -> story_id=%s permalink=%s",
            gem_id, result.get("story_id"), result.get("permalink"),
        )
        ledger.append(
            ledger_path=ledger_path,
            lane="gem",
            identifier=str(gem_id),
            ig_media_id=result.get("story_id"),
            fb_post_id=result.get("fb_post_id"),
        )
        posted += 1

    return posted, False, queue_depth, attempted


# ---------------------------------------------------------------------------
# Archive fallback: single photo via on_this_day.post_daily
# ---------------------------------------------------------------------------


def _post_archive_one(dry_run: bool, ledger_path: Path) -> Optional[dict]:
    """Fire one auto-story cycle from the archive lane. Returns the
    cycle result dict or None if no candidate was posted. Recorded
    in the publish ledger on success."""
    from tools.on_this_day.post_daily import run_auto_story_cycle

    result = run_auto_story_cycle(dry_commit=dry_run)
    if not result.get("posted"):
        log.info("publisher: archive cycle did not post (%s)",
                 result.get("error") or "no_candidate/quota_exhausted")
        return result

    if not dry_run:
        # The cycle returns uuid (single-post lane) — pick whichever
        # field is populated for ledger identification.
        uuid = result.get("uuid") or (result.get("uuids") or [None])[0]
        ledger.append(
            ledger_path=ledger_path,
            lane="archive",
            identifier=str(uuid),
            ig_media_id=result.get("ig_post_id"),
            fb_post_id=result.get("fb_post_id"),
        )
    return result


# ---------------------------------------------------------------------------
# Top-level tick
# ---------------------------------------------------------------------------


def run_tick(dry_run: bool = False) -> dict:
    """One decision cycle. Returns a summary dict. Never raises."""
    cfg = _load_config()
    quota = int(cfg["ig_rolling_24h_quota"])
    reserve_floor = int(cfg["archive_reserve_floor"])
    archive_fallback_enabled = bool(cfg.get("archive_fallback_enabled", True))
    max_per_tick = int(cfg["max_per_tick"])
    ledger_path = REPO_ROOT / cfg["ledger_path"]
    prune_hours = int(cfg["ledger_prune_older_than_hours"])

    # Keep the ledger compact before we read it so the scan window
    # stays bounded.
    ledger.prune_older_than(ledger_path, hours=prune_hours)

    recent = ledger.count_last_24h(ledger_path, platform="ig")
    slots_free = quota - recent
    log.info(
        "publisher: rolling-24h publishes=%d / cap=%d -> slots_free=%d",
        recent, quota, slots_free,
    )

    summary = {
        "tick_started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "recent_24h_publishes": recent,
        "slots_free_at_start": slots_free,
        "dry_run": dry_run,
        "gems_posted": 0,
        "archive_posted": 0,
        "archive_fallback_enabled": archive_fallback_enabled,
        "quota_stopped": False,
    }

    if slots_free <= 0:
        log.info("publisher: no slots free this tick; skipping")
        return summary

    # Load shared paths from the Guardian config (same file the
    # existing scripts use, so ledger-less external changes like a
    # DB relocation stay a one-edit fix).
    guardian_cfg = _load_guardian_config()
    ig_cfg = guardian_cfg.get("instagram") or {}
    db_path = REPO_ROOT / guardian_cfg["guardian_db_path"]
    farm_2026 = Path(ig_cfg.get("farm_2026_repo_path", "")).expanduser()

    if not db_path.exists():
        log.error("publisher: guardian db missing: %s", db_path)
        summary["error"] = f"db missing: {db_path}"
        return summary
    if not farm_2026.exists():
        log.warning(
            "publisher: farm-2026 repo missing but Story gem lane now uses local "
            "Guardian hosting: %s",
            farm_2026,
        )

    # 1. Drain the gem queue (priority).
    gems_posted, quota_hit, gem_queue_depth, gem_attempts = _drain_gem_queue(
        db_path=db_path,
        farm_2026_repo=farm_2026,
        slots_remaining=slots_free,
        max_per_tick=max_per_tick,
        ledger_path=ledger_path,
        dry_run=dry_run,
    )
    summary["gems_posted"] = gems_posted
    summary["gem_queue_depth_at_start"] = gem_queue_depth
    summary["gem_attempts"] = gem_attempts
    slots_free -= gems_posted

    if quota_hit:
        summary["quota_stopped"] = True
        log.info("publisher: platform quota hit; tick done (gems=%d)", gems_posted)
        return summary

    # 2. Archive fallback: only if the reacted-gem queue was empty at
    # the start of this tick. Failed gem attempts do not make archive
    # eligible; Boss's reaction is the priority signal.
    if gem_queue_depth > 0:
        summary["archive_blocked_by_gem_queue"] = True
        if gems_posted > 0:
            log.info(
                "publisher: gem queue had %d item(s), posted %d; skipping archive fallback",
                gem_queue_depth, gems_posted,
            )
        else:
            log.warning(
                "publisher: gem queue had %d item(s) but none posted after %d attempt(s); "
                "blocking archive fallback",
                gem_queue_depth, gem_attempts,
            )
        return summary

    if not archive_fallback_enabled:
        summary["archive_fallback_disabled"] = True
        log.info("publisher: archive fallback disabled by config; skipping")
        return summary

    if slots_free < reserve_floor:
        log.info(
            "publisher: gem queue empty but only %d slot(s) free (< reserve floor %d); "
            "holding quota for future gems",
            slots_free, reserve_floor,
        )
        return summary

    archive_result = _post_archive_one(dry_run=dry_run, ledger_path=ledger_path)
    if archive_result and archive_result.get("posted"):
        summary["archive_posted"] = 1
    return summary


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Unified IG+FB story publisher (gem-priority, archive-fallback).",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Select + prep but skip the actual publishes and the ledger write.")
    return p.parse_args()


def main() -> int:
    _setup_logging()
    args = _parse_args()
    summary = run_tick(dry_run=args.dry_run)
    log.info("publisher: tick summary: %s", json.dumps(summary, default=str))

    # Append the tick summary to a rolling audit log for ops.
    audit_path = REPO_ROOT / "data" / "social" / f"publisher-{dt.date.today().isoformat()}.ndjson"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, default=str) + "\n")

    if summary.get("error"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
