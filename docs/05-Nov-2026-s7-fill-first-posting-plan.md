# 05-Nov-2026 — Fill-first posting rule for the S7 gems (PLAN, not implemented)

Author: Claude Haiku 5.5 Medium
Status: **Plan only. No code, config or posting change has been made. Awaiting Boss approval.**
Evidence: [05-Nov-2026-s7-reaction-findings.md](05-Nov-2026-s7-reaction-findings.md)

## Why

The Boss's reactions track how much of the frame the bird fills. In every scoring era the measured fill sorts reacted posts better than the score does. In the August–September era the score had no fill term at all, and fill still predicted reactions.

Backtest, keeping the top third of already-posted frames (the only frames with reaction labels):

| Era | Baseline reacted share | Top third by score | Top third by measured fill |
|---|---|---|---|
| A, no fill term (12 Aug – 18 Sep) | 15.4% | 18.7% | 23.3% |
| B2, measured fill term (2 Oct on) | 21.7% | 26.6% | 28.8% |

The lift is real but modest. The backtest only reorders posts that already passed the gate, so it cannot show what a new gate would post.

**What the data does not support.** The other score parts (expression, detail, technical, company) were not validated here. Expression and detail sit at a few values, technical is nearly constant in posted frames, and company is flat. Dropping any of them is **not** supported by this data.

## Scope

**In:** how the posting gate, the stored ranking and the caption ping use fill.

**Out:** the VLM prompt and schema; the frame selector's own dominance weight (`tools/pipeline/frame_selector.py`, a separate use); Instagram and Nextdoor lanes other than through the ranking input; any new Discord posting without the Boss's explicit approval.

## Current state (sources)

- **Score.** Five parts since 19 September (`tools/pipeline/orchestrator.py`, `_compute_overall_score`): expression 0–20, detail 0–20, technical 0–15, fill 0–25, company 0–10. Fill uses the detector's measured coverage (full marks at 40%), falling back to the model's coverage figure (full at 55%). Raw sum rescaled from a ceiling of 87 to 95.
- **Gate.** `tools/pipeline/gem_poster.py`: `_MIN_OVERALL_SCORE = 82` and the frame's tier must be "strong".
- **Caption ping.** The "BIRD SELFIE" @-mention fires at a score of 90 or more (orchestrator caption block). Changing the score changes pings.
- **Caps.** The focus check and the static-floor caps adjust the score after computing it (`_cap_score_for_focus` and neighbours, about lines 408–523).
- **Stored score.** Written to `image_archive.vlm_json` as `overall_score`; the Discord post shows it as the "⭐ N/100" line.

## Options

- **A. Re-weight inside the score.** Raise fill's maximum points and reduce the others. One number, small code change. Keeps the other parts' noise, so the result is still not fill-only.
- **B. Fill-first gate.** Post when the frame is strong, its measured fill clears a threshold, and the score clears a lower floor. Changes which frames post, so volume changes and needs a parity backtest.
- **C. Fill-first ordering only.** Keep the gate as it is; use fill to order what the reels and the Story queue pick first. Changes no posting volume.

**Recommendation:** start with a shadow test of all three (no posting change), then choose between A and B on the data. C is the lowest-risk change if the Boss wants something live sooner.

## Architecture (only if approved)

- Compute a `fill_first_score` next to `overall_score` in the orchestrator and store it in `vlm_json` as a new key. No database schema change.
- A flag in `tools/pipeline/config.json`, `gem_rank_mode`: `composite` (current, default) or `fill_first`. The default stays `composite`.
- `gem_poster` reads the flag to choose which score the gate uses.
- The caption ping keeps using the composite score until the Boss decides, so pings do not change silently.

## TODOs

1. **Enumerate every reader** of `overall_score`, the gate and the ordering (grep, then list each call site with file and line). Verify before any change.
2. **Shadow write.** Compute `fill_first_score` on live S7 frames and log it. No gate change. Run for about three weeks.
3. **Replay on archived frames.** Count how many frames each candidate rule would post, era by era, and how many of the currently posted frames would drop out. Target volume parity, as the team did for the 12 August change.
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

## Approval requested

1. Approve steps 1–3 (analysis and shadow logging, no posting change).
2. Decide later, with those results in hand, between options A, B and C.
