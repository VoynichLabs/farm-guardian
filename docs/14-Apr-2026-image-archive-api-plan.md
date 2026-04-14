# Image archive REST surface — backend-internal plan

**Author:** Claude Opus 4.6 (1M context)
**Date:** 14-April-2026
**Status:** Approved by Boss, in implementation
**Supersedes:** none
**Cross-repo parent plan:** [`farm-2026/docs/14-Apr-2026-image-archive-dataset-and-frontend-plan.md`](https://github.com/VoynichLabs/farm-2026/blob/main/docs/14-Apr-2026-image-archive-dataset-and-frontend-plan.md) (commit `ce946c2`). That doc is the cross-repo overview; this one is the farm-guardian-internal spec required by `CODING_STANDARDS.md`.

## Scope

**In:** Add `/api/v1/images/*` REST endpoints (list gems / single gem / JPEG bytes / recent / stats / ping + Boss-only `/review/*` endpoints for promote / demote / flag / unflag / delete / edits-audit). Add `image_archive_edits` audit table. Add bearer-token auth for review routes. Add lazy Pillow thumbnail cache under `data/cache/thumbs/`. Wire the new router into the existing FastAPI app without touching the detection / PTZ / pipeline code paths.

**Out:** `caption_overrides` (v0.2), FTS `/search` (v0.2), Birdadette server-side bucketing, Instagram autofeed, in-process rate limiter (Cloudflare edge handles it), any change to the pipeline's capture → enrich → store path.

## Architecture

```
farm-guardian/
├── database.py                [EDIT] _SCHEMA_SQL grows image_archive +
│                                     image_archive_edits DDL; add image
│                                     repository section at bottom of class
├── api.py                     [EDIT] register_api() now takes config and
│                                     also includes the images router
├── images_api.py              [NEW]  all /api/v1/images/* route handlers
├── images_auth.py             [NEW]  Bearer-token FastAPI dependency
├── images_thumb.py            [NEW]  Pillow thumbnail + disk cache
├── dashboard.py               [EDIT] CORS widened to DELETE +
│                                     Authorization + If-None-Match;
│                                     pass config through to register_api
├── guardian.py                [EDIT] GUARDIAN_REVIEW_TOKEN env overlay
├── CHANGELOG.md               [EDIT] v2.25.0 entry
└── data/cache/thumbs/         [NEW at first request]
```

**Responsibilities:**
- `images_api.py` — HTTP layer only: parse query params (Pydantic), call repository methods, shape responses. No SQL, no filesystem IO except delegating to `images_thumb.py`.
- `database.py` new section — repository methods (`get_gems`, `get_gem`, `get_recent_images`, `get_image_stats`, `get_review_queue`, `get_edits`, `append_edit`, `update_share_worth`, `append_concern`, `clear_concerns`, `soft_delete_image`). All SQL lives here. Uses the existing `GuardianDB._conn` and `_lock`.
- `images_auth.py` — bearer dependency, read token from process-local state set by `register_api`.
- `images_thumb.py` — generate / cache / serve thumbnails; expose ETag helper.

**Why a new module, not growing `api.py`:** `api.py` is already 396 lines covering detection + PTZ. The image surface is ~13 routes. SRP wins.

**Why repository methods on GuardianDB, not a parallel module:** GuardianDB owns the single WAL connection and the write lock. A parallel handle would contend for the same lock without benefit.

## Schema ownership decision

`image_archive` DDL currently lives in `tools/pipeline/store.py:ensure_schema()`. We duplicate it (literally, `CREATE TABLE IF NOT EXISTS` is idempotent) into `database.py:_SCHEMA_SQL` so that fresh installs where the pipeline hasn't run yet still have the table for the image API to query. We accept the minor duplication to avoid a cross-package import (`database.py` → `tools.pipeline.store`). Schema is stable (700+ rows committed).

The new `image_archive_edits` table is API-owned and lives solely in `database.py`.

## Auth

`Authorization: Bearer <GUARDIAN_REVIEW_TOKEN>` on every `/review/*` route. Env var loaded via the same overlay pattern used for `EBIRD_API_KEY` in `guardian.py:load_config()`. If the token is unset, review endpoints return `503 "Review endpoints disabled"`. Public endpoints remain fully open.

Applied via `Depends(require_review_token)`, not middleware. Per-route granularity, easier to reason about.

## Transactional correctness for review mutations

Filesystem-first, DB-last:
1. Resolve source and target hardlink paths from the row's current `image_path`.
2. Execute `os.link` / `Path.unlink(missing_ok=True)` (idempotent, safe to retry).
3. Open `BEGIN IMMEDIATE`, update `image_archive`, insert `image_archive_edits`, `COMMIT`.
4. FS failure before step 3: raise 5xx, DB untouched. DB failure at step 3: rollback, best-effort reverse the FS ops and log.

This mirrors the hardlink/DB pair already used on the pipeline write side in `store.py:174-197`.

## Defense-in-depth against `concerns` leaks

Enforced at three boundaries (plan §1.g):
1. **Query** — every public SQL has `WHERE has_concerns = 0` as the first predicate.
2. **Type** — public Pydantic response models don't include `concerns`, `share_reason` (on private routes only per §1.c), or `vlm_json`.
3. **Route** — `/gems/{id}` also 404s if `has_concerns=1`, even though URL-guessing should already be defused by (1) and (2).

## Verification

Against the live DB on the Mac Mini (699 rows at survey time, 10 strong, 192 with `concerns` = 0 across all cameras):

1. `curl http://localhost:6530/api/v1/images/ping` → `{"ok":true,"rows":699}`.
2. `curl http://localhost:6530/api/v1/images/stats | jq '.by_tier'` → counts match `sqlite3 data/guardian.db "SELECT image_tier, COUNT(*) FROM image_archive WHERE has_concerns=0 GROUP BY image_tier"`.
3. `curl 'http://localhost:6530/api/v1/images/gems?limit=3'` returns 3 rows with valid thumb/full URLs.
4. `curl -I .../gems/{id}/image?size=thumb` → 200, ETag, new `data/cache/thumbs/<sha>-480.jpg` appears.
5. Second request with `If-None-Match: <etag>` → 304, zero bytes body.
6. **Leak test:** `POST /review/{id}/flag` with bearer + `{"note":"smoke"}`; row vanishes from `/gems`, `/recent`, `/stats`; appears in `/review/queue?only_concerns=true`; `POST /review/{id}/unflag` restores it.
7. Over the tunnel: `curl https://guardian.markbarney.net/api/v1/images/ping` → 200.
8. `grep -oE '"(concerns|vlm_json|has_concerns)"' <public-response.json>` → empty.
9. `python tools/pipeline/retention.py --dry-run` — no schema errors.
10. Restart Guardian; dashboard still works at `http://macmini:6530/`.

## Docs / CHANGELOG touchpoints

- `CHANGELOG.md` — `## [2.25.0] - 2026-04-14` entry.
- `CLAUDE.md` — no change needed (the two-repo description already covers this kind of surface).
- Cross-repo: once live, update farm-2026's revision log in `14-Apr-2026-image-archive-dataset-and-frontend-plan.md` to mark Phase 1 complete.

## Risks

- **Plan-doc `share_reason` ambiguity.** Cross-repo §1.c says "skip on public"; cross-repo §2.a's JSON example includes it. Following §2.a (include on public). If Boss objects, remove from the public Pydantic model — one-line change.
- **`concerns` JSON mutation** in `/flag` is SELECT→parse→append→UPDATE. Safe within `BEGIN IMMEDIATE`; tested in the leak-test flow above.
- **Thumbnail cache growth.** Unbounded today (~40MB current, monotonic). Acceptable for v0.1; a future sweep script can evict cold entries.
