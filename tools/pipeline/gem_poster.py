# Author: Claude Opus 4.7 (1M context)
# Date: 16-April-2026
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
    """Gem predicate per Boss 2026-04-16: 'anything that's like sharp and
    good. You can see multiple birds' little faces.' Hardened against
    VLM self-contradiction (sometimes says strong+sharp on a visibly
    corrupted GWTC frame) by ALWAYS requiring image_quality=sharp and
    bird_count>=1 regardless of tier, plus at least one visible bird.

      - image_quality NOT sharp           → skip (catches VLM mis-rating)
      - bird_count == 0                   → skip (empty frames)
      - tier=strong + above               → post
      - tier=decent + bird_count >= 2     → post (the "multiple little
        faces" bar; single-bird decent is not gem material)

    The image_quality==sharp requirement is the single most load-bearing
    check here — it stops auto-posts of H.264-corrupted frames that the
    VLM sometimes over-rates as 'strong' because the stripe edges look
    sharp at a glance. The prompt's compression-artifact clause is the
    first line of defense; this is the second."""
    iq = vlm_metadata.get("image_quality")
    bc = vlm_metadata.get("bird_count", 0)
    if iq != "sharp":
        return False
    if not isinstance(bc, int) or bc < 1:
        return False
    if tier == "strong":
        return True
    if tier == "decent" and bc >= 2:
        return True
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
