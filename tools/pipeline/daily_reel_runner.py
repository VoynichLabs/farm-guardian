# Author: Claude Sonnet 4.6; Claude Opus 4.7 (22-June-2026 — duo2 timelapse lane); Claude Fable 5 (16-Jul-2026 — mba-cam lane relabeled brooder→turkey pen, v2.46.0; Codex captions for vlm_bypass lanes + posted-caption dedup + tag rotation from ledger + chicks bucket retired, v2.47.0; D8 codex_reel_curator wired into the s7-daily lane + opener pacing hook, D10 CAMERA_OF_THE_DAY_POOL/pick_camera_of_the_day rotation, v2.48.0); Claude Opus 4.8 (22-Jul-2026 — per-lane seconds_per_frame override so the two Reolink time-lapse lanes play fast without speeding up the s7/mixed lanes, v2.50.1); Claude Fable 5 (23-Jul-2026 — Codex subscription lapsed: all caption synthesis moved to the local VLM, timelapse lanes no longer short-circuit to a literal, BRAND_RULES extracted to caption_brand.py, s7 Codex frame-curation removed, v2.51.5)
# Date: 09-May-2026 (updated 09-May-2026 — landscape mode + LM Studio caption synthesis + 4 timelapse lanes; 10-May-2026 — GWTC approval gate; 22-June-2026 — DUO2_TIMELAPSE_LANE; 16-Jul-2026 — D8/D10; 22-Jul-2026 — per-lane pacing override)
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
#          16-Jul-2026 (D8): the s7-daily lane keeps a one-frame "hook"
#          duplicate at the front of the stitch list (its Codex frame
#          curation was removed 23-Jul-2026, see v2.51.5). 16-Jul-2026
#          (D10): CAMERA_OF_THE_DAY_POOL + pick_camera_of_the_day()
#          give the per-camera timelapse lanes a shared rotation entry
#          point (scripts/ig-camera-of-the-day-reel.py) without
#          touching the existing standalone lanes/plists.
# SRP/DRY check: Pass - one responsibility is "run a configured daily
#                Reel lane." Reuses ig_selection, reel_stitcher,
#                ig_poster, discord_harvester, caption_brand, and
#                tools.social.ledger.

from __future__ import annotations

import argparse
import json
import logging
import re
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.caption_brand import BRAND_RULES, brand_violations  # noqa: E402

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
    # landscape_mode: output 16:9 (1920×1080) instead of 9:16 center-crop.
    # Use for vlm_bypass cameras that capture 16:9 frames.
    landscape_mode: bool = False
    # discord_preview_scale: ffmpeg scale filter for the Discord upload copy.
    # Portrait lanes use "540:960" (9:16); landscape lanes use "960:540".
    discord_preview_scale: str = "540:960"
    # seconds_per_frame: per-lane override of reels.seconds_per_frame. None =
    # inherit the global config value. 22-Jul-2026: Boss wants the two Reolink
    # time-lapse lanes (house-yard, duo2) to play FAST — many frames, each on
    # screen briefly — while the s7/mixed lanes keep their original slower
    # 1.0s pacing. Speed was briefly changed globally, which wrongly sped up
    # the s7 reel; this field is what keeps the change scoped to one lane.
    seconds_per_frame: Optional[float] = None


MIXED_DAILY_REEL_LANE = DailyReelLane(
    lane_id="daily",
    log_name="ig-daily-reel",
    description="Build + auto-post daily IG Reel.",
    selector_name="select_daily_reel_gems",
    state_subdir="",
    output_filename_prefix="reel-daily",
    discord_username="farm-reel",
    discord_title="Daily reel",
    # Approval gate removed 21-May-2026 per Boss: the daily reel is built from
    # gems he has ALREADY reacted/vetted, so the second reel-level reaction was a
    # redundant bottleneck. Now auto-publishes + sends a Discord notice. (As of
    # the same date ALL reel lanes auto-publish — Boss waived the outdoor gates.)
    approval_required=False,
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

S7_BACKLOG_REEL_LANE = DailyReelLane(
    lane_id="s7-backlog",
    log_name="ig-s7-backlog-reel",
    description="Auto-post one S7 backlog Reel per day, oldest date first.",
    selector_name="select_s7_backlog_reel_gems",
    state_subdir="s7-backlog",
    output_filename_prefix="reel-s7-backlog",
    discord_username="farm-reel-s7",
    discord_title="S7 backlog time-lapse",
    approval_required=False,
    ledger_lane="s7-backlog-reel",
    caption_fallback="A look back at the nesting box.",
    mention_user_id=MARK_DISCORD_USER_ID,
)


MBA_CAM_TIMELAPSE_LANE = DailyReelLane(
    lane_id="mba-cam-timelapse",
    log_name="ig-mba-cam-timelapse-reel",
    description="Auto-post daily MBA-cam turkey-pen time-lapse Reel.",
    selector_name="select_mba_cam_timelapse_gems",
    state_subdir="mba-cam-timelapse",
    output_filename_prefix="reel-mba-cam-timelapse",
    discord_username="farm-reel-mba-cam",
    discord_title="MBA-cam turkey-pen time-lapse",
    approval_required=False,
    ledger_lane="mba-cam-timelapse-reel",
    caption_fallback="A day in the turkey pen.",
    mention_user_id=MARK_DISCORD_USER_ID,
    landscape_mode=True,
    discord_preview_scale="960:540",
)

GWTC_TIMELAPSE_LANE = DailyReelLane(
    lane_id="gwtc-timelapse",
    log_name="ig-gwtc-timelapse-reel",
    description="Build a Discord-approved GWTC coop-roof time-lapse Reel.",
    selector_name="select_gwtc_timelapse_gems",
    state_subdir="gwtc-timelapse",
    output_filename_prefix="reel-gwtc-timelapse",
    discord_username="farm-reel-gwtc",
    discord_title="GWTC coop-roof time-lapse",
    # 21-May-2026: privacy gate waived by Boss (his own property/cameras, no
    # concern). Auto-publishes like the other timelapse lanes.
    approval_required=False,
    ledger_lane="gwtc-timelapse-reel",
    caption_fallback="A day at the coop.",
    mention_user_id=MARK_DISCORD_USER_ID,
    landscape_mode=True,
    discord_preview_scale="960:540",
)

USB_CAM_TIMELAPSE_LANE = DailyReelLane(
    lane_id="usb-cam-timelapse",
    log_name="ig-usb-cam-timelapse-reel",
    description="Auto-post daily USB-cam coop-run time-lapse Reel.",
    selector_name="select_usb_cam_timelapse_gems",
    state_subdir="usb-cam-timelapse",
    output_filename_prefix="reel-usb-cam-timelapse",
    discord_username="farm-reel-usb-cam",
    discord_title="USB-cam coop-run time-lapse",
    approval_required=False,
    ledger_lane="usb-cam-timelapse-reel",
    caption_fallback="A day in the coop run.",
    mention_user_id=MARK_DISCORD_USER_ID,
    landscape_mode=True,
    discord_preview_scale="960:540",
)

DOMINATOR_CAM_TIMELAPSE_LANE = DailyReelLane(
    lane_id="dominator-cam-timelapse",
    log_name="ig-dominator-cam-timelapse-reel",
    description="Auto-post daily Dominator-cam time-lapse Reel when camera is live.",
    selector_name="select_dominator_cam_timelapse_gems",
    state_subdir="dominator-cam-timelapse",
    output_filename_prefix="reel-dominator-cam-timelapse",
    discord_username="farm-reel-dominator",
    discord_title="Dominator-cam time-lapse",
    approval_required=False,
    ledger_lane="dominator-cam-timelapse-reel",
    caption_fallback="A day on the farm.",
    mention_user_id=MARK_DISCORD_USER_ID,
    landscape_mode=True,
    discord_preview_scale="960:540",
)

HOUSE_YARD_CAM_TIMELAPSE_LANE = DailyReelLane(
    lane_id="house-yard-cam-timelapse",
    log_name="ig-house-yard-cam-timelapse-reel",
    description="Build a Discord-approved house-yard Reolink time-lapse Reel.",
    selector_name="select_house_yard_cam_timelapse_gems",
    state_subdir="house-yard-cam-timelapse",
    output_filename_prefix="reel-house-yard-cam-timelapse",
    discord_username="farm-reel-house-yard",
    discord_title="House-yard time-lapse",
    # 21-May-2026: privacy gate waived by Boss; auto-publishes like the others.
    approval_required=False,
    ledger_lane="house-yard-cam-timelapse-reel",
    caption_fallback="A day in the yard.",
    mention_user_id=MARK_DISCORD_USER_ID,
    landscape_mode=True,
    discord_preview_scale="960:540",
    seconds_per_frame=0.4,
)

# 22-June-2026 (Claude Opus 4.7): duo2 (Reolink Duo 2 WiFi) time-lapse lane, the
# stationary 180-degree panoramic complement to the house-yard E1 PTZ. Mirrors the
# other reolink_snapshot vlm_bypass timelapse lanes (raw-tier sharpness selection,
# auto-publish, daily run). landscape_mode=True since the Duo 2 captures a wide
# stitched panoramic (~2.67:1) — reel_stitcher letterboxes it inside the 16:9
# (1920x1080) reel frame undistorted (verified v2.43.0).
DUO2_TIMELAPSE_LANE = DailyReelLane(
    lane_id="duo2-timelapse",
    log_name="ig-duo2-timelapse-reel",
    description="Auto-post daily duo2 Reolink panoramic time-lapse Reel.",
    selector_name="select_duo2_timelapse_gems",
    state_subdir="duo2-timelapse",
    output_filename_prefix="reel-duo2-timelapse",
    discord_username="farm-reel-duo2",
    discord_title="Duo2 panoramic time-lapse",
    approval_required=False,
    ledger_lane="duo2-timelapse-reel",
    caption_fallback="A day across the farm.",
    mention_user_id=MARK_DISCORD_USER_ID,
    landscape_mode=True,
    discord_preview_scale="960:540",
    seconds_per_frame=0.4,
)


# D10 (16-Jul-2026): rotation pool for the consolidated "camera of the day"
# timelapse lane (farm-2026's 16-Jul-2026-birdcatraz-era-refresh-plan.md,
# Part D10). The six lanes ending in _TIMELAPSE_LANE defined above are:
# MBA_CAM, GWTC, USB_CAM, DOMINATOR_CAM, HOUSE_YARD_CAM, DUO2. Pool
# selection, camera by camera:
#   - MBA_CAM, DOMINATOR_CAM, DUO2: live plists today (20:30/21:15/21:20) —
#     exactly the evening stack this rotation is meant to thin. IN.
#   - USB_CAM: live plist (21:00) too, so IN by the same logic. Boss says
#     the camera is physically disconnected right now (separate hardware
#     issue, out of scope here) — its selector already no-ops to an empty
#     reel on a quiet day (see the "not enough frames" skip below), so the
#     pool stays ready for the moment it's reconnected without another
#     code change.
#   - GWTC: EXCLUDED. `cameras.gwtc.enabled` is `false` in config.json
#     today (confirmed live), so a gwtc timelapse would find zero frames
#     every single time it's picked — a dead rotation slot, not a
#     consolidation win.
#   - HOUSE_YARD_CAM: EXCLUDED. Unlike the other four, this lane has never
#     had a plist (checked both deploy/ig-scheduled/ and the live
#     ~/Library/LaunchAgents/) — it has never posted to IG. The lanes this
#     rotation consolidates are the ones already stacking in the evening
#     window; house-yard isn't part of that stack, so folding it in here
#     would be a new content surface riding in on a consolidation change,
#     not a reduction of one. Left out deliberately — see
#     followups_for_main_session if Boss wants it added to the pool later.
CAMERA_OF_THE_DAY_POOL: tuple[DailyReelLane, ...] = (
    MBA_CAM_TIMELAPSE_LANE,
    USB_CAM_TIMELAPSE_LANE,
    DOMINATOR_CAM_TIMELAPSE_LANE,
    DUO2_TIMELAPSE_LANE,
)


def pick_camera_of_the_day(
    pool: tuple[DailyReelLane, ...] = CAMERA_OF_THE_DAY_POOL,
    now: Optional[datetime] = None,
) -> DailyReelLane:
    """Deterministically pick one lane from `pool` for "today".

    Picks by day-of-year modulo pool size: the same UTC calendar day always
    maps to the same camera (idempotent across retries/re-runs within a
    day) and the pool cycles evenly across the year. `now` defaults to the
    current UTC time; pass an explicit value for testing.
    """
    now = now or datetime.now(timezone.utc)
    return pool[now.timetuple().tm_yday % len(pool)]


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


def _make_discord_preview(mp4_path: Path, work_dir: Path, scale: str = _DISCORD_PREVIEW_SCALE) -> Path:
    """Re-encode a lower-bitrate upload copy for Discord webhooks.

    scale: ffmpeg scale filter string. Portrait lanes use "540:960";
    landscape lanes use "960:540".
    """
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
        f"scale={scale}",
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
    preview_scale: str = _DISCORD_PREVIEW_SCALE,
) -> str | None:
    """POST an MP4 to Discord via webhook with wait=true.

    preview_scale: passed to _make_discord_preview if the file exceeds
    the Discord size limit. Use "540:960" for portrait, "960:540" for
    landscape lanes.
    """
    if len(content) > 1900:
        content = content[:1900] + "..."

    upload_path = mp4_path
    tmp_dir = None
    try:
        if mp4_path.stat().st_size > _DISCORD_MAX_BYTES:
            tmp_dir = Path(tempfile.mkdtemp(prefix="reel-discord-"))
            try:
                upload_path = _make_discord_preview(mp4_path, tmp_dir, scale=preview_scale)
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
        recent_tags_used,
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
    # Reels always draw from the reel platform bucket + farm content buckets,
    # regardless of which gem's scene won the best-metadata selection.
    # v2.47.0: chicks bucket retired (the flock is grown); tag rotation now
    # actually fed from the posted-caption ledger instead of a hardcoded [].
    tags = pick_hashtags(
        vlm_metadata=best_meta,
        library=library,
        last_n_tags_used=recent_tags_used(db_path),
        buckets_override=["reel", "chickens", "homestead"],
    )
    return build_caption(journal_body=journal, hashtags=tags)


FARM_DIARY_DIR = Path.home() / "Documents" / "GitHub" / "farm-2026" / "content" / "diary"

# The flock roster — the living birds' names, breeds and personalities. This is
# the durable source of NAMES for captions; the diary is not. As of 23-Jul-2026
# the diary has one usable entry left (see _todays_observations) and every named
# bird in recent captions came from it, so when it ages out the captions would
# have lost names entirely. This file is actively maintained (last touched
# 22-Jul-2026) and carries 35 birds.
FLOCK_PROFILES_PATH = (
    Path.home() / "Documents" / "GitHub" / "farm-2026" / "content" / "flock-profiles.json"
)


# Diary entries older than this are treated as stale and never injected into
# captions. A frozen diary folder (no new entries for weeks) must not surface a
# month-old event — e.g. the healed "buff buttrot" skin irritation — as if it
# were current farm news.
FARM_CONTEXT_MAX_AGE_DAYS = 21

# A dated health-incident entry that reads as resolved must not resurface even
# when it still falls inside the freshness window.
#
# 23-Jul-2026: these are matched on WORD BOUNDARIES, not as bare substrings.
# Plain `"resolved" in text` also fires on "unresolved" — the exact opposite
# meaning — and it really happened: the 23-Jul entry said "Origin unresolved"
# about a mystery cockerel and the whole day's diary was silently dropped from
# the caption context as a "resolved health incident".
_RESOLVED_MARKERS = (
    "cleared up", "cleared per", "has cleared", "resolved", "healed",
    "all clear", "no longer", "fully recovered",
)

_RESOLVED_RE = re.compile(
    r"(?<!un)\b(" + "|".join(re.escape(m) for m in _RESOLVED_MARKERS) + r")\b",
    re.IGNORECASE,
)

_DIARY_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _diary_date(path: Path) -> Optional[date]:
    """Parse the entry date from a diary filename. Handles both
    ``2026-06-10-...md`` (ISO) and ``28-may-2026-...md`` (day-month-year).
    Returns None when no date can be parsed from the name.
    """
    import re

    name = path.name.lower()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", name)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})-([a-z]{3,})-(\d{4})", name)
    if m:
        mon = _DIARY_MONTHS.get(m.group(2)[:3])
        if mon:
            try:
                return date(int(m.group(3)), mon, int(m.group(1)))
            except ValueError:
                return None
    return None


def _load_farm_context(limit: int = 3, char_cap: int = 600) -> str:
    """Recent diary entries to inject into the caption prompt — named birds,
    hatch progress, daily wins. Best-effort: returns '' on any failure so the
    caption pipeline always succeeds.

    Freshness is judged by the DATE in the filename — not file mtime, which
    lies when a file is re-committed or touched. Entries older than
    FARM_CONTEXT_MAX_AGE_DAYS, or that read as a resolved health incident, are
    dropped so a stale event never gets captioned as current news.
    """
    try:
        if not FARM_DIARY_DIR.is_dir():
            return ""
        today = datetime.now(timezone.utc).date()
        dated: list[tuple[date, Path]] = []
        for p in FARM_DIARY_DIR.glob("*.md"):
            d = _diary_date(p)
            if d is None:
                # Unknown filename date → fall back to mtime so it still sorts,
                # but a properly-dated recent entry will almost always beat it.
                d = datetime.fromtimestamp(
                    p.stat().st_mtime, timezone.utc
                ).date()
            if (today - d).days > FARM_CONTEXT_MAX_AGE_DAYS:
                continue
            dated.append((d, p))
        dated.sort(key=lambda t: t[0], reverse=True)
        chunks: list[str] = []
        for _d, f in dated:
            text = f.read_text(encoding="utf-8", errors="ignore").strip()
            if _RESOLVED_RE.search(text):
                continue
            chunks.append(text[:char_cap].strip())
            if len(chunks) >= limit:
                break
        return "\n\n---\n\n".join(chunks)
    except Exception:
        return ""


def _living_flock_roster(log: logging.Logger, limit: int = 14) -> str:
    """Names of birds that are ALIVE, for captions to use.

    ⚠️ The deceased filter is the whole point of this function, not a nicety.
    flock-profiles.json tracks the flock's history, so it carries birds that
    have died — 7 of 35 at the time of writing, including Henrietta and
    Birdatha, both of whom read as perfectly ordinary hens from their name and
    breed alone. Handing the raw roster to the caption model would have it
    writing cheerful present-tense captions about dead birds. Only
    status == "active" is ever surfaced, and the death fields
    (deceased_date / cause_of_death) are never included at all.

    Best-effort: returns "" on any failure, so captions degrade to unnamed
    rather than break.
    """
    try:
        data = json.loads(FLOCK_PROFILES_PATH.read_text(encoding="utf-8"))
        birds = data.get("flock_birds") or []
    except Exception as exc:
        log.info("caption: flock roster unavailable (%s)", exc)
        return ""

    lines: list[str] = []
    for bird in birds:
        if not isinstance(bird, dict):
            continue
        # Strict allowlist on status: anything not explicitly "active" is
        # excluded, so an unrecognised future status can never leak a dead
        # bird into a caption.
        if (bird.get("status") or "").strip().lower() != "active":
            continue
        if bird.get("deceased_date") or bird.get("cause_of_death"):
            continue
        name = (bird.get("name") or "").strip()
        if not name:
            continue
        bits = [b for b in (bird.get("breed"), bird.get("color_description")) if b]
        detail = f" — {', '.join(str(b)[:60] for b in bits)}" if bits else ""
        lines.append(f"{name}{detail}")
        if len(lines) >= limit:
            break

    if not lines:
        return ""
    return (
        "THE FLOCK (living birds you may name — use a name only if it fits "
        "what the reel shows; never invent one):\n- "
        + "\n- ".join(lines)
        + "\n\n"
    )


def _todays_observations(db_path: Path, log: logging.Logger, hours: int = 24) -> str:
    """Fresh farm facts for today, derived from frames the pipeline already
    VLM-enriched. Costs one SQL query and no model calls.

    Why this exists (23-Jul-2026): captions were built almost entirely from
    _load_farm_context(), and the diary had gone stale — 23 entries on disk but
    only ONE inside the 21-day window (2026-07-09, roost order). Every reel
    therefore rewrote that same anecdote; four of the last five posted captions
    were the same sentence rephrased, and the do-not-repeat list could only
    push the model into another paraphrase because it had nothing else to say.
    Meanwhile the pipeline was recording thousands of enriched observations a
    day and none of it reached the caption. This surfaces that.

    Deliberately farm-wide rather than per-camera: the yard lanes are
    vlm_bypass so their own frames carry no observations, and "what the birds
    did today" is true of the flock regardless of which camera watched. The
    phrasing below is careful not to claim a behaviour happened in this
    particular reel's frames.
    """
    # ts is stored ISO-with-T ("2026-07-23T09:43:07+00:00"). Comparing it
    # against SQLite's datetime('now',...) (space-separated) is a trap: "T"
    # sorts after " ", so every row sharing the cutoff's date compares greater
    # and the window silently widens to ~24-48h. Build the bound in the stored
    # format instead — correct AND still index-friendly on ts.
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=int(hours))
    ).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = con.execute(
                """SELECT activity, bird_count, lighting, any_special_chick
                   FROM image_archive
                   WHERE ts > ?
                     AND activity IS NOT NULL AND activity != ''""",
                (cutoff,),
            ).fetchall()
        finally:
            con.close()
    except Exception as exc:
        log.info("caption: today's observations unavailable (%s)", exc)
        return ""

    if not rows:
        return ""

    # "none-visible" dominates raw counts and says nothing worth captioning.
    acts: dict[str, int] = {}
    for activity, _bc, _lt, _sc in rows:
        if activity and activity != "none-visible":
            acts[activity] = acts.get(activity, 0) + 1
    ranked = sorted(acts.items(), key=lambda kv: kv[1], reverse=True)

    facts: list[str] = []
    if ranked:
        common = [a for a, _n in ranked[:3]]
        facts.append("mostly " + ", ".join(common))
        # Rare behaviours are the interesting ones — a single dust-bath is
        # better caption material than the thousandth foraging frame.
        rare = [a for a, n in ranked if n <= max(3, len(rows) // 500)]
        if rare:
            facts.append("also glimpsed: " + ", ".join(rare[:3]))

    counts = [bc for _a, bc, _lt, _sc in rows if isinstance(bc, int) and bc > 0]
    if counts:
        facts.append(f"up to {max(counts)} birds in frame at once")

    dim = sum(1 for _a, _bc, lt, _sc in rows if lt == "dim")
    if dim and len(rows) > 20:
        share = dim / len(rows)
        if share > 0.35:
            facts.append("a lot of dim, low light today")
        elif share > 0.05:
            facts.append("bright most of the day, dim at the edges")

    if sum(1 for _a, _bc, _lt, sc in rows if sc):
        facts.append("one of the special birds turned up")

    if not facts:
        return ""
    return (
        "TODAY ON THE FARM — observed across the cameras in the last 24h. This "
        "is the freshest material available; prefer it over the older diary "
        "below, and do not claim these happened in this exact reel:\n- "
        + "\n- ".join(facts)
        + "\n\n"
    )


def _generate_reel_caption(
    db_path: Path,
    gem_ids: list[int],
    fallback: str,
    cfg: dict,
    log: logging.Logger,
) -> str:
    """Synthesize a reel caption using LM Studio when available.

    Collects caption_drafts from VLM-enriched frames, then calls the
    currently-loaded model to synthesize a single cohesive caption. When a
    lane has no drafts (raw-tier / vlm_bypass timelapse cameras) the model
    writes from the lane's scene hint plus the farm diary instead — as of
    v2.51.5 that case is a different prompt, not a bail-out.

    Falls back to _build_reel_caption()'s literal only when:
      - LM Studio is unreachable
      - The expected VLM model isn't loaded
      - The chat call itself fails

    This is strictly better-effort: the caption pipeline always succeeds
    and the pipeline never blocks on LM Studio availability.

    Per docs/13-Apr-2026-lm-studio-reference.md: always check /v1/models
    before calling /v1/chat/completions; never auto-load; pass
    reasoning_effort=none to suppress thinking budget on Qwen3.
    """
    import requests as _req

    # Collect caption_drafts from up to 20 frames (prompt-length guard).
    drafts: list[str] = []
    for gem_id in gem_ids[:20]:
        row = _fetch_gem_row(db_path, gem_id)
        try:
            meta = json.loads(row.get("vlm_json") or "{}") or {}
        except json.JSONDecodeError:
            meta = {}
        draft = (meta.get("caption_draft") or "").strip()
        if draft:
            drafts.append(draft)

    # NOTE (23-Jul-2026, v2.51.5): the Codex path that used to run before this
    # is gone — the OpenAI Codex subscription lapsed, so `codex exec` returned
    # 401 on every reel build from ~07-Jul onward. For the mixed lane that was
    # invisible (it fell through to LM Studio below), but the vlm_bypass
    # timelapse lanes returned the hardcoded literal instead, so house-yard /
    # s7 / duo2 posted near-identical captions for two weeks. Both lane types
    # now synthesize on the local VLM, and the no-drafts case is handled by
    # the scene-hint prompt below rather than by an early return.
    lm_base = cfg.get("lm_studio_base", "http://localhost:1234")
    # Default must track the live production VLM. If it drifts stale and the
    # config key ever goes missing, the guard below sees "not loaded" and falls
    # back — but a stale name here is one edit away from requesting an unloaded
    # model, which /v1/chat/completions silently auto-loads (the 2026-04-13
    # incident that took the box down).
    vlm_model = cfg.get("vlm_model_id", "qwen/qwen3-vl-4b")

    # Verify LM Studio is up and the right model is loaded before calling.
    try:
        resp = _req.get(f"{lm_base}/v1/models", timeout=5)
        if resp.status_code != 200:
            raise RuntimeError(f"http={resp.status_code}")
        loaded_ids = [m.get("id") for m in resp.json().get("data", [])]
        if vlm_model not in loaded_ids:
            log.info(
                "lm_studio: %s not loaded (loaded=%s); using fallback caption",
                vlm_model, loaded_ids,
            )
            return _build_reel_caption(db_path, gem_ids, fallback)
    except Exception as exc:
        log.info("lm_studio: unreachable (%s); using fallback caption", exc)
        return _build_reel_caption(db_path, gem_ids, fallback)

    farm_context = _load_farm_context()
    # v2.47.0: do-not-repeat list from the posted-caption ledger — before
    # this, consecutive reels rephrased the same diary fact daily.
    try:
        from tools.pipeline.ig_poster import recent_posted_captions as _recent
        avoid_block_items = _recent(db_path, limit=5)
    except Exception:
        avoid_block_items = []
    # 23-Jul-2026: "do not repeat subjects or phrasing" was too weak. With only
    # one usable diary entry left, the model had nothing else to reach for and
    # simply paraphrased — four of the last five posted captions were the same
    # perch sentence reworded. Now it is told explicitly to change SUBJECT.
    avoid_block = (
        "ALREADY POSTED — these are the most recent captions on the account. "
        "Your caption must be about a DIFFERENT subject. Rewording any of "
        "these, or reusing their central image or anecdote, is a failure — "
        "pick something else to talk about:\n"
        + "\n".join(f"- {a[:160]}" for a in avoid_block_items)
        + "\n\n"
        if avoid_block_items
        else ""
    )
    todays_block = _todays_observations(db_path, log)
    roster_block = _living_flock_roster(log)
    # The diary is BACKGROUND, not the subject. It is far richer than a
    # one-line scene hint, so if it leads the prompt the model writes about
    # the diary and ignores what the reel actually shows — verified
    # 23-Jul-2026: three different scene hints produced byte-identical
    # captions about a perch incident from the diary. Subject goes first and
    # is stated as authoritative; the diary is explicitly demoted to a source
    # of names/context that must fit the scene.
    # The diary is now the LAST resort, not the lead. It holds the only named
    # birds we have (the VLM's individuals_visible_csv is still generic
    # "adult"/"chick"), so it stays in — but it is one aging entry, and letting
    # it lead is exactly what produced weeks of identical captions.
    context_block = (
        f"OLDER BACKGROUND — farm diary. May be days old. Use it only for bird "
        f"names or standing context, and only if it fits. Do NOT make a stale "
        f"diary event the subject of the caption:\n\n{farm_context}\n\n"
        if farm_context
        else ""
    )
    # Two shapes of source material. VLM-enriched lanes give us per-frame
    # caption_drafts; the vlm_bypass timelapse lanes (house-yard, s7, duo2)
    # give us nothing but the lane's scene hint. Before v2.51.5 the second
    # case never reached the model at all — it short-circuited to the
    # hardcoded literal, which is why those lanes posted the same text daily.
    if drafts:
        subject_block = (
            "THE REEL — descriptions of individual frames, in order:\n\n"
            + "\n".join(f"- {d}" for d in drafts)
            + "\n\n"
        )
        specificity = "Be warm and specific to what is actually in the frames. "
    else:
        # vlm_bypass lanes: house-yard and duo2, both permanently aimed at the
        # same yard. Nothing described their frames at capture and the scene
        # never changes, so the caption is carried by the farm diary below —
        # that is the only thing that differs day to day, and per Boss
        # (23-Jul-2026) a yard time-lapse needs nothing more. Deliberately NOT
        # sending frames to the VLM here: the pixels are the same every day,
        # so it would buy nothing for three extra vision calls per reel.
        subject_block = (
            "THE REEL — a fixed-angle time-lapse of the same yard across the "
            f"day.\nScene: {fallback}\n\n"
        )
        specificity = (
            "Be warm and plausible for a day in the yard. Do not invent "
            "events or details you were not given. "
        )

    prompt = (
        "You are writing a caption for an Instagram Reel from a small farm.\n\n"
        f"{BRAND_RULES}\n"
        f"{subject_block}"
        f"{todays_block}"
        f"{roster_block}"
        f"{context_block}"
        f"{avoid_block}"
        "Write a single short caption (1-2 sentences, no hashtags) about the "
        f"reel described above. {specificity}"
        "Lead with something from TODAY where you can — a behaviour the birds "
        "were actually seen doing, or how the day looked — rather than "
        "restating older diary news. Reply with the caption text only — no "
        "preamble, quotes, or commentary."
    )

    # Generate, then VERIFY. The brand prohibitions are not left to
    # instruction-following: this model has demonstrably produced "no one
    # looking at the camera, no one chasing a hawk" while being told not to
    # mention either. A violating caption is regenerated, not patched —
    # deleting "hawk" from a sentence about hawks leaves a sentence about
    # nothing. If it still violates, we post the deterministic literal, which
    # is dull but always safe.
    vlm_timeout = int(cfg.get("vlm_timeout_seconds", 300))
    attempts = 3
    for attempt in range(1, attempts + 1):
        this_prompt = prompt
        if attempt > 1:
            # Corrective nudge stays positive — restating the banned words is
            # what plants them in the first place.
            this_prompt = prompt + (
                "\n\nYour previous attempt drifted off-voice. Write it again "
                "as the farmer simply describing the birds' day in plain "
                "words, with nothing about how the scene was observed and "
                "nothing about anything threatening them."
            )
        try:
            resp = _req.post(
                f"{lm_base}/v1/chat/completions",
                json={
                    "model": vlm_model,
                    "messages": [{"role": "user", "content": this_prompt}],
                    "max_tokens": 100,
                    "temperature": 0.7,
                    "reasoning_effort": "none",
                },
                timeout=vlm_timeout,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"chat endpoint returned http={resp.status_code}")
            synthesized = resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            log.warning("lm_studio: caption synthesis failed (%s); using fallback", exc)
            return _build_reel_caption(db_path, gem_ids, fallback)

        bad = brand_violations(synthesized)
        if not bad:
            log.info("lm_studio: synthesized reel caption: %r", synthesized[:80])
            return _wrap_caption_with_hashtags(db_path, gem_ids, synthesized)
        log.warning(
            "caption: brand violation %s on attempt %d/%d: %r",
            bad, attempt, attempts, synthesized[:100],
        )

    log.error(
        "caption: %d attempts all violated brand rules; using safe literal",
        attempts,
    )
    return _build_reel_caption(db_path, gem_ids, fallback)


def _wrap_caption_with_hashtags(db_path: Path, gem_ids: list[int], body: str) -> str:
    """Append verified hashtags (hashtags.yml) to a caption body. Shared by the
    Codex and LM Studio caption paths so both get the brand safety net."""
    from tools.pipeline.ig_poster import (
        _load_hashtag_library,
        build_caption,
        pick_hashtags,
        recent_tags_used,
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

    library = _load_hashtag_library(REPO_ROOT / "tools" / "pipeline" / "hashtags.yml")
    # v2.47.0: chicks bucket retired (grown flock); rotation fed from ledger.
    tags = pick_hashtags(
        vlm_metadata=best_meta,
        library=library,
        last_n_tags_used=recent_tags_used(db_path),
        buckets_override=["reel", "chickens", "homestead"],
    )
    return build_caption(journal_body=body, hashtags=tags)


def _select_gems(
    lane: DailyReelLane,
    db_path: Path,
    scheduled_cfg: dict,
) -> list[int]:
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

    # Per-lane pacing override. reel_stitcher applies one seconds_per_frame to
    # the whole reel, so a faster lane is expressed by handing it a shallow
    # copy of reels_cfg with that key replaced — the global stays untouched for
    # every other lane. crossfade must stay < seconds_per_frame (stitcher
    # guard), so clamp it down when a lane runs faster than the global fade.
    stitch_cfg = reels_cfg
    if lane.seconds_per_frame is not None:
        stitch_cfg = dict(reels_cfg)
        stitch_cfg["seconds_per_frame"] = lane.seconds_per_frame
        crossfade = float(stitch_cfg.get("crossfade_seconds", 0.15))
        if crossfade >= lane.seconds_per_frame:
            stitch_cfg["crossfade_seconds"] = round(lane.seconds_per_frame / 2, 3)
        log.info(
            "build: %s lane pacing override %.2fs/frame (global %.2fs)",
            lane.lane_id,
            lane.seconds_per_frame,
            float(reels_cfg.get("seconds_per_frame", 1.0)),
        )

    try:
        stitch_gems_to_reel(
            gem_ids=gem_ids,
            db_path=db_path,
            config=stitch_cfg,
            output_path=mp4_path,
            landscape=lane.landscape_mode,
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
        caption = _generate_reel_caption(db_path, gem_ids, lane.caption_fallback, cfg, log)
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
        preview_scale=lane.discord_preview_scale,
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
    # Backlog lane runs up to 4x/day — use hour-granularity key so each slot
    # gets its own state file and the "already posted" guard doesn't block
    # subsequent runs within the same calendar day.
    if lane.lane_id == "s7-backlog":
        date_key = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    else:
        date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    posted_file = posted_dir / f"{date_key}.json"
    if posted_file.exists():
        log.info("build: %s reel for %s already posted; skipping", lane.lane_id, date_key)
        return 0

    ledger_path, slots_free = _ledger_status(log)
    if slots_free <= 0 and not dry_run:
        log.warning("build: IG quota is full; skipping %s reel", lane.lane_id)
        return 0

    gem_ids = _select_gems(lane, db_path, scheduled_cfg)
    if not gem_ids:
        log.info("build: not enough frames for %s reel", lane.lane_id)
        return 0

    # D8 (16-Jul-2026) wired the s7-daily lane through
    # codex_reel_curator.curate() to prune redundant/weak frames. REMOVED
    # 23-Jul-2026 (v2.51.5): that curation ran on `codex exec`, the OpenAI
    # Codex subscription has lapsed, and every call had been returning 401
    # since ~07-Jul — so it only ever logged "source=fallback" and handed
    # back the unpruned selection anyway. Deleting it costs no real frame
    # pruning and saves a doomed subprocess (up to a 240 s timeout) on every
    # s7 build. If frame pruning is wanted again, reimplement it against the
    # local VLM in LM Studio rather than reviving the Codex dependency.
    stitch_ids = gem_ids
    if lane.lane_id == S7_DAILY_REEL_LANE.lane_id:
        # Pacing hook: hold the opening frame for one extra beat by
        # duplicating it at the front of the STITCH list only. reel_stitcher
        # applies a single seconds_per_frame to every list entry (no
        # per-frame duration support), so repeating an id is the cheap way
        # to give the reel a "hook" without an ffmpeg rewrite. gem_ids[0] is
        # chronologically earliest (_select_gems preserves chronological
        # order) and already representative. Only when
        # there's enough material that losing one bucket's worth of
        # distinct frames won't thin the time-lapse, and only with headroom
        # under reel_stitcher's hard 90-frame cap (_MIN_FRAMES=2/
        # _MAX_FRAMES=90) so the +1 can't push it over.
        if 3 <= len(gem_ids) < 90:
            stitch_ids = [gem_ids[0]] + gem_ids

    log.info("build: stitching %d frames for %s lane", len(stitch_ids), lane.lane_id)
    try:
        mp4_path = _stitch_reel(lane, stitch_ids, db_path, reels_cfg, log)
    except Exception as exc:
        log.error("build: stitch failed: %s", exc)
        return 1

    try:
        caption = _generate_reel_caption(db_path, gem_ids, lane.caption_fallback, cfg, log)
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

    _append_ledger(ledger_path, lane, date_key, result, dry_run)

    # Mark backlog gems so they leave the story queue and don't get re-selected
    if lane.lane_id == "s7-backlog" and not dry_run:
        from tools.pipeline.ig_selection import mark_gems_used_in_backlog_reel
        mark_gems_used_in_backlog_reel(db_path, gem_ids)

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
        upload_filename=f"{lane.lane_id}-timelapse-reel.mp4",
        log=log,
        preview_scale=lane.discord_preview_scale,
    )

    posted_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "lane": lane.lane_id,
        "date": date_key,
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
    return run_lane(
        lane=lane,
        dry_run=args.dry_run,
        skip_build=args.skip_build,
    )
