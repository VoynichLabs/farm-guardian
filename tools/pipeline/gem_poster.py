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


def should_post(vlm_metadata: dict, tier: str) -> bool:
    """Gem predicate, refined across the 2026-04-16 evening session:

      v2.28.3  tier=strong OR (tier=decent + bird_count>=2)  'multiple faces'
      v2.28.5  sharp + bird_count>=1  (dropped tier gate)    'nothing posts'
      v2.28.6  + bird_face_visible                           'not its fluffy ass'
      v2.28.7           drop face requirement                'VLM cannot reliably tell what a face is'
      v2.36.3  (this)   + share_worth != 'skip'              'butts still slipping through; lean on VLM skip judgment'

    2026-04-22: Boss flagged a sharp-but-butt-forward s7-cam frame landing
    in #farm-2026. Root cause: predicate checked image_quality + bird_count
    but ignored the VLM's holistic `share_worth` verdict, even though the
    prompt explicitly tells Gemma-4 that butt-forward huddles are a
    skip-demote regardless of sharpness. We're not re-adding
    bird_face_visible (v2.28.6's failure mode still applies — the flag is
    noisy per-frame). Instead we use `share_worth` because:
      - it's a holistic judgment the VLM already makes
      - the prompt's skip rules already call out fluffy-butt piles
      - we've already paid the VLM call; this is a free extra signal
    Accepted risk: if the VLM mis-tags a butt shot as 'decent' or
    'strong', it still posts. Next lever if that recurs is prompt-side
    (sharpen the skip clause), not more code gates.

      - image_quality NOT 'sharp'  → skip (compression-artifact defense)
      - bird_count < 1             → skip (empty frame)
      - share_worth == 'skip'      → skip (VLM's own 'not archive-worthy'
                                          verdict — catches butt-forward
                                          and no-subject frames)
      - otherwise                  → post"""
    iq = vlm_metadata.get("image_quality")
    bc = vlm_metadata.get("bird_count", 0)
    sw = vlm_metadata.get("share_worth")
    if iq != "sharp":
        return False
    if not isinstance(bc, int) or bc < 1:
        return False
    if sw == "skip":
        return False
    return True


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
