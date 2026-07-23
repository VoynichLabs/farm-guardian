#!/usr/bin/env python3
# Author: Claude Fable 5
# Date: 23-Jul-2026
# PURPOSE: Brand policy for AI-written Instagram captions, in two halves:
#          BRAND_RULES (the voice, injected into the prompt) and
#          brand_violations() (the hard prohibitions, ENFORCED IN CODE by
#          the caller after generation — the local 4B model cannot be
#          trusted to obey a negative instruction, and enumerating banned
#          words in a prompt is what makes it emit them). Extracted from
#          tools/pipeline/codex_reel_curator.py when the OpenAI Codex
#          subscription lapsed and that module was deleted (v2.51.5) —
#          the rules themselves were the durable asset in it, learned
#          from real off-brand posts, and they outlive whichever model
#          writes the prose. Consumed by
#          daily_reel_runner._generate_reel_caption(); any future caption
#          path (carousel, story, photo) should import from here rather
#          than restating brand policy inline.
# SRP/DRY check: Pass — grepped for existing brand/caption-rule constants
#          before creating this; BRAND_RULES existed only inside
#          codex_reel_curator.py, which this replaces. Hashtags are
#          deliberately NOT covered here: they come from the verified
#          library in tools/pipeline/hashtags.yml (which carries the
#          runtime `forbidden` list) and are appended after generation by
#          daily_reel_runner._wrap_caption_with_hashtags().

# Hard rules. Every line here was paid for by a real mistake:
#   - "chicks/babies": the flock grew up; captions kept calling grown
#     birds babies long after they were.
#   - the predator rule: an early live post carried a "watching for hawks"
#     line. A predator on camera means a dead bird, not content — and
#     CLAUDE.md's content policy forbids framing Guardian as a
#     security/predator system at all.
#   - cameras/AI/technology: the account is a farm diary, not a tech demo.
# Kept POSITIVE on purpose. An earlier version enumerated banned words
# ("do not mention cameras… no hawks"), and the 4B local model obediently
# produced "no one looking at the camera, no one chasing a hawk" — naming a
# thing in order to negate it is still naming it. Small models are especially
# prone to this. So the prompt describes the voice we want, and the hard
# prohibitions are enforced in code by brand_violations() below rather than
# trusted to instruction-following.
BRAND_RULES = (
    "VOICE:\n"
    "- You are the farmer, standing in the yard, telling a friend about the "
    "day. Warm, plain, specific.\n"
    "- The flock is grown: adult and adolescent rare-breed chickens and "
    "turkeys living in Birdcatraz, the farm's outdoor enclosure. Call them "
    "birds, hens, the flock, or by breed.\n"
    "- Stay on the ordinary pleasures: what they ate, where they dozed, the "
    "weather, who was bossy, how the light fell.\n"
    "- Keep it gentle. Nothing grim, nothing dramatic.\n"
)

# Hard prohibitions, enforced in code. Two families:
#   1. Production leakage — the account is a farm diary, not a tech project.
#   2. Content policy — CLAUDE.md forbids framing any of this as a
#      security/predator system, and a real early post carried a
#      "watching for hawks" line. A predator on camera means a dead bird,
#      not content.
# "chick"/"baby" are here because the flock grew up and captions kept
# calling grown birds babies. Matched as whole words, case-insensitively.
FORBIDDEN_CAPTION_TERMS = (
    # production leakage
    "camera", "cameras", "footage", "filming", "film", "lens", "webcam",
    "livestream", "stream", "recording", "timelapse", "time-lapse", "ai",
    "sensor", "surveillance", "monitor", "monitoring",
    # content policy
    "hawk", "hawks", "predator", "predators", "coyote", "fox", "raccoon",
    "attack", "security",
    # grown flock
    "chick", "chicks", "baby", "babies", "chicklet",
)

_TERM_RE = None


def brand_violations(text: str) -> list[str]:
    """Return the forbidden terms present in `text`, lowercased and unique.

    Whole-word, case-insensitive. Empty list means the caption is clean.
    Callers should treat a non-empty result as "regenerate or fall back" —
    never as something to patch up by deleting words, since removing "hawk"
    from a sentence about hawks leaves a sentence about nothing.
    """
    global _TERM_RE
    if _TERM_RE is None:
        import re

        _TERM_RE = re.compile(
            r"\b(" + "|".join(sorted(FORBIDDEN_CAPTION_TERMS, key=len, reverse=True))
            + r")\b",
            re.IGNORECASE,
        )
    return sorted({m.group(0).lower() for m in _TERM_RE.finditer(text or "")})
