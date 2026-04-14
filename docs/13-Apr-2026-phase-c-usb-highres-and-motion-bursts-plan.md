# Phase C — USB cam local high-res snapshots + ONVIF motion-triggered snapshot bursts

**Author:** Claude Opus 4.6
**Date:** 13-April-2026

**STATUS (14-Apr-2026, after v2.26.0):**
- **C1 (USB cam high-res snapshot mode) — SUPERSEDED.** v2.26.0 shipped a different implementation of the same outcome: instead of a local-AVFoundation `UsbSnapshotSource` adapter dispatched from `guardian.py`, the USB camera now flows through a cross-host FastAPI snapshot service (`tools/usb-cam-host/`). The `UsbSnapshotSource` adapter path in `capture.py` still exists but is no longer wired to any camera in `config.json`. The C1 sections of this plan should be treated as historical context — **read `docs/14-Apr-2026-portable-usb-cam-host-plan.md` for the live architecture, and `docs/14-Apr-2026-system-state-snapshot.md` for the current operational wiring.**
- **C2 (ONVIF motion-triggered snapshot bursts on house-yard) — STILL OPEN.** Independent of C1 and of v2.26.0. Anyone picking it up should resume from this plan's C2 sections directly; the motion-event plumbing in `discovery.py` is untouched by v2.26.0.

---

**Goal:** Two related improvements to the snapshot architecture established in Phases A and B:

- **C1.** The Mac Mini's local USB camera (`usb-cam`) switches to the snapshot polling pattern, opening the device at its maximum stills resolution rather than the OpenCV default.
- **C2.** Wire the Reolink ONVIF motion-event subscription (already established in `discovery.py`) to trigger a short burst of ~1-Hz snapshots so we don't miss anything brief between the normal 5s ticks.

This plan is self-contained — a separate Claude session can pick it up and execute end-to-end.

**Depends on Phase A being merged.** Phase B is independent (can be done in either order relative to C, but doing B first is recommended because it exercises the `HttpUrlSnapshotSource` adapter and Phase C does not introduce any new adapters of its own).

---

## Why

After Phases A and B, the Reolink and the Gateway laptop both serve high-quality JPEGs on demand. The USB camera is the last source still on the old "open RTSP/AVFoundation, read at low cadence" pattern — it should be brought into line for consistency, and there's free quality on the table by opening it at higher resolution.

Separately, the snapshot polling cadence (5s normal, 2s during night detection) leaves a meaningful sensitivity gap for fast-moving events. The Reolink already publishes ONVIF motion events when its built-in motion detection fires; we can react to them by bursting snapshots for ~30s. This gives us "low-bandwidth idle, high responsiveness on motion" — the best of both worlds without paying for continuous video.

---

## Scope

**In:**

### C1. USB cam high-res snapshot mode

- A new `UsbSnapshotSource` adapter in `capture.py` that opens an AVFoundation device, reads a frame at the requested resolution, and returns it as a JPEG (encoded locally via `cv2.imencode`).
- `usb-cam` config switches to `source: "snapshot"`, `snapshot_method: "usb"`, `device_index: 0`, `snapshot_interval: 5.0`, optional `snapshot_resolution: [3840, 2160]` (or whatever the device supports).
- A startup probe (run once, log the result) that walks a candidate resolution list and reports which the device actually accepts.

### C2. Motion-event-triggered snapshot bursts

- A new `request_burst(duration_s, interval_s)` method on `CameraSnapshotPoller` that temporarily overrides the polling interval (e.g., 1.0s) for a fixed duration (e.g., 30s) before reverting.
- A subscription handler that listens to ONVIF motion events on cameras where `supports_motion_events: True` and calls `request_burst()` on the corresponding poller.
- The handler must coalesce: if a second motion event fires while a burst is already active, extend the burst rather than start a new overlapping one.
- A new config field per camera: `motion_burst_enabled: true|false` (default true if `supports_motion_events`), `motion_burst_duration_s: 30`, `motion_burst_interval_s: 1.0`.

**Out:**

- Reolink-specific snapshotting (Phase A).
- GWTC laptop (Phase B).
- Replacing the YOLO model. Detection inference is unchanged.
- Wiring motion bursts to non-Reolink cameras. The S7 phone and the GWTC laptop don't expose ONVIF motion events; they're not in scope for C2.

---

## Architecture

### C1: `UsbSnapshotSource`

```python
class UsbSnapshotSource:
    """Opens an AVFoundation USB camera at a requested resolution, grabs a frame,
    and returns it as JPEG bytes. Holds the VideoCapture handle open between
    calls — opening AVFoundation on every snapshot is too slow.

    Thread safety: the snapshot poller calls fetch() from a single worker thread,
    so we don't need a lock around _cap. If we ever expose this to multiple
    pollers, add one.
    """
    def __init__(self, device_index: int, target_resolution: Optional[Tuple[int, int]] = None,
                 jpeg_quality: int = 92, label: Optional[str] = None):
        self._device_index = device_index
        self._target_resolution = target_resolution
        self._jpeg_quality = jpeg_quality
        self._label = label or f"usb:{device_index}"
        self._cap: Optional[cv2.VideoCapture] = None
        self._lock = threading.Lock()

    @property
    def label(self) -> str:
        return self._label

    def _open(self) -> bool:
        cap = cv2.VideoCapture(self._device_index)
        if not cap.isOpened():
            return False
        if self._target_resolution:
            w, h = self._target_resolution
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        log.info("UsbSnapshotSource '%s' opened at %dx%d", self._label, actual_w, actual_h)
        self._cap = cap
        return True

    def fetch(self) -> Optional[bytes]:
        with self._lock:
            if self._cap is None and not self._open():
                return None
            # First read may be stale — read twice
            self._cap.read()
            ret, frame = self._cap.read()
            if not ret or frame is None:
                # Try a single reopen
                try: self._cap.release()
                except Exception: pass
                self._cap = None
                if not self._open():
                    return None
                ret, frame = self._cap.read()
                if not ret or frame is None:
                    return None
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality])
            if not ok:
                return None
            return buf.tobytes()
```

### C1 dispatch in `guardian.py`

In the snapshot-mode `snapshot_method` switch (added by Phase A, extended by Phase B), add:

```python
elif method == "usb":
    device_index = cam_cfg.get("device_index", 0)
    target_res = cam_cfg.get("snapshot_resolution")
    target_tuple = tuple(target_res) if target_res else None
    snap_src = UsbSnapshotSource(device_index, target_resolution=target_tuple,
                                  label=f"usb:{cam.name}")
```

Note: `usb-cam` currently uses the `cam.source == "usb"` branch in `_setup_cameras()` which routes to `add_camera(device_index=...)`. After Phase C1, the dispatch order is:
1. `cam_cfg.get("source") == "snapshot"` → snapshot poller (with `snapshot_method` selecting the source adapter)
2. `cam.source == "usb"` (legacy USB RTSP-style branch) → `CameraCapture` with `device_index`
3. `cam.rtsp_url` → `CameraCapture` with RTSP URL

Make sure step 1 runs first so the new path wins.

### C2: Motion-burst dispatch

#### `CameraSnapshotPoller.request_burst()`

```python
def request_burst(self, duration_s: float = 30.0, interval_s: float = 1.0) -> None:
    """Temporarily override the polling interval. Coalesces with active bursts:
    a new burst that arrives mid-burst extends the deadline instead of stacking.
    Safe to call from any thread.
    """
    with self._burst_lock:
        new_deadline = time.monotonic() + duration_s
        if new_deadline > self._burst_deadline:
            self._burst_deadline = new_deadline
            self._burst_interval = min(self._burst_interval or float("inf"), interval_s)
            log.info("Camera '%s' — burst snapshot mode for %.0fs at %.1fs interval",
                     self._camera_name, duration_s, interval_s)

def _effective_interval(self) -> float:
    # Order of precedence: active burst → night window → normal
    if time.monotonic() < self._burst_deadline:
        return self._burst_interval
    if self._is_night_window and self._is_night_window():
        return self._night_snapshot_interval or self._snapshot_interval
    return self._snapshot_interval
```

When burst expires, reset `_burst_interval` and `_burst_deadline`.

#### Motion subscription wiring

`discovery.py` already subscribes to ONVIF motion events for cameras that support them. **Investigate where those events currently land before designing the wiring** — the existing code may already have a hook point, or the events may currently be discarded. (Likely the latter — check `supports_motion_events` references in the codebase.)

If no current hook: add a `_on_motion_event(camera_name)` method on `GuardianService` and have `discovery.py` invoke it via a callback registered at startup. Then in `_on_motion_event`, find the camera's poller in `FrameCaptureManager` and call `request_burst()` on it.

```python
def _on_motion_event(self, camera_name: str) -> None:
    cam_cfg = self._get_camera_config(camera_name)
    if not cam_cfg or not cam_cfg.get("motion_burst_enabled", True):
        return
    poller = self._capture_manager.get_poller(camera_name)
    if not isinstance(poller, CameraSnapshotPoller):
        log.debug("Motion event for '%s' but it's not a snapshot camera — ignoring", camera_name)
        return
    poller.request_burst(
        duration_s=cam_cfg.get("motion_burst_duration_s", 30.0),
        interval_s=cam_cfg.get("motion_burst_interval_s", 1.0),
    )
```

This requires adding a `get_poller(name)` accessor on `FrameCaptureManager` that returns the underlying capture/poller object (currently `_captures[name]` is private). Or just expose the existing private dict via a public method.

### Config (per camera, both C1 and C2)

```jsonc
// usb-cam (C1)
{
  "name": "usb-cam",
  "source": "snapshot",
  "snapshot_method": "usb",
  "device_index": 0,
  "snapshot_resolution": [3840, 2160],   // optional; if device can't, falls back to its default
  "snapshot_interval": 5.0,
  "detection_enabled": false
}

// house-yard motion burst additions (C2)
{
  "name": "house-yard",
  ...all Phase A fields...,
  "motion_burst_enabled": true,
  "motion_burst_duration_s": 30,
  "motion_burst_interval_s": 1.0
}
```

---

## TODOs (ordered, with verification)

### C1 — USB cam

1. **Probe usb-cam max resolution.** Write a one-shot Python script that opens device 0 and walks `[(3840,2160), (2560,1440), (1920,1080), (1280,720)]` setting + verifying each. Log the first that sticks. (Probably 1920×1080 for typical USB webcams, possibly higher.)

2. **Implement `UsbSnapshotSource`** in `capture.py`. Update file header.

3. **Extend `guardian.py` snapshot-mode dispatch** to handle `snapshot_method: "usb"`. Make sure the snapshot branch wins over the legacy `cam.source == "usb"` branch when both could match (snapshot mode should always win when the camera config has `source: "snapshot"`). Update header.

4. **Update `usb-cam` in `config.json` and `config.example.json`** to snapshot mode with the probed resolution.

5. **Restart Guardian. Verify:**
    - Log shows `UsbSnapshotSource 'usb:usb-cam' opened at NxN`.
    - `curl http://localhost:6530/api/cameras/usb-cam/frame -o /tmp/u.jpg` returns at the expected resolution.
    - Snapshot interval ~5s as configured.

6. **Document USB resolution in CLAUDE.md.**

### C2 — Motion-event bursts

7. **Audit existing motion-event handling.** Search the codebase for `supports_motion_events`, `motion_alarm`, `subscribe_alarm` (and the reolink_aio equivalents). Identify whether motion events are currently delivered anywhere or silently dropped. Document findings as a comment in this plan or as a brief note in the commit message.

8. **Add `request_burst()` and `_effective_interval()`** on `CameraSnapshotPoller`. Initialize `_burst_deadline = 0.0` and `_burst_interval = None` in `__init__`. Add `_burst_lock = threading.Lock()`.

9. **Add `get_poller(name)` accessor** on `FrameCaptureManager`.

10. **Wire motion subscription → `_on_motion_event`.** This is the part most likely to surprise — depends entirely on what step 7 finds. Sketch:
    - If `discovery.py` has an internal callback hook: register one from `GuardianService.__init__()`.
    - If not: spin up a small ONVIF motion-event listener thread (reolink_aio supports `host.subscribe_motion_event` or similar). See `venv/lib/python3.13/site-packages/reolink_aio/api.py` for the actual method names — read it before guessing.

11. **Add config fields** `motion_burst_enabled`, `motion_burst_duration_s`, `motion_burst_interval_s` to `house-yard` (defaults are fine; just document via `config.example.json`).

12. **Restart Guardian. Verify:**
    - Trigger a real motion event by walking past the house-yard camera (Boss can do this). Watch logs for `"Camera 'house-yard' — burst snapshot mode for 30s at 1.0s interval"`.
    - Confirm snapshot rate visibly accelerates in dashboard.
    - After 30s, verify it returns to the normal 5s cadence.
    - Trigger a second event mid-burst; confirm log shows the deadline was extended (not duplicated).

13. **CHANGELOG entry** with version bump (likely v2.20.0). Cite this plan doc.

14. **Commit + push.**

---

## Risks / things to think about

### C1

- **AVFoundation resolution lies.** Setting `CAP_PROP_FRAME_WIDTH` does not guarantee the device honors it. Always re-read `cap.get(CAP_PROP_FRAME_WIDTH)` after setting and trust that. The probe step exists exactly because of this.

- **First-frame staleness.** Some cameras return a 1-frame-old buffer on the first read after being opened. The double-read in `fetch()` mitigates this. If quality is consistently off, increase to 3 reads or add a short sleep.

- **Open/close vs hold-open tradeoff.** Opening AVFoundation on every snapshot is slow (hundreds of ms). Holding it open uses some power and locks the device against other apps. We hold it open. If anything else on the Mac Mini wants the USB camera, it'll fail — but nothing else does today.

### C2

- **Motion event spam during patrol.** If patrol is moving the camera, the camera's onboard motion detector may fire constantly on apparent global motion. Two mitigations:
    - Honor `patrol_pause_event`: if patrol is *not* paused (i.e., patrol is actively moving the camera), suppress motion-burst handling.
    - Or: rate-limit motion events to one burst per N seconds regardless of source.

- **Motion events at night during detection window.** The night cadence is already 2s; bursts at 1s are a marginal improvement. Worth keeping for the 0.5s the burst beats the night cadence. Not worth special-casing.

- **Motion events during bright-day false triggers.** Moving leaves, shadows. The burst is cheap (30 snapshots over 30s at 4K = ~40MB), but if it triggers constantly the Cloudflare tunnel sees more traffic than usual. Acceptable; revisit if it becomes a problem.

- **Subscription lifecycle.** ONVIF event subscriptions have a lease that needs renewal. If the existing code doesn't handle this, renewal logic needs to live somewhere (probably the camera_controller or a new EventSubscriber thread). Verify behavior across an hour-plus runtime.

- **Burst extension vs. stack.** Coalescing matters: a sustained motion event will fire the ONVIF callback repeatedly. Each callback should *extend* the burst deadline, not start a new burst. The `request_burst()` skeleton above handles this with `if new_deadline > self._burst_deadline`.

---

## Docs / Changelog touchpoints

- `CHANGELOG.md` — new top entry per phase part (or one combined entry if both C1 + C2 ship together).
- `CLAUDE.md` — `usb-cam` description, motion-burst note for `house-yard`, possibly a "Recent Changes" line.
- `docs/` — this plan stays as the historical record.
- `config.example.json` — `usb-cam` snapshot config, motion-burst fields on `house-yard`.
