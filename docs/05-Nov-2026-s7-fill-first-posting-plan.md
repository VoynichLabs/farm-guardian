# 05-Nov-2026 — Fill-first posting rule for the S7 gems (PLAN, not implemented)

Author: Claude Haiku 5.5 Medium
Status: **Approved by the Boss for analysis (steps 1 and 3). No pipeline code, config or posting change has been made. The live shadow step (2) was not run; see Shadow below. The choice between A, B and C is still the Boss's.**
Evidence: [05-Nov-2026-s7-reaction-findings.md](05-Nov-2026-s7-reaction-findings.md)

## Why

The Boss's reactions track how much of the frame the bird fills, in the first two scoring eras. In the August–September era the score had no fill term at all, and fill still predicted reactions. **In the newest era (from 3 October) fill and the score rank reacted posts about equally.** An earlier draft of this plan said fill won in every era; that was wrong for the newest era. The Replay section below has the current figures.

Backtest from the earlier draft, keeping the top third of already-posted frames (the only frames with reaction labels). These figures were computed before the rollout days were excluded, so the Replay section supersedes them:

| Era | Baseline reacted share | Top third by score | Top third by measured fill |
|---|---|---|---|
| A, no fill term (12 Aug – 18 Sep) | 15.4% | 18.7% | 23.3% |
| B2, measured fill term (2 Oct on) | 21.7% | 26.6% | 28.8% |

The backtest only reorders posts that already passed the gate, so it cannot show what a new gate would post.

**What the data does not support.** The other score parts (expression, detail, technical, company) were not validated here. Expression and detail sit at a few values, technical is nearly constant in posted frames, and company is flat. Dropping any of them is **not** supported by this data.

## Scope

**In:** how the posting gate, the stored ranking and the caption ping use fill.

**Out:** the VLM prompt and schema; the frame selector's own dominance weight (`tools/pipeline/frame_selector.py`, a separate use); Instagram and Nextdoor lanes other than through the ranking input; any new Discord posting without the Boss's explicit approval.

## Current state (sources)

- **Score.** Five parts since 19 September (`tools/pipeline/orchestrator.py`, `_compute_overall_score`): expression 0–20, detail 0–20, technical 0–15, fill 0–25, company 0–10. Fill uses the detector's measured coverage (full marks at 40%), falling back to the model's coverage figure (full at 55%). Raw sum rescaled from a ceiling of 87 to 95.
- **Gate.** `tools/pipeline/gem_poster.py`: `_MIN_OVERALL_SCORE = 82` and the frame's tier must be "strong".
- **Caption ping.** The "BIRD SELFIE" @-mention fires at a score of 87 or more (`_BIRD_SELFIE_PING_SCORE`, orchestrator.py line 860, set in v2.73.0). An earlier draft of this plan said 90; that was wrong. Changing the score changes pings.
- **Caps.** The focus check and the static-floor caps adjust the score after computing it (`_cap_score_for_focus` and neighbours, about lines 408–523).
- **Stored score.** Written to `image_archive.vlm_json` as `overall_score`; the Discord post shows it as the "⭐ N/100" line.
- **Ordering.** No gem selector reads the score today. `_score_gem` in `tools/pipeline/ig_selection.py` ranks by Discord reactions first, then tier, sharpness, bird count and time. Option C would add fill as a new key in that ranking; it would not replace a score-based order, because none exists.

## Readers (enumerated 05-Nov-2026, TODO 1)

Writers of `overall_score`:
- `tools/pipeline/orchestrator.py` `_compute_overall_score` (line 357): the five-axis sum.
- `orchestrator.py` `_cap_score_for_focus` (line 410): caps an out-of-focus frame at 60 or 45.
- `orchestrator.py` `_calibrate_static_floor_pecking_score` (lines 521 to 523): caps routine floor-pecking frames at 30 or 40. Only the usb-webcam-1080p and gwtc cameras reach it, not s7-cam.

Readers that change what is posted:
- `tools/pipeline/gem_poster.py` `should_post` (lines 292 to 294): tier strong and score at or above `_MIN_OVERALL_SCORE` (line 141, now 82). This is the gate.
- `orchestrator.py` (lines 1254 to 1256): appends the "⭐ N/100" line to the Discord caption.
- `orchestrator.py` (lines 1265 to 1266): the "BIRD SELFIE" @-mention at or above `_BIRD_SELFIE_PING_SCORE` (line 860, now 87).

Readers inside the scorer:
- `orchestrator.py` (line 525): the skip rule at 40 or below. Internal only.

Readers that only report or analyse:
- `scripts/replay-vlm-prompt.py` (lines 131, 134): prints the score during prompt replays.
- `scripts/s7-reaction-dataset.py` (line 184): builds the labelled table.

Ordering readers: none. `_score_gem` (`tools/pipeline/ig_selection.py`, line 208) reads reactions, tier, sharpness, bird count and time. `daily_reel_runner.py` parses `vlm_json` but never reads the score. The score in `frame_selector.py` is its own burst score, not `overall_score`.

Public exposure: the public archive endpoints in `database.py` and `images_api.py` do not return `overall_score`. The review endpoint, behind bearer auth, returns the raw `vlm_json`, which contains it. The farm-2026 site is a separate repository and was not checked.

Doc drift to fix when this is implemented: the comment block above `_MIN_OVERALL_SCORE` in `gem_poster.py` still says the floor is 65 or 70, and `should_post`'s docstring says 65. The constant is 82.

## Shadow (decided 05-Nov-2026, TODO 2)

The live shadow step was **not run**. Every archived strong frame already keeps the inputs a rule needs: tier, the score after caps, the components, both coverage figures, and measured fill from 2 October. A shadow write would only copy values that are on disk. It would not produce reaction labels either, because reactions exist only on posted frames. The replay (below) answers the volume question over the full archive instead. A live shadow can still be added later if the Boss wants the pipeline itself to log a candidate; that needs this branch merged into the running checkout, which is a live change.

## Options

- **A. Re-weight inside the score.** Raise fill's maximum points and reduce the others. One number, small code change. Keeps the other parts' noise, so the result is still not fill-only.
- **B. Fill-first gate.** Post when the frame is strong, its measured fill clears a threshold, and the score clears a lower floor. Changes which frames post, so volume changes and needs a parity backtest.
- **C. Fill-first ordering only.** Keep the gate as it is; add fill as a new key in the gem ordering (`_score_gem`) so reels and the Story queue pick higher-fill frames first. Changes no posting volume. Note that today's ordering is reactions first, so fill would only break ties unless its key is placed earlier.

**Recommendation:** start with a shadow test of all three (no posting change), then choose between A and B on the data. C is the lowest-risk change if the Boss wants something live sooner.

## Replay (run 05-Nov-2026, TODO 3)

Script `scripts/fill-first-replay.py`. Outputs on the SSD: `fill_first_replay_frames.csv` (one row per frame) and `fill_first_replay_summary.txt`.

**Universe:** 32,276 strong s7-cam frames since 12 Aug. Three rollout days are kept in the table but left out of the era analysis, because old and new scoring mix within each day: 12 Aug (floor 65 to 80), 19 Sep (floor 80 to 82, and the subject-size gate went live) and 2 Oct (measured fill went live between about 10:43 and 18:22). That is 1,851 frames.

**Checks before the numbers were trusted:**
- Today's gate, rebuilt from the live `should_post` with its score floor set per era, reproduces every posted labelled frame's result in all three eras (0 mismatches).
- Stored scores recompute exactly from their stored parts: B1 4,511 of 4,511, B2 13,284 of 13,284.
- The first draft mismatched on rollout days and on the subject-size gate in era A. Both were explained and fixed.

**Parity results** (candidate 1 = fill at or above a threshold, other gate clauses unchanged; the threshold is the one that posts closest to today's count):

| Era | Frames | Today's posts | Candidate posts (threshold) | Of today's posts: keeps / drops | Adds (unlabelled) | Labelled kept, reacted | Labelled dropped, reacted | Kept minus dropped |
|---|---|---|---|---|---|---|---|---|
| A, no fill term (13 Aug – 18 Sep) | 12,630 | 2,938 | 2,890 (34) | 1,303 / 1,635 | 1,587 | 21.3% (275 of 1,293) | 11.3% (183 of 1,624) | +10.0 (±2.7) |
| B1, VLM fill (20 Sep – 1 Oct) | 4,511 | 955 | 974 (29) | 680 / 275 | 294 | 25.7% (159 of 619) | 9.5% (24 of 252) | +16.2 (±5.0) |
| B2, measured fill (3 Oct on) | 13,284 | 1,383 | 1,323 (36) | 1,159 / 224 | 164 | 25.6% (222 of 867) | 15.4% (28 of 182) | +10.2 (±6.0) |

The ± figure is an approximate 95% interval. The overall labelled reacted share is 15.7% (A), 21.0% (B1) and 23.8% (B2).

**Ranking** (a measure where 0.5 is chance; fill against the stored score, same labelled posts):
- Era A: fill 0.63, score 0.57.
- Era B1: fill 0.65, score 0.60.
- Era B2: fill 0.56, score 0.57. Where a bird was detected: fill 0.59, score 0.57. If 2 October is included: fill 0.60, score 0.58.

**What the newest era does and does not show:**
- Matching kept and dropped posts within each day cuts the lift from +10.2 to +6.2 (21 days).
- The caption @-mention fires at a score of 87 or more and notifies Mark. Its threshold was 90 from July until 19 Sep, so it predates the August rollout. Within the mention group the newest-era gap is +16.6, but only 18 dropped posts carry the mention. Without it the gap is +4.7 across 164 dropped posts. Eras A and B1 show gaps of +9 to +16 in both groups.
- 2 October alone has 268 labelled posts, 29 reacted (about 11%), against 23.8% for the rest of the newest era. Including it changes the ranking result (above).

**Fallback fill (a defect to fix in any fill-first rule):** 1,594 of the newest-era frames (12%) have no confident bird box, so the live scorer falls back to the vision model's coverage figure. Fill-only would post 162 of them. Setting those frames to fill 0 moves the threshold to 33 and posts 1,411 against today's 1,383, keeping 1,184 of today's posts (against 1,159). Any fill-first rule must not fall back.

**Candidate 2 (fill plus a score floor of 70)** keeps and drops exactly the same labelled posts as candidate 1 in eras A and B2. The floor only removes frames that were never posted: 12 in era A, none in B2, and 31 in B1, where its threshold also moved. Its floor is untested either way, since no labelled post sits below today's floor.

**Option A (fill axis maximum raised from 25 to 40, floor set for parity, fill-term eras only):**
- B1 (VLM fill on both sides): floor 96, posts 1,001 against 955. Keeps 940, drops 15 (one of them reacted). Parity is 46 frames off.
- B2 (measured fill on both sides): floor 95, posts 1,397 against 1,383. Keeps 1,309, drops 74 (7 of 58 labelled ones reacted; ± 8.8).
- At a floor of 95, almost every frame option A posts scores above the caption @-mention threshold of 87. Option A would have to move that threshold too.

**Limits:** whole-point thresholds move volume in steps of roughly 60 to 100 frames, so parity is approximate. The dropped groups are small in the newest era. Reactions are one person's yes; zero means no recorded reaction. Option C (ordering only) is not replayed.

**Reading for the decision:** fill separates reacted from unreacted posts clearly in eras A and B1. In the newest era it does not clearly beat the score. Any option that changes how many frames post would add 164 to 1,587 frames per era that have never been posted and so cannot be judged. On this evidence the posting gate should not change yet. The only test that would settle it is a live trial, which needs the Boss's approval.

## Architecture (only if approved)

- Compute a `fill_first_score` next to `overall_score` in the orchestrator and store it in `vlm_json` as a new key. No database schema change.
- A flag in `tools/pipeline/config.json`, `gem_rank_mode`: `composite` (current, default) or `fill_first`. The default stays `composite`.
- `gem_poster` reads the flag to choose which score the gate uses.
- The caption ping keeps using the composite score until the Boss decides, so pings do not change silently.

## TODOs

1. **Done 05-Nov-2026.** Every reader is listed in the Readers section above.
2. **Shadow write: not run.** Reason in the Shadow section above. Reactions and the archive already cover what a shadow would measure.
3. **Replay on archived frames. Done 05-Nov-2026**, see the Replay section below and `scripts/fill-first-replay.py`. It counts what each candidate would post, era by era, and how many posted frames would drop out, at volume parity. Option A was replayed for the two fill-term eras only.
4. **Boss decision** on option (A, B or C) and on the floor for the chosen option. This is the Boss's call.
5. **Implement behind the flag**, default `composite`. Tests should use real archived rows, not hand-set scores (the lesson from v2.45.0).
6. **Docs in the same change:** a top CHANGELOG entry (what, why, how, credited to the model and effort), the history table in `gem_poster.py`, the docstring in `_compute_overall_score`, this plan, and the findings doc.
7. **Live trial** only on the Boss's OK. Watch posting volume and the reacted share for two to three weeks, against the window before.
8. **Rollback:** set the flag back to `composite`. No data migration is needed.

## Risks

- **Taste from one person, sparse labels.** About a sixth of posts are reacted to. Small samples on any single band.
- **Dropped parts are untested.** Expression, detail, technical and company may encode things the Boss likes that fill cannot see.
- **Volume.** A floor change can flood the channel (the July experience) or starve it. Parity backtest first.
- **Shadow data has no labels.** Frames that a new rule would post but the current rule would not cannot be scored against reactions until they are posted.
- **Live dependence.** Focus and static caps were calibrated on live frames, and the S7 has had downtime.

## Docs and changelog touchpoints

- CHANGELOG entry at implementation time only.
- `gem_poster.py` history table; `_compute_overall_score` docstring.
- This plan and the findings doc, updated with each result.

## Status and decision

1. Approved by the Boss on 05-Nov-2026: the analysis steps. Done: readers enumerated (TODO 1) and the replay run (TODO 3). The live shadow (TODO 2) was not run; see Shadow.
2. Recommendation: keep the posting gate as it is for now. The newest era does not clearly support a change, and any option that changes volume adds frames that nobody has reacted to.
3. Still the Boss's call: whether to run a live trial of any option. That needs his approval. Option C is the only volume-neutral choice, and it is not yet tested.
