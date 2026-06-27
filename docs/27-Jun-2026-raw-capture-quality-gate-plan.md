# 27-Jun-2026 — Raw capture quality gate + duo2 daylight filter

## Scope

Fix two defects discovered during duo2 timelapse review.

**In:** `tools/pipeline/orchestrator.py`, `tools/pipeline/config.json`.
**Out:** VLM path, IG posting, Guardian detection — untouched.

## Problem 1: `laplacian_var = NULL` for every vlm_bypass camera

`run_raw_cycle` calls `store_raw()` with no `gate_metrics`, so every
vlm_bypass camera (house-yard, usb-cam, mba-cam, dominator-cam, gwtc,
duo2) has `laplacian_var = NULL` in `image_archive`. The timelapse
selector (`select_timelapse_gems`) picks the highest-laplacian frame per
bucket — but with all NULLs it picks randomly, defeating the entire
sharpness-based selection scheme.

## Problem 2: Corrupted grey frames from duo2 (~16% of captures)

The duo2 periodically emits frames with `p50=129, std≈6, lap<200` —
a grey noise pattern caused by the dual-lens stitch entering a bad state.
These should be rejected at capture time, not stored.

Investigation: `passes_exposure_gate` with the existing config value
`exposure_std_floor=15.0` rejects any frame with std < 15. The corrupted
frames have std≈6, so they fail this gate automatically. No new threshold
config needed.

## Problem 3: duo2 reel includes night IR frames

duo2 is not in `timelapse_reel_daylight_only_cameras`, so its nighttime
B&W IR frames (good quality, but visually jarring mid-color-reel) are
included. Fix: add "duo2" to the list.

## Architecture

### orchestrator.py — `run_raw_cycle`

After capture, before `store_raw`:
1. Decode JPEG → cv2 image.
2. `passes_trivial_gate` → std_dev check + compute metrics. Skip if blank.
3. `passes_exposure_gate` → rejects washed-out / flat frames (catches the grey corruption).
4. `passes_sharpness_gate` if `laplacian_floor > 0` in camera config (per-camera opt-in, same key as VLM path).
5. Pass `gate_metrics` to `store_raw`.

Mirrors the VLM path's gate chain. Cheap (milliseconds per frame).

### config.json

Add `"duo2"` to `timelapse_reel_daylight_only_cameras`.

## TODOs

- [x] Edit `run_raw_cycle` in `orchestrator.py`
- [x] Add duo2 to `timelapse_reel_daylight_only_cameras` in `tools/pipeline/config.json`
- [x] Update file headers + CHANGELOG (v2.44.1)
- [ ] Optional: one-time backfill script for existing NULL rows (not needed — they age out in 24h anyway)

## Verification

Restart pipeline, let it run one cycle, check `image_archive` for a duo2 row with non-NULL `laplacian_var`.
