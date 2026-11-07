# 07-Nov-2026 — Raise the Kaggle usability score, count the people who reacted, say the text is unreliable

Author: Claude Sonnet 5.5 Medium

Follow-up to `docs/06-Nov-2026-kaggle-s7-dataset-publish-plan.md` (private upload done). The Boss
asked on 07-Nov-2026 for four things, and added a fifth while the work was under way.

## What the Boss asked

1. **Usability score to 10.** Kaggle shows 6.47 (11 of 17 checks). Its Pending Actions list three
   open items: file descriptions, column descriptors, provenance ("in the metadata tab").
2. **Reactions: anybody in the Discord can react.** Mostly him, sometimes others; some posts got two
   or three. Use that if it is worth anything.
3. **Where the pictures came from belongs in the metadata tab**, not only in the card.
4. **He already added a better cover picture** on Kaggle. Do not lose it.
5. **The written descriptions are often flat wrong, and the card does not say so.** A bird's back end
   described as looking at the camera; a chicken called a turkey. So the text is not reliable.

## What was found (07-Nov-2026)

- Kaggle score 6.47. Metadata fields the CLI can set: title, subtitle, description, private flag,
  tags, license, collaborators, per-file description and per-column description (`data`),
  `userSpecifiedSources` (the Provenance "Sources" box), `expectedUpdateFrequency` and `image`.
  There is no field for "collection methodology".
- `datasets version` only carries subtitle, description, tags and the files. Everything else needs
  `datasets metadata --update`, which sends the whole settings object, so it must be checked that it
  does not drop his cover picture. A backup of the cover is on the SSD (`cover-backup/`).
- **Reaction people count already exists locally.** `image_archive.discord_reactions` is written by the
  reaction sync as the number of DISTINCT non-bot human reactors across all emoji (see
  `_count_human_reactions` in `scripts/discord-reaction-sync.py`). For all 7,520 shipped frames it
  agrees exactly with `reacted` (0 means no reaction; 1,259 frames with one person, 82 with two, 1
  with three). No new Discord reads are needed, which keeps the standing rule against reactor
  look-ups. Caveat to state: the sync only re-checks recent posts, so a late reaction to an old post
  may be missing.
- **The card's account of the posting gate is wrong in one place:** it says the gate checked that "a
  bird's face is visible". That is the vision model's own claim, and the Boss says it is often wrong
  (rear views passed). Reword it.

## Scope

**In:**
- New column `reaction_people` (0 to 3), from the archive count, with a consistency check against
  `reacted` in the build.
- Card rewrite: a plain "the text and most model columns are often wrong" warning near the top, with a
  measured rate from a spot check (below) and the two named failure kinds (facing-the-camera claims
  that are rear or side views; species mixed up); who reacts (anyone, usually the owner; count of
  people); posting gate described correctly; provenance moved into the metadata Sources box with the
  card keeping a short pointer.
- Kaggle metadata: description of every file, every column, Sources text, update frequency, then a
  new dataset version (for the new column) followed by a metadata update.
- A spot check of captions against pictures: a few hundred randomly chosen frames across the four eras,
  each judged by two independent readers, answering only what a picture can settle (is the bird facing
  the lens, is it a chicken or a turkey, does the bird count hold). Reported as an informal check by
  AI readers, not a human audit, with the disagreement rate shown.
- Independent critic pass on the finished card and package before upload.

**Out:** making the dataset public (still the Boss's click); dropping frames for content (he said no
picture screening); any change to posting, scoring, the pipeline or Discord; a Kaggle notebook (see
the open item at the end).

## Architecture

- `scripts/kaggle-s7-package.py` stays the one builder. It gains: `reaction_people` read from the
  archive (read-only), the new schema line, file descriptions, the Sources text, the update frequency,
  and a metadata file in the form `datasets metadata --update` accepts (`data`, `userSpecifiedSources`,
  `expectedUpdateFrequency`, `licenses`, `keywords`).
- Card source `docs/06-Nov-2026-kaggle-s7-dataset-card.md` is edited in place; the provenance wording
  lives once in a new source file `docs/07-Nov-2026-kaggle-s7-dataset-provenance.md` that the script
  puts into the Sources box and, in short form, into the card.
- Spot-check results go to the SSD folder and a summary file in `docs/`; the card quotes the summary.

## TODOs

1. This plan. 2. Advisor check. 3. Spot check (workflow). 4. Script and card changes. 5. Rebuild and
verify (the build already re-reads the package). 6. Critic pass (workflow). 7. `datasets version` with
`--dir-mode zip` and `TMPDIR` on the Team 4TB drive, then `datasets metadata --update`; confirm the
cover picture, tags and license survive. 8. Re-read the score on the Kaggle page and the live listing.
9. Commit and push.

## Docs and changelog

New: this plan, the provenance source, the spot-check summary. Updated: the card, the 06-Nov publish
plan status block, the findings doc pointer, the memory note. CHANGELOG is not touched: nothing about
the farm's behaviour changes.

## Open item

Kaggle also rewards a public notebook that uses the dataset. A notebook cannot be public before the
dataset is, so that point is left until the Boss makes the dataset public. Reported at the end.

## Results so far (07-Nov-2026, Claude Sonnet 5.5 Medium)

**Spot check** (160 frames, 40 per era, seed 20261107; two AI readers per question, a third on splits; raw
output in `/Volumes/Team 4TB/kaggle/s7-reaction-20261105/spot-check/`):
- 140 captions claim a bird looks at the camera; for 96 (69%) the readers found it untrue. 112 of 160
  (70%) had at least one contradicted claim; 5 named a turkey with none visible; 17 had another claim
  contradicted. Eras: 48%, 76%, 74%, 71% of eye-contact claims wrong (earliest to newest).
- Blind orientation of the main bird: facing the lens in 28 of 160 (18%), three-quarter 42, side profile 56,
  head down 13, from behind 10, hidden or blurred 11. Readers agreed on 130 of 160.
- Reactions: facing-lens frames 7 of 28 reacted, others 22 of 132 (Fisher p about 0.29): no clear link.
- Ships as `caption_spot_check.csv` and as the card's honesty section.

**Kaggle rehearsal** (throwaway private dataset `markbarney/s7-rehearsal-scratch-07nov`, to be deleted):
- Score arithmetic: 17 checks, 6.47 = 11 of 17 on the real dataset.
- `datasets version` with `resources` (file description plus column schema) stores FILE descriptions
  (the "Add file information" item cleared: 4.71 to 8.82) but does NOT store column descriptions, with or
  without column types. Typing the column descriptions into the page's file editor does store them and
  cleared "Include column descriptors": 9.41.
- `datasets metadata --update` sets sources, update frequency, license and cover; it needs the long
  license name ("Attribution 4.0 International (CC BY 4.0)"), and sends is_private=False when
  `isPrivate` is missing (it would publish): the update file now always carries `isPrivate: true`, and
  the build fails if it does not. An update without an `image` field leaves the cover picture alone
  (cover URL unchanged after a second update and after new versions).
- The remaining point(s) need a public notebook and a public dataset ("Publish a notebook", "Make Public"
  on the page's pending list): not reachable while private.
- Collection Methodology has no command-line field; it is pasted into the page.

**Order for the real dataset:** `datasets version` (files, subtitle, description, tags, file notes) ->
`datasets metadata --update` (sources, frequency, license, private) -> type column notes and Collection
Methodology into the page. A later new version would drop the typed column notes; redo them.
