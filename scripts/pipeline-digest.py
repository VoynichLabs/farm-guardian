#!/usr/bin/env python3
# Author: GPT-5.5
# Date: 03-May-2026
# PURPOSE: Post a pipeline status digest to Discord at noon and 8pm.
#          Noon slot shows stories posted since midnight.
#          Evening slot shows stories posted since noon, plus reel status.
#          Story counts combine image_archive reacted-gem Story metadata
#          with the shared social ledger's archive fallback Story rows.
#          Both show queue depth, oldest queued gem, and IG quota used in
#          the rolling 24h Graph API window.
#          Queue depth excludes rows marked story-permanent-skip by the
#          publisher after local file/path failures.
#          Posts to DISCORD_WEBHOOK_URL as username "farm-pipeline" so it
#          is visually distinct from gem posts and can't trigger the
#          reaction-quality-gate cross-reference.
#
#          LaunchAgents:
#            com.farmguardian.pipeline-digest-noon.plist    — 12:00 local
#            com.farmguardian.pipeline-digest-evening.plist — 20:00 local
#
# SRP/DRY check: Pass — read-only queries + one Discord POST. No DB writes,
#                no IG API, no git. Reuses tools.social.ledger for quota.

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sqlite3
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def _load_env() -> None:
    from tools.pipeline.gem_poster import load_dotenv
    env_path = REPO_ROOT / "tools" / "pipeline" / "config.json"
    cfg = json.loads(env_path.read_text())
    ig_cfg = cfg.get("instagram", {})
    meta_env = ig_cfg.get("meta_env_file", "")
    if meta_env:
        load_dotenv(Path(meta_env).expanduser())
    load_dotenv(REPO_ROOT / ".env")


def _window_start_utc(slot: str) -> dt.datetime:
    """UTC datetime for the start of the window for this slot.

    noon    → today 00:00 local (midnight)
    evening → today 12:00 local (noon)
    """
    local_tz = dt.datetime.now().astimezone().tzinfo
    today = dt.date.today()
    hour = 0 if slot == "noon" else 12
    start_local = dt.datetime(today.year, today.month, today.day, hour, 0, 0,
                              tzinfo=local_tz)
    return start_local.astimezone(dt.timezone.utc)


def _parse_iso(ts: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _story_ledger_counts(ledger_path: Path, since_utc: dt.datetime) -> dict:
    """Return IG Story publish counts by social ledger lane."""
    counts = {"gem": 0, "archive": 0}
    if not ledger_path.exists():
        return counts
    with ledger_path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if entry.get("lane") not in counts or not entry.get("ig_media_id"):
                continue
            ts = _parse_iso(entry.get("ts", ""))
            if ts is None or ts < since_utc:
                continue
            counts[entry["lane"]] += 1
    return counts


def _stories_in_window(db_path: Path, ledger_path: Path, since_utc: dt.datetime) -> dict:
    """Return Story counts posted since since_utc.

    image_archive has the camera breakdown for reacted gem Stories.
    The social ledger is the shared source for archive fallback Story
    publishes, so combine them without double-counting gem rows.
    """
    cutoff = since_utc.isoformat()
    with sqlite3.connect(str(db_path)) as c:
        rows = c.execute(
            """
            SELECT camera_id, COUNT(*) as n
              FROM image_archive
             WHERE ig_story_posted_at >= ?
             GROUP BY camera_id
             ORDER BY n DESC
            """,
            (cutoff,),
        ).fetchall()
    db_reacted_total = sum(r[1] for r in rows)
    by_cam = {r[0]: r[1] for r in rows}
    ledger_counts = _story_ledger_counts(ledger_path, since_utc)
    reacted_total = max(db_reacted_total, ledger_counts["gem"])
    archive_total = ledger_counts["archive"]
    return {
        "total": reacted_total + archive_total,
        "reacted": reacted_total,
        "archive": archive_total,
        "by_cam": by_cam,
    }


def _queue_depth(db_path: Path) -> dict:
    """Return count and oldest timestamp of unposted reacted gems."""
    with sqlite3.connect(str(db_path)) as c:
        count = c.execute(
            """
            SELECT COUNT(*) FROM image_archive
             WHERE discord_reactions >= 1
               AND ig_story_id IS NULL
               AND share_worth IN ('strong','decent')
               AND image_quality IN ('sharp','soft')
               AND bird_count >= 1
               AND (has_concerns = 0 OR has_concerns IS NULL)
               AND image_path IS NOT NULL
               AND (
                   ig_story_skip_reason IS NULL
                   OR ig_story_skip_reason NOT LIKE 'story-permanent-skip:%'
               )
            """
        ).fetchone()[0]
        oldest_row = c.execute(
            """
            SELECT ts FROM image_archive
             WHERE discord_reactions >= 1
               AND ig_story_id IS NULL
               AND share_worth IN ('strong','decent')
               AND image_quality IN ('sharp','soft')
               AND bird_count >= 1
               AND (has_concerns = 0 OR has_concerns IS NULL)
               AND image_path IS NOT NULL
               AND (
                   ig_story_skip_reason IS NULL
                   OR ig_story_skip_reason NOT LIKE 'story-permanent-skip:%'
               )
             ORDER BY ts ASC LIMIT 1
            """
        ).fetchone()
    oldest = oldest_row[0][:10] if oldest_row else None
    return {"count": count, "oldest": oldest}


def _quota_used_rolling_24h(ledger_path: Path) -> int:
    """Count IG publishes in the rolling 24h window via the social ledger."""
    from tools.social import ledger
    return ledger.count_last_24h(ledger_path, platform="ig")


def _reel_status_today() -> str:
    """One-line reel status for today: posted / pending approval / quiet day."""
    today = dt.date.today().isoformat()
    posted_dir = REPO_ROOT / "data" / "reels" / "posted"
    pending_dir = REPO_ROOT / "data" / "reels" / "pending"
    expired_dir = REPO_ROOT / "data" / "reels" / "expired"

    if (posted_dir / f"{today}.json").exists():
        return "posted to IG ✅"
    if (pending_dir / f"{today}.json").exists():
        state = json.loads((pending_dir / f"{today}.json").read_text())
        n = len(state.get("gem_ids", []))
        return f"queued in Discord for approval 🎬  ({n} frames)"
    if (expired_dir / f"{today}.json").exists():
        return "expired — no reaction in Discord ⚠️"
    return "not built yet (runs at 6 pm)"


def _build_message(slot: str, stories: dict, queue: dict,
                   quota: int, reel: str) -> str:
    now_local = dt.datetime.now().astimezone()
    date_str = f"{now_local.strftime('%b')} {now_local.day}"
    slot_label = "noon" if slot == "noon" else "8 pm"
    window_label = "since midnight" if slot == "noon" else "since noon"

    lines = [f"📊 **Pipeline check-in — {slot_label}, {date_str}**", ""]

    # Stories
    n = stories["total"]
    if n == 0:
        lines.append(f"Stories posted {window_label}: none")
    else:
        lane_parts = []
        if stories.get("reacted"):
            lane_parts.append(f"reacted gems {stories['reacted']}")
        if stories.get("archive"):
            lane_parts.append(f"archive fallback {stories['archive']}")
        cam_parts = "  ·  ".join(
            f"{cam} {count}" for cam, count in stories["by_cam"].items()
        )
        lines.append(f"Stories posted {window_label}: **{n}**")
        if lane_parts:
            lines.append("  " + "  ·  ".join(lane_parts))
        if cam_parts:
            lines.append(f"  {cam_parts}")

    lines.append("")

    # Queue
    q = queue["count"]
    oldest = queue["oldest"] or "—"
    lines.append(f"Reaction queue: **{q}** gems waiting  ·  oldest {oldest}")

    # Quota
    lines.append(f"IG quota used (rolling 24h): **{quota} / 25**")

    # Reel (evening only — it hasn't run yet at noon)
    if slot == "evening":
        lines.append(f"Reel: {reel}")

    return "\n".join(lines)


def _post_to_discord(webhook_url: str, message: str) -> bool:
    payload = {"username": "farm-pipeline", "content": message}
    try:
        r = requests.post(webhook_url, json=payload, timeout=15)
        if 200 <= r.status_code < 300:
            return True
        logging.warning("discord: POST returned %d — %s", r.status_code, r.text[:200])
        return False
    except requests.RequestException as e:
        logging.warning("discord: POST failed: %s", e)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post pipeline digest to Discord.")
    parser.add_argument("--slot", choices=["noon", "evening"], required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print message to stdout; don't post to Discord.")
    args = parser.parse_args(argv)

    _setup_logging()
    _load_env()

    import os
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url and not args.dry_run:
        logging.error("DISCORD_WEBHOOK_URL not set")
        return 3

    pipe_cfg = json.loads((REPO_ROOT / "tools" / "pipeline" / "config.json").read_text())
    db_path = REPO_ROOT / pipe_cfg["guardian_db_path"]
    if not db_path.exists():
        logging.error("guardian db not found: %s", db_path)
        return 1

    social_cfg = json.loads(
        (REPO_ROOT / "tools" / "social" / "config.json").read_text()
    )
    ledger_path = REPO_ROOT / social_cfg["ledger_path"]

    since_utc = _window_start_utc(args.slot)
    stories = _stories_in_window(db_path, ledger_path, since_utc)
    queue = _queue_depth(db_path)
    quota = _quota_used_rolling_24h(ledger_path)
    reel = _reel_status_today() if args.slot == "evening" else ""

    message = _build_message(args.slot, stories, queue, quota, reel)

    if args.dry_run:
        print(message)
        return 0

    ok = _post_to_discord(webhook_url, message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
