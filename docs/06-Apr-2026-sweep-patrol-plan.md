# Sweep Patrol Plan — 06-Apr-2026

## Goal

Replace the fixed preset-hopping patrol with a continuous sweep that scans everything the PTZ camera can physically see, minus its own mounting point.

## Scope

**In scope:**
- Continuous serpentine raster scan (pan full range, shift tilt, reverse)
- Position polling via reolink_aio readback
- Configurable dead zone to skip mount point
- Deterrent pause/resume integration (same as before)
- Config-driven speed, tilt range, stall detection
- Legacy preset patrol still available via `patrol_mode: "preset"`

**Out of scope:**
- Detection-weighted patrol (future — bias sweep toward hot zones)
- Time-of-day awareness (future — different speeds for day/night)
- eBird-reactive patrol (future — sky-watch bias on raptor advisory)
- Multi-camera sweep coordination

## Architecture

### New module: `patrol.py`

`SweepPatrol` class — the sweep brain. Takes a `CameraController`, camera ID, and config. Runs a blocking loop on a dedicated thread (same pattern as the old `start_patrol`).

**Sweep algorithm:**
1. Reset zoom to wide (0)
2. Move tilt to starting row
3. Pan in current direction at configured speed
4. Poll position every ~1s
5. When pan stalls (position unchanged for N polls) → hit physical limit → stop
6. If entering dead zone → rush through at high speed
7. Brief dwell at edge
8. Reverse pan direction, advance tilt row
9. When all tilt rows done in one direction → reverse tilt direction
10. Repeat forever

**Tilt positioning:** Since there's no absolute tilt command, uses a poll-and-nudge loop — short tilt bursts with position checks until within 3 degrees of target.

### Modified: `camera_control.py`

Added position-reading wrappers:
- `get_pan_position()` / `get_tilt_position()` / `get_position()` — refresh position state from camera, return current values
- `get_zoom()` / `set_zoom()` — read/write absolute zoom

### Modified: `guardian.py`

Patrol startup now checks `ptz.patrol_mode`:
- `"sweep"` → creates `SweepPatrol`, runs on thread
- `"preset"` → uses existing `start_patrol()` with hardware presets

### Config: `ptz.sweep` block

| Key | Default | Purpose |
|-----|---------|---------|
| `pan_speed` | 15 | Pan speed (0-255, low = thorough) |
| `tilt_speed` | 10 | Tilt nudge speed |
| `tilt_steps` | 3 | Number of horizontal scan rows |
| `tilt_min` | 5 | Lowest tilt angle (near horizon) |
| `tilt_max` | 60 | Highest tilt angle (looking down) |
| `position_poll_interval` | 1.0s | How often to check camera position |
| `stall_threshold` | 3 | Polls with no movement before declaring stall |
| `dead_zone_pan` | null | `[start, end]` pan range to skip (wraps 360) |
| `dead_zone_skip_speed` | 60 | Speed when rushing through dead zone |
| `dwell_at_edge` | 2.0s | Pause at pan limits before reversing |

## Tuning Notes

- `dead_zone_pan` starts as `null` (no exclusion). Set once you can see the camera output and identify the mount point angles.
- `pan_speed: 15` is deliberately slow for thorough coverage. Increase if sweeps take too long.
- `tilt_steps: 3` gives 3 horizontal rows across the tilt range. Increase for finer vertical coverage.
- `stall_threshold: 3` means 3 seconds of no movement triggers a reversal. The E1 Outdoor Pro may need tuning here depending on how fast it reports position changes.

## Verification

1. `patrol_enabled: true`, `patrol_mode: "sweep"` in config
2. `python guardian.py --debug`
3. Debug logs show position readbacks, direction changes, tilt shifts
4. Camera physically sweeps back and forth
5. Tune `dead_zone_pan` based on what you see
6. Test deterrent pause: trigger detection → patrol pauses → resumes after
