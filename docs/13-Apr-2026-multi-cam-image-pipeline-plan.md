<!--
Author: Claude Opus 4.6 (1M context)
Date: 13-April-2026
PURPOSE: Plan for the Farm Guardian multi-camera high-quality image pipeline.
         Captures sharp frames from all five cameras on per-camera cadences,
         gates on local quality (Laplacian sharpness + exposure), enriches
         every passing frame via glm-4.6v-flash with structured JSON output,
         archives images + metadata to SQLite for downstream "find the gems"
         queries. 90-day tiered retention. Auto-publish all metadata.
         SUPERSEDES docs/13-Apr-2026-brooder-vlm-narrator-plan.md.
SRP/DRY check: Pass — reuses Guardian's snapshot endpoints, database.py,
               and image-scoring primitives. The narrator plan it supersedes
               was scoped to one camera + flat narrative log; this is a
               different shape entirely (5 cameras, structured metadata,
               queryable archive).
-->

# Multi-Camera Image Pipeline — Plan (Draft for Approval)

**Date:** 13-April-2026
**Author:** Claude Opus 4.6 (1M context)
**Status:** Draft — awaiting Boss approval
**Supersedes:** `docs/13-Apr-2026-brooder-vlm-narrator-plan.md` (mark that doc superseded on implementation; do not delete — it has the LM Studio safety analysis we're inheriting)

---

## Concept reframe — why this exists

Until now, Farm Guardian has been a **live-video predator-detection system** that incidentally produces image artifacts (event snapshots for Discord alerts). The hardware buildout (5 cameras across 5 machines) and the unused capacity on the Mac Mini have shifted the center of gravity:

> **The product is now a continuously-curated archive of high-quality images of the flock and the property, with rich queryable metadata.** Live video and predator detection keep running unchanged on the hot path; this pipeline is a separate slow-cadence consumer of the same camera fleet.

Boss's framing: "we're producing a massive number of [images] and what we're doing is we're getting essentially high-quality photographs… you really want rich metadata about the images."

The previous narrator plan (one camera, flat narrative log, discard images) was the wrong shape for that goal. This plan replaces it.

---

## What this pipeline is, in one paragraph

A standalone tool that wakes on per-camera cadences, captures **one sharp frame** from each camera using device-specific focus discipline, gates the frame on cheap local quality heuristics, and (if it passes) sends it to `glm-4.6v-flash` with a **structured-JSON prompt** to extract rich metadata — bird count, individuals visible, activity, lighting, composition, image quality, share-worthiness, caption draft, and any health/welfare concerns. Every passing frame and its metadata are archived: the metadata in SQLite (forever), the JPEGs on disk (90-day tiered retention by share-worth). Downstream tooling (dashboard widgets, social-media exports, the Birdadette retrospective, daily highlight reels) **queries the metadata** to surface gems.

---

## Scope

**In scope (v0.1):**
- Standalone tool tree under `farm-guardian/tools/pipeline/`
- All five cameras: `house-yard`, `usb-cam`, `s7-cam`, `gwtc`, `mba-cam`
- Per-camera cadences in config (brooder tight, yard relaxed)
- Per-camera capture recipes that respect each camera's focus reality
- **Trivial** garbage filter before any VLM call (reject all-black / all-white / dropped-frame only — pixel std-dev floor); no calibrated sharpness threshold (see note below)
- `glm-4.6v-flash` enrichment via LM Studio, structured JSON output via `response_format: json_schema`
- Tiered storage: full-res for `share_worth=strong`, downscaled for `decent`, discard for `skip`
- 90-day retention with `concerns`-flagged exemption
- New SQLite table `image_archive` in Guardian's existing `database.py`
- Single-in-flight VLM call (LM Studio race-safe per `13-Apr-2026-lm-studio-reference.md`)
- Read-only LM Studio coordination (no auto-load that contention with G0DM0D3 sweeps)
- launchd plist for boot-time start (optional v0.1 — Boss may want to run manually first)

**Out of scope (v0.1, future):**
- Reference-image-based individual identification ("this is Birdadette" — v0.2; v0.1 only flags `any_special_chick: true`)
- Public website auto-export (v0.2 — adds rsync to `farm-2026/public/photos/`)
- Daily / weekly highlight-reel generation (v0.2)
- Birdadette retrospective auto-curation (v0.2 — but the v0.1 schema makes the queries trivial)
- Anomaly-detection alerts on `concerns` content (v0.2)
- Multi-image VLM rank-then-extract two-pass (v0.2; v0.1 picks via local scorer only)
- Dashboard gallery view (v0.2)

---

## Architecture

```
                                                           ┌─────────────────────────────┐
                                                           │ glm-4.6v-flash on LM Studio │
                                                           │ http://localhost:1234       │
                                                           └──────────────▲──────────────┘
                                                                          │ POST /v1/chat/completions
                                                                          │ (image + JSON-schema prompt)
                                                                          │ single-in-flight only
                                                                          │
   per-camera                                                             │
   ┌──────────────────┐  high-quality grab    ┌─────────────┐  pass  ┌────┴────────┐
   │ house-yard       ├──── HTTP cmd=Snap ───►│             ├───────►│ vlm_enricher│
   │ usb-cam          ├──── OpenCV +AF ──────►│ quality_    │        │ structured  │
   │ s7-cam           ├──── HTTP /photo.jpg ──►│   gate     │        │ JSON output │
   │ gwtc             ├──── RTSP burst+pick ──►│             │        └────┬────────┘
   │ mba-cam (NEW)    ├──── RTSP burst+pick ──►│             │             │
   └──────────────────┘                       └──────┬──────┘             │
                                                     │ fail               │
                                                     ▼                    ▼
                                                 (discard,       ┌──────────────────┐
                                                  log fail)      │  store           │
                                                                 │  ┌────────────┐  │
                                                                 │  │ JPEG to    │  │
                                                                 │  │ data/      │  │
                                                                 │  │ archive/   │  │
                                                                 │  └────────────┘  │
                                                                 │  ┌────────────┐  │
                                                                 │  │ row in     │  │
                                                                 │  │ SQLite     │  │
                                                                 │  │ image_     │  │
                                                                 │  │ archive    │  │
                                                                 │  └────────────┘  │
                                                                 └────┬─────────────┘
                                                                      │
                                                                      ▼
                                          ┌──────────────────────────────────────────┐
                                          │ downstream consumers (v0.1 = ad-hoc SQL; │
                                          │ v0.2 = dashboard gallery, daily reels,   │
                                          │ Birdadette retrospective auto-pull,      │
                                          │ farm-2026 public site rsync)             │
                                          └──────────────────────────────────────────┘
```

**Why standalone, not a Guardian module:**
1. Different cadence (slow). Guardian runs at 2-5 s; this runs at 2-10 min.
2. Different failure mode. If this dies, Guardian must keep running. If Guardian dies, this must back off cleanly.
3. Different concurrency. Guardian is multi-threaded real-time; this is a single sleep-then-call loop with a single in-flight VLM call.
4. The removed `vision.py` (v2.17.0) lesson holds: **VLM calls do not belong in the hot path.** This is the slow-path replacement.

**Module breakdown (SRP-friendly):**

| Module | Responsibility | LOC est. |
|---|---|---|
| `tools/pipeline/orchestrator.py` | Per-camera scheduler, single-in-flight VLM gate, LM Studio loaded-model check, SIGINT handler | ~250 |
| `tools/pipeline/capture.py` | Per-camera high-quality grab. Knows the focus recipe for each device. | ~200 |
| `tools/pipeline/quality_gate.py` | Laplacian variance + exposure + occupancy. Pure functions. Reuses logic from the (already-planned) `tools/image_scorer.py`. | ~80 |
| `tools/pipeline/vlm_enricher.py` | Build prompt, base64 image, POST to LM Studio with `response_format: json_schema`, parse, validate against schema. | ~150 |
| `tools/pipeline/store.py` | Write JPEG (full or downscaled per tier), insert SQLite row, update sidecar `.json`. | ~120 |
| `tools/pipeline/retention.py` | Daily sweep: delete expired images per tier, never touch metadata rows. | ~80 |
| `tools/pipeline/config.json` | Per-camera cadence, score weights, tier rules, retention days, schema path | — |
| `tools/pipeline/prompt.md` | Prompt template (separate file so it's iteratable without code edits) | — |
| `tools/pipeline/schema.json` | JSON schema for the structured VLM response | — |

Database changes go into Guardian's existing `database.py` — one new table, see schema below. Do not create a second SQLite file.

---

## Per-camera capture recipes

This is the part that decides "gem stream vs. blur stream." Every camera needs different handling.

| Camera | Capture method | Native res | Focus reality | Recipe |
|---|---|---|---|---|
| **house-yard** | HTTP `cmd=Snap` (already wired in `camera_control.take_snapshot`) | 4K (3840×2160) | Motorized AF, triggerable via `camera_control.autofocus()` | `autofocus()` → sleep 3 s → `take_snapshot()` |
| **usb-cam** | OpenCV `cv2.VideoCapture(0)` AVFoundation (local on Mini) | 1920×1080 | Continuous AF on most consumer cams | `CAP_PROP_AUTOFOCUS=1`; throw away `snapshot_warmup_frames` (already 3 in config); read frame 4+ as keeper |
| **s7-cam** | HTTP `GET http://192.168.0.249:8080/photo.jpg` (NOT the RTSP — IP Webcam serves a fresh JPEG endpoint that's sharper than RTSP grab) | ~1080p | App-driven AF, retriggerable via `POST /focus` | `POST /focus` → sleep 1.5 s → `GET /photo.jpg` |
| **gwtc** | RTSP grab via ffmpeg one-shot (or Phase-B HTTP snap once that lands) | 1280×720 | Fixed focus (laptop built-in webcam) | Burst of K=5 frames at 0.5 s spacing, score with `quality_gate`, keep sharpest |
| **mba-cam** *(new, 13-Apr-2026)* | RTSP grab via ffmpeg one-shot from MediaMTX | 1280×720 | **2013 FaceTime HD has no autofocus — fixed-focus lens.** No software knobs to turn. | Same as gwtc: burst of K=5, sharpest wins. Physical placement matters: hyperfocal sweet spot ≈ 2–4 ft. |

**The mba-cam fixed-focus reality is freeing, not limiting.** There's no AF dance to tune — the camera either lives in its focal sweet spot or it doesn't. If the brooder placement produces consistently soft frames, the answer is to move the laptop closer, not to tune software. A persistent "soft" rate above ~50% on the mba-cam quality gate after 24 h is a placement signal, not a software bug.

**Trivial garbage filter (all that runs before the VLM):**

Every captured frame goes through `quality_gate.py`, which rejects only obvious broken frames — not soft frames. Rationale (Boss pushback, 13-Apr PM): GLM 4.6v already returns `image_quality: sharp|soft|blurred` in the structured output, so a calibrated Laplacian threshold would be the same call made twice with one being a hand-tuned heuristic. A "blurry chick photo" still has valuable archive metadata (count, activity, lighting, time-of-day stats) that we lose by pre-gating. VLM cost is cheap enough (~3 h GPU/day total) that saving 30% of calls doesn't matter.

The filter checks only:
- **Pixel std-dev floor** (~5.0 on 0-255). Rejects all-black, all-white, lens-cap, dropped-frame artifacts. Anything with real content has std-dev > 20.

That's it. One check. If it fails, retry the cycle up to 3× with the per-camera capture recipe. If still failing, skip the cycle (log only — no VLM cost paid).

**For burst cameras (gwtc, mba-cam)** we still compute Laplacian variance on each burst frame to pick the sharpest *within the burst* — that's an internal ranking, not a threshold. The winner goes to the VLM regardless of its absolute sharpness value; GLM will decide if it's sharp or soft.

---

## VLM prompt + structured-JSON schema

**Prompt** (lives at `tools/pipeline/prompt.md`, hash-logged per call so we can audit changes over time):

```
This is a snapshot from the {camera_name} camera at a small backyard flock in
Hampton CT. Camera context: {camera_context}.

Known birds in this flock as of {today}:
- Birdadette: 3rd-gen Speckled Sussex hen, ~1 yo, GPU-incubated
- Four winter-survivor adults (mixed breeds — not individually distinct in
  most photos)
- ~22 brooder chicks, currently 1-3 weeks old, mixed exotic-island-fowl
  variants and rare breeds. Most are visually similar; flag anything that
  stands out from its siblings.

Output ONLY a single JSON object that conforms to the provided schema. No
prose, no commentary, no markdown fences. The system will reject your
response if it does not parse as valid JSON matching the schema.
```

**`tools/pipeline/schema.json`** (passed to LM Studio via `response_format: {"type":"json_schema","json_schema":{...}}`):

```json
{
  "name": "farm_image_metadata",
  "strict": true,
  "schema": {
    "type": "object",
    "properties": {
      "scene":              { "type": "string", "enum": ["brooder","yard","coop","nesting-box","sky","other"] },
      "bird_count":         { "type": "integer", "minimum": 0 },
      "individuals_visible":{ "type": "array",   "items": { "type": "string", "enum": ["birdadette","adult-survivor","chick","unknown-bird"] } },
      "any_special_chick":  { "type": "boolean" },
      "apparent_age_days":  { "type": ["integer","null"], "minimum": 0, "maximum": 365 },
      "activity":           { "type": "string", "enum": ["huddling","eating","drinking","dust-bathing","foraging","preening","sleeping","sparring","alert","none-visible","other"] },
      "lighting":           { "type": "string", "enum": ["natural-good","heat-lamp","dim","blown-out","backlit","mixed"] },
      "composition":        { "type": "string", "enum": ["portrait","group","wide","cluttered","empty"] },
      "image_quality":      { "type": "string", "enum": ["sharp","soft","blurred"] },
      "share_worth":        { "type": "string", "enum": ["skip","decent","strong"] },
      "share_reason":       { "type": "string", "maxLength": 200 },
      "caption_draft":      { "type": "string", "maxLength": 200 },
      "concerns":           { "type": "array",  "items": { "type": "string", "maxLength": 200 } }
    },
    "required": ["scene","bird_count","individuals_visible","any_special_chick","apparent_age_days","activity","lighting","composition","image_quality","share_worth","share_reason","caption_draft","concerns"],
    "additionalProperties": false
  }
}
```

Notes on the schema:
- `caption_draft` is **never** to attribute artistry to real people, never to invent sources, never to put Boss's name in it. (Auto-memory: "no editorializing.") Operator can lift it verbatim or rewrite.
- `concerns` is the private-notes hook. Non-empty → routes to the brooder private track (auto-memory: "brooder private notes — INTERNAL ONLY, never publish"). v0.1 just sets a `has_concerns` flag and exempts from auto-delete; v0.2 wires the actual private-notes write.
- Breeds are deliberately **not** in the schema. Boss's note: "we know the breeds more or less well." We don't need GLM to guess at a wide breed taxonomy.

**LM Studio `response_format` support note:** GLM 4.6v on LM Studio may or may not honor strict JSON schemas — depends on the build. If the `strict: true` path errors, fall back to `response_format: {"type":"json_object"}` and validate with `jsonschema` Python package after the fact. Either way, never ship un-validated VLM output to the database.

---

## Storage — SQLite + on-disk JPEGs, tiered retention

**SQLite table** (added to Guardian's existing `database.py`, same DB file, no second store):

```sql
CREATE TABLE IF NOT EXISTS image_archive (
  id INTEGER PRIMARY KEY,
  camera_id TEXT NOT NULL,
  ts TEXT NOT NULL,                         -- ISO 8601 with timezone
  image_path TEXT,                          -- relative to data/archive/; NULL after retention sweep
  image_tier TEXT NOT NULL,                 -- 'strong' | 'decent' | 'skip' (skip rows have NULL image_path from creation)
  sha256 TEXT,                              -- of the stored JPEG (NULL if not stored)
  width INT, height INT, bytes INT,
  sharpness REAL, exposure_p50 REAL, occupancy REAL,
  vlm_model TEXT, vlm_inference_ms INT,
  vlm_prompt_hash TEXT,                     -- so we can correlate quality with prompt versions
  vlm_json TEXT NOT NULL,                   -- full structured response

  -- denormalized columns from vlm_json for query speed:
  scene TEXT, bird_count INT, activity TEXT, lighting TEXT,
  composition TEXT, image_quality TEXT, share_worth TEXT,
  any_special_chick INT, apparent_age_days INT, has_concerns INT,
  individuals_visible_csv TEXT,             -- comma-joined for cheap LIKE queries

  retained_until TEXT,                      -- ISO date; NULL means keep until manual review
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_archive_camera_ts ON image_archive(camera_id, ts);
CREATE INDEX IF NOT EXISTS idx_archive_share    ON image_archive(share_worth, image_quality);
CREATE INDEX IF NOT EXISTS idx_archive_concerns ON image_archive(has_concerns) WHERE has_concerns = 1;
CREATE INDEX IF NOT EXISTS idx_archive_retain   ON image_archive(retained_until);
```

**On-disk layout:**

```
farm-guardian/data/archive/
├── 2026-04/
│   ├── house-yard/
│   │   ├── 2026-04-13T14-22-05-strong.jpg     (full 4K, 350KB)
│   │   ├── 2026-04-13T14-22-05-strong.json    (sidecar — same as vlm_json)
│   │   ├── 2026-04-13T14-27-10-decent.jpg     (downscaled to 1920px long edge, 80KB)
│   │   └── 2026-04-13T14-27-10-decent.json
│   ├── usb-cam/
│   ├── s7-cam/
│   ├── gwtc/
│   └── mba-cam/
└── 2026-05/
    └── ...
```

The sidecar `.json` is redundant with the SQLite row but cheap, grep-friendly, and survives DB corruption.

**Tier rules (config-driven, defaults below):**

| share_worth | Action | Resolution | Retention |
|---|---|---|---|
| `strong` | Archive | full | 90 d, then flag for manual keep/discard (don't auto-delete) |
| `decent` | Archive | downscaled to 1920px long edge, JPEG q=85 | 90 d, then auto-delete |
| `skip` | Discard | — | row only, no JPEG ever written |

**`concerns` non-empty:** override above — `retained_until = NULL`, never auto-delete. These go to the brooder private notes track (private-notes integration is v0.2; v0.1 just preserves them).

**Storage budget projection (5 cameras, 90-day steady state):**

- ~1,700 cycles/day across 5 cameras (per-camera config below)
- Quality gate pass rate ~50% (calibrate after first 48 h) → ~850 enriched/day
- Tier mix estimate (will adjust from real data): 70% skip, 25% decent, 5% strong
  - skip: 595/day × 0 KB = 0
  - decent: 213/day × 80 KB = 17 MB/day
  - strong: 42/day × 250 KB avg = 10 MB/day (mix of 4K house-yard at ~350 KB and 720p mba/gwtc at ~50 KB)
- **Total: ~27 MB/day × 90 days ≈ 2.5 GB steady-state**
- SQLite metadata: ~3 KB/row × 850/day × 365 days = ~900 MB/year (cheap)

Comfortable on the M4 Pro. Per Boss's directive — "this machine might be tight on space, so don't go crazy" — these tier mixes are conservative and the retention sweep catches drift.

---

## Per-camera cadence (config defaults)

```json
{
  "cameras": {
    "house-yard": { "cycle_seconds": 600, "burst_size": 1,  "quality_floor": 80,  "context": "PTZ overlooking the yard, sky, and coop approach" },
    "usb-cam":    { "cycle_seconds": 180, "burst_size": 1,  "quality_floor": 100, "context": "Brooder interior, indoors, heat-lamp lit" },
    "s7-cam":     { "cycle_seconds": 600, "burst_size": 1,  "quality_floor": 80,  "context": "Fixed angle on the nesting box / coop interior" },
    "gwtc":       { "cycle_seconds": 600, "burst_size": 5,  "quality_floor": 60,  "context": "Fixed-focus laptop webcam in the coop / nesting area" },
    "mba-cam":    { "cycle_seconds": 300, "burst_size": 5,  "quality_floor": 60,  "context": "Fixed-focus 2013 MacBook Air camera, currently aimed at the brooder" }
  }
}
```

Brooder cameras (usb-cam, mba-cam) get tighter cadence — chicks are interesting and changing fast. Yard / coop cameras (house-yard, s7-cam, gwtc) relax to 10 min — mostly empty most of the time.

**Throughput math:**
- house-yard: 144/day
- usb-cam: 480/day
- s7-cam: 144/day
- gwtc: 144/day × 5 burst = 720 frames captured, 144 enrichments
- mba-cam: 288/day × 5 burst = 1440 frames captured, 288 enrichments

Total enrichments/day: **~1,200** (well within the LM Studio budget).
At ~10 s avg/VLM call: ~3.3 h GLM-time/day, ~7 GB VRAM held while loaded.

---

## LM Studio coordination (inheriting from `13-Apr-2026-lm-studio-reference.md`)

Hard rules from the reference doc, repeated here so the implementer doesn't have to chase pointers:

1. **Before each cycle:** `GET /v1/models`. If something other than `zai-org/glm-4.6v-flash` is loaded, **log and skip the cycle**. Do not unload someone else's working model. This protects the G0DM0D3 sweeps.
2. **If nothing is loaded:** attempt to load with `context_length: 8192`, `flash_attention: true`, `parallel: 1`. Wait for verify. Proceed.
3. **If `glm-4.6v-flash` is loaded:** proceed.
4. **Single in-flight VLM call** — orchestrator holds a per-process lock. The cycle math above (~1,200 calls/day, ~3 h total GLM time) is naturally serialized.
5. **Never** call `/v1/chat/completions` with a `model:` field that doesn't match what's currently loaded — auto-load stacks instances and crashed the box on 2026-04-13.

---

## TODOs (ordered)

1. **Approve this plan** (Boss).
2. Confirm `mba-cam` TCC Camera permission is granted at the Air's keyboard. If not, that's the one button-click prerequisite. (Boss may handle while at the coop.)
3. Add `image_archive` table + indices to `database.py`. Migration: pure additive, no risk to existing tables.
4. Implement `tools/pipeline/quality_gate.py` first (~80 lines, pure functions, hand-testable).
5. Implement `tools/pipeline/capture.py`. Each camera method is independent; can be developed and tested per-camera.
6. Implement `tools/pipeline/vlm_enricher.py` with the safe LM Studio model-check pattern. Test in isolation against a single hand-picked image first.
7. Implement `tools/pipeline/store.py` and `retention.py`. Test retention sweep with synthetic dated rows.
8. Implement `tools/pipeline/orchestrator.py`. Run with `--once --camera usb-cam` first, then `--once` (all cameras), then `--daemon` for a 24 h soak.
9. Audit the first 200 archived rows by hand. Check: (a) does the share_worth distribution roughly match the 70/25/5 estimate? (b) does the tier-3 (strong) sample look genuinely Instagram-worthy? (c) any concerns rows that should not have been published?
11. Mark the narrator plan superseded: add a banner line at the top of `13-Apr-2026-brooder-vlm-narrator-plan.md` pointing here. Do not delete it.
12. CHANGELOG entry: v2.23.0 — "Multi-camera image pipeline: standalone tool, all 5 cameras, structured-JSON enrichment via glm-4.6v-flash, queryable SQLite archive, 90-day tiered retention. Out-of-band from main Guardian pipeline."
13. Update `CLAUDE.md` "Modules" section under a new "Tools (not part of the main pipeline)" subsection.

**Verification steps:**
- Per-camera `capture.py --camera <name> --out /tmp/test.jpg` smoke test → file exists, opens as JPEG, resolution matches expected
- `quality_gate.py /tmp/test.jpg` → prints sharpness/exposure/occupancy + pass/fail
- `vlm_enricher.py /tmp/test.jpg` → prints valid JSON matching schema
- `orchestrator.py --once` → one row per camera in `image_archive`, JPEGs on disk per tier, log clean
- `sqlite3 data/guardian.db "SELECT camera_id, ts, share_worth, activity, image_quality FROM image_archive ORDER BY ts DESC LIMIT 20"` → eyeball-sane rows
- After 24 h: `SELECT camera_id, COUNT(*), AVG(sharpness) FROM image_archive GROUP BY camera_id` → cycle counts match cadence config, sharpness distributions look distinct per camera

---

## Downstream consumer queries (illustrative — these are v0.2 surface area)

The v0.1 schema makes these one-liners. None of them ship in v0.1 — they're here to validate that the schema is right.

```sql
-- Birdadette weekly portrait pull (for the retrospective project)
SELECT image_path, ts, caption_draft
FROM image_archive
WHERE individuals_visible_csv LIKE '%birdadette%'
  AND composition = 'portrait'
  AND image_quality = 'sharp'
ORDER BY ts DESC LIMIT 7;

-- Today's Instagram candidates
SELECT image_path, caption_draft, share_reason
FROM image_archive
WHERE share_worth = 'strong'
  AND date(ts) = date('now')
ORDER BY ts DESC;

-- Brood growth montage (one portrait per day-of-life)
SELECT MIN(image_path), apparent_age_days, MAX(sharpness)
FROM image_archive
WHERE scene = 'brooder' AND composition = 'portrait' AND image_quality = 'sharp'
GROUP BY apparent_age_days
ORDER BY apparent_age_days;

-- Private review queue (concerns flagged — never publish)
SELECT image_path, ts, vlm_json
FROM image_archive
WHERE has_concerns = 1
ORDER BY ts DESC;

-- Activity audit
SELECT date(ts) AS day, activity, COUNT(*) AS n
FROM image_archive
WHERE camera_id = 'usb-cam' AND ts > date('now', '-7 days')
GROUP BY day, activity ORDER BY day, n DESC;
```

---

## Risks & open questions

1. **GLM 4.6v JSON-schema fidelity.** 4-bit quants sometimes wander off-schema. Mitigation: schema-validate every response; on validation failure, log + retry once with stricter prompt; on second failure, store row with `share_worth='skip'` and the raw response in `vlm_json` for later analysis.
2. **mba-cam fixed focus + brooder distance.** If the Air is more than ~4 ft from the brooder, every frame will be soft. v0.1 will surface this as a high `image_quality='soft'` rate on mba-cam — a placement signal, not a software failure. Boss can move the Air closer.
3. **Cycle drift on the M4 Pro.** Five concurrent timers + a single VLM lock means cycles will skew when calls take long. Acceptable — the orchestrator should log the skew, not try to "catch up" by stacking calls.
4. **Tier mix is a guess.** The 70/25/5 split is from prior intuition, not data. The first 48 h will tell us; the storage math has 5× headroom even if the strong tier is twice the estimate.
5. **`concerns` going stale.** If GLM keeps flagging the same crooked beak chick day after day, the private-notes track will fill up with duplicates. v0.2 should dedup or summarize. v0.1 just stores.
6. **Multi-image rank-then-extract (v0.2).** GLM 4.6v supports multiple `image_url` blocks per message. A v0.2 enhancement: send the top 3 burst frames as a single rank request ("which is most photogenic, why?"), then extract metadata only on the winner. ~1.3× cost; better than relying solely on Laplacian-derived "sharp" = "good photo." Out of scope for v0.1.
7. **Reference-image identification (v0.2).** v0.1 only flags `any_special_chick: true`. To resolve "this is Birdadette" specifically requires a small reference-image gallery (5–10 photos of each named bird) sent as additional `image_url` blocks. Defer until v0.1 metadata proves the rest of the schema is right.

---

## Why this is the right shape (and the narrator plan was the wrong one)

The narrator plan asked GLM "what do you see?" and threw the image away. That captured zero of the value: no archive, no queryability, no retrospective material, no Instagram pipeline, no welfare-flagging, no per-individual tracking, no growth montage. The image was the asset and the prompt was extracting prose about it.

This plan inverts that: **the image is the asset, the metadata is the index, GLM is the indexer.** Every passing image becomes durable; every property of it becomes queryable; every downstream consumer (dashboard, social, retrospective, private notes) reads the same SQLite. The pipeline can run for a year and the result is a 100k-row archive that you can slice any way you want.

Boss's wording — "we want all the metadata as a sidebar" + "auto publish everything" + "picking out the gems" — only makes sense if the metadata persists. This is the shape that makes those queries possible.

---

## Docs / changelog touchpoints

- **New file:** this plan (`docs/13-Apr-2026-multi-cam-image-pipeline-plan.md`).
- **Mark superseded:** add banner line at top of `docs/13-Apr-2026-brooder-vlm-narrator-plan.md` pointing here.
- **On implementation:** new files in `tools/pipeline/`; one new table in `database.py`; CHANGELOG v2.23.0 entry; CLAUDE.md "Modules" subsection update.
- **No changes** to existing camera capture / detection / alert paths. This plan is purely additive.

---

**Approval requested before any code changes.** No edits to existing Guardian code beyond the additive `database.py` migration are required to ship v0.1.
