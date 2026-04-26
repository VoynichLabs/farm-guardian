# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Caption writer for Nextdoor cross-posts. Calls the currently-
#          loaded LM Studio VLM with the post's photo and a lane-specific
#          system prompt (today vs throwback), returns a warm
#          neighbor-voice 1-3 sentence caption. Falls back to a short
#          static caption if LM Studio is unreachable or returns junk —
#          never fails the post over a caption problem.
#
#          Follows the LM Studio safety rules from CLAUDE.md:
#            1. Queries /api/v0/models for what's already loaded; never
#               calls /models/load, never passes a model name that isn't
#               in the loaded list.
#            2. Times out after 20s.
#
# SRP/DRY check: Pass — only concern is "image + lane -> caption string".
#                Doesn't touch DB, doesn't post, doesn't know about the
#                browser.

from __future__ import annotations

import base64
import json
import logging
import random
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

log = logging.getLogger("nextdoor.caption_writer")

LM_BASE = "http://localhost:1234"
TIMEOUT_S = 60

Lane = Literal["today", "throwback"]

SYSTEM_TODAY = (
    "You are writing a short Nextdoor post for a neighborhood feed in "
    "Hampton, CT. The author is a local backyard-chicken keeper sharing "
    "what's happening in their brooder / coop / backyard today.\n\n"
    "Voice rules (hard):\n"
    "- 1 to 3 sentences. Mixed case, normal punctuation.\n"
    "- Warm, good-neighbor register. Not commercial, not promotional.\n"
    "- At most ONE emoji, and only tame ones: 🐣 ☀️ ❤️ 🌱. Most posts use none.\n"
    "- Never mention Instagram, Facebook, or that this was cross-posted.\n"
    "- Never include hashtags, @-mentions, or URLs.\n"
    "- Never say the author's name or exact address. 'Hampton' is fine; "
    "the street is not.\n"
    "- Describe what's actually in the photo. If there are chicks, say "
    "chicks. If there's a dog, say the dog. Avoid flowery adjectives.\n"
    "- Optionally end with a light question or invitation to chat if it "
    "fits naturally. Don't force it.\n\n"
    "Return ONLY the caption. No quotes, no preface, no 'Caption:' label."
)

SYSTEM_THROWBACK = (
    "You are writing a short Nextdoor post for a neighborhood feed in "
    "Hampton, CT. This is a throwback — the photo is from the author's "
    "backyard-chicken farm archive (any year from the last few).\n\n"
    "Voice rules (hard):\n"
    "- 1 to 3 sentences. Open with a phrase that signals it's a "
    "throwback: 'Throwback —', 'Flashback to —', 'A while back —', "
    "'From the archive —'. Vary it; don't always use the same one.\n"
    "- Warm, lightly nostalgic, good-neighbor register.\n"
    "- At most ONE emoji, only tame ones: 🐣 ☀️ ❤️ 🌱. Most posts use none.\n"
    "- Never mention Instagram, Facebook, or cross-posting.\n"
    "- Never include hashtags, @-mentions, or URLs.\n"
    "- Never say the author's name or exact address. 'Hampton' is fine.\n"
    "- Describe what's in the photo honestly. Don't invent details the "
    "image doesn't show.\n"
    "- No sad notes, no losses, no 'we miss them' framing. Keep it light.\n\n"
    "Return ONLY the caption. No quotes, no preface, no 'Caption:' label."
)

# Static fallbacks used if LM Studio is unreachable or returns junk.
STATIC_FALLBACKS = {
    "today": [
        "A little scene from the Hampton backyard flock this evening.",
        "Today in the coop — our little Hampton flock keeping busy.",
        "Check-in from the brooder in Hampton. 🐣",
    ],
    "throwback": [
        "Throwback — a quiet moment from the Hampton backyard archive.",
        "Flashback to a morning on the Hampton chicken farm.",
        "From the archive — one of our Hampton flock.",
    ],
}


def _get_loaded_model() -> str | None:
    """Return the id of a model currently loaded on LM Studio, or None."""
    url = f"{LM_BASE}/api/v0/models"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.warning("LM Studio /api/v0/models unreachable: %s", e)
        return None
    for entry in payload.get("data", []):
        if entry.get("state") == "loaded":
            return entry.get("id")
    log.info("LM Studio has no loaded model")
    return None


def _chat_completion(model: str, system: str, image_path: Path) -> str | None:
    """Call /v1/chat/completions with the image and system prompt.
    Returns the trimmed caption text, or None on any failure."""
    try:
        data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    except Exception as e:
        log.warning("read image failed: %s", e)
        return None
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Write the Nextdoor caption for this photo. "
                            "Return only the caption text."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{data}"
                        },
                    },
                ],
            },
        ],
        "temperature": 0.85,
        "max_tokens": 200,
        # Disable thinking — LM Studio's OpenAI-compat endpoint honors
        # `reasoning_effort: "none"`, NOT the older `reasoning: "off"`.
        # Without this the model burns max_tokens on reasoning_content
        # and returns empty content (verified 2026-04-26).
        "reasoning_effort": "none",
    }
    req = urllib.request.Request(
        f"{LM_BASE}/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.warning("LM Studio chat call failed: %s", e)
        return None
    try:
        txt = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        log.warning("LM Studio response shape off: %s / %s", e, str(payload)[:200])
        return None
    return _clean(txt)


_URL_RE = re.compile(r"https?://\S+", re.I)
_HASHTAG_RE = re.compile(r"(?<!\w)#\w+")
_MENTION_RE = re.compile(r"(?<!\w)@\w+")
_QUOTE_STRIP = re.compile(r'^\s*["\'“”‘’]+|["\'“”‘’]+\s*$')


def _clean(text: str) -> str:
    """Scrub hashtags, @-mentions, URLs, stray quotes, and 'Caption:'
    prefixes from the VLM output; trim to ~3 sentences max."""
    if not text:
        return ""
    t = text.strip()
    # Strip common preambles.
    for prefix in ("Caption:", "Caption :", "Post:", "Text:"):
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix):].strip()
    # Strip wrapping quotes.
    t = _QUOTE_STRIP.sub("", t).strip()
    # Remove URLs, hashtags, mentions.
    t = _URL_RE.sub("", t)
    t = _HASHTAG_RE.sub("", t)
    t = _MENTION_RE.sub("", t)
    # Collapse whitespace.
    t = re.sub(r"\s+\n", "\n", t)
    t = re.sub(r"[ \t]+", " ", t).strip()
    # Trim to at most 3 sentences (rough heuristic).
    sentences = re.split(r"(?<=[.!?…])\s+", t)
    if len(sentences) > 3:
        t = " ".join(sentences[:3]).strip()
    return t


def write_caption(image_path: Path, lane: Lane) -> str:
    """Produce a caption for this photo in the given lane. Always
    returns a non-empty string — falls back to a static library if
    LM Studio is unavailable or returns junk."""
    model = _get_loaded_model()
    if model:
        system = SYSTEM_TODAY if lane == "today" else SYSTEM_THROWBACK
        caption = _chat_completion(model, system, image_path)
        if caption and len(caption) >= 20:
            log.info("VLM caption (%s) from %s: %r", lane, model, caption)
            return caption
        log.warning("VLM returned too-short or empty caption; falling back")
    # Fallback.
    pool = STATIC_FALLBACKS.get(lane, STATIC_FALLBACKS["today"])
    return random.choice(pool)
