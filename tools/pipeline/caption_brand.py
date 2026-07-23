#!/usr/bin/env python3
# Author: Claude Fable 5
# Date: 23-Jul-2026
# PURPOSE: Single home for the hard brand rules injected into every
#          AI-written Instagram caption. Extracted from
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
BRAND_RULES = (
    "BRAND RULES (hard):\n"
    "- The brand is a striking rare-breed flock / cozy farm life: grown and "
    "nearly-grown exotic chickens and turkeys living in Birdcatraz, the "
    "farm's outdoor enclosure. Warm, genuine, specific to what's actually "
    "in the frames. NOT cold tech-marketing. These are not baby chicks "
    "anymore — do not call them chicks or babies.\n"
    "- NEVER frame the camera as a security / predator-detection system. No "
    "\"watching for hawks\" type lines. A predator on camera means a dead bird, "
    "not content.\n"
    "- Do not mention cameras, AI, or technology.\n"
    "- Do not dramatize loss or death.\n"
)
