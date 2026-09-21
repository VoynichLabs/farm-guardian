# 21-Sep-2026 — VLM form cleanup, temperature, and flock-registry refresh

Author: Claude Opus 5 · Requested by Boss 21-Sep-2026

## Why

Boss: the VLM output is "talking about chicks and special chicks. That's from months ago.
Everybody's all grown up now." And: "0.2 is just so boring for the temperature."

Measured before changing anything (s7-cam, 07–21 Sep 2026, 35,077 enriched frames):

- Only 10 captions said "chick", so the captions are mostly clean already. The chick
  framing comes from **the form the model fills in** and **the flock registry**:
  - `schema.json` forces three chick-era answers on every frame: `any_special_chick`,
    `individuals_visible ∈ {adult, chick, unknown-bird}`, and `apparent_age_days`. The model
    tops `apparent_age_days` out near its 365 maximum (mean 352), so it carries no information.
  - farm-2026 renders `any_special_chick` as a **"special chick" badge** on gem cards and turns
    `apparent_age_days` into a made-up age row ("~11 months old").
  - The named-bird descriptions fed into the prompt (`roster.format_named_individuals_block`)
    are stale. Birdimir's current description literally says "baby down still visible around
    the eyes… molting out of chick fuzz". Several are dated 23-Jun, when the birds were
    about 5 weeks old.
- Temperature is `vlm_temperature: 0.2` in `tools/pipeline/config.json`.

## Scope

**In**
1. `vlm_temperature` 0.2 → **0.5** (Boss's call, for variety). Grammar sampling still enforces
   every enum and range, so nothing can go out of bounds; the thing that could feel different is
   how many frames clear the gem gate (`overall_score >= 80`). Spot-check after.
2. The form (`schema.json`, `prompt.md`):
   - `any_special_chick` → **`standout_bird`** (a bird that stands out: striking colour, a
     character, a great pose).
   - `apparent_age_days` → **removed**.
   - `individuals_visible` enum → `hen`, `rooster`, `turkey`, `unknown-bird` (drop `chick`
     and the always-true `adult`).
   - The prompt gets a flock-age line **generated from the roster** (youngest living bird's hatch
     date) so it can't go stale the way the chick wording did. The `incubating` list is not
     used: eggs aren't on camera, and a new hatch is added to `flock_birds` with its date.
3. Storage/API keep their shape (no DB migration):
   - DB column `any_special_chick` stays and stores `standout_bird` (documented at the insert
     site). Renaming a SQLite column declared in two places across 2.8M rows buys nothing.
   - DB column `apparent_age_days` stays and is written NULL from now on.
   - The public images API keeps emitting `any_special_chick` (same value) and now always emits
     `apparent_age_days: null`, so the website's made-up age row disappears for old rows too,
     without a frontend deploy. It also emits `standout_bird` for the frontend to move to.
4. farm-2026 frontend: badge text "special chick" → "standout". **Edited, not committed/pushed**:
   farm-2026 deploys to Railway on push, and has uncommitted diary files that must not ride along.
5. Flock registry (`farm-2026/content/flock-profiles.json`): new dated `color_observations`
   entries **only for birds with September photos whose band identifies them**:
   Birdimir, Adelbird, Ingebird, Henriessa, Henridot, Birdthazar, Birdsilla. The history is
   append-only: old entries are kept, and `color_description` mirrors the newest entry
   (enforced by `tools/pipeline/validate_flock_profiles.py`).

**Out**
- Birdadotta, Horstabird, Henriello, Robirda, Bobirda: no photo newer than July/August.
  Writing adult descriptions for them without a photo would be fabrication. They need photos.
- Using `band_bird` to name birds in captions/Discord (the other big recognition win). Separate
  change, proposed to Boss.
- `caption_brand.py`'s "chick"/"baby" blocklist stays as a safety net until it's measured idle.
- `scene: "brooder"` hardcoded in `scripts/discord-reaction-sync.py` for reaction-synced rows:
  flagged, not changed.

## Touch points

| File | Change |
|---|---|
| `tools/pipeline/config.json` | `vlm_temperature` 0.5 |
| `tools/pipeline/schema.json` | field rename/removal, enum |
| `tools/pipeline/prompt.md` | grown-flock wording, `{flock_age_line}`, new field text |
| `tools/pipeline/roster.py` | `format_flock_age_line()` |
| `tools/pipeline/vlm_enricher.py` | substitute `{flock_age_line}` |
| `tools/pipeline/store.py` | map `standout_bird` → `any_special_chick` column, NULL age |
| `database.py` | API row: `standout_bird`, age always null |
| `images_api.py` | emit `standout_bird` |
| `tools/pipeline/daily_reel_runner.py` | reel-caption fact reworded to "a few birds really stood out" |
| `scripts/discord-reaction-sync.py` | vlm_json key `standout_bird` |
| `tools/pipeline/config.json` s7-cam `context` | dropped "bantams" and the chick sentence |
| test fixtures (`test_gem_poster_gate.py`, `test_floor_pecking_calibration.py`) | new field names |
| farm-2026 `GemCardBadges.tsx`, `types.ts` | badge label, optional `standout_bird` |
| farm-2026 `content/flock-profiles.json` | 7 new observations |

## Notes for the next person

- `individuals_visible_csv` holds one tag per visible bird, repeats included (`hen,hen,rooster`),
  as it did before with `adult,adult`. Read it as a list, not a set of distinct tags. The
  `',csv,' LIKE '%,hen,%'` filter in `database.py` works either way.
- Gem-gate baseline for judging the 0.5 temperature: earlier on 21-Sep (14:00–17:50Z, temp 0.2),
  89 of 1,045 s7 frames (8.5%) scored `overall_score >= 80`, and the average was 32.7. Compare a few
  hours of post-change frames against that before deciding whether 0.5 is starving the gem lane.
- `validate_flock_profiles.py` used to exit 1 on Malt Liquor, Hawk Food, White Rooster and Loud Dumb Bird
  (a shortened `color_description` that didn't match its 7-Sep observation). Restored to the full text; it now passes.

## Findings to raise with Boss (not resolved here)

- **Ingebird (green #2) — RESOLVED by Boss 21-Sep:** the big-combed green-band bird of 21-Sep 11:48 wears its band on the RIGHT leg, so it is a purchased bird, not Ingebird (every ornitharch is banded on the LEFT). Barred ornitharchs look exactly like the purchased Barred Rocks, so plumage cannot name them. Only band colour plus leg can. `resolve_band()` ignores the leg and treats green as unique, so it has misnamed that rooster as Ingebird (61 rows, 38 read as right-leg). **Fixed same day:** a band now names a bird only when its number is readable.
- **Look-alikes (Boss 21-Sep):** Ingebird looks remarkably like Adelbird; both, and Henridot (formerly Henridotta, now believed a rooster, purple band on his left leg), are colored like the purchased Barred Rocks. Their descriptions say "plumage alone cannot tell her/him apart", and that phrase is now in `roster._HEDGE_MARKERS`, so these three are no longer offered to the model for "likely <name>" naming. Band colours stay out of descriptions on purpose, because `_format_band` explains why the model must not be handed the band table.
- **Henriessa (pink #8):** on file as "barred/cuckoo"; September photos show a soft blue-grey
  laced body with a white, black-spotted head.
- Band colours shared by two living birds (purple: Birdthazar/Henridot; pink: Henriello/Henriessa)
  can't be told apart at camera distance, because band numbers are almost never legible.

## TODO

1. Plan doc (this).
2. Schema + prompt + roster age line + enricher substitution.
3. store / database / images_api / tests.
4. Temperature.
5. Registry observations; run `validate_flock_profiles.py`.
6. farm-2026 badge label + type.
7. Verify: render the prompt; one live `vlm_enricher` call on a real frame; run the pipeline tests;
   reload `com.farmguardian.pipeline` + `com.farmguardian.guardian`; watch fresh rows land with
   `standout_bird` and no age; check the API response; spot-check the score spread after ~1h.
8. CHANGELOG v2.74.0, file headers, CLAUDE.md pointer.
