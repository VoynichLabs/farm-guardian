# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Post strong-tier frames to the #farm-2026 Discord channel as they
#          land. Called from orchestrator.run_cycle whenever store returns
#          tier=strong. Failures here must NEVER break the pipeline cycle —
#          the post is fire-and-log.
#
#          v2.37.0 adds a strict per-camera gate for the laptop webcams
#          (mba-cam, gwtc) — Boss flagged that they were flooding Discord
#          with sleeping-chick frames. The strict gate is shadow-logged
#          alongside the legacy gate so both decisions can be reviewed
#          before flipping `strict_gate_enabled: true` in config.json.
# SRP/DRY check: Pass — single responsibility is building the multipart post
#                and deciding whether to send it. Reuses the webhook+payload
#                shape documented in docs/skills-farm-2026-discord-post.md.

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("pipeline.gem_poster")

# Username shown on each Discord post, keyed by camera name. Matches the
# convention from docs/skills-farm-2026-discord-post.md.
_USERNAME_BY_CAMERA = {
    "s7-cam": "S7 Brooder",
    "house-yard": "Yard",
    "mba-cam": "Brooder Overhead",
    "usb-cam": "Brooder Floor",
    "gwtc": "Coop",
}


_CONFIG_PATH = Path(__file__).parent / "config.json"

# Default per-camera strict rules. Used when `per_camera_rules` is absent
# from the live config.json. Only mba-cam and gwtc (the two laptop webcams
# that flood Discord with sleeping-chick frames) are tightened; every other
# camera has NO entry and therefore falls through to the legacy gate.
_DEFAULT_PER_CAMERA_RULES: dict[str, dict] = {
    "mba-cam": {
        "require_face": True,
        "allowed_quality": ["sharp"],
        "reject_activities": ["sleeping", "resting", "unclear"],
        "interesting_activities": [
            "huddled", "calling", "scuffling", "active",
            "alert", "eating", "drinking", "preening",
        ],
        "require_interest": True,
        "min_bird_count_if_no_interest": 2,
    },
    "gwtc": {
        "require_face": True,
        "allowed_quality": ["sharp"],
        "reject_activities": ["sleeping", "resting", "unclear"],
        "interesting_activities": [
            "huddled", "calling", "scuffling", "active",
            "alert", "eating", "drinking", "preening",
        ],
        "require_interest": True,
        "min_bird_count_if_no_interest": 2,
    },
}


def _load_pipeline_config() -> dict:
    """Read tools/pipeline/config.json once per call. File is tiny; skipping
    the cache keeps tests trivially overridable via monkeypatching this
    function, and keeps the live process always-current if the boss edits
    config at runtime."""
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _get_per_camera_rules() -> dict[str, dict]:
    """Merged rules: config.json's `per_camera_rules` overrides the
    defaults per-camera. A camera absent from both falls through to the
    legacy gate (no strict rule applied)."""
    cfg_rules = _load_pipeline_config().get("per_camera_rules") or {}
    if not isinstance(cfg_rules, dict):
        return _DEFAULT_PER_CAMERA_RULES
    merged = dict(_DEFAULT_PER_CAMERA_RULES)
    for cam, rules in cfg_rules.items():
        if isinstance(rules, dict):
            merged[cam] = rules
    return merged


def _strict_gate_enabled() -> bool:
    return bool(_load_pipeline_config().get("strict_gate_enabled", False))


def _evaluate_legacy(vlm_metadata: dict, camera_id: Optional[str]) -> bool:
    """v2.36.4 legacy gate. Preserved verbatim so shadow-logging gives a
    clean apples-to-apples comparison against the strict path."""
    iq = vlm_metadata.get("image_quality")
    bc = vlm_metadata.get("bird_count", 0)
    sw = vlm_metadata.get("share_worth")
    face_visible = bool(vlm_metadata.get("bird_face_visible"))

    if sw == "skip":
        return False
    if not isinstance(bc, int) or bc < 1:
        return False
    if iq == "sharp":
        return True
    if iq == "soft" and camera_id != "s7-cam" and camera_id is not None:
        return face_visible or bc >= 2
    return False


def _evaluate_strict(vlm_metadata: dict, camera_id: Optional[str]) -> bool:
    """Strict per-camera gate for the laptop webcams (mba-cam, gwtc).

    If the camera has no rule entry, return True — we don't shadow-gate
    cameras we aren't trying to tighten. The caller decides whether to
    act on that True (strict_gate_enabled flag + should_post fall-through
    to legacy for un-ruled cameras)."""
    rules = _get_per_camera_rules().get(camera_id or "")
    if rules is None:
        return True

    sw = vlm_metadata.get("share_worth")
    if sw == "skip":
        return False

    bc = vlm_metadata.get("bird_count", 0)
    if not isinstance(bc, int) or bc < 1:
        return False

    iq = vlm_metadata.get("image_quality")
    allowed_quality = rules.get("allowed_quality", ["sharp"])
    if iq not in allowed_quality:
        return False

    if rules.get("require_face") and not bool(vlm_metadata.get("bird_face_visible")):
        return False

    # Backward-compat defaults: if the VLM hasn't been redeployed with the
    # new schema, these fields are missing and we lean conservative —
    # `unclear` activity is in the default reject list; `medium` interest
    # neither saves nor kills a frame on its own.
    activity = vlm_metadata.get("bird_activity", "unclear")
    reject_activities = rules.get("reject_activities", [])
    if activity in reject_activities:
        return False

    if rules.get("require_interest"):
        interest = vlm_metadata.get("scene_interest", "medium")
        interesting = rules.get("interesting_activities", [])
        min_crowd = rules.get("min_bird_count_if_no_interest", 2)
        if activity in interesting:
            return True
        if interest == "high":
            return True
        if bc >= min_crowd and interest != "low":
            return True
        return False

    return True


def would_post_strict(vlm_metadata: dict, tier: str, camera_id: Optional[str] = None) -> bool:
    """Strict-gate decision regardless of feature-flag state. Used for
    shadow-logging and for direct callers that want the new semantics
    without toggling the global flag."""
    return _evaluate_strict(vlm_metadata, camera_id)


def should_post(vlm_metadata: dict, tier: str, camera_id: Optional[str] = None) -> bool:
    """Discord-post predicate. Runs BOTH the legacy v2.36.4 gate and the
    new strict per-camera gate every call, logs both decisions for later
    validation, then returns whichever the `strict_gate_enabled` flag
    selects.

    Strict gate (v2.37.0) — only active for cameras listed in
    `per_camera_rules` (defaults: mba-cam, gwtc). For every other camera
    the strict path returns True (i.e. permissive), so when the flag is
    flipped on, only the tightened cameras see behavior change; s7-cam /
    usb-cam / house-yard continue to post as before.

    Legacy gate (v2.36.4, preserved):
      - share_worth != 'skip'
      - bird_count >= 1
      - image_quality 'sharp' accepts universally
      - image_quality 'soft' accepts on non-s7-cam IFF face OR bird_count>=2
      - image_quality 'blurred' rejects

    Shadow-logging: every call emits one INFO line with camera_id, both
    decisions, and the five metadata fields the gate looks at. Boss flips
    strict_gate_enabled=True in tools/pipeline/config.json once the logs
    look right."""
    legacy_decision = _evaluate_legacy(vlm_metadata, camera_id)
    strict_decision = _evaluate_strict(vlm_metadata, camera_id)

    log.info(
        "gate: camera=%s legacy=%s strict=%s iq=%s bc=%s face=%s activity=%s interest=%s share_worth=%s",
        camera_id,
        legacy_decision,
        strict_decision,
        vlm_metadata.get("image_quality"),
        vlm_metadata.get("bird_count"),
        vlm_metadata.get("bird_face_visible"),
        vlm_metadata.get("bird_activity", "unclear"),
        vlm_metadata.get("scene_interest", "medium"),
        vlm_metadata.get("share_worth"),
    )

    if _strict_gate_enabled():
        return strict_decision
    return legacy_decision


def load_dotenv(path: Path) -> None:
    """Minimal .env reader — no python-dotenv dependency. Sets os.environ
    only for keys not already present, so launchd-injected vars win."""
    import os
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def post_gem(
    image_bytes: bytes,
    caption: str,
    camera_name: str,
    webhook_url: str,
    timeout: int = 20,
) -> bool:
    """POST the image + caption to a Discord webhook as a multipart attachment.
    Returns True on 2xx, False otherwise. Never raises."""
    if not webhook_url:
        log.debug("gem_poster: no webhook configured, skipping %s", camera_name)
        return False
    username = _USERNAME_BY_CAMERA.get(camera_name, camera_name)
    content = caption if caption else f"New {camera_name} gem."
    # Discord content length cap is 2000; captions are already ≤200 in the
    # schema, so this is belt-and-suspenders.
    if len(content) > 1900:
        content = content[:1900] + "…"
    try:
        r = requests.post(
            webhook_url,
            files={"file": (f"{camera_name}-gem.jpg", image_bytes, "image/jpeg")},
            data={"payload_json": json.dumps({"username": username, "content": content})},
            timeout=timeout,
        )
    except requests.RequestException as e:
        log.warning("gem_poster: %s request failed: %s", camera_name, e)
        return False
    if 200 <= r.status_code < 300:
        log.info("gem_poster: posted %s gem (%d bytes, http=%d)",
                 camera_name, len(image_bytes), r.status_code)
        return True
    log.warning("gem_poster: %s post failed http=%d body=%r",
                camera_name, r.status_code, (r.text or "")[:200])
    return False
