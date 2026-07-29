# 28-Jul-2026 — Using the leg bands to actually identify birds

**Status: BUILT AND SHIPPED, 28-Jul-2026 (v2.55.0).** Boss: "I want you to fix
everything. Be thorough." Measured outcomes are in the section at the bottom —
**read that before changing any of this**, because two of the plan's own
assumptions turned out to be wrong when tested against the live model.

Question 1 (should the AI name birds from bands?) was answered **yes, softly** —
it resolves a name only when the reading can belong to exactly one living bird.
Question 2 (`config/flock_bands.json`) was answered by **making it load-bearing**
as the offline fallback rather than deleting it.

Alongside this, a bigger problem surfaced that the plan below does not mention at
all: **the plumage half of the AI's cheat sheet described chicks.** Six of the
eleven named birds still carried their hatch-day down in the field the prompt
reads on every frame. That is fixed in farm-2026 v1.35.0, and the hands-on
follow-ups are in [`29-Jul-2026-hands-on-bird-check-list.md`](29-Jul-2026-hands-on-bird-check-list.md).

---

## What you asked

Look at how the AI judges the birds and writes the descriptions, look at the band list
and at what an "ornitharch" is, and work out whether we'd get better results by telling
the AI outright which bird is which.

Short answer: **we already tried that on 22-Jul, and I can now measure that it didn't
work.** Six days of real data say the current approach produces essentially zero correct
identifications and a steady trickle of *wrong* band claims that are going out on
Instagram and Facebook. The fix isn't to tell the AI more — it's to stop asking the AI
to do the part it's bad at.

---

## Part 1 — How it works today

Three separate places where an AI looks at birds:

**1. The per-frame judge.** Every captured frame goes to the vision model on this Mac
(`qwen3-vl-4b`, in LM Studio). It fills in a fixed form: how many birds, what they're
doing, how sharp it is, a 0–100 "is this a gem" score, and a written caption. That form
is `tools/pipeline/schema.json`; the instructions are `tools/pipeline/prompt.md`. This is
what feeds Discord and every reel.

**2. The named-bird cheat sheet.** Inside those instructions is a slot that gets filled
at runtime by `tools/pipeline/roster.py`, from the bird roster over in the farm-2026
repo. It currently renders 11 birds with their colouring, breed and band — about 3,700
characters of text prepended to every single frame's instructions.

**3. The reel caption writer.** A second, separate AI call in
`tools/pipeline/daily_reel_runner.py` writes the actual Instagram caption for a reel. It
gets a *different* and much thinner bird list — names and colours only, no bands.

Plus a fourth path: when you drop an iPhone photo in Discord, `bird_photo_ingest.py`
runs the same instructions and, if the caption names a bird, files that photo into that
bird's profile. That's the one place an AI-written name becomes permanent.

---

## Part 2 — Ornitharchs and the bands

An **ornitharch** is a bird hatched here on the farm and named individually — as opposed
to a bird bought in, or one of the anonymous group entries like "Cackle Hatchery cohort
(15)". There are 11 living ornitharchs. It's a flag in the roster file, and it's what
drives the "Class of 2026" tiles on the website.

The banding system encodes exactly that distinction:

- **Left leg = hatched here (ornitharch). Right leg = bought.**

Which is why the leg is genuinely part of the ID and not a footnote — **orange** and
**white** each appear on *both* legs on different birds:

| colour + leg | bird | | colour + leg | bird |
|---|---|---|---|---|
| yellow left | Birddor #1 **and** Horstabird #2 | | pink left | Henriella #3 **and** Henriessa #8 |
| orange left | Birdadotta #10 | | orange right | Robirda #1 |
| white left | Birdsilla #3 | | white right | Bobirda #6 |
| red left | Birdimir #3 | | green left | Ingebird #2 |
| purple left | Henridotta #12 | | blue left | Adelbird #7 |

12 birds banded. **8 of the 12 are uniquely identified by colour + leg alone — no number
needed.** Only yellow-left and pink-left genuinely need the number to separate two birds.

**10 of the 11 living ornitharchs are banded — only Birdthazar has none.** (The other two
banded birds, Robirda and Bobirda, were bought in, hence their right-leg bands.) Worth
noting the 22-Jul plan said "8 of the 11" six days ago; Henriella was confirmed on the
23rd and Horstabird has since been added. The roster moves — which is the argument for
Part 5's point about four copies of this table drifting apart.

---

## Part 3 — What the last six days actually show

I pulled every s7 caption since the band feature went live on 22-Jul. **17,617 frames.**

| | |
|---|---|
| Frames where the AI claimed to see a band | **440** (all of them on the S7 — no other camera is close enough) |
| …that included the band's **number** | **18 (4%)** |
| …that included which **leg** | 171 (39%) |
| …with colour + number + leg together — what the instructions demand before naming a bird | **15 (3%)** |
| Birds actually named in six days | **3** |
| …and all three were **Birdthazar — the bird with no band**, recognised by his back stripe |

**Not one bird has ever been identified from its band.**

And the reads it does make are unreliable. Of the 19 numbered band readings, **5 were
combinations that don't exist on any bird** — "green #1", "pink #1", "purple #1". The
colour tallies are worse: "yellow band" was claimed **127 times** and "purple" 98 times,
when there are exactly two yellow bands and one purple band in the whole flock. It is
reading leg scales, mulch and shadow as bands.

Some of those wrong claims have already been published. Example, 22-Jul:
*"A large orange-brown chicken with a blue leg band…"* — the only blue band belongs to
Adelbird, who is dark grey/black.

---

## Part 4 — Why it's failing

The instructions ask a small, fast vision model to do two hard jobs at once: **read a
1 cm plastic ring on a chicken's leg from across the run**, then **look it up in an
11-row table in its head** — and then gate the whole thing on the number, which is the
one part it can almost never make out (4% of the time).

So the number blocks every identification, while nothing at all checks the colour. That's
exactly backwards. The model is *forbidden* from using the information it can actually
get (colour + leg = 8 of 12 birds) and *free* to publish the information it's guessing at.

Nothing validates the output either. "Green #1" isn't a misread we catch — it just gets
written into a caption and posted.

---

## Part 5 — The fix

**One idea: the AI reports what it sees, the Mac decides who it is.**

The model should never hold a table of birds in its head. It should answer one narrow
question — *"is there a band on a leg in this picture, and if so what colour, what leg,
what number if you can read it?"* — and plain Python code does the lookup, because the
lookup is a dozen fixed rows that never change and code gets it right 100% of the time.

### Phase 1 — Stop the wrong claims (safe, no naming, do this first)

Add three fields to the form the model fills in: band colour, band leg, band number —
each with an explicit "none / can't tell" answer. Then code checks the answer against
the real band list and **throws away anything impossible**, so "green #1" never reaches a
caption. The caption text gets rebuilt from the verified fact rather than the model's
prose.

**Validate on colour + leg, not just colour + number** — this is where most of the value
is. There are 8 band colours × 2 legs = 16 possible combinations and only 10 of them
exist, so blue-right, green-right, pink-right, purple-right, red-right and yellow-right
are all provably wrong reads. That matters because **39% of the model's claims already
name a leg, against only 4% that name a number** — so checking the leg catches roughly
ten times as many bad claims as checking the number would.

This can only ever *remove* a false claim. It cannot invent an identification. It needs
no database change (the readings ride along in the `vlm_json` blob we already store) and
no coordination with the website.

### Phase 2 — Actually name birds (only if you want it, see questions)

Once Phase 1 is measuring how good the colour reads are, switch the matching rule from
"colour + number + leg" to **"colour + leg, and only when that pair belongs to exactly
one bird."** That instantly makes 8 of the 12 birds identifiable. Yellow-left and
pink-left stay anonymous unless the number is legible — correctly so, since those are
two birds each.

**Calibrated expectation, not a promise:** yellow and pink are ~43% of everything the
model currently claims to see, and those are precisely the two ambiguous colours. So this
does not turn into a flood of named birds. The realistic win is a handful of confident,
*correct* IDs a week on the S7 — plus a much bigger win on the iPhone photos you drop in
Discord, where the bands are actually legible and the same instructions apply.

### Phase 3 — Small cleanups worth doing regardless

- **The reel caption writer is cut off before the ornitharchs.** It takes the first 14
  living birds in file order and stops. I checked what survives that cut: **9 of the 11
  named farm-hatched birds — Birdthazar, Henriella, Birdsilla, Birdimir, Ingebird,
  Henriessa, Horstabird, Henridotta, Adelbird — never reach the caption writer at all.**
  Only Birddor and Birdadotta make it. Fix is to sort the named birds to the front, not
  to raise the limit (raising it just moves the cliff as the flock grows).
- **The band table now exists in four places** and they've already drifted apart:
  the roster in farm-2026 (the real one), the new `config/flock_bands.json` here, the
  website's `/flock/banding` page (which still shows Henridotta's leg as *unconfirmed*
  and is missing Henriella entirely), and the text we render into the AI instructions.
  See question 2.

---

## Part 6 — Two questions only you can answer

**1. Do you want the AI to name birds from bands at all — or just describe the band?**
The 22-Jul plan raised this and I can't find an answer anywhere. "Describe only" is the
safest option and Phase 1 is worth doing either way. Phase 2 is the part that hangs on
this. My recommendation: yes to naming, but only under the colour+leg-is-unique rule
above, and only ever as "likely Ingebird" — never stated as certain.

**2. `config/flock_bands.json` — you committed it today and nothing reads it.**
Two readings, and they're materially different jobs:
 - **(a) It's a redundant copy — delete it.** The roster in farm-2026 is already the
   source of truth and the code already reads it. *My recommendation.*
 - **(b) You want farm-guardian to own the band table locally.** Doable, but it flips
   the current direction — the farm and the website would then need syncing, and the
   website page has already drifted once.

Note either way: that file uses the key `leg` where the roster uses `side`. If anyone
wires it in as-is, the leg silently comes through as blank — which would quietly break
the exact thing that makes bands work.

---

## Scope

**In:** `schema.json` (three observation fields), `prompt.md` (simplify — the model no
longer does matching, so the "match all three against the list" instructions come *out*),
`roster.py` (the lookup/validation function), `daily_reel_runner.py` (roster ordering).

**Out:** No database migration. No change to the website's TypeScript types. No change to
how gems are scored or which frames get posted. No new AI model, no hosted API.

---

## The thing most likely to go wrong

`prompt.md` is ~116 lines of carefully tuned scoring instructions on a small model, and
the bird cheat sheet is already 3,700 characters of it. **Editing it can silently shift
the gem scoring** — which decides what reaches Discord and every reel. So the verification
below includes a before/after on the scores themselves, not just on band reading.

Baseline recorded today, S7, 22-Jul onward, 17,617 frames:

| | |
|---|---|
| skip / decent / strong | 91.9% / 6.1% / 2.0% |
| average score | 21.9 (median 17, p90 49) |
| frames scoring 80+ (the Discord gem bar) | 1.10% |
| time per frame | 5,586 ms average (median 5,623, p90 6,149) |

If those move meaningfully after the edit, the change is wrong even if band reading
improved. Working in our favour: moving the matching into code lets the instructions get
*shorter*, which may well help the scoring rather than hurt it.

**Watch the timing row too.** Three new fields means the model writes more on every
frame, and Guardian's night predator-alert check shares the same LM Studio with the
pipeline, one call at a time. A slower pipeline call means the alert check waits longer
for its turn — so a big jump in time-per-frame is a reason to stop, even if everything
else improved.

---

## Steps, in order

1. Confirm the two questions in Part 6.
2. `schema.json` — add band colour / leg / number, each with an explicit "none" value.
3. `roster.py` — a `resolve_band()` function: given colour+leg+number, return the one
   bird it can only be, or nothing. Rejects impossible combinations.
4. `prompt.md` — cut the "match against the list" instructions; keep and strengthen the
   "only report a band you can actually SEE" rule. Update the caption guidance.
5. `daily_reel_runner.py` — sort named farm-hatched birds first.
6. **Verify, three ways — this is the gate, a code review is not enough:**
   - **Positive:** rerun on the iPhone close-ups where we know the answer (IMG_7713 =
     Henridotta purple #12, Ingebird green #2, Henriessa pink #8). It should read them.
   - **Negative, at scale:** rerun over a few hundred stored distant S7 frames. Any
     colour+leg or colour+number pair that doesn't exist is definitionally a false read,
     so this gives a real before/after accuracy number for free — no human labelling
     needed. Score it on colour+**leg** primarily (39% coverage), not colour+number (4%).
   - **Regression:** re-check the score table above, **including time per frame**. Neither
     must move.
7. `CHANGELOG.md` top entry (proposed **v2.55.0**).
8. Commit and push.

## Docs to update

- `CHANGELOG.md` — new top entry.
- This plan doc — mark it done, record the measured before/after.
- `roster.py` header.
- `docs/22-Jul-2026-vlm-leg-band-identification-plan.md` — add a closing note that the
  soft/prose approach was measured and superseded, so nobody re-tries it.
- `/flock/banding` page in farm-2026 — only if you pick option (b) in question 2.

---

## For the next agent: this is NOT a v2.38.2 violation

v2.38.2 removed named-bird classification because the model was **predicting names from
plumage** and getting them wrong. That ban still stands and this plan does not touch it.

What's proposed here is different in kind: the model reports a **physical object it can
see** (a coloured ring), and ordinary Python code does a fixed-table lookup. The model
never holds a list of birds. An impossible reading is *rejected* rather than published.
That is strictly safer than what runs today, where the model both guesses and publishes
with nothing checking it.

Do not "restore" the prose-matching approach on the strength of the 22-Jul plan doc.
It ran for six days, produced 0 identifications from 440 band sightings, and published
at least 5 bands that don't exist.

---

# What actually happened when it was built (28-Jul-2026)

Two of this plan's assumptions were wrong. Both were caught by measuring against
the live model rather than reasoning about it, and both are recorded here so
nobody re-derives them.

## Wrong assumption 1 — "colour + leg resolves 8 of 12 birds"

The plan proposed matching on **colour + leg**, on the arithmetic that 8 of the
12 banded birds are unique on that pair and that the leg was legible in 39% of
sightings versus 4% for the number.

**The model cannot tell a bird's left leg from its right.** Measured on the six
handheld banding portraits where the band is plainly legible: it read the colour
correctly 5 times out of 6 and three numbers exactly — and answered **"right" for
five birds that all wear their band on the left**. (It did read Robirda's genuine
right leg correctly, so it is not inverted, just unreliable.)

The first implementation let a contradicting leg veto the match. That single rule
took a run that should have identified four birds down to **zero**.

**`resolve_band()` now ignores the leg entirely** — and does not need it, because
**colour + number is unique across all twelve banded birds with no collisions**,
and colour alone is unique for green, red, purple and blue. The leg is still
recorded in `band_leg` so a future model can be re-measured. ⚠️ Do not
"improve" the matcher by filtering on the leg.

Live result on the six portraits after the fix: **4 of 6 resolved to the correct
bird, 0 wrong.** The two misses are correct refusals — Henriella's number was
illegible and pink is shared with Henriessa; Horstabird's band was not visible.

## Wrong assumption 2 — "prompt.md can stop the model mentioning bands in captions"

The plan assumed that telling the model to keep bands out of `caption_draft`
would be enough. Measured over 100 real frames **after** that instruction was
added: **5 still put a band in the caption.** A prose claim we never parsed is a
claim we cannot check.

`_strip_band_clauses()` in `vlm_enricher.py` now removes band mentions from
`caption_draft` and `share_reason` deterministically. Verified against all **797**
real band-mentioning captions in the archive: **0 leaks, 0 captions emptied**,
mean length 253 → 166 chars.

## A measurement trap worth not falling into twice

The first regression run looked catastrophic — `strong` at 45% against a 2.0%
baseline, mean score 21.9 → 59.3. **It was a sampling artefact.** Only frames
scored `decent` or `strong` keep a JPEG on disk, so *any* sample drawn from the
archive by `image_path` is pre-filtered to good frames and cannot be compared
against an all-frames baseline. The correct instrument is a **paired** test —
same frames, old prompt versus new — which is immune to that bias.

Likewise, **inference timing cannot be measured while the pipeline is running.**
A test harness reported 14,216 ms against a 5,586 ms baseline; the live pipeline
was simultaneously logging 5,628 ms for the same model. Two inference streams,
one LM Studio. Paired, under identical contention, the schema change costs
**+3%**.

And the paired test must score the **recomputed** gem number, not the VLM's
`overall_score` — `orchestrator._compute_overall_score` discards the model's
guess and rebuilds the score from `largest_subject_pct`, `expression_score`,
`detail_score` and `image_quality`/`lighting`. Comparing the discarded field
measures nothing that reaches Discord.

## Prompt-length guard

The rewritten plumage descriptions are richer, which took the rendered
named-individuals block from 3,244 to 6,031 characters — a real risk to gem
calibration on a 4B model. `roster._lead()` trims to leading **whole sentences**
(240 chars), landing the block at **3,498** — under the 3,676 it was before any
of today's changes. Every rewritten description leads with the bird's single
most discriminating feature, so the trim costs nothing.

## What an adversarial review pass caught before commit

Worth recording because the failure mode is subtle: the first draft of the
rewritten descriptions **described Henridotta and Adelbird in near-identical
words** — the two entries had collapsed onto one bird — and claimed superlatives
("the only truly black-headed bird", "by far the largest comb") that no single
photograph supports. Corrected before shipping. The honest finding is that
**Henridotta and Adelbird are not separable by plumage**, and both entries now
say so instead of offering a tell that does not work.
