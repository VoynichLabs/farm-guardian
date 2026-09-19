<!--
Author: Claude Opus 5 (Bubba)
Date: 19-September-2026
PURPOSE: Diagnose why the s7-cam gem score ranked a distant rooster (95) above a
         frame-filling three-hen portrait (80), and record the measured
         recalibration that wires frame-fill and bird count back into the score.
SRP/DRY check: Pass — this is the analysis record for v2.73.0. The constants
               themselves live in orchestrator.py/gem_poster.py; this file
               explains where they came from and what was backtested.
-->

# Gem score: frame-fill is measured, stored, and scored nothing (v2.73.0)

## The two frames Boss flagged (19-Sep-2026, ~15:37 ET)

| | id | what it is | expr | detail | technical | `subject_coverage_pct` | `largest_subject_pct` | score |
|---|---|---|---|---|---|---|---|---|
| "100% shot" | 2825208 | three speckled hens at the waterer, faces and leg bands readable | 18/30 | 22/25 | 15/15 | 70 | 30 | **80** |
| "awful" | 2838001 | one rooster ~10% of frame, bottom half wood chips | 25/30 | 25/25 | 15/15 | 10 | 10 | **95** |

Both rows verified from `image_archive.vlm_json` in `data/guardian.db`.

## Cause

`_compute_overall_score` has scored exactly three axes since v2.68.0 (08-Aug-2026):

    expression (0-30, VLM) + detail (0-25, VLM) + technical (0-15, derived)
    rescaled by 95/65

Frame-fill is **not one of them**. The VLM emits `largest_subject_pct` and
`subject_coverage_pct` on every frame, the store writes them into `vlm_json`, and
for `s7-cam` nothing downstream reads them: `gem_poster`'s subject-size gate
(`_LARGEST_SUBJECT_PCT_MIN`) was inside the `is_non_s7` branch and only covered
`macbook-air-facetime` and `gwtc`; `_calibrate_static_floor_pecking_score` only
covers `usb-webcam-1080p` and `gwtc`. So on the one camera that produces gems,
frame-fill is measured, stored, and feeds nothing.

That is the whole defect, and the rooster frame is the clean demonstration: the
model itself reported `largest_subject_pct: 10` — it *knew* the bird was a tenth
of the frame — and still scored 25/25 on the two subjective axes. 25+25+15 = 65
is the exact raw ceiling, so it landed on 95/95. The hen frame lost only because
a 4b model handed out 18 and 22 instead of 25 and 25.

This was a deliberate removal, not an oversight. v2.68.0 dropped dominance
because at 0-30 it acted as a *gate* and dragged good frames under the posting
floor, and moved it into `frame_selector` as a burst-selection weight. The
measurement that motivated it still stands (Boss's reacted frames had a median
largest-box of 31.4% of frame against 19.5% for strong-but-unreacted). What went
wrong is that the fix removed the signal from the ranking as well as from the
gate — so within the set of frames that do post, the score had no opinion at all
about whether the bird was near or far.

## Boss's stated taste (19-Sep-2026, verbatim)

> "A good picture is one bird looking at the camera or just crisp and filling up
> a decent amount of the frame. A doubly good picture is two birds and that
> three-bird picture there is just as good as a picture can get. You can see all
> three band colors in that picture too, which is kind of huge."

Three requirements, and they are not equally implementable — see the band section.

## The change

Five axes, with the two subjective ones reweighted down in code (no schema or
prompt change — `schema.json` still enforces 0-30 / 0-25 and the VLM is asked the
same two questions):

| axis | max | source |
|---|---|---|
| expression | 20 | VLM `expression_score` × 20/30 |
| detail | 20 | VLM `detail_score` × 20/25 |
| technical | 15 | `image_quality` + `lighting` (unchanged) |
| subject fill | 25 | `subject_coverage_pct`: 0 at ≤12%, linear, full at ≥55% |
| company | 10 | `bird_count`: 1→7, 2→9, 3→10, 4→9, 5→8, 6+→7 |

`_SCORE_RAW_CEILING` 65 → 87, `_SCORE_SCALE_TO` unchanged at 95. Raw range is
0-90 theoretical; 87 is the observed max over the 13,377-frame population,
because the 4b model never reaches the top of its own subjective ranges. Scaling
by the theoretical 90 would have quietly preserved the status quo — the exact
trap v2.45.1 fell into by calibrating against synthetic scores.

Also changed, and derived from the same backtest rather than carried over:
`_MIN_OVERALL_SCORE` 80 → 82, BIRD SELFIE @-mention 90 → 87
(`_BIRD_SELFIE_PING_SCORE`), and `s7-cam` added to `_LARGEST_SUBJECT_PCT_MIN` at
12 with the gate moved out of the `is_non_s7` branch.

### Why coverage and not largest-subject

The first draft of this change scored `largest_subject_pct`. That is wrong, and
the population says so plainly — mean largest-subject by bird count across the
13,377 strong frames:

| birds | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| mean `largest_subject_pct` | 41.3 | 42.4 | 34.6 | 34.3 | 28.6 | 28.5 |

It falls monotonically as bird count rises, so an axis built on it would
*penalise* exactly the multi-bird frames Boss ranks highest. The flagged frame is
the case in point: coverage 70, largest 30. Coverage separates it from the
rooster (coverage 10) cleanly; largest barely separates them at all.

The 12%/55% knees are read off the coverage histogram: below 12% is the "distant
bird in a field of wood chips" class, and the histogram's mass sits in the 55-60
buckets (modal 60, n=3,057). Zero-below-12 also lines up with the 08-Aug YOLO
measurement above.

### Why company is small

10 points, spread of 3 between a lone bird and a trio. It encodes Boss's stated
ranking and nothing else — there is **no measurement behind it**.
`discord_reactions` looked like the obvious empirical check on his taste, but 502
of 510 posted frames sit at exactly 1: it is an auto-react and measures nothing.
Said plainly here so a future pass doesn't mistake this curve for data.

Keeping it small is load-bearing. A wider spread squeezes out the single-bird
portraits Boss also called good; at spread 3, coverage does the work and the
single-bird frames that survive are the ones that fill the frame.

### Leg bands are NOT scored

Boss called the three readable band colours "kind of huge," and for a human
looking at that photo he is right — the band is the farm's real bird ID.

It cannot be a score input today. qwen3-vl-4b resolves `band_color` to something
other than `"none"` on **54 of 13,377** strong frames (0.4%) — and returned
`"none"` on the three-band frame itself. Wiring a band bonus in would reward
noise and hand his best-ever picture a zero on the axis he cares most about.

The fix is a second pass, not a weight: crop to the YOLO leg box and re-ask, or
run a dedicated band classifier. Filed as the next step.

## Backtest

Run with the edited code — importing the real `_compute_overall_score` and the
real `should_post`, not a reimplementation — over all 13,377 strong-tier s7-cam
frames since 12-Aug-2026. 12-Aug is the start of the current regime (new handset
10-Aug, floor set to 80 on 12-Aug), so earlier frames are not comparable.

| | before | after |
|---|---|---|
| postable (`should_post` = True) | 3,175 (83.6/day) | 3,234 (85.1/day) |
| BIRD SELFIE @-mentions, of posted frames | 6.1/day | 7.0/day |
| "100% shot" (id 2825208) | 80 | **87** |
| "awful" (id 2838001) | 95 | **64** |

Posting volume is held at parity on purpose — **the floor moved with the scale**.
A five-axis score with the floor left at 80 would have posted 3,367 and the ping
left at 90 would have changed how often the pipeline @-mentions Boss for no
stated reason. Only the *order* changes, not the volume. That is the correction
to v2.68.0's mistake in the other direction: it removed the signal to stop it
gating, instead of moving the floor with it.

What actually moves:

| | before | after |
|---|---|---|
| single bird, coverage ≤30% | 539 | **0** |
| single bird, coverage 31-45% | 275 | 52 |
| single bird, coverage ≥46% | 807 | 784 |
| two birds | 795 | 1,111 |
| three birds | 418 | 838 |
| 5+ birds | 146 | 95 |

Single-bird portraits that fill the frame are untouched. What stops posting is
the distant-bird class — which is the frame Boss called awful. The headline
"single-bird postables halved" is entirely that class.

The ping threshold is 87 because that is where the frame Boss called a 100% shot
lands. That is the definition of the ping, so it is the right way to set it; it
is also a single data point, and if #farm-2026 starts feeling like a mention
firehose, this is the first number to re-derive.

Only `s7-cam` produces VLM-scored frames in this window (house-yard and duo2 run
the raw/keyframe lane and never reach `_compute_overall_score`), so there is no
second camera to collapse under a fill axis. If a wide-angle camera is ever added
to the gem lane, re-derive `_SUBJECT_ZERO_PCT`/`_SUBJECT_FULL_PCT` per camera
before enabling it — a yard cam's subjects are small by construction.

## ⚠️ Verified against the archive only

s7-cam was down while this was written and applied (last frame 19:59 UTC /
15:59 ET, Boss restarting the handset). Every number above comes from replaying
archived `vlm_json` through the new code. No frame has been scored live under
v2.73.0 yet. Re-derive the floor and the ping from a week of live rows before
trusting them.

Existing archived rows keep their old three-axis `overall_score` — nothing was
rescored in place, so values before 19-Sep-2026 are on the old scale and should
not be compared to values after it.

## Known limitation, not fixed here

qwen3-vl-4b emits ~6 distinct values for `expression_score` and ~8 for
`detail_score`. Adding two axes raises the composite from 33 distinct values to
58, which helps, but the floor still lands on a quantisation edge and stays
fragile to prompt or model drift. No reweighting fixes this.

The two real fixes, neither done today:
1. Feed `presence.largest_area_pct` (YOLO, continuous) into the subject axis
   instead of the VLM's quantized guess. It is already computed in
   `_hunt_capture` and handed to `run_cycle` as `hunt_out["presence"]`; it is
   simply not persisted, which is why it could not be backtested. Persist it
   first, gather a few weeks, then swap.
2. Crop-and-reask for leg bands, so the band signal Boss actually cares about
   becomes usable at all.

## Rollback

    cp tools/pipeline/orchestrator.py.bak-pre-fillscore-20260919 tools/pipeline/orchestrator.py
    cp tools/pipeline/gem_poster.py.bak-pre-fillscore-20260919   tools/pipeline/gem_poster.py
    launchctl kickstart -k gui/$(id -u)/com.farmguardian.pipeline
