# Instagram Pipeline — Phase 2 (Stories) + Phase 3 (Reels) Implementation Plan

**Audience:** a fresh Claude Code session picking up this work without the context of the planning conversation.
**Last updated:** 2026-04-20
**Status:** approved by Boss, implementation in-flight.
**Prereq reading (≤15 min):** [`HOW_IT_ALL_FITS.md`](HOW_IT_ALL_FITS.md) for the 10,000-ft system map, then [`20-Apr-2026-ig-next-phases-plan.md`](20-Apr-2026-ig-next-phases-plan.md) for the spec this plan operationalizes, then [`19-Apr-2026-instagram-posting-plan.md`](19-Apr-2026-instagram-posting-plan.md) for account voice / hashtag / framing rules.

**2026-05-04 Story-hosting update:** the Story section below is superseded for image hosting only. Story assets are no longer committed to `farm-2026/public/photos/stories/` or served from GitHub raw URLs. `post_gem_to_story()` now writes the prepared JPEG to `data/story-assets/` and passes Meta `https://guardian.markbarney.net/api/v1/images/story-assets/<name>.jpg`. Reels still use the farm-2026 public media path.

---

## Context

Phases 1–7 of [`20-Apr-2026-ig-poster-implementation-plan.md`](20-Apr-2026-ig-poster-implementation-plan.md) shipped as v2.29.0: single-photo auto-posting to `@pawel_and_pawleen` via the orchestrator's `_maybe_post_to_ig()` hook, gated on `instagram.enabled` + `instagram.auto_dry_run` in `tools/pipeline/config.json`. **Photo auto-posting is currently live** (`enabled=true, auto_dry_run=false`). Boss has asked to extend the pipeline with two new media types:

- **Phase 2 — Stories** (24-hour ephemeral, lower quality bar, 9:16 aspect ratio)
- **Phase 3 — Reels** (short-form video stitched from N photo gems, 9:16 aspect ratio)

Carousels (§2.1 in the next-phases spec) are **out of scope** for this session — deferred to a separate follow-up.

Both phases are **purely additive**: no existing primitive is removed, renamed, or refactored. The one shared-code change is promoting the private `_local_path_for_gem()` helper from `ig_poster.py` to a public `resolve_gem_image_path()` in `store.py` so the new `reel_stitcher.py` module can use it without importing a private name.

---

## Hard rules (apply to all code in this plan)

These are verbatim from [`20-Apr-2026-ig-next-phases-plan.md` §"Hard rules that apply to ALL three phases"](20-Apr-2026-ig-next-phases-plan.md). Violating any is a regression:

1. **Stick to adorable baby birds.** Brand is brooder chicks, portraits, feather detail. Secondary: adult flock, coop, yard-diary. Never security / AI-showcase / flex content.
2. **Never frame Guardian as a security/predator system.** A predator on camera = a dead bird, not content.
3. **Hashtags only from [`tools/pipeline/hashtags.yml`](../tools/pipeline/hashtags.yml).** The `forbidden` list is runtime-enforced.
4. **Creator-branded hashtags are dead zones** (`#markbarney*`, `#builtwithai`, etc.). Only place the `📸 @markbarney121` sign-off lives is inside built captions.
5. **Call `advisor` before substantive edits.** Non-negotiable.
6. **No emoji in code files or commit messages.** The 📸 sign-off only appears inside runtime-built caption strings.
7. **Tokens live in macOS keychain + `/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env` (0600)**. Never commit either.

---

## Scope

**In:**

- Stories: new predicate + posting function + CLI branch + orchestrator hook + 3 DB columns + config block.
- Reels: new stitcher module + new posting function + new CLI branch + config block + extension-guard update in `git_helper.py`.
- Tests: helper unit-tests on fixtures where cheap, one CLI dry-run per feature, one real post each to `@pawel_and_pawleen` for visual sign-off.

**Out:**

- Carousels (Phase 2.1) — deferred.
- Orchestrator auto-reels (Phase 3.1) — per [next-phases §3 "Orchestrator integration is OPTIONAL for v1"](20-Apr-2026-ig-next-phases-plan.md). CLI only in this phase.
- Real video capture (pipeline stays still-only).
- Licensed / royalty-free audio. Silent reels with a silent AAC track (IG's fetcher rejects pure-video files).
- Cover-frame customization for reels (v1 uses first frame).

---

## Phase 2 — Stories

### Files touched

| File | Change |
|---|---|
| [`tools/pipeline/store.py`](../tools/pipeline/store.py) | Add 3 columns to `image_archive`: `ig_story_id TEXT`, `ig_story_posted_at TEXT`, `ig_story_skip_reason TEXT`. Extend `_SCHEMA_SQL` + `_LATE_COLUMNS` + `_LATE_INDEX_SQL`. |
| [`tools/pipeline/ig_poster.py`](../tools/pipeline/ig_poster.py) | Add `_prepare_story_image()` (cv2 9:16 center-crop), `_create_story_container()` (media_type=STORIES, no caption), `_write_story_metadata()`, `should_post_story()` predicate, `query_last_story_ts()`, `post_gem_to_story()` public entry. |
| [`tools/pipeline/orchestrator.py`](../tools/pipeline/orchestrator.py) | Add `_maybe_post_to_story()` hook in `run_cycle()`, directly after `_maybe_post_to_ig()`. Gated on `cfg["instagram"]["stories"]["enabled"]`. |
| [`tools/pipeline/config.json`](../tools/pipeline/config.json) | Add nested block under `instagram`: `"stories": {"enabled": false, "auto_dry_run": true, "min_hours_between_stories": 2}`. |
| [`scripts/ig-post.py`](../scripts/ig-post.py) | Refactor into `--mode {photo,story,reel}` dispatch (default `photo` preserves back-compat). Post-parse validation for mode-specific required args. |

### `should_post_story` predicate

Looser than `should_post_ig`:

| Field | Photo predicate | Story predicate |
|---|---|---|
| `share_worth` | must be `strong` | must be `strong` or `decent` |
| `image_quality` | must be `sharp` | must be `sharp` or `soft` |
| `bird_count` | ≥ 1 | ≥ 1 |
| `has_concerns` | must be falsy | must be falsy |
| cadence | `min_hours_between_posts` (default 3) | `min_hours_between_stories` (default 2) |
| per-camera cadence | `min_hours_per_camera` (default 12) | **none** (stories are casual, repeat-camera is fine) |

Returns `(bool, reason)` like `should_post_ig`. Reason persisted to `ig_story_skip_reason`.

### Aspect-ratio handling — `_prepare_story_image(local_path: Path) -> Path`

Stories need 9:16 vertical. Fleet cameras are 16:9 landscape (1920×1080, 1280×720). Strategy: **center-crop on width** at native height (no upscale).

```python
# 1920×1080 → crop to 607×1080
# 1280×720  → crop to 405×720
h, w = img.shape[:2]
target_w = int(round(h * 9 / 16))
if target_w >= w:
    # portrait source — pad top/bottom with black instead
    target_h = int(round(w * 16 / 9))
    top = (target_h - h) // 2
    return cv2.copyMakeBorder(img, top, target_h - h - top, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
x0 = (w - target_w) // 2
return img[:, x0:x0 + target_w]
```

Write result as JPEG quality 92 to a `tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)` path. Caller is responsible for cleanup (via `tempfile.TemporaryDirectory` context-manager at the `post_gem_to_story` level).

Superseded 2026-05-04: Story images are stored under `data/story-assets/` in the Farm Guardian repo and served by Guardian's `/api/v1/images/story-assets/<name>.jpg` route. The URL must keep the `.jpg` extension.

### `post_gem_to_story` flow

Mirrors `post_gem_to_ig` minus caption:

1. `_lookup_gem(db_path, gem_id)` — existing.
2. `resolve_gem_image_path(gem, db_path)` — promoted helper (see Phase 3b).
3. `_prepare_story_image(local_path)` → temp 9:16 JPEG.
4. `_publish_story_asset(staging_path, db_path, filename)` → local Guardian HTTPS URL ending in `.jpg`.
5. `_load_credentials()` — existing.
6. `_create_story_container(ig_id, image_url, user_token)` — no caption field. IG rejects `caption` on stories with a 400.
7. `_wait_for_container(container_id, user_token, timeout_s=30)` — standard photo timeout (stories are images, same latency).
8. `_publish(ig_id, container_id, user_token)` — existing.
9. `_write_story_metadata(db_path, gem_id, story_id, posted_at)` — new. Mirrors `_write_permalink` but targets story columns. **Does not touch `ig_permalink`.**

Return dict shape matches `post_gem_to_ig`: `{gem_id, dry_run, raw_url, caption: None, story_id, permalink, posted_at, error}`.

Dry-run: zero side effects. Predict URL, return early.

### Orchestrator hook

`_maybe_post_to_story(cfg, db_path, camera_name, gem_id, vlm_metadata, store_result, result)` lives next to `_maybe_post_to_ig`. Four-layer gate:

1. `cfg["instagram"]["stories"]["enabled"]` — default `false`.
2. `gem_id` present.
3. `should_post_story()` passes; if not, persist reason to `ig_story_skip_reason`.
4. `cfg["instagram"]["stories"]["auto_dry_run"]` flips dry-vs-live.

Fires **after** `_maybe_post_to_ig`, not instead of. A single gem could in theory trigger both (the photo predicate is stricter — strong+sharp — while the story predicate allows decent+sharp, so in practice they'll land on different gems). Explicitly allow.

### Verification

1. Helper test: `_prepare_story_image` on a 1920×1080 fixture → assert 607×1080 JPEG output.
2. CLI dry-run: `python3 scripts/ig-post.py --mode story --gem-id N --dry-run` → local Guardian Story URL printed, no file write, no Graph API.
3. CLI real: one story without `--dry-run` → visually confirm on `@pawel_and_pawleen` for 24h. SQL-check `ig_story_id` populated.
4. Orchestrator dry-run: flip `stories.enabled=true`, leave `auto_dry_run=true`. Let daemon run for a few cycles. Grep `/tmp/guardian.*.log` + pipeline log for `story_dry_run` lines. Audit skip-reason distribution before flipping `auto_dry_run=false`.
5. Re-post sanity: attempt `--mode story` twice on the same gem; latest post wins (documented v1 semantics).

---

## Phase 3 — Reels

### Files touched

| File | Change |
|---|---|
| [`tools/pipeline/reel_stitcher.py`](../tools/pipeline/reel_stitcher.py) | **New module.** `stitch_gems_to_reel(gem_ids, output_path, db_path, config) -> Path`. cv2 for pre-crop, ffmpeg subprocess for MP4 assembly. |
| [`tools/pipeline/ig_poster.py`](../tools/pipeline/ig_poster.py) | Add `_create_reel_container()` (media_type=REELS, video_url), `post_reel_to_ig()` public entry. Reuse existing `_wait_for_container` with `timeout_s=180, poll_interval_s=5` at call site. |
| [`tools/pipeline/store.py`](../tools/pipeline/store.py) | Promote `_local_path_for_gem` (currently in `ig_poster.py`) to a public `resolve_gem_image_path(gem_row, db_path)` function. `ig_poster._local_path_for_gem` becomes a one-line wrapper so existing call sites don't change. |
| [`tools/pipeline/git_helper.py`](../tools/pipeline/git_helper.py) | Add explicit extension whitelist `{.jpg, .jpeg, .png, .mp4}` at the top of `commit_image_to_farm_2026`. Self-documents + catches mistakes. |
| [`scripts/ig-post.py`](../scripts/ig-post.py) | Add `--mode reel` branch. Requires `--gem-ids` (comma-separated 2–10 ints) and `--caption`. |
| [`tools/pipeline/config.json`](../tools/pipeline/config.json) | Add nested block under `instagram`: `"reels": {"enabled": false, "auto_dry_run": true, "output_root": "data/reels", "seconds_per_frame": 1.0, "crossfade_seconds": 0.15, "frames_per_reel_default": 6}`. `enabled` is consumed only by a future orchestrator hook — the CLI never reads it. |
| [`.gitignore`](../.gitignore) | Verify `data/` already masks `data/reels/`. Add explicit entry if not. |

### `reel_stitcher.stitch_gems_to_reel(gem_ids, output_path, db_path, config) -> Path`

Resolve each gem id → full-res JPEG via `store.resolve_gem_image_path`. ffmpeg pipeline:

1. **Pre-crop each JPEG to 9:16 at native height** using cv2. 1920×1080 → 607×1080; 1280×720 → 405×720. **Do not upscale.** If source is narrower than 9:16 (unlikely — all fleet cams are 16:9 landscape), pad top/bottom with black bars.
2. **All frames in one reel must share output resolution.** If the gem set mixes camera resolutions (e.g. gwtc 720p + s7 1080p), upscale the smaller frames to match the largest — this is the only sanctioned upscale, and solely to keep ffmpeg's xfade filter happy (it rejects mid-reel resolution changes). Prefer callers pass single-camera gem sets to avoid this entirely.
3. **Assemble MP4** with one ffmpeg subprocess call. Chain `xfade` between N `-loop 1 -t {seconds_per_frame}` image inputs; add `anullsrc` silent audio source sized to the exact computed duration. Output: cropped resolution, 30fps, H.264 high@4.1, yuv420p, AAC silent.

Representative ffmpeg invocation (6 s7-cam frames, 1.0s each, 0.15s xfade — total `6 × 1 − 5 × 0.15 = 5.25s`, output 607×1080):

```
ffmpeg -y \
  -loop 1 -t 1 -i frame-00.jpg \
  -loop 1 -t 1 -i frame-01.jpg \
  -loop 1 -t 1 -i frame-02.jpg \
  -loop 1 -t 1 -i frame-03.jpg \
  -loop 1 -t 1 -i frame-04.jpg \
  -loop 1 -t 1 -i frame-05.jpg \
  -f lavfi -t 5.25 -i anullsrc=r=48000:cl=stereo \
  -filter_complex "
    [0:v][1:v]xfade=transition=fade:duration=0.15:offset=0.85[v01];
    [v01][2:v]xfade=transition=fade:duration=0.15:offset=1.70[v02];
    [v02][3:v]xfade=transition=fade:duration=0.15:offset=2.55[v03];
    [v03][4:v]xfade=transition=fade:duration=0.15:offset=3.40[v04];
    [v04][5:v]xfade=transition=fade:duration=0.15:offset=4.25[v]
  " \
  -map "[v]" -map 6:a \
  -c:v libx264 -profile:v high -level 4.1 -pix_fmt yuv420p -r 30 -b:v 3M \
  -c:a aac -b:a 128k -shortest \
  output.mp4
```

Computed programmatically:

- `total_duration = N * seconds_per_frame − (N−1) * crossfade_seconds`
- `offset_i = i * seconds_per_frame − i * crossfade_seconds` for `i in [1, N−1]`
- `anullsrc -t {total_duration}` — sized exactly, not a long track truncated by `-shortest`.

Output MP4 validated with `ffprobe`:

- ≥ 1 video stream with `codec_name=h264`, `pix_fmt=yuv420p`, expected `width` × `height` (matches pre-crop), `duration ≈ total_duration ± 0.1s`.
- ≥ 1 audio stream with `codec_name=aac`.
- `file size < 100 MB` (IG's cap — easily under for short clips).

### `post_reel_to_ig` flow

```python
def post_reel_to_ig(
    reel_mp4_path: Path,
    caption: str,
    db_path: Path,
    farm_2026_repo_path: Path,
    associated_gem_ids: Optional[list[int]] = None,
    dry_run: bool = False,
) -> dict: ...
```

1. Validate: `reel_mp4_path` exists, extension is `.mp4`, ffprobe passes.
2. Dry-run: predict raw URL (subdir=`reels/YYYY-MM`), return.
3. `commit_image_to_farm_2026(reel_mp4_path, subdir="reels/YYYY-MM", ...)` → raw URL. Function name is misleading for MP4 but semantically fine; don't rename now (bigger diff).
4. `_create_reel_container(ig_id, video_url=raw_url, caption=caption, user_token)` — body is `{media_type: "REELS", video_url, caption, access_token}`.
5. `_wait_for_container(container_id, user_token, timeout_s=180, poll_interval_s=5)` — reels take 30–60s to process.
6. `_publish(ig_id, container_id, user_token)`.
7. For each `gem_id in associated_gem_ids`: `_write_permalink(db_path, gem_id, permalink, posted_at_iso=posted_at)`. Same permalink propagates to all source gems so the 3h/12h cooldowns in `should_post_ig` naturally prevent re-posting reel frames as standalone photos.

Return dict: `{reel_path, associated_gem_ids, dry_run, raw_url, caption, media_id, permalink, posted_at, error}`.

### CLI — `--mode {photo,story,reel}`

Refactor `scripts/ig-post.py` from single-purpose to mode-dispatched:

| Mode | Required args | Forbidden args | Entry point |
|---|---|---|---|
| `photo` (default) | `--gem-id`, `--caption` | `--gem-ids` | `post_gem_to_ig` |
| `story` | `--gem-id` | `--caption`, `--gem-ids` | `post_gem_to_story` |
| `reel` | `--gem-ids` (2–10), `--caption` | `--gem-id` | `stitch_gems_to_reel` → `post_reel_to_ig` |

Post-parse validation (argparse can't natively express conditional required-ness) raises a clear error and exits with code 2.

Back-compat: `python3 scripts/ig-post.py --gem-id N --caption "..."` still works because `--mode` defaults to `photo`.

Auto-selection predicate `should_post_reel` is NOT shipped in v1; Boss hand-picks the gem_ids for each reel. Phase 3.1 (later) can add auto-selection.

### Verification

1. Offline stitch: 6 fixture JPEGs → MP4. ffprobe confirms codec/res/duration/audio. Plays in QuickTime + Safari.
2. Raw URL after git push: `curl -I <url>` → `HTTP/2 200` + `content-type: video/mp4`.
3. CLI dry-run: `python3 scripts/ig-post.py --mode reel --gem-ids 1,2,3 --caption "..." --dry-run` → predicted URL, no git push, no Graph API.
4. CLI real: one reel to `@pawel_and_pawleen`. Boss visually confirms playback quality.
5. DB sanity: after real post, all N `image_archive` rows for input gem_ids have matching `ig_permalink` + `ig_posted_at`.

---

## Cross-cutting decisions

1. **Aspect-ratio handling = center-crop, no letterbox.** Lower visual compromise. Tested on fleet cams (all 16:9). Letterbox knob can be added later if needed.
2. **Config nesting.** Both `stories` and `reels` are nested blocks under `instagram`, not flat keys. Matches the nested `reels: {...}` style in the spec and the existing `instagram.*` convention.
3. **Dedup semantics (`ig_permalink`).** Latest post wins — `_write_permalink` already uses `COALESCE(?, ig_permalink)` which overwrites non-NULL args. Acceptable because predicate bars differ (strong+sharp for photos vs. manual for reels) so collisions are rare. If it bites, add `ig_reel_permalink` in v1.1.
4. **Stories never touch `ig_permalink`.** Stories write to `ig_story_id` only. A gem can be both a story and a photo cleanly.
5. **Reel source-gem metadata propagation.** All `associated_gem_ids` get the reel's `ig_permalink` + `ig_posted_at`. Downstream effect: `should_post_ig` cooldowns short-circuit future attempts to re-post those frames as standalone photos — intended.
6. **No new Python deps.** cv2 and pyyaml are already pulled in. ffmpeg is already a runtime dep via `capture.py`.
7. **Advisor call before substantive edits AND before declaring done.** Plan hard-rule #5.

---

## Non-decisions (deliberately unchanged)

- `gem_poster.py` (Discord) — untouched.
- `should_post_ig` / `pick_hashtags` / `build_caption` — interfaces frozen.
- `_load_credentials` source order — same for all three post types.
- Retention sweep — story/reel metadata lives in existing `image_archive` rows that already get swept by the 90d/365d retention policy. No new retention work.
- Photo auto-posting live state (`instagram.enabled=true, auto_dry_run=false`) — not flipping. Stories and reels ship gated off as a separate roll-out.

---

## Open questions (tracked here because they surface at execution time)

1. First real reel's source gems — will use a `SELECT id FROM image_archive WHERE camera_id='s7-cam' AND share_worth='strong' AND image_quality='sharp' ORDER BY ts DESC LIMIT 6` to propose candidates for Boss to approve before actually posting.
2. Carousels (§2.1) — deferred to a follow-up session.

---

## Execution order

1. **Advisor on this plan** — done (fixes applied).
2. **Create this durable doc** — self-reference, done.
3. **Phase 2a–f + verify.**
4. **Phase 3a–f + verify.**
5. **CHANGELOG + CLAUDE.md sync** — v2.30.0 entry covering both phases.
6. **Commit + push.**
7. **Advisor before declaring done.**

Estimated session count: one long session for both phases end-to-end, with breakpoints between Phase 2 verify and Phase 3 start.
