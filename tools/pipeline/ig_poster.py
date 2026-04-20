# Author: Claude Opus 4.7 (1M context)
# Date: 20-April-2026
# PURPOSE: Post curated gems to Instagram @pawel_and_pawleen via Meta
#          Graph API. Parallels gem_poster.py (which posts to Discord)
#          but with a multi-step container+publish flow required by
#          Instagram, plus the farm-2026 git-commit hop that produces
#          an IG-fetcher-compatible GitHub raw URL.
#
#          Phase 4 scope (20-Apr-2026): core posting flow —
#          _load_credentials, _create_container, _publish,
#          _wait_for_container, _write_permalink, post_gem_to_ig.
#          Accepts a pre-built full caption (journal body + hashtags)
#          from the caller; auto-hashtag selection lives in Phase 6.
#
#          Failures here must NEVER break the pipeline cycle — all
#          exceptions caught and returned in the `error` field of the
#          result dict. The only exception that can escape
#          post_gem_to_ig() is credential-missing at the entry gate
#          (loud failure on misconfiguration beats silent no-op).
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
#                + publish flow. DRY: reuses git_helper.py for the
#                farm-2026 commit hop; reuses the existing .env file
#                from the 2026-04-19 manual work rather than
#                reinventing secret storage.

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tools.pipeline.git_helper import GitHelperError, commit_image_to_farm_2026

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
    """Resolve the on-disk full-res JPEG for a gem.

    store.py writes image_path relative to the archive's parent dir.
    We reconstruct the absolute path by combining db_path.parent (the
    data/ dir) with image_path. Raises FileNotFoundError if the file
    doesn't exist on disk — typically means the archive retention
    sweep already deleted it.
    """
    image_path = gem_row.get("image_path")
    if not image_path:
        raise FileNotFoundError(
            f"gem {gem_row.get('id')} has no image_path on disk (skip tier?)"
        )

    # image_path is relative to data/ — i.e. starts with "archive/YYYY-MM/..."
    # db_path is typically data/guardian.db, so db_path.parent is data/.
    candidate = (db_path.parent / image_path).resolve()
    if candidate.exists():
        return candidate

    # Fallback: strong-tier gems also hardlinked into gems/ — try there.
    fname = Path(image_path).name
    ym = Path(image_path).parts[-3] if len(Path(image_path).parts) >= 3 else ""
    cam = Path(image_path).parts[-2] if len(Path(image_path).parts) >= 2 else ""
    if ym and cam:
        alt = (db_path.parent / "gems" / ym / cam / fname).resolve()
        if alt.exists():
            return alt

    raise FileNotFoundError(
        f"gem {gem_row.get('id')}: image not found at {candidate} or gems/ fallback"
    )


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
