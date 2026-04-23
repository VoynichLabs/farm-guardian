# Author: Claude Opus 4.7 (1M context)
# Date: 22-April-2026
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


def should_post(vlm_metadata: dict, tier: str, camera_id: Optional[str] = None) -> bool:
    """Gem predicate, refined across the 2026-04-16 evening session:

      v2.28.3  tier=strong OR (tier=decent + bird_count>=2)  'multiple faces'
      v2.28.5  sharp + bird_count>=1  (dropped tier gate)    'nothing posts'
      v2.28.6  + bird_face_visible                           'not its fluffy ass'
      v2.28.7           drop face requirement                'VLM cannot reliably tell what a face is'
      v2.36.3           + share_worth != 'skip'              'butts still slipping through; lean on VLM skip judgment'
      v2.36.4  (this)   per-camera sharpness tolerance       's7 strict; others allow soft when faces or >=2 birds'

    2026-04-22: Boss flagged that usb-cam / mba-cam / gwtc gems that are
    'a little blurry but pretty good' (faces visible, multiple birds) never
    reach Discord because the sharp-only gate rejects them. s7-cam, by
    contrast, produces consistently sharp frames — leave it alone. So the
    gate now branches on camera_id:

      - s7-cam (+ any camera_id we don't recognize as non-s7):
          image_quality must be 'sharp' and bird_face_visible must be
          True. Rear-only or wing-only S7 frames do not post.
      - every other camera (usb-cam, mba-cam, gwtc, house-yard, iphone-cam):
          image_quality may be 'sharp' OR 'soft', but if 'soft' we also
          require a face signal — either bird_face_visible=True, or
          bird_count>=2 (proxy for 'crowd, some face is likely visible').
          'blurred' still rejected; soft-without-faces still rejected.

    bird_face_visible was pulled in v2.28.6 because Gemma-4's flag was
    noisy. We're on qwen3.6-35b-a3b now and Boss has been eyeballing
    output for weeks — the flag is acceptable to him. And the multi-bird
    fallback (bird_count>=2) keeps content flowing even if the face flag
    is wrong on a given frame.

    share_worth != 'skip' still applies universally — the VLM's own
    butt-forward / not-archive-worthy verdict wins.

    Non-sharp rules:
      - image_quality 'blurred' → reject (always)
      - image_quality 'soft'     → reject for s7-cam; on others, require
                                   bird_face_visible OR bird_count>=2
      - image_quality 'sharp'    → accept on non-s7; on s7 require
                                   bird_face_visible=True
    Bird rules:
      - bird_count < 1           → reject
    Holistic:
      - share_worth == 'skip'    → reject"""
    iq = vlm_metadata.get("image_quality")
    bc = vlm_metadata.get("bird_count", 0)
    sw = vlm_metadata.get("share_worth")
    face_visible = bool(vlm_metadata.get("bird_face_visible"))

    if sw == "skip":
        return False
    if not isinstance(bc, int) or bc < 1:
        return False

    # Camera-specific sharpness tolerance. s7-cam (the consistently-sharp
    # source Boss trusts) keeps the strict rule; other cameras get a
    # face-signal fallback on 'soft' frames.
    if iq == "sharp":
        if camera_id == "s7-cam" and not face_visible:
            return False
        return True
    if iq == "soft" and camera_id != "s7-cam" and camera_id is not None:
        return face_visible or bc >= 2
    return False


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
