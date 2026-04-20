#!/usr/bin/env python3
# Author: Claude Opus 4.7 (1M context)
# Date: 20-April-2026 (initial); extended 2026-04-20 to ingest human
#       drops alongside Guardian-webhook gem reactions.
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
#          Bot reactions (Larry, Bubba, Egon — other Claude instances)
#          are excluded from BOTH paths; only real-human reactions count.
#
#          Reuses the Discord API plumbing + BOT_USER_IDS from
#          tools/discord_harvester.py so we don't fork two Discord
#          clients.
#
# SRP/DRY check: Pass — single responsibility is "Discord reactions ->
#                image_archive". Guardian-gem updates and human-drop
#                inserts are two sides of the same coin (both use the
#                same reactor-counting + match/insert policy).

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


# ---------------------------------------------------------------------------
# Human-drop ingestion (Boss's iPhone drops, Larry's shares, etc.)
# ---------------------------------------------------------------------------

# Only still images are in-scope for IG posting. Videos from drops are
# a separate pipeline (not built yet).
_DROP_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}

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
    for m in messages:
        if not m.get("reactions"):
            continue
        author = (m.get("author") or {}).get("username") or ""
        cam = _camera_for_username(author)
        if cam is not None:
            guardian_msgs.append(m)
            continue
        attachments = m.get("attachments") or []
        has_image = any(
            Path((a.get("filename") or "")).suffix.lower() in _DROP_IMAGE_EXTENSIONS
            for a in attachments
        )
        if has_image:
            drop_msgs.append(m)

    log.info(
        "messages with reactions: %d (guardian gems) + %d (human drops)",
        len(guardian_msgs), len(drop_msgs),
    )

    if not guardian_msgs and not drop_msgs:
        log.info("nothing to sync")
        return 0

    updated_gems = 0
    unmatched_gems = 0
    drops_inserted = 0
    drops_updated = 0
    drops_skipped = 0
    conn = sqlite3.connect(str(db_path))
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
            updated_gems += 1
            log.info(
                "updated gem_id=%s discord_reactions=%d (msg=%s cam=%s)",
                gem_id, human_count, msg["id"], cam,
            )

        # ---- 2. Human-drop ingestion ----
        for i, msg in enumerate(drop_msgs):
            if i > 0:
                time.sleep(0.5)
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

        conn.commit()
    finally:
        conn.close()

    log.info(
        "sync complete: guardian_updated=%d guardian_unmatched=%d "
        "drops_inserted=%d drops_updated=%d drops_skipped=%d",
        updated_gems, unmatched_gems,
        drops_inserted, drops_updated, drops_skipped,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
