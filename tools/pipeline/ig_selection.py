# Author: Claude Sonnet 4.6
# Date: 07-May-2026; 09-May-2026 — _score_raw_frame + select_timelapse_gems for vlm_bypass lanes; 10-May-2026 — daylight filter for coop-roof time-lapse lanes; 11-May-2026 — S7 backlog duplicate guard
# PURPOSE: Select Instagram-post-eligible gems from image_archive on
#          wall-clock windows (day, 2-hour, week). Pure SELECT +
#          scoring + diversity filtering; no posting, no I/O beyond
#          SQLite reads.
#
#          Corresponds to the five scheduled posting lanes:
#            - Daily carousel at 18:00 local:
#                select_daily_carousel_gems(db_path, cfg)
#            - 2-hour story slot:
#                select_best_story_gem(db_path, cfg)
#            - Daily reel at 18:00 (Discord-approved before IG post):
#                select_daily_reel_gems(db_path, cfg)
#            - S7 daily time-lapse reel at 21:00:
#                select_s7_daily_reel_gems(db_path, cfg)
#            - S7 backlog reel at 12:00 (one past date per day, oldest first):
#                select_s7_backlog_reel_gems(db_path, date_str, cfg)
#            - Weekly reel Sunday 19:00 (retired 29-Apr-2026):
#                select_weekly_reel_gems(db_path, cfg)
#            - Per-camera time-lapse reels (mba-cam/gwtc/usb-cam/dominator-cam):
#                select_timelapse_gems(camera_id, db_path, cfg)
#                select_mba_cam_timelapse_gems(db_path, cfg)  ← wrapper
#                select_gwtc_timelapse_gems(db_path, cfg)     ← wrapper
#                select_usb_cam_timelapse_gems(db_path, cfg)  ← wrapper
#                select_dominator_cam_timelapse_gems(db_path, cfg) ← wrapper
#
#          Each helper returns the id(s) the caller should post, or
#          an empty result when the window has nothing worth posting.
#          Scheduler scripts handle the "skip slot gracefully" path.
#
#          Story queue permanent skips (2026-05-03): social-publisher
#          may mark local file/path failures as
#          ig_story_skip_reason='story-permanent-skip:...'. Story
#          selectors exclude those rows so dead old queue items do not
#          block later reacted gems forever. Transient publish failures
#          are not marked and remain eligible for retry.
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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


def _score_raw_frame(row: dict) -> tuple:
    """Rank key for raw-tier frames (vlm_bypass cameras) — higher is better.

    VLM fields are all NULL for raw-tier rows. Laplacian variance is the
    sharpness proxy captured at store_raw time; timestamp breaks ties so
    diversity within a time bucket favors the sharpest capture.
    """
    laplacian = float(row.get("laplacian_var") or 0.0)
    ts = row.get("ts") or ""
    return (laplacian, ts)


def _parse_archive_ts(ts_iso: str) -> datetime:
    """Parse archive timestamps into timezone-aware datetimes.

    SQLite rows are written as UTC ISO strings with a '+00:00' suffix;
    tolerate a trailing 'Z' and naive strings so tests/old rows do not
    explode the selector.
    """
    clean = (ts_iso or "").replace("Z", "+00:00")
    dt = datetime.fromisoformat(clean)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _is_local_hour_in_window(
    ts_iso: str,
    *,
    start_hour: int,
    end_hour: int,
    timezone_name: str,
) -> bool:
    """Return true when ts falls inside the configured local hour window.

    The end hour is exclusive: start=6,end=20 accepts 06:00:00 through
    19:59:59 local. Windows that wrap midnight are supported for future
    callers, though the current GWTC use is daylight-only.
    """
    local_dt = _parse_archive_ts(ts_iso).astimezone(ZoneInfo(timezone_name))
    hour = local_dt.hour
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def _timelapse_daylight_only_enabled(camera_id: str, cfg: dict) -> bool:
    """Whether a raw time-lapse lane should discard non-daylight frames."""
    cameras = cfg.get("timelapse_reel_daylight_only_cameras", ["gwtc"])
    if isinstance(cameras, str):
        cameras = [item.strip() for item in cameras.split(",") if item.strip()]
    return camera_id in set(cameras or [])


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
      - ig_story_skip_reason not marked story-permanent-skip
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
               AND (
                   ig_story_skip_reason IS NULL
                   OR ig_story_skip_reason NOT LIKE 'story-permanent-skip:%'
               )
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


def select_all_unposted_story_gems(
    db_path: Path,
    cfg: dict,
    now: Optional[datetime] = None,
) -> list[int]:
    """Return EVERY gem_id that has a Discord reaction and has not yet
    been posted as a Story — ordered oldest-first so the caller posts
    them FIFO.

    This is the backstop against gem loss. Boss's directive (2026-04-23):
    "Anything that gets a reaction in that channel is worthy of posting on
    Instagram and Facebook. There's no limit to the number of stories we
    can post." The earlier select_best_story_gem picked one winner per
    window and silently dropped every other reacted gem that didn't score
    highest — that's the behaviour being replaced.

    Criteria (same predicate as select_best_story_gem MINUS the time
    window):
      - share_worth in ('strong', 'decent')
      - image_quality in ('sharp', 'soft')
      - bird_count >= 1
      - has_concerns false
      - image_path populated
      - ig_story_id NULL  (not already posted as a story)
      - ig_story_skip_reason not marked story-permanent-skip
      - discord_reactions >= 1  (Boss gave it a thumbs-up)

    No ts cutoff: a gem that got reacted 4 days ago but wasn't posted
    (agent was down, IG API hiccuped, etc.) will still be picked up on
    the next tick. That's the whole point of the backstop.

    Returns [] when every reacted gem has already been posted.
    """
    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            """
            SELECT id, camera_id, ts, share_worth, image_quality, bird_count,
                   discord_reactions
              FROM image_archive
             WHERE share_worth IN ('strong', 'decent')
               AND image_quality IN ('sharp', 'soft')
               AND bird_count >= 1
               AND (has_concerns = 0 OR has_concerns IS NULL)
               AND image_path IS NOT NULL
               AND ig_story_id IS NULL
               AND (
                   ig_story_skip_reason IS NULL
                   OR ig_story_skip_reason NOT LIKE 'story-permanent-skip:%'
               )
               AND discord_reactions >= 1
             ORDER BY ts ASC
            """,
        ).fetchall()

    gem_ids = [row["id"] for row in rows]
    log.info(
        "select_all_unposted_stories: %d reacted gem(s) awaiting story publish",
        len(gem_ids),
    )
    return gem_ids


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


def select_daily_reel_gems(
    db_path: Path,
    cfg: dict,
    now: Optional[datetime] = None,
) -> list[int]:
    """Return ALL reacted gem_ids from the past 24h for the daily reel,
    oldest-first. No diversity bucketing — every reacted gem comes through.

    Criteria:
      - discord_reactions >= 1
      - has_concerns false
      - image_path populated
      - ts >= now - daily_reel_window_hours

    Capped at daily_reel_max_frames (default 90) to stay within Instagram's
    90s reel limit (90 frames × 1s/frame − 89 × 0.15s xfade ≈ 77s).

    cfg keys (all under instagram.scheduled):
      daily_reel_window_hours (int, default 24)
      daily_reel_max_frames   (int, default 90)
      daily_reel_min_frames   (int, default 6)

    Returns [] if fewer than daily_reel_min_frames candidates — quiet day.
    """
    window_h = int(cfg.get("daily_reel_window_hours", 24))
    max_frames = int(cfg.get("daily_reel_max_frames", 90))
    min_frames = int(cfg.get("daily_reel_min_frames", 6))
    now = _ensure_timezone(now)
    cutoff_iso = (now - timedelta(hours=window_h)).isoformat()

    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            """
            SELECT id, camera_id, ts, share_worth, image_quality, bird_count,
                   discord_reactions
              FROM image_archive
             WHERE ts >= ?
               AND (has_concerns = 0 OR has_concerns IS NULL)
               AND image_path IS NOT NULL
               AND discord_reactions >= 1
             ORDER BY ts ASC
             LIMIT ?
            """,
            (cutoff_iso, max_frames),
        ).fetchall()

    if len(rows) < min_frames:
        log.info(
            "select_daily_reel: only %d reacted gems in last %dh (need >=%d); quiet day",
            len(rows), window_h, min_frames,
        )
        return []

    ids = [row["id"] for row in rows]
    log.info(
        "select_daily_reel: %d gem_ids in last %dh (cap=%d)",
        len(ids), window_h, max_frames,
    )
    return ids


def select_s7_daily_reel_gems(
    db_path: Path,
    cfg: dict,
    now: Optional[datetime] = None,
) -> list[int]:
    """Return representative S7 frames for the daily time-lapse Reel.

    This lane is intentionally different from the mixed daily Reel:
    source frames do not require individual Discord reactions by default.
    The value is the fixed-angle S7 time-lapse effect, so selection favors
    broad time coverage across the last 24h rather than only reacted gems.

    Criteria:
      - camera_id = 's7-cam'
      - image_quality = 'sharp'
      - bird_count >= 1
      - has_concerns false
      - image_path populated
      - ts >= now - s7_daily_reel_window_hours
      - optional discord_reactions >= 1 when
        s7_daily_reel_require_source_reactions is true

    Diversity: group by N-minute buckets, pick the highest-scoring frame
    per bucket, cap at max frames, return oldest-first.

    cfg keys:
      s7_daily_reel_window_hours (int, default 24)
      s7_daily_reel_bucket_minutes (int, default 15)
      s7_daily_reel_max_frames (int, default 90)
      s7_daily_reel_min_frames (int, default 12)
      s7_daily_reel_require_source_reactions (bool, default false)
    """
    window_h = int(cfg.get("s7_daily_reel_window_hours", 24))
    bucket_min = max(1, int(cfg.get("s7_daily_reel_bucket_minutes", 15)))
    max_frames = int(cfg.get("s7_daily_reel_max_frames", 90))
    min_frames = int(cfg.get("s7_daily_reel_min_frames", 12))
    require_reactions = bool(cfg.get("s7_daily_reel_require_source_reactions", False))
    now = _ensure_timezone(now)
    cutoff_iso = (now - timedelta(hours=window_h)).isoformat()

    reaction_clause = "AND discord_reactions >= 1" if require_reactions else ""
    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            f"""
            SELECT id, camera_id, ts, share_worth, image_quality, bird_count,
                   discord_reactions
              FROM image_archive
             WHERE ts >= ?
               AND camera_id = 's7-cam'
               AND image_quality = 'sharp'
               AND bird_count >= 1
               AND (has_concerns = 0 OR has_concerns IS NULL)
               AND image_path IS NOT NULL
               {reaction_clause}
             ORDER BY ts ASC
            """,
            (cutoff_iso,),
        ).fetchall()

    if not rows:
        log.info("select_s7_daily_reel: no candidates in last %dh", window_h)
        return []

    groups: dict[str, list[dict]] = {}
    for row in rows:
        item = dict(row)
        groups.setdefault(_bucket_key(item["ts"], bucket_min), []).append(item)

    representatives = [max(group, key=_score_gem) for group in groups.values()]
    representatives.sort(key=_score_gem, reverse=True)
    representatives = representatives[:max_frames]
    representatives.sort(key=lambda item: item["ts"])

    if len(representatives) < min_frames:
        log.info(
            "select_s7_daily_reel: only %d bucketed S7 frames in last %dh "
            "(need >=%d); quiet day",
            len(representatives),
            window_h,
            min_frames,
        )
        return []

    ids = [row["id"] for row in representatives]
    log.info(
        "select_s7_daily_reel: picked %d frames from %d raw S7 candidates "
        "in %d buckets (last %dh, reactions_required=%s)",
        len(ids),
        len(rows),
        len(groups),
        window_h,
        require_reactions,
    )
    return ids


def select_s7_backlog_reel_gems(
    db_path: Path,
    cfg: dict,
) -> list[int]:
    """Return the next batch of s7-cam reacted portrait gems for the backlog drain.

    Pool-based (no date scope): pulls the oldest max_frames chronologically from
    all unposted, Discord-reacted, portrait-orientation s7-cam gems.  Discord
    reactions are the quality gate — Boss already voted on these frames.  VLM
    share_worth is intentionally NOT used here because it is unreliable as a
    ranking signal.

    Criteria:
      - camera_id = 's7-cam'
      - width=1080, height=1920  (portrait frames only; pre-switch landscape excluded)
      - discord_reactions >= 1   (Boss approved in Discord — the real quality gate)
      - has_concerns false
      - image_path populated
      - not already marked story-permanent-skip or used-in-backlog-reel

    Ordering: chronological ASC (oldest first) so the reel has a time-lapse arc.

    cfg keys (all under instagram.scheduled):
      s7_backlog_reel_max_frames (int, default 25)
      s7_backlog_reel_min_frames (int, default 20)
    """
    max_frames = int(cfg.get("s7_backlog_reel_max_frames", 25))
    min_frames = int(cfg.get("s7_backlog_reel_min_frames", 20))

    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            """
            SELECT id, ts
              FROM image_archive
             WHERE camera_id = 's7-cam'
               AND width = 1080 AND height = 1920
               AND discord_reactions >= 1
               AND (has_concerns = 0 OR has_concerns IS NULL)
               AND image_path IS NOT NULL
               AND (ig_story_skip_reason IS NULL
                    OR (ig_story_skip_reason != 'used-in-backlog-reel'
                        AND ig_story_skip_reason NOT LIKE 'story-permanent-skip:%'
                        AND ig_story_skip_reason NOT LIKE 'used-in-backlog-reel:%'))
             ORDER BY ts ASC
             LIMIT ?
            """,
            (max_frames,),
        ).fetchall()

    if len(rows) < min_frames:
        log.info(
            "select_s7_backlog_reel: only %d eligible portrait gems in pool (need >=%d); backlog empty or exhausted",
            len(rows),
            min_frames,
        )
        return []

    ids = [r["id"] for r in rows]
    log.info("select_s7_backlog_reel: picked %d portrait gems (oldest %s)", len(ids), rows[0]["ts"][:10])
    return ids


def select_timelapse_gems(
    camera_id: str,
    db_path: Path,
    cfg: dict,
    now: Optional[datetime] = None,
) -> list[int]:
    """Return representative raw-tier frames for a time-lapse Reel.

    vlm_bypass cameras (mba-cam, gwtc, usb-cam, dominator-cam) write
    image_tier='raw' rows with no VLM enrichment — discord_reactions,
    share_worth, image_quality, and bird_count are all NULL. Selection
    uses laplacian_var (sharpness proxy written by store_raw) instead
    of _score_gem.

    No Discord-reaction gate: these time-lapse reels are auto-posted
    without a human approval step. The value is breadth-of-day coverage
    showing the camera's view evolving over time.

    Criteria:
      - camera_id matches the specified camera
      - image_tier = 'raw'
      - image_path IS NOT NULL (not yet deleted by sweep_raw)
      - ts >= now - timelapse_reel_window_hours
      - optional local daylight window for configured outdoor/privacy lanes

    Diversity: group by N-minute time buckets; pick the frame with the
    highest laplacian_var per bucket; cap at max_frames; return oldest-
    first so the reel reads as a chronological time-lapse.

    cfg keys (all under instagram.scheduled):
      timelapse_reel_window_hours   (int, default 24)
      timelapse_reel_bucket_minutes (int, default 5)
      timelapse_reel_max_frames     (int, default 60)
      timelapse_reel_min_frames     (int, default 6)
      timelapse_reel_daylight_only_cameras (list[str], default ["gwtc"])
      timelapse_reel_daylight_start_hour   (int, default 6, inclusive local hour)
      timelapse_reel_daylight_end_hour     (int, default 20, exclusive local hour)
      timelapse_reel_timezone              (str, default "America/New_York")

    Returns [] if fewer than timelapse_reel_min_frames candidates qualify.
    """
    window_h = int(cfg.get("timelapse_reel_window_hours", 24))
    bucket_min = max(1, int(cfg.get("timelapse_reel_bucket_minutes", 5)))
    max_frames = int(cfg.get("timelapse_reel_max_frames", 60))
    min_frames = int(cfg.get("timelapse_reel_min_frames", 6))
    daylight_only = _timelapse_daylight_only_enabled(camera_id, cfg)
    daylight_start = int(cfg.get("timelapse_reel_daylight_start_hour", 6))
    daylight_end = int(cfg.get("timelapse_reel_daylight_end_hour", 20))
    daylight_tz = str(cfg.get("timelapse_reel_timezone", "America/New_York"))
    now = _ensure_timezone(now)
    cutoff_iso = (now - timedelta(hours=window_h)).isoformat()

    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            """
            SELECT id, camera_id, ts, laplacian_var
              FROM image_archive
             WHERE camera_id = ?
               AND image_tier = 'raw'
               AND image_path IS NOT NULL
               AND ts >= ?
             ORDER BY ts ASC
            """,
            (camera_id, cutoff_iso),
        ).fetchall()

    if not rows:
        log.info(
            "select_timelapse: no raw frames for %s in last %dh",
            camera_id, window_h,
        )
        return []

    if daylight_only:
        raw_count = len(rows)
        try:
            rows = [
                row for row in rows
                if _is_local_hour_in_window(
                    row["ts"],
                    start_hour=daylight_start,
                    end_hour=daylight_end,
                    timezone_name=daylight_tz,
                )
            ]
        except (ValueError, ZoneInfoNotFoundError) as exc:
            log.warning(
                "select_timelapse: invalid daylight window config for %s "
                "(start=%s end=%s tz=%s): %s; using unfiltered rows",
                camera_id, daylight_start, daylight_end, daylight_tz, exc,
            )
        else:
            log.info(
                "select_timelapse: daylight filter kept %d/%d raw frames for %s "
                "(%02d:00-%02d:00 %s)",
                len(rows), raw_count, camera_id,
                daylight_start, daylight_end, daylight_tz,
            )
            if not rows:
                return []

    groups: dict[str, list[dict]] = {}
    for row in rows:
        item = dict(row)
        groups.setdefault(_bucket_key(item["ts"], bucket_min), []).append(item)

    representatives = [max(group, key=_score_raw_frame) for group in groups.values()]
    # Cap at max_frames by taking every Nth representative to preserve
    # temporal spread (rather than picking top-N by sharpness, which would
    # cluster at the best-lit part of the day).
    if len(representatives) > max_frames:
        step = len(representatives) / max_frames
        representatives = [representatives[int(i * step)] for i in range(max_frames)]

    if len(representatives) < min_frames:
        log.info(
            "select_timelapse: only %d bucketed frames for %s in last %dh "
            "(need >=%d); not enough for a reel",
            len(representatives), camera_id, window_h, min_frames,
        )
        return []

    # Sort chronologically — already ASC from SQL, but after bucketing
    # representatives may be reordered by score; re-sort here.
    representatives.sort(key=lambda r: r["ts"])
    ids = [r["id"] for r in representatives]
    log.info(
        "select_timelapse: picked %d/%d bucketed frames for %s "
        "(last %dh, %d-min buckets)",
        len(ids), len(rows), camera_id, window_h, bucket_min,
    )
    return ids


def select_mba_cam_timelapse_gems(db_path: Path, cfg: dict) -> list[int]:
    return select_timelapse_gems("mba-cam", db_path, cfg)


def select_gwtc_timelapse_gems(db_path: Path, cfg: dict) -> list[int]:
    return select_timelapse_gems("gwtc", db_path, cfg)


def select_usb_cam_timelapse_gems(db_path: Path, cfg: dict) -> list[int]:
    return select_timelapse_gems("usb-cam", db_path, cfg)


def select_dominator_cam_timelapse_gems(db_path: Path, cfg: dict) -> list[int]:
    return select_timelapse_gems("dominator-cam", db_path, cfg)


def select_house_yard_cam_timelapse_gems(db_path: Path, cfg: dict) -> list[int]:
    return select_timelapse_gems("house-yard", db_path, cfg)


def mark_gems_used_in_backlog_reel(
    db_path: Path,
    gem_ids: list[int],
) -> None:
    """Mark gems as consumed by a backlog Reel so they leave the story queue.

    Sets ig_story_skip_reason = 'used-in-backlog-reel' on each gem.
    select_all_unposted_story_gems and select_s7_backlog_reel_gems both
    exclude these rows so the same gem never gets posted as a story or
    re-selected for another backlog reel.
    """
    if not gem_ids:
        return
    reason = "used-in-backlog-reel"
    placeholders = ",".join("?" * len(gem_ids))
    with sqlite3.connect(str(db_path)) as c:
        c.execute(
            f"UPDATE image_archive SET ig_story_skip_reason = ? WHERE id IN ({placeholders})",
            [reason, *gem_ids],
        )
    log.info(
        "mark_gems_used_in_backlog_reel: marked %d gems as %s",
        len(gem_ids),
        reason,
    )
