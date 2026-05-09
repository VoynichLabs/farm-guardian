# 09-May-2026 Pipeline Redesign Plan

Author: Claude Sonnet 4.6
Date: 09-May-2026

## Background

Reels are primary content. Stories are an afterthought. Four cameras
(mba-cam, gwtc, usb-cam, dominator-cam) produce footage that is only
useful as raw material for time-lapse reels — their frames are not
good enough for VLM gem lanes or Discord curation. Feeding those
cameras into the VLM wastes LM Studio cycles and floods Discord with
noise. s7-cam and iPhone imports keep VLM + Discord unchanged.

## Scope

**In scope — this session (Track 1 + Track 3):**
- Set `vlm_bypass: true` for mba-cam, gwtc, usb-cam, dominator-cam
- Add those three cameras (gwtc, usb-cam, dominator-cam) to
  `_GEM_POST_DISABLED_CAMERAS` in gem_poster.py (mba-cam already there)
- Add `reel` hashtag bucket to hashtags.yml
- Reel runner draws from reel bucket + farm content tags

**In scope — Track 2 (DONE v2.40.9):**
- LM Studio caption generation for reels
  - Checks /v1/models before calling (safe pattern per lm-studio-reference.md)
  - Calls qwen3.5-9b with all frame caption_drafts to synthesize a cohesive caption
  - Falls back to `_build_reel_caption` if LM Studio unreachable or raw-tier frames
  - Both `_build_and_preview()` and `_build_publish_and_notify()` use new function

**In scope — Track 4 (DONE v2.40.9):**
- Per-camera time-lapse reel lanes for mba-cam, gwtc, usb-cam, dominator-cam
- New selectors, 4 script shims, 4 LaunchAgents, all loaded and registered

**Out of scope:**
- Nextdoor/FB changes
- Story lane changes

## Architecture

### Track 1 — VLM bypass + Discord disable

Camera classification after this change:

| Camera | VLM | Discord gem post | Purpose |
|--------|-----|-----------------|---------|
| s7-cam | ✓   | ✓               | nesting box portrait gems |
| house-yard | bypass (already) | ✗ (already) | yard-diary time-lapse only |
| mba-cam | bypass ← NEW | ✗ (already)   | brooder time-lapse material |
| gwtc   | bypass ← NEW | ✗ ← NEW        | coop overhead time-lapse |
| usb-cam | bypass ← NEW | ✗ ← NEW       | coop run time-lapse |
| dominator-cam | bypass ← NEW | ✗ ← NEW | opportunistic time-lapse |

Two code levers per camera:
1. `vlm_bypass: true` in `tools/pipeline/config.json` → orchestrator
   routes to `run_raw_cycle()` instead of `run_cycle()`. Frames are
   stored as raw captures; no VLM call, no Discord post.
2. `_GEM_POST_DISABLED_CAMERAS` in `gem_poster.py` → belt-and-suspenders
   for any frame that somehow bypasses the first gate.

### Track 3 — Reel hashtag bucket

Add `reel` bucket to `tools/pipeline/hashtags.yml` with verified
platform-level tags. In `daily_reel_runner._build_reel_caption()`:
pass `buckets_override=["reel", "chickens", "chicks", "homestead"]`
to `pick_hashtags()` so reels draw consistent farm + platform tags
regardless of which gem won the "best metadata" selection.

### Track 4 — Per-camera time-lapse reel lanes (future)

Orientation decisions:

| Camera | Native res | Reel format | Rationale |
|--------|-----------|------------|---------|
| mba-cam | 1280×720 | 16:9 landscape | brooder wide-angle |
| gwtc   | 1280×720 | 16:9 landscape | coop overhead |
| usb-cam | 1920×1080 | 16:9 landscape | coop run |
| dominator-cam | 1920×1080 | 16:9 landscape | variable aim |

Selector: `select_timelapse_gems(camera_id, db_path, cfg)` — time-sampled
chronological (1 frame per N minutes across the day). No reaction gate
since these cameras never post to Discord.

LaunchAgent cadence (staggered to avoid quota collision):
- mba-cam reel: 20:30
- gwtc reel: 20:45
- usb-cam reel: 21:00
- dominator-cam reel: 21:15

One-time drain: usb-cam has 7 remaining reacted gems from before the
vlm_bypass flip. First usb-cam time-lapse run should include them.

## TODOs

### Track 1 (this session — DONE)
- [x] `tools/pipeline/config.json`: add `"vlm_bypass": true` to
      usb-cam, gwtc, mba-cam, dominator-cam
- [x] `tools/pipeline/gem_poster.py`: add gwtc, usb-cam, dominator-cam
      to `_GEM_POST_DISABLED_CAMERAS`
- [x] Reload pipeline LaunchAgent: `launchctl kickstart -k gui/$(id -u)/com.farmguardian.pipeline`

### Track 3 (this session — DONE)
- [x] `tools/pipeline/hashtags.yml`: add `reel` bucket
- [x] `tools/pipeline/daily_reel_runner.py`: pass `buckets_override`
      for reel caption hashtag selection
- [x] CHANGELOG v2.40.8, commit, push

### Track 2 (DONE v2.40.9)
- [x] `tools/pipeline/daily_reel_runner.py`: `_generate_reel_caption()` helper
- [x] Falls back to `_build_reel_caption` if LM Studio unreachable or raw-tier
- [x] Both build paths use `_generate_reel_caption()`
- [x] Safe LM Studio check: GET /v1/models → verify model → POST /v1/chat/completions

### Track 4 (DONE v2.40.9)
- [x] `tools/pipeline/ig_selection.py`: `_score_raw_frame`, `select_timelapse_gems`,
      4 wrapper functions
- [x] `tools/pipeline/daily_reel_runner.py`: `DailyReelLane.landscape_mode` +
      `discord_preview_scale`; 4 lane constants; `_stitch_reel` passes landscape param;
      Discord preview scale propagated
- [x] `tools/pipeline/reel_stitcher.py`: `landscape=True` mode, `_pre_fit_landscape_frame`,
      `_MAX_LANDSCAPE_WIDTH/HEIGHT` constants
- [x] `tools/pipeline/config.json`: timelapse schedule keys + per-camera `raw_retention_hours: 48`
- [x] 4 script shims + 4 LaunchAgent plists (20:30/20:45/21:00/21:15)
- [x] LaunchAgents loaded: all 4 show LastExitStatus=0
- [ ] SOCIAL_MEDIA_MAP.md: add four new reel lanes (next session)
- [ ] usb-cam pre-vlm_bypass reacted gems: already in the reaction queue and
      handled by story publisher; not needed in timelapse lane
