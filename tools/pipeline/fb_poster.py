# Author: Claude Opus 4.7 (1M context)
# Date: 20-April-2026
# PURPOSE: Cross-post finished IG content to the linked Facebook Page
#          "Yorkies App" (page_id 614607655061302) via Graph API v25.0.
#          Called from tools/pipeline/ig_poster.py at the tail of each
#          successful IG publish lane (photo / carousel / story / reel).
#
#          Four public entry points, one per IG lane:
#            - crosspost_photo(image_url, caption)
#            - crosspost_carousel(image_urls, caption)
#            - crosspost_photo_story(image_url)
#            - crosspost_reel(video_url, caption)
#
#          Each returns {"ok": bool, "fb_post_id": str|None, "error": str|None}.
#          Never raises (except FBPosterError for missing credentials
#          when the caller explicitly asked us to run — this mirrors
#          ig_poster's loud-failure-on-misconfig pattern).
#
#          Credential source: same env file as ig_poster
#          (/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env
#          mirrored from keychain). We read FB_PAGE_ID and
#          LONG_LIVED_PAGE_TOKEN from os.environ; if missing, we source
#          the env file directly so the CLI entry point works
#          standalone.
#
#          The page token as of 2026-04-20 does NOT hold
#          `pages_manage_posts`; any publish attempt 400s with
#          (#200) permission error. Once Boss regenerates the token
#          via Graph Explorer (recipe in
#          ~/bubba-workspace/skills/farm-facebook-crosspost/SKILL.md),
#          the same code path succeeds with zero edits. Until then,
#          every attempt logs a warning and returns ok=false; IG posts
#          continue to succeed because the caller swallows fb_poster
#          failures.
#
# SRP/DRY check: Pass — SRP is "publish to Yorkies FB Page, full stop."
#                Does not know about gems, git, farm-2026, image prep,
#                or the IG API. DRY: duplicates a ~20-line urllib
#                Graph-API wrapper from ig_poster rather than
#                cross-importing — the two modules are peers, not
#                parent/child, and that coupling wasn't worth the
#                tightness.

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger("pipeline.fb_poster")

GRAPH_API_VERSION = "v25.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Same env file ig_poster uses. Absolute by design: farm-guardian is
# single-host on this Mac Mini. If ever ported, both modules become
# config-driven at that time, not before.
_META_ENV_FILE = Path("/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env")

_REQUIRED_ENV = {
    "page_id": "FB_PAGE_ID",
    "page_token": "LONG_LIVED_PAGE_TOKEN",
}


class FBPosterError(RuntimeError):
    """Raised on credential-missing at the entry gate. Non-credential
    Graph API failures are caught and returned in the result dict's
    error field."""


# ---------------------------------------------------------------------------
# Credential loading (mirrors ig_poster's two-step pattern)
# ---------------------------------------------------------------------------


def _source_meta_env_file(path: Path = _META_ENV_FILE) -> None:
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


def _load_fb_credentials() -> dict:
    creds = {k: os.environ.get(env) for k, env in _REQUIRED_ENV.items()}
    if all(creds.values()):
        return creds  # already in env

    _source_meta_env_file()
    creds = {k: os.environ.get(env) for k, env in _REQUIRED_ENV.items()}
    if all(creds.values()):
        return creds

    missing = [env for logical, env in _REQUIRED_ENV.items() if not os.environ.get(env)]
    raise FBPosterError(
        "fb_poster: missing required credentials: " + ", ".join(missing) + ".\n"
        f"Expected in env (orchestrator sources {_META_ENV_FILE} at startup).\n"
        "Token regeneration recipe: ~/bubba-workspace/skills/farm-facebook-crosspost/SKILL.md"
    )


# ---------------------------------------------------------------------------
# Graph API primitive
# ---------------------------------------------------------------------------


def _graph_request(
    method: str,
    path: str,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
    timeout: int = 30,
) -> dict:
    """Minimal Graph API wrapper. path is post-version, e.g. '/{page_id}/photos'.

    Returns parsed JSON on 2xx. On HTTPError, reads the body and raises
    RuntimeError with the Graph API error message surfaced — caller gets
    a useful string in the result dict instead of a bare urllib trace.
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

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
            err_json = json.loads(err_body)
            msg = err_json.get("error", {}).get("message", err_body)
        except Exception:
            msg = f"HTTP {e.code}"
        raise RuntimeError(f"graph {method} {path}: {msg}") from None


# ---------------------------------------------------------------------------
# Public entry points — one per IG lane
# ---------------------------------------------------------------------------


def crosspost_photo(image_url: str, caption: str) -> dict:
    """Publish a single photo to the FB Page. Returns
    {"ok": bool, "fb_post_id": str|None, "error": str|None}.

    fb_post_id is the Page-scoped post id (the shareable one), not the
    internal photo fbid — Graph returns both; we prefer post_id for
    permalink construction.
    """
    result: dict = {"ok": False, "fb_post_id": None, "error": None}
    try:
        creds = _load_fb_credentials()
        resp = _graph_request(
            "POST",
            f"/{creds['page_id']}/photos",
            body={
                "url": image_url,
                "caption": caption,
                "access_token": creds["page_token"],
            },
        )
        # post_id is "pageid_postid"; id is the photo fbid. Prefer post_id.
        result["fb_post_id"] = resp.get("post_id") or resp.get("id")
        result["ok"] = bool(result["fb_post_id"])
        if result["ok"]:
            log.info("fb_poster: photo posted -> %s", result["fb_post_id"])
        else:
            result["error"] = f"no post_id in response: {resp}"
    except FBPosterError:
        raise
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        log.warning("fb_poster: photo crosspost failed: %s", result["error"])
    return result


def crosspost_carousel(image_urls: list[str], caption: str) -> dict:
    """Publish a multi-photo Page post via the 2-step pattern:
    unpublished photo uploads, then /feed with attached_media.

    Matches the shape an IG carousel takes. Page posts don't have a
    native "carousel" media type; multi-image FB posts work via
    attached_media and render as a photo grid on the Page feed.
    """
    result: dict = {"ok": False, "fb_post_id": None, "error": None}
    try:
        creds = _load_fb_credentials()
        if not image_urls:
            raise ValueError("image_urls is empty")

        # Step A: upload each image unpublished, collect photo fbids.
        photo_fbids: list[str] = []
        for url in image_urls:
            resp = _graph_request(
                "POST",
                f"/{creds['page_id']}/photos",
                body={
                    "url": url,
                    "published": "false",
                    "access_token": creds["page_token"],
                },
            )
            fbid = resp.get("id")
            if not fbid:
                raise RuntimeError(f"unpublished photo upload returned no id: {resp}")
            photo_fbids.append(fbid)

        # Step B: create a feed post referencing all of them.
        # attached_media is an indexed form parameter: attached_media[0]={...}
        body: dict = {
            "message": caption,
            "access_token": creds["page_token"],
        }
        for i, fbid in enumerate(photo_fbids):
            body[f"attached_media[{i}]"] = json.dumps({"media_fbid": fbid})

        resp = _graph_request(
            "POST",
            f"/{creds['page_id']}/feed",
            body=body,
        )
        result["fb_post_id"] = resp.get("id")
        result["ok"] = bool(result["fb_post_id"])
        if result["ok"]:
            log.info(
                "fb_poster: carousel of %d photos posted -> %s",
                len(image_urls), result["fb_post_id"],
            )
        else:
            result["error"] = f"no id in /feed response: {resp}"
    except FBPosterError:
        raise
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        log.warning("fb_poster: carousel crosspost failed: %s", result["error"])
    return result


def crosspost_photo_story(image_url: str) -> dict:
    """Publish a 24-hour FB Page Story with a single photo.

    Two-step per Graph docs: upload unpublished photo, then POST
    /{page-id}/photo_stories with photo_id. No caption (Page Stories
    don't support one in the API, same as IG).
    """
    result: dict = {"ok": False, "fb_post_id": None, "error": None}
    try:
        creds = _load_fb_credentials()

        # Step A: unpublished photo upload.
        upload_resp = _graph_request(
            "POST",
            f"/{creds['page_id']}/photos",
            body={
                "url": image_url,
                "published": "false",
                "access_token": creds["page_token"],
            },
        )
        photo_fbid = upload_resp.get("id")
        if not photo_fbid:
            raise RuntimeError(f"story photo upload returned no id: {upload_resp}")

        # Step B: create story.
        resp = _graph_request(
            "POST",
            f"/{creds['page_id']}/photo_stories",
            body={
                "photo_id": photo_fbid,
                "access_token": creds["page_token"],
            },
        )
        result["fb_post_id"] = resp.get("post_id") or resp.get("id")
        result["ok"] = bool(result["fb_post_id"])
        if result["ok"]:
            log.info("fb_poster: photo story posted -> %s", result["fb_post_id"])
        else:
            result["error"] = f"no post_id in /photo_stories response: {resp}"
    except FBPosterError:
        raise
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        log.warning("fb_poster: story crosspost failed: %s", result["error"])
    return result


def crosspost_reel(video_url: str, caption: str) -> dict:
    """Publish a FB Page Reel (short-form vertical video).

    The documented 2026 path is a resumable upload; however, the
    /{page-id}/video_reels endpoint still accepts file_url for remote
    URL ingestion for small MP4s (our reels are typically 5-15MB, well
    under the threshold where resumable is required). If file_url ever
    stops working, we escalate to resumable here in v2.

    Graph flow:
      1. Start a reel upload session with upload_phase=start -> returns
         video_id + upload_url.
      2. Tell Meta to pull from our public file_url
         (upload_phase=transfer, file_url=<mp4 url>).
      3. Finish with upload_phase=finish + video_state=PUBLISHED.

    We collapse 1+2+3 into the single-call form that works for small
    publicly-hosted MP4s: POST /{page-id}/video_reels with
    upload_phase=start is step 1. Given our small file sizes and that
    file_url ingestion still works on /videos for the same account,
    we fall back to the legacy /{page-id}/videos endpoint which accepts
    file_url + description in one call. That produces a normal video
    post (not specifically branded as a Reel in FB UI) but visually
    identical, and avoids the multi-call reels dance.

    Trade-off accepted: FB-side it's tagged "Video" not "Reel"; the
    media is identical. Boss directive ("just dual post, I don't
    fucking care") supports this simpler path.
    """
    result: dict = {"ok": False, "fb_post_id": None, "error": None}
    try:
        creds = _load_fb_credentials()
        resp = _graph_request(
            "POST",
            f"/{creds['page_id']}/videos",
            body={
                "file_url": video_url,
                "description": caption,
                "access_token": creds["page_token"],
            },
            timeout=120,  # video ingestion is slower than photos
        )
        result["fb_post_id"] = resp.get("post_id") or resp.get("id")
        result["ok"] = bool(result["fb_post_id"])
        if result["ok"]:
            log.info("fb_poster: reel/video posted -> %s", result["fb_post_id"])
        else:
            result["error"] = f"no id in /videos response: {resp}"
    except FBPosterError:
        raise
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        log.warning("fb_poster: reel crosspost failed: %s", result["error"])
    return result


# ---------------------------------------------------------------------------
# Thin dispatcher used by ig_poster — honors config and swallows errors
# ---------------------------------------------------------------------------


def maybe_crosspost(
    kind: str,
    *,
    image_url: Optional[str] = None,
    image_urls: Optional[list[str]] = None,
    video_url: Optional[str] = None,
    caption: Optional[str] = None,
) -> dict:
    """Single entry used by ig_poster success paths. Honors the
    FB_CROSSPOST_ENABLED env var (default "1"); the orchestrator
    derives it from config.json's `facebook.crosspost_enabled` key.
    NEVER raises — all failures land in the returned dict.

    kind: "photo" | "carousel" | "story" | "reel"

    Returns {"ok", "fb_post_id", "error", "kind", "skipped_reason"}.
    """
    base: dict = {
        "ok": False,
        "fb_post_id": None,
        "error": None,
        "kind": kind,
        "skipped_reason": None,
    }
    if os.environ.get("FB_CROSSPOST_ENABLED", "1") == "0":
        base["skipped_reason"] = "FB_CROSSPOST_ENABLED=0"
        return base

    try:
        if kind == "photo":
            if not image_url or caption is None:
                raise ValueError("photo requires image_url + caption")
            r = crosspost_photo(image_url, caption)
        elif kind == "carousel":
            if not image_urls or caption is None:
                raise ValueError("carousel requires image_urls + caption")
            r = crosspost_carousel(image_urls, caption)
        elif kind == "story":
            if not image_url:
                raise ValueError("story requires image_url")
            r = crosspost_photo_story(image_url)
        elif kind == "reel":
            if not video_url or caption is None:
                raise ValueError("reel requires video_url + caption")
            r = crosspost_reel(video_url, caption)
        else:
            raise ValueError(f"unknown kind: {kind!r}")
        base.update(r)
    except FBPosterError as e:
        # Swallow — the IG success path must not be poisoned by a
        # missing FB cred. Loud-failure happens via the log.warning.
        base["error"] = f"FBPosterError: {e}"
        log.warning("fb_poster: credentials missing, cross-post skipped")
    except Exception as e:
        # Already handled inside the crosspost_* helpers; defensive.
        base["error"] = f"{type(e).__name__}: {e}"
        log.warning("fb_poster: dispatcher unexpected failure: %s", base["error"])
    return base
