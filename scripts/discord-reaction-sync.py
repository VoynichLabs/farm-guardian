#!/usr/bin/env python3
# Author: Claude Opus 4.7 (1M context)
# Date: 20-April-2026
# PURPOSE: Scrape #farm-2026 Discord channel for human reactions on
#          gem posts, match each reacted message back to the
#          Guardian image_archive row that produced it (by camera +
#          timestamp within ±60s), write the reaction count back to
#          image_archive.discord_reactions. This is the quality
#          signal the IG selection helpers gate on — only reacted
#          gems make it to posts / stories / reels.
#
#          Bot reactions (Larry, Bubba, Egon — other Claude instances)
#          are excluded; only real-human reactions count.
#
#          Reuses the Discord API plumbing + BOT_USER_IDS from
#          tools/discord_harvester.py so we don't fork two Discord
#          clients.
#
# SRP/DRY check: Pass — single responsibility is "Discord reactions ->
#                image_archive.discord_reactions". Reuses channel id,
#                bot exclusion list, and paginated message fetch from
#                discord_harvester; reuses gem_poster's camera ->
#                username map for reverse lookup.

"""
discord-reaction-sync.py — pull human reaction counts from
#farm-2026 Discord into image_archive.

Invocation:
  LaunchAgent cadence: every 30 minutes
  (deploy/ig-scheduled/com.farmguardian.discord-reaction-sync.plist).

  Manual (testing):
    venv/bin/python scripts/discord-reaction-sync.py [--since-hours N]
    venv/bin/python scripts/discord-reaction-sync.py --backfill

Exit codes:
  0 — synced OK (possibly 0 updates)
  1 — runtime failure (Discord auth, API error, DB write)
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


# Map gem_poster webhook username -> camera_id. Covers the cameras that
# gem_poster's _USERNAME_BY_CAMERA renames; other cameras fall through
# to their raw camera name.
_KNOWN_CAMERAS = {"s7-cam", "house-yard", "mba-cam", "usb-cam", "gwtc", "iphone-cam"}


def _camera_for_username(username: str) -> Optional[str]:
    """Reverse gem_poster._USERNAME_BY_CAMERA; fall back to raw
    camera name for cameras not in the friendly-name map
    (iphone-cam is the current example).

    Returns None for messages whose author isn't one of Guardian's
    webhook identities — those are iPhone drops / manual shares and
    aren't in image_archive at all.
    """
    from tools.pipeline.gem_poster import _USERNAME_BY_CAMERA
    reverse = {v: k for k, v in _USERNAME_BY_CAMERA.items()}
    if username in reverse:
        return reverse[username]
    if username in _KNOWN_CAMERAS:
        return username
    return None


def _load_discord_client():
    """Pull the harvester's Discord API helpers. It already implements
    token loading, pagination, and the right rate-limit pacing."""
    from tools import discord_harvester as dh
    return dh


def _count_human_reactions(msg: dict, token: str, dh) -> int:
    """Sum unique non-bot human reactors across every emoji on the
    message. Bot user IDs + user.bot=True are excluded. Returns 0 if
    the message has no reactions OR only bot reactions.
    """
    reactions = msg.get("reactions") or []
    if not reactions:
        return 0
    import requests
    headers = dh.discord_headers(token)
    humans: set[str] = set()
    for i_react, reaction in enumerate(reactions):
        if i_react > 0:
            time.sleep(0.35)
        emoji = reaction.get("emoji", {}) or {}
        name = emoji.get("name", "")
        eid = emoji.get("id")
        param = f"{name}:{eid}" if eid else name
        url = (
            f"{dh.DISCORD_API}/channels/{dh.CHANNEL_ID}/messages/"
            f"{msg['id']}/reactions/{urllib.parse.quote(param)}?limit=100"
        )
        resp = None
        for attempt in range(4):
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 429:
                retry = resp.json().get("retry_after", 2.0)
                time.sleep(retry + 0.25)
                continue
            break
        if resp is None or resp.status_code != 200:
            continue
        for user in resp.json():
            uid = str(user.get("id", ""))
            if user.get("bot"):
                continue
            if uid in dh.BOT_USER_IDS:
                continue
            humans.add(uid)
    return len(humans)


def _find_matching_gem(
    conn: sqlite3.Connection, camera_id: str, msg_ts_iso: str, tolerance_s: int = 60,
) -> Optional[int]:
    """Look up the image_archive row for `camera_id` with the ts closest
    to `msg_ts_iso`, within ±tolerance_s. Returns gem_id or None.

    Uses strftime('%s', ...) on both sides; SQLite handles the '+00:00'
    suffix. 60s default is generous — the webhook typically fires
    within 1-2s of the capture.
    """
    row = conn.execute(
        """
        SELECT id,
               abs(strftime('%s', ts) - strftime('%s', ?)) AS delta
          FROM image_archive
         WHERE camera_id = ?
         ORDER BY delta ASC
         LIMIT 1
        """,
        (msg_ts_iso, camera_id),
    ).fetchone()
    if not row:
        return None
    gem_id, delta = row
    if delta is None or int(delta) > tolerance_s:
        return None
    return int(gem_id)


def _update_gem_reactions(
    conn: sqlite3.Connection,
    gem_id: int,
    discord_message_id: str,
    reactions: int,
) -> None:
    """Write reaction count back. checked_at always advances so the
    sync can use it for incremental logic later. No COALESCE on
    reactions — we want newest-observed count to win."""
    conn.execute(
        """
        UPDATE image_archive
           SET discord_message_id = ?,
               discord_reactions = ?,
               discord_reactions_checked_at = ?
         WHERE id = ?
        """,
        (
            discord_message_id,
            int(reactions),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            gem_id,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync Discord reaction counts into image_archive.",
    )
    parser.add_argument(
        "--since-hours",
        type=int,
        default=48,
        help="Only check messages newer than this many hours (default 48).",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Override --since-hours and scan the entire channel history.",
    )
    args = parser.parse_args(argv)
    _setup_logging()
    log = logging.getLogger("discord-reaction-sync")

    dh = _load_discord_client()

    try:
        token = dh.load_bot_token()
    except SystemExit:
        log.error("could not load Discord bot token (see stderr above)")
        return 1

    db_path = REPO_ROOT / "data" / "guardian.db"
    if not db_path.exists():
        log.error("guardian db not found: %s", db_path)
        return 1

    log.info(
        "fetching messages from #farm-2026 (%s)...",
        "ALL (backfill)" if args.backfill else f"last {args.since_hours}h",
    )
    messages = dh.fetch_all_messages(token)
    log.info("fetched %d total messages", len(messages))

    if not args.backfill:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
        def _msg_dt(m):
            return datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
        messages = [m for m in messages if _msg_dt(m) >= cutoff]
        log.info("after --since-hours filter: %d messages", len(messages))

    # Only messages with at least one reaction + known Guardian sender
    candidates: list[dict] = []
    for m in messages:
        if not m.get("reactions"):
            continue
        author = (m.get("author") or {}).get("username") or ""
        cam = _camera_for_username(author)
        if cam is None:
            continue
        candidates.append(m)
    log.info("messages with reactions from Guardian cameras: %d", len(candidates))

    if not candidates:
        log.info("nothing to sync")
        return 0

    updated = 0
    unmatched = 0
    conn = sqlite3.connect(str(db_path))
    try:
        for i, msg in enumerate(candidates):
            if i > 0:
                time.sleep(0.5)  # spread the per-message reaction-user fetches
            author = (msg.get("author") or {}).get("username") or ""
            cam = _camera_for_username(author)
            if cam is None:
                continue
            human_count = _count_human_reactions(msg, token, dh)
            if human_count == 0:
                continue
            gem_id = _find_matching_gem(conn, cam, msg["timestamp"])
            if gem_id is None:
                unmatched += 1
                log.debug(
                    "unmatched: msg=%s cam=%s ts=%s (no gem within 60s)",
                    msg["id"], cam, msg["timestamp"],
                )
                continue
            _update_gem_reactions(conn, gem_id, msg["id"], human_count)
            updated += 1
            log.info(
                "updated gem_id=%s discord_reactions=%d (msg=%s cam=%s)",
                gem_id, human_count, msg["id"], cam,
            )
        conn.commit()
    finally:
        conn.close()

    log.info(
        "sync complete: updated=%d unmatched=%d candidates=%d",
        updated, unmatched, len(candidates),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
