# AGENTS_CAMERA.md — Reolink Camera Operations for Farm Guardian

**Read this entire file before touching the camera.** Every mistake documented here was made by a real assistant in a real session. The next one will make them again if they skim this.

---

## The Camera

**Reolink E1 Outdoor Pro** — 4K PTZ WiFi camera mounted on a wooden post in the yard.
- IP: `192.168.0.88` (local network)
- RTSP transport: TCP (HEVC over WiFi/UDP drops packets)
- Pan range: 0–7200 raw units (20 units per degree, 360° total)
- Tilt: readback is broken at many angles (returns 945). Values ~28 = level. Values 731–813 = pointed at ground.
- Zoom: 0 (widest) to 33 (max telephoto). **Always leave at 0. Zoom is out of scope.**
- Autofocus: motorized lens. Must be triggered after movement. Takes 2–3 seconds to settle.

---

## How to Talk to the Camera

The camera is just an HTTP server. Guardian wraps it with a REST API exposed via Cloudflare tunnel.

**Base URL:** `https://guardian.markbarney.net/api/v1`

Guardian must be running on the Mac Mini for any of this to work. If you get a 502, Guardian is down.

### Every Endpoint

| Endpoint | Method | Body | Returns |
|----------|--------|------|---------|
| `/cameras/house-yard/position` | GET | — | `{"camera_id":"house-yard","pan":3600,"pan_degrees":180.0,"tilt":28,"zoom":0}` |
| `/cameras/house-yard/snapshot` | GET | — | JPEG image bytes (`image/jpeg`) |
| `/cameras/house-yard/ptz` | POST | `{"action":"move","pan":1,"tilt":0,"speed":5}` | `{"ok":true,"action":"move"}` |
| `/cameras/house-yard/ptz` | POST | `{"action":"stop"}` | `{"ok":true,"action":"stop"}` |
| `/cameras/house-yard/autofocus` | POST | — | `{"ok":true}` |
| `/cameras/house-yard/zoom` | POST | `{"level":0}` | `{"ok":true,"zoom":0}` — **do not use** |
| `/cameras/house-yard/guard` | POST | `{"enabled":false}` | `{"ok":true,"guard_enabled":false}` |
| `/cameras/house-yard/spotlight` | POST | `{"on":true,"brightness":100}` | `{"ok":true}` |
| `/cameras/house-yard/siren` | POST | `{"duration":10}` | `{"ok":true,"duration":10}` |
| `/cameras/house-yard/presets` | GET | — | `{"camera_id":"house-yard","presets":{"house":0,"yard":1}}` |
| `/cameras/house-yard/preset/save` | POST | `{"id":0,"name":"house"}` | `{"ok":true,"preset_id":0,"name":"house"}` |
| `/cameras/house-yard/preset/goto` | POST | `{"id":0}` | `{"ok":true,"preset_id":0}` |

### PTZ move body — exact shape

```json
{"action": "move", "pan": 1, "tilt": 0, "speed": 5}
```
- `pan`: 1 = right (increasing pan values), -1 = left (decreasing)
- `tilt`: 1 = up, -1 = down
- `speed`: 1–64. **Use 5 for remote control.** Even 5 moves at ~85°/second.
- Diagonals work: `{"pan": 1, "tilt": 1}` = right + up

### Preset save body — exact shape

```json
{"id": 0, "name": "house"}
```
- `id`: 0–63. The camera supports up to 64 presets.
- `name`: descriptive string. Gets stored on the camera itself.
- Saves **the camera's current position**. Move first, then save.

### Preset goto body — exact shape

```json
{"id": 0}
```
- The camera moves itself autonomously to the saved position. No polling. No overshoot. Instant.

---

## How to Take a Snapshot (Do This Every Time)

```bash
# 1. Trigger autofocus
curl -s -X POST https://guardian.markbarney.net/api/v1/cameras/house-yard/autofocus

# 2. WAIT 3 SECONDS — the lens is motorized, it needs time
sleep 3

# 3. Take the snapshot (use --max-time 15, snapshots can be slow over the tunnel)
curl -s --max-time 15 https://guardian.markbarney.net/api/v1/cameras/house-yard/snapshot \
  --output /tmp/snap_descriptive_name.jpg

# 4. Read the image with the Read tool — you can see it, the user cannot
# 5. Describe what you see to the user in detail
```

**Never skip the 3-second wait.** Every blurry snapshot in this project's history was from skipping it.

**You cannot display images to the user.** They don't render in chat. You must describe what you see — landmarks, animals, objects, focus quality, changes from the previous snapshot.

---

## How to Move the Camera

### Method 1: Presets (preferred — always use this when available)

```bash
# Go to house view
curl -s -X POST https://guardian.markbarney.net/api/v1/cameras/house-yard/preset/goto \
  -H "Content-Type: application/json" -d '{"id": 0}'
# Wait 2 seconds for the camera to arrive, then do the snapshot procedure above
```

Check what presets exist first:
```bash
curl -s https://guardian.markbarney.net/api/v1/cameras/house-yard/presets
```

If presets are empty (`{}`), they haven't been saved yet. See the plan doc in `docs/` for preset setup procedures.

### Method 2: Manual nudge (fallback only — unreliable over the internet)

```bash
# Short burst: move 0.3-0.5 seconds, stop, check, repeat
curl -s -X POST https://guardian.markbarney.net/api/v1/cameras/house-yard/ptz \
  -H "Content-Type: application/json" -d '{"action":"move","pan":-1,"tilt":0,"speed":5}'
sleep 0.4
curl -s -X POST https://guardian.markbarney.net/api/v1/cameras/house-yard/ptz \
  -H "Content-Type: application/json" -d '{"action":"stop"}'
curl -s https://guardian.markbarney.net/api/v1/cameras/house-yard/position
# Check pan_degrees, repeat if not close enough
```

**Speed calibration (measured 08-Apr-2026):**

| Speed | Degrees per second | 0.5s burst covers |
|-------|-------------------|-------------------|
| 5 | ~85° | ~43° |
| 6 | ~130° | ~65° |
| 8 | ~170° | ~85° |

**Never sleep more than 0.5 seconds before stopping.** You will overshoot. Always stop, check position, then move again.

---

## World Model — What the Camera Sees

| Pan (degrees) | Pan (raw) | Location | Key Details |
|---------------|-----------|----------|-------------|
| 0° / 360° | 0 / 7200 | **DEAD ZONE** | Wooden mounting post blocks ~40% of frame. Useless. Dead zone config: pan 340°–22°. |
| ~90° | ~1800 | Yard / hillside | Green grass slope uphill, fire pit with stones, pink tarp edge, white unidentified object, bare treeline background |
| ~180° | ~3600 | **THE HOUSE** | Two-story house with upper deck, dark truck in driveway, green lawn, chicken coop (wire enclosure) on right side. **This is where the chickens are. Most important angle.** |
| ~270° | ~5400 | Old stable / property edge | Crumbling concrete foundation, cut wood stacked on it, Rose of Sharon bushes in rows (NOT trees), thin treeline boundary, neighbor's corn field beyond. Green chicken wire perimeter fencing. |

**Predator approach vectors:**
- Hawks: from above, any direction. Sky-watch (tilt up) matters.
- Ground predators (coyote, bobcat, fox): likely from treeline/property edges (90° and 270°), not from the house/driveway side.

**Accept Mark's corrections about the world model immediately.** He lives there. Previous assistants argued about what they saw — don't.

---

## The reolink_aio Library — What You Need to Know

**Location:** `venv/lib/python3.11/site-packages/reolink_aio/api.py` (~5000 lines)

### The library is a partial wrapper, not the full API

The camera's HTTP API accepts raw JSON commands. The `reolink_aio` library wraps some of them but not all. Where the library has gaps, you bypass it.

### What the library blocks (and shouldn't)

`set_ptz_command()` (line 4453) validates commands against `PtzEnum` (line 99 of `enums.py`):
```
Stop, Left, Right, Up, Down, LeftUp, LeftDown, RightUp, RightDown, ZoomInc, ZoomDec, Auto
```

Commands like `"setPos"` (save preset) are NOT in `PtzEnum`, so the library rejects them. But the camera firmware accepts them fine.

### How to bypass the library

`camera_control.py` has `ptz_save_preset()` which calls `host.send_setting()` directly:

```python
body = [{"cmd": "PtzCtrl", "action": 0, "param": {
    "channel": 0,
    "op": "setPos",
    "id": 0,
    "name": "house"
}}]
self._run_async(host.send_setting(body))
```

This is the pattern for any command the library doesn't expose. Construct the raw JSON body and call `send_setting()`.

### Key methods in the library (with line numbers)

| Method | Line | What it does |
|--------|------|-------------|
| `set_ptz_command()` | 4453 | Sends PTZ commands. Validates against PtzEnum. |
| `send_setting()` | 5699 | Sends raw JSON to camera. **Use this to bypass validation.** |
| `ptz_pan_position()` | 4495 | Returns current pan (raw units) from `_ptz_position[ch]["Ppos"]` |
| `ptz_tilt_position()` | 4499 | Returns current tilt from `_ptz_position[ch]["Tpos"]` |
| `get_state(cmd="GetPtzCurPos")` | — | Refreshes position data from camera before reading |
| `ptz_presets()` | 4426 | Returns dict of `{name: id}` for saved presets |
| `set_zoom()` | 4401 | Absolute zoom (0–33). Uses `StartZoomFocus` with `op: "ZoomPos"`. |
| `get_snapshot()` | — | Returns JPEG bytes |

### What the library reads vs writes

| Feature | Read | Write |
|---------|------|-------|
| Pan/tilt position | Yes (`GetPtzCurPos` → `Ppos`/`Tpos`) | **No** — firmware limitation, no absolute positioning |
| Zoom | Yes (`get_zoom`) | Yes (`set_zoom` — absolute) |
| Presets | Yes (`GetPtzPreset` → name/id) | **Bypassed** — `send_setting()` with `op: "setPos"` |
| Directional move | n/a | Yes (`set_ptz_command` with Left/Right/Up/Down) |

### The absolute positioning limitation

The Reolink firmware does NOT support "go to pan=X, tilt=Y". Confirmed by:
- reolink_aio maintainer, [issue #147](https://github.com/starkillerOG/reolink_aio/issues/147)
- Reolink community forums
- Our own testing

**Do not waste time trying to send absolute coordinates.** Use presets instead. This has been investigated thoroughly — see `docs/08-Apr-2026-absolute-ptz-investigation.md`.

---

## Patrol Conflict

Guardian's step-and-dwell patrol moves the camera through 11 positions every ~2 minutes. If patrol is running and you send manual PTZ commands, patrol will override you on its next cycle (~8 seconds).

**You cannot win this fight.** If Mark wants manual camera control, patrol must be stopped on the Mac Mini first. You cannot stop it remotely — someone with local access must kill it or disable it in config.

---

## Responding to Mark's Commands

Mark messages from his phone while outside. He expects action, not questions.

| Mark says | You do |
|-----------|--------|
| "pan left" / "pan right" | Short PTZ burst, report new position |
| "look at the house" | Preset goto "house" (id 0), snapshot, describe |
| "what do you see?" | Snapshot (with autofocus wait), describe in detail |
| "tilt up" / "tilt down" | Short tilt burst, report new position |
| "is it in focus?" | Snapshot, evaluate sharpness, report honestly |
| "stop" | `POST /ptz` with `{"action":"stop"}` immediately |

---

## Monitoring Mode

When Mark asks you to watch the camera:

1. **With every message he sends**, also run a camera check (you can't schedule these — no cron available remotely)
2. Read position, trigger autofocus, wait 3s, take snapshot
3. Name files sequentially: `snap_001_HHMM_panXXXdeg_tiltYY.jpg`
4. Log to `/tmp/camera_observations.md`: timestamp, position, focus quality, what you see, changes from last check
5. Alert Mark immediately if you see: animals, people, significant scene changes, camera problems

---

## Mistakes Previous Assistants Made (Learn From These)

### 1. "Absolute pan/tilt is impossible"
**What happened:** An assistant searched the web, found a GitHub issue, and declared it a firmware limitation. Then confidently told Mark to use the Reolink phone app to save presets.

**The truth:** The camera firmware doesn't support absolute pan/tilt coordinates — that part was correct. But preset saving IS supported via the same API we already use. The `reolink_aio` library just hadn't wired it up. The fix was to bypass the library with `send_setting()`. **Never tell Mark to use the Reolink app. We are the Reolink app.**

**Lesson:** Don't declare things impossible without reading the full library source. Don't trust GitHub issues as the final word — they might reflect library gaps, not firmware limitations.

### 2. "Speed 5-8 is slow for positioning"
**What happened:** The handoff doc said "use speed 5-8 for slow positioning." An assistant sent speed 6 with a 1.5-second sleep and overshot from 78° to 362° — nearly a full rotation.

**The truth:** Speed 5 moves at ~85°/second. The "slow" advice was calibrated for a local Python script polling every 0.3 seconds. Over the Cloudflare tunnel, network latency makes it impossible to react that fast.

**Lesson:** For remote control, use 0.3–0.5 second bursts maximum. Stop, check position, move again. Or better — use presets.

### 3. Skipping the autofocus wait
**What happened:** Multiple assistants took snapshots immediately after moving the camera. Every image was blurry.

**The truth:** The Reolink E1 has a motorized lens that physically moves to focus. After any PTZ movement, it needs 2–3 seconds to recalculate and adjust. There is no shortcut.

**Lesson:** Always trigger autofocus, always wait 3 seconds, then snapshot. Every time. No exceptions.

### 4. Fighting the patrol
**What happened:** An assistant tried to manually position the camera while Guardian's step-and-dwell patrol was running. The patrol moved the camera every 8 seconds, undoing every manual command.

**The truth:** Patrol runs in Guardian's process and sends PTZ commands on a timer. Manual commands via the API go to the same camera. They fight.

**Lesson:** If you need manual control, patrol must be stopped first. You cannot do this remotely.
