# Camera Setup Handoff — 08-Apr-2026

**Purpose:** This document is a complete handoff for the next Claude session to continue helping Mark set up and calibrate his Reolink E1 Outdoor Pro PTZ camera. Read this entire document before doing anything.

---

## What Is Happening Right Now

Mark is going outside to physically fix the camera to its mounting post (it's wobbly). He wants to communicate with you from his phone while outside, telling you to pan/tilt the camera so he can verify positioning. He will also want you to report what you see through the camera — you are his eyes.

**Your job:** Be Mark's remote camera assistant. He will message you from outside saying things like "pan left", "look at the house", "tilt up", "what do you see?" — and you respond by controlling the camera hardware and showing him snapshots of what the camera sees.

---

## How to Control the Camera

The camera is controlled via `camera_control.py` using the `CameraController` class. You need to connect first, then issue commands. Here's the boilerplate:

```python
import time, json, os, sys
sys.path.insert(0, "/Users/macmini/Documents/GitHub/farm-guardian")
from dotenv import load_dotenv
from camera_control import CameraController

# Load config with secrets from .env
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

### Key commands:

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
- **Tilt:** Readback is broken at some angles (returns 945). Values around 28 = nearly level. Values around 731-813 = steeply angled down at the ground.
- **Zoom:** 0 = widest angle (use this for patrol), 33 = max telephoto
- **Autofocus:** Motorized lens. Must be triggered after zoom changes or significant movement. Camera takes 2-3 seconds to settle and refocus.

### Pan directions:
- Panning RIGHT increases pan values: 0 → 1000 → 2000 → ... → 7200
- Panning LEFT decreases pan values: 7200 → 6000 → ... → 0

---

## World Model — What the Camera Sees at Each Angle

This was built from survey snapshots taken at 0°, 90°, 180°, 270° and corrected by Mark. **This is critical context — don't guess what things are, use this.**

### Pan ~0° (pan=0) — DEAD ZONE / Mounting Post
- The wooden post the camera is mounted on fills ~40% of the left side of the frame
- Useless for surveillance
- Dead zone config: `[6800, 440]` = pan 340°–22°

### Pan ~90° (pan=1800) — Yard / Hillside
- Green grass slope going uphill
- Scattered items, fire pit area
- Edge of pink tarp visible (lower left)
- Treeline in background
- White object visible (unidentified — may be a chicken or a rock)

### Pan ~180° (pan=3600) — THE HOUSE / Primary View
- Mark's house with deck/balcony
- Black truck in driveway
- Green yard / lawn
- Chicken coop visible on the right side of frame
- **This is where the chickens are. This is the most important monitoring angle.**
- Sharpest image quality of all angles

### Pan ~270° (pan=5400) — Old Stable Foundation / Property Edge
- **NOT forest/stumps** (I initially got this wrong, Mark corrected me)
- Crumbling concrete foundation of the old stable
- Cut pieces of wood on the foundation
- Center-right "trees" are actually **Rose of Sharon bushes** in rows
- Background tree line = thin boundary layer to the neighboring property
- Neighboring property is a **big open corn field**
- This is the property edge — lower predator threat direction but not ruled out
- Green chicken wire fencing visible = perimeter fencing

### Predator approach vectors (best guess, needs Mark's input):
- Hawks: from above/any direction — sky watch is important
- Ground predators (coyote, bobcat, fox): likely from the treeline/property edges, NOT from the house/driveway side

---

## Current State of the Codebase

### What was done today (v2.5.0, v2.5.1, v2.6.0):

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

### What is NOT done / deferred:
- **Detection pipeline:** Ignore for now. Detection is useless until camera produces good frames consistently. The ignore_classes list is incomplete (detects traffic lights, umbrellas in a forest), but fixing that is pointless while the feed is garbage.
- **RTSP stream corruption:** Both cameras (Reolink HEVC and nesting-box S7 H.264) produce massive codec errors. Thousands of decode failures per session. This needs a separate investigation — possibly switching to sub-stream or H.264 on the Reolink.
- **Tilt calibration:** Tilt readback is broken on this camera model. Tilt bursts were removed in the step-and-dwell rewrite. Tilt management needs a separate approach — Mark wants sky-watch capability for hawks.
- **Vision refinement:** GLM vision model is disabled in config. Would distinguish hawk vs chicken but needs LM Studio running.

### Live config.json notes:
- The live `config.json` still has old sweep settings (`pan_speed`, `tilt_speed`, `tilt_burst_seconds`, `start_pan`, `stall_threshold`, `dead_zone_skip_speed`, `dwell_at_edge`). The new patrol code ignores them — it reads `step_degrees`, `dwell_seconds`, `move_speed`, `settle_seconds` with sensible defaults if absent.
- `tilt_steps: 3` is dead config (was never used even in the old sweep code).
- No config changes are required to use the new patrol.

### Guardian startup command:
```bash
cd /Users/macmini/Documents/GitHub/farm-guardian
source venv/bin/activate
python guardian.py --debug
```

Or double-click **"Start Guardian (Debug).command"** on the Desktop.

**WARNING:** Do NOT run Guardian while also running a manual camera control script. They will fight over the camera. Kill one before starting the other.

### Guardian is currently NOT running.
The monitor script and guardian were both killed before this handoff. The camera is idle at pan ~267° (old stable view).

---

## What the Next Assistant Should Do

1. **Be Mark's camera assistant.** He's outside fixing the camera mount. He'll message you from his phone with commands like "pan left", "tilt up", "what do you see?" — move the camera and show him snapshots.

2. **Build a world model.** As you take snapshots, name them descriptively (e.g., `snap_house_pan180_tilt28.jpg`), note what you see, and update your understanding of the property layout. Mark will correct you — accept his corrections, he lives there.

3. **Don't run Guardian yet.** Mark wants manual camera control right now, not automated patrol. Only start Guardian when Mark explicitly says to.

4. **Autofocus needs time.** After any movement, wait 2-3 seconds, trigger autofocus, wait another 2-3 seconds before taking a snapshot. Rushed snapshots will be blurry.

5. **Use slow speeds for movement.** Speed 5-8 for positioning. Speed 40+ will overshoot wildly. Poll position every 0.3 seconds during moves.

6. **Keep a log** of every snapshot: timestamp, pan, tilt, what you see, Mark's corrections. Save to `/tmp/camera_observations.md` or similar.

7. **Don't start the step-and-dwell patrol** until Mark has verified the camera mount is solid and the positioning/autofocus behavior is acceptable during manual testing.

---

## Files Changed Today

| File | Version | What |
|------|---------|------|
| `camera_control.py` | v2.5.0 | +autofocus, +guard control, -dead stub |
| `patrol.py` | v2.6.0 | Complete rewrite: step-and-dwell replaces continuous sweep |
| `guardian.py` | v2.5.1 | `force=True` on basicConfig for debug/file logging |
| `config.example.json` | v2.6.0 | New sweep config block |
| `CHANGELOG.md` | v2.6.0 | Three new entries |
| `docs/08-Apr-2026-camera-autofocus-and-guard-plan.md` | — | Plan doc for autofocus/guard |
| `docs/08-Apr-2026-step-and-dwell-patrol-plan.md` | — | Plan doc for step-and-dwell |
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
