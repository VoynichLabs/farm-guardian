# On-This-Day → Facebook Pipeline (plan)

**Date:** 21-April-2026
**Author:** Claude Opus 4.7 (1M context)
**Status:** Implemented — see `tools/on-this-day/` and CHANGELOG v2.36.0.

## Goal

Every day, surface the prettiest iPhone/iCloud photos Boss took on *this calendar date* in **2022, 2024, and 2025** (deliberately skipping 2023) and publish the best one(s) to the linked Facebook Page *Yorkies App*. Purpose is audience-building around brooder/yorkie/flock/coop/yard-diary content — not business, not money, not hawks/predator framing.

## Why this exists

Boss has ~90,000 photos between his iPhone and iCloud. The vast majority sit unseen. The 2026-03 vision-catalog pass (`~/bubba-workspace/projects/photos-curation/photo-catalog/master-catalog.csv`, 21,639 rows of Qwen 3.5-35B descriptions) proved that LLM-tagged metadata is rich enough to mine for good-looking posts without human triage. This pipeline is the consumer side of that catalog.

## Scope — in

- Backfill script that brings the catalog from ~21k up to the full iPhone-library size (~64k locally, ~90k on iCloud).
- Selector that reads Photos.sqlite, filters to today's month-day in 2022/2024/2025, joins against the catalog, ranks by aesthetic signals, and returns top-N candidates.
- Caption composer built from catalog fields (no new LLM calls at post time).
- Orchestrator that exports the HEIC/JPEG master, converts to JPEG if needed, publishes via `git_helper.commit_image_to_farm_2026`, then calls `fb_poster.crosspost_photo`.
- Dry-run mode (default) writes candidates to `data/on-this-day/{YYYY-MM-DD}-candidates.json` for human review without posting.

## Scope — out

- No new vision model. The backfill reuses `process_batch.py` in `bubba-workspace/projects/photos-curation/photo-catalog/` — same Qwen 3.5-35B, same LM Studio host.
- No Discord reaction gate (yet). The live IG pipeline uses one; this pipeline uses `--dry-run` → human review → `--publish` as the equivalent gate. A reaction gate is a reasonable future extension but adds dependency on `image_archive.discord_reactions`, and these aren't camera frames — they're iPhone photos that never went through Guardian.
- No Instagram posting. This is FB-only. The IG pipeline is reaction-gated off camera-gem content; mixing historical iPhone shots into that stream would confuse the voice.
- No write-back to Photos library. Read-only via `Photos.sqlite` (WAL-mode SQLite; opened read-only to avoid contending with Photos.app).
- No on-device ML. All ranking uses catalog fields already computed.

## Architecture

```
┌─────────────────────────┐
│ Photos.sqlite           │  (macOS Photos Library DB)
│  ZASSET, ZDATECREATED   │
└──────────┬──────────────┘
           │ read-only query
           ▼
┌─────────────────────────┐       ┌──────────────────────────────┐
│ selector.py             │◄──────│ master-catalog.csv           │
│  - month-day match      │       │  (21k+ Qwen descriptions)    │
│  - year ∈ {2022,24,25}  │       └──────────────────────────────┘
│  - farm-theme filter    │
│  - rank by aesthetic    │
└──────────┬──────────────┘
           │ top-N candidate UUIDs + metadata
           ▼
┌─────────────────────────┐
│ post_daily.py (--publish)│
│  1. osxphotos export     │       ┌──────────────────────────┐
│  2. HEIC → JPEG if needed│──────►│ git_helper               │
│  3. caption from catalog │       │ commit_image_to_farm_2026│
│  4. fb_poster.crosspost  │       └────────┬─────────────────┘
└─────────────────────────┘                 │ raw.githubusercontent URL
                                            ▼
                                  ┌──────────────────────────┐
                                  │ fb_poster.crosspost_photo│
                                  │ (existing, unchanged)    │
                                  └──────────────────────────┘
```

Reuses:

- `tools/pipeline/git_helper.py` → `commit_image_to_farm_2026` (unchanged).
- `tools/pipeline/fb_poster.py` → `crosspost_photo` (unchanged).
- `~/bubba-workspace/projects/photos-curation/photo-catalog/process_batch.py` (unchanged; backfill just re-invokes it scoped to missing UUIDs).

New:

- `tools/on_this_day/__init__.py`
- `tools/on_this_day/catalog_backfill.py` — diff Photos.sqlite ∩ catalog, shell out to `process_batch.py` on misses.
- `tools/on_this_day/selector.py` — the core query + ranking.
- `tools/on_this_day/caption.py` — deterministic caption composer.
- `tools/on_this_day/post_daily.py` — CLI orchestrator; `--dry-run` default, `--publish` to actually post.
- `tools/on_this_day/README.md` — onboarding doc for the next Claude.

## Ranking heuristic (v1)

A candidate row gets scored; top-N by score win. All signals come from the catalog CSV.

Positive signals (each +1 unless noted):

- `aesthetic_tags` overlaps {`cute`, `beautiful`, `vibrant`, `warm`, `soft`, `bokeh`, `golden-hour`, `sunny`, `lush`}
- `scene_description` mentions any of: `chicken`, `hen`, `rooster`, `chick`, `yorkie`, `dog`, `pawel`, `pawleen`, `flock`, `coop`, `brooder`, `egg`, `garden`, `snow`, `pasture`, `yard`, `farm`, `puppy`, `kitten`, `bird`, `sunset`, `sunrise` (+2 for matches; core farm content)
- `time_of_day` ∈ {`golden-hour`, `sunset`, `sunrise`, `dawn`, `dusk`} (+2)
- `lighting` = `soft` (+1)
- `primary_subjects` has a non-empty subject list with `approximate_size_pct ≥ 30` (+1; subject-forward composition)

Negative signals (each −5, then hard-rejected if sum < 0):

- `aesthetic_tags` overlaps {`accident`, `damage`, `documentary`, `retail`, `receipt`, `screenshot`, `text-heavy`, `meme`}
- `scene_description` mentions any of: `accident`, `damage`, `receipt`, `screenshot`, `paperwork`, `invoice`, `meme`, `text message`, `hawk`, `predator`, `dead`, `blood`, `injury` (the hawk / predator filter is non-negotiable per the IG content policy and the FB cross-post inherits that voice)

Floor filter (before scoring): reject anything where `dimensions` parses to <1500 px on the short edge, or orientation/aspect ratio is out of normal photo range (corrupt rows).

## Caption format

Single line, no hashtags (FB Page audience behaves differently from IG — Boss wants the feed clean):

```
On this day, {YYYY} — {scene_description_first_sentence}
```

If `scene_description` is empty or implausibly short (<20 chars), fall back to a skeleton caption from `primary_subjects[0].subject` + `time_of_day`.

Caption sanity gate: reject captions containing any of `hawk`, `predator`, `dead`, `blood`, `injury`, `accident`, `damage`, `receipt`, `screenshot`, `paperwork`, `invoice`, `meme` — even if the scorer missed them.

## Operational modes

```bash
# Dry run — selects today's candidates, writes JSON, no FB call
python3 -m tools.on_this_day.post_daily

# Specific date (useful for backfilling a missed day or spot-checking)
python3 -m tools.on_this_day.post_daily --date 2026-04-21

# Publish the top-1 candidate to FB
python3 -m tools.on_this_day.post_daily --publish

# Publish a specific candidate (by Photos.sqlite UUID) from the last dry-run
python3 -m tools.on_this_day.post_daily --publish --uuid <ZUUID>

# Catalog backfill (run ad-hoc; slow — LM Studio throughput-bound)
python3 -m tools.on_this_day.catalog_backfill --limit 500
```

## TODOs (all complete unless noted)

1. [x] Write the plan doc (this file).
2. [x] Build `catalog_backfill.py` — diff Photos.sqlite vs `master-catalog.csv`, invoke `process_batch.py` on missing UUIDs.
3. [x] Build `selector.py` — month-day + year filter, join, rank.
4. [x] Build `caption.py` — caption composer + sanity gate.
5. [x] Build `post_daily.py` — orchestrator with `--dry-run`/`--publish`.
6. [x] Write `tools/on-this-day/README.md` aimed at the next Claude.
7. [x] Update `CHANGELOG.md` top entry (v2.36.0).
8. [x] Commit + push to `VoynichLabs/farm-guardian`.
9. [ ] **Operational (Boss or a future Claude):** schedule `post_daily.py --publish` via LaunchAgent once Boss is comfortable with dry-run output for a few days.
10. [ ] **Operational:** kick off a full catalog backfill run — several hours of LM Studio time.

## Docs/changelog touchpoints updated by this plan

- `CHANGELOG.md` → v2.36.0 entry.
- `CLAUDE.md` → no change required; this tool is Guardian-adjacent but doesn't alter any of the existing services.
- `tools/on-this-day/README.md` → primary onboarding artifact.

## Why FB-only and not IG cross-post

IG's `@pawel_and_pawleen` voice is camera-gem driven: today's yard, today's brooder, today's coop. Mixing in 4-year-old iPhone photos would muddle that. FB Page *Yorkies App* has a different audience contract — it's the "scrapbook" surface — so historical content fits there naturally. If Boss wants to also post to IG later, the selector output is reusable; only the publishing step (URL → `ig_poster` instead of `fb_poster`) changes.

## What the next Claude should NOT do

- Do not re-catalog photos that already have a JSON sidecar in `originals-{0-9,A-F}/{UUID}.json`. The backfill script enforces this but a hand-run of `process_batch.py` would re-describe everything.
- Do not query `Photos.sqlite` in read-write mode. Photos.app keeps it open in WAL; a writer lock will conflict and can leave the library in a half-consistent state. Always open with `mode=ro` via the URI form.
- Do not remove the `2023` exclusion without asking Boss. He skipped that year on purpose.
- Do not turn this into a reaction-gated pipeline without reading `docs/20-Apr-2026-ig-scheduled-posting-architecture.md` first. The existing reaction plumbing is camera-gem specific (`image_archive.discord_reactions` keyed by camera_id + timestamp); iPhone photos don't fit that schema.
- Do not add hashtags to captions. Boss was explicit: FB Page feed stays clean.
