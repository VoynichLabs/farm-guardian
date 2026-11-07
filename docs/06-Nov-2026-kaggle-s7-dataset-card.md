# Chicken & Turkey Photos Labelled by Chat Reactions 🐔🦃

{{N_FRAMES}} photos from Birdcatraz, my chicken-and-turkey enclosure 🐓🦃🐣. Each one is labelled with whether anyone reacted when it got posted to my Discord, and how many different people did. A phone camera took them between {{FIRST_DAY}} and {{LAST_DAY}}. The same frames also feed my Instagram Reels.

**Four quick things before you dig in 🥚**

1. 🐥 **The written descriptions are often wrong.** The caption and almost every other descriptive column were written by a small vision-language model (Qwen3-VL 4B for all but {{N_OTHER_MODEL}} frames, which were scored by larger Qwen models that happened to be loaded; the table does not say which). I can tell you a lot of them are flat out wrong: sometimes a bird seen from behind is described as looking straight at the camera, and sometimes a chicken is called a turkey. A spot check below agrees for eye-contact claims, and the problem is large (the turkey point is only a hint). Do not treat `caption` as ground truth, and treat the other model columns as unchecked guesses.
2. 🐔 **It is a re-ranking dataset, not a "rate any chicken photo" dataset.** Every picture here had already cleared an automatic posting gate before anyone saw it, and the label records which of those already-good frames anyone bothered to react to. The best use is learning to rank good frames the way these people do. A model trained on it will not learn what a bad chicken photo looks like, because there are few or none in it.
3. 🍳 **A label of 0 means "no reaction was recorded", not "disliked".** Any emoji counts as a reaction and the emoji was not recorded, so a 1 means someone reacted, not necessarily that they liked it.
4. 🪺 **`reaction_people` is part of the label, not a feature.** It is zero exactly when `reacted` is zero. Leave both out of your inputs.

## The descriptions are often wrong 🐓

The captions, and the vision model's other columns (`composition`, `scene`, `lighting`, `activity`, `bird_count`, `expression_score`, `detail_score`, `subject_coverage_pct`, `largest_subject_pct`), are that model's guesses, not facts about the picture. My own observation is that captions sometimes say a bird is looking at the camera when it is seen from behind, and sometimes call a chicken a turkey. To see how often, {{SPOT_N}} frames were drawn at random (40 from each scoring era, blank captions excluded) and each was checked twice by AI readers (Claude models from Anthropic) who opened the picture: once with the caption beside it, and once blind to the caption, classifying which way the main bird faces. Where the first two readers disagreed, a third decided, so each final call comes from two readers who agreed, or from the third reader's call where they did not. **This is an informal check by AI readers, not a human audit**, and I did not re-judge the pictures myself. Treat the figures as rough.

- {{SPOT_CLAIMED}} of the {{SPOT_N}} captions say or imply that a bird is looking at the camera. For {{SPOT_FACING_WRONG}} of those ({{SPOT_FACING_WRONG_PCT}}) the readers found that was not true. If a bird turned three-quarters toward the lens is allowed to count as eye contact, the figure falls to {{SPOT_FACING_LENIENT}} of {{SPOT_CLAIMED}} ({{SPOT_FACING_LENIENT_PCT}}); on either reading, most of these captions overstate it. The {{SPOT_FACING_WRONG}} comes from the pass in which readers judged each caption's own claim; the table comes from the separate blind pass, which counts only a face clearly toward the lens, so {{SPOT_NOT_FACING}} of the {{SPOT_CLAIMED}} were not clearly facing it there. The two passes were run separately and do not match exactly.
- Blind to the captions, the readers found the main bird clearly facing the lens in only {{ORIENT_ALL_FACING}} of the {{SPOT_N}} pictures. For the {{SPOT_CLAIMED}} captions that claim eye contact, the main bird was actually:

{{ORIENT_TABLE}}

  Three-quarter views are a judgement call: the face is partly toward the camera, but these captions describe eye contact with the lens, which a partial turn does not give. The "main bird" is the most prominent bird, chosen by the blind reader; it is not always the bird the caption describes.
- {{SPOT_SPECIES}} captions named a turkey that the readers could not find in the picture. In each case it was a background bird and the readers were not always sure, so read this as a hint rather than a finding. (The enclosure does hold real turkeys 🦃, and they are in the pictures.)
- {{SPOT_OTHER}} captions made some other claim (an activity, an object, a colour, a count, how sharp the picture is) that the picture plainly contradicts.
- All told, {{SPOT_ANY}} of the {{SPOT_N}} captions ({{SPOT_ANY_PCT}}) had at least one claim the picture contradicts. The first two readers disagreed on at least one point for {{SPOT_ANY_SPLIT}} of the {{SPOT_N}} frames ({{SPOT_SPLIT}} on the caption claims, {{SPOT_ORIENT_SPLIT}} on which way the bird faces, {{SPOT_BOTH_SPLIT}} on both), so the figures are rough. Where a reader answered "unclear" the claim was counted as holding, so the contradiction figures are, if anything, low. In {{SPOT_ONLY_EYE}} of the {{SPOT_ANY}} captions the only contradicted claim was about eye contact; {{SPOT_OTHER_ANY}} of the {{SPOT_N}} had some other contradicted claim (a species, an activity, an object, a colour, a count or sharpness).
- `bird_count` held up better: it was within one bird of the average of the two readers' own counts for {{BIRD_COUNT_WITHIN1}} of {{BIRD_COUNT_N}} checked frames (a constant guess of {{BC_CONST}} birds would be within one for {{BC_BASE}}). The other model columns (`composition`, `scene`, `lighting`, `activity`, the scores and fill guesses) were not checked at all.
- The facing-claim problem is not confined to one era:

{{SPOT_FACING_TABLE}}

- **Why the captions say this so often.** The captioning prompt's own example caption reads "A rust-and-gold hen looks straight into the lens, comb bright red", and the prompt rates direct eye contact highest. {{N_STRAIGHT}} of the {{N_FRAMES}} captions contain the words "straight into the lens". These rates describe frames that were also selected for a visible face, not the model's captions in general.
- **The posting gate did not guarantee a readable face.** It needed the model to report a face visible, and the prompt counts any eye, beak or facial profile, even side-on, so side and three-quarter views pass legitimately. But in the blind pass {{SPOT_NO_FACE}} of the {{SPOT_N}} frames showed the main bird from behind or hidden, cut off or too blurred to tell. A `bird_face_visible` column is not shipped: it is true on every row because only frames where the model claimed a face got through, not because a face was readable.
- In this sample, pictures where the main bird clearly faced the lens drew a reaction in {{SPOT_REACT_FACING}}, against {{SPOT_REACT_OTHER}} for all others. That is too few frames to tell whether facing the lens matters to these people.

`caption_spot_check.csv` holds the per-frame result for all {{SPOT_N}} frames (see "Files"), so you can measure caption reliability yourself. It is not a test set for models trained on the suggested split: {{SPOT_TRAIN}} of the {{SPOT_N}} frames are in the train fold ({{SPOT_VAL}} validation, {{SPOT_TEST}} test). Everything outside it is unchecked.

## Where the pictures come from (short version) 🐣

A Samsung Galaxy S7 phone at a fixed spot over the farm's poultry enclosure ("Birdcatraz": chickens and turkeys) takes a picture every few seconds in daylight. A small vision-language model rates each frame; the best ones are posted to a chat channel with the picture, a caption and a score, and people in the chat server react to posts with any emoji. The full account (sources, how frames were captured, scored, posted, labelled and joined, and the AI spot check) is in the **Provenance** section of this dataset's metadata tab.

What matters when you open the pictures: portrait, 1080 x 1920, the original archive JPEG with no resizing and no metadata. I haven't hand-checked them. 🦃 Turkeys are in here along with the chickens, so don't assume every bird is a chicken. The only person who appears is me, and the farm dog can show up too. In the early weeks the phone was sometimes carried indoors, so a few frames may show a room or the farm dog instead of the flock, and some frames, mostly from July, come from other spots (`scene` shows `yard` and `coop`; it is the model's own label and not a reliable screen). The handset was replaced by another Galaxy S7 on 10 August 2026. {{N_DAYS}} days have frames. File size is not comparable across 7 August 2026: earlier files were saved at lower JPEG quality than later ones.

## The label 🥚

`reacted` is 1 when at least one person reacted to the post of that frame, and 0 when no reaction was recorded. `reaction_people` is how many different people reacted: 0, 1, 2 or 3.

- **What counts.** Any emoji, from non-bot accounts only. Reacted posts are tied to their picture through a message id that the reaction-sync job stamps on the archived frame; unreacted posts are tied by capture time and score.
- **Who reacts.** Anyone in the chat server can. Most of the reactions are mine, but there is no record of who reacted. So `reaction_people` of 1 does not mean "the owner", and 2 does not mean "the owner plus one". Of the {{N_REACTED}} reacted frames, {{N_PEOPLE_1}} were reacted to by one person, {{N_PEOPLE_2}} by two and {{N_PEOPLE_3}} by three. That is too few multi-person frames to model on their own, and the share is not steady over time (share of reacted frames with two or more people: {{PEOPLE_BY_ERA}}), so a graded target or weight built from it will partly encode the date. Use it with care.
- **`reaction_people` is part of the label, not a feature.** It is zero exactly when `reacted` is zero, so using it as an input leaks the label. Both columns come from the same reaction-sync job, so their agreement is not independent evidence. The job re-checks each post for about 48 hours, so `reaction_people` does not count reactions added later. Every post labelled 0 showed no reaction of any kind in the whole-channel listing read on 5 November.
- **A 0 is not a dislike.** It means no reaction was recorded when the channel was read on 5 November 2026. They may not have looked.
- **What we saw.** The post showed the picture, its caption and its score. From 19 September, posts scoring 87 or more also notified me directly (90 or more before that), which plausibly made them more likely to be noticed (those posts also opened with a mention and heading line that is not in the shipped caption). A reaction is a reaction to the post, not only to the picture. The caption they read is often wrong (above), so a reaction can reflect a caption that did not match the picture.
- **What is left out.** Of {{N_POSTS}} posts, {{N_UNLINKED}} could not be tied to a single picture with confidence and are excluded, and {{N_TOO_RECENT}} posted after {{CUTOFF}} are excluded because a recent post has not had time to collect a reaction, so its zero is not yet a label. {{N_DUP_POSTS}} pictures were posted twice and appear once, counted as reacted if either post was. That leaves {{N_FRAMES}} rows, of which {{N_REACTED}} ({{REACT_SHARE}}) are reacted.

## Files 🐤

- `labels.csv`: one row per picture (columns below).
- `images/YYYY-MM/<frame_id>.jpg`: the pictures.
- `caption_spot_check.csv`: the AI spot check of {{SPOT_N}} frames described above.
- `caption_spot_check_readers.csv`: every AI reader's own answers and short notes behind that check (the settled result is in the file above).
- `README.md`: this card.

## Columns

`labels.csv`, one row per picture. The picture is at the path in `image`. Columns that would give the label away (how the post was matched to the picture, message ids, post times) or that are effectively constant because of the posting gate are not shipped (`bird_face_visible`, `share_worth`, `image_quality`, and the model's `standout_bird` flag, which is true on nearly every row that has it and is blank for everything before 21 September, so it only marks the date).

| Column | What it is |
|---|---|
| `frame_id` | Row number in the farm's archive. The join key. It rises with capture time, so join on it, do not feed it to a model. |
| `day` | The US Eastern calendar date of capture (the farm's local day). Group by this, not by the date in `captured_at`, which is UTC: the suggested split uses these days. |
| `image` | Path of the picture, `images/YYYY-MM/<frame_id>.jpg`. |
| `captured_at` | When the phone took the frame (UTC). |
| `scoring_era`, `rollout_day` | Which scoring rules were live. See below. |
| `split` | Suggested split: `train`, `val`, `test`. See below. |
| `reacted`, `reaction_people` | The label and its count. Never use `reaction_people` as an input. |
| `score` | The 0 to 100 score printed on the post. Computed by the farm's pipeline from the vision model's sub-scores and a fill term, not written by the model and not a human label. |
| `composition`, `scene`, `lighting`, `activity`, `bird_count`, `expression_score`, `detail_score`, `subject_coverage_pct`, `largest_subject_pct`, `caption` | What the vision model said about the frame. `caption` is often wrong (above); the rest are unchecked guesses, except `bird_count`, which held up better. `lighting` is almost always `natural-good`. `expression_score` runs 10 to 30 and `detail_score` 15 to 25. |
| `measured_fill_pct`, `fill_status` | How much of the frame the birds cover, measured by an object detector rather than guessed by the vision model. Blank when `fill_status` is not `ok`. |

**Everything the vision model produced is that small model's output, not ground truth.** Treat it as a feature to test, and expect errors of the kind above. Its fill guess runs high next to the detector's measurement (that is why `measured_fill_pct` is included), and many captions repeat verbatim.

**Every vision-model column drifts between eras, not only the score**, because the prompt and what it asked for changed. For example the share of frames labelled `none-visible`, the average `bird_count` and the caption length all differ by era. Early captions are longer and can be cut off mid-sentence, and caption length by itself carries signal in the earliest era that probably reflects the old prompt, not the picture. {{N_BLANKED}} captions where the model had echoed its own instructions or slipped into another language script are blank. Stratify or normalise every model-output column by `scoring_era`. The vision-model prompt also changed on 6 August (inside `early_older_rules`) and on 21 September (inside `B1_vlm_fill`), and the era boundaries do not mark those, so model columns can step on those days too.

`fill_status` values: `ok`; `no_bird_detected` (the detector found no confident bird box; the vision model nonetheless reported birds, and the picture may have a bird at the edge or partly hidden); `skipped_dark_gate` (the detector's brightness gate skipped the frame; the name describes the gate, not necessarily how the picture looks). {{N_NO_FILL}} frames have no measured fill, and only {{NO_FILL_REACTED}} of them were reacted to against {{REACT_SHARE}} overall.

`caption_spot_check.csv` columns: `frame_id` (joins to `labels.csv`); `caption_claims_eye_contact` (1 if the caption says or implies a bird looks at the camera); `caption_eye_contact_wrong` (1 if so and the final reading found it untrue, 0 if true, blank if no claim); `caption_species_wrong` (1 if the caption names a species, in practice a turkey, that the readers could not find); `caption_other_claim_wrong` (1 if some other specific claim is plainly contradicted); `caption_any_claim_wrong` (1 if any of the three); `main_bird_orientation` (the blind reading of the most prominent bird: `facing_lens`, `three_quarter`, `side_profile`, `rear_view`, `head_down`, `hidden_or_blurred`); `readers_split_on_caption` and `readers_split_on_orientation` (1 where the first two readers disagreed and a third decided).

`caption_spot_check_readers.csv` is long format, one row per frame, pass and reader: `pass` is `caption_check` (reader saw the caption) or `blind_orientation` (picture only); `reader` is A or B (the first two) or C (the third, only where they disagreed); the answer columns are filled for the pass they belong to, and `note` is the reader's own short comment.

## Scoring eras: do not pool `score` across them

The scoring formula and the posting minimum changed during this period. The same score means different things in different eras, so any question about what the score rewards has to be asked inside one era.

{{ERA_TABLE}}

`early_older_rules` is a catch-all, not one regime: the lowest allowed score fell from 70 to 65 on 7 August and the formula was rewritten on 8 August, so `rollout_day` flags both days. `A_no_fill` has no term for how much of the frame a bird fills. `B1_vlm_fill` adds one that reads the vision model's guess, plus a small term for how many birds are in frame. `B2_measured_fill` reads the detector's measurement and falls back to the vision model's guess when the detector finds no bird; from 2 October a pixel-based sharpness check can also overrule the model's own sharpness call. `rollout_day` (dated by the UTC capture date, so it can differ from `day` on a couple of frames) is 1 on days a change went live part-way through, so those frames mix old and new rules; drop them for a clean era comparison.

## Splitting: please don't split at random 🐥

Frames taken seconds apart are nearly the same picture. A random train/test split puts near-duplicates on both sides and inflates every score. The `split` column assigns whole calendar days (US Eastern, the farm's local time) to one fold, shuffled within each scoring era so every era is in every fold.

{{SPLIT_TABLE}}

The test fold is only about 16 days, and within one era it is far smaller (test-fold days per era: {{TEST_DAYS_BY_ERA}}), so test scores will be noisy. Grouped-by-day cross-validation (group on `day`) is a better guide than one test number, and within a single era it is the only sensible option.

## Reactions cluster in time

Across the {{N_BUSY_DAYS}} days with 30 or more frames, the reacted share runs from {{DAY_RATE_MIN}} to {{DAY_RATE_MAX}}, and a frame is far more likely to be reacted to when the frame just before it was. Rows are not independent: the effective number of independent examples is nearer a thousand than {{N_FRAMES}}. Report uncertainty, group by day, and expect part of any pooled score to come from recognising which days drew attention rather than which pictures did.

## Baselines

Ranking ability of single columns for the reacted label, measured as the chance that a random reacted frame outranks a random unreacted one (0.5 is chance, 1.0 is perfect), per era, excluding rollout days, on all rows in this table:

{{BASELINE_TABLE}}

Blank `measured_fill_pct` is read two ways: as zero fill (a frame with no detectable bird fills none of the frame; the {{N_SKIPPED_DARK}} frames the detector skipped are blank too), or as the vision model's own guess. Among the columns compared here, measured fill (read with blanks as zero) ranks reacted frames best after the earliest era, by about 0.04 to 0.07 over the score (the scores in `B1_vlm_fill` and `B2_measured_fill` already contain a fill term), and in the earliest era the score is slightly ahead. Treat each figure as uncertain: day-level bootstrap intervals run roughly 0.03 to 0.07 either way, so the differences are small, and in the newest era the answer depends on how blanks are read (read as the model's guess, the two come out level within noise). Part of the score's ranking power after the earliest era comes from the direct notification to the owner at 87 or more (90 or more before 19 September), which makes those posts more likely to be seen; that threshold change falls on the first day of `B1_vlm_fill`, so the rise in reacted share from `A_no_fill` to `B1_vlm_fill` mixes it with the new fill term. These are associations on an already-selected set of good frames, not causes. They say how well a column sorts these frames, not how well it would sort every frame the camera took.

## Caveats

- A small group of people's taste, in one place, on a phone camera that was replaced once. Who reacted was not kept.
- Selected frames only, as described at the top.
- Newer pictures will be judged by a different model (Gemma 4 E2B), so expect the model columns to change character after this snapshot.
- A zero label is the absence of a recorded reaction.
- The caption is often wrong, and the other model columns are unchecked. The AI spot check is rough and covers {{SPOT_N}} frames.
- A reaction can depend on when the post appeared and on the score and caption shown with it, as well as on the picture. Nothing here controls for that.
- Size on disk: {{SIZE_GB}} GB.

## License and credit 🐔🐓🐣🐤🐥🥚🍳🦃

{{LICENSE}}. Credit: Farm Guardian project, VoynichLabs. The pictures are mine; the reactions are by people in my Discord server. Have fun, and say hi to the turkeys 🦃.
