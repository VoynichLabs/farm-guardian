# 22-Jul-2026 — Three fixed daily camera reels (kill camera-of-the-day)

## Goal (Boss's words)

Three time-lapse Reels every single day, one per camera, from the two Reolink
cameras and the S7. Each reel is ONE camera — no combining/stitching cameras
together. Faster playback: more frames, each shown for less time. Built from
the previous day's frames.

## Scope

**In:**
- house-yard (Reolink E1 PTZ) → its own daily reel (was never scheduled).
- duo2 (Reolink Duo 2) → its own daily reel (was only posting via the rotation).
- s7-cam → its own daily reel (already running; unchanged).
- Faster reels (shorter per-frame hold, more frames).
- Disable everything that made a reel from mba-cam, usb-cam, dominator-cam, and
  the camera-of-the-day rotation.

**Out:**
- No cross-camera combined reel (explicitly rejected).
- No changes to the 18:00 mixed reaction-gated `ig-daily-reel` or the
  `ig-s7-backlog-reel` — flagged to Boss as a separate decision.
- mba-cam/usb-cam/dominator hardware fixes (offline/not aimed at birds).

## Why the rotation had to go

`com.farmguardian.ig-camera-of-the-day-reel` (v2.48.x) picked ONE camera per
day out of a pool (mba/usb/dominator/duo2) by day-of-year. That benched most
cameras daily and is the reason duo2 only posted ~1 day in 4. Boss wants each
camera to post daily on its own — the rotation is the opposite of that.

## Changes made (scheduling + config only, no code)

1. **house-yard plist created** — `~/Library/LaunchAgents/com.farmguardian.ig-house-yard-cam-timelapse-reel.plist`,
   runs 09:00 local, points at the existing shim `scripts/ig-house-yard-cam-timelapse-reel.py`
   → `HOUSE_YARD_CAM_TIMELAPSE_LANE`.
2. **duo2 reactivated** — renamed off `.disabled`, loaded. Runs 18:00 local.
3. **camera-of-the-day disabled** — booted out, renamed
   `.plist.disabled-22jul2026`.
4. **config faster playback** — `tools/pipeline/config.json`:
   - `reels.seconds_per_frame` 1.0 → 0.4
   - `instagram.scheduled.timelapse_reel_max_frames` 60 → 90
   - crossfade stays 0.15 (< 0.4 spf, satisfies stitcher guard).

Post times (Boss-chosen): house-yard 09:00 · s7 12:00 · duo2 18:00. (Plus the
still-running 12:30 carousel and 18:00 mixed reel — see open item.)

## Verification

- Config valid JSON.
- Dry-run frame selection on `data/guardian.db`: house-yard 90, duo2 90 — both
  ≥ 6-frame minimum, both WILL POST.
- `launchctl list` shows all three lanes loaded:
  `ig-house-yard-cam-timelapse-reel`, `ig-duo2-timelapse-reel`,
  `ig-s7-daily-reel`.

## Docs/changelog touchpoints

- CHANGELOG v2.50.0.
- This plan.
- SOCIAL_MEDIA_MAP.md / CLAUDE.md reel-lane references should be updated on the
  next pass (they still describe the rotation as live).

## Open item for Boss

The 18:00 mixed reaction-gated Reel (`ig-daily-reel`) and `ig-s7-backlog-reel`
are separate surfaces, not per-camera timelapses. Left running pending Boss's
yes/no on whether they stay, since he asked for exactly three reels.
