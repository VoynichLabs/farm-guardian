# 23-Apr-2026 — Gem-gate tightening (Discord curation lane)

**Branch:** `gem-gate-tightening-23apr2026`
**Author:** Claude Opus 4.7 (1M context)
**Status:** In progress

## Scope

Tighten `tools/pipeline/gem_poster.py::should_post` so that `mba-cam` and `gwtc` stop flooding `#farm-2026` with low-curatorial-value frames — specifically huddle/sleeping group shots and generic "fluffy chicks" captions. s7-cam rules unchanged (already strict).

**Out of scope.** IG publishing (already reaction-gated, untouched). Upstream `quality_gate.py` pixel sanity (orthogonal layer, not the source of the noise). VLM model/prompt changes (prompt v2.36.6 is current; the issue is gate trust, not prompt quality).

## Observations

Sampled `data/gems/2026-04/{gwtc,mba-cam}/*-strong.json`:

- **mba-cam strong frames are all pre-qwen3.6 era** (glm-4.6v-flash + gemma-4-31b through 20-Apr-2026). Zero qwen3.6 strong mba-cam frames on disk — the model swap alone may have silenced that lane. Don't over-index on gemma-4 caption patterns.
- **gwtc qwen-era strong frames** show the residual failure modes that matter:
  - `activity=sleeping, composition=portrait, bird_count=2` → posted at 2026-04-23T00:09:07. Sleeping birds are not archive-worthy by Boss's standard.
  - Multiple `activity=foraging, composition=group, bird_count ∈ {5,7}` → the "pile of birds pecking the ground, no single subject" pattern.
  - 2026-04-23T12:19:25 caption leaked CJK (`籠`) — VLM output hygiene miss.
- Discord is the **human curation surface**, not the publish surface. A `decent`-tier demote is sufficient — the frame stays archived, it just stops spamming Discord. Reaction-gated IG is unaffected.

## Design

All rules below apply **only** to non-s7 cameras (`mba-cam`, `gwtc`, `usb-cam`, `house-yard`, `iphone-cam`). s7-cam retains its existing `image_quality=sharp` + `bird_face_visible=true` gate.

Ordered checks in `should_post`, after the existing universal rules (`share_worth!=skip`, `bird_count>=1`, `image_quality!='blurred'`):

1. **Activity gate.** Reject if `activity ∈ {huddling, sleeping, none-visible, other}`. Covers the fluffy-pile huddle shots and the "no subject" frames the VLM still occasionally approves.
2. **Composition gate.** Reject if `composition ∈ {cluttered, empty}`.
3. **Caption hygiene gate.** Reject if `caption_draft`:
   - matches one of three generic patterns Boss flagged — `"A group of [adj]* (chicks|birds|...)"`, `"(Cute|Fluffy|...) baby (chicks|birds)"`, `"(Chicks|Birds|Baby chicks) in/under/near the (brooder|coop|heat lamp|...)"`, OR
   - contains any non-ASCII code point (catches the 23-Apr qwen CJK leak `籠`).
4. Existing sharpness branch continues to apply (sharp → accept; soft → require face-or-multibird; blurred → reject).

**Intentionally NOT a bird_count cap.** Boss flagged mid-work (23-Apr) that MBA + GWTC produce their best frames when one bird poses close to the lens with the rest of the flock in the background; those frames have high bird_count and deserve to post. Huddle blobs are caught by the activity gate, not by counting birds.

`should_post` keeps its current signature and return type. The caption argument is already in the VLM metadata dict — no new parameter. Each rejection path logs a short reason tag (`skip_activity`, `skip_composition`, `skip_group_too_large`, `skip_generic_caption`, `skip_non_ascii_caption`) at DEBUG level for later tuning.

## Tests

No existing test harness in this repo — add a self-contained `test_gem_poster_gate.py` that imports `should_post` and runs assertion cases for each branch. Runnable via `python -m tools.pipeline.test_gem_poster_gate`. Not wired into CI (none exists).

## Rollout

1. Edit `tools/pipeline/gem_poster.py` — tighten `should_post`, update docstring history block (append v2.37.2 entry), update file header timestamp.
2. Add `tools/pipeline/test_gem_poster_gate.py`.
3. CHANGELOG entry: `v2.37.2 — gem gate: reject huddle/sleeping, large group shots, generic-opener + non-ASCII captions`.
4. Commit + push branch; merge to main (fast-forward) once green; push main.
5. Live pipeline (`com.farmguardian.pipeline` LaunchAgent) picks up the new gate on next orchestrator restart — kickstart after merge.

## Historical replay (pre-commit, from `test_gem_poster_gate.py`)

New gate applied to every `-strong.json` on disk under `data/gems/2026-04/`:

- **mba-cam:** 15 total → 8 still accepted, 7 newly rejected (5 × huddling, 2 × none-visible).
- **gwtc:** 22 total → 15 still accepted, 7 newly rejected (2 × cluttered, 2 × sleeping, 1 × none-visible, 1 × generic caption, 1 × non-ASCII caption).

Those rejection tags are the failure modes Boss flagged — no false positives in the archive.

## Rollback

Single-file revert on `tools/pipeline/gem_poster.py`; no schema, no config, no migrations.
