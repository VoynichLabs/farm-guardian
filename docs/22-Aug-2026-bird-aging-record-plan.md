<!--
Author: Claude Opus 5 (Bubba)
Date: 22-August-2026
PURPOSE: Plan for turning the flock's existing dated photos into a year-over-year
         aging reference, so next spring's chicks can be compared against this
         year's at the same age. Written for Dr. Opus to implement.
SRP/DRY check: Pass — extends docs/../2026-08-11-bird-observation-timestamps.md
         (which added dates to observations but not coverage across ages).
-->

# Bird aging record — plan

**What the Boss wants:** a record of what each bird looked like at various ages
— hatch, six weeks, twelve weeks, adult. So next year when he hatches birds, he
can compare them against this year's at the same age.

## What we already have

The photos are on disk and already dated. 61 dated photo entries in
`flock-profiles.json`. Twelve birds have a real series:

| bird | ages photographed (weeks) |
|---|---|
| Henridotta | 0.1, 6.3, 6.9, 6.9, 8.0, 8.0, 8.0, 10.7, 11.4, 11.4 |
| Birddor | 0.0, 1.0, 3.0, 6.3, 6.3, 11.1 |
| Birdsilla | 0.6, 1.7, 1.7, 5.4, 8.7, 9.4 |
| Henriello | 0.6, 5.4, 5.4, 5.4, 8.7, 9.7 |
| Henriessa | 0.1, 6.3, 6.9, 6.9, 9.7 |
| Birdthazar | 0.6, 5.4, 5.4, 8.7 |
| Birdimir, Ingebird, Horstabird, Adelbird, Birdadotta, Chonkette | 2–3 each |

The growth curve is there. Nobody ever wrote down what the pictures show.

## What's missing

**1. A description per photo, not per bird.** Right now each bird has one
description covering ten photos. Nothing says what it looked like at six weeks
versus eleven. That's the gap.

**2. Seven birds get no age at all.** Scissor Beak, Robirda, Bobirda, Chonkers,
Chonkette, Ravenessa, Quasibirdo have `hatch_date_estimated: true`, and
`bird_photo_ingest.py:429` and `backfill_color_observations.py:145` both refuse
to compute weeks from an estimated hatch date. `age_weeks()` itself is fine with
it — the refusal is caller-side. For "what does a six-week-old look like," an
estimate within a couple weeks is good enough. As it stands a quarter of the
flock is excluded for nothing.

**3. No way to ask the question.** "Show me every Henrietta-line bird at six
weeks" is the whole point and nothing answers it.

## Changes needed

**a. New `source` value.** `_set_photo_and_commit` hardcodes
`source="boss-caption"`. Descriptions written by an agent from an archived photo
must not be stamped as the Boss's words — 61 of them would poison the reference
he'll be trusting next spring. Needs something like `agent-observed-archive`,
and the module doctrine ("the VLM never describes the bird for the roster")
should be amended to allow it explicitly for dated archive backfill only, never
for live filing.

**b. Relax the estimated-hatch rule.** Compute `age_weeks` from an estimated
hatch date and mark the entry `age_estimated: true` so it's honest about it.

**c. Backfill script.** Walk each bird's dated `photos[]`, describe each image,
append a `color_observations[]` entry stamped with that photo's date and
computed age.

**Verified safe:** `_sort_key` in `validate_flock_profiles.py` sorts
observations chronologically (undated first). A backfilled 0.6-week entry dated
back in June slots into history and will NOT displace the current
`color_description`. This was the one thing that could have broken quietly.

**d. Age query.** Something that answers "all birds at N weeks", filterable by
line/breed. This is the deliverable the Boss actually asked for; a–c are what
make it possible.

## Order

c depends on a and b. Do a and b, spot-check the backfill on Henridotta (best
series, 10 photos) before running the other 11 birds, then build d.
