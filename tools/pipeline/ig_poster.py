# Author: Claude Opus 4.7 (1M context)
# Date: 20-April-2026 (Phase 4 core + Phase 6 predicate/hashtags 20-Apr-2026; Phase 2 stories 20-Apr-2026)
# PURPOSE: Post curated gems to Instagram @pawel_and_pawleen via Meta
#          Graph API. Parallels gem_poster.py (which posts to Discord)
#          but with a multi-step container+publish flow required by
#          Instagram, plus the farm-2026 git-commit hop that produces
#          an IG-fetcher-compatible GitHub raw URL.
#
#          Phase 4 scope (20-Apr-2026): core feed-post flow —
#          _load_credentials, _create_container, _publish,
#          _wait_for_container, _write_permalink, post_gem_to_ig.
#          Accepts a pre-built full caption (journal body + hashtags)
#          from the caller; auto-hashtag selection lives in Phase 6.
#
#          Phase 2 scope (20-Apr-2026, appended to this module): Story
#          posting — 24-hour ephemeral, no caption, 9:16 vertical.
#          Functions: _prepare_story_image (cv2 center-crop),
#          _create_story_container (media_type=STORIES), should_post_story
#          (looser predicate than feed posts — decent+soft allowed),
#          query_last_story_ts, _write_story_metadata, post_gem_to_story.
#          Shares the Graph API primitives and credential loader above.
#
#          Failures here must NEVER break the pipeline cycle — all
#          exceptions caught and returned in the `error` field of the
#          result dict. The only exception that can escape
#          post_gem_to_ig()/post_gem_to_story() is credential-missing
#          at the entry gate (loud failure on misconfiguration beats
#          silent no-op).
#
#          Credential source: keychain is the source of truth on this
#          Mac Mini; env file at
#          /Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env
#          mirrors the keychain and is sourced by the orchestrator at
#          startup. This module reads from os.environ — if not already
#          set, falls back to reading the env file directly (so
#          scripts/ig-post.py can be invoked standalone without
#          requiring the orchestrator's env-loading).
#
# SRP/DRY check: Pass — SRP is orchestrating the Graph API container
#                + publish flow (both feed and story lanes). DRY:
#                reuses git_helper.py for the farm-2026 commit hop;
#                story posting reuses _graph_request, _wait_for_container,
#                _publish, _load_credentials, _lookup_gem, and
#                _local_path_for_gem from the feed-post code above.

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tools.pipeline.git_helper import GitHelperError, commit_image_to_farm_2026
from tools.pipeline.store import resolve_gem_image_path

log = logging.getLogger("pipeline.ig_poster")

GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Absolute by design — farm-guardian is single-host on this Mac Mini.
# If the service is ever ported, this becomes a config key at that time,
# not before. Premature portability is a cost.
_META_ENV_FILE = Path("/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env")

# Caption limits per Meta docs
_IG_CAPTION_MAX_CHARS = 2200
_IG_HASHTAG_MAX_COUNT = 30


class IGPosterError(RuntimeError):
    """Raised on credential-missing at the entry gate. Other failures
    are caught inside post_gem_to_ig() and returned in the result dict's
    `error` field."""


# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------

# Required env-var names, source-of-truth in
# ~/bubba-workspace/secrets/farm-guardian-meta.env (mirror of keychain).
_REQUIRED_ENV = {
    "ig_id": "IG_BUSINESS_ACCOUNT_ID",
    "user_token": "LONG_LIVED_USER_TOKEN",
    "app_id": "FB_APP_ID",
}
# Optional env vars used for diagnostics
_OPTIONAL_ENV = {
    "app_secret": "FB_APP_SECRET",
    "page_id": "FB_PAGE_ID",
    "ig_username": "IG_USERNAME",
}


def _source_meta_env_file(path: Path = _META_ENV_FILE) -> None:
    """Minimal .env reader — if the env file exists and the required
    vars aren't already in os.environ, read them from the file.

    Non-destructive: already-set env vars win (so the orchestrator's
    load_dotenv() at startup takes precedence when this module is
    imported inside the pipeline). Pure stdlib; no python-dotenv
    dependency beyond what's already there.
    """
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _load_credentials() -> dict:
    """Return a dict with ig_id, user_token, app_id (required) and
    any optional diagnostic fields that are present.

    Source order:
      1. Environment variables already set (orchestrator's load_dotenv
         covers this path).
      2. Fallback: read /Users/macmini/bubba-workspace/secrets/
         farm-guardian-meta.env directly (for standalone CLI use).

    Raises IGPosterError if any required field is still missing after
    both sources. Actionable error message points at the keychain-to-
    env-file regeneration recipe in the memory file.
    """
    # First try: env already loaded. If all required present, done.
    creds = _collect_from_env()
    if _all_required(creds):
        return creds

    # Second try: source the env file directly.
    _source_meta_env_file()
    creds = _collect_from_env()
    if _all_required(creds):
        return creds

    missing = [env_name for logical, env_name in _REQUIRED_ENV.items() if not os.environ.get(env_name)]
    raise IGPosterError(
        "ig_poster: missing required credentials: " + ", ".join(missing) + ".\n"
        f"Expected in env (orchestrator sources {_META_ENV_FILE} at startup).\n"
        "See ~/.claude/projects/-Users-macmini-bubba-workspace/memory/farm-instagram.md "
        "for the keychain-to-env regeneration recipe."
    )


def _collect_from_env() -> dict:
    out = {}
    for logical, env_name in {**_REQUIRED_ENV, **_OPTIONAL_ENV}.items():
        val = os.environ.get(env_name)
        if val:
            out[logical] = val
    return out


def _all_required(creds: dict) -> bool:
    return all(key in creds for key in _REQUIRED_ENV)


# ---------------------------------------------------------------------------
# Graph API primitives
# ---------------------------------------------------------------------------


def _graph_request(
    method: str,
    path: str,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
    timeout: int = 30,
) -> dict:
    """Minimal Graph API wrapper using urllib (no new deps).

    path is the part AFTER the version, e.g. '/me/accounts'.
    method is 'GET' or 'POST'.
    For POST, body fields go as x-www-form-urlencoded (what Graph
    expects for the image_url+caption style calls; multipart upload
    is NOT needed since we pass image_url, not the bytes themselves).

    Returns the parsed JSON response. Raises urllib errors on
    network/HTTP failures; the caller is expected to handle.
    """
    url = GRAPH_API_BASE + path
    if method == "GET":
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, method="GET")
    elif method == "POST":
        data = urllib.parse.urlencode(body or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    else:
        raise ValueError(f"Unsupported method: {method}")

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read().decode("utf-8")

    return json.loads(payload)


def _create_container(
    ig_id: str,
    image_url: str,
    caption: str,
    user_token: str,
) -> str:
    """POST /{ig_id}/media. Returns container id.

    Raises if the API returns no 'id' — typically means the media fetch
    failed on Meta's side (9004/2207052) or the token lacks
    instagram_content_publish.
    """
    resp = _graph_request(
        "POST",
        f"/{ig_id}/media",
        body={
            "image_url": image_url,
            "caption": caption,
            "access_token": user_token,
        },
    )
    cid = resp.get("id")
    if not cid:
        raise RuntimeError(f"create_container returned no id: {resp}")
    return cid


def _wait_for_container(
    container_id: str,
    user_token: str,
    timeout_s: int = 30,
    poll_interval_s: int = 3,
) -> str:
    """Poll /{container}?fields=status_code until FINISHED or timeout.

    Returns the final status_code string. IG uses:
      - EXPIRED: container expired before publish (don't retry, re-create)
      - ERROR: processing failed (check error_message)
      - FINISHED: ready to publish
      - IN_PROGRESS: still processing, keep polling
      - PUBLISHED: already published (re-running after publish)

    For photos (not reels/videos) FINISHED typically arrives within 2-5s.
    A 30s timeout is comfortable headroom without making a stuck
    container hang the pipeline forever.
    """
    deadline = time.time() + timeout_s
    last_status = ""
    while time.time() < deadline:
        resp = _graph_request(
            "GET",
            f"/{container_id}",
            params={"fields": "status_code", "access_token": user_token},
        )
        last_status = resp.get("status_code", "")
        if last_status in ("FINISHED", "PUBLISHED", "ERROR", "EXPIRED"):
            return last_status
        time.sleep(poll_interval_s)
    return last_status or "TIMEOUT"


def _publish(ig_id: str, container_id: str, user_token: str) -> dict:
    """POST /{ig_id}/media_publish. Returns dict with media_id + permalink."""
    resp = _graph_request(
        "POST",
        f"/{ig_id}/media_publish",
        body={"creation_id": container_id, "access_token": user_token},
    )
    media_id = resp.get("id")
    if not media_id:
        raise RuntimeError(f"publish returned no media id: {resp}")

    # Fetch permalink separately — IG's publish response only returns id.
    perma = _graph_request(
        "GET",
        f"/{media_id}",
        params={"fields": "permalink,timestamp", "access_token": user_token},
    )
    return {
        "media_id": media_id,
        "permalink": perma.get("permalink"),
        "timestamp": perma.get("timestamp"),
    }


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------


def _lookup_gem(db_path: Path, gem_id: int) -> Optional[dict]:
    """Return the image_archive row for a given gem id as a dict, or
    None if the id is not in the table."""
    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT * FROM image_archive WHERE id = ?",
            (gem_id,),
        ).fetchone()
    return dict(row) if row else None


def _write_permalink(
    db_path: Path,
    gem_id: int,
    permalink: Optional[str],
    posted_at_iso: Optional[str],
    skip_reason: Optional[str] = None,
) -> None:
    """UPDATE image_archive.{ig_permalink, ig_posted_at, ig_skip_reason}
    for the given gem.

    Any combination of fields can be set — pass None to leave alone or
    explicitly clear. Skip reason is used for should_post_ig=False
    decisions (so we can audit later why a gem wasn't posted).
    """
    with sqlite3.connect(str(db_path)) as c:
        c.execute(
            """
            UPDATE image_archive
               SET ig_permalink = COALESCE(?, ig_permalink),
                   ig_posted_at = COALESCE(?, ig_posted_at),
                   ig_skip_reason = COALESCE(?, ig_skip_reason)
             WHERE id = ?
            """,
            (permalink, posted_at_iso, skip_reason, gem_id),
        )
        c.commit()


# ---------------------------------------------------------------------------
# Helper: find the local JPEG on disk for a given gem
# ---------------------------------------------------------------------------


def _local_path_for_gem(gem_row: dict, db_path: Path) -> Path:
    """Backwards-compatible wrapper around store.resolve_gem_image_path.

    The canonical helper lives in store.py (Phase 3b, 20-Apr-2026) so
    reel_stitcher.py can share it without importing a private name from
    ig_poster. This wrapper stays put so existing call sites inside
    post_gem_to_ig / post_gem_to_story don't change.
    """
    return resolve_gem_image_path(gem_row, db_path)


# ---------------------------------------------------------------------------
# Predicate + hashtag selection (Phase 6)
# These aren't called from post_gem_to_ig itself — they're preparation for
# V2.2 auto-posting (orchestrator hook uses should_post_ig to decide whether
# to fire post_gem_to_ig per gem) and for a future CLI --auto-hashtags flag.
# Kept here so all IG-policy logic lives in one module.
# ---------------------------------------------------------------------------


def should_post_ig(
    vlm_metadata: dict,
    gem_row: dict,
    last_ig_post_ts: Optional[str] = None,
    last_same_camera_ts: Optional[str] = None,
    min_hours_between_posts: int = 3,
    min_hours_per_camera: int = 12,
) -> tuple[bool, str]:
    """Predicate gate for auto-posting to Instagram.

    STRICTER than gem_poster.should_post() because IG is public-facing:
      - tier == "strong" (share_worth in vlm_metadata)
      - image_quality == "sharp" (no decent/soft for IG)
      - bird_count >= 1
      - has_concerns is false (privacy filter; enforced by images_api
        too, belt and suspenders here)
      - No other IG post in the last N hours (default 3h cadence window)
      - No other IG post from the same camera in the last M hours
        (default 12h scene dedup — don't post 4 brooder shots in
        quick succession)

    Returns (should_post: bool, reason: str). The reason string is
    either "ok" (when should_post is True) or a specific skip reason
    that gets logged to image_archive.ig_skip_reason so skip patterns
    can be audited later.

    last_ig_post_ts / last_same_camera_ts are caller-supplied ISO8601
    timestamps (the orchestrator / CLI queries image_archive for the
    most recent ig_posted_at values). Pass None on the very first run
    or when the query returned no rows.
    """
    share = vlm_metadata.get("share_worth")
    if share != "strong":
        return False, f"tier={share} (need strong)"

    quality = vlm_metadata.get("image_quality")
    if quality != "sharp":
        return False, f"quality={quality} (need sharp)"

    bird_count = vlm_metadata.get("bird_count", 0)
    if not bird_count or bird_count < 1:
        return False, f"bird_count={bird_count} (need >= 1)"

    # has_concerns might come through as int 0/1 or bool — normalize.
    concerns = vlm_metadata.get("concerns") or gem_row.get("has_concerns")
    if concerns and concerns not in (0, False, [], "", None):
        return False, "has_concerns flagged"

    now = datetime.now(timezone.utc)

    if last_ig_post_ts:
        try:
            last_ts = datetime.fromisoformat(last_ig_post_ts.replace("Z", "+00:00"))
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            age_h = (now - last_ts).total_seconds() / 3600
            if age_h < min_hours_between_posts:
                return False, f"last_post_{age_h:.1f}h_ago (min {min_hours_between_posts}h)"
        except (ValueError, TypeError):
            log.warning("could not parse last_ig_post_ts=%r; ignoring", last_ig_post_ts)

    if last_same_camera_ts:
        try:
            last_cam_ts = datetime.fromisoformat(last_same_camera_ts.replace("Z", "+00:00"))
            if last_cam_ts.tzinfo is None:
                last_cam_ts = last_cam_ts.replace(tzinfo=timezone.utc)
            age_h = (now - last_cam_ts).total_seconds() / 3600
            if age_h < min_hours_per_camera:
                return False, (
                    f"same_camera_post_{age_h:.1f}h_ago "
                    f"(min {min_hours_per_camera}h per camera)"
                )
        except (ValueError, TypeError):
            log.warning(
                "could not parse last_same_camera_ts=%r; ignoring",
                last_same_camera_ts,
            )

    return True, "ok"


def query_last_ig_post_ts(db_path: Path, camera_id: Optional[str] = None) -> Optional[str]:
    """Helper: look up the most recent ig_posted_at value.

    If camera_id is None, returns the overall most-recent IG post.
    If camera_id is provided, returns the most-recent IG post from
    that camera. Used to feed last_ig_post_ts / last_same_camera_ts
    to should_post_ig().

    Returns None if no gem has ig_posted_at populated yet — i.e. the
    very first auto-post will always pass the time-gate.
    """
    with sqlite3.connect(str(db_path)) as c:
        if camera_id:
            row = c.execute(
                "SELECT MAX(ig_posted_at) FROM image_archive WHERE camera_id = ?",
                (camera_id,),
            ).fetchone()
        else:
            row = c.execute(
                "SELECT MAX(ig_posted_at) FROM image_archive"
            ).fetchone()
    return row[0] if row and row[0] else None


# Scene → hashtag bucket auto-mapping. Extend as the VLM prompt grows
# new scene types. See docs/19-Apr-2026-instagram-posting-plan.md
# §_scene_to_buckets for the policy and what's manual-override-only.
_SCENE_BUCKET_MAP = {
    "brooder": ["chicks", "chickens", "homestead"],
    # Coop / yard / orchard buckets are manual-override only today
    # because the VLM doesn't emit those scene labels yet. The CLI's
    # --override-tags (Phase 6.5) is the escape hatch for posts whose
    # content doesn't map cleanly from vlm_metadata.
}


def _scene_to_buckets(vlm_metadata: dict) -> list[str]:
    """Map gem metadata to relevant hashtag-library buckets.

    Conservative: if the scene isn't in the known map, returns an empty
    list — the caller should then either fall back to `homestead` as a
    universal default or require an explicit override.
    """
    scene = vlm_metadata.get("scene", "")
    return list(_SCENE_BUCKET_MAP.get(scene, []))


def _load_hashtag_library(path: Path) -> dict:
    """Load tools/pipeline/hashtags.yml.

    Uses pyyaml (already a transitive dependency via ultralytics, so
    no new pin in requirements.txt). Returns the full nested dict
    including the 'forbidden' list. Verifies at load time that no
    bucket contains a forbidden tag — raises ValueError if so.
    """
    import yaml

    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"hashtags.yml did not parse to a dict: got {type(data)}")

    forbidden = set(data.get("forbidden", []) or [])

    for bucket_name, tiers in data.items():
        if bucket_name == "forbidden":
            continue
        if not isinstance(tiers, dict):
            continue
        for tier_name, tags in tiers.items():
            if not tags:
                continue
            bad = [t for t in tags if t in forbidden]
            if bad:
                raise ValueError(
                    f"hashtags.yml bucket {bucket_name}.{tier_name} "
                    f"contains forbidden tags: {bad}"
                )

    return data


def pick_hashtags(
    vlm_metadata: dict,
    library: dict,
    last_n_tags_used: Optional[list[str]] = None,
    max_tags: int = 10,
    buckets_override: Optional[list[str]] = None,
) -> list[str]:
    """Select up to max_tags hashtags from the library based on the gem's
    scene/content, weighted toward long-tail for account-size reasons.

    Weighting strategy (verified in docs/19-Apr-2026 §hashtag library):
      - 1-2 top-tier (algorithmic signal, not reach on a 381-follower
        account)
      - 3-4 mid-tier (actual discoverability lane)
      - 4-5 long-tail (rank potential on "Recent" feed)

    Dedupes against last_n_tags_used to force rotation — IG shadow-bans
    obvious repeat tag sets.

    Tags returned WITHOUT the leading '#'. The caller appends the '#'
    when building the caption (to simplify rotation-set comparisons).

    buckets_override lets the caller specify which buckets to draw
    from (e.g., ["yorkies", "homestead"] for a yorkie post). If None,
    _scene_to_buckets is consulted.
    """
    buckets = buckets_override or _scene_to_buckets(vlm_metadata)
    # Universal fallback: homestead
    if not buckets:
        buckets = ["homestead"]

    last_n = set(last_n_tags_used or [])
    forbidden = set(library.get("forbidden", []) or [])

    # Collect tiered pools across all relevant buckets, deduped.
    top_pool: list[str] = []
    mid_pool: list[str] = []
    long_pool: list[str] = []
    for bucket_name in buckets:
        bucket = library.get(bucket_name, {})
        if not isinstance(bucket, dict):
            continue
        for t in bucket.get("top_tier", []) or []:
            if t not in top_pool and t not in forbidden:
                top_pool.append(t)
        for t in bucket.get("mid_tier", []) or []:
            if t not in mid_pool and t not in forbidden:
                mid_pool.append(t)
        for t in bucket.get("long_tail", []) or []:
            if t not in long_pool and t not in forbidden:
                long_pool.append(t)

    # Rotation: prefer tags NOT in last_n_tags_used.
    def _order_fresh_first(pool: list[str]) -> list[str]:
        fresh = [t for t in pool if t not in last_n]
        stale = [t for t in pool if t in last_n]
        return fresh + stale

    top_pool = _order_fresh_first(top_pool)
    mid_pool = _order_fresh_first(mid_pool)
    long_pool = _order_fresh_first(long_pool)

    # Targets: long-tail first (rank lane), then mid (discoverability),
    # then top (signal).
    target_long = min(5, len(long_pool))
    target_mid = min(4, len(mid_pool))
    target_top = min(2, len(top_pool))

    # If budget allows more and pools have depth, promote into mid/top.
    selected: list[str] = []
    selected.extend(long_pool[:target_long])
    selected.extend(mid_pool[:target_mid])
    selected.extend(top_pool[:target_top])

    # If we're under max_tags and there's still pool depth, fill from
    # mid then long (not top — we want to stay long-tail heavy).
    leftover_mid = mid_pool[target_mid:]
    leftover_long = long_pool[target_long:]
    while len(selected) < max_tags and (leftover_mid or leftover_long):
        if leftover_mid:
            selected.append(leftover_mid.pop(0))
            if len(selected) >= max_tags:
                break
        if leftover_long:
            selected.append(leftover_long.pop(0))

    # Final dedup + cap (selected could have duplicates if buckets
    # overlap — e.g. #backyardchickens appears in both chickens.top_tier
    # and coop.top_tier)
    seen = set()
    out = []
    for t in selected:
        if t not in seen and t not in forbidden:
            out.append(t)
            seen.add(t)
    return out[:max_tags]


def build_caption(
    journal_body: str,
    hashtags: list[str],
    sign_off: Optional[str] = "📸 @markbarney121",
) -> str:
    """Build a full IG caption from journal body + tags + sign-off.

    Layout (matches post #2 and #3):
      <journal body>
      <blank line>
      <sign-off, if provided>
      <blank line>
      <#tag1 #tag2 ...>

    If hashtags is empty, the hashtag line is omitted. If sign_off is
    None or empty, the sign-off line and its blank line are omitted.

    Total length guarded to stay under the 2200-char IG limit; raises
    ValueError if the caller built something too long.
    """
    parts = [journal_body.rstrip()]
    if sign_off:
        parts.extend(["", sign_off.strip()])
    if hashtags:
        tag_line = " ".join("#" + t.lstrip("#") for t in hashtags)
        parts.extend(["", tag_line])
    caption = "\n".join(parts)
    if len(caption) > _IG_CAPTION_MAX_CHARS:
        raise ValueError(
            f"built caption is {len(caption)} chars; IG max is {_IG_CAPTION_MAX_CHARS}"
        )
    return caption


# ---------------------------------------------------------------------------
# Stories (Phase 2, 20-Apr-2026)
# ---------------------------------------------------------------------------
# 24-hour ephemeral posts. Looser quality bar than feed posts (stories
# disappear, so "good enough" is fine), 9:16 vertical aspect ratio
# required, NO caption support from the Graph API (passing one returns
# a cryptic 400). Stories share the Graph API primitives, credential
# loader, and gem lookup above — only the aspect-ratio prep, the
# container media_type, the DB columns, and the predicate differ.

# Story images must be 9:16 vertical. Fleet cameras produce 16:9
# landscape, so _prepare_story_image center-crops on width at the
# source's native height — no upscaling. 1920x1080 -> 607x1080;
# 1280x720 -> 405x720.
_STORY_JPEG_QUALITY = 92


def _prepare_story_image(local_path: Path) -> Path:
    """Center-crop a landscape JPEG to 9:16 vertical and write the
    result to a new JPEG in a temp location.

    Returns the path to the prepared JPEG. The caller is responsible
    for deleting it after the commit+post step (post_gem_to_story
    wraps this in a try/finally with an unlink).

    Strategy — native-height center crop:
      h, w = img.shape[:2]
      target_w = h * 9/16
      if target_w < w:
          crop middle target_w columns; result is w'=target_w, h'=h.
      else:
          source is already narrower than 9:16 (e.g. a portrait phone
          shot) — pad top/bottom with black bars to reach 9:16 rather
          than zoom in and lose edges.

    No upscaling path. A 1920x1080 source becomes 607x1080; a 1280x720
    becomes 405x720. Both are fine for mobile viewing on IG.

    Raises:
      FileNotFoundError - local_path doesn't exist.
      ValueError - source cannot be decoded by cv2 or the JPEG
                   write fails.
    """
    # Local imports: cv2 adds ~100ms to cold start and is already pulled
    # in by store.py in-process; keep it lazy here so scripts/ig-post.py's
    # argparse path doesn't pay for it on --help.
    import cv2
    import tempfile
    if not local_path.exists():
        raise FileNotFoundError(f"_prepare_story_image: {local_path} not found")

    img = cv2.imread(str(local_path))
    if img is None:
        raise ValueError(f"_prepare_story_image: could not decode {local_path}")

    h, w = img.shape[:2]
    target_w = int(round(h * 9 / 16))
    if target_w < w:
        x0 = (w - target_w) // 2
        prepared = img[:, x0:x0 + target_w]
    else:
        # Source is narrower than 9:16 — pad top/bottom with black bars.
        target_h = int(round(w * 16 / 9))
        top = (target_h - h) // 2
        bottom = target_h - h - top
        prepared = cv2.copyMakeBorder(
            img, top, bottom, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0),
        )

    fd, tmp_path = tempfile.mkstemp(suffix="-story.jpg")
    os.close(fd)
    ok = cv2.imwrite(
        tmp_path,
        prepared,
        [int(cv2.IMWRITE_JPEG_QUALITY), _STORY_JPEG_QUALITY],
    )
    if not ok:
        raise ValueError(f"_prepare_story_image: cv2.imwrite failed for {tmp_path}")
    return Path(tmp_path)


def _create_story_container(
    ig_id: str,
    image_url: str,
    user_token: str,
) -> str:
    """POST /{ig_id}/media with media_type=STORIES.

    Stories do NOT accept the caption field — Graph API returns a
    cryptic 400 if one is passed. Text stickers, @mentions, location
    tags, and swipe-up links all require manual posting from the
    phone (Graph API doesn't expose them). Returns the container id.
    """
    resp = _graph_request(
        "POST",
        f"/{ig_id}/media",
        body={
            "image_url": image_url,
            "media_type": "STORIES",
            "access_token": user_token,
        },
    )
    cid = resp.get("id")
    if not cid:
        raise RuntimeError(f"create_story_container returned no id: {resp}")
    return cid


def _write_story_metadata(
    db_path: Path,
    gem_id: int,
    story_id: Optional[str],
    posted_at_iso: Optional[str],
    skip_reason: Optional[str] = None,
) -> None:
    """UPDATE image_archive.{ig_story_id, ig_story_posted_at,
    ig_story_skip_reason} for the given gem.

    Mirrors _write_permalink's COALESCE pattern — pass None to leave
    a field untouched. Strictly does not modify ig_permalink or the
    other feed-post columns; a gem can be both a story and a feed
    post without collisions.
    """
    with sqlite3.connect(str(db_path)) as c:
        c.execute(
            """
            UPDATE image_archive
               SET ig_story_id = COALESCE(?, ig_story_id),
                   ig_story_posted_at = COALESCE(?, ig_story_posted_at),
                   ig_story_skip_reason = COALESCE(?, ig_story_skip_reason)
             WHERE id = ?
            """,
            (story_id, posted_at_iso, skip_reason, gem_id),
        )
        c.commit()


def should_post_story(
    vlm_metadata: dict,
    gem_row: dict,
    last_story_ts: Optional[str] = None,
    min_hours_between_stories: int = 2,
) -> tuple[bool, str]:
    """Predicate gate for posting a gem as an Instagram Story.

    LOOSER than should_post_ig because stories expire in 24h:
      - tier in {"strong", "decent"}   (feed requires "strong")
      - image_quality in {"sharp", "soft"}  (feed requires "sharp")
      - bird_count >= 1
      - has_concerns is falsy (privacy belt-and-suspenders)
      - no other story in the last N hours (default 2; feed is 3)

    No per-camera dedup — stories are casual, repeat-camera content
    is fine.

    Returns (should_post, reason). The reason is either "ok" (True)
    or a specific skip string (False) that gets logged to
    image_archive.ig_story_skip_reason for audit.

    last_story_ts is a caller-supplied ISO8601 timestamp from
    query_last_story_ts(). Pass None on first run.
    """
    share = vlm_metadata.get("share_worth")
    if share not in ("strong", "decent"):
        return False, f"tier={share} (need strong/decent for story)"

    quality = vlm_metadata.get("image_quality")
    if quality not in ("sharp", "soft"):
        return False, f"quality={quality} (need sharp/soft for story)"

    bird_count = vlm_metadata.get("bird_count", 0)
    if not bird_count or bird_count < 1:
        return False, f"bird_count={bird_count} (need >= 1)"

    concerns = vlm_metadata.get("concerns") or gem_row.get("has_concerns")
    if concerns and concerns not in (0, False, [], "", None):
        return False, "has_concerns flagged"

    now = datetime.now(timezone.utc)
    if last_story_ts:
        try:
            last_ts = datetime.fromisoformat(last_story_ts.replace("Z", "+00:00"))
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            age_h = (now - last_ts).total_seconds() / 3600
            if age_h < min_hours_between_stories:
                return False, (
                    f"last_story_{age_h:.1f}h_ago "
                    f"(min {min_hours_between_stories}h)"
                )
        except (ValueError, TypeError):
            log.warning("could not parse last_story_ts=%r; ignoring", last_story_ts)

    return True, "ok"


def query_last_story_ts(db_path: Path) -> Optional[str]:
    """Return the most recent ig_story_posted_at across all gems, or
    None if no stories have been posted yet.

    Stories don't use per-camera dedup (casual content, repeat-camera
    is fine), so this helper has no camera_id variant — unlike
    query_last_ig_post_ts.
    """
    with sqlite3.connect(str(db_path)) as c:
        row = c.execute(
            "SELECT MAX(ig_story_posted_at) FROM image_archive"
        ).fetchone()
    return row[0] if row and row[0] else None


def post_gem_to_story(
    gem_id: int,
    db_path: Path,
    farm_2026_repo_path: Path,
    dry_run: bool = False,
) -> dict:
    """Post a gem to Instagram as a 24-hour Story. No caption (Graph
    API rejects caption on story containers with a cryptic 400).

    Flow:
      1. Look up the gem row.
      2. Resolve local full-res JPEG.
      3. _prepare_story_image center-crops to 9:16 (native height,
         no upscale).
      4. Commit the 9:16 JPEG to farm-2026/public/photos/stories/ and
         derive the GitHub raw URL.
      5. _load_credentials.
      6. _create_story_container — media_type=STORIES, no caption.
      7. _wait_for_container — standard 30s timeout works for stories
         (same latency as feed photos).
      8. _publish → media_id + permalink.
      9. _write_story_metadata writes ig_story_id + ig_story_posted_at
         back to the gem row. Does NOT touch ig_permalink.

    dry_run=True stops BEFORE the git commit AND before any Graph API
    call — predicts the raw URL and returns. Still runs
    _prepare_story_image to exercise the 9:16 prep path? No: skip the
    prep too (there's no output path to predict on the prep, and we
    want dry-run to be zero side effects). The dry-run return still
    includes a predicted raw_url so operators can audit.

    Returns (never raises except IGPosterError at credential gate):
      {
        "gem_id": int,
        "dry_run": bool,
        "raw_url": str | None,
        "caption": None,          # stories have no caption
        "story_id": str | None,
        "permalink": str | None,
        "posted_at": str | None,
        "error": str | None,
      }
    """
    result: dict = {
        "gem_id": gem_id,
        "dry_run": dry_run,
        "raw_url": None,
        "caption": None,
        "story_id": None,
        "permalink": None,
        "posted_at": None,
        "error": None,
    }

    try:
        gem = _lookup_gem(db_path, gem_id)
        if not gem:
            raise ValueError(f"gem_id {gem_id} not found in image_archive")

        local_path = _local_path_for_gem(gem, db_path)

        subdir = "stories"
        stamped_name = (
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
            f"-gem{gem_id}-story.jpg"
        )

        if dry_run:
            result["raw_url"] = (
                f"https://raw.githubusercontent.com/VoynichLabs/farm-2026/main/"
                f"public/photos/{subdir}/{stamped_name}"
            )
            log.info(
                "ig_poster STORY DRY RUN: would prepare+commit %s -> %s and post as story",
                local_path, result["raw_url"],
            )
            return result

        # 3-4. Prepare 9:16 image, stage under the stamped name, commit+push.
        import shutil as _shutil
        import tempfile
        prepared = _prepare_story_image(local_path)
        try:
            with tempfile.TemporaryDirectory() as td:
                staging = Path(td) / stamped_name
                _shutil.copy2(prepared, staging)
                committed_path, raw_url = commit_image_to_farm_2026(
                    local_image=staging,
                    subdir=subdir,
                    repo_path=farm_2026_repo_path,
                    commit_message=f"public/photos/{subdir}: gem {gem_id} story (ig auto)",
                )
        finally:
            try:
                prepared.unlink()
            except OSError:
                pass
        result["raw_url"] = raw_url

        # 5. Credentials
        creds = _load_credentials()

        # 6. Story container
        container_id = _create_story_container(
            ig_id=creds["ig_id"],
            image_url=raw_url,
            user_token=creds["user_token"],
        )
        log.info("ig_poster: story container created %s", container_id)

        # 7. Wait for FINISHED (stories are images, same 30s timeout)
        status = _wait_for_container(container_id, creds["user_token"])
        if status != "FINISHED":
            raise RuntimeError(
                f"story container {container_id} ended in status={status} (expected FINISHED)"
            )

        # 8. Publish
        pub = _publish(creds["ig_id"], container_id, creds["user_token"])
        result["story_id"] = pub["media_id"]
        result["permalink"] = pub["permalink"]
        result["posted_at"] = pub["timestamp"]

        # 9. Write back to DB (story columns only)
        _write_story_metadata(
            db_path,
            gem_id,
            story_id=pub["media_id"],
            posted_at_iso=pub["timestamp"],
        )
        log.info(
            "ig_poster: posted gem %s as story -> %s", gem_id, pub["permalink"],
        )

    except IGPosterError:
        # Credential missing — loud failure, escape the caught-all path.
        raise
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        log.exception("ig_poster: gem %s story post failed", gem_id)

    return result


# ---------------------------------------------------------------------------
# Reels (Phase 3, 20-Apr-2026)
# ---------------------------------------------------------------------------
# Short-form video, 9:16 vertical. The stitching work lives in
# tools/pipeline/reel_stitcher.py (ffmpeg subprocess + cv2 pre-crop);
# this module's responsibility is the Graph API container+publish flow
# for an already-stitched MP4.
#
# Reels differ from photos on the Graph side in three ways:
#   1. media_type="REELS" on the container create.
#   2. video_url replaces image_url.
#   3. Container processing takes 30–60s (photos are 2–5s). The shared
#      _wait_for_container helper takes a timeout_s argument; we call
#      it with timeout_s=180 and poll_interval_s=5 at the reel call
#      site rather than changing the default.
#
# Associated gem_ids: a reel is stitched from N source gems. After a
# successful publish, we write the reel's permalink + posted_at to
# EACH source gem's image_archive row. Downstream effect: the feed
# predicate (should_post_ig) will naturally short-circuit future
# attempts to re-post those frames as standalone photos. For v1,
# ig_permalink is a single column; if the same gem later participates
# in a second post, the newer permalink overwrites the older one. A
# per-media-type column split (ig_reel_permalink) is v1.1 territory.

# Reels need extra runway: container processing routinely runs 30-60s.
# Photos are ~2-5s. Both timeout/poll are applied at the call site;
# the defaults in _wait_for_container stay tuned for the photo path.
_REEL_CONTAINER_TIMEOUT_S = 180
_REEL_CONTAINER_POLL_S = 5


def _create_reel_container(
    ig_id: str,
    video_url: str,
    caption: str,
    user_token: str,
) -> str:
    """POST /{ig_id}/media with media_type=REELS.

    Returns the container id. Reels carry a caption (unlike stories).
    The video_url must end in .mp4 and be publicly accessible — the
    GitHub raw URL path via commit_image_to_farm_2026 is what we use.
    """
    resp = _graph_request(
        "POST",
        f"/{ig_id}/media",
        body={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": user_token,
        },
        timeout=60,  # reel container POST is slower than photos
    )
    cid = resp.get("id")
    if not cid:
        raise RuntimeError(f"create_reel_container returned no id: {resp}")
    return cid


def _ffprobe_sanity(mp4_path: Path) -> None:
    """Quick ffprobe-based sanity check: the file is readable, has ≥1
    video stream, and isn't empty.

    Not exhaustive — we don't enforce specific codec/resolution here
    (the stitcher is responsible for producing a valid MP4). This just
    catches gross failures like a 0-byte file or a corrupt container
    before we hand the URL to the Graph API.
    """
    exe = shutil.which("ffprobe")
    if not exe:
        # ffprobe is part of the ffmpeg package; if it's missing,
        # ffmpeg almost certainly is too and the stitch step would
        # have already failed. Don't hard-fail here — the Graph API
        # side-path will catch a bad MP4 with a clear error.
        log.warning("ffprobe not on PATH; skipping MP4 sanity check")
        return
    if mp4_path.stat().st_size == 0:
        raise RuntimeError(f"reel MP4 is 0 bytes: {mp4_path}")
    cmd = [
        exe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height",
        "-of", "default=nw=1:nk=1", str(mp4_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(
            f"ffprobe rejected {mp4_path}: rc={proc.returncode} "
            f"stderr={proc.stderr.strip()}"
        )


def post_reel_to_ig(
    reel_mp4_path: Optional[Path],
    caption: str,
    db_path: Path,
    farm_2026_repo_path: Path,
    associated_gem_ids: Optional[list[int]] = None,
    dry_run: bool = False,
) -> dict:
    """Post a stitched MP4 to Instagram as a Reel.

    Parameters:
      reel_mp4_path
          Path to the MP4 produced by reel_stitcher.stitch_gems_to_reel.
          Required on live runs; optional on dry-run (a synthetic URL
          is predicted in that case).
      caption
          Full caption (journal body + hashtags + sign-off). Same
          2200-char limit as feed posts.
      db_path, farm_2026_repo_path
          Same semantics as post_gem_to_ig.
      associated_gem_ids
          Source gem ids used to stitch the reel. On a successful live
          post, each gem's image_archive row gets the reel's permalink
          and posted_at written to ig_permalink / ig_posted_at so
          downstream predicates (should_post_ig's cadence gate)
          short-circuit future attempts to re-post those frames.
          Pass [] or None to skip the write-back.
      dry_run
          Skip the git commit AND the Graph API calls. Predict a raw
          URL from the MP4 filename (or a synthetic name if mp4_path
          is None). Return shape identical to live path.

    Returns:
      {
        "reel_path": str | None,
        "associated_gem_ids": list[int],
        "dry_run": bool,
        "raw_url": str | None,
        "caption": str,
        "media_id": str | None,
        "permalink": str | None,
        "posted_at": str | None,
        "error": str | None,
      }

    Never raises except IGPosterError at the credential gate.
    """
    gem_ids = list(associated_gem_ids or [])
    result: dict = {
        "reel_path": str(reel_mp4_path) if reel_mp4_path else None,
        "associated_gem_ids": gem_ids,
        "dry_run": dry_run,
        "raw_url": None,
        "caption": caption,
        "media_id": None,
        "permalink": None,
        "posted_at": None,
        "error": None,
    }

    try:
        if len(caption) > _IG_CAPTION_MAX_CHARS:
            raise ValueError(
                f"caption is {len(caption)} chars; IG max is {_IG_CAPTION_MAX_CHARS}"
            )

        # Predict the URL subdir + filename regardless of dry-vs-live so
        # dry-run can return a realistic raw_url.
        ym = datetime.now(timezone.utc).strftime("%Y-%m")
        subdir = f"reels/{ym}"
        if reel_mp4_path is None:
            # Dry-run without a real MP4 — synthesize a filename. Operator
            # won't see this file; the URL exists purely for audit.
            stamped = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
            stamped_name = f"reel-{stamped}-dryrun.mp4"
        else:
            reel_mp4_path = Path(reel_mp4_path)
            stamped_name = reel_mp4_path.name

        if dry_run:
            result["raw_url"] = (
                f"https://raw.githubusercontent.com/VoynichLabs/farm-2026/main/"
                f"public/photos/{subdir}/{stamped_name}"
            )
            log.info(
                "ig_poster REEL DRY RUN: would commit %s -> %s and post reel",
                reel_mp4_path or "(synthetic)", result["raw_url"],
            )
            return result

        # Live path: real MP4 path is required.
        if reel_mp4_path is None:
            raise ValueError("reel_mp4_path is required for a live (non-dry-run) post")
        if not reel_mp4_path.exists():
            raise FileNotFoundError(f"reel MP4 not found: {reel_mp4_path}")
        if reel_mp4_path.suffix.lower() != ".mp4":
            raise ValueError(f"reel path must be .mp4: {reel_mp4_path}")

        # Sanity-check the MP4 before handing its URL to IG; catches
        # a 0-byte or corrupt file without burning a Graph API call.
        _ffprobe_sanity(reel_mp4_path)

        # Commit the MP4 to farm-2026 + push. Reuses the same helper
        # as photos/stories; its extension whitelist now includes .mp4.
        committed_path, raw_url = commit_image_to_farm_2026(
            local_image=reel_mp4_path,
            subdir=subdir,
            repo_path=farm_2026_repo_path,
            commit_message=(
                f"public/photos/{subdir}: reel from gems "
                f"{gem_ids[:3]}{'...' if len(gem_ids) > 3 else ''} (ig auto)"
            ),
        )
        result["raw_url"] = raw_url

        creds = _load_credentials()

        container_id = _create_reel_container(
            ig_id=creds["ig_id"],
            video_url=raw_url,
            caption=caption,
            user_token=creds["user_token"],
        )
        log.info("ig_poster: reel container created %s", container_id)

        # Reels take 30–60s to reach FINISHED; use the longer window
        # and a larger poll interval to avoid hammering the endpoint.
        status = _wait_for_container(
            container_id,
            creds["user_token"],
            timeout_s=_REEL_CONTAINER_TIMEOUT_S,
            poll_interval_s=_REEL_CONTAINER_POLL_S,
        )
        if status != "FINISHED":
            raise RuntimeError(
                f"reel container {container_id} ended in status={status} (expected FINISHED)"
            )

        pub = _publish(creds["ig_id"], container_id, creds["user_token"])
        result["media_id"] = pub["media_id"]
        result["permalink"] = pub["permalink"]
        result["posted_at"] = pub["timestamp"]

        # Propagate the reel's permalink + posted_at to each source gem
        # so should_post_ig's 3h/12h cadence gates naturally prevent
        # re-posting those frames as standalone photos. Best-effort:
        # a DB write failure here doesn't invalidate the already-live
        # IG post.
        for gid in gem_ids:
            try:
                _write_permalink(
                    db_path=db_path,
                    gem_id=gid,
                    permalink=pub["permalink"],
                    posted_at_iso=pub["timestamp"],
                )
            except Exception as e:
                log.warning(
                    "ig_poster: failed to write reel permalink to gem %s: %s",
                    gid, e,
                )
        log.info("ig_poster: posted reel -> %s", pub["permalink"])

    except IGPosterError:
        raise
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        log.exception("ig_poster: reel post failed")

    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def post_gem_to_ig(
    gem_id: int,
    full_caption: str,
    db_path: Path,
    farm_2026_repo_path: Path,
    dry_run: bool = False,
) -> dict:
    """Post a gem to Instagram. The full caption (journal body +
    hashtags, if any) is built by the caller — Phase 4 ships without
    auto-hashtag selection; Phase 6 adds that.

    Flow:
      1. Look up the gem row in image_archive.
      2. Resolve the local JPEG path.
      3. Commit the JPEG into farm-2026/public/photos/brooder/ and
         derive the GitHub raw URL.
      4. Load credentials.
      5. POST /media → container id.
      6. Poll container until FINISHED.
      7. POST /media_publish → media id + permalink.
      8. UPDATE image_archive with permalink and posted_at.

    dry_run=True stops BEFORE step 3's git push — the image is copied
    into farm-2026/public/photos/ as a working-tree change but NOT
    committed or pushed; NO Graph API call happens. Still returns the
    computed raw_url and the full caption for operator review.

    Returns (never raises except at _load_credentials entry gate):
      {
        "gem_id": int,
        "dry_run": bool,
        "raw_url": str | None,
        "caption": str,
        "media_id": str | None,
        "permalink": str | None,
        "posted_at": str | None,
        "error": str | None,
      }
    """
    result: dict = {
        "gem_id": gem_id,
        "dry_run": dry_run,
        "raw_url": None,
        "caption": full_caption,
        "media_id": None,
        "permalink": None,
        "posted_at": None,
        "error": None,
    }

    try:
        if len(full_caption) > _IG_CAPTION_MAX_CHARS:
            raise ValueError(
                f"caption is {len(full_caption)} chars; IG max is {_IG_CAPTION_MAX_CHARS}"
            )

        # 1. Look up gem
        gem = _lookup_gem(db_path, gem_id)
        if not gem:
            raise ValueError(f"gem_id {gem_id} not found in image_archive")

        # 2. Resolve local JPEG
        local_path = _local_path_for_gem(gem, db_path)

        # 3. Commit to farm-2026 (or predict URL only, for dry_run)
        subdir = "brooder"  # only scene we auto-map to today; Phase 6 expands
        stamped_name = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-gem{gem_id}.jpg"

        if dry_run:
            # Zero side effects. Predict the URL that WOULD exist if the
            # commit path ran, but don't touch the farm-2026 working tree
            # at all — no file copy, no git call, no IG API call.
            result["raw_url"] = (
                f"https://raw.githubusercontent.com/VoynichLabs/farm-2026/main/"
                f"public/photos/{subdir}/{stamped_name}"
            )
            log.info(
                "ig_poster DRY RUN: would commit %s -> %s and post with caption: %r",
                local_path, result["raw_url"], full_caption[:80],
            )
            return result

        # 3 (real). Commit to farm-2026 + push.
        # Copy to a temp-named file first so git_helper sees the intended
        # filename as the destination basename.
        import shutil as _shutil
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            staging = Path(td) / stamped_name
            _shutil.copy2(local_path, staging)
            committed_path, raw_url = commit_image_to_farm_2026(
                local_image=staging,
                subdir=subdir,
                repo_path=farm_2026_repo_path,
                commit_message=f"public/photos/{subdir}: gem {gem_id} (ig auto)",
            )
        result["raw_url"] = raw_url

        # 4. Credentials
        creds = _load_credentials()

        # 5. Container
        container_id = _create_container(
            ig_id=creds["ig_id"],
            image_url=raw_url,
            caption=full_caption,
            user_token=creds["user_token"],
        )
        log.info("ig_poster: container created %s", container_id)

        # 6. Wait for FINISHED
        status = _wait_for_container(container_id, creds["user_token"])
        if status != "FINISHED":
            raise RuntimeError(
                f"container {container_id} ended in status={status} (expected FINISHED)"
            )

        # 7. Publish
        pub = _publish(creds["ig_id"], container_id, creds["user_token"])
        result["media_id"] = pub["media_id"]
        result["permalink"] = pub["permalink"]
        result["posted_at"] = pub["timestamp"]

        # 8. Write back to DB
        _write_permalink(
            db_path,
            gem_id,
            permalink=pub["permalink"],
            posted_at_iso=pub["timestamp"],
        )
        log.info("ig_poster: posted gem %s -> %s", gem_id, pub["permalink"])

    except IGPosterError:
        # Credential missing — loud failure, escape the caught-all path.
        raise
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        log.exception("ig_poster: gem %s failed", gem_id)

    return result
