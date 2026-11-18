#!/usr/bin/env python3
# Author: Claude Sonnet 5.5 Low
# Date: 18-Nov-2026 (💯 relabeled "underscored" later the same day, per Boss)
# PURPOSE: Turn emoji reactions on S7 pictures in the Autoresearch Arena #farm
#          channel into labeled data. For every Guardian gem message in that
#          channel it records WHO reacted with WHICH emoji and WHEN in the
#          image_labels table (one row per message+person+emoji, removals
#          marked, never deleted), mapped to a small judgment vocabulary
#          (like / underscored / wrong / questionable; anything else is kept raw
#          with no label). It also keeps image_archive.discord_message_id and
#          discord_reactions up to date, where discord_reactions now means
#          "distinct people who liked it" (like or underscored) so the existing
#          gating (website gems gallery, Instagram lanes) only ever acts on
#          pictures people liked, never on ones marked wrong.
#          Does NOT touch the old #farm-2026 flow: scripts/discord-reaction-sync.py
#          still reads that channel unchanged.
# SRP/DRY check: Pass — one job (Arena reactions -> image_labels). Reuses
#                discord-reaction-sync.py's gem matching (_camera_for_username,
#                _find_matching_gem) by loading it as a module, and
#                tools/discord_harvester.py's token loading, headers and
#                bot-ID list, rather than forking a second Discord client.

"""
arena-label-sync.py — Arena #farm reactions -> image_labels.

Invocation:
  LaunchAgent every 10 minutes
  (deploy/ig-scheduled/com.farmguardian.arena-label-sync.plist).

  Manual:
    venv/bin/python scripts/arena-label-sync.py [--since-hours N] [--backfill] [--dry-run]

Exit codes: 0 synced OK (possibly nothing new); 1 runtime failure.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import sqlite3
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Autoresearch Arena guild, channel "farm" (18-Nov-2026).
ARENA_CHANNEL_ID = "1572675202096173146"

# The judgment vocabulary. Keys are emoji with the variation selector (U+FE0F)
# stripped, so "❤" and "❤️" are the same thing. Anything not listed is still
# recorded (raw emoji, label NULL) so no reaction is ever lost.
LIKE_EMOJI = {"❤", "🧡", "💛", "💚", "💙", "💜", "🤎", "🖤", "🤍", "💗", "💖", "♥"}
LABEL_BY_EMOJI = {
    **{e: "like" for e in LIKE_EMOJI},
    "💯": "underscored",    # "deserves a higher score" — the model scored this too low (Boss, 18-Nov-2026)
    "❌": "wrong",          # something about the picture or caption is wrong
    "✖": "wrong",
    "❎": "wrong",
    "🧐": "questionable",   # needs a closer look; not sure it is right
}
# Labels that count as "people liked this" for image_archive.discord_reactions.
LIKING_LABELS = {"like", "underscored"}  # underscored = liked more than the model scored it

SCHEMA = """
CREATE TABLE IF NOT EXISTS image_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gem_id INTEGER NOT NULL,
    discord_message_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    username TEXT,
    emoji TEXT NOT NULL,
    label TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    removed_at TEXT,
    UNIQUE (discord_message_id, user_id, emoji)
);
CREATE INDEX IF NOT EXISTS idx_image_labels_gem ON image_labels(gem_id);
CREATE INDEX IF NOT EXISTS idx_image_labels_label ON image_labels(label);
"""

log = logging.getLogger("arena-label-sync")


def _norm(emoji_name: str) -> str:
    """Strip the variation selector so heart variants compare equal."""
    return (emoji_name or "").replace("️", "")


def label_for(emoji_name: str):
    """Judgment label for an emoji, or None if it is not in the vocabulary."""
    return LABEL_BY_EMOJI.get(_norm(emoji_name))


def _load_sync_helpers():
    """Load discord-reaction-sync.py (hyphenated name, so not importable the
    usual way) to reuse its gem-matching helpers instead of copying them."""
    spec = importlib.util.spec_from_file_location(
        "discord_reaction_sync", REPO_ROOT / "scripts" / "discord-reaction-sync.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fetch_recent_messages(dh, token: str, cutoff: datetime | None) -> list[dict]:
    """Page backwards through the Arena channel, stopping once messages are
    older than `cutoff` (None = whole history). Bounded so a year of history
    does not cost hundreds of requests every run."""
    import requests
    headers = dh.discord_headers(token)
    out: list[dict] = []
    before = None
    while True:
        url = f"{dh.DISCORD_API}/channels/{ARENA_CHANNEL_ID}/messages?limit=100"
        if before:
            url += f"&before={before}"
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 429:
            time.sleep(float(resp.json().get("retry_after", 2.0)) + 0.25)
            continue
        if resp.status_code != 200:
            raise RuntimeError(f"Discord {resp.status_code}: {resp.text[:200]}")
        page = resp.json()
        if not page:
            break
        out.extend(page)
        before = page[-1]["id"]
        oldest = datetime.fromisoformat(page[-1]["timestamp"].replace("Z", "+00:00"))
        if len(page) < 100 or (cutoff and oldest < cutoff):
            break
        time.sleep(0.5)
    if cutoff:
        out = [m for m in out
               if datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00")) >= cutoff]
    return out


def _fetch_reactors(dh, token: str, msg_id: str, emoji: dict) -> list[dict] | None:
    """Users who reacted with one emoji (first 100). None if the fetch failed,
    so the caller never mistakes an API hiccup for 'everyone removed it'."""
    import requests
    name, eid = emoji.get("name", ""), emoji.get("id")
    param = urllib.parse.quote(f"{name}:{eid}" if eid else name)
    url = (f"{dh.DISCORD_API}/channels/{ARENA_CHANNEL_ID}/messages/{msg_id}"
           f"/reactions/{param}?limit=100")
    for _ in range(4):
        resp = requests.get(url, headers=dh.discord_headers(token), timeout=15)
        if resp.status_code == 429:
            time.sleep(float(resp.json().get("retry_after", 2.0)) + 0.25)
            continue
        return resp.json() if resp.status_code == 200 else None
    return None


def record_message_labels(conn, gem_id: int, msg_id: str, now_iso: str,
                          seen: dict[tuple[str, str], dict]) -> int:
    """Upsert the reactions currently on one message and mark vanished ones
    removed. `seen` maps (user_id, normalized emoji) -> {username, raw, label}
    for every reaction fetched successfully. Returns the number of distinct
    people currently liking the picture (like or underscored)."""
    for (uid, emoji), info in seen.items():
        conn.execute(
            """
            INSERT INTO image_labels
                (gem_id, discord_message_id, user_id, username, emoji, label,
                 first_seen_at, last_seen_at, removed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT (discord_message_id, user_id, emoji)
            DO UPDATE SET last_seen_at = excluded.last_seen_at,
                          removed_at = NULL,
                          username = excluded.username,
                          gem_id = excluded.gem_id
            """,
            (gem_id, msg_id, uid, info["username"], emoji, info["label"], now_iso, now_iso),
        )
    # Anything previously recorded for this message and not seen now was removed.
    for row in conn.execute(
        "SELECT id, user_id, emoji FROM image_labels "
        "WHERE discord_message_id = ? AND removed_at IS NULL", (msg_id,)
    ).fetchall():
        if (row[1], row[2]) not in seen:
            conn.execute("UPDATE image_labels SET removed_at = ? WHERE id = ?", (now_iso, row[0]))
    likers = {uid for (uid, emoji), info in seen.items() if info["label"] in LIKING_LABELS}
    return len(likers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync Arena #farm reactions into image_labels.")
    parser.add_argument("--since-hours", type=int, default=48,
                        help="Only re-check messages newer than this (default 48).")
    parser.add_argument("--backfill", action="store_true", help="Scan the whole channel history.")
    parser.add_argument("--dry-run", action="store_true", help="Read and report; write nothing.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from tools import discord_harvester as dh
    try:
        token = dh.load_bot_token()
    except SystemExit:
        log.error("could not load Discord bot token")
        return 1
    helpers = _load_sync_helpers()

    db_path = REPO_ROOT / "data" / "guardian.db"
    if not db_path.exists():
        log.error("guardian db not found: %s", db_path)
        return 1

    cutoff = None if args.backfill else datetime.now(timezone.utc) - timedelta(hours=args.since_hours)
    try:
        messages = _fetch_recent_messages(dh, token, cutoff)
    except Exception as e:
        log.error("fetching Arena messages failed: %s", e)
        return 1

    # Only Guardian gem posts (webhook identity -> camera). Bubba's chatter and
    # human messages are not pictures and carry no labels here.
    gem_msgs = [m for m in messages
                if helpers._camera_for_username((m.get("author") or {}).get("username") or "")]
    log.info("fetched %d messages, %d are Guardian gem posts", len(messages), len(gem_msgs))

    conn = sqlite3.connect(str(db_path), timeout=30)
    labeled_msgs = unmatched = total_rows = 0
    try:
        conn.executescript(SCHEMA)
        for i, msg in enumerate(gem_msgs):
            cam = helpers._camera_for_username(msg["author"]["username"])
            gem_id = helpers._find_matching_gem(conn, cam, msg["timestamp"])
            if gem_id is None:
                unmatched += 1
                continue
            seen: dict[tuple[str, str], dict] = {}
            all_ok = True
            for reaction in msg.get("reactions") or []:
                if i or seen:
                    time.sleep(0.35)
                emoji = reaction.get("emoji") or {}
                users = _fetch_reactors(dh, token, msg["id"], emoji)
                if users is None:
                    all_ok = False  # don't mark anything removed on a failed fetch
                    continue
                raw = emoji.get("name", "")
                for user in users:
                    uid = str(user.get("id", ""))
                    if user.get("bot") or uid in dh.BOT_USER_IDS:
                        continue
                    seen[(uid, _norm(raw))] = {
                        "username": user.get("username"), "raw": raw, "label": label_for(raw)}
            if not all_ok:
                log.warning("msg=%s: some reaction fetches failed; skipping this message", msg["id"])
                continue
            now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
            likers = record_message_labels(conn, gem_id, msg["id"], now_iso, seen)
            conn.execute(
                "UPDATE image_archive SET discord_message_id = ?, discord_reactions = ?, "
                "discord_reactions_checked_at = ? WHERE id = ?",
                (msg["id"], likers, now_iso, gem_id))
            if seen:
                labeled_msgs += 1
                total_rows += len(seen)
            if args.dry_run:
                conn.rollback()
            else:
                conn.commit()
    finally:
        conn.close()

    log.info("done%s: messages with reactions=%d, reaction rows=%d, unmatched posts=%d",
             " (dry run, nothing written)" if args.dry_run else "", labeled_msgs, total_rows, unmatched)
    return 0


if __name__ == "__main__":
    sys.exit(main())
