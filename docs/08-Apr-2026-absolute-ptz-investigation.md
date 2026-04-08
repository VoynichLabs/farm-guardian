# Absolute PTZ Positioning Investigation — 08-Apr-2026

**Author:** Claude Opus 4.6 (remote session via Cloudflare tunnel)
**Date:** 08-April-2026
**Status:** Investigation complete — findings need verification by local session

---

## Problem

Remote camera control via move/stop commands is unreliable over the internet. The current approach is:

1. Send `ptz_move()` with a direction and speed
2. The camera starts moving continuously
3. Poll position with `get_position()`
4. Send `ptz_stop()` when close to target

This works locally with 0.3s polling loops, but over the Cloudflare tunnel, network latency makes it impossible to stop in time. Even speed 5 moves at ~85°/second — a 0.5s burst covers ~43°, and by the time the stop command arrives the camera has overshot.

**What we actually want:** Send "go to pan=3600, tilt=28" and have the camera move there on its own.

---

## What I Tried

### 1. Examined the reolink_aio library source (v0.19.1)

**File:** `venv/lib/python3.11/site-packages/reolink_aio/api.py`

- `set_ptz_command()` (line 4453) — the only method for sending PTZ commands. Constructs a `PtzCtrl` body with `"op"` set to a directional command string. Accepts optional `speed` and `preset` (as index).
- When `preset` is provided, `op` is hardcoded to `"ToPos"` and the preset index is sent as `"id"` in the params (line 4467-4489).
- The body shape sent to the camera is:
  ```json
  [{"cmd": "PtzCtrl", "action": 0, "param": {"channel": 0, "op": "ToPos", "id": <preset_index>, "speed": <int>}}]
  ```
- There are **no parameters for `Ppos` or `Tpos`** in the body construction.

**File:** `venv/lib/python3.11/site-packages/reolink_aio/enums.py`

- `PtzEnum` (line 99) — only directional commands: Stop, Left, Right, Up, Down, LeftUp, LeftDown, RightUp, RightDown, ZoomInc, ZoomDec, Auto. No absolute positioning command.

### 2. Checked position readback

The library CAN read absolute positions:

- `GetPtzCurPos` command returns `{"Ppos": <int>, "Tpos": <int>}` (line 4084)
- `ptz_pan_position()` returns `Ppos` (line 4497)
- `ptz_tilt_position()` returns `Tpos` (line 4501)
- Pan range: 0–7200 (20 units per degree, 360° total)

This asymmetry (read works, write doesn't) is what made me suspect the library was just missing the write method.

### 3. Checked how absolute zoom works (as a pattern)

Zoom DOES support absolute positioning:

- `set_zoom()` uses `StartZoomFocus` with `"op": "ZoomPos"` and `"pos": <int>` (line 4416-4422)
- This proves the camera firmware supports absolute positioning for SOME axes, just not pan/tilt.

### 4. Searched GitHub issues for reolink_aio

Found the definitive answer:

- **[Issue #147: PTZ - Set Absolute Pan / Tilt](https://github.com/starkillerOG/reolink_aio/issues/147)** — open feature request for exactly this capability.
- The library maintainer (starkillerOG) stated he has been asking Reolink firmware engineers to add an absolute pan/tilt setter "for quite some time" but they "have way too many things on their plate."
- **This is a firmware limitation, not a library limitation.** The camera simply does not accept absolute pan/tilt coordinates via its HTTP API.

### 5. Searched Reolink community forums and third-party API references

- [Reolink Community: CGI API PTZ position](https://community.reolink.com/topic/6014/cgi-api-ptz-position) — confirms no absolute positioning
- [Reolink Community: Camera API Setting Default Preset Location](https://community.reolink.com/topic/6770/camera-api-setting-default-preset-location) — discusses presets as the only workaround
- [nechry/ReolinkAPI PTZ Commands](https://github.com/nechry/ReolinkAPI/blob/main/Reolink_API_PTZ_Commands.sh) — bash examples showing only directional commands
- [reolink_aio Issue #10](https://github.com/starkillerOG/reolink_aio/issues/10) — Reolink command reference, no absolute position command listed

### 6. Verified our own codebase already knew this

The existing sweep patrol plan doc (`docs/06-Apr-2026-sweep-patrol-plan.md`) and CHANGELOG already document this limitation:

> "Uses continuous movement commands with position polling (reolink_aio has no absolute pan/tilt positioning)"

---

## Experimental Evidence from This Session

### Speed calibration (not documented in handoff)

| Test | Start | End | Duration | Speed | Degrees/sec |
|------|-------|-----|----------|-------|-------------|
| pan=-1 (left) | 362° (7240) | 276.6° (5532) | 1.0s | 5 | ~85°/s |
| pan=1 (right) | 77.8° (1556) | 362° (7240) | ~1.5s | 6 | ~190°/s |

**Speed 5 is NOT slow.** The handoff doc's advice that "speed 5-8 is slow for positioning" was calibrated for local Python with 0.3s polling. Over the internet, even speed 5 is uncontrollable — you can't react fast enough to stop.

### Move/stop burst approach (what works remotely)

Short bursts of 0.3-0.5 seconds with stop between each:

| Burst | Start | End | Moved |
|-------|-------|-----|-------|
| 0.5s left, speed 5 | 276.6° | 245.9° | ~31° |
| 0.5s left, speed 5 | 245.9° | 189.8° | ~56° |
| 0.2s left, speed 5 | 189.8° | 171.6° | ~18° |

This works but is slow, imprecise, and annoying. Each burst requires a full HTTP round-trip through Cloudflare.

---

## My Theory / Recommendation

### Presets are the right answer

The Reolink E1 supports up to 64 saved presets. A preset stores an absolute pan/tilt position on the camera itself. The `ToPos` command recalls a preset by ID and the camera moves there autonomously — no polling, no overshooting, no latency issues.

**Proposed approach:**

1. Save 4-6 presets on the camera using the Reolink app (or via API if `set_ptz_command` supports saving):
   - Preset 0: "house" — pan ~180° (3600), tilt ~28. Chickens and coop.
   - Preset 1: "yard" — pan ~90° (1800), tilt ~28. Hillside and fire pit.
   - Preset 2: "stable" — pan ~270° (5400), tilt ~28. Old stable foundation, property edge.
   - Preset 3: "sky" — pan ~180° (3600), tilt high. Hawk sky-watch over house.

2. Add a `POST /cameras/{id}/goto` API endpoint that accepts either:
   - `{"preset": "house"}` — recalls a named preset (instant, reliable)
   - `{"pan_degrees": 180, "tilt": 28}` — poll-and-nudge fallback for arbitrary positions

3. The world model becomes a simple lookup table. Any future assistant reads this doc, calls `goto("house")`, waits 3 seconds for autofocus, snaps. No guessing, no overshooting.

### UPDATE: Preset saving IS possible via the API

After Mark pushed back on the "use the Reolink app" suggestion (correctly — we ARE the Reolink app), I dug deeper into the library source and found the answer.

**The `PtzCtrl` command supports `op: "setPos"` to save the current camera position as a preset.** This is confirmed by:

1. `reolink_aio/enums.py` line 119: `GuardEnum` has `set = "setPos"` and `goto = "toPos"`
2. `reolink_aio/api.py` line 4064-4070: `GetPtzPreset` parses presets as `{enable, name, id}` — the camera stores them
3. `reolink_aio/baichuan/xmls.py` line 440-451: The Baichuan protocol has a `PtzPreset` XML template with `<command>toPos</command>` for recall — implying `setPos` exists for save

**The raw HTTP call to save a preset should be:**
```json
[{"cmd": "PtzCtrl", "action": 0, "param": {"channel": 0, "op": "setPos", "id": 0, "name": "house"}}]
```

This saves the camera's CURRENT position as preset 0 named "house". Then to recall:
```json
[{"cmd": "PtzCtrl", "action": 0, "param": {"channel": 0, "op": "ToPos", "id": 0}}]
```

**Why the library doesn't expose this:** The `set_ptz_command()` method validates commands against `PtzEnum` (line 4462-4464), which only contains directional commands. `"setPos"` is not in `PtzEnum` — it's in `GuardEnum`. So the library blocks it. But the camera firmware almost certainly accepts it if sent via raw `send_setting()`.

**The old `ptz_save_preset()` stub was removed from `camera_control.py`** because it "didn't work" — but it likely failed because it was calling `set_ptz_command()` which rejected `"setPos"` at the validation step. The fix is to bypass the library's validation and call `send_setting()` directly with the raw body.

### What Bubba needs to do

1. Add a `save_preset(camera_id, preset_id, name)` method to `camera_control.py` that calls `host.send_setting()` directly with the `PtzCtrl` / `setPos` body, bypassing `set_ptz_command()` validation.
2. Add a `goto_preset(camera_id, preset_name_or_id)` method — the library already supports this via `set_ptz_command(channel, preset=id)`, just needs a clean wrapper.
3. Add API endpoints: `POST /cameras/{id}/preset/save` and `POST /cameras/{id}/preset/goto`.
4. One-time setup: nudge camera to each key position, save as preset. Then any assistant can recall instantly.

### Additional angles I did NOT investigate (but Bubba should consider)

1. **ONVIF AbsoluteMove** — ONVIF Profile S defines `AbsoluteMove` with absolute pan/tilt/zoom coordinates. The `onvif-zeep` library in requirements.txt might support this, bypassing the Reolink HTTP API entirely. The camera advertises ONVIF support.
2. **Reolink CGI API** vs JSON API — different API surface, might have different commands.
3. **Baichuan protocol** — `reolink_aio` has a `baichuan` module that communicates via a binary protocol on port 9000. This might have capabilities the HTTP API doesn't.
4. **Newer firmware** — the reolink_aio issue #147 is from 2023. Reolink may have added absolute positioning in newer firmware. The camera's current firmware version should be checked.
5. **Home Assistant integrations** — the Reolink Home Assistant integration uses reolink_aio and may have workarounds documented in their community.

---

## Files Referenced

- `camera_control.py` — current PTZ control implementation (move/stop only)
- `api.py` — REST API endpoints (v2.7.0, no goto endpoint yet)
- `patrol.py` — step-and-dwell patrol (uses poll-and-nudge internally)
- `venv/lib/python3.11/site-packages/reolink_aio/api.py` — library source
- `venv/lib/python3.11/site-packages/reolink_aio/enums.py` — PtzEnum (directional only)
- `docs/06-Apr-2026-sweep-patrol-plan.md` — already documented this limitation
- `/tmp/camera_observations.md` — speed calibration data from this session
