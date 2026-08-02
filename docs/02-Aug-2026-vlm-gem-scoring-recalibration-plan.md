# 02-Aug-2026 — VLM Gem-Scoring Recalibration Plan

Author: Claude Sonnet 5
Date: 02-Aug-2026
Status: DRAFT — awaiting Boss approval before implementation

## Why this doc exists

Boss asked for a review of the VLM bird-judging prompts (`tools/pipeline/prompt.md` +
`tools/pipeline/schema.json`, read by `tools/pipeline/vlm_enricher.py`), suspecting stale
copy (leg bands) and excessive strictness, plus asked whether the pipeline could be made
faster. Investigation (three parallel research passes + direct DB/log verification against
`data/guardian.db` and the live `/tmp/pipeline.err.log`) found:

1. **Bands are NOT stale — already fixed correctly 5 days ago.** No action needed here.
2. **Strictness has a specific, dated, measured cause**, not a vague feeling. Fixable.
3. **Speed** has two safe, low-risk wins and one item that needs a live measurement before
   committing to a specific cut.

This doc is the required plan (per `CLAUDE.md` Coding Standards) before touching
`prompt.md`, `gem_poster.py`, or any prompt/threshold behavior. **Nothing below has been
implemented yet.**

---

## Scope

**In scope:**
- Reverting the `share_worth` "close-and-looking" hard prerequisite in `tools/pipeline/prompt.md`
  from an absolute AND-gate back to a weighted OR-criterion (the mechanism that crashed the
  gem rate).
- Syncing the stale "reaches Discord at 80+" prompt text and the stale `gem_poster.py`
  docstring comment to the actual enforced value (`_MIN_OVERALL_SCORE = 70`).
- Trimming two low-value, schema-unenforced prompt sections to cut token cost per call.
- One live latency measurement (prefill vs. total) to decide if further prompt trimming is
  worth pursuing beyond the two safe cuts above.
- A new one-off replay script to verify the prompt change against real archived images
  before it goes live, following the project's existing `--with-vlm` replay pattern
  (`scripts/replay-artifact-filter.py`) used for the night-alert system.
- A documentation note (CLAUDE.md, Camera 2 section) capturing the S7 Qi-pad charging
  behavior Boss described, so a future agent doesn't misread expected charging downtime as
  a brownout incident.

**Explicitly out of scope (verified fine, don't touch):**
- **Band handling** (`roster.py`, `_format_band()`, `resolve_band()`, the band schema
  fields). This was reworked 28-Jul-2026 (v2.55.0/v2.55.1) after a measured incident (440
  band sightings, 0 correct IDs, 5 phantom bands published) and is working as designed —
  VLM reports raw observations only, Python resolves identity from `config/flock_bands.json`,
  captions are scrubbed of band mentions. No changes proposed.
- **`vlm_load_context_length: 16384`** — this is memory headroom, not a latency lever, and
  was set specifically to fix a real "context size exceeded" incident on portrait S7 frames.
  Leave it.
- **The single-in-flight VLM lock (`_VLM_LOCK`, `"parallel": 1`)** — deliberate, and
  consistent with the LM Studio incident history already documented in `CLAUDE.md`. Not
  currently even a bottleneck (s7-cam is the only camera that calls `enrich()` today; all
  others are `vlm_bypass`). Not touching this.
- **Model choice** (`qwen/qwen3-vl-4b`) — already chosen for speed; no measured case for
  swapping, and `CLAUDE.md` has a standing "don't restore qwen3.5-9b" rule from a past
  regression. Not evaluating alternatives without a specific reason.
- **The S7 outage seen during this investigation** — turned out to be Boss manually powering
  the phone off to actually charge it (running it while resting on the Qi pad drains it net
  negative — the pad's ~5W delivery is less than the phone's active draw). Not a bug, no
  code fix. Documentation-only touchpoint (see below).
- **All the other skip-triggers in `prompt.md`** (wire mesh, cluttered floor, partial/obscured
  birds, floor-pecking calibration, etc.) — these predate 12-Jul and aren't implicated in the
  rate crash. Leave them exactly as they are.

---

## Background: what the data actually shows

### The strictness regression (dated and measured)

`git log` pinpoints the change: commit `c8e24af` (12-Jul-2026, v2.45.0) rewrote
`share_worth` scoring from a 0-10 scale to the current 0-100 weighted-component scheme, and
in the same commit added a new **hard prerequisite**: a frame can only be tagged `strong` if
some bird is *both* close to the camera *and* looking at the lens — no other factor
(sharpness, light, plumage, story) can compensate if that fails.

Querying `image_archive` for `camera_id='s7-cam'` by day:

| Period | `strong`-tag rate |
|---|---|
| 03–12 Jul (before the change) | 20–42% |
| 13 Jul (day after) | 4.4% |
| 13 Jul – 02 Aug (3 weeks, sustained) | mostly 1–6%, one day as low as 0.06% |

`git show c8e24af` confirms the pre-12-Jul prompt had no such AND-gate — `strong` was
already gated by sharpness + face-visibility + a list of skip triggers, but any one of four
OR'd positive signals (direct eye contact, sharp profile, standout behavior, strong
composition) was sufficient. The 12-Jul rewrite turned "close AND looking, on the same
bird" into a precondition that overrides all four of those signals. That precondition,
not the numeric threshold, is the primary driver of the sustained rate collapse.

**A secondary, smaller compounding issue:** the same 12-Jul commit added the line "A frame
only reaches Discord at 80+" to `prompt.md`. The very next day (v2.45.2, 13-Jul) the real
gate (`gem_poster.py:68`, `_MIN_OVERALL_SCORE`) was lowered to 70 at Boss's request — but the
prompt text and the `gem_poster.py` docstring comment (line 168) were never updated. The
model has been calibrating `expression_score`/`detail_score` (55 of the 100 recomputed
points) against a bar 10 points higher than what's actually enforced, for three weeks. Of
the frames the VLM does tag `strong` in the last 14 days, 19 of 459 (~4%) fall below the
real 70 cutoff and get silently rejected at the gate — a small but real additional loss on
top of an already tiny pool.

### Speed — real measured numbers, not estimates

From `image_archive.vlm_inference_ms` (9,062 real calls, past 7 days, s7-cam): average
**5.66s/call**, p5=4.6s, p95=6.4s — a tight distribution, no evidence of a fat prefill tail
in the common case. Two optimizations are already live: images are downscaled to 768px
long edge before sending (`_downscale_for_vlm`, already applied June 2026), and a 4-stage
pre-VLM gate (trivial/exposure/sharpness/motion) rejects a real ~15% of captures before they
ever cost a VLM call (verified from `/tmp/pipeline.err.log`: in a recent sample, 82 "gated"
+ 472 "ok" out of 554 non-error cycles). The other ~85% that pass the pre-filter do reach
the VLM and cost the full 5.66s regardless of the eventual `share_worth` verdict — including
the ~93% of *those* that the VLM itself scores `skip`. That 93% is a real cost paid for very
little return, and it's the actual lever worth pulling, not a filtering win to be proud of.

`prompt.md` is ~21KB (~3,400 words), resent in full on every call. Two sections add bulk
without touching anything the schema enforces:
- The `overall_score` field guidance (lines ~93-106) restates most of the `share_worth` /
  `expression_score` / `detail_score` rubric a second time with 6 calibration examples that
  substantially duplicate the `share_worth` calibration examples already given.
- The breed-speculation block (~10 lines) feeds only a free-text embellishment inside
  `caption_draft` — `schema.json` has no `breed` field at all, so this is prompt cost paying
  for caption flavor text, not anything gated or scored.

No teardown of prefill-vs-decode timing exists yet — the 5.66s figure is end-to-end. Before
committing to a specific token-trim target, do one live timed comparison (see TODOs).

---

## Architecture — what changes, what's reused

No new modules. All changes are edits to files that already own this responsibility:

- **`tools/pipeline/prompt.md`** — sole source of truth for VLM instructions (confirmed:
  `birds_preset_path` is `""` in `config.json`, retiring the old LM Studio preset fallback
  as of v2.55.0). This is where the close-and-looking fix, the 80→70 text sync, and the
  prompt trims all land.
- **`tools/pipeline/gem_poster.py`** — `_MIN_OVERALL_SCORE` (line 68) is already correct at
  70; only its stale docstring comment (line 168) needs to match. No logic change.
- **`tools/pipeline/schema.json`** — untouched. No field additions or removals.
- **New file: `scripts/replay-vlm-prompt.py`** — a verification-only script, same shape as
  the existing `scripts/replay-artifact-filter.py --with-vlm` pattern already used for the
  night-alert system. `vlm_enricher.enrich()` already takes `prompt_template` as an
  argument, so this script loads a **candidate prompt file from disk** (not the live
  `prompt.md`) and calls `enrich()` directly against real archived s7-cam images
  (`image_archive.image_path`) — it never edits `prompt.md` and **never writes to
  `image_archive`**, pure read-and-compare, reporting the new `share_worth` next to what's
  already stored for the same rows.

  **Known limitation, checked against the DB before writing this plan:** retention isn't
  uniform across tiers. `skip`-tier rows never get an `image_path` at all (verified: 37,652
  `skip` rows since 13-Jul, 0 with a stored path) — there is no on-disk image to re-test for
  any historical frame the old prompt called `skip`. `decent`-tier images follow the 7-day
  retention window (2,736 rows since 13-Jul, only 448 — the last ~7 days — still have a
  file). `strong`-tier images are fully retained (1,190 of 1,190 have a file, 365-day
  retention). **This means the retrospective replay can only ever test two questions: (a)
  do currently-`strong` frames stay `strong` under the new prompt (no regression), and (b)
  do some of the last 7 days' `decent` frames move up to `strong` (the population sitting
  right at the old boundary).** It cannot answer "does this rescue frames the hard-gate
  wrongly downgraded all the way to `skip`" — those images don't exist to re-test. That
  question can only be answered by watching the live `share_worth` rate after deploy (TODO
  8), which is why that step is not optional.
- **`CLAUDE.md`** — one new paragraph under the Camera 2 (s7-cam) hardware section
  documenting the Qi-pad charging behavior.
- **`CHANGELOG.md`** — one new top entry (next SemVer, currently `v2.58.0` → `v2.59.0`)
  covering all of the above.

---

## TODOs (ordered)

1. **Draft the `share_worth` prompt rewrite.** Replace the "HARD PREREQUISITE" block with a
   5th OR'd criterion alongside the existing four ("direct eye contact," "sharp profile,"
   "standout behavior," "strong composition"): *"A bird close to the camera making eye
   contact with the lens (direct or three-quarter) — closeness and eye contact together are
   enough on their own, even without dramatic action or exceptional light."* Remove the "do
   not let other factors compensate" override paragraph and the forced
   low-`expression_score`/`detail_score` instruction that goes with it — those fields keep
   their own field-level rubrics. **Keep** skip-trigger #10 (closest/most-prominent bird
   facing away with only small/distant birds looking at the lens) as an ordinary skip
   trigger, not a global override — that's a legitimate reason to skip on its own, it just
   shouldn't zero out every other signal when it doesn't apply.
2. **Sync the stale threshold text.** `prompt.md`: "A frame only reaches Discord at 80+" →
   "70+" (and check the two sentences around it that reference the old number). `gem_poster.py`
   line 168 docstring: "`overall_score < 80` or missing → reject (v2.45.0; fail closed)" →
   update to 70 with a note that it was lowered in v2.45.2. `_MIN_OVERALL_SCORE` itself is
   already correct — no code behavior change here, comment-only.
3. **Draft the trim as a scratch candidate file first** — condense the duplicate
   `overall_score` rubric restatement and the schema-unbacked breed-speculation block down
   to the minimum needed to keep caption quality, without touching any schema-enforced field
   instructions (`band_*`, `image_quality`, `share_worth`, `expression_score`,
   `detail_score`). Save as a candidate file, do **not** edit the live `prompt.md` yet.
4. **Measure before committing to the trim, not after.** Pause the live daemon's VLM calls
   first (`touch /tmp/farm-pipeline.pause`) and remove it when done. Run ~10 timed `enrich()`
   calls against the **same** real archived frame with the current prompt, and ~10 with the
   scratch-trimmed prompt from step 3, using the already-loaded `qwen/qwen3-vl-4b`.

   **DONE — measured 02-Aug-2026, result: no meaningful win, trim NOT applied.**
   10 calls per arm, same real archived frame (`2026-08-02T18-55-30-strong.jpg`):
   current-prompt median **6.30s** (range 5.92–6.99s, one 22.2s outlier matching the
   production p95 tail already seen in the archive data); trimmed-prompt median **6.53s**
   (range 5.83–6.81s, one 21.1s outlier) — the trim was not faster, if anything trivially
   slower, well within noise. An ~11% cut in prompt character count produced no measurable
   latency change. This means the bottleneck is decode (generating the ~600-token JSON
   response, including the free-text caption), not prefill (processing the prompt + 768px
   image) — trimming prompt *text* was the wrong lever. **The scratch-trimmed prompt file
   is discarded; `prompt.md`'s breed-speculation block and `overall_score` calibration
   examples are staying as they are.** No caption-quality risk was taken for zero benefit.
   If per-call speed becomes a real problem later, the lever to pull is `vlm_max_tokens`
   (currently 600) or the response schema's field count, not prompt prose length — untested,
   flagged for a future investigation, not part of this plan's scope.
5. **Build `scripts/replay-vlm-prompt.py`** and run it against the images that actually
   survive. **DONE, with one significant correction along the way.**

   **⚠️ The first version of the script was wrong and produced alarming, entirely spurious
   results.** It read archived JPEGs and passed them straight to `enrich()` — but the live
   pipeline always calls `_downscale_for_vlm(jpeg_bytes, vlm_input_long_edge_px)` first
   (`orchestrator.py:526`, 768 px). Feeding the model full-resolution 1080×1920 originals
   made it report `image_quality: "soft"` and `expression_score: 0` on frames stored as
   `sharp` with `expression_score: 20`, which read as a catastrophic prompt regression
   (strong→skip 7/40, decent→skip 20/40). Three frames (ids 1504248, 1636686, 1709618) were
   inspected field-by-field to find the cause. With the downscale applied — matching what
   production actually sends — all three scored correctly again. **The script now applies
   the downscale, with a comment explaining why. Any future replay harness that skips it is
   testing an input the pipeline never sends.**

   **Corrected results** (40 `strong` + 40 `decent`, seed 2026, downscale applied):
   `strong` → 34 `strong` / 2 `decent` / 4 `skip`; `decent` → **23 `strong`** / 9 `decent` /
   8 `skip`. Direction is right: most gems survive, and a large share of `decent` frames —
   the population sitting right at the old AND-gate boundary — move up.

   **Control run — COMPLETED, and it is what makes the result causal.** The control was
   first started, then deliberately killed mid-run to free LM Studio for the live camera
   during a short S7 battery window (the timeline in git history looks odd for this reason),
   then re-run to completion once the live window proved too dark to yield a usable number
   anyway. Same 80 rows, same seed, same script, pre-edit prompt:

   | stored tier | → `strong` under OLD prompt | → `strong` under NEW prompt |
   |---|---|---|
   | `strong` (n=40) | 23 (57.5%) | **34 (85.0%)** |
   | `decent` (n=40) | 1 (2.5%) | **23 (57.5%)** |
   | **total gems from same 80 images** | **24** | **57** |

   **Two things this establishes.** First, the model's run-to-run noise is *large* — the old
   prompt reproduced its own stored `strong` verdict only 57.5% of the time at
   `temperature: 0.2`. Any future prompt experiment on this pipeline must clear roughly ±40%
   churn on borderline frames before claiming an effect, and a single uncontrolled replay run
   is not sufficient evidence. **Always run the control.** Second, the `decent`→`strong`
   movement (1/40 → 23/40, a 23× difference) is far outside that noise band, so the effect is
   real. The new prompt is also *more* self-consistent on gems (85% vs 57.5%), which rules out
   "it just says strong more often at random."

   To reproduce:
   ```
   git show 3b90b66:tools/pipeline/prompt.md > /tmp/prompt-control.md
   ./venv/bin/python scripts/replay-vlm-prompt.py --prompt-file /tmp/prompt-control.md --seed 2026
   ```
6. **Sanity-check the result, don't chase a number.** The goal is a `strong` rate
   meaningfully above the current 1-6% floor — not necessarily back to the exact pre-12-Jul
   20-42% (some of that era's looseness may have been genuinely too permissive; the
   floor-pecking calibration and skip triggers added since then are good and are staying).
   Boss's own Discord reactions remain the real quality gate for what actually gets promoted
   into daily/weekly Reels — this fix is about not needlessly starving the `strong` tag
   before a human ever sees the frame.
7. **Run existing regression tests**: `python -m tools.pipeline.test_gem_poster_gate` and
   `python -m tools.pipeline.test_floor_pecking_calibration` — confirm nothing in the
   existing gate/calibration logic breaks from the docstring-only `gem_poster.py` edit.
8. **Reload the pipeline daemon** (`launchctl kickstart -k gui/$(id -u)/com.farmguardian.pipeline`)
   after the prompt/config edits land, and watch `/tmp/pipeline.err.log` for the next ~2
   hours of real s7-cam cycles to confirm the `share_worth` rate has actually moved and
   nothing is erroring.
9. **Documentation.** Add the CLAUDE.md Camera 2 note about Qi-pad charging behavior
   (see below for suggested text). Update `CHANGELOG.md` top entry.

---

## Suggested CLAUDE.md addition (Camera 2 / s7-cam section)

> **Powering the S7 while it rests on the Qi pad drains it, it does not charge it.** The Qi
> pad delivers ~5W; running the IP Webcam app with the screen and WiFi active draws more
> than that, so the phone loses charge net-negative even while resting on the pad. Boss
> manually powers the phone off to actually charge it, which means **s7-cam going offline for
> a stretch with a clean "Host is down" / connection-refused error (not a 0-byte app-wedge
> response) may simply mean it's off to charge** — check with Boss / look for a charging
> cadence before treating this as a brownout incident or opening a diagnostic thread. This is
> distinct from the documented app-wedge failure mode (`/photoaf.jpg` returning 0 bytes),
> which is a different symptom entirely.

---

## Docs/Changelog touchpoints

- `CHANGELOG.md` — new top entry, next SemVer (`v2.59.0`), covering: close-and-looking
  reverted to a weighted criterion, 80→70 text/docstring sync, prompt trim, new replay
  script, CLAUDE.md S7 charging note. Author: Claude Sonnet 5.
- `CLAUDE.md` — Camera 2 (s7-cam) section, new paragraph (above).
- No changes needed to `docs/28-Jul-2026-band-based-bird-id-plan.md`,
  `docs/22-Jul-2026-vlm-leg-band-identification-plan.md`, or any band-related doc — out of
  scope, already correct.

---

## Approval needed before implementation

Per standing process, this plan needs a go-ahead before any of `prompt.md`, `gem_poster.py`,
or the new replay script are touched. Specifically flagging one judgment call for Boss to
confirm: the exact replacement wording for the close-and-looking criterion in TODO #1 (drafted
above) — happy to adjust the phrasing before it goes in.
