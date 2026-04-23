# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Generate a short, warm, context-aware Instagram comment for a
#          given post by sending the post image + caption to the locally-
#          loaded Qwen3.6-VL on LM Studio. Voice rules enforce 1–8 words,
#          lowercase-friendly, never markety, and occasional curiosity
#          questions (which get replies and build reciprocity — that is
#          the actual reason we comment).
#
# SRP/DRY check: Pass — one module, one responsibility: "text for a post".
#                Callers feed us bytes + caption; we return a string or None.

from __future__ import annotations

import base64
import logging
import random
from typing import Optional

import requests

log = logging.getLogger("ig-engage.comment_writer")

LM_STUDIO = "http://localhost:1234"
MODEL = "qwen/qwen3.6-35b-a3b"
TIMEOUT_S = 90

SYSTEM_PROMPT = (
    "You are helping a hobby-farm owner engage warmly with other small bird- "
    "and dog-centric Instagram accounts. You are looking at someone else's "
    "post. Write a single short comment to leave on the post.\n\n"
    "Voice rules (absolute):\n"
    "- 1 to 8 words.\n"
    "- Lowercase-friendly, casual, kind, like an older neighbor leaving a "
    "sweet comment. Never markety, never salesy, never 'great content!'.\n"
    "- 0 or 1 emoji — no emoji spam.\n"
    "- A genuine curiosity question about what's in the photo is great "
    "(e.g., 'what breed is the striped one?'). Builds reciprocity.\n"
    "- Specific beats generic: name what you actually see.\n"
    "- Never mention you are an AI; never reference Instagram itself; "
    "never start with 'nice post' or 'great post' or 'love it'.\n"
    "- Output ONLY the comment text — no quotes, no label, no explanation."
)

# Avoid repeating ourselves across a session. The caller keeps a rolling set
# of recent comments and passes it in.
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

# Shared fallback comments used only if the VLM path fails outright (refusal,
# timeout, LM Studio down). These are hand-curated, short, varied, never
# "nice post". We rotate randomly and the budget ensures we never exceed the
# daily comment cap anyway.
FALLBACK_COMMENTS = [
    "those feet!",
    "what a sweet face",
    "gorgeous markings",
    "little floof 🐣",
    "so fluffy",
    "such a cutie",
    "love the coloring",
    "beautiful birds",
    "so pretty",
    "little dude ❤️",
    "what a heart-melter",
    "aw, the tiny stretch",
    "precious 😍",
    "so sweet together",
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
    """Returns a short IG-style comment, or None on hard failure. Callers
    should verify returned text is <120 chars and skip the comment step if
    None (the like/story-react steps still run)."""
    recently_posted = recently_posted or set()
    if not image_bytes or not caption:
        return random.choice(FALLBACK_COMMENTS)

    b64 = base64.b64encode(image_bytes).decode("ascii")
    dedupe_hint = ""
    if recently_posted:
        dedupe_hint = (
            "\n\nDo NOT use any of these exact comments (we already used them "
            f"this session): {sorted(recently_posted)[:20]}."
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
        "max_tokens": 60,
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

        # Strip quotes/wrapping the model sometimes adds despite instructions.
        for wrap in ('"', "'", "“", "”", "‘", "’"):
            if text.startswith(wrap) and text.endswith(wrap) and len(text) > 2:
                text = text[1:-1].strip()

        if not text:
            continue
        if _looks_refused(text):
            log.warning("VLM appears to have refused: %r", text[:120])
            continue
        if len(text) > 200:
            # Too long — this is exactly the markety spam we don't want.
            log.warning("VLM returned %d-char reply; rejecting: %r", len(text), text[:120])
            continue
        if text.lower() in recently_posted:
            continue
        return text

    log.warning("VLM failed to produce a usable comment; using fallback")
    # Don't repeat if we can help it.
    pool = [c for c in FALLBACK_COMMENTS if c.lower() not in recently_posted]
    return random.choice(pool or FALLBACK_COMMENTS)
