# Author: Claude Opus 4.7 (1M context)
# Date: 20-April-2026
# PURPOSE: Select Instagram-post-eligible gems from image_archive on
#          wall-clock windows (day, 2-hour, week). Pure SELECT +
#          scoring + diversity filtering; no posting, no I/O beyond
#          SQLite reads.
#
#          Corresponds to the three scheduled posting lanes shipped
#          2026-04-20 (see docs/20-Apr-2026-ig-scheduled-posting-
#          architecture.md):
#            - Daily carousel at 18:00 local:
#                select_daily_carousel_gems(db_path, cfg)
#            - 2-hour story slot:
#                select_best_story_gem(db_path, cfg)
#            - Weekly reel Sunday 19:00:
#                select_weekly_reel_gems(db_path, cfg)
#
#          Each helper returns the id(s) the caller should post, or
#          an empty result when the window has nothing worth posting.
#          Scheduler scripts handle the "skip slot gracefully" path.
#
#          Diversity rule (carousel + reel): group by (camera_id,
#          time-bucket-minutes); pick highest-share gem per group;
#          order chronologically. Avoids posting N near-identical
#          shots from the same burst. Boss flagged this on
#          @pawel_and_pawleen post #2 — two identical shots was the
#          worst thing about that carousel.
#
#          Quality gate (2026-04-20): ALL helpers require
#          image_archive.discord_reactions >= 1. The reaction count
#          is populated by scripts/discord-reaction-sync.py which
#          cross-references Discord #farm-2026 messages (author ->
#          camera via gem_poster._USERNAME_BY_CAMERA, timestamp
#          match within ±60s) and writes the human-reactor count
#          back to the gem row. VLM tier/quality tags are not
#          sufficient by themselves — the Boss-approved quality
#          signal is "did a human react on Discord." No reactions,
#          no IG post.
#
# SRP/DRY check: Pass — single responsibility is "query the archive
#                for post candidates on a time window." No Graph API,
#                no git, no stitching (all in their own modules).

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("pipeline.ig_selection")


def _ensure_timezone(dt: Optional[datetime]) -> datetime:
    """Default to now (UTC) when caller passes None; normalize any
    naive datetime to UTC."""
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _bucket_key(ts_iso: str, bucket_minutes: int) -> str:
    """Quantize an ISO timestamp down to the nearest N-minute boundary.

    Used as the second half of a (camera_id, bucket) group key for
    diversity filtering. Timestamps are UTC ISO8601 — SQLite stores
    them with '+00:00' suffix; tolerate both 'Z' and offset forms.
    """
    clean = ts_iso.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(clean)
    except ValueError:
        # Fallback: strip fractional seconds or any trailing garbage
        dt = datetime.fromisoformat(clean.split(".")[0] + "+00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    minute_of_day = dt.hour * 60 + dt.minute
    bucket_idx = minute_of_day // bucket_minutes
    return f"{dt.date().isoformat()}-{bucket_idx:04d}"


def _score_gem(row: dict) -> tuple:
    """Rank key for "best gem in a set" — higher is better.

    Reaction count is the top-ranked signal: a gem with 2 human reactions
    always beats a gem with 1, regardless of VLM tags (humans saw both
    and liked one more). Within the same reaction count, VLM tier >
    quality > bird_count > most-recent timestamp as tiebreakers.
    """
    reactions = row.get("discord_reactions") or 0
    tier_rank = {"strong": 2, "decent": 1}.get(row.get("share_worth"), 0)
    quality_rank = {"sharp": 2, "soft": 1}.get(row.get("image_quality"), 0)
    bird_count = row.get("bird_count") or 0
    ts = row.get("ts") or ""
    return (reactions, tier_rank, quality_rank, bird_count, ts)


def select_daily_carousel_gems(
    db_path: Path,
    cfg: dict,
    today_utc_date: Optional[str] = None,
) -> list[int]:
    """Return up to N gem_ids for today's carousel (UTC day boundary).

    Criteria:
      - share_worth = 'strong' AND image_quality = 'sharp'
      - bird_count >= 1
      - has_concerns false
      - image_path populated (not a skip-tier row)
      - ig_permalink NULL (not already posted as feed/reel)
      - ts is today's UTC date (or `today_utc_date` override)

    Diversity filter: group by (camera_id, 15-min bucket); pick the
    highest-scoring gem per group. If that produces >N gems, keep
    the top N by score. Returned list is ordered chronologically so
    the carousel reads as a morning->evening narrative.

    cfg keys (with defaults if missing):
      daily_carousel_max_items (int, default 10)
      daily_carousel_min_items (int, default 2)
      daily_carousel_bucket_minutes (int, default 15)

    Returns [] when fewer than min_items candidates qualify (caller
    should skip the slot cleanly).
    """
    max_items = int(cfg.get("daily_carousel_max_items", 10))
    min_items = int(cfg.get("daily_carousel_min_items", 2))
    bucket_min = int(cfg.get("daily_carousel_bucket_minutes", 15))

    if today_utc_date is None:
        today_utc_date = datetime.now(timezone.utc).date().isoformat()

    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            """
            SELECT id, camera_id, ts, share_worth, image_quality,
                   bird_count, activity, discord_reactions
              FROM image_archive
             WHERE date(ts) = ?
               AND share_worth = 'strong'
               AND image_quality = 'sharp'
               AND bird_count >= 1
               AND (has_concerns = 0 OR has_concerns IS NULL)
               AND image_path IS NOT NULL
               AND ig_permalink IS NULL
               AND discord_reactions >= 1
             ORDER BY ts ASC
            """,
            (today_utc_date,),
        ).fetchall()

    if not rows:
        log.info("select_daily_carousel: no candidates for %s", today_utc_date)
        return []

    # Group by (camera_id, bucket) — one representative per bucket.
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        d = dict(r)
        key = (d["camera_id"], _bucket_key(d["ts"], bucket_min))
        groups.setdefault(key, []).append(d)

    representatives = [max(group, key=_score_gem) for group in groups.values()]

    # Cap at max_items by score (keep the strongest), then re-sort
    # chronologically so the carousel reads morning->evening.
    representatives.sort(key=_score_gem, reverse=True)
    representatives = representatives[:max_items]
    representatives.sort(key=lambda r: r["ts"])

    if len(representatives) < min_items:
        log.info(
            "select_daily_carousel: only %d candidates (min %d); skip slot",
            len(representatives), min_items,
        )
        return []

    ids = [r["id"] for r in representatives]
    log.info(
        "select_daily_carousel: picked %d gem_ids from %d raw candidates "
        "in %d buckets for %s: %s",
        len(ids), len(rows), len(groups), today_utc_date, ids,
    )
    return ids


def select_best_story_gem(
    db_path: Path,
    cfg: dict,
    now: Optional[datetime] = None,
) -> Optional[int]:
    """Return the single best gem_id from the last N minutes, or None
    if nothing in the window passes the story predicate.

    Criteria (matches should_post_story from ig_poster.py):
      - share_worth in ('strong', 'decent')
      - image_quality in ('sharp', 'soft')
      - bird_count >= 1
      - has_concerns false
      - image_path populated
      - ig_story_id NULL (not already posted as a story)
      - ts >= now - story_window_minutes

    Winner picked by _score_gem (strong+sharp+most birds+most recent).

    cfg keys:
      story_window_minutes (int, default 120)
    """
    window_m = int(cfg.get("story_window_minutes", 120))
    now = _ensure_timezone(now)
    cutoff_iso = (now - timedelta(minutes=window_m)).isoformat()

    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            """
            SELECT id, camera_id, ts, share_worth, image_quality, bird_count,
                   discord_reactions
              FROM image_archive
             WHERE ts >= ?
               AND share_worth IN ('strong', 'decent')
               AND image_quality IN ('sharp', 'soft')
               AND bird_count >= 1
               AND (has_concerns = 0 OR has_concerns IS NULL)
               AND image_path IS NOT NULL
               AND ig_story_id IS NULL
               AND discord_reactions >= 1
             ORDER BY ts DESC
            """,
            (cutoff_iso,),
        ).fetchall()

    if not rows:
        log.info(
            "select_best_story: no candidates in last %dm (cutoff=%s)",
            window_m, cutoff_iso,
        )
        return None

    winner = max((dict(r) for r in rows), key=_score_gem)
    log.info(
        "select_best_story: %d candidates in last %dm; winner gem_id=%s "
        "(tier=%s quality=%s birds=%s camera=%s)",
        len(rows), window_m, winner["id"], winner["share_worth"],
        winner["image_quality"], winner["bird_count"], winner["camera_id"],
    )
    return winner["id"]


def select_weekly_reel_gems(
    db_path: Path,
    cfg: dict,
    now: Optional[datetime] = None,
) -> list[int]:
    """Return gem_ids for a weekly best-of reel.

    Criteria:
      - share_worth = 'strong' AND image_quality = 'sharp'
      - bird_count >= 1
      - has_concerns false
      - image_path populated
      - ts >= now - weekly_reel_window_days

    Diversity: group by (camera_id, N-hour bucket); pick best per
    group; cap at max_frames; order chronologically.

    cfg keys:
      weekly_reel_window_days (int, default 7)
      weekly_reel_max_frames (int, default 8)
      weekly_reel_bucket_hours (int, default 6)

    Returns [] if fewer than 2 candidates (reel needs >=2 frames to
    be more than a video loop of one image).
    """
    window_d = int(cfg.get("weekly_reel_window_days", 7))
    max_frames = int(cfg.get("weekly_reel_max_frames", 8))
    bucket_h = int(cfg.get("weekly_reel_bucket_hours", 6))
    now = _ensure_timezone(now)
    cutoff_iso = (now - timedelta(days=window_d)).isoformat()

    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            """
            SELECT id, camera_id, ts, share_worth, image_quality, bird_count,
                   discord_reactions
              FROM image_archive
             WHERE ts >= ?
               AND share_worth = 'strong'
               AND image_quality = 'sharp'
               AND bird_count >= 1
               AND (has_concerns = 0 OR has_concerns IS NULL)
               AND image_path IS NOT NULL
               AND discord_reactions >= 1
             ORDER BY ts ASC
            """,
            (cutoff_iso,),
        ).fetchall()

    if not rows:
        log.info("select_weekly_reel: no candidates in last %dd", window_d)
        return []

    groups: dict[tuple[str, str], list[dict]] = {}
    bucket_min = bucket_h * 60
    for r in rows:
        d = dict(r)
        key = (d["camera_id"], _bucket_key(d["ts"], bucket_min))
        groups.setdefault(key, []).append(d)

    representatives = [max(group, key=_score_gem) for group in groups.values()]
    representatives.sort(key=_score_gem, reverse=True)
    representatives = representatives[:max_frames]
    representatives.sort(key=lambda r: r["ts"])

    if len(representatives) < 2:
        log.info(
            "select_weekly_reel: only %d candidates after diversity (need >=2)",
            len(representatives),
        )
        return []

    ids = [r["id"] for r in representatives]
    log.info(
        "select_weekly_reel: picked %d gem_ids from %d raw candidates "
        "in %d buckets (last %dd): %s",
        len(ids), len(rows), len(groups), window_d, ids,
    )
    return ids
