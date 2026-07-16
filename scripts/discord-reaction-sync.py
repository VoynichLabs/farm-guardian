#!/usr/bin/env python3
# Author: GPT-5.5; Claude Sonnet 4.6 (04-May-2026 — per-message commit to fix DB lock contention, v2.40.3); Claude Fable 5 (16-Jul-2026 — bird-name reply tagging + retention pinning, v2.47.2)
# Date: 03-May-2026 (last touched 16-Jul-2026)
# PURPOSE: Two jobs, run in one pass over #farm-2026:
#
#          1. GUARDIAN GEMS — for every reacted message whose author is
#             a Guardian webhook identity (mapped via gem_poster's
#             _USERNAME_BY_CAMERA), find the matching image_archive row
#             by (camera_id, ts ±60s) and write the human-reactor count
#             to image_archive.discord_reactions.
#
#          2. HUMAN DROPS — for every reacted message whose author is
#             NOT a Guardian camera AND whose attachments include an
#             image (.jpg/.jpeg/.png), download the image to
#             data/discord-drops/YYYY-MM/ and ingest a synthetic row
#             into image_archive so the image becomes eligible for the
#             scheduled IG lanes. sha256 dedup: re-runs don't create
#             duplicate rows. camera_id='discord-drop'. vlm_json's
#             caption_draft is populated from the Discord message text
#             so the IG caption builder has something narrative to use.
#
#             2026-05-03: messages authored by the Archive webhook are
#             explicitly ignored. The archive throwback picker is disabled
#             after poor old-photo selection polluted daily Reel material.
#
#          Bot reactions (Larry, Bubba, Egon — other Claude instances)
#          are excluded from BOTH paths; only real-human reactions count.
#
#          Reuses the Discord API plumbing + BOT_USER_IDS from
#          tools/discord_harvester.py so we don't fork two Discord
#          clients.
#
#          3. BIRD-NAME TAGGING (v2.47.2, 16-Jul-2026, plan Part E3) — a
#             third job in the same pass: for every message that is a
#             Discord REPLY to a Guardian gem post, take the reply's text,
#             match it against the live flock roster (tools/pipeline/
#             roster.py, case-insensitive exact match), and if it matches:
#               - append the matched name (lowercased) to the target gem's
#                 individuals_visible_csv — the SAME column + CSV-contains
#                 convention the old (pre-v2.38.2) structured VLM
#                 classification used for "birdadette" and that the public
#                 gallery's `individual=` filter already queries
#                 (database.py._individuals_clause). No schema change.
#               - pin the gem's retention (retained_until = NULL) so a
#                 named frame survives the sweep forever (E4 — retention.py
#                 itself needed no change: sweep() already skips
#                 retained_until IS NULL rows).
#               - audit the tag in image_archive_edits (action=
#                 'identify_bird'), reusing the table's existing
#                 promote/demote/flag audit-trail design for a new action.
#             A reply that does NOT match any roster name gets a ❓
#             reaction back (best-effort) instead of a silent drop, per
#             Boss's 16-Jul decision. Idempotent both ways: a name already
#             in the CSV is skipped; an already-❓'d reply (checked via the
#             fetched message's own `reactions[].me` flag) is skipped.
#             This is optional enrichment on top of Boss's normal Discord
#             reactions — replying with a name is never required.
#
# SRP/DRY check: Pass — single responsibility is "Discord activity ->
#                image_archive". Guardian-gem reaction updates, human-drop
#                inserts, and bird-name reply tagging are three sides of
#                the same coin (all three read the same fetched message
#                list and write to image_archive).

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
_BLOCKED_DROP_AUTHORS = {"archive"}


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


# ---------------------------------------------------------------------------
# Bird-name reply tagging (v2.47.2, plan Part E3)
# ---------------------------------------------------------------------------

_UNKNOWN_NAME_EMOJI = "❓"


def _find_gem_by_message_id(conn: sqlite3.Connection, discord_message_id: str) -> Optional[dict]:
    """Look up the image_archive row a Guardian gem message posted for,
    by the discord_message_id stamped in _update_gem_reactions. Returns
    the row (as a dict) with individuals_visible_csv, or None if this
    message id isn't a known gem post (e.g. it's some other message)."""
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, individuals_visible_csv FROM image_archive WHERE discord_message_id = ?",
        (discord_message_id,),
    ).fetchone()
    return dict(row) if row else None


def _already_reacted(msg: dict, emoji: str) -> bool:
    """True if the bot has already placed `emoji` on this message —
    read straight from the reactions Discord already sent us (the `me`
    flag), no extra API call."""
    for reaction in msg.get("reactions") or []:
        if (reaction.get("emoji") or {}).get("name") == emoji and reaction.get("me"):
            return True
    return False


def _add_reaction(msg_id: str, emoji: str, token: str, dh) -> bool:
    """PUT a reaction from the bot account onto a message. Best-effort —
    returns False (never raises) on any failure so a Discord hiccup
    never breaks the sync run."""
    import requests
    url = (
        f"{dh.DISCORD_API}/channels/{dh.CHANNEL_ID}/messages/{msg_id}/"
        f"reactions/{urllib.parse.quote(emoji)}/@me"
    )
    try:
        resp = requests.put(url, headers=dh.discord_headers(token), timeout=10)
        return resp.status_code in (200, 204)
    except Exception:
        return False


def _sync_bird_name_tags(
    conn: sqlite3.Connection,
    messages: list[dict],
    token: str,
    dh,
) -> dict:
    """Third pass over the same fetched message list: replies to gem
    posts that name a roster bird get written onto the gem row.

    Iterates ALL fetched messages (not just reacted ones — a reply is
    its own signal, independent of the reaction path above) looking for
    `referenced_message` (Discord inlines the resolved parent message on
    a reply, when it hasn't been deleted).
    """
    from tools.pipeline.roster import match_name

    tagged = 0
    unmatched = 0
    skipped_not_a_reply = 0

    for msg in messages:
        parent = msg.get("referenced_message")
        if not parent or not parent.get("id"):
            skipped_not_a_reply += 1
            continue

        author = msg.get("author") or {}
        if author.get("bot"):
            continue
        author_id = str(author.get("id", ""))
        if author_id in dh.BOT_USER_IDS:
            continue

        gem = _find_gem_by_message_id(conn, parent["id"])
        if gem is None:
            continue  # reply to something other than a tracked gem post

        reply_text = (msg.get("content") or "").strip()
        if not reply_text:
            continue

        matched = match_name(reply_text)
        if matched is None:
            if _already_reacted(msg, _UNKNOWN_NAME_EMOJI):
                continue
            _add_reaction(msg["id"], _UNKNOWN_NAME_EMOJI, token, dh)
            unmatched += 1
            continue

        existing_csv = gem.get("individuals_visible_csv") or ""
        existing_names = [n for n in existing_csv.split(",") if n]
        tag = matched.lower()
        if tag in existing_names:
            continue  # already tagged — idempotent no-op

        pre_state = {"individuals_visible_csv": existing_csv, "retained_until": None}
        new_csv = ",".join(existing_names + [tag])
        gem_id = gem["id"]

        # Pin retention (E4): retained_until = NULL means retention.sweep()'s
        # `retained_until IS NOT NULL AND retained_until <= today` filter
        # never matches this row — a named frame is kept indefinitely.
        conn.execute(
            "UPDATE image_archive SET individuals_visible_csv = ?, retained_until = NULL WHERE id = ?",
            (new_csv, gem_id),
        )
        import json as _json
        conn.execute(
            """
            INSERT INTO image_archive_edits
                (target_image_id, action, actor, note, request_id, pre_state, post_state)
            VALUES (?, 'identify_bird', ?, ?, ?, ?, ?)
            """,
            (
                gem_id,
                f"discord:{author.get('username', author_id)}",
                reply_text[:200],
                msg["id"],
                _json.dumps(pre_state),
                _json.dumps({"individuals_visible_csv": new_csv, "retained_until": None}),
            ),
        )
        conn.commit()
        tagged += 1
        logging.getLogger("discord-reaction-sync").info(
            "bird-name tag: gem_id=%s name=%s (reply msg=%s)", gem_id, matched, msg["id"],
        )

    return {"tagged": tagged, "unmatched": unmatched, "not_replies": skipped_not_a_reply}


# ---------------------------------------------------------------------------
# Human-drop ingestion (Boss's iPhone drops, Larry's shares, etc.)
# ---------------------------------------------------------------------------

# Only still images are in-scope for IG posting. Videos from direct
# posts are a separate pipeline (not built yet).
_DROP_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

# Boss's Discord user id. Any message he posts in #farm-2026 with an
# image attachment is auto-ingested — no human reaction needed.
# Rationale (2026-04-23): his act of posting the photo IS the
# quality-gate signal; requiring a self-reaction is friction. Other
# non-bot users still need at least one real-human reaction.
BOSS_DISCORD_USER_ID = "293569238386606080"

# Where downloaded drops live under data/. Relative to REPO_ROOT. Matches
# the rest of Guardian's data/ layout (gitignored).
_DROP_SUBDIR = "discord-drops"


def _drop_dest(msg: dict, attachment: dict, idx: int) -> tuple[Path, str, str]:
    """Compute (absolute_path, path_relative_to_data_root, extension)
    for a downloaded drop. Relative path is what gets written to
    image_archive.image_path so the shared resolve_gem_image_path
    helper can locate it later.
    """
    filename = attachment.get("filename", "") or ""
    ext = Path(filename).suffix.lower() or ".jpg"
    ts_iso = msg["timestamp"]
    dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    ym = dt.strftime("%Y-%m")
    out_name = f"discord-{msg['id']}-{idx}{ext}"
    rel = f"{_DROP_SUBDIR}/{ym}/{out_name}"
    abs_path = REPO_ROOT / "data" / rel
    return abs_path, rel, ext


def _download_attachment(url: str, dest: Path) -> bool:
    """Pull a Discord CDN attachment to dest. Returns True on success.
    Does NOT overwrite if dest already exists (idempotent re-runs)."""
    import requests
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(url, stream=True, timeout=30)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        log = logging.getLogger("discord-reaction-sync")
        log.warning("drop download failed url=%s: %s", url, e)
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        return False


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _ingest_drop(
    conn: sqlite3.Connection,
    msg: dict,
    attachment: dict,
    idx: int,
    reaction_count: int,
) -> Optional[tuple[str, int]]:
    """Ensure this drop attachment is represented in image_archive with
    the latest reaction count.

    Idempotent: dedups against existing rows by sha256. If the image is
    already a row (e.g. a Guardian gem that was re-shared, or a
    previously-ingested drop), UPDATE its reactions; otherwise INSERT
    a synthetic row.

    Returns (action, gem_id) where action is 'insert' | 'update' | 'skip',
    or None for fatal skip (download failed, unsupported type).
    """
    filename = attachment.get("filename", "") or ""
    ext = Path(filename).suffix.lower()
    if ext not in _DROP_IMAGE_EXTENSIONS:
        return None

    abs_path, rel_path, _ = _drop_dest(msg, attachment, idx)
    url = attachment.get("url")
    if not url:
        return None

    if not _download_attachment(url, abs_path):
        return None

    # Verify cv2 can decode before we commit to inserting — catches the
    # "CDN returned an HTML error page instead of the image" case.
    try:
        import cv2
        img = cv2.imread(str(abs_path))
        if img is None:
            raise ValueError("cv2 returned None")
        height, width = img.shape[:2]
    except Exception as e:
        logging.getLogger("discord-reaction-sync").warning(
            "drop decode failed, removing %s: %s", abs_path, e,
        )
        try:
            abs_path.unlink()
        except OSError:
            pass
        return None

    sha = _sha256(abs_path)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    existing = conn.execute(
        "SELECT id FROM image_archive WHERE sha256 = ?", (sha,),
    ).fetchone()
    if existing:
        gid = int(existing[0])
        conn.execute(
            """
            UPDATE image_archive
               SET discord_message_id = ?,
                   discord_reactions = ?,
                   discord_reactions_checked_at = ?
             WHERE id = ?
            """,
            (str(msg["id"]), int(reaction_count), now_iso, gid),
        )
        return ("update", gid)

    # New drop — insert a synthetic row. Defaults set so the three
    # IG selection helpers treat it as post-eligible once it has
    # reactions: strong+sharp+bird_count=1+no concerns.
    import json as _json
    caption_draft = (msg.get("content") or "").strip()
    vlm_meta = {
        "scene": "brooder",
        "bird_count": 1,
        "activity": "unknown",
        "lighting": "unknown",
        "composition": "unknown",
        "image_quality": "sharp",
        "share_worth": "strong",
        "any_special_chick": False,
        "apparent_age_days": None,
        "has_concerns": False,
        "concerns": [],
        "individuals_visible": [],
        "caption_draft": caption_draft,
    }

    cur = conn.execute(
        """
        INSERT INTO image_archive (
            camera_id, ts, image_path, image_tier, sha256,
            width, height, bytes,
            vlm_model, vlm_inference_ms, vlm_prompt_hash, vlm_json,
            scene, bird_count, activity, lighting, composition,
            image_quality, share_worth, any_special_chick, apparent_age_days,
            has_concerns, individuals_visible_csv, retained_until,
            discord_message_id, discord_reactions, discord_reactions_checked_at
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?
        )
        """,
        (
            "discord-drop", msg["timestamp"], rel_path, "strong", sha,
            int(width), int(height), abs_path.stat().st_size,
            "discord-drop", 0, "", _json.dumps(vlm_meta),
            "brooder", 1, "unknown", "unknown", "unknown",
            "sharp", "strong", 0, None,
            0, "", None,
            str(msg["id"]), int(reaction_count), now_iso,
        ),
    )
    return ("insert", int(cur.lastrowid))


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

    # Bucket reacted messages into Guardian-gem updates vs human drops.
    # A message qualifies as a drop when: author is not a Guardian camera
    # AND at least one attachment is a still image.
    guardian_msgs: list[dict] = []
    drop_msgs: list[dict] = []
    blocked_drop_msgs = 0
    for m in messages:
        if not m.get("reactions"):
            continue
        author = (m.get("author") or {}).get("username") or ""
        cam = _camera_for_username(author)
        if cam is not None:
            guardian_msgs.append(m)
            continue
        if author.lower() in _BLOCKED_DROP_AUTHORS:
            blocked_drop_msgs += 1
            continue
        attachments = m.get("attachments") or []
        has_image = any(
            Path((a.get("filename") or "")).suffix.lower() in _DROP_IMAGE_EXTENSIONS
            for a in attachments
        )
        if has_image:
            drop_msgs.append(m)

    # Bird-name reply tagging (v2.47.2) reads the FULL message list, not
    # just reacted ones — a reply is its own signal independent of
    # reactions on either the reply or the parent gem post.
    reply_msgs = [m for m in messages if (m.get("referenced_message") or {}).get("id")]

    log.info(
        "messages with reactions: %d (guardian gems) + %d (human drops); "
        "replies (bird-name candidates): %d; skipped disabled archive drops=%d",
        len(guardian_msgs), len(drop_msgs), len(reply_msgs), blocked_drop_msgs,
    )

    if not guardian_msgs and not drop_msgs and not reply_msgs:
        log.info("nothing to sync")
        return 0

    updated_gems = 0
    unmatched_gems = 0
    drops_inserted = 0
    drops_updated = 0
    drops_skipped = 0
    # timeout=30: match database.py; commit per-message so the write lock is
    # released between Discord API calls and doesn't starve the pipeline writer.
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        # ---- 1. Guardian-gem reaction updates ----
        for i, msg in enumerate(guardian_msgs):
            if i > 0:
                time.sleep(0.5)  # spread per-message reaction-user fetches
            author = (msg.get("author") or {}).get("username") or ""
            cam = _camera_for_username(author)
            if cam is None:
                continue
            human_count = _count_human_reactions(msg, token, dh)
            if human_count == 0:
                continue
            gem_id = _find_matching_gem(conn, cam, msg["timestamp"])
            if gem_id is None:
                unmatched_gems += 1
                log.debug(
                    "unmatched: msg=%s cam=%s ts=%s (no gem within 60s)",
                    msg["id"], cam, msg["timestamp"],
                )
                continue
            _update_gem_reactions(conn, gem_id, msg["id"], human_count)
            conn.commit()  # release write lock between messages
            updated_gems += 1
            log.info(
                "updated gem_id=%s discord_reactions=%d (msg=%s cam=%s)",
                gem_id, human_count, msg["id"], cam,
            )

        # ---- 2. Human-posted image ingestion ----
        for i, msg in enumerate(drop_msgs):
            if i > 0:
                time.sleep(0.5)
            author_id = (msg.get("author") or {}).get("id") or ""
            # Boss's posts qualify automatically (the act of posting
            # IS his quality signal — no self-reaction required).
            # Everyone else still needs at least one real-human react.
            if author_id == BOSS_DISCORD_USER_ID:
                human_count = max(1, _count_human_reactions(msg, token, dh))
            else:
                human_count = _count_human_reactions(msg, token, dh)
            if human_count == 0:
                continue
            attachments = msg.get("attachments") or []
            author = (msg.get("author") or {}).get("username") or "(unknown)"
            for idx, att in enumerate(attachments):
                ext = Path((att.get("filename") or "")).suffix.lower()
                if ext not in _DROP_IMAGE_EXTENSIONS:
                    continue
                result = _ingest_drop(conn, msg, att, idx, human_count)
                if result is None:
                    drops_skipped += 1
                    continue
                conn.commit()  # release write lock between messages
                action, gem_id = result
                if action == "insert":
                    drops_inserted += 1
                    log.info(
                        "inserted drop gem_id=%s reactions=%d (msg=%s author=%s idx=%d)",
                        gem_id, human_count, msg["id"], author, idx,
                    )
                elif action == "update":
                    drops_updated += 1
                    log.info(
                        "updated drop gem_id=%s reactions=%d (msg=%s author=%s idx=%d)",
                        gem_id, human_count, msg["id"], author, idx,
                    )

        # ---- 3. Bird-name reply tagging (v2.47.2, plan Part E3) ----
        # Isolated in its own try/except: it's the newest of the three jobs
        # and a bug here must not cost the reaction-sync work already done
        # above in this same run.
        try:
            tag_stats = _sync_bird_name_tags(conn, reply_msgs, token, dh)
        except Exception as e:
            log.warning("bird-name tagging failed (reactions/drops above still synced): %s", e)
            tag_stats = {"tagged": 0, "unmatched": 0, "not_replies": len(reply_msgs)}

    finally:
        conn.close()

    log.info(
        "sync complete: guardian_updated=%d guardian_unmatched=%d "
        "drops_inserted=%d drops_updated=%d drops_skipped=%d "
        "bird_tags=%d bird_tag_unmatched=%d",
        updated_gems, unmatched_gems,
        drops_inserted, drops_updated, drops_skipped,
        tag_stats["tagged"], tag_stats["unmatched"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
