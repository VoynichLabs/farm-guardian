# Instagram Pipeline — Next Phases Plan (Carousels, Stories, Reels)

**Audience:** a fresh Claude Code session (or other agent) picking up the Instagram pipeline work without the context of the sessions that shipped phases 1–7.
**Last updated:** 2026-04-20
**Status:** not started. Implementation plan for three parallel work-streams.
**Prereq reading (≤15 min):** [`HOW_IT_ALL_FITS.md`](HOW_IT_ALL_FITS.md) first — it's the 10,000-ft architecture. Then [`19-Apr-2026-instagram-posting-plan.md`](19-Apr-2026-instagram-posting-plan.md) for the narrative/account-voice rules, and [`20-Apr-2026-ig-poster-implementation-plan.md`](20-Apr-2026-ig-poster-implementation-plan.md) for the phase-1-through-7 build history.

---

## The state you're inheriting

As of 2026-04-20, the IG pipeline auto-posts **one single photo per post** when a strong+sharp brooder gem lands. The orchestrator hook fires every capture cycle, the `should_post_ig` predicate gates on tier=strong + quality=sharp + cooldowns, `pick_hashtags` + `build_caption` build the text, `git_helper` commits the JPEG to `farm-2026/public/photos/brooder/`, and `post_gem_to_ig` hits the Graph API. VLM is qwen/qwen3.5-35b-a3b via `/v1/chat/completions` with `response_format=json_schema` (schema enforcement server-side). Cadence is `min_hours_between_posts: 6` (4 posts/day) + `min_hours_per_camera: 12` (scene dedup).

**Everything below is additive.** None of these phases replaces any existing code. They extend it.

---

## Hard rules that apply to ALL three phases

Bake these into any code or caption or hashtag selection. These are not preferences — Boss has flagged each one explicitly, and violating any of them is a regression:

1. **Stick to adorable baby birds.** That's the brand for @pawel_and_pawleen. Not security, not AI showcasing, not flex content. Brooder chicks, portrait shots, close-up feather detail. Adult flock, coop, yard-diary content is secondary.
2. **NEVER frame Farm Guardian as a security/predator system.** Predator detection as coded does not work. Even if it did, a predator on camera = a dead bird, not "content." No "watching for hawks" language. No dramatization of losses.
3. **Hashtags only from `tools/pipeline/hashtags.yml`.** The `forbidden` list in that file is a runtime safety net — any tag it contains (`#markbarney*`, `#builtwithai`, invented composites, etc.) is rejected at load time. Do not bypass.
4. **Creator-branded hashtags are dead zones.** `#markbarney121`, `#markbarneyai`, `#builtwithai` — all banned. The account is family-farm content; Boss's personal brand is referenced only via the `📸 @markbarney121` sign-off at the end of captions, never as a hashtag.
5. **Call `advisor` before substantive edits.** Boss's explicit directive after multiple mistakes in prior sessions. Not optional.
6. **No emoji in code files.** No emoji in commit messages. The `📸` in sign-offs is the only emoji allowed and it goes in captions, not source.
7. **Tokens live in macOS keychain + `/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env` (0600).** Never commit either. Never write tokens to plist env vars, Railway config, or anything on disk that isn't the keychain / 0600 env file.

---

## Phase 2.1 — Carousel posting (batch N gems into one post)

**Problem:** IG rate-limit is 100 posts/24h, but we're self-limiting to 4/day (one every 6h). With single-photo posting, each 6-hour slot uses 1 gem and wastes the other 9 "slots" IG would let us fill in a carousel. A carousel of 5–10 strong shots is dramatically better content per post.

**What IG's API supports:** `media_type=CAROUSEL_ALBUM`, 2–10 child items per carousel. Each child is an image container you create first, then you create a parent container that references them by creation_id, then publish the parent. Three-step fan-out → gather → publish.

### Scope (in vs. out)

- **IN:**
  - New function `post_gem_carousel_to_ig(gem_ids: list[int], full_caption: str, ...) -> dict` in `tools/pipeline/ig_poster.py`, parallel to `post_gem_to_ig` (single). Reuses `_load_credentials`, `commit_image_to_farm_2026`, `_create_container`, `_wait_for_container`, `_publish`.
  - Batch-selection logic: given a pool of strong+sharp gems from image_archive since last IG post, pick the top N (2–10) by share-worth + diversity (no two near-identical shots — see below).
  - CLI flag: `scripts/ig-post.py --gem-ids 6849,6850,6853` (comma-separated) OR `scripts/ig-post.py --auto-carousel` (picks top gems itself).
  - Orchestrator mode toggle: `config.json → instagram.post_mode = "single"` (current) vs `"carousel"`.
  - DB write: `image_archive.ig_permalink` populates for ALL gems in the carousel (same URL for each — they're different frames of one post).

- **OUT:**
  - Mixing media types (photo + video) in one carousel. IG supports it, but we're shipping photo-only first.
  - Intra-carousel captions (not a thing — carousels share one caption).
  - Re-sequencing (`swap_children`) — pick once, publish, done.

### Diversity rule (important — don't ship without it)

The 2026-04-20 post #2 was two near-identical chick-on-laptop shots. Boss flagged: "just two pictures, and it was the same picture each fucking time." For carousels, when picking the top N:

- Group candidate gems by `camera_id` + `activity` + approximate timestamp bucket (15-min windows).
- From each group, pick the single highest `share_worth` gem.
- Across groups, prefer temporal spread (one huddle, one portrait, one feeding, rather than three huddles from the same 2-minute burst).
- Cheap version: SHA-256 perceptual hash (pHash) the JPEGs; reject any candidate whose hash is within N bits of an already-picked gem.

### Files to touch

| File | Change |
|---|---|
| `tools/pipeline/ig_poster.py` | New `post_gem_carousel_to_ig()` + new internal `_create_carousel_container()` + new `pick_carousel_gems()` selection function. |
| `tools/pipeline/orchestrator.py` | `_maybe_post_to_ig()` reads `cfg["instagram"]["post_mode"]`; if "carousel" and N strong+sharp gems are available since last post, fire carousel path. Otherwise single-photo path (fallback). |
| `tools/pipeline/config.json` | Add `post_mode: "single"` default + `carousel_max_items: 10` + `carousel_min_items: 2` + `carousel_diversity_phash_bits: 10`. |
| `scripts/ig-post.py` | Add `--gem-ids` and `--auto-carousel` flags. |
| `CHANGELOG.md` | v2.31.0 entry describing the capability + the diversity rule. |

### Verification

1. Dry-run the carousel path against 3 historical gems → verify the 3 git commits land + 3 raw-URL shapes are correct + the parent container payload references all 3 creation_ids.
2. Post a real 3-image carousel with Boss's sign-off → verify appears on @pawel_and_pawleen with all 3 shots in order.
3. Diversity check: feed 5 near-identical huddle shots, confirm pHash filter rejects 4 of them.
4. The predicate gate + cooldowns still work: `query_last_ig_post_ts` returns the carousel's posted_at; the 6h cooldown applies to the carousel as a unit, not per child.

### Gotchas

- IG carousel children are created with `is_carousel_item: true`. If you forget this flag the child container works but the parent container create fails with a cryptic 400.
- All children must finish FINISHED state before the parent container is created. Poll all N in parallel, then fan-in.
- The permalink returned by `media_publish` is for the parent; children don't have individual permalinks. Write the same permalink to all N `image_archive` rows.

---

## Phase 2.5 — Stories (24-hour ephemeral)

**Problem:** Stories are a much lower bar for posting — they disappear in 24h, so the quality gate can be looser. They're where you'd share the 3am-chick-heat-lamp-check kind of shots, the quick iPhone drops, the behind-the-scenes farm-life stuff. We're currently not using them at all.

**What IG's API supports:** `media_type=STORIES` in the same container+publish flow as regular posts. No caption (stories have stickers/text overlays we can't set programmatically via Graph API today). 1 image per story.

### Scope

- **IN:**
  - `post_gem_to_story(gem_id, db_path, farm_2026_repo_path, dry_run=False) -> dict` in `ig_poster.py`.
  - CLI: `scripts/ig-post.py --gem-id N --story` (no `--caption` — not applicable).
  - Separate predicate `should_post_story(vlm_metadata, gem_row, ...)` — looser than `should_post_ig`:
    - tier ≥ decent (not required to be strong)
    - image_quality ∈ {sharp, soft} (soft is OK for stories — ephemeral, casual vibe)
    - bird_count ≥ 1
    - has_concerns == false
    - min_hours_between_stories: 2 (stories can be more frequent than posts)
  - DB columns: `ig_story_id` + `ig_story_posted_at` + `ig_story_skip_reason` (migration in `store.py`, same pattern as the phase-2 add).
  - Orchestrator hook in `run_cycle()` parallel to the main post hook — gated by `cfg["instagram"]["stories_enabled"]` (default false).

- **OUT:**
  - Text stickers, mentions, location tags, swipe-up links. Graph API doesn't support these for programmatic posting; if you want them, a human posts from the phone.
  - Reposting a feed post to stories. Possible via the API but requires a separate flow.

### Files to touch

| File | Change |
|---|---|
| `tools/pipeline/ig_poster.py` | Add story-posting function + `should_post_story` predicate. |
| `tools/pipeline/store.py` | Migration adds `ig_story_id`, `ig_story_posted_at`, `ig_story_skip_reason` + their index. |
| `tools/pipeline/orchestrator.py` | `_maybe_post_to_story()` hook in `run_cycle()`, gated on config. |
| `tools/pipeline/config.json` | Add `stories: {enabled, auto_dry_run, min_hours_between_stories}` block. |
| `scripts/ig-post.py` | Add `--story` flag (mutually exclusive with `--caption`). |

### Verification

1. Dry-run path: confirm the raw URL + container body shape without publishing.
2. Real post: one story lands on @pawel_and_pawleen, visible for 24h.
3. Re-posting the same gem 24h after the first story: confirm `ig_story_id` lets you detect it was already a story, even after the story itself expired.

### Gotchas

- Stories have tighter aspect-ratio requirements than feed posts: **9:16 vertical** is preferred; 4:5 works. 1:1 and 1.91:1 are NOT recommended and may be rejected. The s7-cam produces 16:9 landscape — needs a crop or a letterboxed 9:16 render before upload. Consider adding a `_prepare_story_image()` helper that pillow-crops or pads to 9:16 before the git_helper commit.
- Story container creation doesn't accept `caption` — passing one returns a cryptic 400.

---

## Phase 3 — Reels (short-form video)

**Problem:** The brooder is 22+ chicks moving constantly. Any 6-second loop of one of them eating, drinking, or flapping is better IG content than any single photo. We have the raw material — sequences of high-frame-rate captures from s7-cam + iphone-cam already in the archive — we just don't stitch them.

**What IG's API supports:** `media_type=REELS` + an `image_url`-style `video_url` (must end in `.mp4`, GitHub raw URL works). 9:16 aspect ratio required. Max 90 seconds. No caption programmatic control beyond the main caption field.

### Scope

- **IN:**
  - New module `tools/pipeline/reel_stitcher.py`:
    - `stitch_gems_to_reel(gem_ids: list[int], output_path: Path, config: dict) -> Path`
    - Uses `ffmpeg` (already a dep via `capture.py`) to:
      1. Pull the N JPEGs from `data/archive/`.
      2. Crop each to 9:16 (portrait orientation) — center-crop is fine for v1.
      3. Compose into an MP4 with 0.8–1.2s per frame (so 6–10 frames fills the 6–12s sweet spot for engagement), soft cross-dissolve between frames (0.15s fade).
      4. Optional: a slow pan/zoom (Ken Burns effect) on each frame — ffmpeg's `zoompan` filter. Makes static photos feel alive.
      5. Add an audio track — silent for v1, but leave a hook where a royalty-free background-audio file path can be dropped in.
      6. Output at 1080×1920 H.264 high@4.1, 30fps, ~3Mbps. IG transcodes anyway, no need to go higher.
  - New function `post_reel_to_ig(reel_mp4_path, caption, ...) -> dict` in `ig_poster.py`. Same container+publish flow as photos, but commits the MP4 to `farm-2026/public/photos/reels/YYYY-MM-DD-<slug>.mp4` and uses `media_type=REELS`.
  - CLI: `scripts/ig-post.py --reel --gem-ids 6849,6850,... --caption "..."` — stitches + posts.
  - `should_post_reel` predicate (even stricter than photo): need ≥ 5 strong-tier gems of the same subject/camera within a time window (the narrative thread — one chick's day, one feeding session).
  - Orchestrator integration is OPTIONAL for v1. Manual CLI only is fine. Auto-stitching introduces a lot of failure modes (ffmpeg timeouts, disk pressure, etc.) that are better left for Phase 3.1.

- **OUT (v1):**
  - Real video clips (like, actually taking 30-second video from the cameras). The pipeline only captures stills; moving to video capture is its own project.
  - Background music / licensed audio. Every royalty-free-music library I've seen has usage-tracking; don't want to land in a takedown situation. Silent reels for v1.
  - Cover-frame customization (IG lets you pick which frame is the thumbnail). v1 uses the default (first frame).

### Files to touch

| File | Change |
|---|---|
| `tools/pipeline/reel_stitcher.py` | New module — pure ffmpeg subprocess calls. |
| `tools/pipeline/ig_poster.py` | Add `post_reel_to_ig()` + the reel-specific container shape. |
| `tools/pipeline/git_helper.py` | Allow MP4 in addition to JPEG (extend the allowed-extensions guard). |
| `scripts/ig-post.py` | Add `--reel` flag with required `--gem-ids` and `--caption`. |
| `tools/pipeline/config.json` | Add `reels: {enabled, auto_dry_run, output_root, frames_per_reel_default, seconds_per_frame, crossfade_seconds, cover_frame_index}` block. |
| `data/reels/YYYY-MM/` | New subdirectory of `data/` for generated MP4s before they get committed to farm-2026. |

### Verification

1. Stitch 6 JPEGs into an MP4 offline, confirm: duration = (6 × 1.0s) + 5 × 0.15s crossfade = ~6.75s, 1080×1920, 30fps, H.264, file size under IG's 100MB limit (way under).
2. Confirm the output plays in QuickTime + Safari (IG's fetcher is picky about codec parameters).
3. Commit the MP4 to farm-2026, confirm the raw URL is accessible and `curl -I` returns `content-type: video/mp4`.
4. Real post: reel lands on @pawel_and_pawleen, plays in-feed, Boss confirms it doesn't look like shit.

### Gotchas

- **Reels take 30–60 seconds to finish** (`status: IN_PROGRESS` → `FINISHED`) after container creation. The existing `_wait_for_container` polls every 2s — for reels, extend the timeout ceiling to 180s.
- **ffmpeg pixel format** must be `yuv420p`. Defaults to 10-bit on some Macs, which IG rejects. Explicit `-pix_fmt yuv420p` on every ffmpeg call.
- **No upscaling** — if gems are 1920x1080 (s7-cam native), cropping to 9:16 gets you roughly 607×1080. Fine for mobile viewing on IG. Do NOT upscale to 1080x1920 — it looks worse than the lower-res native crop.
- **Audio track is still required even for "silent" reels** — IG's fetcher occasionally rejects pure-video files. Add a silent AAC track: `-f lavfi -i anullsrc=r=48000:cl=stereo -c:a aac -shortest`.

---

## Shared infrastructure the three phases should reuse

Don't duplicate these — they exist and work:

- **Credential loading:** `ig_poster._load_credentials()` — reads macOS keychain first, falls back to env file. Story/reel posts need the same tokens as photo posts.
- **Graph API wrapper:** `ig_poster._graph_request()` — handles retry, timeout, error shape. Don't write a new one.
- **Git commit path:** `git_helper.commit_image_to_farm_2026()` — handles the raw-URL derivation + non-interactive `git push`. Extend to allow MP4 for reels (change the extension guard, not the whole function).
- **Hashtag library:** `tools/pipeline/hashtags.yml` + `pick_hashtags()`. Stories usually have no caption but reels do — reels use the same hashtag function as photos, with `buckets_override` possibly pointing at scene-specific buckets.
- **Caption builder:** `ig_poster.build_caption()` — works identically for reels. Stories don't use it (no caption).

---

## Suggested work order for a parallel Claude Code session

If one fresh Claude is picking this up, ship in this order:

1. **Phase 2.1 (Carousels) first.** Biggest immediate ROI — turns 1 daily post into 1 daily carousel of 5–10 shots. Least risk; reuses the most existing code. Should be a 1-session job.
2. **Phase 2.5 (Stories) second.** Orthogonal to carousels. Adds a second posting lane. Different cadence, different tier requirement, different DB columns. Self-contained 1-session job.
3. **Phase 3 (Reels) last.** Bigger scope (new module, ffmpeg work, new media type). May take 2 sessions — session 1 builds `reel_stitcher.py` + CLI posting; session 2 wires it into the orchestrator if Boss wants auto-reels.

**Do not work on these in parallel with the main pipeline session.** The orchestrator + config.json is touched by all three; merge conflicts are painful and silent (config.json is gitignored, so divergence doesn't show up in git). One IG phase in flight at a time.

---

## What this doc is NOT

- A complete implementation. Every "IN" bullet is a spec; the code isn't written.
- A set of approved designs. The diversity rule, the stories aspect-ratio handling, the silent-audio reel hack — these are proposed defaults. Boss gets to veto / adjust.
- A replacement for calling `advisor`. Call advisor before committing to any of these approaches. You will catch things Boss-level context flagged me on that I forgot to write down.
