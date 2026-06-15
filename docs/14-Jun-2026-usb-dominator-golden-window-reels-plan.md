<!--
Author: Claude Opus 4.8 (Bubba sub-agent)
Date: 14-June-2026
PURPOSE: Design note + as-built record for the golden-window rework of the usb-cam
         and dominator-cam Instagram time-lapse reel pipeline. Covers the two levers
         (reel selection + raw capture cadence), the dynamic-sunrise calc, config
         schema, blast-radius analysis, verification evidence, and the activation /
         rollback plan.
SRP/DRY check: Pass — single design doc for one change set (v2.42.0). References the
               shared golden_windows module rather than restating its math.
-->

# Golden-window time-lapse reels — usb-cam + dominator-cam (14-Jun-2026)

## Problem / Boss intent
The usb-cam (coop run) and dominator-cam time-lapse reels spread ~60 frames across
the whole 06:00–20:00 daylight band, so consecutive frames were ~14 min apart and
most of the reel was empty midday coop. Boss in #meet-the-lobsters (2026-06-14):
*"anything during the day is just junk, we don't care about it"* → cover only the two
daily activity windows, sample them denser, and stop wasting capture on the midday.
Boss explicitly cleared the build. Bubba elected BOTH levers (selection + capture)
with a veto offer.

## The two windows (Boss spec, America/New_York)
1. **Morning: sunrise → 09:00** — sunrise computed DYNAMICALLY for the farm
   (41.7558 N, 71.9789 W) so it tracks the season. No hardcoded morning hour.
2. **Evening: 19:30 → 20:30.**
Everything outside these two windows is dropped from the reel.

## Scope guardrails (do NOT touch)
- s7-cam (nesting box), mba-cam (brooder): birds consistently present — keep full behavior.
- gwtc: keeps its legacy single daylight window.
- house-yard PTZ (192.168.0.88): the ONLY Stage-0 predator-motion source. Untouched.
- Only usb-cam + dominator-cam (both `type=fixed` secondary cams, `detection_enabled=false`,
  no hardware-motion events) are in scope. Trimming their capture is safe for predator alerting.

## Architecture note (important — differs from the original brief)
The reel raw frames are written by the **pipeline orchestrator's**
`_run_raw_camera_thread` (`cycle_seconds` in `tools/pipeline/config.json`), NOT the
guardian root `config.json` `snapshot_interval` (which only feeds the in-memory
dashboard ring buffer for these cams). So Lever 2 lives in the orchestrator. Also:
these lanes are `vlm_bypass` — there is **no VLM bird-judge gate** in this path;
selection is laplacian-sharpness-ranked over raw frames (and `laplacian_var` is
currently NULL on these rows, so the per-bucket pick degrades to "latest in bucket").
The window itself is the bird proxy — and it works because birds ARE active at
dawn/dusk. No gate was fabricated.

## Lever 1 — reel selection (PRIMARY)
- New shared module `tools/pipeline/golden_windows.py`: NOAA sunrise/sunset (stdlib
  only; `astral` absent), `minute_in_window` primitive (minute granularity, supports
  midnight wrap), `is_dt_in_golden_windows`, camera gating helpers. Sunrise cached per
  (date, lat, lon, tz).
- `ig_selection.select_timelapse_gems`: for cameras in
  `instagram.scheduled.timelapse_golden_windows.cameras`, replace the single daylight
  window with the two configured windows; bucket by `sample_bucket_seconds` (30s) via
  new `_bucket_key_seconds`; cap at `max_frames`; chronological; `min_frames` gate.
- `_is_local_hour_in_window` refactored to delegate to `minute_in_window` (DRY).
- s7/mba/gwtc unaffected (not in the cameras list).

## Lever 2 — capture cadence (SECONDARY)
- `orchestrator._run_raw_camera_thread`: per-iteration cadence — **thick `cycle_seconds`
  in-window, sparse `offpeak_cycle_seconds` (180s) off-peak** — for cams that opt in via
  the same golden config AND set `offpeak_cycle_seconds`. Window calc shares the golden
  config (single source of truth). Errors fall back to thick (never stops capture).
- Mirrors the house-yard `night_snapshot_interval` precedent.
- usb-cam/dominator only; other raw cams unchanged.

## Config schema (`tools/pipeline/config.json` → `instagram.scheduled`)
```json
"timelapse_golden_windows": {
  "enabled": false,                       // master gate for BOTH levers
  "cameras": ["usb-cam", "dominator-cam"],
  "timezone": "America/New_York",
  "latitude": 41.7558, "longitude": -71.9789,
  "windows": [ {"start":"sunrise","end":"09:00"}, {"start":"19:30","end":"20:30"} ],
  "sample_bucket_seconds": 30,            // density knob (~1 frame/30s in-window)
  "max_frames": 90, "min_frames": 6,
  "per_camera": {}                         // per-lane overrides go here
}
```
Plus `"offpeak_cycle_seconds": 180` on the usb-cam and dominator-cam camera blocks
(their `cycle_seconds` left untouched). usb-cam is KEPT in
`timelapse_reel_daylight_only_cameras`: golden takes precedence in code when the
block is enabled (`if use_golden: ... elif daylight_only:`), and when golden is
disabled usb-cam falls back to its original daylight 06:00–20:00 reel rather than
an all-hours (incl. night) reel — i.e. `enabled` is a true clean toggle.
dominator-cam was never daylight-filtered, so its off-state fallback is its
original full-24h behavior.

## Density decision / known constraint
Shipped the **window-cut as the safe core** at the global `seconds_per_frame=1.0`
(reel ≈ 76.6s at 90 frames — proven stitch path, same as the daily reel). The literal
"1 frame / 20–30s of in-window time" (~540 frames) would need a per-lane
`seconds_per_frame` < 1 for a watchable fast-timelapse (and a matching smaller
crossfade, since the stitcher asserts `crossfade < spf`). Left as a one-knob follow-up
(`sample_bucket_seconds` + `max_frames` are already config; per-lane spf is the next
step) rather than risk the stitcher's crossfade assert on an unattended auto-post.

## Blast-radius (verified)
The two reel lanes are the SOLE consumers of these cams' raw frames. Carousel/story/
daily/weekly all require `discord_reactions >= 1` (weekly also `share_worth='strong'`),
which raw frames never have; both cams are in `gem_poster._GEM_POST_DISABLED_CAMERAS`.
Predator detection untouched (`detection_enabled=false`, `type=fixed`, no motion events;
.88 not touched). Off-peak trim starves nothing.

## Verification evidence
- Sunrise: 05:13 ET (2026-06-14), 07:12 ET (winter solstice) — matches reference; DST-correct.
- Selection: both cams return 90 frames, ALL inside golden windows, chronological,
  morning starts at 05:13:29 (= computed sunrise), zero midday frames.
- Stitch: both lanes → valid 1920×1080 H.264, 76.6s.
- Bird presence: sampled frames bird-bearing in the active morning window; post-sunset
  evening tail (after ~20:24) thins as birds roost (Boss-visible caveat).
- Lever 2 cadence flips thick/sparse correctly at every boundary; gating keeps
  s7/house-yard out.
- `test_golden_windows` (24 tests) + existing `test_ig_selection_timelapse` pass.

## Activation & rollback
- Gated by `timelapse_golden_windows.enabled`. Set `false` during tonight's 21:00/21:15
  cuts so they run OLD behavior (per brief). Flip `true` + one `launchctl kickstart -k`
  of `com.farmguardian.pipeline` after tonight's cuts → live for tomorrow's 21:00 reel
  (Lever 1 auto-picks-up on the fresh per-run reel script; Lever 2 needs the one restart).
- Rollback: set `enabled:false` (instant no-op) or restore the timestamped
  `*.bak.golden.20260614-202802` backups of config.json / ig_selection.py /
  orchestrator.py and restart the pipeline once.
