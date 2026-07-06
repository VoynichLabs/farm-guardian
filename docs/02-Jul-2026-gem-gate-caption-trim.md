# Gem Gate Restore + Discord Caption Trim — 02-Jul-2026

**Author:** Claude Fable 5 (Bubba)
**Approved:** Boss, 02-Jul-2026 morning (Discord/CLI session). Explicitly rejected: Discord cooldown, motion-gate re-enable. Explicitly approved: score gate + caption trim.

## Problem (diagnosed 02-Jul-2026, three-agent investigation)

1. `gem_poster.should_post()` accepts `tier` and has not read it since v2.28.5 —
   no numeric threshold exists anywhere on the Discord lane. 26 posts scored
   4–6 landed in #farm-2026 between 26-Jun and 02-Jul. The ⭐ N/10 caption
   suffix is decorative.
2. The 01-Jul swap to qwen/qwen3-vl-4b (v2.44.3) triples the strong rate
   (8.0% → 27.2%) and runs ~5x faster → 43 posts/day (30-Jun) became 196
   (01-Jul), peaking at 48/hour.
3. Captions run long by design: prompt asks for "2–4 sentences, up to ~450
   chars", schema caps at 450, only truncation is Discord's 1900 belt.

## Scope (surgical, Discord lane only)

- `tools/pipeline/gem_poster.py`
  - `should_post`: restore tier gate (`tier == "strong"`) AND require
    `overall_score >= 7` (module constant `_MIN_OVERALL_SCORE`). Both checks
    because the 4b model emits inconsistent score/tier pairs. Missing score →
    reject (fail closed).
  - New `trim_caption(caption, limit=300)`: sentence-boundary-preferring trim,
    word-boundary + ellipsis fallback. Discord lane only.
- `tools/pipeline/orchestrator.py`: wrap `caption_draft` with `trim_caption()`
  in the Discord gem block (line ~546); add import (both import sites).
  ⭐ score suffix and score-10 Boss ping unchanged, appended after trim.
- `tools/pipeline/test_gem_poster_gate.py`: base meta gains
  `overall_score: 8`; new cases for tier/decent reject, score-6 reject,
  missing-score reject, score-7 pass; `trim_caption` unit cases.
- `CHANGELOG.md`: v2.44.5 entry.
- Restart `com.farmguardian.pipeline` LaunchAgent; verify skip reasons in
  /tmp/pipeline.err.log.

## Non-goals (Boss said no)

- No Discord cooldown/rate limit.
- No s7-cam motion_gate change.
- No IG-lane caption changes (IG keeps full captions + hashtags).
- No model change (qwen3-vl-4b stays).

## Expected effect

Replay of the 345 posts since 26-Jun through the new gate: the 26 sub-7
posts disappear; strong-but-7+ volume remains (that's the generous model,
accepted by Boss in lieu of a cooldown). Captions cap at ~300 chars + score
suffix.
