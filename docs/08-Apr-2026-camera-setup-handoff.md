# Camera Setup Handoff — 08-Apr-2026

**Purpose:** This document is a complete handoff for the next Claude session to continue helping Mark set up and calibrate his Reolink E1 Outdoor Pro PTZ camera. Read this entire document before doing anything.

---

## What Is Happening Right Now

**Guardian is RUNNING** (PID 11254, started 15:07, `--debug` mode). The step-and-dwell patrol is active — the camera is cycling through 11 positions at 30° intervals, dwelling 8 seconds at each. The API is live at `http://macmini:6530/api/v1/`.

Mark is going outside to physically fix the camera to its mounting post (it's wobbly). He wants to communicate with you from his phone while outside, telling you to pan/tilt the camera so he can verify positioning. He will also want you to report what you see through the camera — you are his eyes.

**Your job:** Be Mark's remote camera assistant. He will message you from outside saying things like "pan left", "look at the house", "tilt up", "what do you see?" — and you respond by controlling the camera hardware and showing him snapshots of what the camera sees.

### Ongoing monitoring task

Mark asked for the camera feed to be checked every 5 minutes for the next 2 hours. At each check:
1. Take a snapshot (via the API or direct Python)
2. Read the position (pan degrees, tilt, zoom)
3. Look at the image and describe what you see
4. Note whether the image is in focus
5. Note any changes from the previous snapshot (did Mark move the camera? did something enter the frame?)
6. Save snapshots with descriptive names: e.g., `snap_003_1520_pan180deg_tilt28.jpg`
7. Log all observations to `/tmp/camera_observations.md`

You can use a CronCreate job (every 5 minutes) or a scheduled task to automate this. The previous session had a cron job set up but it was session-only and died with the session.

**When Mark messages from outside**, respond to his commands immediately — don't wait for the next scheduled check. If he says "pan left", do it now. If he says "what do you see?", take a snapshot now and show him.

---

## How to Control the Camera

### Option A: REST API (works if Guardian is running — preferred for remote sessions)

Guardian is currently running. The API at `http://macmini:6530/api/v1/` has full camera control:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/cameras/house-yard/position` | GET | Read current pan (degrees), tilt, zoom |
| `/cameras/house-yard/snapshot` | GET | Take JPEG snapshot — returns image/jpeg bytes |
| `/cameras/house-yard/ptz` | POST | Move camera: `{"action":"move","pan":1,"tilt":0,"speed":5}` or `{"action":"stop"}` |
| `/cameras/house-yard/zoom` | POST | Set zoom: `{"level": 0}` (0=wide, 33=max) |
| `/cameras/house-yard/autofocus` | POST | Trigger autofocus cycle |
| `/cameras/house-yard/guard` | POST | Disable guard: `{"enabled": false}` |
| `/cameras/house-yard/spotlight` | POST | Toggle: `{"on": true, "brightness": 100}` |
| `/cameras/house-yard/siren` | POST | Fire siren: `{"duration": 10}` |

Example — take a snapshot and save it:
```bash
curl http://macmini:6530/api/v1/cameras/house-yard/snapshot --output /tmp/snap.jpg
```

Example — read position:
```bash
curl http://macmini:6530/api/v1/cameras/house-yard/position
# Returns: {"camera_id":"house-yard","pan":3600,"pan_degrees":180.0,"tilt":28,"zoom":0}
```

Example — move camera right slowly then stop:
```bash
curl -X POST http://macmini:6530/api/v1/cameras/house-yard/ptz \
  -H "Content-Type: application/json" \
  -d '{"action":"move","pan":1,"tilt":0,"speed":5}'
sleep 2
curl -X POST http://macmini:6530/api/v1/cameras/house-yard/ptz \
  -H "Content-Type: application/json" \
  -d '{"action":"stop"}'
```

**NOTE:** When you issue manual PTZ commands via the API, the patrol is still running and will move the camera on its next cycle. If Mark wants full manual control, you may need to stop Guardian first and use direct Python (Option B), or accept that the patrol will override your position after the current dwell period.

### Option B: Direct Python (local only — requires Claude Code on Mac Mini)

**WARNING:** Do NOT use this while Guardian is running — they will fight over the camera. Kill Guardian first: `kill $(pgrep -f guardian.py)`

The camera is controlled via `camera_control.py` using the `CameraController` class. You need to connect first, then issue commands. Here's the boilerplate:

```python
import time, json, os, sys
sys.path.insert(0, "/Users/macmini/Documents/GitHub/farm-guardian")
from camera_control import CameraController

config = json.load(open("/Users/macmini/Documents/GitHub/farm-guardian/config.json"))
# NOTE: load_dotenv() crashes in heredoc scripts. Instead, use:
#   set -a && source /Users/macmini/Documents/GitHub/farm-guardian/.env && set +a
# before running python, then use os.environ.get("CAMERA_PASSWORD")
env_pw = os.environ.get("CAMERA_PASSWORD")
if env_pw:
    for cam in config.get("cameras", []):
        if not cam.get("password") or "YOUR_" in cam.get("password", ""):
            cam["password"] = env_pw

ctrl = CameraController(config)
ctrl.connect_camera("house-yard",
    ip=config["cameras"][0]["ip"],
    username=config["cameras"][0]["username"],
    password=config["cameras"][0]["password"],
    port=config["cameras"][0]["port"])
```

### Key commands (direct Python):

| Action | Code |
|--------|------|
| **Pan right** | `ctrl.ptz_move("house-yard", pan=1, tilt=0, speed=5)` |
| **Pan left** | `ctrl.ptz_move("house-yard", pan=-1, tilt=0, speed=5)` |
| **Tilt up** | `ctrl.ptz_move("house-yard", pan=0, tilt=1, speed=5)` |
| **Tilt down** | `ctrl.ptz_move("house-yard", pan=0, tilt=-1, speed=5)` |
| **Stop** | `ctrl.ptz_stop("house-yard")` |
| **Read position** | `ctrl.get_position("house-yard")` → returns (pan, tilt) |
| **Read zoom** | `ctrl.get_zoom("house-yard")` |
| **Set zoom** | `ctrl.set_zoom("house-yard", 0)` (0=wide, 33=max zoom) |
| **Trigger autofocus** | `ctrl.trigger_autofocus("house-yard")` |
| **Enable autofocus** | `ctrl.ensure_autofocus("house-yard")` |
| **Take snapshot** | `ctrl.take_snapshot("house-yard")` → returns JPEG bytes |
| **Disable guard** | `ctrl.disable_guard("house-yard")` |

### Moving to a specific pan position:

Use slow speed (5-8) with frequent position polling (0.3s). Speed 40 overshoots massively. Example:

```python
def move_to_pan(ctrl, target, speed=8):
    for _ in range(100):
        pos = ctrl.get_position("house-yard")
        if not pos:
            time.sleep(0.3)
            continue
        current_pan, _ = pos
        if abs(current_pan - target) < 300:
            ctrl.ptz_stop("house-yard")
            return current_pan
        pan_dir = 1 if (target - current_pan) > 0 else -1
        ctrl.ptz_move("house-yard", pan=pan_dir, tilt=0, speed=speed)
        time.sleep(0.3)
    ctrl.ptz_stop("house-yard")
```

### Taking a snapshot and showing it to Mark:

```python
ctrl.trigger_autofocus("house-yard")
time.sleep(3)  # Let autofocus settle — IMPORTANT, don't skip this
img = ctrl.take_snapshot("house-yard")
with open("/tmp/some_descriptive_name.jpg", "wb") as f:
    f.write(img)
```

Then use the Read tool on the .jpg file to view it and describe it to Mark.

### IMPORTANT: .env loading

`load_dotenv()` crashes when called from a Python heredoc (`<< 'PYEOF'`). Instead, source the .env in bash before running python:

```bash
cd /Users/macmini/Documents/GitHub/farm-guardian && source venv/bin/activate && set -a && source .env && set +a && python3 << 'PYEOF'
# ... python code here ...
PYEOF
```

---

## Reolink E1 Outdoor Pro Coordinate System

- **Pan range:** 0–7200 (20 units per degree, 360° total)
- **Pan=0 / Pan=7200:** Camera's home position = the wooden mounting post. DEAD ZONE.
- **Tilt:** Readback is broken at some angles (returns 945). Values around 28 = nearly level (good). Values around 731-813 = steeply angled down at the ground (bad — this is what the old tilt burst was doing).
- **Zoom:** 0 = widest angle (use this for patrol), 33 = max telephoto
- **Autofocus:** Motorized lens. Must be triggered after zoom changes or significant movement. Camera takes 2-3 seconds to settle and refocus. Without this wait, snapshots are blurry.

### Pan directions:
- Panning RIGHT increases pan values: 0 → 1000 → 2000 → ... → 7200
- Panning LEFT decreases pan values: 7200 → 6000 → ... → 0

---

## World Model — What the Camera Sees at Each Angle

This was built from survey snapshots taken at 0°, 90°, 180°, 270° on 08-Apr-2026 and corrected by Mark. **This is critical context — don't guess what things are, use this. Mark will correct you if you're wrong, and you should accept his corrections immediately — he lives there.**

### Pan ~0° (pan=0) — DEAD ZONE / Mounting Post
- The wooden post the camera is mounted on fills ~40% of the left side of the frame
- A cable/wire runs diagonally across the post
- Beyond the post: bare trees, blue sky, pink tarp at ground level
- Useless for surveillance — the post blocks the view
- Dead zone config: `[6800, 440]` = pan 340°–22°

### Pan ~90° (pan=1800) — Yard / Hillside
- Green grass slope going uphill
- Scattered items, fire pit area with stones
- Edge of pink tarp visible (lower left)
- Treeline in background
- White object visible on the grass (unidentified — may be a chicken, a rock, or something else)
- Chickens may free-range in this area

### Pan ~180° (pan=3600) — THE HOUSE / Primary View
- Mark's house — two-story with deck/balcony on upper level
- Black truck in driveway to the left of house
- Green yard / lawn with muddy patches
- Chicken coop visible on the right side of frame (wire enclosure)
- **This is where the chickens are. This is the most important monitoring angle.**
- Sharpest image quality of all angles — autofocus works best here (more contrast/detail)

### Pan ~270° (pan=5400) — Old Stable Foundation / Property Edge
- **NOT forest/stumps** (I initially called this "forest edge" — Mark corrected me)
- Crumbling concrete foundation of the old stable
- Cut pieces of wood stacked on the foundation
- Center-right "trees" are actually **Rose of Sharon bushes** planted in rows
- Background tree line = thin boundary layer to the neighboring property
- Neighboring property is a **big open corn field** beyond the trees
- This is the property edge — lower predator threat direction but not ruled out
- Green chicken wire fencing visible along the ground = perimeter fencing

### Predator approach vectors (best guess, needs Mark's confirmation):
- **Hawks:** from above, any direction — sky watch is important. Mark specifically asked about tilting up for hawk sky-watch.
- **Ground predators (coyote, bobcat, fox):** likely from the treeline/property edges (90° and 270° views), NOT from the house/driveway side (180°)
- The 180° house view is still critical because that's where the chickens live

---

## Current State of the Codebase

### What was done today (v2.5.0 through v2.7.0):

1. **v2.5.0 — Camera autofocus & PTZ guard fix**
   - Added `trigger_autofocus()`, `ensure_autofocus()` to `camera_control.py`
   - Added `disable_guard()`, `is_guard_enabled()`, `set_guard_position()` to `camera_control.py`
   - Removed dead `ptz_save_preset()` stub
   - Patrol now disables PTZ guard on startup (prevents camera returning to pan=0/mount post)
   - Patrol triggers autofocus after zoom changes and deterrent resume

2. **v2.5.1 — Debug logging fix**
   - `guardian.py` called `logging.basicConfig()` twice without `force=True`
   - Second call (which sets DEBUG level and file handler) was silently ignored
   - `--debug` flag never actually worked, `guardian.log` was never written after first session
   - Fixed by adding `force=True`

3. **v2.6.0 — Step-and-dwell patrol replaces continuous sweep**
   - Old patrol panned continuously at ~70°/second — every frame was motion-blurred
   - New patrol: 11 positions at 30° intervals, 8-second dwell at each, 3-second settle + autofocus between moves
   - Full cycle ~2 minutes instead of 5 seconds
   - Camera is stationary during frame capture = clean frames for detection
   - Dead zone positions (mounting post) are automatically skipped
   - Config: `step_degrees`, `dwell_seconds`, `move_speed`, `settle_seconds` in `ptz.sweep`

4. **v2.7.0 — Remote camera control API endpoints**
   - `GET /cameras/{id}/snapshot` — take JPEG snapshot, returns image bytes
   - `GET /cameras/{id}/position` — read current pan (with degrees), tilt, zoom
   - `POST /cameras/{id}/zoom` — set absolute zoom level (0-33)
   - `POST /cameras/{id}/autofocus` — trigger autofocus cycle
   - `POST /cameras/{id}/guard` — enable/disable PTZ guard
   - Removed dead `save_preset` action from PTZ endpoint (called non-existent method)

### What is NOT done / deferred:
- **Detection pipeline:** The ignore_classes list is incomplete (detects traffic lights, umbrellas in a forest). Detection tuning deferred until camera produces consistently good frames.
- **RTSP stream corruption:** Both cameras (Reolink HEVC and nesting-box S7 H.264) produce massive codec errors — thousands per session. Needs separate investigation (possibly sub-stream or H.264 on the Reolink). Two plan docs exist for this: `docs/08-Apr-2026-rtsp-substream-plan.md` and `docs/08-Apr-2026-gwtc-webcam-stream-plan.md`.
- **Tilt calibration:** Tilt readback is broken on this camera model (returns 945 at many angles). Tilt bursts were removed in the step-and-dwell rewrite. Tilt management needs a separate approach. Mark specifically wants sky-watch capability — tilting UP to watch for hawks.
- **Vision refinement:** GLM vision model is disabled in config (`vision.enabled: false`). Would distinguish hawk vs chicken but needs LM Studio running with the `zai-org/glm-4.6v-flash` model.

### Live config.json notes:
- The live `config.json` still has old sweep settings (`pan_speed`, `tilt_speed`, `tilt_burst_seconds`, `start_pan`, `stall_threshold`, `dead_zone_skip_speed`, `dwell_at_edge`, `tilt_steps`). The new patrol code ignores all of them — it reads `step_degrees`, `dwell_seconds`, `move_speed`, `settle_seconds` with sensible defaults if absent.
- No config changes are required to use the new patrol or API endpoints.

### Guardian startup:
```bash
cd /Users/macmini/Documents/GitHub/farm-guardian
source venv/bin/activate
python guardian.py --debug
```

Or double-click **"Start Guardian (Debug).command"** on the Desktop.

---

## What the Next Assistant Should Do

1. **Set up the monitoring cron job.** Every 5 minutes, take a snapshot via the API, read position, look at the image, log observations. Use CronCreate or a scheduled task. Mark wants this running for ~2 hours.

2. **Be Mark's camera assistant.** He may be outside fixing the camera mount. He'll message from his phone with commands like "pan left", "tilt up", "what do you see?" — respond immediately by controlling the camera and showing him snapshots. **NOTE:** If Guardian is running, the patrol will override your PTZ commands on its next cycle. You may need to stop Guardian for full manual control: `kill $(pgrep -f guardian.py)`

3. **Build a world model.** As you take snapshots, name them descriptively (e.g., `snap_003_1520_pan180deg_tilt28.jpg`), note what you see, and update your understanding of the property layout. Mark will correct you — accept his corrections, he lives there.

4. **Autofocus needs time.** After any movement, wait 2-3 seconds, trigger autofocus, wait another 2-3 seconds before taking a snapshot. Rushed snapshots will be blurry.

5. **Use slow speeds for movement.** Speed 5-8 for positioning. Speed 40+ will overshoot wildly. Poll position every 0.3 seconds during moves.

6. **Keep a log** of every snapshot: timestamp, pan, tilt, what you see, Mark's corrections. Append to `/tmp/camera_observations.md`.

---

## Files Changed Today

| File | Version | What |
|------|---------|------|
| `camera_control.py` | v2.5.0 | +autofocus, +guard control, -dead stub |
| `patrol.py` | v2.6.0 | Complete rewrite: step-and-dwell replaces continuous sweep |
| `guardian.py` | v2.5.1 | `force=True` on basicConfig for debug/file logging |
| `api.py` | v2.7.0 | +snapshot, +position, +zoom, +autofocus, +guard endpoints |
| `config.example.json` | v2.6.0 | New sweep config block |
| `CHANGELOG.md` | v2.7.0 | Four new entries |
| `docs/08-Apr-2026-camera-autofocus-and-guard-plan.md` | — | Plan doc for autofocus/guard |
| `docs/08-Apr-2026-step-and-dwell-patrol-plan.md` | — | Plan doc for step-and-dwell |
| `docs/08-Apr-2026-remote-camera-api-plan.md` | — | Plan doc for API endpoints |
| `docs/08-Apr-2026-camera-setup-handoff.md` | — | This document |
| `docs/08-Apr-2026-camera-observations.md` | — | World model observations log |
| `Desktop/Start Guardian.command` | — | Double-click launcher |
| `Desktop/Start Guardian (Debug).command` | — | Double-click launcher with debug |

---

## Mark's Communication Style

- Direct, blunt, expects results not questions
- Don't ask permission for standard workflow (commits, changelogs, etc.) — just do it
- Don't ask obvious questions — he considers it a waste of time
- Keep responses short — he doesn't want chain-of-thought dumps
- When he corrects you, update your understanding immediately and don't argue
- He's a hobbyist farmer, not a developer — avoid jargon
- He has other developers working on the UI in the same repo — use worktrees for code changes
- End completed tasks with "done" or "next"
- Git identity should be "Mark Barney" / "mark@markbarney.net" (the Mac Mini's git config currently says "Bubba" / "bubba@voynich.ai" — needs manual fix)
