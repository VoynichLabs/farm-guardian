#!/usr/bin/env python3
# Author: Claude Sonnet 4.6; Claude Opus 5.5 Medium (29-Oct-2026 — Instagram key health check for the farm and VoynichLabs accounts, pings Mark when a key dies or the farm goes quiet 36h, v2.79.0)
# Date: 06-May-2026 (updated 29-Oct-2026)
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
#          Each run also does two read-only Instagram key checks (farm Page
#          key via graph.facebook.com, VoynichLabs Instagram-login key via
#          graph.instagram.com) plus a staleness check on each daily time-lapse
#          lane, and leads with a Mark mention if anything fails.
#
# SRP/DRY check: Pass — read-only queries + one Discord POST. No DB writes,
#                no publishing, no git. Reuses tools.social.ledger for quota.

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
    """One line: when each daily time-lapse reel last really posted.

    Rewritten 29-Oct-2026. The old version only looked at the legacy mixed-reel
    folders (data/reels/posted etc.); that lane was retired 13-Aug, so from
    14-Aug this line read "not built yet" every night whatever the camera reels
    did, and their 11-week outage looked like a quiet day. It now reads each
    live lane's own posted/ folder.
    """
    now = dt.datetime.now().timestamp()
    parts = []
    for label, folder in DAILY_REEL_LANES.items():
        markers = list((REPO_ROOT / "data" / "reels" / folder / "posted").glob("*.json"))
        if not markers:
            parts.append(f"{label} never ⚠️")
            continue
        age_h = (now - max(m.stat().st_mtime for m in markers)) / 3600
        mark = "✅" if age_h <= STALE_POST_HOURS else "⚠️"
        parts.append(f"{label} {age_h:.0f}h ago {mark}")
    return "last posted: " + "  ·  ".join(parts)


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
        if quota >= 20:
            lines.append(
                f"Stories posted {window_label}: **none** "
                f"(quota {quota}/25 from prior window — publisher resumes when slots free)"
            )
        else:
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

    # Daily time-lapse reels (evening only; the 21:00/21:30 lanes run after 8 pm,
    # so the noon slot would only repeat yesterday).
    if slot == "evening":
        lines.append(f"Time-lapse reels {reel}")

    return "\n".join(lines)


# Instagram keys checked on every digest. Added 29-Oct-2026 after the farm key
# was silently invalidated by Facebook on 8-Oct and nothing posted for three
# weeks while every lane only logged a 400 to /tmp. The farm key comes from the
# meta env file _load_env() already sourced; the VoynichLabs key (Instagram
# login, no Facebook Page) lives in its own file and is read directly.
VOYNICHLABS_ENV = Path("/Users/macmini/bubba-workspace/secrets/ai-literacy-meta.env")
STALE_POST_HOURS = 36


def _read_env_file(path: Path) -> dict:
    if not path.exists():
        return {}
    return {
        line.split("=", 1)[0]: line.split("=", 1)[1].strip()
        for line in path.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    }


def _scrub(text: str) -> str:
    # requests puts the full URL, access_token included, into connection-error
    # messages. These lines go to Discord, so blank any token before use.
    import re

    return re.sub(r"access_token=[^&\s'\")]+", "access_token=<hidden>", str(text))[:200]


def _graph_error(resp: requests.Response) -> str:
    try:
        return _scrub(resp.json().get("error", {}).get("message", "")[:160]) or f"http {resp.status_code}"
    except ValueError:
        return f"http {resp.status_code}"


def _credential_alerts() -> list[str]:
    """Return one line per broken Instagram account; empty when all is well.

    Never raises: a network hiccup is reported as a line, not a crash, so the
    rest of the digest still goes out.
    """
    import os

    alerts: list[str] = []
    farm_token = os.environ.get("LONG_LIVED_PAGE_TOKEN", "")
    farm_ig = os.environ.get("IG_BUSINESS_ACCOUNT_ID", "")
    try:
        resp = requests.get(
            f"https://graph.facebook.com/v25.0/{farm_ig}/media",
            params={"fields": "timestamp", "limit": 1, "access_token": farm_token},
            timeout=20,
        )
        if resp.status_code != 200:
            alerts.append(f"Farm Instagram key is not working: {_graph_error(resp)}")
        else:
            data = resp.json().get("data") or []
            newest = _parse_iso(data[0]["timestamp"].replace("+0000", "+00:00")) if data else None
            if newest is not None:
                age_h = (dt.datetime.now(dt.timezone.utc) - newest).total_seconds() / 3600
                if age_h > STALE_POST_HOURS:
                    alerts.append(f"Nothing has posted to the farm Instagram for {age_h / 24:.1f} days")
    except requests.RequestException as exc:
        alerts.append(f"Could not reach Instagram to check the farm key: {_scrub(exc)}")

    voyn = _read_env_file(VOYNICHLABS_ENV)
    if voyn.get("IG_ACCESS_TOKEN"):
        try:
            resp = requests.get(
                f"{voyn.get('IG_API_BASE', 'https://graph.instagram.com/v25.0')}/me",
                params={"fields": "username", "access_token": voyn["IG_ACCESS_TOKEN"]},
                timeout=20,
            )
            if resp.status_code != 200:
                alerts.append(f"VoynichLabs Instagram key is not working: {_graph_error(resp)}")
        except requests.RequestException as exc:
            alerts.append(f"Could not reach Instagram to check the VoynichLabs key: {_scrub(exc)}")
    alerts.extend(_stale_reel_lane_alerts())
    return alerts


# Daily time-lapse lanes and their posted-state folders under data/reels/.
# Each lane writes posted/<key>.json only after Instagram accepted the reel, so
# the newest marker's age is "time since this camera last really posted". The
# 9-Aug -> 29-Oct outage (duo2, house-yard, dashcam built a reel every day and
# crashed before publishing) would have been flagged here within two days.
DAILY_REEL_LANES = {
    "S7 nest-box": "s7",
    "Duo2": "duo2-timelapse",
    "Guardian yard camera": "house-yard-cam-timelapse",
    "Dashcam": "jieli-dashcam-timelapse",
}


def _stale_reel_lane_alerts() -> list[str]:
    alerts: list[str] = []
    now = dt.datetime.now().timestamp()
    for label, folder in DAILY_REEL_LANES.items():
        markers = list((REPO_ROOT / "data" / "reels" / folder / "posted").glob("*.json"))
        if not markers:
            alerts.append(f"{label} daily time-lapse has never recorded a post")
            continue
        age_h = (now - max(m.stat().st_mtime for m in markers)) / 3600
        if age_h > STALE_POST_HOURS:
            alerts.append(f"{label} daily time-lapse has not posted for {age_h / 24:.1f} days")
    return alerts


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
    alerts = _credential_alerts()
    if alerts:
        # Lead with the problem and ping Mark: buried in the digest body, the
        # 8-Oct outage would have read like an ordinary quiet day.
        from tools.pipeline.daily_reel_runner import MARK_DISCORD_USER_ID
        warning = "\n".join(f"🚨 {line}" for line in alerts)
        message = f"<@{MARK_DISCORD_USER_ID}> **Instagram posting problem**\n{warning}\n\n{message}"

    if args.dry_run:
        print(message)
        return 0

    ok = _post_to_discord(webhook_url, message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
