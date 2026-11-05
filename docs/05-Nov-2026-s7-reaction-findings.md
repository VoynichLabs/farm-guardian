# 05-Nov-2026 — What the Boss's reactions say about the S7 scores

Author: Claude Haiku 5.5 Medium
Plan: [05-Nov-2026-s7-reaction-dataset-plan.md](05-Nov-2026-s7-reaction-dataset-plan.md)
Code: [scripts/s7-reaction-dataset.py](../scripts/s7-reaction-dataset.py)
Data (on the SSD, not in git): `/Volumes/Team 4TB/kaggle/s7-reaction-20261105/` — `s7_posts_labelled.csv`, `README.md`, `cache/s7_posts.json`

## Question

Does the Samsung S7's score (the 0–100 figure on each Discord gem post) rank the frames the Boss likes, and which parts of the picture drive his reactions? The useful output is a labelled table: each posted frame, the Boss's reaction as the label, and the frame's details.

## Data and labels

- **Listing.** One paged read of #farm-2026 back to 1 April 2026: 305 pages, 30,500 messages (from the run log; the raw listing is not saved, only the S7 posts). Rate-limited pages wait and retry the same page. The S7 posts are cached on the SSD.
- **Posts.** 7,835 S7 gem posts carry a score line.
- **Label `reacted` = 1.** The reaction sync stamped the post's message ID on the archive frame, which it only does when a post has a human reaction. It is almost always one person, presumably the Boss. A small number of posts have two different human reactors, and reactor identity is not stored, so the label is "a human reacted", not "the Boss reacted".
- **Label `reacted` = 0.** The post showed zero reactions on Discord when the channel was listed (5 November). Every unstamped post showed zero, so the zero label is the current state, not a sync timing gap. It means "no reaction recorded", not a verdict: the Boss may not have looked.
- **Frame link.** A stamped post is linked exactly. Any other post is linked to the frame captured in the 60 seconds before it with the same score. Several candidates are settled by the caption (the mention-heading line is stripped first). 29 posts remain ambiguous and are excluded. Gap between frame and post for stamped pairs: under one to eleven seconds.
- **Validation.** For all 1,382 reacted posts, ignoring their stamp, the stamped frame is among the candidates. Every unforced link (1,376) points to the stamped frame.

### A bug caught along the way

The first linker matched captions alone. Posts begin with a mention line and a "BIRD SELFIE" heading, which the check did not strip, so most high-scoring passed-over posts failed to link and the top bands looked fully reacted. That version was discarded and no result from it is used.

## Scoring eras (read this before any score number)

The score's formula and floor changed inside the window, so pooled bands compare different quantities.

| Era | Dates | What the score used |
|---|---|---|
| pre_floor_80 | 1 Jul – 11 Aug | Older rules; floor 65 from 7 Aug, 70 before. Not analysed here. |
| A_no_fill | 12 Aug – 18 Sep | Expression + detail + technical only; **no frame-fill term** (CHANGELOG v2.68.0, 8 Aug); floor 80 from 12 Aug |
| B1_vlm_fill | 19 Sep – 1 Oct | Frame-fill term added, reading the vision model's own fill answer |
| B2_measured_fill | 2 Oct on | Fill read from the detector's measured fill, falling back to the model's answer when the detector is not confident (CHANGELOG v2.75.1) |

Source: CHANGELOG entries for v2.68.0, the 19 September fill-score change, and v2.75.1. The changelog also says scores before 19 September are on the old scale, and they are not rescored.

## Results

All numbers are from `s7_posts_labelled.csv`, restricted to confidently linked posts at least seven days old.

### Era A — the score ignored frame fill

This is the cleanest test. Fill is in the archive, but the score did not use it, so a fill effect here cannot be the score feeding back.

- 3,144 confidently linked posts, 472 reacted (15.0%). (The era also holds 13 ambiguous posts, excluded.)
- Score bands: 80–84: 13.7% (1,649 posts); 85–89: 13.3% (218); 90–94: 18.8% (319); 95–100: 18.3% (840). The 118 posts below 80 (all from 12 August, scores 66–77) reacted at 2.5%.
- Read: the score ranks only weakly here. The top band is higher, but 85–89 dips below 80–84.

Fill (the vision model's answer, `subject_coverage_pct`):

| Fill | Share reacted |
|---|---|
| 10–19% | 4.8% |
| 20–29% | 7.1% |
| 30–39% | 8.0% |
| 40–49% | 10.8% |
| 50–59% | 16.3% |
| 60–69% | 19.4% |
| 70–79% | 23.1% |

Largest subject (`largest_subject_pct`): 10–19% 5.6%; 30–39% 9.2%; 50–59% 16.6%; 60–69% 20.4%; 70–79% 23.5%.

Composition: portrait 15.5% (2,048 posts); group 14.1% (1,096).

### Era B1 — fill term reading the model's answer

Small (1,002 confidently linked posts; 22.1% reacted). Bands: 80–84 17.4%; 85–89 16.0%; 90–94 29.3%; 95–100 41% on only 39 posts. Fill rises from about 10–15% at 30–40% fill to about 26–32% at 60–70%. Too small to lean on.

### Era B2 — fill term reading the detector's measured fill

1,269 posts, 263 reacted (20.7%).

- Bands: 80–84 12.8% (305); 85–89 20.7% (368); 90–94 24.7% (588); 95–100 37.5% on 8 posts.
- Measured fill, on the 933 posts that have a detector value (the five bins below cover 913; the other 20 are outside them): 30–39% 18.8%; 40–49% 25.3%; 50–59% 31.4%; 60–69% 30.8%; 70–79% 25.7% (35 posts).
- Portrait 24.3% (936 posts) vs group 10.8% (333).

## Fill measured on every frame (5 Nov, later)

The detector's fill was then measured on every linked frame, not only the October-on frames, with `scripts/s7-measured-fill.py`. It reuses the pipeline's own detector (`tools/pipeline/presence.py`, same weights, same threshold, same dark-frame rule). Output: `fill_measured.csv` on the SSD.

**Check against the live values.** On 300 frames that already carried the live pipeline's measured fill, the re-measurement differs by a median of about a quarter of a point, and all 300 agree within two points. The stored JPEGs reproduce the live measurement.

**Coverage.** 7,463 frames measured; 348 had no confident bird box at the pipeline's threshold; 21 were too dark to judge. Frames with no confident box are kept as their own group, not counted as empty.

**Share reacted by measured fill** (confidently linked posts, at least seven days old):

| Fill | Era A (no fill term) | Era B1 (model fill term) | Era B2 (measured fill term) | Before 12 Aug |
|---|---|---|---|---|
| 0–9% | 5.4% | 9.1% | — | 2.3% |
| 10–19% | 7.5% | 8.3% | 0.0% (41 posts) | 6.3% |
| 20–29% | 13.5% | 16.6% | 6.9% | 13.0% |
| 30–39% | 17.1% | 22.5% | 17.0% | 19.6% |
| 40–49% | 24.0% | 28.5% | 25.9% | 23.5% |
| 50–59% | 22.7% | 34.2% | 29.0% | 28.5% |
| 60–69% | 21.8% | 36.4% | 28.6% | 27.6% |
| 70–79% | 27.8% | — | 26.2% | 27.9% |

No confident bird box: 7.3% (Era A, 151 posts); 3.7% (B1, 27); 8.9% (B2, 79); 4.6% (before 12 Aug, 65).

**Ranking ability** (0.5 is chance; same posts in each comparison):

| Measure | Era A | Era B2 |
|---|---|---|
| Measured fill alone | 0.646 | 0.626 |
| Model's fill answer alone | 0.628 | 0.610 |
| Score alone | 0.583 | 0.585 |

## Understanding

**Revised after the full-set measurement:**

- Fill predicts your reactions in every era, including Era A, where the score had no fill term. The pattern holds with the detector's measure as well.
- The detector's fill ranks reacted posts slightly better than the model's fill answer. The margin is small, so the detector is a modest improvement, not a different story.
- The score ranks reacted posts worse than fill alone in both eras. In Era B2 the score includes fill and still ranks below fill by itself, so its other parts add noise. This is the most useful practical point for the posting rules, and changing them is a pipeline change that needs its own plan.
- Frames the detector finds no bird in are rarely reacted to.
- Ranking figures are computed on posted frames only, which are already selected.

The earlier summary below still stands, with the era numbers above replacing the pooled ones.


1. **Frame fill is the strongest signal, and it appears in every era, including Era A when the score ignored fill.** Share reacted rises from about 5% for a small bird to about 23% for a bird filling most of the frame. That is the clearest result here.
2. **Portrait framing also helps.** It shows in Era A (slightly) and clearly in B2.
3. **The score ranks weakly in Era A.** It tilts the right way but does not separate well. In B2 the bands rise more steeply, but the fill term is part of the score there, so the rise is probably the fill term doing the work. Separating the two needs component-level data we have not analysed.
4. **Not separable here:** scene, lighting and sharpness barely vary in the posted set; expression and detail are concentrated at a few values and were not analysed.

## Corrections from the independent checks

Checks were run on the numbers and on the conclusions. These points were withdrawn or reworded:

- **Standout flag.** Withdrawn. The field only exists from about late September, so "flagged vs not" is a date comparison, not a standout effect.
- **Expression and detail "flat".** Withdrawn. They are not nearly all at one value and were not analysed.
- **Pooled score bands.** Replaced by per-era tables above.
- **"The model's fill answer runs about 18 points high".** Reworded: on the 933 posted frames with a detector value, the model's figure is a median of 18 points above the measured one. These are selected posts, so this is not a general bias claim.
- **"Score rewards an inflated fill figure".** Withdrawn. Within the B2 frames the stored score tracks the detector figure, and the fill term did not exist in Era A.
- **"Reaction share flattens around half the frame".** Withdrawn. The top groups are small.
- **Scene.** Seven posts are coop (1–11 September), not all birdcatraz.
- **Stamped-pair gap.** Corrected to under one to eleven seconds.
- **Listing counts.** From the run log, not from a saved listing.

## Caveats

- A reaction is one person's yes. A zero is no recorded reaction, not a verdict.
- Fill, activity, bird count, scene and sub-scores are model outputs, not human labels.
- Observational only. These are associations, not causes.
- Posts are a selected set: frames that passed the scoring gate and the floor.
- The fill figures are the detector's reading of each frame at the pipeline's threshold. Its misses (about one bird frame in twenty) sit in the no-box group, not in the empty ones.
- Ranking figures compare reacted and passed-over posts that were already selected for posting. They say how well fill sorts those posts, not how well it would sort all frames.

## Next steps

1. Done: fill is measured on every linked frame (see the section above).
2. Separate the fill term's effect from the rest of the score inside B2.
3. Before any Kaggle upload: write these caveats on the card, decide the label wording ("a human reacted"), and do the people-in-frame check.
4. Any change to how the score uses fill is a pipeline change. The plan is in [05-Nov-2026-s7-fill-first-posting-plan.md](05-Nov-2026-s7-fill-first-posting-plan.md), and it is awaiting approval; nothing has been changed.
