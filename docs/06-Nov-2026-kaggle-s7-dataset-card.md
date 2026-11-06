# Chicken Photos Labelled by a Farmer's Reactions

{{N_FRAMES}} photos from the poultry enclosure of a small farm, each labelled with whether a person reacted to it when it was shared in a chat channel. Taken by a phone camera between {{FIRST_DAY}} and {{LAST_DAY}}.

**Read this first. It is a re-ranking dataset, not a "rate any chicken photo" dataset.** Every picture here had already cleared an automatic posting gate (below) before anyone saw it, and the label records which of those already-good frames the farmer bothered to react to. The best use is learning to rank good frames the way he does. A model trained on it will not learn what a bad chicken photo looks like, because there are few or none in it.

The other two things to know before you start: a label of 0 means "no reaction was recorded", not "disliked"; and almost every column other than `reacted` is the output of a small vision-language model, not a human judgement.

## Where the pictures come from

A Samsung Galaxy S7 phone sits in a fixed position at the farm and takes a photo every few seconds. A small vision-language model rates each frame, and the best ones are posted to a chat channel with the picture, a caption and a score (shown as, for example, 87/100). The farm owner reacts to the posts he likes, with whichever emoji is quickest. The emoji does not matter and is not recorded.

- **The posting gate.** A frame was posted only if the vision model rated it "strong", its score cleared a minimum, and it passed a few further checks (for example that the picture is sharp and a bird's face is visible). The lowest score that was ever posted in each era is in the table below.
- **The place.** "Birdcatraz" is the farm's name for the fenced enclosure that holds the chickens and turkeys. From August the camera stays in that one spot. The first weeks, in July, include some frames from other spots (`scene` shows `yard` and `coop` for those).
- **The phone.** The handset was replaced by another Galaxy S7 on 10 August 2026, so the earliest era is not one physical camera.
- **Gaps.** {{N_DAYS}} days have frames. The phone was off or away on other days (it was charged indoors in the early weeks).
- **The pictures.** Portrait, 1080 x 1920, the original archive JPEG with no resizing, daylight, chickens and turkeys. They were **not hand-checked**: in the early weeks the phone was sometimes carried indoors, so a few frames may show a room or the farm dog instead of the flock. `scene` may help you filter, but it is the vision model's own label and is not a reliable screen. File size is not comparable across 7 August 2026: earlier files were saved at lower JPEG quality than later ones.

## The label

`reacted` is 1 when a person reacted to the post of that frame, and 0 when no reaction was recorded.

- **A 0 is not a dislike.** It means no reaction was recorded when the channel was read on 5 November 2026. He may not have looked.
- **Whose reaction.** Nearly all reactions are from the farm owner. A small number of posts were also reacted to by a second person, and reactor identity was not kept, so the strict reading is "a person reacted".
- **What the person saw.** The post showed the picture, its caption and its score. From 19 September, posts scoring 87 or more also notified the owner directly (90 or more before that), which makes high-scoring posts more likely to be noticed. A reaction is a reaction to the post, not only to the picture.
- **What is left out.** Of {{N_POSTS}} posts, {{N_UNLINKED}} could not be tied to a single picture with confidence and are excluded, and {{N_TOO_RECENT}} posted after {{CUTOFF}} are excluded because a recent post has not had time to collect a reaction, so its zero is not yet a label. {{N_DUP_POSTS}} pictures were posted twice and appear once, counted as reacted if either post was. That leaves {{N_FRAMES}} rows, of which {{N_REACTED}} ({{REACT_SHARE}}) are reacted.

## Columns

`labels.csv`, one row per picture. The picture is at the path in `image`. Columns that would give the label away (how the post was matched to the picture, message ids, post times) or that never vary because of the posting gate are not shipped.

| Column | What it is |
|---|---|
| `frame_id` | Row number in the farm's archive. The join key. It rises with capture time, so join on it, do not feed it to a model. |
| `image` | Path of the picture, `images/YYYY-MM/<frame_id>.jpg`. |
| `captured_at` | When the phone took the frame (UTC). |
| `scoring_era`, `rollout_day` | Which scoring rules were live. See below. |
| `split` | Suggested split: `train`, `val`, `test`. See below. |
| `reacted` | The label. |
| `score` | The 0 to 100 score printed on the post. |
| `composition`, `scene`, `lighting`, `activity`, `bird_count`, `standout_bird`, `expression_score`, `detail_score`, `subject_coverage_pct`, `largest_subject_pct`, `caption` | What the vision model said about the frame. `lighting` is almost always `natural-good`. `expression_score` runs 10 to 30 and `detail_score` 15 to 25. |
| `measured_fill_pct`, `fill_status` | How much of the frame the birds cover, measured by an object detector rather than guessed by the vision model. Blank when `fill_status` is not `ok`. |

**Everything the vision model produced is that small model's output, not ground truth.** Treat it as a feature to test. It answers by rote for some fields: its fill guess runs high next to the detector's measurement (that is why `measured_fill_pct` is included), and many captions repeat verbatim. `standout_bird` is blank until part-way through 21 September 2026 because the field did not exist; it replaced an older flag with a different meaning.

**Every vision-model column drifts between eras, not only the score**, because the prompt and what it asked for changed. For example the share of frames labelled `none-visible`, the average `bird_count` and the caption length all differ by era. Early captions are longer and can be cut off mid-sentence, and caption length by itself carries signal in the earliest era that reflects the old prompt, not the picture. {{N_BLANKED}} captions where the model had echoed its own instructions or slipped into another language script are blank. Stratify or normalise every model-output column by `scoring_era`.

`fill_status` values: `ok`; `no_bird_detected` (the detector found no confident bird box; the vision model nonetheless reported birds, and the picture may have a bird at the edge or partly hidden); `skipped_dark_gate` (the detector's brightness gate skipped the frame; the name describes the gate, not necessarily how the picture looks). {{N_NO_FILL}} frames have no measured fill, and only {{NO_FILL_REACTED}} of them were reacted to against {{REACT_SHARE}} overall.

## Scoring eras: do not pool `score` across them

The scoring formula and the posting minimum changed during this period. The same score means different things in different eras, so any question about what the score rewards has to be asked inside one era.

{{ERA_TABLE}}

`early_older_rules` is a catch-all, not one regime: the lowest allowed score fell from 70 to 65 on 7 August and the formula was rewritten on 8 August, so `rollout_day` flags both days. `A_no_fill` has no term for how much of the frame a bird fills. `B1_vlm_fill` adds one that reads the vision model's guess, plus a small term for how many birds are in frame. `B2_measured_fill` reads the detector's measurement and falls back to the vision model's guess when the detector finds no bird. `rollout_day` is 1 on days a change went live part-way through, so those frames mix old and new rules; drop them for a clean era comparison.

## Splitting: do not split at random

Frames taken seconds apart are nearly the same picture. A random train/test split puts near-duplicates on both sides and inflates every score. The `split` column assigns whole calendar days (US Eastern, the farm's local time) to one fold, shuffled within each scoring era so every era is in every fold.

{{SPLIT_TABLE}}

The test fold is only about 16 days, so test scores will be noisy. Grouped-by-day cross-validation is a better guide than one test number.

## Reactions cluster in time

Across the {{N_BUSY_DAYS}} days with 30 or more frames, the reacted share runs from {{DAY_RATE_MIN}} to {{DAY_RATE_MAX}}, and a frame is far more likely to be reacted to when the frame just before it was. Rows are not independent: the effective number of independent examples is nearer a thousand than {{N_FRAMES}}. Report uncertainty, group by day, and expect part of any pooled score to come from recognising which days drew attention rather than which pictures did.

## Baselines

Ranking ability of single columns for the reacted label, measured as the chance that a random reacted frame outranks a random unreacted one (0.5 is chance, 1.0 is perfect), per era, excluding rollout days, on all rows in this table:

{{BASELINE_TABLE}}

Blank `measured_fill_pct` is read two ways: as zero fill (a frame with no detectable bird fills none of the frame), or as the vision model's own guess. How much of the frame a bird fills is the clearest single signal of what this person reacts to among the columns compared here: after the earliest era, measured fill (read with blanks as zero) ranks reacted frames about 0.04 to 0.07 better than the score does, and in the earliest era the score is slightly ahead. Treat each figure as uncertain: day-level bootstrap intervals run roughly 0.03 to 0.07 either way, so the differences are small, and in the newest era the answer depends on how blanks are read (read as the model's guess, the two come out level within noise). These are associations on an already-selected set of good frames, not causes. They say how well a column sorts these frames, not how well it would sort every frame the camera took.

## Caveats

- One farmer's taste, in one place, on a phone camera that was replaced once.
- Selected frames only, as described at the top.
- A zero label is the absence of a recorded reaction.
- A reaction can depend on when the post appeared and on the score and caption shown with it, as well as on the picture. Nothing here controls for that.
- Size on disk: {{SIZE_GB}} GB.

## License and credit

{{LICENSE}}. Credit: Farm Guardian project, VoynichLabs. The pictures and labels are the farm owner's own.
