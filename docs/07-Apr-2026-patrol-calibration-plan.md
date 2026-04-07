# Patrol Calibration — Fix Sweep for Reolink E1 Outdoor Pro

**Date:** 07-Apr-2026
**Goal:** Fix the sweep patrol to work correctly with the Reolink E1 Outdoor Pro's actual coordinate system, skip the mounting post, and start from a useful position.

## Problem

The sweep patrol was written assuming degree-based coordinates. The Reolink E1 Outdoor Pro reports pan in its own unit scale (0–7240 for ~355°). Tilt position readback is broken (always returns 945). The camera's default/home position is pan≈0, which stares directly at its own mounting post. The patrol has no dead zone configured, no sensible start position, and the tilt positioning logic can never work because it relies on position feedback that doesn't exist.

## Findings (measured via reolink_aio)

| Axis | Range | Notes |
|------|-------|-------|
| Pan  | 0–7240 | ~20.4 units/degree. Wraps near 355°. Readback works but is laggy (2-8s delay). |
| Tilt | always 945 | Commands work physically, but `GetPtzCurPos` never updates tilt. |
| Home | pan≈0 | Boot default. Stares at the mounting post. |
| Post dead zone | pan≈0 + pan≈7240 | Post is directly behind the mount. Narrow obstruction. |
| Opposite of post | pan≈3620 | Best "home" position — faces away from the post. |

## Scope

**In:**
- Update `patrol.py` sweep config defaults to use Reolink unit scale (0–7240) instead of degrees
- Set dead zone around the mounting post (near pan 0 / 7240 wrap point)
- Add a `start_pan` config so patrol begins facing away from the post (~3620)
- Replace tilt poll-and-nudge with timed tilt bursts (since readback doesn't work)
- Update `config.json` sweep section with calibrated values
- Update CHANGELOG

**Out:**
- No changes to camera_control.py (the API works fine, the position data is what it is)
- No changes to deterrent integration (pause/resume is unaffected)
- No hardware changes

## Plan

### Step 1: Update config.json sweep values

```json
"sweep": {
    "pan_speed": 15,
    "tilt_speed": 10,
    "tilt_steps": 3,
    "tilt_burst_seconds": 1.5,
    "position_poll_interval": 1.0,
    "stall_threshold": 3,
    "start_pan": 3620,
    "dead_zone_pan": [6800, 440],
    "dead_zone_skip_speed": 60,
    "dwell_at_edge": 2.0
}
```

- `start_pan: 3620` — opposite of the mounting post
- `dead_zone_pan: [6800, 440]` — wrap-around zone: pan values from 6800 through 0 to 440 are behind the post (~30° window). The `_in_dead_zone` method already handles wrap-around.
- Remove `tilt_min` / `tilt_max` — they were degree-based and useless without readback

### Step 2: Update patrol.py

1. **Start position**: On patrol start, pan to `start_pan` using timed movement. Pan left/right from current position toward the start point. Since readback works for pan, we can poll-and-approach.

2. **Remove tilt poll-and-nudge**: Replace `_move_tilt_to_row()` with `_tilt_burst()` — a short timed movement (up or down) between pan sweeps. No position feedback, just a fixed-duration burst that shifts the camera slightly. Direction alternates each pass.

3. **Stall detection**: Keep `abs(diff) < 0.5` — this actually works because the laggy readback returns identical values when stalled. The lag means we see the same number for several polls, which is the stall we're detecting. No change needed.

4. **Dead zone**: Already implemented, just needs config values set.

### Step 3: Verify

- Patrol starts facing away from the post
- Camera sweeps pan range, speeds through the post dead zone
- Tilt shifts between passes (won't be precise, but covers more area)
- No stalling/jerking at edges

## Key Files

- `patrol.py` — sweep logic
- `config.json` — sweep config values
- `config.example.json` — matching example config
