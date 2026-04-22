# Plan — iCloud library VLM categorization pipeline

**Author:** Claude Opus 4.7 (1M context)
**Date:** 22-Apr-2026
**Status:** DRAFT — awaiting Boss approval before implementation.

---

## Goal

Every photo in Boss's iCloud library (currently ~85,392 assets) gets a VLM-produced structured JSON description stored in a persistent SQLite index, keyed by the Photos library asset UUID. New photos added to the library each day are picked up and categorized within 24 hours. The index is **searchable by any future LLM agent** — no VLM re-run required — so a future agent can answer "find the photo of Birdadette on the day she hatched" by querying the index, then using osxphotos to pull only that one photo from iCloud on demand.

**Boss's explicit framing:**
- Public/non-private — not worried about privacy boundaries on this library.
- Future LLM-agent ergonomics matter more than single-user UI — queryable structure beats a pretty search page.
- New photos every day — this is a rolling pipeline, not a one-shot backfill.

---

## Scope

**In:**
- Enumerate every asset in `~/Pictures/Photos Library.photoslibrary` via osxphotos.
- For each asset without an up-to-date VLM record, pull original from iCloud, run GLM-4.6V via LM Studio `/api/v1/chat` with reasoning disabled, write structured JSON row to a new SQLite DB, delete the temp copy.
- Incremental mode: run daily, pick up only new assets (or assets whose `modification_date` has changed since the last VLM pass).
- Structured output schema covering: scene, subjects (people / animals / objects), apparent_date, lighting, dominant_colors, quality tier, share_worth, caption_draft, 10–20 short keywords for free-text search.
- A small CLI (`scripts/icloud-vlm query "chickens in snow"`) for humans and agents to hit the index without writing SQL.

**Out:**
- No re-ingestion of Guardian camera frames — those live in `image_archive` in `data/guardian.db` and have their own VLM records from the farm pipeline. This is a SEPARATE DB.
- No OCR on text-heavy screenshots (Phase 2 candidate; GLM-4.6V can caption them, which is enough for search).
- No face recognition or person identification beyond "N people visible, adult/child" counts. Boss's library contains identifiable humans; we are not building a face-recognition index.
- No cloud hosting of the index — lives on the Mac Mini; agents query locally.

---

## Architecture

### Where it lives

**`~/Documents/GitHub/farm-guardian/tools/icloud-vlm/`** — new tool directory inside the existing repo. Rationale: reuses the farm-guardian patterns (LM Studio safe-load discipline, `/api/v1/chat` with reasoning disabled, SQLite-as-primary-store, no-thinking-cost structured output), lives next to related code, same agents already know the repo. **Alternative considered:** new standalone repo. Rejected because (a) every operational doc and skill would need to be re-established, (b) the overlap in LM Studio discipline is significant, (c) Boss already has `farm-guardian/docs/` as his plan-doc home.

**Files to create:**
```
tools/icloud-vlm/
├── README.md                     — "how to run it and what's in the DB"
├── vlm_client.py                 — thin wrapper around LM Studio /api/v1/chat,
│                                   enforces "no reasoning" + retries
├── enumerate_library.py          — osxphotos → candidate UUIDs to process
├── process_asset.py              — download → VLM → write row → delete temp
├── runner.py                     — orchestrator; rate-limits, logs, resumes
├── db.py                         — SQLite schema + migrations + query helpers
├── query.py                      — CLI: free-text / keyword / date-range search
└── prompts/
    └── categorize_image.txt      — the VLM prompt (stable; hash logged per row)
```

**Data location:** `~/Library/Application Support/icloud-vlm/library_index.db` (NOT in git, NOT in the repo's `data/` dir — this is personal-library data, not farm data, and it'll grow to hundreds of MB). The repo's `.gitignore` stays unchanged; the tool reads a config path from `~/.config/icloud-vlm/config.json`.

**LaunchAgent:** `~/Library/LaunchAgents/com.farmguardian.icloud-vlm.plist` — runs `runner.py` every 2 hours during daylight, or once overnight as one long pass. Starts with the latter for phase 1, tunes later.

### LLM: GLM-4.6V via LM Studio, reasoning OFF

**Per `CLAUDE.md` and `project_farm_pipeline_v2_28.md`:** GLM-family thinking tokens only disable via LM Studio's **native `/api/v1/chat` endpoint**, not the OpenAI-compat `/v1/chat/completions`. The latter silently includes reasoning tokens no matter what you send in the body. Use the native endpoint exclusively.

**Also per `docs/13-Apr-2026-lm-studio-reference.md`:**
- Never call `/v1/chat/completions` against a model that isn't already loaded — it auto-loads and races other tenants.
- Before each run, hit `/api/v0/models` and confirm `glm-4.6v-flash` (or whatever exact ID Boss has loaded) is already resident with a bounded context_length. Do NOT `/api/v1/models/load`.
- If not loaded, log a warning and bail — don't load it silently. Boss loads what he wants loaded.

**Request shape:**
```json
POST http://localhost:1234/api/v1/chat
{
  "model": "glm-4.6v-flash",
  "messages": [
    {"role": "system", "content": "<prompts/categorize_image.txt contents>"},
    {"role": "user",   "content": [
      {"type": "text",      "text": "Return JSON only. No prose. No thinking."},
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
    ]}
  ],
  "temperature": 0.1,
  "top_p": 0.9,
  "max_tokens": 800,
  "response_format": {"type": "json_object"},
  "reasoning_effort": "none",
  "chat_template_kwargs": {"enable_thinking": false}
}
```

The last three fields are belt-and-suspenders. GLM family supports `enable_thinking: false` in template kwargs when LM Studio 0.3.x+ is running; `reasoning_effort: "none"` is a newer fallback. If both fail in empirical testing, the prompt itself includes a "Do not think. Return JSON only." directive as the final hard stop.

### Output JSON schema (stored in `library_index.db`)

```sql
CREATE TABLE vlm_record (
    asset_uuid        TEXT PRIMARY KEY,     -- Photos library ZGENERICASSET.ZUUID
    original_filename TEXT,                  -- e.g. IMG_9323.HEIC
    asset_date        TEXT NOT NULL,        -- ISO8601, when the photo was taken
    asset_modified_at TEXT NOT NULL,        -- for incremental detection
    vlm_model         TEXT NOT NULL,        -- e.g. "glm-4.6v-flash@2026-04-22"
    vlm_prompt_hash   TEXT NOT NULL,        -- SHA1 of prompt; re-run on change
    vlm_inference_ms  INTEGER,
    scene             TEXT,                  -- "brooder, indoor" / "yard, dusk"
    subjects_json     TEXT NOT NULL,         -- array of {type, count, notes}
    people_count      INTEGER DEFAULT 0,
    animals_json      TEXT,                  -- [{species, count, notes}, ...]
    lighting          TEXT,                  -- "daylight" / "heat-lamp-orange"
    dominant_colors   TEXT,                  -- "warm-orange, beige, brown"
    image_quality     TEXT,                  -- sharp / soft / blurry
    share_worth       TEXT,                  -- strong / weak / none
    caption_draft     TEXT,                  -- 1-2 sentences
    keywords_csv      TEXT,                  -- "chick,heat-lamp,day-5,birdadette"
    has_text          INTEGER DEFAULT 0,     -- screenshot / document flag
    errors            TEXT,                  -- non-null if VLM returned malformed
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_vlm_date     ON vlm_record(asset_date);
CREATE INDEX idx_vlm_scene    ON vlm_record(scene);
CREATE INDEX idx_vlm_quality  ON vlm_record(image_quality, share_worth);
CREATE VIRTUAL TABLE vlm_fts USING fts5(
    asset_uuid UNINDEXED,
    caption_draft,
    keywords_csv,
    scene,
    subjects_json,
    content='vlm_record',
    content_rowid='rowid'
);
-- FTS5 triggers to keep vlm_fts in sync omitted here; see db.py
```

**Why FTS5:** every future LLM agent will want `MATCH 'birdadette AND day-5'` or `MATCH 'snow AND yard'`. SQLite's FTS5 is fast, in-process, and every Python+sqlite3 is already wired for it.

### Processing flow (per asset)

```
1. SELECT asset_modified_at FROM vlm_record WHERE asset_uuid = ?
   — if present AND Photos library's mod date <= stored, SKIP.
   — if present AND prompt hash changed, queue for re-run.
2. osxphotos export --uuid <uuid> --download-missing $tmpdir
   — this is where iCloud is hit. One asset at a time.
3. If HEIC, convert to JPEG via sips; else use as-is.
   (GLM-4.6V expects standard JPEG/PNG; HEIC support is uneven in LM Studio.)
4. Base64 the JPEG; POST to LM Studio; parse JSON.
5. INSERT OR REPLACE vlm_record row.
6. rm -f $tmpdir/*   — explicit, no accumulation. tmpdir is per-run, ephemeral.
```

### Rate + concurrency

- **Single-threaded.** LM Studio + GLM-4.6V at Q4 on the M4 Pro processes one image in ~2–4 seconds. 85k × 3s = ~70 hours for the backfill. Acceptable as a multi-day background job.
- **Cooperative with other LM Studio tenants:** the farm pipeline uses the same LM Studio. Runner checks if `/v1/models` shows the farm's Gemma-4 as the currently-active model — if so, **wait 15 min and try again**. Per `project_lm_studio_guardian_vram_race.md`, two tenants hammering the same GPU memory is what crashed the box on 13-Apr-2026. Don't re-enact.
- **Back-pressure via config:** `max_per_hour` in `~/.config/icloud-vlm/config.json`. Default 300/hour during a backfill, 50/hour in steady-state (post-backfill).

### Incremental / daily pickup

Phase 2 LaunchAgent fires once every 24 hours and does:
1. `osxphotos query --json --field uuid,original_filename,date,modification_date` over the full library — cheap (~10s for 85k assets).
2. Diff against `vlm_record.asset_uuid` + `asset_modified_at`.
3. Queue anything new or modified; process the queue at the configured rate.

New photos added via the iPhone's iCloud upload appear in the Mac's library within minutes of the upload completing. Next daily run picks them up. Nothing else is needed.

---

## TODOs (ordered)

1. **Approve this plan.** (Boss.)
2. **Confirm the model name.** Boss said "Plan 3.6" — transcribed as GLM-4.6V. Before writing code, check `curl http://localhost:1234/api/v0/models` and get the exact model ID currently loaded. Use that string in the config.
3. **Write `tools/icloud-vlm/vlm_client.py`** — the LM Studio client, with reasoning-off enforcement and pre-flight model-loaded check. Write a one-image smoke test against a single known image and eyeball the JSON.
4. **Design and lock the `categorize_image.txt` prompt.** Test on 20 diverse images — brooder, yard, portrait, screenshot, document, action shot, night, etc. Iterate until JSON is consistent and the keywords are useful for search. Compute and pin `vlm_prompt_hash`.
5. **Write `db.py` + schema migration.** Include FTS5 triggers. Create `~/Library/Application Support/icloud-vlm/library_index.db`.
6. **Write `enumerate_library.py` + `process_asset.py`.** Separate modules so the enumerator can run standalone for re-discovery after big library changes.
7. **Write `runner.py`.** Rate-limit, cooperative-with-farm-pipeline guard, structured logging to `~/Library/Logs/icloud-vlm/runner.log`, graceful shutdown on SIGTERM.
8. **Write `query.py`.** CLI. Two modes: `icloud-vlm query "<FTS5 expression>"` and `icloud-vlm describe <asset-uuid>`. Output: TSV by default, JSON with `--json`.
9. **Dry-run phase 1 — 200 assets.** Confirm throughput, error rate, quality. Inspect the resulting rows. Tune the prompt.
10. **Backfill phase — full library.** 70 hours over several nights. LaunchAgent at night only (21:00–07:00) so it doesn't thrash during the day when Boss is using LM Studio interactively.
11. **Steady-state phase — daily LaunchAgent.** Once backfill is complete, drop to once-per-24h pickup of new assets.
12. **Agent documentation.** Write `~/bubba-workspace/skills/icloud-photo-search/SKILL.md` describing how any future Claude agent queries the index. Cross-reference from `farm-guardian/CLAUDE.md`.

---

## Docs / changelog touchpoints

- **New plan doc:** this file.
- **New skill doc:** `~/bubba-workspace/skills/icloud-photo-search/SKILL.md` (phase 11).
- **CLAUDE.md updates:**
  - Add a one-liner under the "Operational skills" block pointing at `tools/icloud-vlm/` and the skill doc.
  - Note that `icloud-vlm` is a cooperative LM Studio tenant and must be paused/unpaused when the farm pipeline does big work.
- **CHANGELOG.md:** new minor version (v2.36.0 candidate) when phase 3 (first end-to-end working smoke test) lands.

---

## Risks + pre-buried wrong turns

1. **"Let's just use OpenAI-compat — it's simpler."** Don't. GLM reasoning tokens sneak in via `/v1/chat/completions` regardless of request body. Use native `/api/v1/chat`. (Farm pipeline learned this the hard way; see `project_farm_pipeline_v2_28.md`.)
2. **"Let's auto-load the model if it's not loaded."** Don't. Per 13-Apr-2026 incident, that's how the box crashes. Log a warning and bail; Boss loads what he wants loaded.
3. **"Let's parallelize with 4 workers for 4× speed."** Don't. Single-GPU VLM inference is sequential at the driver level anyway; 4 parallel clients just queue behind each other AND race the farm pipeline. Keep it single-threaded and cooperative.
4. **"Let's store images in the DB."** Don't. The library already holds them; iCloud is the authoritative backup. The DB holds metadata only, keyed by UUID. Total DB size estimate: 85k rows × ~1.5 KB = ~130 MB — fits in memory for FTS queries.
5. **"Let's skip the tmpdir and stream base64 straight from osxphotos to LM Studio."** Tempting, but LM Studio's image handler has historically choked on large base64 strings sent inline. The tmpdir costs ~10 MB peak (one image at a time) and makes debugging 10× easier. Keep it.
6. **"Let's use osxphotos --library flag."** Current default (last-opened library) is fine on this single-library Mac. If Boss ever has two libraries, revisit.
7. **"Let's skip Birdadette-specific tags."** The existing `project_birdadette_retrospective_curation.md` memory says Boss wants Birdadette photos findable by day-of-life. The VLM prompt must include "if a chick is the focal subject and this looks like a brooder scene, estimate apparent_age_days." Don't forget this.

---

## Success criteria (how we know it's done)

- `library_index.db` has a row for every asset in `~/Pictures/Photos Library.photoslibrary`.
- `scripts/icloud-vlm query 'birdadette AND "day 5"'` returns at least one result (assuming such a photo exists).
- A fresh photo taken on the iPhone today shows up in the index within 24h with a useful caption + keywords.
- The farm pipeline's throughput has not degraded; `data/pipeline-logs/*.log` shows no new LM Studio timeout errors after the backfill starts.
- A future Claude agent, given only `~/bubba-workspace/skills/icloud-photo-search/SKILL.md`, can answer "find the photo where Boss is holding Pawel in the snow" without re-running any VLM.
