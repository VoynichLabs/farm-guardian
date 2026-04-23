# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Generate a short, warm, "good neighbor" Nextdoor comment for a
#          given post by sending the post image + caption to the locally-
#          loaded Qwen3.6-VL on LM Studio. Mirrors tools/ig-engage/
#          comment_writer.py but with a DIFFERENT voice prompt — Nextdoor
#          is longer, mixed-case, properly punctuated, minimal emoji,
#          "older-neighbor" register. Comes with a curated fallback pool
#          that's distinct from IG's (never use the IG fallback line on
#          Nextdoor — the voice is wrong).
#
# SRP/DRY check: Pass — one module, one responsibility: "text for a Nextdoor
#                post". Callers feed us bytes + caption; we return a string
#                or None. VLM prompt is the only real delta from the IG
#                comment writer.

from __future__ import annotations

import base64
import logging
import random
from typing import Optional

import requests

log = logging.getLogger("nextdoor.comment_writer")

LM_STUDIO = "http://localhost:1234"
MODEL = "qwen/qwen3.6-35b-a3b"
TIMEOUT_S = 90

SYSTEM_PROMPT = (
    "You are helping a Hampton, Connecticut neighbor engage kindly on "
    "Nextdoor — the local neighborhood social network. You are looking at "
    "a post someone in the neighborhood shared. Write a single short, "
    "warm comment to leave on the post.\n\n"
    "Voice rules (absolute):\n"
    "- 1 to 3 sentences, proper mixed case and punctuation.\n"
    "- Warm 'good neighbor' register — like an older neighbor chatting "
    "across the fence, not a teenager commenting on Instagram.\n"
    "- NEVER lowercase-aesthetic, NEVER 'tho', NEVER 'vibes', NEVER "
    "'slay'. Grown-up voice.\n"
    "- 0 or at most 1 emoji, and only a tame one like ❤️ ☀️ 🐣 🌷. "
    "No 😍 🔥 😂.\n"
    "- A brief curiosity question about the post is great (it invites "
    "a reply and builds reciprocity). Example: 'Do you have a favorite "
    "early-spring flower for the front bed?'\n"
    "- Specific beats generic: name what you actually see.\n"
    "- NEVER commercial, NEVER include a link, NEVER recommend a "
    "product or service.\n"
    "- NEVER mention AI, Nextdoor itself, or that this is a cross-post.\n"
    "- NEVER reveal the neighbor's name, address, or exact location.\n"
    "- Never start with 'nice post', 'great post', 'lovely post'.\n"
    "- Output ONLY the comment text — no quotes, no label, no explanation."
)

RefusalMarkers = (
    "can't help",
    "cannot help",
    "i'm not able",
    "not appropriate",
    "as an ai",
    "i cannot",
    "i won't",
    "policy",
    "unable to",
)

# Hand-curated neighborhood-voice fallbacks for use when the VLM path fails
# (refusal, timeout, LM Studio down). Short, warm, mixed-case, no emoji
# spam. DO NOT copy the IG fallback list in here — wrong voice.
FALLBACK_COMMENTS = [
    "What a nice view — thanks for sharing.",
    "Lovely shot. ❤️",
    "That's a real New England morning right there.",
    "Such a sweet photo, thanks for posting.",
    "This made me smile — thank you.",
    "Beautiful. Spring is really showing up this week.",
    "What a pretty scene from the neighborhood.",
    "So nice to see. Thanks for sharing it.",
    "Looks wonderful — thanks for the update.",
    "That's a sight. Thank you for posting.",
]


def _looks_refused(text: str) -> bool:
    lowered = text.lower()
    return any(m in lowered for m in RefusalMarkers)


def write_comment(
    image_bytes: bytes,
    caption: str,
    recently_posted: Optional[set[str]] = None,
    temperature: float = 0.8,
    attempts: int = 2,
) -> Optional[str]:
    """Short "good neighbor" comment, or a fallback, or None on hard failure."""
    recently_posted = recently_posted or set()
    if not image_bytes or not caption:
        return random.choice(FALLBACK_COMMENTS)

    b64 = base64.b64encode(image_bytes).decode("ascii")
    dedupe_hint = ""
    if recently_posted:
        dedupe_hint = (
            "\n\nDo NOT reuse any of these comments (we already used them "
            f"recently): {sorted(recently_posted)[:20]}."
        )
    user_text = (
        f"Here's the post. Caption:\n> {caption}{dedupe_hint}\n\n"
        "Write the comment. Output only the comment text."
    )
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            },
        ],
        "temperature": temperature,
        "max_tokens": 140,
        "reasoning": "off",
    }

    for attempt in range(attempts):
        try:
            r = requests.post(
                f"{LM_STUDIO}/v1/chat/completions", json=body, timeout=TIMEOUT_S
            )
            if r.status_code != 200:
                log.warning("LM Studio HTTP %d: %s", r.status_code, r.text[:200])
                continue
            text = (r.json()["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:
            log.warning("LM Studio request failed (attempt %d): %s", attempt + 1, e)
            continue

        # Strip quote-wrapping the model sometimes adds.
        for wrap in ('"', "'", "“", "”", "‘", "’"):
            if text.startswith(wrap) and text.endswith(wrap) and len(text) > 2:
                text = text[1:-1].strip()

        if not text:
            continue
        if _looks_refused(text):
            log.warning("VLM refused: %r", text[:120])
            continue
        # Neighborhood voice is allowed to be longer than IG's but still cap
        # at ~280 chars — anything longer is monologue and reads as spam.
        if len(text) > 280:
            log.warning("VLM returned %d-char reply; rejecting: %r", len(text), text[:120])
            continue
        if text.lower() in recently_posted:
            continue
        return text

    log.warning("VLM failed; using fallback")
    pool = [c for c in FALLBACK_COMMENTS if c.lower() not in recently_posted]
    return random.choice(pool or FALLBACK_COMMENTS)
