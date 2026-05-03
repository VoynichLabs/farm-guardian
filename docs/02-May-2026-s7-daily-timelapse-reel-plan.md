# S7 Daily Timelapse Reel - Plan

**Date:** 02-May-2026
**Author:** GPT-5.5
**Goal:** Add a second daily Reel lane made only from `s7-cam` frames, while preserving the existing mixed-camera daily Reel lane.

**Approved change after review:** the S7 time-lapse Reel should publish automatically without a final Discord approval gate, then post a Discord notice mentioning Mark's user ID, `<@293569238386606080>`, after the IG publish succeeds. Schedule it at 21:00 local.

---

## Working Name

Call this the **S7 daily timelapse Reel**.

Technically the current Reel system stitches still images into an MP4, so this is a still-photo montage. Because the S7 is fixed, portrait, high quality, and all shots are from one angle, the result should read like a time-lapse.

---

## Current Reel Construction

Current daily Reels are built by `scripts/ig-daily-reel.py`.

The live flow is:

1. Check `data/reels/pending/*.json` for prior previews.
2. Fetch each preview's Discord message and count human reactions.
3. If approved, post the MP4 through `tools/pipeline/ig_poster.py::post_reel_to_ig`.
4. Select today's source frames with `tools/pipeline/ig_selection.py::select_daily_reel_gems`.
5. Stitch the selected images with `tools/pipeline/reel_stitcher.py::stitch_gems_to_reel`.
6. Upload the MP4 preview to Discord and save pending state.

`reel_stitcher.py` already does the right media work:

- resolves `image_archive` rows to local JPEGs
- crops or pads each frame to 9:16
- caps oversized frames at 1080x1920
- uses ffmpeg `xfade` transitions
- writes H.264 MP4 with a silent AAC track for Instagram compatibility

`post_reel_to_ig` already does the right publish work:

- commits the MP4 into `farm-2026/public/photos/reels/YYYY-MM/`
- gives Instagram a GitHub raw `.mp4` URL
- creates and publishes a Graph API `REELS` container
- writes the permalink back to source gem rows
- cross-posts to the Facebook Page

---

## Scope

### In

- Add a second daily Reel lane that selects only `camera_id = 's7-cam'`.
- Keep the existing mixed daily Reel lane unchanged from the user's point of view.
- Auto-publish the finished S7 MP4 to IG/FB without a final approval reaction.
- Post a Discord notice after the S7 Reel is live, mentioning `<@293569238386606080>`.
- Store S7 posted audit state separately from the existing mixed Reel state.
- Reuse the existing ffmpeg stitcher, Instagram poster, hashtag logic, Discord reaction-counting helpers, and publish ledger.
- Add a new LaunchAgent for the S7 lane, scheduled after the existing 18:00 social jobs.
- Update docs and changelog for the new lane.

### Out

- No camera movement or S7 phone setting changes.
- No changes to S7 orientation. Portrait remains deliberate.
- No new external services or dependencies.
- No final approval gate for the S7 time-lapse lane; approval remains only on the existing mixed daily Reel lane.
- No rewrite of the Instagram poster or stitcher.
- No changes to the existing story/carousel lanes.

---

## Architecture

### 1. Selector

Add a camera-specific daily Reel selector in `tools/pipeline/ig_selection.py`.

Preferred shape:

- Create a small shared helper for daily Reel selection.
- Keep `select_daily_reel_gems()` as the existing mixed-camera public function.
- Add `select_s7_daily_reel_gems()` as the new S7 public function.

S7 selector criteria:

- `camera_id = 's7-cam'`
- last `s7_daily_reel_window_hours`, default `24`
- `image_path IS NOT NULL`
- no concerns: `has_concerns = 0 OR has_concerns IS NULL`
- `image_quality = 'sharp'`
- `bird_count >= 1`

Selection should favor time coverage, not just strongest scores:

- group by `s7_daily_reel_bucket_minutes`, proposed default `15`
- pick the best frame per bucket using the existing `_score_gem`
- return selected frames oldest-first
- cap at `s7_daily_reel_max_frames`, proposed default `90`
- skip if fewer than `s7_daily_reel_min_frames`, proposed default `12`

Important decision: source frames do **not** need individual Discord reactions by default. The finished S7 Reel preview still requires a human reaction before posting to IG. This is what makes it a time-lapse lane rather than just a narrower copy of the existing reacted-gem Reel.

If Boss wants the stricter existing rule, add config key `s7_daily_reel_require_source_reactions = true` and include `discord_reactions >= 1` in the S7 selector.

### 2. Shared Daily Reel Runner

Extract the reusable two-phase workflow out of `scripts/ig-daily-reel.py` into a focused module, likely:

`tools/pipeline/daily_reel_runner.py`

Responsibilities:

- load config and env
- check pending state for one named Reel lane
- count human Discord reactions on preview messages
- expire unreacted previews after 48h
- build captions from selected source gems
- stitch MP4s via `stitch_gems_to_reel`
- upload Discord previews
- call `post_reel_to_ig` for approved previews
- append successful IG publishes to `tools/social/ledger.py`

The existing `scripts/ig-daily-reel.py` becomes a thin entry point that configures the current mixed lane.

The new `scripts/ig-s7-daily-reel.py` becomes a thin entry point that configures the S7 lane.

This avoids copying the current 500-line script and keeps approval/publish behavior consistent.

### 3. State Layout

Keep lanes separate:

- existing mixed lane stays at `data/reels/pending/`, `data/reels/posted/`, `data/reels/expired/`
- new S7 lane writes completed audit records to `data/reels/s7/posted/`

MP4 names should make the lane obvious:

- mixed: existing `reel-daily-...mp4`
- S7: `reel-s7-daily-...mp4`

S7 posted JSON should include a `lane` field:

```json
{
  "lane": "s7-daily",
  "date": "YYYY-MM-DD",
  "discord_notice_message_id": "...",
  "ig_permalink": "...",
  "mp4_path": "...",
  "gem_ids": [123],
  "caption": "...",
  "posted_at": "..."
}
```

### 4. Config

Add S7 keys under `tools/pipeline/config.json` -> `instagram.scheduled`:

```json
"s7_daily_reel_window_hours": 24,
"s7_daily_reel_bucket_minutes": 15,
"s7_daily_reel_max_frames": 90,
"s7_daily_reel_min_frames": 12,
"s7_daily_reel_require_source_reactions": false
```

Do not use `instagram.enabled`; scheduled lanes stay independent of the dead per-cycle Instagram hook.

### 5. Discord Notice

Reuse the existing video-upload behavior, including the low-bitrate Discord transcode when the MP4 is over the webhook limit.

S7 notice message should be visually distinct:

- username: `farm-reel-s7`
- content prefix: `<@293569238386606080> S7 daily time-lapse Reel posted to IG`

The S7 notice is informational only. It does not gate the post.

### 6. Quota Ledger

Because this adds up to one more IG publish per day, approved Reel posts should append to the shared publish ledger:

`data/social/publish_ledger.ndjson`

Use lane names:

- existing mixed Reel: `reel`
- new S7 Reel: `s7-reel`

Before publishing an approved pending Reel, check the 25-per-24h IG cap through the existing ledger. If no slot is free, leave the pending JSON in place and try again on the next scheduled run.

This also tightens the existing mixed Reel lane, which currently relies on Graph API failures rather than checking the ledger first.

### 7. LaunchAgent

Add a new deploy plist:

`deploy/ig-scheduled/com.farmguardian.ig-s7-daily-reel.plist`

Schedule: daily at **21:00 local**.

Reason:

- existing carousel and mixed daily Reel run at 18:00
- 21:00 captures more of the day's S7 frames
- 21:00 avoids simultaneous farm-2026 git pushes and social-publisher activity as much as a daily calendar job can

Installation on the Mac Mini after code approval:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.farmguardian.ig-s7-daily-reel.plist
```

---

## TODOs

- [x] Write this plan doc.
- [x] Refactor current daily Reel script into a reusable runner module.
- [x] Keep `scripts/ig-daily-reel.py` working as the mixed-camera lane entry point.
- [x] Add `select_s7_daily_reel_gems()` with bucketed S7-only selection.
- [x] Add `scripts/ig-s7-daily-reel.py`.
- [x] Add S7 scheduled config keys.
- [x] Add `deploy/ig-scheduled/com.farmguardian.ig-s7-daily-reel.plist`.
- [x] Add ledger-aware Reel publish checks and ledger appends.
- [x] Update `docs/SOCIAL_MEDIA_MAP.md`.
- [x] Update `CLAUDE.md` Instagram current-architecture notes if they still mention weekly-only Reel behavior.
- [x] Update `CHANGELOG.md`.
- [ ] Dry-run existing mixed daily Reel to confirm refactor did not break it. Blocked in this Windows checkout because `data/guardian.db` and the Mac Mini archive are not present.
- [ ] Dry-run S7 daily Reel selection and MP4 build. Blocked in this Windows checkout because `data/guardian.db` and the Mac Mini archive are not present; selector logic was verified against a temporary SQLite table.
- [ ] Verify MP4 with `ffprobe`: duration under 90s, portrait dimensions, video/audio streams present. Must run on the Mac Mini with real archive images.
- [ ] On the Mac Mini, install/load the LaunchAgent and check `/tmp/ig-s7-daily-reel.err.log`.

---

## Verification Plan

In this Windows workspace, `data/guardian.db` is not present, so selector counts and MP4 dry-runs must be verified on the Mac Mini or in the live repo checkout that has the database and archive files.

Completed locally in this checkout:

- `python -m compileall tools/pipeline/daily_reel_runner.py tools/pipeline/ig_selection.py scripts/ig-daily-reel.py scripts/ig-s7-daily-reel.py`
- JSON parse check for `tools/pipeline/config.json` and `tools/social/config.json`
- plist parse check for `deploy/ig-scheduled/com.farmguardian.ig-s7-daily-reel.plist`
- temporary SQLite selector test confirming `select_s7_daily_reel_gems()` returns only sharp, safe `s7-cam` rows and excludes other cameras, soft frames, and concern rows

Commands after implementation:

```bash
venv/bin/python scripts/ig-daily-reel.py --dry-run
venv/bin/python scripts/ig-s7-daily-reel.py --dry-run
ffprobe -hide_banner data/reels/.../reel-s7-daily-....mp4
```

Success criteria:

- existing mixed Reel dry-run still selects and stitches
- S7 dry-run selects only `s7-cam`
- S7 output is portrait MP4 with AAC audio
- S7 posted audit state goes under `data/reels/s7/posted/`
- S7 lane posts to IG through the existing `post_reel_to_ig` without waiting for a Discord reaction
- ledger records the IG publish
- Discord notice mentions `<@293569238386606080>`

---

## Docs And Changelog Touchpoints

- `docs/SOCIAL_MEDIA_MAP.md`: add separate S7 daily timelapse Reel row.
- `CHANGELOG.md`: add a new SemVer entry describing the second Reel lane, approval gate, and quota handling.
- `CLAUDE.md`: update Instagram current-state bullets if they are stale after this change.
- This plan stays as the implementation record.
