# Author: GPT-5.5
# Date: 03-May-2026
# PURPOSE: Shared runner for scheduled Instagram Reel lanes. The
#          existing mixed-camera daily Reel uses the approval-gated
#          flow: build MP4, upload a Discord preview, wait for a human
#          reaction on a later run, then publish to IG/FB. The S7
#          daily time-lapse lane uses the same selector/stitch/post
#          primitives but auto-publishes and then sends a Discord
#          notice mentioning Mark. This module centralizes Discord
#          upload/transcode, pending-state handling, quota-ledger
#          checks, caption construction, and reel MP4 creation so new
#          lane scripts stay thin and the publish path does not fork.
# SRP/DRY check: Pass - one responsibility is "run a configured daily
#                Reel lane." Reuses ig_selection, reel_stitcher,
#                ig_poster, discord_harvester, and tools.social.ledger.

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MARK_DISCORD_USER_ID = "293569238386606080"

_EXPIRE_HOURS = 48
_DISCORD_MAX_BYTES = 7 * 1024 * 1024
_DISCORD_PREVIEW_SCALE = "540:960"


@dataclass(frozen=True)
class DailyReelLane:
    """Configuration for one scheduled Reel lane."""

    lane_id: str
    log_name: str
    description: str
    selector_name: str
    state_subdir: str
    output_filename_prefix: str
    discord_username: str
    discord_title: str
    approval_required: bool
    ledger_lane: str
    caption_fallback: str
    mention_user_id: Optional[str] = None


MIXED_DAILY_REEL_LANE = DailyReelLane(
    lane_id="daily",
    log_name="ig-daily-reel",
    description="Build + Discord-gate daily IG Reel.",
    selector_name="select_daily_reel_gems",
    state_subdir="",
    output_filename_prefix="reel-daily",
    discord_username="farm-reel",
    discord_title="Daily reel preview",
    approval_required=True,
    ledger_lane="reel",
    caption_fallback="A day at the farm.",
)

S7_DAILY_REEL_LANE = DailyReelLane(
    lane_id="s7-daily",
    log_name="ig-s7-daily-reel",
    description="Build and auto-post the S7 daily time-lapse IG Reel.",
    selector_name="select_s7_daily_reel_gems",
    state_subdir="s7",
    output_filename_prefix="reel-s7-daily",
    discord_username="farm-reel-s7",
    discord_title="S7 daily time-lapse Reel",
    approval_required=False,
    ledger_lane="s7-reel",
    caption_fallback="S7 daily time-lapse from the farm.",
    mention_user_id=MARK_DISCORD_USER_ID,
)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def _load_config() -> dict:
    cfg_path = REPO_ROOT / "tools" / "pipeline" / "config.json"
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def _load_social_config() -> dict:
    cfg_path = REPO_ROOT / "tools" / "social" / "config.json"
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def _load_env() -> None:
    from tools.pipeline.gem_poster import load_dotenv

    load_dotenv(REPO_ROOT / ".env")


def _load_discord_client():
    from tools import discord_harvester as dh

    return dh


def _resolve_repo_path(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _reels_root(reels_cfg: dict, lane: DailyReelLane) -> Path:
    root = _resolve_repo_path(reels_cfg.get("output_root", "data/reels"))
    if lane.state_subdir:
        return root / lane.state_subdir
    return root


def _state_dirs(reels_cfg: dict, lane: DailyReelLane) -> tuple[Path, Path, Path]:
    root = _reels_root(reels_cfg, lane)
    return root / "pending", root / "posted", root / "expired"


def _ledger_status(log: logging.Logger) -> tuple[Path, int]:
    from tools.social import ledger

    social_cfg = _load_social_config()
    ledger_path = _resolve_repo_path(social_cfg["ledger_path"])
    prune_hours = int(social_cfg.get("ledger_prune_older_than_hours", 48))
    quota = int(social_cfg["ig_rolling_24h_quota"])

    ledger.prune_older_than(ledger_path, hours=prune_hours)
    recent = ledger.count_last_24h(ledger_path, platform="ig")
    slots_free = quota - recent
    log.info(
        "quota: rolling-24h IG publishes=%d / cap=%d -> slots_free=%d",
        recent,
        quota,
        slots_free,
    )
    return ledger_path, slots_free


def _append_ledger(
    ledger_path: Path,
    lane: DailyReelLane,
    identifier: str,
    result: dict,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    from tools.social import ledger

    ledger.append(
        ledger_path=ledger_path,
        lane=lane.ledger_lane,
        identifier=identifier,
        ig_media_id=result.get("media_id"),
        fb_post_id=result.get("fb_post_id"),
    )


def _count_human_reactions_on_message(
    message_id: str,
    token: str,
    dh,
    log: logging.Logger,
) -> int:
    """Fetch a Discord message and count unique non-bot reactors."""

    headers = dh.discord_headers(token)
    msg_url = f"{dh.DISCORD_API}/channels/{dh.CHANNEL_ID}/messages/{message_id}"
    try:
        resp = requests.get(msg_url, headers=headers, timeout=15)
    except requests.RequestException as exc:
        log.warning("discord: failed to fetch message %s: %s", message_id, exc)
        return 0

    if resp.status_code != 200:
        log.warning(
            "discord: message fetch %s returned http=%d",
            message_id,
            resp.status_code,
        )
        return 0

    reactions = (resp.json().get("reactions") or [])
    if not reactions:
        return 0

    humans: set[str] = set()
    for index, reaction in enumerate(reactions):
        if index > 0:
            time.sleep(0.35)
        emoji = reaction.get("emoji", {}) or {}
        name = emoji.get("name", "")
        emoji_id = emoji.get("id")
        param = f"{name}:{emoji_id}" if emoji_id else name
        url = (
            f"{dh.DISCORD_API}/channels/{dh.CHANNEL_ID}/messages/"
            f"{message_id}/reactions/{urllib.parse.quote(param)}?limit=100"
        )

        reaction_resp = None
        for _ in range(4):
            reaction_resp = requests.get(url, headers=headers, timeout=15)
            if reaction_resp.status_code == 429:
                retry = reaction_resp.json().get("retry_after", 2.0)
                time.sleep(retry + 0.25)
                continue
            break
        if reaction_resp is None or reaction_resp.status_code != 200:
            continue

        for user in reaction_resp.json():
            user_id = str(user.get("id", ""))
            if user.get("bot"):
                continue
            if user_id in dh.BOT_USER_IDS:
                continue
            humans.add(user_id)

    return len(humans)


def _make_discord_preview(mp4_path: Path, work_dir: Path) -> Path:
    """Re-encode a lower-bitrate upload copy for Discord webhooks."""

    preview = work_dir / "discord-preview.mp4"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(mp4_path),
        "-vf",
        f"scale={_DISCORD_PREVIEW_SCALE}",
        "-c:v",
        "libx264",
        "-b:v",
        "700k",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "-movflags",
        "+faststart",
        str(preview),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(
            f"preview transcode failed rc={result.returncode}: "
            f"{result.stderr[-300:].strip()}"
        )
    return preview


def _post_video_to_discord(
    mp4_path: Path,
    content: str,
    webhook_url: str,
    username: str,
    upload_filename: str,
    log: logging.Logger,
    timeout: int = 90,
) -> str | None:
    """POST an MP4 to Discord via webhook with wait=true."""

    if len(content) > 1900:
        content = content[:1900] + "..."

    upload_path = mp4_path
    tmp_dir = None
    try:
        if mp4_path.stat().st_size > _DISCORD_MAX_BYTES:
            tmp_dir = Path(tempfile.mkdtemp(prefix="reel-discord-"))
            try:
                upload_path = _make_discord_preview(mp4_path, tmp_dir)
                log.info(
                    "discord: preview encoded %s -> %s (%.1f MB)",
                    mp4_path.name,
                    upload_path.name,
                    upload_path.stat().st_size / 1024 / 1024,
                )
            except Exception as exc:
                log.warning(
                    "discord: preview transcode failed (%s); trying original",
                    exc,
                )
                upload_path = mp4_path

        wait_url = webhook_url.rstrip("/") + "?wait=true"
        with upload_path.open("rb") as file_handle:
            response = requests.post(
                wait_url,
                files={"file": (upload_filename, file_handle, "video/mp4")},
                data={
                    "payload_json": json.dumps(
                        {"username": username, "content": content}
                    )
                },
                timeout=timeout,
            )
    except requests.RequestException as exc:
        log.warning("discord: video upload failed: %s", exc)
        return None
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if 200 <= response.status_code < 300:
        message_id = response.json().get("id")
        log.info("discord: posted reel message, message_id=%s", message_id)
        return message_id

    log.warning(
        "discord: video upload rejected http=%d body=%r",
        response.status_code,
        (response.text or "")[:200],
    )
    return None


def _fetch_gem_row(db_path: Path, gem_id: int) -> dict:
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM image_archive WHERE id = ?",
            (gem_id,),
        ).fetchone()
    return dict(row) if row else {}


def _build_reel_caption(
    db_path: Path,
    gem_ids: list[int],
    fallback: str,
) -> str:
    """Build an IG caption from the best source gem plus hashtags."""

    from tools.pipeline.ig_poster import (
        _load_hashtag_library,
        build_caption,
        pick_hashtags,
    )

    best_meta: dict = {}
    best_reactions = -1
    for gem_id in gem_ids:
        row = _fetch_gem_row(db_path, gem_id)
        try:
            meta = json.loads(row.get("vlm_json") or "{}") or {}
        except json.JSONDecodeError:
            meta = {}
        reactions = row.get("discord_reactions") or 0
        if reactions > best_reactions:
            best_reactions = reactions
            best_meta = meta

    journal = (best_meta.get("caption_draft") or "").strip() or fallback
    library = _load_hashtag_library(REPO_ROOT / "tools" / "pipeline" / "hashtags.yml")
    tags = pick_hashtags(vlm_metadata=best_meta, library=library, last_n_tags_used=[])
    return build_caption(journal_body=journal, hashtags=tags)


def _select_gems(lane: DailyReelLane, db_path: Path, scheduled_cfg: dict) -> list[int]:
    from tools.pipeline import ig_selection

    selector = getattr(ig_selection, lane.selector_name)
    return selector(db_path=db_path, cfg=scheduled_cfg)


def _stitch_reel(
    lane: DailyReelLane,
    gem_ids: list[int],
    db_path: Path,
    reels_cfg: dict,
    log: logging.Logger,
) -> Path:
    from tools.pipeline.reel_stitcher import ReelStitcherError, stitch_gems_to_reel

    output_root = _reels_root(reels_cfg, lane)
    year_month = datetime.now(timezone.utc).strftime("%Y-%m")
    out_dir = output_root / year_month
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    slug = uuid.uuid4().hex[:8]
    mp4_path = out_dir / f"{lane.output_filename_prefix}-{stamp}-{slug}.mp4"

    try:
        stitch_gems_to_reel(
            gem_ids=gem_ids,
            db_path=db_path,
            config=reels_cfg,
            output_path=mp4_path,
        )
    except ReelStitcherError:
        raise

    log.info("build: MP4 ready %s (%d bytes)", mp4_path, mp4_path.stat().st_size)
    return mp4_path


def _check_pending_reels(
    lane: DailyReelLane,
    db_path: Path,
    farm_2026: Path,
    reels_cfg: dict,
    dh,
    token: str,
    dry_run: bool,
    log: logging.Logger,
) -> None:
    """Post approval-gated pending reels once their Discord preview is reacted."""

    from tools.pipeline.ig_poster import IGPosterError, post_reel_to_ig

    pending_dir, posted_dir, expired_dir = _state_dirs(reels_cfg, lane)
    pending_dir.mkdir(parents=True, exist_ok=True)
    posted_dir.mkdir(parents=True, exist_ok=True)
    expired_dir.mkdir(parents=True, exist_ok=True)

    pending_files = sorted(pending_dir.glob("*.json"))
    if not pending_files:
        log.info("pending: no pending reels to check")
        return

    now = datetime.now(timezone.utc)
    ledger_path, slots_free = _ledger_status(log)

    for pending_file in pending_files:
        try:
            state = json.loads(pending_file.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("pending: could not read %s: %s", pending_file.name, exc)
            continue

        message_id = state.get("discord_message_id", "")
        mp4_path = Path(state.get("mp4_path", ""))
        gem_ids = state.get("gem_ids") or []
        caption = state.get("caption", "")
        created_iso = state.get("created_at", "")

        if not message_id:
            log.warning("pending: %s has no discord_message_id", pending_file.name)
            continue

        try:
            created_at = datetime.fromisoformat(created_iso)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        except ValueError:
            created_at = now
        age_hours = (now - created_at).total_seconds() / 3600

        human_count = _count_human_reactions_on_message(message_id, token, dh, log)
        log.info(
            "pending: %s message_id=%s age=%.1fh reactions=%d",
            pending_file.name,
            message_id,
            age_hours,
            human_count,
        )

        if human_count == 0:
            if age_hours >= _EXPIRE_HOURS:
                log.warning(
                    "pending: %s expired (%dh old, no reactions)",
                    pending_file.name,
                    int(age_hours),
                )
                pending_file.rename(expired_dir / pending_file.name)
            else:
                log.info(
                    "pending: %s still awaiting approval (%.1fh old)",
                    pending_file.name,
                    age_hours,
                )
            continue

        if slots_free <= 0 and not dry_run:
            log.warning(
                "pending: %s approved but IG quota is full; leaving pending",
                pending_file.name,
            )
            continue

        if not mp4_path.exists() and not dry_run:
            log.error(
                "pending: %s approved but MP4 missing at %s; expiring",
                pending_file.name,
                mp4_path,
            )
            pending_file.rename(expired_dir / pending_file.name)
            continue

        log.info("pending: %s approved; posting to IG", pending_file.name)
        try:
            result = post_reel_to_ig(
                reel_mp4_path=mp4_path if not dry_run else None,
                caption=caption,
                db_path=db_path,
                farm_2026_repo_path=farm_2026,
                associated_gem_ids=gem_ids,
                dry_run=dry_run,
            )
        except IGPosterError as exc:
            log.error("pending: IG post failed (credentials): %s", exc)
            continue

        if result.get("error"):
            log.error("pending: IG post failed: %s", result["error"])
            continue

        log.info(
            "pending: posted reel to IG -> %s",
            result.get("permalink") or result.get("raw_url"),
        )
        _append_ledger(ledger_path, lane, pending_file.stem, result, dry_run)
        if not dry_run:
            slots_free -= 1
            pending_file.rename(posted_dir / pending_file.name)


def _build_and_preview(
    lane: DailyReelLane,
    db_path: Path,
    cfg: dict,
    webhook_url: str,
    dry_run: bool,
    log: logging.Logger,
) -> int:
    """Build an approval-gated reel preview and save pending state."""

    ig_cfg = cfg.get("instagram") or {}
    scheduled_cfg = ig_cfg.get("scheduled") or {}
    reels_cfg = ig_cfg.get("reels") or {}

    pending_dir, _, _ = _state_dirs(reels_cfg, lane)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pending_file = pending_dir / f"{today}.json"
    if pending_file.exists():
        log.info("build: today's reel already queued (%s); skipping", today)
        return 0

    gem_ids = _select_gems(lane, db_path, scheduled_cfg)
    if not gem_ids:
        log.info("build: quiet day; not enough gems for a reel")
        return 0

    log.info("build: stitching %d gems for %s (dry_run=%s)", len(gem_ids), today, dry_run)
    try:
        mp4_path = _stitch_reel(lane, gem_ids, db_path, reels_cfg, log)
    except Exception as exc:
        log.error("build: stitch failed: %s", exc)
        return 1

    try:
        caption = _build_reel_caption(db_path, gem_ids, lane.caption_fallback)
    except Exception as exc:
        log.exception("build: caption build failed: %s", exc)
        return 1

    if dry_run:
        log.info(
            "build: dry-run; would post %s to Discord and save pending state",
            mp4_path.name,
        )
        return 0

    frame_note = f" ({len(gem_ids)} frames)" if gem_ids else ""
    content = f"{lane.discord_title}{frame_note} - react to approve for IG\n\n{caption}"
    message_id = _post_video_to_discord(
        mp4_path=mp4_path,
        content=content,
        webhook_url=webhook_url,
        username=lane.discord_username,
        upload_filename="daily-reel-preview.mp4",
        log=log,
    )
    if not message_id:
        log.error("build: Discord preview post failed; not saving pending state")
        return 1

    pending_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "lane": lane.lane_id,
        "date": today,
        "discord_message_id": message_id,
        "mp4_path": str(mp4_path),
        "gem_ids": gem_ids,
        "caption": caption,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    pending_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    log.info("build: pending state saved -> %s", pending_file.name)
    return 0


def _build_publish_and_notify(
    lane: DailyReelLane,
    db_path: Path,
    farm_2026: Path,
    cfg: dict,
    webhook_url: str,
    dry_run: bool,
    log: logging.Logger,
) -> int:
    """Build an auto-published reel and send an informational Discord notice."""

    from tools.pipeline.ig_poster import IGPosterError, post_reel_to_ig

    ig_cfg = cfg.get("instagram") or {}
    scheduled_cfg = ig_cfg.get("scheduled") or {}
    reels_cfg = ig_cfg.get("reels") or {}

    _, posted_dir, _ = _state_dirs(reels_cfg, lane)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    posted_file = posted_dir / f"{today}.json"
    if posted_file.exists():
        log.info("build: today's %s reel already posted; skipping", lane.lane_id)
        return 0

    ledger_path, slots_free = _ledger_status(log)
    if slots_free <= 0 and not dry_run:
        log.warning("build: IG quota is full; skipping %s reel", lane.lane_id)
        return 0

    gem_ids = _select_gems(lane, db_path, scheduled_cfg)
    if not gem_ids:
        log.info("build: quiet day; not enough S7 frames for a time-lapse reel")
        return 0

    log.info("build: stitching %d S7 frames for %s", len(gem_ids), today)
    try:
        mp4_path = _stitch_reel(lane, gem_ids, db_path, reels_cfg, log)
    except Exception as exc:
        log.error("build: stitch failed: %s", exc)
        return 1

    try:
        caption = _build_reel_caption(db_path, gem_ids, lane.caption_fallback)
    except Exception as exc:
        log.exception("build: caption build failed: %s", exc)
        return 1

    try:
        result = post_reel_to_ig(
            reel_mp4_path=mp4_path if not dry_run else None,
            caption=caption,
            db_path=db_path,
            farm_2026_repo_path=farm_2026,
            associated_gem_ids=gem_ids,
            dry_run=dry_run,
        )
    except IGPosterError as exc:
        log.error("build: IG post failed (credentials): %s", exc)
        return 3

    if result.get("error"):
        log.error("build: IG post failed: %s", result["error"])
        return 1

    _append_ledger(ledger_path, lane, today, result, dry_run)

    if dry_run:
        log.info("build: dry-run OK; would post -> %s", result.get("raw_url"))
        return 0

    mention = f"<@{lane.mention_user_id}> " if lane.mention_user_id else ""
    frame_note = f" ({len(gem_ids)} frames)" if gem_ids else ""
    permalink = result.get("permalink") or result.get("raw_url") or ""
    notice = (
        f"{mention}{lane.discord_title}{frame_note} posted to IG\n"
        f"{permalink}\n\n{caption}"
    ).strip()
    notice_message_id = _post_video_to_discord(
        mp4_path=mp4_path,
        content=notice,
        webhook_url=webhook_url,
        username=lane.discord_username,
        upload_filename="s7-daily-timelapse-reel.mp4",
        log=log,
    )

    posted_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "lane": lane.lane_id,
        "date": today,
        "discord_notice_message_id": notice_message_id,
        "ig_permalink": result.get("permalink"),
        "ig_media_id": result.get("media_id"),
        "fb_post_id": result.get("fb_post_id"),
        "mp4_path": str(mp4_path),
        "gem_ids": gem_ids,
        "caption": caption,
        "posted_at": result.get("posted_at") or datetime.now(timezone.utc).isoformat(),
    }
    posted_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    log.info("build: posted state saved -> %s", posted_file.name)

    if not notice_message_id:
        log.error("build: IG post succeeded but Discord notice failed")
        return 1
    log.info("build: Discord notice sent -> %s", notice_message_id)
    return 0


def run_lane(
    lane: DailyReelLane,
    dry_run: bool = False,
    skip_build: bool = False,
) -> int:
    """Run one scheduled Reel lane tick."""

    log = logging.getLogger(lane.log_name)
    _load_env()

    cfg = _load_config()
    ig_cfg = cfg.get("instagram") or {}
    reels_cfg = ig_cfg.get("reels") or {}
    db_path = _resolve_repo_path(cfg["guardian_db_path"])
    farm_2026_value = ig_cfg.get("farm_2026_repo_path", "")
    farm_2026 = Path(farm_2026_value).expanduser() if farm_2026_value else REPO_ROOT
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")

    if not webhook_url:
        log.error("DISCORD_WEBHOOK_URL missing from environment")
        return 3
    if not db_path.exists():
        log.error("guardian db not found: %s", db_path)
        return 1
    if not farm_2026_value and not dry_run:
        log.error("instagram.farm_2026_repo_path missing from config")
        return 1
    if not farm_2026.exists() and not dry_run:
        log.error("farm_2026 repo not found: %s", farm_2026)
        return 1

    if lane.approval_required:
        dh = _load_discord_client()
        try:
            token = dh.load_bot_token()
        except Exception as exc:
            log.error("discord bot token missing: %s", exc)
            return 3

        _check_pending_reels(
            lane=lane,
            db_path=db_path,
            farm_2026=farm_2026,
            reels_cfg=reels_cfg,
            dh=dh,
            token=token,
            dry_run=dry_run,
            log=log,
        )

    if skip_build:
        log.info("--skip-build set; skipping reel construction")
        return 0

    if lane.approval_required:
        return _build_and_preview(
            lane=lane,
            db_path=db_path,
            cfg=cfg,
            webhook_url=webhook_url,
            dry_run=dry_run,
            log=log,
        )

    return _build_publish_and_notify(
        lane=lane,
        db_path=db_path,
        farm_2026=farm_2026,
        cfg=cfg,
        webhook_url=webhook_url,
        dry_run=dry_run,
        log=log,
    )


def main(lane: DailyReelLane, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=lane.description)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build locally; skip Discord upload, IG post, and ledger writes.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Only run the pending approval check when this lane has one.",
    )
    args = parser.parse_args(argv)
    setup_logging()
    return run_lane(lane=lane, dry_run=args.dry_run, skip_build=args.skip_build)
