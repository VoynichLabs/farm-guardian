# 06-Nov-2026 — Publish the S7 reaction dataset to Kaggle (private first)

Author: Claude Sonnet 5.5 Medium

**Status (06-Nov-2026):** done up to the public switch. Uploaded as the private Kaggle dataset
`markbarney/s7-chicken-photo-reactions` (https://www.kaggle.com/datasets/markbarney/s7-chicken-photo-reactions);
Kaggle lists 7,520 pictures (July 1,060, August 2,705, September 2,310, October 1,445 - the same
counts as on disk), `labels.csv` and `README.md`, 11.97 GB, private, CC BY 4.0. Making it public
is the Boss's one click. To publish a corrected version later, rebuild with
`scripts/kaggle-s7-package.py` and use `kaggle datasets version` with `--dir-mode zip` and
`TMPDIR` on the Team 4TB drive.

## Scope

**In:** turn the labelled S7 post table (`docs/05-Nov-2026-s7-reaction-dataset-plan.md`) into a
proper Kaggle dataset **with the pictures**: one row per archive frame, the human label
(did the Boss react to it when it was posted), the scoring features, a split that does not leak,
a dataset card, and a private upload on the `markbarney` Kaggle account. The Boss approved
sharing it ("I'd love to share it") on 06-Nov-2026.

**Out:** making it public (that is the Boss's one click after he has read the card). No pipeline,
posting, scoring or Discord change. No new Discord reads (the cached channel listing already
holds everything). The fill-first posting question stays parked.

## Decisions taken (advisor review, 06-Nov-2026)

1. **`link_method` does not ship.** Every reacted row was linked by the stamped message id and
   every stamped row is reacted, so the column alone predicts the label perfectly. Dropped,
   together with the 29 `ambiguous` posts and any `no_frame_found` post (no column left to
   exclude them with). `msg_id` and `posted_at` also go (private-server ids, and `posted_at`
   adds nothing over the capture time).
2. **Timestamp lag checked for the same leak, and it is clean:** capture-to-post lag has the
   same distribution for reacted and unreacted posts (median 1.21 s both). `frame_captured_at`
   stays.
3. **One row per frame.** Three frames were posted twice; they collapse to one row, reacted if
   any post of that frame was reacted to (the same rule the analysis used).
4. **Constant columns dropped** (`share_worth` and `image_quality` are the same for every row,
   a consequence of the posting gate). `scoring_era` stays and the card says plainly: do not
   pool `score` across eras (four values, including `pre_floor_80` which the analysis never
   covered).
5. **The selection caveat leads the card:** every row passed the posting gate, so this is a
   re-ranking set over already-good frames, a zero means "no reaction", and the scoring fields
   are a small vision model's output rather than ground truth.
6. **A split ships, by whole calendar days**, shuffled within each scoring era so every era
   appears in train, validation and test. Frames seconds apart are near-duplicates; a random
   split would inflate every score, and the card says so.
7. **Pictures at original resolution** (1080 x 1920, about 12.5 GB total, all 7,832 still on
   disk). Kaggle's limit is 200 GB per dataset; the 50-top-level-file limit means the images go
   in `images/YYYY-MM/` folders. One version only. If the upload throughput or a limit bites,
   downscale, do not build two.
8. **No picture screening (Boss, 06-Nov-2026: "You don't need to check the images for people
   or bad frames").** The advisor suggested a quality screen (detector scan plus contact-sheet
   review); it was started, stopped on his word, and its script and output deleted. Nothing is
   dropped for content. The model's `scene` column ships so users can filter, and the card
   says in one line that the pictures were not hand-checked.
9. **License CC BY 4.0, chosen by me**; the Boss can switch it before going public.
10. **Upload private.** He reads the card and flips it public himself.

## Review outcome (06-Nov-2026, four independent reviewers, each finding re-checked by a skeptic)

Reviewers: cold reader, fact checker, leakage hunter, Kaggle-readiness checker (26 agents in
all). What was confirmed and fixed:

- **Blocker: a plain `kaggle datasets create` skips the `images/` folder** (the CLI's default
  directory mode is "skip") and would have made a private dataset with no pictures. Fixed by
  uploading with `--dir-mode zip` and `TMPDIR` on the Team 4TB drive (the zip is built in the
  temp folder; the default is the internal disk). A 20-picture throwaway private upload proved
  Kaggle unpacks the zip into `images/YYYY-MM/<frame_id>.jpg`, then it was deleted. The same
  rehearsal showed `birds` is not a Kaggle tag; dropped.
- **Card facts:** `expression_score` runs 10 to 30, not 0 to 25; the phone was replaced on
  10 Aug (not "one camera"); the earliest era is not one regime (lowest allowed score 70 to 65
  on 7 Aug, formula rewritten 8 Aug), so both days are now flagged `rollout_day`; every
  vision-model column drifts between eras, not just the score; the reaction was to the post
  (picture, caption, visible score, and a direct ping at score 87+); `standout_bird` is blank
  into part of 21 Sep.
- **Table:** `bird_face_visible` dropped (constant 1); a build-time check now fails on any
  constant shipped column; eras renamed (`pre_floor_80` reads backwards, now
  `early_older_rules`); fill statuses renamed to say what happened; 8 captions where the model
  echoed its own instructions or slipped into another script are blanked; the baseline table
  shows blank fill read both as zero and as the model's guess.
- **Checked and cleared:** no column or file property leaks the label (capture-to-post lag,
  frame id order, caption text, file properties all hold up); no personal data in captions;
  split days disjoint and every era in every fold; all 7,520 pictures open, 1080 x 1920, no
  metadata, no duplicates.
- **Found and kept as a stated caveat, not fixed:** reactions cluster in time (the farmer's
  attention varies by day), so the effective sample is nearer a thousand than 7,520; the card
  says so and recommends grouped-by-day validation.
- Not acted on: the cold reader asked for within-day baselines and a test-split baseline; the
  skeptics showed within-day and pooled numbers differ by 0.003 to 0.014 in seven of eight
  cells, so the card gives the bootstrap interval widths in words instead.

## Architecture

- `scripts/kaggle-s7-package.py` (new, one responsibility: table + split + package). Reads
  `s7_posts_labelled.csv` and `fill_measured.csv` from the SSD folder, writes the package under
  `/Volumes/Team 4TB/kaggle/s7-reaction-20261105/kaggle-package/`, then re-reads it from disk
  and checks it (the verification lives in the same script so a build can never skip it).
- Card: `docs/06-Nov-2026-kaggle-s7-dataset-card.md` is the source (versioned in git, with
  count placeholders the script fills). The script writes it out as the package `README.md` and
  as the Kaggle description. A critic pass asks what a stranger would get wrong.
- Everything big stays on the Team 4TB drive (Boss directive), nothing on the internal disk.

## TODOs

1. Plan (this file).
2. Packaging script: table, split, images (clone-copy on the same volume, EXIF check),
   metadata, card, verification.
3. Dataset card.
4. Build the package; read the verification output.
5. Critic pass over the card (workflow: independent readers who have never seen this repo).
6. Private upload; verify on Kaggle that file count and size match.
7. CHANGELOG is **not** touched: no behaviour of the farm changes. Findings doc and memory note
   get a pointer to the dataset.

## Docs / changelog touchpoints

New: this plan, the two scripts, the card. Updated: `docs/05-Nov-2026-s7-reaction-findings.md`
(pointer), the memory note for this dataset.
