# Camera Autofocus & PTZ Guard Fix — 08-Apr-2026

## Goal

Fix two hardware-level issues making the Reolink E1 Outdoor Pro camera feed useless:
1. Camera auto-returns to pan=0 (mounting post) during PTZ idle gaps
2. Camera never refocuses after zoom changes, producing blurry frames

## Scope

**In scope:**
- Disable PTZ guard (auto-return-to-home) on patrol startup
- Add autofocus trigger after zoom changes and patrol resume
- Clean up dead code and magic numbers in patrol.py
- Document Reolink E1 coordinate system

**Out of scope:**
- RTSP stream corruption (separate investigation)
- Detection pipeline tuning (deferred until camera produces usable video)
- Tilt raster implementation (tilt readback is broken on this camera; timed bursts are the only option)

## Root Causes

### Problem 1: Camera pointed at mounting post

The Reolink E1 has a **PTZ guard** feature (`SetPtzGuard` API). When enabled, the camera auto-returns to its saved guard position after a configurable timeout (default 60s) of PTZ inactivity. The default guard position is pan=0 — the mounting post.

During sweep patrol, there are gaps where no PTZ commands are active:
- `dwell_at_edge` (2s pause at pan limits)
- `_tilt_burst` (1.5s tilt + 0.3s settle)
- Between `ptz_stop` and next movement command

If any gap exceeds the guard timeout, the camera snaps back to pan=0.

**Fix:** Disable PTZ guard via `host.set_ptz_guard(ch, enable=False)` on patrol startup.

### Problem 2: Blurry feed

`patrol.py` calls `set_zoom(camera_id, 0)` on every start and resume but never triggers autofocus. The Reolink's motorized lens needs an explicit autofocus command after zoom changes to recalculate focus for the new field of view.

**Fix:** Call `host.set_autofocus(ch, True)` and cycle it off→on to force a fresh focus calculation.

## Architecture

### Modified: `camera_control.py`

New methods using reolink_aio API:
- `ensure_autofocus(camera_id)` → `host.set_autofocus(ch, True)`
- `trigger_autofocus(camera_id)` → cycles autofocus off→on to force recalculation
- `is_guard_enabled(camera_id)` → `host.ptz_guard_enabled(ch)`
- `disable_guard(camera_id)` → `host.set_ptz_guard(ch, enable=False)`
- `set_guard_position(camera_id)` → `host.set_ptz_guard(ch, command="setPos")`

Removed: `ptz_save_preset()` dead stub

### Modified: `patrol.py`

Startup sequence now:
1. Log current position diagnostic (pan in degrees, dead zone check)
2. Check and disable PTZ guard
3. Set zoom to 0 (wide angle)
4. Enable and trigger autofocus, wait 1s for lens to settle
5. Move to start_pan position

On deterrent resume: re-set zoom + trigger autofocus

Cleanup:
- Removed dead `tilt_steps` variable (read but never used)
- Replaced magic `200` tolerance → config `positioning_tolerance`
- Replaced magic `speed=40` → config `positioning_speed`
- Dead zone entry/exit logging upgraded DEBUG → INFO
- Added Reolink coordinate system docs throughout

### Modified: `config.example.json`

Added full `ptz.sweep` block with all settings including new fields.

## Config Changes

New optional fields in `ptz.sweep` (with defaults matching prior behavior):
- `positioning_tolerance: 200` — pan units considered "close enough" (~10°)
- `positioning_speed: 40` — speed for positioning moves

Dead field to remove from live config:
- `tilt_steps: 3` — was never used in patrol code

## Verification

1. Stop currently running guardian
2. Remove `tilt_steps` from live `config.json` sweep section
3. Start guardian with `--debug`
4. Check logs for:
   - "PTZ guard is enabled — disabling to prevent auto-return to mount post"
   - "Autofocus triggered for 'house-yard'"
   - Position diagnostic showing pan in degrees
5. Watch camera feed — should stay in focus and not return to mounting post
6. Wait through a full sweep cycle to confirm dead zone skip and tilt bursts work
7. Trigger a deterrent pause/resume and confirm autofocus re-triggers
