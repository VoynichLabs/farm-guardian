# 03-Aug-2026 — Weekly + monthly daylight time-lapse Reels (house-yard, duo2)

Author: Claude Sonnet 5
Date: 03-Aug-2026
Status: DRAFT — awaiting Boss approval before any code changes.

## Why

Boss wants two new kinds of Reel for the two Reolink cameras (house-yard,
duo2), on top of the existing daily time-lapse lanes:

- A **weekly** Reel: daylight hours only, spanning 7 days.
- A **monthly** Reel: daylight hours only, spanning ~30 days.

Decisions already made with Boss (02-Aug-2026 conversation):

- **One Reel per camera**, not a combined cross-camera cut. Four new lanes:
  `house-yard-weekly`, `house-yard-monthly`, `duo2-weekly`, `duo2-monthly`.
- **house-yard framing:** ship a best-effort first draft from the existing
  `data/yard-diary/` stockpile, accepting that the camera's PTZ framing
  drifts across that archive (verified — an 18-Apr noon frame and a
  1-Aug noon frame show completely different pan angles). Locking the
  camera to a fixed preset before each capture is explicitly deferred,
  not part of this work — see "Deliberately deferred" below.

## Scope

**In scope:**
- A permanent, low-cadence "keyframe" tier in `image_archive`, immune to
  both existing retention sweeps by construction, feeding these reels.
- A one-time backfill of the 307 existing `data/yard-diary/*.jpg` files
  into that tier (house-yard's historical seed — this is what makes a
  house-yard monthly reel possible almost immediately instead of a
  30-day wait).
- Going forward, both cameras get new keyframe captures via a small
  addition to the orchestrator's existing per-camera raw-capture loop —
  no new scripts, no new network calls to either camera.
- Four new selector functions, four new `DailyReelLane` entries, four
  thin script shims, four LaunchAgents — all following the existing
  timelapse-lane pattern exactly (`reel_stitcher`, `daily_reel_runner`,
  landscape mode, auto-publish, Discord notice mentioning Mark).
- A plain sunrise→sunset daylight filter (distinct from the existing
  `timelapse_golden_windows` two-narrow-activity-window feature, which
  would be wrong here — it excludes midday).

**Out of scope (this round):**
- Any change to `yard-diary-capture.py` itself. It keeps doing exactly
  what it does today (year-end-timelapse masters) — per CLAUDE.md, don't
  touch it. The new keyframe mechanism is a fully separate code path that
  happens to also read house-yard, not an extension of that script.
- Any change to `raw_retention_hours` (48h stays 48h for both cameras).
  The keyframe tier solves the long-retention need without touching the
  existing rolling window.
- duo2's backlog problem has no shortcut: it has zero pre-existing
  stockpile, so its weekly reel needs ~7 days and its monthly reel needs
  ~30 days of new keyframe captures to accrue before they can post for
  the first time. Nothing shortens that; it's flagged here so it isn't a
  surprise later.

**Deliberately deferred (KNOWN, not forgotten):**
- Locking house-yard to a fixed PTZ preset before each capture. This
  would fix the framing-drift problem for good (there's already
  preset-recall plumbing in `camera_control.py`), but Boss chose the
  best-effort path for this round. If the framing jumps read as
  distracting in the first posted reels, this is the follow-up.

## Architecture

### 1. New permanent "keyframe" tier — `tools/pipeline/store.py`

Add `store_keyframe(db_path, archive_root, camera_id, jpeg_bytes,
gate_metrics=None)`, refactored to share its INSERT body with
`store_raw()` (a `_store_bypass_frame(tier, ...)` internal, both public
functions call it with `tier="raw"` / `tier="keyframe"` respectively —
avoids duplicating the 20-column INSERT). Key difference: `retained_until`
is left `NULL` and `image_tier="keyframe"`.

This is retained forever **by construction**, with zero new retention
code: `retention.sweep()` only touches rows where `retained_until IS NOT
NULL`; `retention.sweep_raw()` only touches rows where `image_tier =
'raw'`. A `keyframe` row matches neither. Add a one-line comment in
`retention.py` next to both functions noting this so a future edit
doesn't accidentally start sweeping the tier.

### 2. Going-forward capture — `tools/pipeline/orchestrator.py`

Both house-yard and duo2 already run through the shared
`_run_raw_camera_thread` → `run_raw_cycle()` loop (house-yard at 5s/2s
cadence, duo2 similarly) — the same loop that feeds the 48h raw window
today. `run_raw_cycle()` already has `jpeg_bytes` and `gate_metrics` in
scope right where it calls `store_raw()`.

Add a small, config-gated check right after that call: for cameras
listed in a new `keyframe_capture.cameras` config block, if the current
local time is within a tolerance window (±5 min) of one of
`keyframe_capture.local_times` (default `["07:00", "12:00", "16:00"]`,
matching yard-diary's existing cadence for parity between the two
camera's reels) **and** no keyframe row exists yet for
`(camera_id, today's date, that slot)`, call `store_keyframe()` with the
frame just captured this tick.

This means: zero new scripts, zero new connections to either camera —
duo2 and house-yard are already being captured on this loop regardless;
we're just promoting up to 3 of those already-happening frames per day
into the permanent tier. Config:

```json
"keyframe_capture": {
  "cameras": ["house-yard", "duo2"],
  "local_times": ["07:00", "12:00", "16:00"],
  "timezone": "America/New_York",
  "tolerance_minutes": 5
}
```

### 3. One-time backfill — `scripts/backfill-yard-diary-keyframes.py`

Scans `data/yard-diary/*.jpg`, parses date + slot (`morning`/`noon`/
`evening`) from the filename, computes sha256/width/height/laplacian_var,
and inserts `image_tier='keyframe'` rows. **No file copy** —
`resolve_gem_image_path` reconstructs paths as `db_path.parent /
image_path`, and `data/yard-diary/` already sits under `data/` alongside
`data/guardian.db`, so `image_path = "yard-diary/2026-04-18-noon.jpg"`
resolves correctly in place. Idempotent: skip a file if a keyframe row
already references that exact path.

duo2 gets no backfill — it starts accruing from zero the day this ships.

### 4. Daylight filter — reuse `golden_windows.py` primitives, new mode

`select_timelapse_gems`'s existing daylight filter is a fixed local-hour
window (06:00–20:00) — close, but not what "daylight hours" means across
seasons. `timelapse_golden_windows` is the wrong tool entirely (two
narrow dawn/dusk activity windows, excludes midday). Add a small
`is_daylight(dt, latitude, longitude, tz_name)` helper in
`golden_windows.py` built from the *existing* `sunrise_minute()` /
`sunset_minute()` / `minute_in_window()` primitives — no new solar math,
just a (sunrise, sunset) window instead of the two configured activity
windows.

### 5. Selection — `tools/pipeline/ig_selection.py`

New shared `select_multiday_timelapse_gems(camera_id, db_path, cfg,
since_days)`:
- `WHERE camera_id = ? AND image_tier = 'keyframe' AND ts >= now - since_days`
- filter to `is_daylight(...)`
- order by `ts` ascending
- defensive cap at `reel_stitcher._MAX_FRAMES` (90), dropping oldest
  excess with a logged warning (never a silent truncation)

No bucketing/scoring needed, unlike `select_timelapse_gems` or
`growth_timelapse.select_growth_frames` — capture is already sparse
(≤3/day) and daylight-filtered, so there's nothing dense to thin out.
3/day × 7 days ≈ 21 frames (weekly); 3/day × 30 days ≈ 90 frames
(monthly, right at the cap — a clean coincidence, not tuned for it).

Four one-line wrappers, mirroring the existing
`select_mba_cam_timelapse_gems` pattern exactly:
`select_house_yard_weekly_timelapse_gems`,
`select_house_yard_monthly_timelapse_gems`,
`select_duo2_weekly_timelapse_gems`, `select_duo2_monthly_timelapse_gems`
(`since_days=7` / `30`).

### 6. Lanes — `tools/pipeline/daily_reel_runner.py`

Four new `DailyReelLane` entries, same shape as `HOUSE_YARD_CAM_TIMELAPSE_LANE`
/ `DUO2_TIMELAPSE_LANE` (`landscape_mode=True`,
`discord_preview_scale="960:540"`, `mention_user_id=MARK_DISCORD_USER_ID`,
`approval_required=False`). Pacing (tune at implementation time, but
starting point below keeps both safely under `_MAX_REEL_SECONDS`):
- Weekly (~21 frames): `seconds_per_frame=1.8` → ~35s reel.
- Monthly (~90 frames): `seconds_per_frame=0.8` → ~59s reel.

### 7. Scripts + LaunchAgents

Four thin shims under `scripts/` (`ig-house-yard-weekly-reel.py`, etc.),
identical shape to the existing timelapse shims. Four LaunchAgents:
weekly pair on Sundays (clear of the existing Sun 10:30 s7-gems and Sun
20:00 digest — propose 11:00 / 11:15), monthly pair on the 1st of the
month (propose 08:00 / 08:15, clear of house-yard's 09:00 daily reel).
Exact times confirmed at install time against the live LaunchAgent list.

## TODOs (implementation order)

1. `store.py`: `_store_bypass_frame` refactor + `store_keyframe()`.
2. `retention.py`: comment-only — document tier immunity.
3. `golden_windows.py`: `is_daylight()` helper.
4. `orchestrator.py`: keyframe-promotion hook in `run_raw_cycle` path +
   `keyframe_capture` config block.
5. `ig_selection.py`: `select_multiday_timelapse_gems` + 4 wrappers.
6. `scripts/backfill-yard-diary-keyframes.py`: one-time backfill, run
   once against the live DB, verify row count (~307) and spot-check a
   few resolved paths actually open.
7. `daily_reel_runner.py`: 4 new `DailyReelLane`s.
8. 4 script shims under `scripts/`.
9. `tools/pipeline/config.json`: `keyframe_capture` block + any new
   `instagram.scheduled` keys the selectors need.
10. Verify: run each new selector against the real DB (dry, no post),
    sanity-check frame counts and a handful of resolved images
    (house-yard's known framing jumps are expected, not a bug to chase).
    Build one reel manually per lane and check the Discord preview
    before installing any LaunchAgent.
11. Install the 4 LaunchAgents; confirm `launchctl kickstart` picks up
    the orchestrator change (needed for the keyframe-capture hook).

## Docs/Changelog touchpoints

- `CHANGELOG.md` top entry: new keyframe tier + 4 lanes, house-yard
  framing caveat, duo2 backlog timeline.
- `docs/SOCIAL_MEDIA_MAP.md`: add all 4 lanes to the schedule table
  (single source of truth per that doc's own instructions).
- `CLAUDE.md`: extend the "Live daily/weekly schedule" bullets.
- Separate, smaller fix (not part of this plan): `daily_reel_runner.py`'s
  comment claiming house-yard "has never had a plist, never posted to
  IG" is stale — there's a live `com.farmguardian.ig-house-yard-cam-timelapse-reel.plist`
  and it's on CLAUDE.md's daily schedule. Worth a one-line comment fix
  whenever that file is next touched.
