# `tools/on_this_day/` — Historical iPhone → Facebook publisher

**Status:** Live as of 2026-04-21 (v2.36.0). Dry-run verified; publish path untested in production.
**Owner:** Boss · **Plan:** [`docs/21-Apr-2026-on-this-day-fb-pipeline-plan.md`](../../docs/21-Apr-2026-on-this-day-fb-pipeline-plan.md)

## What this is

Daily, at a cadence Boss sets, pick a pretty iPhone photo from **this calendar date** in **2022, 2024, or 2025** (never 2023 — Boss excluded it on purpose), and post it to the Facebook Page *Yorkies App* with an auto-generated caption.

The selector draws from ~78k non-trashed, non-hidden photos in the Mac Mini's Photos library, joined against the Qwen 3.5-35B–described master catalog at
`~/bubba-workspace/projects/photos-curation/photo-catalog/master-catalog.csv` (21,639 rows as of 2026-04-21 — backfill needed; see below).

This pipeline is **completely separate from the Instagram/camera-gem pipeline** (`tools/pipeline/`). That one mines live camera frames and gates on Discord reactions. This one mines historical iPhone content and gates on human review of the dry-run JSON. They share exactly two helpers: `tools/pipeline/git_helper.py` (farm-2026 commit) and `tools/pipeline/fb_poster.py` (FB Graph publish) — both unchanged.

## Quick reference

```bash
# How many photos are still uncatalogued? (Currently ~56k.)
python3 -m tools.on_this_day.catalog_backfill --status

# Walk the Photos library and describe every uncatalogued asset via LM Studio.
# Multi-hour run; resumable (per-UUID sidecar skip).
python3 -m tools.on_this_day.catalog_backfill --run

# Dry-run for today (default). Writes data/on-this-day/YYYY-MM-DD-candidates.json.
python3 -m tools.on_this_day.post_daily

# Dry-run for a specific date — also shows filtered rows with reasons.
python3 -m tools.on_this_day.post_daily --date 2026-04-21 --include-rejected

# DEFAULT (v2.36.2+): publish top-8 as separate 24-hour FB Stories,
# one story per photo. Stories don't dilute the feed and give us
# per-photo engagement signals for the later "best-of" promotion step.
python3 -m tools.on_this_day.post_daily --publish

# Post more stories (no hard cap; the default is 8).
python3 -m tools.on_this_day.post_daily --publish --publish-n 15

# Promote the top candidates to a single FB feed carousel — use this
# AFTER reviewing which stories performed well, as the "best-of" post.
python3 -m tools.on_this_day.post_daily --publish --carousel

# Separate feed posts (rarely wanted; prefer stories or carousel).
python3 -m tools.on_this_day.post_daily --publish --single --publish-n 3

# Publish one specific UUID as a single feed post.
python3 -m tools.on_this_day.post_daily --publish --uuid <ZUUID>

# Export + caption locally, skip the farm-2026 push + FB call. For smoke-testing.
python3 -m tools.on_this_day.post_daily --publish --dry-commit --uuid <ZUUID>
```

## How it's built

Four modules, in order of dependency:

| Module | Role |
|---|---|
| `selector.py` | Photos.sqlite read → catalog join → content filter → aesthetic rank. Returns `Candidate` objects. |
| `caption.py` | `Candidate` → single-sentence caption. Belt-and-suspenders banned-keyword check. |
| `catalog_backfill.py` | `--status` compares Photos.sqlite vs the catalog. `--run` shells out to `~/bubba-workspace/projects/photos-curation/photo-catalog/run_all_folders.py` (the existing vision pipeline — we deliberately do not re-implement the describer). |
| `post_daily.py` | CLI orchestrator. Dry-run writes JSON; publish exports via `osxphotos`, HEIC→JPEG via `sips`, commits to `farm-2026/public/photos/on-this-day/YYYY-MM-DD/`, calls `fb_poster.crosspost_photo`. |

## Selector scoring — hot summary

A candidate gets:

- **+2** per farm-content keyword hit in `scene_description` (`chicken`, `yorkie`, `coop`, `brooder`, `garden`, `yard`, ~25 terms).
- **+1** per good aesthetic tag (`cute`, `vibrant`, `bokeh`, etc.).
- **+2** if `time_of_day` is golden-hour / sunset / sunrise / dawn / dusk.
- **+1** if `lighting` is soft/warm/natural.
- **+1** if the primary subject is ≥40% of the frame.

Hard rejects (score zeroed, candidate dropped unless `--include-rejected`):

- `aesthetic_tags` includes `accident`, `damage`, `retail`, `receipt`, `screenshot`, `text-heavy`, `meme`, `automotive`, `paperwork`, `medical`.
- `scene_description` or `notable_elements` mentions `hawk`, `predator`, `dead`, `blood`, `injury`, `wound`, `carcass`, or any of the above.
- Short edge < 1500 px (thumbnails, corrupt rows).
- Zero positive signals (noise floor).

Top-N is score-desc, ties broken by year-desc (2025 beats 2024 beats 2022).

## Paths & invariants

| What | Where |
|---|---|
| Photos library DB | `/Users/macmini/Pictures/Photos Library.photoslibrary/database/Photos.sqlite` (opened **read-only** via `file:...?mode=ro` URI — do not change this) |
| Master catalog | `/Users/macmini/bubba-workspace/projects/photos-curation/photo-catalog/master-catalog.csv` |
| Per-photo JSON sidecars | `…/photo-catalog/originals-{0-9,A-F}/{UUID}.json` |
| External vision pipeline | `…/photo-catalog/run_all_folders.py` (shelled out, unmodified) |
| Candidate JSON (per-date) | `data/on-this-day/YYYY-MM-DD-candidates.json` (this repo, gitignored via `data/` rules) |
| Publish result JSON | `data/on-this-day/YYYY-MM-DD-publish-result.json` (this repo) |
| farm-2026 public drop | `farm-2026/public/photos/on-this-day/YYYY-MM-DD/` (committed + pushed live) |
| FB token path | `/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env` (handled by `fb_poster`, non-expiring as of 2026-04-21) |

## Two social-posting pipelines on this Mini (don't confuse them)

| Lane | Source of photos | Quality gate | Destination | Canonical code |
|---|---|---|---|---|
| **Gem lane** | Live cameras via `com.farmguardian.pipeline` | Boss's reactions on Discord `#farm-2026` (human-in-the-loop) | IG (`@pawel_and_pawleen`) + FB Page (*Yorkies App*) — stories every 2h, carousel daily 18:00, reel Sun 19:00 | `tools/pipeline/` (this repo), state in `data/image_archive.db` |
| **Archive lane** (this doc) | Qwen-catalogued iPhone archive at `~/bubba-workspace/projects/photos-curation/photo-catalog/` | Automated content filter (farm/pet keywords in; hawks/receipts/etc. out) | Same IG + FB Page — stories every 90 min | `tools/on_this_day/` (this folder), state in `data/on-this-day/posted.json` |

They share `fb_poster.py`, `git_helper.py`, and `ig_poster.py` helpers but have independent selectors, LaunchAgents, and state files. Both commit into `farm-2026/public/photos/` and use `raw.githubusercontent.com` URLs to feed Meta's media fetcher. Don't merge them — different content policies, different schemas.

**Zero-loss invariants:**
- Gem lane: `select_all_unposted_story_gems` runs every 2h with **no time window**. Anything with `discord_reactions >= 1` and `ig_story_id IS NULL` gets FIFO-drained. Capped at 25/tick to respect IG's 24h publish quota; large backlogs drain over subsequent ticks.
- Archive lane: posted UUIDs are recorded in `data/on-this-day/posted.json`, never reposted. Cloud-only photos blacklist until tomorrow and retry on next day's ticks.

## Automation (v2.36.3) — you never type these commands

Two LaunchAgents run this pipeline with zero human touch:

| Label | Cadence | Script | What it does |
|---|---|---|---|
| `com.farmguardian.on-this-day` | **every 90 min** (`StartInterval 5400`) | `scripts/on-this-day-stories.py` | Fires `post_daily.py --auto-story` — picks ONE top unposted candidate (today's on-this-day pool first, back-catalog fallback next) and publishes it as BOTH an FB Page Story AND an Instagram Story. Posted UUIDs are recorded in `data/on-this-day/posted.json` so the same photo is never re-cycled. |
| `com.farmguardian.reciprocate` | every 4 hours (`StartInterval 14400`) | `scripts/reciprocate-harvest.py` | Scans the last 2 days of Page posts + Stories, aggregates reactors/commenters, writes `data/on-this-day/engagers-YYYY-MM-DD.{json,txt}`, and posts a summary to Discord channel **`1476787165638951026`** (via the Bubba bot token from `~/.openclaw/openclaw.json`). NEVER `#farm-2026` — that channel is the IG-gem reaction-quality-gate and we don't pollute its signal. |

**Install (one-time, already done on this Mac Mini):**

```bash
cp deploy/ig-scheduled/com.farmguardian.on-this-day.plist  ~/Library/LaunchAgents/
cp deploy/ig-scheduled/com.farmguardian.reciprocate.plist  ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.farmguardian.on-this-day.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.farmguardian.reciprocate.plist
```

**Manual fire (debugging only):**

```bash
launchctl kickstart -k gui/$(id -u)/com.farmguardian.on-this-day
launchctl kickstart -k gui/$(id -u)/com.farmguardian.reciprocate
tail -f /tmp/on-this-day.out.log /tmp/reciprocate.out.log
```

**Why Graph API doesn't auto-follow-back:** Meta does not expose a "Page likes user" or "Page follows user profile" action to Page access tokens. It's asymmetric by design; only a Page→Page follow is possible programmatically, and even that requires elevated scopes we don't hold. So the reciprocate tool surfaces the *click list* — profile name + FB URL + engagement summary — and Boss follows/friends manually from the Discord DM. If Meta ever opens the API for reciprocal follows, the list is ready.

**Graph API identity quirk you'll hit:** for Page-post reactions, FB suppresses reactor `id`/`name` unless the reactor has granted the Page's app visibility (typically: Page admins, previous commenters, people who've messaged the Page). Strangers liking the Page show up in the `summary.total_count` but NOT in `data[]`. Comments always expose `from{id,name}` because comments are public content. If Boss sees "10 likes, 0 named engagers" — that's the API, not a bug.

## Posted-state ledger

`data/on-this-day/posted.json` is the single source of truth for which photos have already gone out. Keyed by Photos UUID; each entry records `posted_at`, `lanes` (`fb_story` / `ig_story` / both), `fb_post_id`, `ig_post_id`, `raw_url`. The selector respects this ledger via `already_posted(uuid)` so the 90-min LaunchAgent can't pick the same photo twice. Delete an entry to force a repost. Seeded 2026-04-22 with the four FB stories from the initial partial run.

An audit trail of every LaunchAgent tick lives at `data/on-this-day/auto-story-YYYY-MM-DD.ndjson` (one JSON row per fire — success, caption-safety skip, or no-candidate steady state).

## Publishing strategy (v2.36.2)

Three lanes, one shared selector:

1. **Story lane (default, `--publish`)** — every top candidate goes out as its own 24-hour FB Page Story. Stories are cheap: they don't compete for feed real estate, Boss can post many per day, and each one produces its own performance signal (impressions, reactions, taps-forward/backward). This is the firehose.
2. **Carousel lane (`--publish --carousel`)** — curated "best-of" feed post. Use this AFTER letting the stories run for a day or two, reading insights, and picking the winners. One carousel per chosen date. Per-photo captions are dropped (FB only renders the /feed message once per carousel); the carousel-level caption is `"On this day — {Month Day}, from {years}."`.
3. **Single lane (`--publish --single` or `--uuid`)** — one feed post per photo. Rarely wanted; lives for granular manual control.

The loop Boss described: **post many stories → harvest insights → promote the winners to a carousel**. That's why the story lane writes each `fb_post_id` + caption + UUID + score into `data/on-this-day/YYYY-MM-DD-publish-result.json` — future insight-scraping code reads that file to know which posts to pull metrics for.

## Content policy — non-negotiables

1. **Never hawks, never predators, never "Guardian is watching" framing.** This is a pretty-pictures-of-pets page, not a security demo. Both the scorer *and* the caption sanity gate reject these keywords — keep both lists in sync if you add one.
2. **Never hashtags.** Boss was explicit. Clean feed on the Page.
3. **Never 2023.** Do not remove the year filter without asking Boss.
4. **Never editorialize in captions.** Use the Qwen `scene_description` verbatim as the sentence body. Do not invent emotions, do not attribute to the photographer, do not mention "Boss"/"Mark" in public copy. See `feedback_no_editorializing` in memory.
5. **Never auto-post without a review step for now.** The `--dry-run` → human-eyeballs → `--publish` loop is the quality gate. A Discord-reaction gate is feasible later but needs schema work on `image_archive` (see plan doc).

## Gotchas the next assistant will hit

- **`ZSAVEDASSETTYPE` is not a safe filter.** Older Photos versions used 0=library, 3=web, 5=screenshot; this library uses 3/4/6 with 6 as the bulk "from library" value. We dropped the filter entirely because the catalog's `aesthetic_tags` already rejects screenshots via `screenshot`/`text-heavy`/`meme`. If you add the filter back, cross-check the enum values against a `GROUP BY ZSAVEDASSETTYPE` sample on the live DB first.
- **Catalog is ~28% of the library.** Selector will silently skip uncatalogued UUIDs. Run `catalog_backfill --status` any time "not many candidates" is confusing; 8 of the 143 matches for 2026-04-21 were uncatalogued on first run.
- **`osxphotos` path is hardcoded** to `~/.local/bin/osxphotos` (installed via `uv tool install osxphotos`, v0.75.7 as of 2026-04-21). Update the constant in `post_daily.py` if that ever moves.
- **HEIC→JPEG via `sips`**, not ImageMagick or Pillow. `sips` is built into macOS and handles iPhone HEIC correctly including orientation EXIF. Do not introduce a Python HEIC dependency.
- **`git_helper.commit_image_to_farm_2026` is idempotent by sha256** — if you try to republish the same exact JPEG (e.g. re-running after a transient FB failure), the second commit becomes a no-op and you get the existing raw URL back. Good for retries.
- **LM Studio safety** — `catalog_backfill --run` only does a `/v1/models` GET as pre-flight. It does **not** auto-load the Qwen model (that's forbidden per CLAUDE.md). If the model isn't loaded, `run_all_folders.py` will fail fast on the first inference call and you restart after loading.
- **The selector uses `astimezone()` with no arg** to convert Cocoa-epoch UTC to local time for month/day comparison. This matches Photos.app's calendar semantics. If this machine ever moves timezones mid-day, "today" will roll over locally but not in UTC — expected.

## When things go wrong

| Symptom | Most likely cause |
|---|---|
| `no candidates for <date>` | Either genuinely no eligible photos, or catalog is thin on those years. Try `--include-rejected` to see uncatalogued hits; run `catalog_backfill --run` if many. |
| `catalog CSV missing` error | Someone moved bubba-workspace. Paths are absolute on purpose (single-host pipeline); update `PHOTOS_SQLITE`/`CATALOG_CSV` constants in `selector.py`. |
| `osxphotos export failed` | TCC likely revoked Photos-library access from Terminal/Claude. Re-grant at System Settings → Privacy → Photos. |
| `fb_poster returned error` | Read `fb_poster.py` header + `~/bubba-workspace/skills/farm-facebook-crosspost/SKILL.md`. Token debug recipe in the SKILL doc. **Do not** regenerate tokens unless the `/debug_token` check there actually fails. |
| `CaptionSafetyError` in publish results | Scorer let something through that the caption check caught. Usually harmless — next candidate in the queue wins. If a specific UUID keeps tripping, add its keyword to `selector.BAD_TEXT_KEYWORDS` too. |
| Caption fine but photo looks wrong on FB | FB caches aggressively by URL. Renaming the staged file forces a new commit path (`git_helper` is sha-based for skip, name-based for the raw URL). |

## Next steps (not yet done)

- **Run the full catalog backfill.** 56k uncatalogued photos — hours of LM Studio time. Boss should kick this off when the machine will otherwise be idle.
- **Consider a LaunchAgent** to run `post_daily.py` (dry-run) every morning at, say, 07:00 local, then Boss reviews and manually runs `--publish --uuid <…>` when he's happy. That matches the "quality gate is human review" model we started with.
- **Consider widening eligible years** once Boss has an opinion on how the feed feels. 2023 is excluded intentionally; 2021 is probably thin catalog-wise but could be added.
