# Sky-cam predator detection — 17-May-2026

**Author:** Claude Opus 4.7 (1M context)
**Status:** Plan, decisions locked. Awaiting Boss's "go" before code.
**Trigger:** Easter-egger hen lost to suspected aerial predator. Existing ground-cam predator detection on `house-yard` was disabled because false positives on a busy ground scene were unworkable. Repurpose `usb-cam` (physically plugged into GWTC at `192.168.0.68:8089`, currently being re-aimed at the sky by Boss) to detect aerial threats, where the visual signal-to-noise ratio is dramatically better.

---

## Decisions (locked, do not re-litigate)

1. **Day window: fixed clock 06:00–20:00 America/New_York.** No astronomical sunrise/sunset library. Tweak in config later if needed.
2. **Alerts go to the existing `#farm-2026` Discord channel** with an `AERIAL — ` title prefix. No separate channel.
3. **GWTC runs nothing new.** It stays a dumb JPEG-server. All frame-diff and YOLO work runs on the Mac Mini, where the cycles live.
4. **Camera physical position: Boss is handling.** Current pull shows mid-handling artifacts; re-poll after settle to confirm sky angle.

---

## Scope

### In
- Re-aim `usb-cam` at the sky (Boss handling physically).
- Two-stage detection, **both stages on the Mac Mini**:
  1. **Stage 1 — frame-diff motion gate** in a new module `motion.py` on the Mini. Runs on every polled frame. Downscaled grayscale + EMA background + `cv2.absdiff` + contour-area threshold. ~1 ms per frame.
  2. **Stage 2 — YOLOv8 classification** in the existing `detect.py`, called only on motion frames.
- Per-camera detection-filter overrides in `detect.py` (the current global bbox/dwell/class knobs are wrong for sky and silently filter out every hawk if left global).
- Per-camera day/night window in `guardian.py` (the current global window is night-only; hawks are diurnal).
- Reuse the v2.20.0 motion-burst pattern: motion detected by Stage 1 on the Mini triggers `poller.request_burst()` to tighten the snapshot cadence to 1 s for 30 s. This is the existing burst plumbing, with a new trigger source.
- `AERIAL — ` title prefix on Discord alerts via `alerts.py` for `usb-cam` events.
- **Gem-pollution guard:** per-camera `social_excluded: true` flag, honored by `scripts/discord-reaction-sync.py` so reactions on aerial alerts can never promote a predator frame to Instagram.

### Out (deferred or off the table)
- **Anything running on GWTC.** No frame-diff there, no YOLO there, no new agents. GWTC is and remains a JPEG-server. The existing `usb-cam-host` keeps its current behavior unchanged.
- **Deterrent integration.** No siren, no spotlight, no smart-plug, no inflatable tube man in this iteration. Alerts-only. Wire deterrents in a separate iteration once the alert lane is observed live for 1–2 weeks and trusted.
- **YOLO retraining / custom hawk model.** COCO's `bird` class works against open sky; the uniform background is what makes it work, not the model.
- **VLM / LM Studio reintroduction.** `vision.py` was removed in v2.17.0 for good reason.
- **Owls / night raptors.** Visible-light cameras can't see owls in the dark. Separate hardware problem (IR), separate iteration.
- **Pipeline-side change (`tools/pipeline/config.json`).** Out of scope — VLM enrichment of sky frames is harmless and will self-rate as low-share-worth. One-line follow-up if it proves noisy.

---

## Why these specific architecture choices

### Why frame-diff moved off GWTC onto the Mac Mini
- GWTC is a slow Gateway laptop running Windows, MediaMTX, ffmpeg, two watchdogs, and `usb-cam-host`. Adding `cv2.absdiff` + contour analysis on every captured frame is borrowed time on a budget-constrained box that drops off the WiFi several times a week. The Mini is a 14-core M4 Pro with 64 GB RAM. The cycles belong here.
- Network cost is acceptable: at a 5 s baseline poll, the Mini fetches `/photo.jpg` 17,280 times/day from GWTC — ~1.5 GB/day across the same WiFi the cameras already use. Burst windows add some. The Mini does the diff locally on each received frame.
- The diff's reliability does not depend on GWTC's health. If GWTC's WiFi blips, the diff resumes on the next frame after reconnect; the EMA background absorbs any minor lighting change in the gap.

### Why reuse the v2.20.0 motion-burst pattern instead of running YOLO on every frame
- `guardian.py:_motion_watch_loop` already polls a motion oracle and calls `poller.request_burst(duration_s=30, interval_s=1.0)` on `False → True` edges. The burst plumbing on the poller is generic. We just need to swap the oracle from "ask the Reolink" to "diff the last polled usb-cam frame."
- The sky is empty ~99% of the time. Polling at 5 s baseline + bursting to 1 s on motion gives us 1 s response during interesting moments and trivial cost the rest of the time. Running YOLO at 1 s constant cadence would be wasteful — sky is empty, and YOLO on Apple Silicon MPS still costs ~30 ms per call.

### Why frame-diff is the dominant signal, not YOLO
- A bird-shaped object against open sky is the textbook positive case for `absdiff`. None of the standard ground-cam false-positive sources (wind-blown leaves, shadow drift, chickens, humans, fast clouds) exist when the camera faces straight up. Edge clutter from a tree branch in the corner can be masked with a no-diff-zone polygon if needed.
- YOLO becomes the Stage-2 sanity check that turns "something moved" into "yes, that movement was a bird." It also filters the few false positives a sky-cam *does* see: a bug close to the lens (too small in bbox terms even at 1% cutoff), a passing plane (COCO `airplane` already in `ignore_classes`), a leaf gust into the frame edge (no bird-class confidence).

### Why route AERIAL alerts to `#farm-2026` even though it's the gem-quality gate
- Boss explicitly chose it. The cost is the gem-pollution risk, and that's a fixable one-line SQL filter — see "Gem-pollution guard" below.

---

## Current architecture — what we are extending

### Already in our favor (no changes needed)
- `guardian.py:528` — per-camera `detection_enabled` flag exists. Flip `usb-cam` to `true`.
- `usb-cam-host` on GWTC — keeps a warm `cv2.VideoCapture` open and serves `/photo.jpg` in ~50 ms. Already host-portable. Zero changes needed.
- `tracker.py` — dedupes individual detections into visit tracks. A hawk circling overhead produces ONE Discord post per visit, not one per frame.
- v2.20.0 motion-burst plumbing in `guardian.py:_motion_watch_loop` + `CameraSnapshotPoller.request_burst()`. Reused; oracle source swapped.

### Already in our way (must change)
- `detect.py` — all filter knobs (`_bird_min_bbox_pct = 8.0`, `_min_dwell_frames = 3`, `_predator_classes`, `_class_thresholds`, `_no_alert_zone`) are global. For sky-cam they all need different values, and these values must not regress ground cams.
- `guardian.py:532` — `_detection_window_open()` returns true 20:00–09:00 (night-only). Gates every detection-enabled camera through the same window. If we flip `usb-cam.detection_enabled` and stop there, YOLO runs on sky frames at night only — exactly when hawks aren't there.
- `_motion_watch_loop` — branches only on `snapshot_method == "reolink"` (uses `reolink_aio.get_motion_state`). USB cams currently invisible to it.
- `scripts/discord-reaction-sync.py:_find_matching_gem` — promotes ANY image_archive row matching `(camera_id, ts ±60s)` of a reacted Discord message. If a `usb-cam` predator alert lands in `#farm-2026` and Boss reacts, the matching usb-cam frame in `image_archive` gets gem-promoted and posted to Instagram. **This must be blocked.**

---

## Architecture — module changes

### 1. New module `motion.py` on the Mac Mini
A small, single-responsibility frame-differ. Same SRP shape as `detect.py` / `tracker.py`.

```python
class MotionGate:
    def __init__(self, config: dict): ...
    def update(self, frame: np.ndarray, camera_name: str) -> MotionState: ...
```

- Per-camera state: `prev_gray`, `ema_bg`, `last_event_ts`, `active`, `last_contour_area_pct`. Stored in `dict[str, _CameraState]`.
- On `update()`:
  - Downscale to ~320×180, convert to grayscale.
  - Lazy-init `ema_bg` to `cur_gray` on first frame.
  - EMA: `bg = 0.95 * bg + 0.05 * cur_gray`.
  - `diff = cv2.absdiff(cur_gray, bg)`, threshold at 25 (config-overridable per camera), `cv2.findContours`.
  - Largest contour area as % of downscaled frame area. If above `motion_min_area_pct` (default 0.05%, config-overridable), set `active = True`, `last_event_ts = now`, return state.
  - Active flag decays 5 s after the last triggering frame (config-overridable).
- Per-camera knobs read from `cameras[i].motion.{enabled, threshold, min_area_pct, decay_s}` with sensible global defaults. Cameras without a `motion` block get `enabled = False`, so this module is a no-op for ground cams in this PR.
- Optional `no_diff_zone: [[x_pct, y_pct], ...]` polygon — same shape as `no_alert_zone` in `detect.py`. Lets us mask out a tree branch corner if the live frame has stationary clutter that's still triggering due to wind. Defer adding this until calibration reveals it's needed.
- Debug instrumentation: when `motion.debug_log_path` is set in config, write each transition (False→True, True→False) to a JSONL log with timestamp, contour area, and camera. Used during the calibration window.

### 2. `guardian.py` — wire MotionGate + extend `_motion_watch_loop` away from Reolink-only
- Add `self._motion_gate = MotionGate(config)` near the existing detector init.
- In `_on_frame` (line 517), **before** the existing window/detector checks, call `self._motion_gate.update(frame, camera_name)`. If the camera has motion gating enabled and the result is `active=False`, drop the frame (return). If `active=True`:
  - Skip YOLO if the per-camera day/night window is closed (see below).
  - Otherwise let the existing `self._detector.detect(...)` path run.
  - Independently, if this is a False→True edge AND the poller supports `request_burst`, call it to tighten cadence. This is the trigger that replaces `_motion_watch_loop`'s Reolink poll for HTTP cameras.
- `_motion_watch_loop` keeps its existing Reolink behavior. We do **not** add HTTP polling to that loop in this PR — the motion oracle for HTTP cameras lives in `_on_frame` via `MotionGate`, not in a separate polling thread. This is simpler than the earlier plan (no `/motion` endpoint, no extra HTTP round-trip every 2 s) and removes one moving part.

### 3. `detect.py` — per-camera filter overrides
- `AnimalDetector.__init__` continues to load global `detection.*` as defaults. Additionally iterate `config["cameras"]`; for each camera with a `detection` sub-block, store the per-camera overrides keyed by camera name: `self._per_camera: dict[str, dict] = {...}`.
- Private resolver `_get_filter(camera_name, key)` returns per-camera override if present, else global.
- Update `detect(frame, camera_name)` to resolve per-call:
  - `predator_classes`
  - `class_thresholds` (per-camera class threshold → per-camera default → global class threshold → global default)
  - `bird_min_bbox_pct`
  - `min_dwell_frames`
  - `no_alert_zone`
- Existing dwell tracker key already includes camera_name (`(camera_name, class_name)`), no change needed.
- Acceptance: `house-yard`'s behavior is byte-identical when no `detection` sub-block exists on its config entry.

### 4. `guardian.py` — per-camera day/night window
- `_detection_window_open()` becomes `_detection_window_open(camera_name: str | None = None)`.
- New per-camera config key `detection.window: "day" | "night" | "always"`. Default `"night"` (preserves current behavior for ground cams).
- `_on_frame` passes `frame_result.camera_name`. For `"day"`: returns True if local time is between 06:00 and 20:00 inclusive. For `"always"`: always True. For `"night"`: existing 20:00–09:00 behavior.

### 5. `alerts.py` — `AERIAL — ` title prefix for usb-cam
- New optional per-camera config key `alerts.title_prefix` (default empty).
- Read at alert-send time, prepended to the Discord embed title.
- No webhook override — `#farm-2026` is the destination, same as everything else.

### 6. Gem-pollution guard
Two changes, both small and explicit:

a. **Config:** add `social_excluded: true` to the `usb-cam` block at the top level.

b. **`scripts/discord-reaction-sync.py`:** modify `_find_matching_gem` to skip cameras marked `social_excluded`. Simplest implementation — pass a `social_excluded_cameras: set[str]` (loaded once from `config.json` at startup) into the function and add a guard:
```python
if camera_id in social_excluded_cameras:
    return None
```
That single check, executed before the SQL, prevents any aerial alert frame from ever being promoted to a gem. The reaction itself still posts to Discord and is visible; it just never lands in the IG queue.

This is reversible and config-driven. If later we decide a small subset of aerial frames *are* shareable (silhouette against sunset, etc.), the right answer is a manual curation path, never automatic promotion.

### 7. `config.json` — usb-cam block additions
```json
{
  "name": "usb-cam",
  "type": "fixed",
  "source": "snapshot",
  "snapshot_method": "http_url",
  "http_base_url": "http://192.168.0.68:8089",
  "http_photo_path": "/photo.jpg",
  "http_trigger_focus": false,
  "snapshot_interval": 5.0,
  "detection_enabled": true,
  "motion_burst_enabled": true,
  "motion_burst_duration_s": 30.0,
  "motion_burst_interval_s": 1.0,
  "social_excluded": true,
  "motion": {
    "enabled": true,
    "threshold": 25,
    "min_area_pct": 0.05,
    "decay_s": 5.0
  },
  "detection": {
    "window": "day",
    "predator_classes": ["bird"],
    "bird_min_bbox_width_pct": 1.0,
    "class_confidence_thresholds": { "bird": 0.30 },
    "min_dwell_frames": 1,
    "no_alert_zone": []
  },
  "alerts": {
    "title_prefix": "AERIAL — "
  }
}
```
- `snapshot_interval: 5.0` stays — the burst overrides to 1 s during motion windows.
- `bird_min_bbox_width_pct: 1.0` — ~19 px on a 1920-wide frame. Conservative starting point; may tune to 0.5 after live data.
- `class_confidence_thresholds.bird: 0.30` — lower than the ground-cam 0.45 because uniform-sky backgrounds yield cleaner YOLO confidence distributions for `bird`.
- `min_dwell_frames: 1` — a stoop can be over in one frame. We rely on Stage 1 motion gating to filter noise, not on requiring three consecutive YOLO hits.

### 8. `tools/pipeline/config.json` — **no change** in this PR
Deferred deliberately to keep blast radius small.

---

## TODOs — ordered

1. **`motion.py`** — new file. `MotionGate` class with the API and per-camera state described above. Header per repo standards.
2. **Verify `MotionGate` in isolation** — a tiny `scripts/test-motion-gate.py` that polls `http://192.168.0.68:8089/photo.jpg` once per second for ~30 s, feeds frames into `MotionGate.update()`, prints transitions. Wave a hand in front of the camera; confirm False→True→False with sensible contour areas. No new test framework — single ad-hoc script.
3. **Wire `MotionGate` into `guardian.py:_on_frame`** — drop non-motion frames; on False→True edges call `poller.request_burst()` for cameras that support it.
4. **Per-camera detection-filter overrides in `detect.py`** — refactor, verify ground-cam unchanged with no override block.
5. **Per-camera day window in `guardian.py:_detection_window_open(camera_name)`** — fixed clock 06:00–20:00.
6. **`alerts.py`** — per-camera `title_prefix` support.
7. **Gem-pollution guard in `scripts/discord-reaction-sync.py`** — `social_excluded_cameras` set, early-return in `_find_matching_gem`.
8. **Update `config.json`** with the usb-cam block above. Hand-edit; grep both configs per the "TWO SEPARATE CONFIG FILES" rule in CLAUDE.md to confirm the pipeline config is intentionally left alone.
9. **Reload Guardian:** `launchctl kickstart -k gui/$(id -u)/com.farmguardian.guardian`. No GWTC restart needed (unchanged there).
10. **Calibration window — 24 to 48 hours of debug logging.** Confirm motion debug log accumulates real events. Sample a few transitions, inspect the corresponding frames, decide if `min_area_pct` needs tightening or loosening. Watch the `image_archive` table; confirm that even when Boss reacts to AERIAL alerts in `#farm-2026`, no usb-cam frame ever gets `discord_reactions > 0` written by the sync.
11. **CHANGELOG.md entry, SemVer minor.** Target `v2.38.0` pending confirmation against the current top entry. What/why/how. Reference this plan.
12. **Update CLAUDE.md** — Camera 3 (usb-cam) entry: role change, motion-gated detection, social-excluded; Recent Changes section.
13. **Update `HARDWARE_INVENTORY.md`** — usb-cam role: aerial predator monitor, day-window, social-excluded.
14. **Commit + push to main** per `feedback_farm_guardian_commit_to_main.md`.
15. **Follow-up issue (not in this PR):** wire `usb-cam` bird detections to `deterrent.py` (siren first, smart-plug / tube-man later).

---

## Verification

- **MotionGate unit-level:** `scripts/test-motion-gate.py` shows clean False→True transition when motion enters frame, True→False decay after stillness.
- **Per-camera detection filters:** at Guardian startup, log the resolved filters per camera; usb-cam shows the sky-tuned values, house-yard shows globals.
- **Per-camera window:** at 14:00, `_detection_window_open("usb-cam")` returns True, `_detection_window_open("house-yard")` returns False. At 03:00, the inverse.
- **End-to-end positive:** wave or throw an object into the camera's sky cone during the day window. Within ~5 s:
  - `MotionGate` flips active.
  - Poller cadence tightens to 1 s.
  - YOLO runs against the burst frames.
  - If bird-class detection lands, an alert posts to `#farm-2026` with title `AERIAL — bird detected on usb-cam` and a snapshot.
- **End-to-end negative (window):** repeat at 22:00. Motion still detected, burst still fires (motion is window-independent — bursts are about catching brief events). But `_on_frame` short-circuits the YOLO call because the day window is closed. No alert.
- **Gem-pollution guard:** during the calibration window, react to an AERIAL alert in `#farm-2026` with any emoji. Confirm via `sqlite3` query that the matching `usb-cam` row in `image_archive` has `discord_reactions = NULL` or 0 after the next `discord-reaction-sync` tick. Also confirm no story-publish queue entry appears.

---

## Risks and tripwires

- **GWTC WiFi watchdog cycles** — every few days GWTC drops off WiFi for ~25 s while `farmcam-wifi-watchdog` bounces the adapter. `MotionGate` sees no new frames during the gap; on reconnect, the first frame may differ from the EMA background due to natural sky/cloud movement during the gap and trigger a spurious motion event. YOLO Stage 2 filters this out (no bird class on a cloud-shifted sky frame), so worst case is one wasted YOLO call per reconnect. Not worth special-casing in v1.
- **Sun glare / fast AE adjustment / lens condensation** — EMA background absorbs slow lighting changes. Sudden AE swings (cloud passing fast) may trip the threshold once before EMA catches up; YOLO filters those out. Lens condensation in morning fog is an ops issue.
- **Frame edge clutter from a tree branch** — visible in the current pre-positioning snapshot. If post-positioning a tree branch corner still triggers diff due to wind, add a `no_diff_zone` polygon to the motion config. Defer the code path until calibration data says we need it; the field is in the spec above.
- **8% bird-min-bbox global silently filtering aerial birds** — addressed by the per-camera override refactor. Verify ground-cam behavior unchanged when no per-camera detection block exists. Acceptance: identical resolved filter values pre/post refactor for `house-yard`.
- **Reaction-sync running before the social-excluded check is deployed** — small window during the deploy where Boss could react to an early AERIAL alert and trigger a gem promotion. Mitigation: deploy in this order: (1) push the reaction-sync patch, (2) restart the sync agent, (3) flip `detection_enabled` on usb-cam. The guard lands before the first alert can fire.
- **VLM pipeline rates sky frames as low share_worth and clutters Discord gem lane** — possible. Out of scope; one-line `vlm_enabled: false` follow-up if it happens.
- **Camera physical mount fails (falls, gets pecked, condenses inside)** — ops issue. The Stage 1 motion gate will show abnormal patterns (constant motion from a wobbling mount, or zero motion ever from a fogged lens). The calibration window log will surface this.

---

## Docs / changelog touchpoints
- `CHANGELOG.md` — new entry, SemVer minor, target `v2.38.0`.
- `CLAUDE.md` — Camera 3 (usb-cam) description; Recent Changes section; reference this plan.
- `HARDWARE_INVENTORY.md` — usb-cam role.
- `docs/SOCIAL_MEDIA_MAP.md` — no change (sky frames are explicitly social-excluded).
- `tools/pipeline/config.json` — no change in this PR.
