# AGENTS_CAMERA.md — Reolink Camera Operations for Farm Guardian

**Read this entire file before touching the camera.** Every mistake documented here was made by a real assistant in a real session. The next one will make them again if they skim this.

---

## The Camera

**Reolink E1 Outdoor Pro** — 4K PTZ WiFi camera mounted on a wooden post in the yard.
On-screen display name / Reolink app name: **`FarmGuardian1`** (Boss says "FarmGuardian One").
That string is `cameras[0].device_name` in `config.json` and is burned into the bottom-right of
every frame — it is how you confirm a snapshot came from this camera and not `duo2`.
- IP: `192.168.0.88` (local network)
- RTSP transport: TCP (HEVC over WiFi/UDP drops packets)
- Pan range: 0–7200 raw units (20 units per degree, 360° total)
- Tilt: readback is broken at many angles (returns 945). Values ~28 = level. Values 731–813 = pointed at ground.
- Zoom: 0 (widest) to 33 (max telephoto). **Currently 19 — deliberately, set by Boss. See "Current
  Pointing" below. Do not reset it to 0 and do not treat the old "always leave at 0" rule as live.**
- Autofocus: motorized lens. Must be triggered after movement. Takes 2–3 seconds to settle.

---

## 🔴 Current Pointing — set by Boss 06-Aug-2026. LEAVE IT ALONE.

Boss aimed this camera by hand in the Reolink app and it is **exactly where he wants it**. Verified
against the live camera 06-Aug-2026 20:09 EDT:

| | Value |
|---|---|
| Pan | `1885` raw = **94.2°** — stable, trustworthy |
| Tilt | `0` as reported — **not trustworthy, see below** |
| Zoom | reported `19` in daylight, `27` after dark — **not trustworthy, see below** |

Pan was read four times over ~30 minutes and never moved. **The framing never moved either** —
verified by eye against snapshots taken 26 minutes apart.

**⚠️ Only pan is trustworthy on this camera. Tilt and zoom readback both lie.**

- **Tilt** returns 945 at many angles (see the spec at the top of this file). The `0` above is what
  the API said, not a verified mechanical position.
- **Zoom** drifts on its own with no command sent. Measured 06-Aug-2026: `19` in daylight at 20:09,
  `27` at 20:35 after the camera switched to night IR — while two snapshots either side of the
  change are **identically framed**, and `guardian.log` shows no PTZ or zoom command in between.
  `GetZoomFocus` (camera ground truth, not a Guardian cache) reports the same 27. The likely cause
  is the IR-cut/night focus shift moving the shared zoom-focus carriage. **An earlier version of
  this note recorded `19` as the canonical zoom and told you to preserve it — that was wrong, and
  a future agent who "restores zoom to 19" would be moving a lens that was never out of place.**

**Judge this camera's aim by the picture, not by the numbers.** Pull `/snapshot` and compare
against the description below. A changed zoom or tilt readback on its own is not evidence the
camera moved.

This is also the whole reason the aim is stored as a camera-side preset (below) rather than as
three numbers in this doc: the preset holds the real mechanical position, and this firmware has no
absolute "go to tilt=X" command to dial one in by hand anyway.

**What it sees at this position:** the Birdcatraz compound, framed across the middle third with the
lawn filling the bottom half. Left edge: the pink-tarped run with its wire panels and a hanging
feeder. Centre: the cinderblock/plywood shed with its open wire pen and shade frame. Right: a tall
row of sunflowers, then the garden with green step-in posts and poultry netting. Treeline behind.
A low green electric-poultry-net fence runs across the middle of the shot.

**Nothing in this repo will move it.** Checked, all of it, on the live camera and in the code:

| Possible override | State | Why it can't move the camera |
|---|---|---|
| PTZ guard (auto-return-to-home) — the camera's own firmware | **OFF** | `GetPtzGuard` → `benable: 0`, **and `bexistPos: 0`** — guard is disabled *and* has no home position saved, so there is nothing for it to snap back to |
| Reolink AI auto-tracking | **OFF** | `GetAiCfg` → `aiTrack: 0`, `bSmartTrack: 0`. The camera will not pan to follow a person or animal |
| On-camera cruise / preset tour — configured in the Reolink app, invisible to any code search | **OFF** | `GetPtzPatrol` → all six slots (`cruise1`–`cruise6`) have `enable: 0`, `bOpen: 0`, `running: 0`, `preset: null`. **Check this one explicitly** — it lives entirely on the camera, so grepping this repo will never rule it out |
| Guardian sweep/preset patrol | **OFF** | `config.json` → `ptz.patrol_enabled: false`. `guardian.py:300` only starts a patrol thread when that is true |
| Sky-watch park-at-preset | **OFF** | `config.json` → `sky_watch.enabled: false`. When true, `guardian.py:277` fires `ptz_goto_preset` at startup |
| Deterrent engine | no PTZ | Every rule in `deterrent.response_rules` is `spotlight` / `siren` / `audio_alarm`. `deterrent.py` never touches PTZ |
| Scheduled jobs (LaunchAgents, `scripts/`, `tools/`) | none | The only scheduled job that touches this camera is `scripts/yard-diary-capture.py`, and it calls **`/snapshot`** and nothing else |

**⚠️ Two switches that WOULD move it — do not flip either without asking Boss:**

1. `ptz.patrol_enabled: true` — starts the sweep. It also runs `set_zoom(camera, 0)`
   (`patrol.py:124`), so it would **wipe the zoom 19 as well as the aim**.
2. `sky_watch.enabled: true` — jumps straight to preset id 1 on Guardian startup.

### The restore point — preset 5 `boss-birdcatraz-aim`

**Boss's aim is saved as camera preset id 5, name `boss-birdcatraz-aim`** (saved 06-Aug-2026,
confirmed present in `GetPtzPreset` read straight off the camera). If anything ever knocks this
camera off its aim, that is the way back:

```bash
curl -s -X POST http://localhost:6530/api/v1/cameras/house-yard/preset/goto \
  -H 'Content-Type: application/json' -d '{"id": 5}'
```

**⚠️ Saved but never recall-tested.** Testing it would mean moving the camera off the aim it is
protecting, which was not worth doing on the night it was set. The write is confirmed against the
camera's own preset table, so the entry definitely exists — what is unproven is that recalling it
lands exactly back. **First person to actually need it: take a `/snapshot` before and after and
compare framing**, and record the result here.

**The other five presets are stale April-2026 aims — ids 0–4: `yard-center`, `coop-approach`,
`fence-line`, `sky-watch`, `driveway`. None of them is this position.** They are harmless because
nothing recalls them, but **any `preset/goto` with an id other than 5 throws away Boss's framing.**

**Note the `ptz.presets[]` array in `config.json` is NOT these presets.** That array holds
pan/tilt/zoom *degrees* for the legacy `patrol_mode: "preset"` path only; the real presets live on
the camera and are addressed by id. Do not try to reconcile the two.

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

⚠️ **This table was mapped in April 2026 at zoom 0. The camera is now at 94.2° / zoom 19 (see
"Current Pointing" above), so the framing at any given angle is tighter than these notes describe.
Treat everything below as approximate bearings, not as what you would see today.**

| Pan (degrees) | Pan (raw) | Location | Key Details |
|---------------|-----------|----------|-------------|
| 0° / 360° | 0 / 7200 | **DEAD ZONE** | Wooden mounting post blocks ~40% of frame. Useless. Dead zone config: pan 340°–22°. |
| **94.2°** | **1885** | **BIRDCATRAZ — the live aim** | Coop run under the pink tarp (left), cinderblock/plywood shed and its wire pen (centre), sunflower row and netted garden (right), treeline behind, lawn across the bottom. **This is where the flock is, and it is where Boss wants the camera.** At zoom 0 this same bearing was described only as "yard / hillside … pink tarp edge" — at that zoom the coop sat small in a much wider frame, so the April note never named it. |
| ~180° | ~3600 | The house | Two-story house with upper deck, dark truck in driveway, green lawn, chicken coop (wire enclosure) on right side. **Was the "most important angle" under the April aim — it is not any more.** |
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
body = [{"cmd": "SetPtzPreset", "action": 0, "param": {"PtzPreset": {
    "channel": 0,
    "enable": 1,
    "id": 5,
    "name": "boss-birdcatraz-aim"
}}}]
self._run_async(host.send_setting(body))
```

This is the pattern for any command the library doesn't expose. Construct the raw JSON body and call `send_setting()`.

**🔴 The body above changed on 06-Aug-2026. This doc previously told you to send
`{"cmd": "PtzCtrl", "op": "setPos", "id": …, "name": …}` — that does not work.** The camera answers
`param error / rspCode -4` and saves nothing. Verified against the live camera; `SetPtzPreset`
returns `rspCode 200` and the preset then appears in `GetPtzPreset`.

**It went unnoticed for four months because every layer reported success.** `send_setting()` does
raise `ApiError` on a non-zero code — but `camera_control._run_async()` catches *all* exceptions,
logs them at ERROR, and returns `None`; the old `ptz_save_preset()` then returned `True`
unconditionally, so `POST /preset/save` answered `{"ok": true}` over a camera that had saved
nothing.

**Two lessons that generalise beyond presets:**

1. **`_run_async()` swallows every async error.** Any method in `camera_control.py` that returns
   `True` right after a bare `self._run_async(...)` is reporting "I sent it", not "it worked" —
   that includes `disable_guard`, `set_zoom`, and `set_guard_position`. If a camera write matters,
   read the state back and check it. `ptz_save_preset()` now does exactly that.
2. **`get_presets()` used to serve a connect-time cache**, so a preset saved by the Reolink app or
   a direct curl stayed invisible to Guardian until a restart. It now refreshes via
   `get_state(cmd="GetPtzPreset")` before answering. If you are ever comparing what Guardian
   reports against what the camera holds, query `GetPtzPreset` directly to settle it.

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

**Patrol is OFF as of 06-Aug-2026** (`ptz.patrol_enabled: false`) and has been for a long time, so
there is nothing to fight right now. The rest of this section applies only if someone turns it back
on.

Guardian's step-and-dwell patrol moves the camera through 11 positions every ~2 minutes. If patrol is running and you send manual PTZ commands, patrol will override you on its next cycle (~8 seconds). It also forces zoom to 0 at startup and after every pause.

**You cannot win this fight.** If Mark wants manual camera control, patrol must be stopped on the Mac Mini first. You cannot stop it remotely — someone with local access must kill it or disable it in config.

---

## Responding to Mark's Commands

Mark messages from his phone while outside. He expects action, not questions.

⚠️ **The camera is parked on a hand-set aim Boss chose (94.2° / zoom 19). Any move below throws it
away and there is no saved preset to restore it.** Before moving, save the current position to a
free slot (`preset/save`, id ≥ 5) so you can put it back.

| Mark says | You do |
|-----------|--------|
| "pan left" / "pan right" | Short PTZ burst, report new position |
| "look at the house" | Preset goto id 0 — **note the presets are April-2026 aims and id 0 is named `yard-center`, not `house`**; check `/presets` and snapshot before trusting a name |
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

**⚠️ Addendum 06-Aug-2026 — the bypass was right, the command in it was wrong.** The `send_setting()`
approach is sound and still how we save presets. But the body that April session wrote
(`PtzCtrl` / `op: "setPos"`) never actually worked, and it was written up here as a solved problem
and copied into `camera_control.py`. The correct command is `SetPtzPreset` — see "How to bypass the
library" above. **The deeper mistake was declaring victory without reading the result back.** Nobody
listed `GetPtzPreset` afterwards to confirm a preset existed; the `{"ok": true}` was believed
instead. When you bypass a library, the camera's own state is the only thing that tells you whether
the bypass worked.

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
