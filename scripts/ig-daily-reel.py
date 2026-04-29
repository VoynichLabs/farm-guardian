#!/usr/bin/env python3
# Author: Claude Sonnet 4.6
# Date: 29-April-2026
# PURPOSE: LaunchAgent entry point for the daily Instagram Reel.
#          Fires daily at 18:00 local via
#          com.farmguardian.ig-daily-reel.plist.
#
#          Two-phase flow per run:
#
#          Phase 1 — Approval check:
#            Scan data/reels/pending/*.json for reels posted to Discord
#            the previous day. For each, fetch the Discord message via
#            bot token and count human reactions. If reactions > 0,
#            post the reel to IG and move the state file to
#            data/reels/posted/. If no reactions after 48h, expire it
#            to data/reels/expired/.
#
#          Phase 2 — Build + Discord preview:
#            Select past 24h of reaction-gated gems via
#            ig_selection.select_daily_reel_gems. If fewer than
#            daily_reel_min_frames, exit 0 (quiet day). Otherwise:
#            stitch MP4 via reel_stitcher, build caption, POST the
#            MP4 to the Discord webhook with ?wait=true (returns
#            message_id), save pending state JSON.
#
#          No-action exits (exit 0):
#            - Fewer than min_frames qualifying gems in the 24h window
#            - Today's pending file already exists (already ran today)
#
#          Real failures (ffmpeg exit, Graph API, Discord 4xx) exit 1.
#          Credential-missing exits 3.
#
# SRP/DRY check: Pass — entry point only; all heavy lifting delegated
#                to ig_selection, reel_stitcher, ig_poster, and the
#                discord_harvester helpers already in the codebase.

"""
ig-daily-reel.py — build the day's best gems into a Reel, post to
Discord for approval, and (on the next day's run) publish to IG once
Boss has reacted.

Invocation:
  $LaunchAgent cadence: daily at 18:00 local
    (com.farmguardian.ig-daily-reel.plist)

  Manual:
    venv/bin/python scripts/ig-daily-reel.py [--dry-run] [--skip-build]

Exit codes:
  0 — success, OR no-action (quiet day / already ran today)
  1 — runtime failure
  3 — credentials missing
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_PENDING_DIR = REPO_ROOT / "data" / "reels" / "pending"
_POSTED_DIR = REPO_ROOT / "data" / "reels" / "posted"
_EXPIRED_DIR = REPO_ROOT / "data" / "reels" / "expired"

# Age after which an unreacted pending reel is abandoned.
_EXPIRE_HOURS = 48


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def _load_config() -> dict:
    cfg_path = REPO_ROOT / "tools" / "pipeline" / "config.json"
    return json.loads(cfg_path.read_text())


def _load_env() -> None:
    from tools.pipeline.gem_poster import load_dotenv
    load_dotenv(REPO_ROOT / ".env")


def _load_discord_client():
    from tools import discord_harvester as dh
    return dh


def _count_human_reactions_on_message(message_id: str, token: str, dh) -> int:
    """Fetch the Discord message and count unique human (non-bot) reactors.

    Uses the same exclusion logic as discord-reaction-sync: skip any user
    whose ID is in dh.BOT_USER_IDS or whose user.bot flag is True.
    Returns 0 on any API error.
    """
    headers = dh.discord_headers(token)
    # Fetch the message to get its reactions list.
    msg_url = f"{dh.DISCORD_API}/channels/{dh.CHANNEL_ID}/messages/{message_id}"
    try:
        resp = requests.get(msg_url, headers=headers, timeout=15)
    except requests.RequestException as e:
        logging.getLogger("ig-daily-reel").warning(
            "discord: failed to fetch message %s: %s", message_id, e
        )
        return 0

    if resp.status_code != 200:
        logging.getLogger("ig-daily-reel").warning(
            "discord: message fetch %s returned http=%d", message_id, resp.status_code
        )
        return 0

    msg = resp.json()
    reactions = msg.get("reactions") or []
    if not reactions:
        return 0

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
            f"{message_id}/reactions/{urllib.parse.quote(param)}?limit=100"
        )
        r2 = None
        for _ in range(4):
            r2 = requests.get(url, headers=headers, timeout=15)
            if r2.status_code == 429:
                retry = r2.json().get("retry_after", 2.0)
                time.sleep(retry + 0.25)
                continue
            break
        if r2 is None or r2.status_code != 200:
            continue
        for user in r2.json():
            uid = str(user.get("id", ""))
            if user.get("bot"):
                continue
            if uid in dh.BOT_USER_IDS:
                continue
            humans.add(uid)

    return len(humans)


def _post_video_to_discord(
    mp4_path: Path,
    caption: str,
    webhook_url: str,
    timeout: int = 60,
) -> str | None:
    """POST the MP4 file to Discord via webhook with ?wait=true.

    Returns the Discord message_id string on success, None on failure.
    The ?wait=true param makes Discord return the full message object
    synchronously so we can capture the id for later reaction-checking.
    """
    log = logging.getLogger("ig-daily-reel")
    content = (caption or "Daily reel preview.").strip()
    # Discord content cap is 2000; prefix with a flag emoji to make it
    # easy to spot in #farm-2026 among the individual gem posts.
    preview_content = f"🎬 Daily reel preview — react to approve for IG\n\n{content}"
    if len(preview_content) > 1900:
        preview_content = preview_content[:1900] + "…"

    wait_url = webhook_url.rstrip("/") + "?wait=true"
    try:
        with mp4_path.open("rb") as f:
            r = requests.post(
                wait_url,
                files={"file": ("daily-reel-preview.mp4", f, "video/mp4")},
                data={"payload_json": json.dumps({
                    "username": "farm-reel",
                    "content": preview_content,
                })},
                timeout=timeout,
            )
    except requests.RequestException as e:
        log.warning("discord: video upload failed: %s", e)
        return None

    if 200 <= r.status_code < 300:
        msg_id = r.json().get("id")
        log.info("discord: posted reel preview, message_id=%s", msg_id)
        return msg_id

    log.warning(
        "discord: video upload rejected http=%d body=%r",
        r.status_code, (r.text or "")[:200],
    )
    return None


def _fetch_gem_row(db_path: Path, gem_id: int) -> dict:
    import sqlite3
    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        r = c.execute(
            "SELECT * FROM image_archive WHERE id = ?", (gem_id,),
        ).fetchone()
    return dict(r) if r else {}


def _build_reel_caption(db_path: Path, gem_ids: list[int]) -> str:
    """Build IG caption: best VLM caption_draft from the gem set, plus hashtags."""
    from tools.pipeline.ig_poster import build_caption, pick_hashtags, _load_hashtag_library

    best_gem = None
    best_meta: dict = {}
    best_reactions = -1
    for gid in gem_ids:
        row = _fetch_gem_row(db_path, gid)
        try:
            meta = json.loads(row.get("vlm_json") or "{}") or {}
        except json.JSONDecodeError:
            meta = {}
        reactions = row.get("discord_reactions") or 0
        if reactions > best_reactions:
            best_reactions = reactions
            best_gem = row
            best_meta = meta

    journal = (best_meta.get("caption_draft") or "").strip()
    if not journal:
        journal = "A day at the farm."

    library = _load_hashtag_library(REPO_ROOT / "tools" / "pipeline" / "hashtags.yml")
    tags = pick_hashtags(vlm_metadata=best_meta, library=library, last_n_tags_used=[])
    return build_caption(journal_body=journal, hashtags=tags)


# ---------------------------------------------------------------------------
# Phase 1 — check pending reels for approval
# ---------------------------------------------------------------------------

def _check_pending_reels(
    db_path: Path,
    farm_2026: Path,
    dh,
    token: str,
    dry_run: bool,
    log: logging.Logger,
) -> None:
    """Iterate data/reels/pending/*.json; post approved ones to IG."""
    from tools.pipeline.reel_stitcher import ReelStitcherError
    from tools.pipeline.ig_poster import post_reel_to_ig, IGPosterError

    _PENDING_DIR.mkdir(parents=True, exist_ok=True)
    _POSTED_DIR.mkdir(parents=True, exist_ok=True)
    _EXPIRED_DIR.mkdir(parents=True, exist_ok=True)

    pending_files = sorted(_PENDING_DIR.glob("*.json"))
    if not pending_files:
        log.info("pending: no pending reels to check")
        return

    now = datetime.now(timezone.utc)
    for pf in pending_files:
        try:
            state = json.loads(pf.read_text())
        except Exception as e:
            log.warning("pending: could not read %s: %s", pf.name, e)
            continue

        message_id = state.get("discord_message_id", "")
        mp4_path = Path(state.get("mp4_path", ""))
        gem_ids = state.get("gem_ids") or []
        caption = state.get("caption", "")
        created_iso = state.get("created_at", "")

        if not message_id:
            log.warning("pending: %s has no discord_message_id, skipping", pf.name)
            continue

        # Age check — expire old unreacted reels.
        try:
            created_at = datetime.fromisoformat(created_iso)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        except ValueError:
            created_at = now  # be conservative — don't expire if unparseable

        age_h = (now - created_at).total_seconds() / 3600

        human_count = _count_human_reactions_on_message(message_id, token, dh)
        log.info(
            "pending: %s — message_id=%s age=%.1fh reactions=%d",
            pf.name, message_id, age_h, human_count,
        )

        if human_count == 0:
            if age_h >= _EXPIRE_HOURS:
                log.warning(
                    "pending: %s expired (%dh old, no reactions); moving to expired/",
                    pf.name, int(age_h),
                )
                pf.rename(_EXPIRED_DIR / pf.name)
            else:
                log.info("pending: %s still awaiting approval (%.1fh old)", pf.name, age_h)
            continue

        # Boss reacted — post to IG.
        if not mp4_path.exists():
            log.error(
                "pending: %s approved but MP4 missing at %s; expiring",
                pf.name, mp4_path,
            )
            pf.rename(_EXPIRED_DIR / pf.name)
            continue

        log.info(
            "pending: %s approved (%d reactions); posting to IG",
            pf.name, human_count,
        )
        try:
            result = post_reel_to_ig(
                reel_mp4_path=mp4_path if not dry_run else None,
                caption=caption,
                db_path=db_path,
                farm_2026_repo_path=farm_2026,
                associated_gem_ids=gem_ids,
                dry_run=dry_run,
            )
        except IGPosterError as e:
            log.error("pending: IG post failed (credentials): %s", e)
            continue

        if result.get("error"):
            log.error("pending: IG post failed: %s", result["error"])
            continue

        log.info(
            "pending: posted reel to IG -> %s",
            result.get("permalink") or result.get("raw_url"),
        )
        pf.rename(_POSTED_DIR / pf.name)


# ---------------------------------------------------------------------------
# Phase 2 — build today's reel and post to Discord
# ---------------------------------------------------------------------------

def _build_and_preview(
    db_path: Path,
    cfg: dict,
    webhook_url: str,
    dry_run: bool,
    skip_build: bool,
    log: logging.Logger,
) -> int:
    """Select gems, stitch MP4, post to Discord, save pending state.

    Returns 0 on success or quiet-day skip; 1 on failure.
    """
    from tools.pipeline.ig_selection import select_daily_reel_gems
    from tools.pipeline.reel_stitcher import stitch_gems_to_reel, ReelStitcherError

    ig_cfg = cfg.get("instagram") or {}
    sched_cfg = ig_cfg.get("scheduled") or {}
    reels_cfg = ig_cfg.get("reels") or {}

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pending_file = _PENDING_DIR / f"{today_str}.json"

    if pending_file.exists() and not skip_build:
        log.info("build: today's reel already queued (%s); skipping build", today_str)
        return 0

    gem_ids = select_daily_reel_gems(db_path=db_path, cfg=sched_cfg)
    if not gem_ids:
        log.info("build: quiet day — not enough gems for a reel; skipping slot")
        return 0

    log.info("build: stitching %d gems for %s (dry_run=%s)", len(gem_ids), today_str, dry_run)

    output_root = Path(reels_cfg.get("output_root", "data/reels"))
    if not output_root.is_absolute():
        output_root = REPO_ROOT / output_root
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    out_dir = output_root / ym
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    slug = uuid.uuid4().hex[:8]
    mp4_path = out_dir / f"reel-daily-{stamp}-{slug}.mp4"

    try:
        stitch_gems_to_reel(
            gem_ids=gem_ids,
            db_path=db_path,
            config=reels_cfg,
            output_path=mp4_path,
        )
    except ReelStitcherError as e:
        log.error("build: stitch failed: %s", e)
        return 1

    try:
        caption = _build_reel_caption(db_path, gem_ids)
    except Exception as e:
        log.exception("build: caption build failed: %s", e)
        return 1

    log.info(
        "build: MP4 ready %s (%d bytes)",
        mp4_path, mp4_path.stat().st_size,
    )

    if dry_run:
        log.info(
            "build: dry-run — would post %s to Discord and save pending state",
            mp4_path.name,
        )
        return 0

    message_id = _post_video_to_discord(mp4_path, caption, webhook_url)
    if not message_id:
        log.error("build: Discord preview post failed; not saving pending state")
        return 1

    _PENDING_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "date": today_str,
        "discord_message_id": message_id,
        "mp4_path": str(mp4_path),
        "gem_ids": gem_ids,
        "caption": caption,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    pending_file.write_text(json.dumps(state, indent=2))
    log.info(
        "build: pending state saved -> %s (discord_message_id=%s)",
        pending_file.name, message_id,
    )
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build + Discord-gate daily IG reel.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Stitch locally; skip Discord upload and IG post.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Only run the approval-check phase; do not build a new reel.",
    )
    args = parser.parse_args(argv)
    _setup_logging()
    log = logging.getLogger("ig-daily-reel")

    _load_env()

    cfg = _load_config()
    ig_cfg = cfg.get("instagram") or {}
    db_path = REPO_ROOT / cfg["guardian_db_path"]
    farm_2026 = Path(ig_cfg.get("farm_2026_repo_path", "")).expanduser()

    import os
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        log.error("DISCORD_WEBHOOK_URL missing from environment")
        return 3
    if not db_path.exists():
        log.error("guardian db not found: %s", db_path)
        return 1
    if not farm_2026.exists():
        log.error("farm_2026 repo not found: %s", farm_2026)
        return 1

    dh = _load_discord_client()
    try:
        token = dh.load_bot_token()
    except Exception as e:
        log.error("discord bot token missing: %s", e)
        return 3

    # Phase 1: check and post any approved pending reels.
    _check_pending_reels(
        db_path=db_path,
        farm_2026=farm_2026,
        dh=dh,
        token=token,
        dry_run=args.dry_run,
        log=log,
    )

    if args.skip_build:
        log.info("--skip-build set; skipping reel construction")
        return 0

    # Phase 2: build today's reel and post to Discord for approval.
    rc = _build_and_preview(
        db_path=db_path,
        cfg=cfg,
        webhook_url=webhook_url,
        dry_run=args.dry_run,
        skip_build=False,
        log=log,
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
