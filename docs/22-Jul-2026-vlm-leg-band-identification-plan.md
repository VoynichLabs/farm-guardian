# 22-Jul-2026 — Teach the VLM prompt to use the new leg bands

## Why

Boss banded the flock ~2026-07-21. `farm-2026/content/flock-profiles.json` now
carries a structured `leg_band` per bird (`color`, `number`, `side`,
`confirmed`). 8 of the 11 living named (ornitharch) birds have a **confirmed
left-leg band** with a unique `color+number` key:

| bird | band | | bird | band |
|---|---|---|---|---|
| Birddor | yellow #1 L | | Ingebird | green #2 L |
| Birdadotta | orange #10 L | | Henriessa | pink #8 L |
| Birdsilla | white #3 L | | Henridotta | purple #12 L |
| Birdimir | red #3 L | | Adelbird | blue #7 L |

The VLM prompt (`prompt.md` + the `{named_individuals_block}` that `roster.py`
renders) still identifies birds by **plumage only**. Several of these birds are
described in the current prompt as *"near mirror images"* / *"near-identical"*
(Birdimir↔Ingebird, Henridotta↔Adelbird, Adelbird↔Ingebird) — the exact
ambiguity that got structured named-bird classification disabled in v2.38.2. A
legible colored band resolves it cleanly. Bands are the reliable discriminator
plumage never was.

## Scope

**In:**
- `roster.py::format_named_individuals_block()` — append each named bird's
  confirmed band (`color`, `number`, `side`) to its line, so the VLM has a
  reference to match against.
- `prompt.md` — add a short **Leg bands** guidance block and a one-line caption
  note. Keep identification **soft/caption-level** (the v2.38.2 boundary): the
  VLM may *say* "likely Ingebird" and *describe* the band; it is never forced to.

**Out (deliberately, to stay surgical):**
- **No `schema.json` change.** A structured band-observation field
  (`band_color`/`band_number`) is the "right" long-term design — VLM *observes*,
  Python *classifies* — but it's grammar-sampled on every frame, touches
  `store.py`/the DB, and per CLAUDE.md must be coordinated with farm-2026 TS
  types. Its payoff is also camera-limited (see Payoff). **Named follow-up, not
  this change.**
- No change to the `format_named_individuals_block` hedge-gate (all 11 birds
  render today) or `bird_photo_ingest._silver_sanity_note`. Follow-ups if wanted
  — the band would make a better sanity cross-check than `silver_marking`.

## The dominant risk: confabulation

This is v2.38.2 wearing a new hat. If the prompt says "Birdimir = red #3,
Ingebird = green #2" and the VLM sees Ingebird-ish plumage but **can't actually
read the leg**, it may hallucinate the expected band to justify the ID. The
guard against that is the whole point of the edit. `prompt.md` will state, hard:

- Only mention a band you can **actually see on the leg in THIS frame**. Never
  infer a band from plumage. Leg hidden / too far / too blurry → say **nothing**
  about a band; fall back to a generic label. Do not guess.
- Read **color AND number** — band colors look alike small and dim (red/orange/
  pink, green/blue); the number disambiguates. (Live trap: `orange #1` on the
  **right** leg is *Robirda*, a non-named pullet — not in the named list.)
- A clearly legible band **outranks plumage**; a band you can't read changes
  nothing.

Each bird's `side` goes in its line (confirmatory only) — the prompt will **not**
teach a left/right semantic, because `color+number` is already unique.

## Payoff (calibrated — don't over-promise)

Bands were confirmed from **handheld iPhone close-ups** (the IMG_77xx Discord
drops). The per-cycle Guardian diet is distant overhead coop views of backs and
rumps where legs are rarely legible — expect little there. The real wins:
1. **`bird_photo_ingest.py` handheld path** — `prompt.md` is shared, so this one
   edit already covers it. Confident naming of banded birds in Discord drops.
2. **Richer captions** — "purple leg band #12 visible" when a band is legible,
   matching the human-written `photos[].caption` style already in the roster.

## Content decision to confirm with Boss

Should the VLM **name** by band ("likely Ingebird") *and* **describe** it in
captions — or **describe only**? Recommendation: **both**, with naming staying
soft ("you may say likely X"), identical to today's plumage guidance. This is
the one content choice; everything else is mechanical.

## TODOs (ordered)

1. `roster.py` — add a `_format_band(leg_band)` helper (guards `confirmed`,
   null `number`); append its clause to each rendered line. Update file header.
2. `prompt.md` — add the **Leg bands** guidance block (anti-confabulation rules
   above) after the named-individuals section; add the one-line band note to the
   `caption_draft` field guidance.
3. **Verify — two-sided, against real LM Studio (the gate; a rendered-block diff
   is not enough):**
   - **Positive:** run `vlm_enricher` on committed known-band portraits —
     `IMG_7713` (Henridotta purple #12), plus Ingebird green #2 / Henriessa pink
     #8 drops — confirm it reads the band and names correctly.
   - **Negative:** run on a real distant Guardian frame with no legible leg band
     — confirm it does **not** invent a band. This is what tells us the edit
     helps rather than manufacturing confident wrong IDs.
4. `CHANGELOG.md` — top entry (proposed **v2.49.0**; band-aware soft ID).
5. Commit + push (both repos untouched except this repo's 3 files; roster data
   already lives in farm-2026 and needs no edit).

## Docs / changelog touchpoints

- `CHANGELOG.md` — new top entry.
- This plan doc.
- `roster.py` header — note the band clause.
- No CLAUDE.md change needed (the v2.38.2 note still governs; this respects it).
