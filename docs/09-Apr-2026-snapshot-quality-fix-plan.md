# Plan: Snapshot Quality, Fixed-Position Sky Watch & Alert Image Fix

**Date:** 09-April-2026
**Author:** Bubba (Claude Opus 4.6)
**Status:** Ready for coding agent

---

## Context — Why This Matters

**Birdadette (Speckled Sussex hen) was taken by a hawk on 08-April-2026.** The remaining flock is 5 birds: one rooster, one speckled hen, and three gray/lavender pullets. Hawk predation is the #1 threat. Guardian needs to watch the sky and alert when raptors are overhead.

## Problem Summary

Three issues need fixing:
1. Detection alert images posted to Discord are consistently blurry and out of focus
2. The camera should be in a **fixed position** aimed to cover both yard and sky — patrol mode is excessive and unnecessary for this use case
3. We need to assess what tilt angle gives the best sky coverage for hawk detection while still seeing the yard

---

## Root Cause Analysis (Confirmed 09-Apr-2026)

### 1. RTSP Stream Frames Are Blurry

The detection pipeline uses `capture.py` to grab frames from the RTSP stream at ~1fps. These frames are:
- Grabbed continuously without triggering autofocus
- Downscaled from 4K to 1080p (`_TARGET_WIDTH = 1920` in `capture.py`)
- Often captured during or immediately after PTZ movement, before the motorized lens settles

The Reolink E1 Outdoor Pro has a **motorized autofocus lens** that needs 2-3 seconds to lock after any movement. The RTSP stream doesn't wait — it just delivers whatever the sensor sees, blurry or not.

### 2. Alert Snapshots Use RTSP Buffer Frames

`alerts.py` receives the detection frame (from `capture.py`'s RTSP buffer) and posts it directly to Discord. It does NOT use the camera's HTTP snapshot API, which produces sharp 4K images.

**Evidence:**
- Direct HTTP snap (`/cgi-bin/api.cgi?cmd=Snap`) → 4K, 330KB, sharp
- Guardian API snap (`/api/v1/cameras/house-yard/snapshot` via `camera_control.take_snapshot()`) → 4K, 321KB, sharp
- RTSP buffer frame (`/api/cameras/house-yard/frame` via dashboard) → 1080p, 69KB, corrupted/blurry
- Detection event snapshots in `events/2026-04-09/` → 1080p, ~235KB, blurry

### 3. Dashboard RTSP Decode Is Corrupted

The dashboard's `/api/cameras/{name}/frame` endpoint serves frames from the RTSP capture buffer. These frames show:
- Right half completely white/blank
- Magenta/pink digital artifacts in the bottom half
- Data corruption suggesting HEVC decode issues over WiFi

This is separate from the focus issue but compounds the image quality problem.

---

## Proposed Fixes

### Fix 1: Alert Images — Use Direct Camera Snapshot (HIGH PRIORITY)

**File:** `alerts.py`

When posting a detection alert to Discord, instead of using the RTSP buffer frame:
1. Call `camera_control.take_snapshot(camera_id)` to get a fresh 4K JPEG from the camera's HTTP API
2. Draw bounding boxes on the 4K image (scale detection coordinates from 1080p to 4K)
3. Post the 4K annotated image to Discord

**Why:** The camera's HTTP snapshot API consistently produces sharp, focused images regardless of RTSP stream state.

**Change scope:**
- `alerts.py` needs access to the `CameraController` instance (currently only has the frame from capture)
- `_encode_snapshot()` needs to handle coordinate scaling if detection was done on 1080p but snapshot is 4K
- Fallback: if `take_snapshot()` fails, fall back to the RTSP buffer frame (current behavior)

### Fix 2: Fixed-Position Sky Watch Mode (HIGH PRIORITY)

**Concept:** Instead of patrol (which sweeps through 11 positions), the camera should sit in ONE optimal position that covers both the yard and as much sky as possible. This is the primary operating mode.

**Tasks:**
1. Determine the optimal pan angle — likely ~180° (house view, where the chickens are)
2. Assess tilt angles: take snapshots at several tilt values to find the sweet spot that shows both yard and sky overhead
3. Save this as a preset (e.g., preset 0 = "sky-watch")
4. On Guardian startup, go to this preset and stay there
5. Patrol should remain disabled by default — it's overkill for a fixed homestead camera

**Tilt Assessment Needed:**
- Take snapshots at tilt values from current (28 = level) stepping up toward the sky
- Find the angle where you can still see the yard/chickens in the lower half but get maximum sky in the upper half
- Hawks approach from above — we need sky coverage to catch them before they dive
- Document the optimal angle in the plan for the coding agent

### Fix 3: RTSP Stream Decode Issues (LOW PRIORITY — INVESTIGATE)

**File:** `capture.py`

The corrupted frames (white half, magenta artifacts) suggest HEVC decode problems. Possible causes:
- WiFi packet loss on HEVC stream (already using TCP transport, but still happening)
- OpenCV FFMPEG backend struggling with 4K HEVC
- Buffer underrun in the capture loop

Investigation needed:
- Check if switching to the camera's substream (lower resolution but H.264) produces cleaner frames
- Check if the RTSP substream plan in `docs/08-Apr-2026-rtsp-substream-plan.md` addresses this
- Consider using the main stream for snapshots only and substream for continuous capture

---

## Current State (as of 09-Apr-2026 12:45 PM EDT)

- **Guardian process:** Running (PID 15848, single clean process)
- **Detection:** DISABLED on house-yard (set `detection_enabled: false` in config.json)
- **Patrol:** DISABLED (`patrol_enabled: false` in config.json)
- **Nesting box detection:** Already disabled
- **Cloud branch:** Merged into main, pushed to origin (commit `b1c7812`)
- **Dashboard:** Live at `http://192.168.0.105:6530`
- **Stray processes:** Killed one orphaned G0DM0D3 research script

## Files to Modify

| File | Change | Priority |
|------|--------|----------|
| `alerts.py` | Use `camera_control.take_snapshot()` for alert images instead of RTSP frame | HIGH |
| `guardian.py` | Pass `CameraController` reference to `AlertManager` | HIGH |
| `guardian.py` | On startup, go to sky-watch preset and stay there (no patrol) | HIGH |
| `config.json` | Add `sky_watch_preset` setting (preset ID for fixed position) | HIGH |
| `capture.py` | Investigate HEVC decode corruption on RTSP stream | LOW |

## Acceptance Criteria

1. Discord alert images are sharp and in focus (4K from HTTP snapshot API)
2. Bounding boxes still render correctly on alert images (coordinate scaling works)
3. If HTTP snapshot fails, falls back gracefully to RTSP frame
4. No regression in detection pipeline performance
5. Guardian restart required after code changes — restart cleanly with `python guardian.py`

---

## Notes for Coding Agent

- Camera HTTP snapshot API: `host.get_snapshot(channel)` via reolink_aio in `camera_control.py`
- The `CameraController` instance lives in `guardian.py` as `self._camera_ctrl`
- `AlertManager` is initialized in `guardian.py` as `self._alert_manager`
- Detection runs on 1080p frames (downscaled in `capture.py`), so bounding box coordinates are 1080p-relative
- If using 4K snapshot for alerts, scale bounding boxes by 2x (1920→3840, 1080→2160)
- The `take_snapshot()` method is synchronous (runs async in background thread) — safe to call from alert thread
- Config file: `config.json` — detection_enabled per camera, patrol_enabled global
- Test by re-enabling detection on house-yard and triggering a detection (any bird/animal should do)
