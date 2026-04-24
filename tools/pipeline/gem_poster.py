# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Post strong-tier frames to the #farm-2026 Discord channel as they
#          land. Called from orchestrator.run_cycle whenever store returns
#          tier=strong. Failures here must NEVER break the pipeline cycle —
#          the post is fire-and-log.
# SRP/DRY check: Pass — single responsibility is building the multipart post.
#                Reuses the webhook+payload shape documented in
#                docs/skills-farm-2026-discord-post.md.

from __future__ import annotations

import json
import logging
import re
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


# Cameras whose frames are hard-disabled from Discord gem posting,
# regardless of VLM verdict. 24-Apr-2026: Boss asked to pull mba-cam
# (the MBA FaceTime HD overhead-brooder view) out of the gem lane
# entirely — its frames are consistently low-quality (fixed-focus,
# 720p, heat-lamp overexposure) and "basically all of them look the
# same." Cadence is also dropped to 30 min in config so VLM cost is
# minimal, but this gate is the belt-and-suspenders block on anything
# that still slips through. Keep in sync with `gem_post_enabled: false`
# entries in tools/pipeline/config.json — the config flag is currently
# documentary; this set is the actual enforcement point.
_GEM_POST_DISABLED_CAMERAS = frozenset({"mba-cam"})

# Non-s7 cameras rejected at these activity/composition tags even when the
# VLM self-approves them as strong. Huddle/sleep/empty frames are the
# single largest noise source in #farm-2026 per 23-Apr-2026 review.
_REJECT_ACTIVITIES_NON_S7 = frozenset({"huddling", "sleeping", "none-visible", "other"})
_REJECT_COMPOSITIONS_NON_S7 = frozenset({"cluttered", "empty"})

# Minimum "biggest-bird percent of frame" for each camera. Per Boss
# 2026-04-23: "I'm seeing a lot of images where the bird is 20% and
# 80% is background, especially from MBA and GWTC." Thresholds are
# per-camera because the rigs see different distances:
#   mba-cam = brooder overhead, close — birds should be at least 15%
#   gwtc    = coop, birds are further from the lens — require 25%
#             because anything smaller reads as "distant coop shot"
# Cameras not in this dict are NOT gated on subject size.
# s7-cam is intentionally absent (Boss said don't touch it).
_LARGEST_SUBJECT_PCT_MIN = {
    "mba-cam": 15,
    "gwtc":    25,
}

# Caption hygiene. The prompt already lists "bad captions to avoid"; the
# gate enforces three specific patterns Boss has flagged:
#   - "A group of [adj]* chicks/birds/chickens/poults..."       (the
#     dominant mba-cam huddle-caption shape — matches even if there's a
#     trailing location phrase like "...under the heat lamp.")
#   - "(Cute|Fluffy|Tiny|Small) baby (chicks|birds)"
#   - "(Chicks|Baby chicks|Birds|Baby birds) in the (brooder|coop|yard)"
# Deliberately NOT matching "A small chick..." alone — singular-subject
# captions are usually followed by specifics ("...with orange markings",
# "...pecking at the feeder") and rejecting them would kill legit frames.
_GENERIC_CAPTION_RE = re.compile(
    r"^\s*("
    r"a\s+group\s+of\s+(?:small\s+|fluffy\s+|cute\s+|tiny\s+|little\s+)*"
    r"(?:chicks|birds|chickens|poults|baby\s+birds|baby\s+chicks)"
    r"|"
    r"(?:cute|fluffy|tiny|small|little)\s+baby\s+(?:chicks|birds)"
    r"|"
    r"(?:chicks|baby\s+chicks|birds|baby\s+birds)\s+"
    r"(?:in|under|near|by)\s+the\s+"
    r"(?:brooder|coop|yard|heat\s+lamp|feeder|waterer|bedding)"
    r")",
    re.IGNORECASE,
)


def _caption_is_clean(caption: str) -> tuple[bool, Optional[str]]:
    """Caption hygiene gate. Returns (ok, reason). Non-ASCII trips the
    leak check (seen 2026-04-23: qwen emitted CJK `籠` mid-caption).
    Generic-opener regex catches the VLM's default-mode output."""
    if not caption:
        return True, None
    try:
        caption.encode("ascii")
    except UnicodeEncodeError:
        return False, "skip_non_ascii_caption"
    if _GENERIC_CAPTION_RE.match(caption):
        return False, "skip_generic_caption"
    return True, None


def _reject(camera_id: Optional[str], reason: str) -> bool:
    """Log the rejection reason at DEBUG and return False. Central funnel so
    every skip path is traceable when Boss asks why a frame didn't post."""
    log.debug("gem_gate: %s rejected (%s)", camera_id or "?", reason)
    return False


def should_post(vlm_metadata: dict, tier: str, camera_id: Optional[str] = None) -> bool:
    """Gem predicate — what lands in #farm-2026 Discord for Boss to curate.

    History (see docs/23-Apr-2026-gem-gate-tightening-plan.md for rationale):
      v2.28.3  tier=strong OR (tier=decent + bird_count>=2)  'multiple faces'
      v2.28.5  sharp + bird_count>=1  (dropped tier gate)    'nothing posts'
      v2.28.6  + bird_face_visible                           'not its fluffy ass'
      v2.28.7           drop face requirement                'VLM cannot reliably tell a face'
      v2.36.3           + share_worth != 'skip'              'butts still slipping'
      v2.36.4           per-camera sharpness tolerance       's7 strict; others allow soft+face'
      v2.37.2  (this)   non-s7 activity/composition/caption  'huddle blobs + generic captions'

    v2.37.2 additions (non-s7 only; s7-cam logic unchanged — it's already
    strict and Boss trusts its output):
      - Reject activity ∈ {huddling, sleeping, none-visible, other}.
        Huddle/sleep piles are the fluffy-ass blob Boss keeps flagging.
      - Reject composition ∈ {cluttered, empty}.
      - Reject caption matching generic-opener regex ("A group of fluffy
        chicks...", "Chicks in the brooder.", "Cute baby birds.") or
        containing non-ASCII (qwen CJK leak 2026-04-23).

    Intentionally NOT a bird_count cap. MBA/GWTC produce great frames
    where one bird poses close-to-lens and others are in the background
    with high total bird_count; killing those by count would be a
    regression. Huddle blobs are caught by the activity gate above.

    Universal rules (all cameras):
      - share_worth == 'skip'  → reject
      - bird_count < 1         → reject
      - image_quality 'blurred'→ reject

    Sharpness branch (unchanged):
      - sharp: s7-cam requires face_visible; others accept
      - soft:  s7-cam rejects; others require face_visible OR bird_count>=2
      - blurred: always reject"""
    iq = vlm_metadata.get("image_quality")
    bc = vlm_metadata.get("bird_count", 0)
    sw = vlm_metadata.get("share_worth")
    activity = vlm_metadata.get("activity")
    composition = vlm_metadata.get("composition")
    caption = vlm_metadata.get("caption_draft", "") or ""
    face_visible = bool(vlm_metadata.get("bird_face_visible"))

    # Hard block for cameras explicitly disabled from the gem lane. This
    # runs first so there's no chance a low-quality MBA frame sneaks
    # through any of the nuanced checks below.
    if camera_id in _GEM_POST_DISABLED_CAMERAS:
        return _reject(camera_id, "skip_camera_disabled_for_gems")

    if sw == "skip":
        return _reject(camera_id, "skip_share_worth")
    if not isinstance(bc, int) or bc < 1:
        return _reject(camera_id, "skip_no_birds")

    # Non-s7 semantic gates. s7-cam is already strict on sharp+face and
    # Boss has explicitly asked not to touch it.
    #
    # NOTE: No group-size cap. The MBA + GWTC produce Boss's favourite
    # framings when a chick poses close to the lens with the rest of the
    # flock in the background — the VLM tags these `composition=portrait`
    # or `group` with high bird_count, and we want them through. Huddle
    # blobs are already killed by the activity gate below.
    is_non_s7 = camera_id is not None and camera_id != "s7-cam"
    if is_non_s7:
        if activity in _REJECT_ACTIVITIES_NON_S7:
            return _reject(camera_id, f"skip_activity={activity}")
        if composition in _REJECT_COMPOSITIONS_NON_S7:
            return _reject(camera_id, f"skip_composition={composition}")
        clean, why = _caption_is_clean(caption)
        if not clean:
            return _reject(camera_id, f"{why}: {caption[:60]!r}")

        # Subject-size gate (added 2026-04-23 per Boss — "I'm seeing
        # images where a bird is 20% of the frame and the other 80%
        # is empty coop"). Uses the VLM's largest_subject_pct field.
        # Missing field (pre-v2.38 VLM output) → treated as pass so
        # we don't mass-reject while the schema rolls out.
        threshold = _LARGEST_SUBJECT_PCT_MIN.get(camera_id)
        if threshold is not None:
            largest = vlm_metadata.get("largest_subject_pct")
            if isinstance(largest, int) and largest < threshold:
                return _reject(
                    camera_id,
                    f"largest_subject_pct={largest} < {threshold}",
                )

    # Sharpness branch (unchanged from v2.36.4).
    if iq == "sharp":
        if camera_id == "s7-cam" and not face_visible:
            return _reject(camera_id, "s7_sharp_no_face")
        return True
    if iq == "soft" and is_non_s7:
        if face_visible or bc >= 2:
            return True
        return _reject(camera_id, "soft_no_face_no_crowd")
    return _reject(camera_id, f"image_quality={iq}")


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
