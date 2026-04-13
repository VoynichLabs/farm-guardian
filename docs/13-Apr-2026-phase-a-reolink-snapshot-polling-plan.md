# Phase A — Reolink house-yard switches from RTSP to HTTP snapshot polling

**Author:** Claude Opus 4.6
**Date:** 13-April-2026
**Goal:** Stop pulling continuous RTSP video from the Reolink E1 Outdoor Pro. Replace it with periodic HTTP snapshot polling against the camera's `cmd=Snap` endpoint, which returns the camera's native 4K JPEG. This is Phase A of a three-phase shift to "use the cameras as cameras, not as video streams."

This plan is self-contained — a separate Claude session can pick it up and execute end-to-end.

---

## Why

The architecture pivot was directed by Boss after seeing two things back-to-back:

1. **v2.16.0** rejected HEVC decode-garbage frames (the gray washed-out smears the lossy WiFi link was producing). Filter worked correctly but capped delivered fps because the WiFi was genuinely losing reference packets every 1–2s.
2. **v2.17.0** switched the Reolink from the 4K HEVC main RTSP stream to the ~640×360 H.264 sub-stream. Garbage rejections dropped to zero; live view became smooth — but at low resolution.

Boss's insight: since v2.17.0 also ripped out the GLM vision species refinement (Discord alert just posts the snapshot, no classification), there is no need for live video at all. What we actually want is **high-quality stills**. And the Reolink CAN deliver that — it has an HTTP `cmd=Snap` endpoint that returns the camera's full native resolution JPEG.

### Verified facts (tested 2026-04-13)

- Direct HTTP `GET /cgi-bin/api.cgi?cmd=Snap&channel=0&token=...` returns **3840×2160 JPEG, 1.35MB, in ~630ms**.
- Same path via the existing `camera_control.take_snapshot('house-yard')` (which uses `reolink_aio.host.get_snapshot(channel)` under the hood) returns the same 4K JPEG. End-to-end through Guardian's REST API: 1.13s, 1.35MB. The slightly higher latency is the asyncio-loop wrapper inside `CameraController._run_async()`.
- 4K snapshot every 5s = ~270 KB/s sustained on the WiFi link. Trivial.
- 4K snapshot every 2s = ~675 KB/s. Still trivial.

The Reolink is a much better camera than its RTSP firmware lets on. The streaming codec is the bottleneck, not the sensor.

---

## Scope

**In:**

- A new `CameraSnapshotPoller` class in `capture.py` that implements the same interface as `CameraCapture` (start/stop/recent_frames/is_running/camera_name) but acquires frames via periodic HTTP snapshot instead of RTSP.
- A new `ReolinkSnapshotSource` adapter in `capture.py` that wraps a `CameraController` and exposes a uniform `fetch() -> Optional[bytes]` method. This is the snapshot abstraction layer Phase B and Phase C will plug into.
- Optional `jpeg_bytes` field on `FrameResult` so the dashboard can serve the camera's original JPEG directly without a re-encode loss.
- `FrameCaptureManager.add_camera()` accepts an optional `snapshot_source` parameter and dispatches accordingly.
- New per-camera config fields: `source: "snapshot"`, `snapshot_method: "reolink"`, `snapshot_interval` (default 5.0s), `night_snapshot_interval` (optional override active during the night detection window).
- `guardian.py` setup loop reads the new fields and routes the Reolink (`house-yard`) to the snapshot poller.
- `config.json` + `config.example.json` updated so house-yard runs in snapshot mode.
- `dashboard.py` `/api/cameras/{name}/frame` and `/stream` prefer `FrameResult.jpeg_bytes` when present (zero re-encode).
- ONVIF discovery still runs for house-yard (we want the motion-event subscription Phase C will use, plus we want the controller to confirm the camera is reachable). The RTSP URL it returns is just ignored for snapshot-mode cameras.
- CHANGELOG entry, CLAUDE.md updates, plan doc (this file).

**Out:**

- GWTC laptop changes (Phase B).
- USB-cam high-res switchover (Phase C).
- Wiring ONVIF motion events to trigger snapshot bursts (Phase C).
- Removing the `CameraCapture` (RTSP) class — gwtc and usb-cam still use it.

---

## Architecture

### New types in `capture.py`

```python
class SnapshotSource(Protocol):
    """Anything that can produce a JPEG on demand."""
    @property
    def label(self) -> str: ...
    def fetch(self) -> Optional[bytes]:
        """Return JPEG bytes or None on failure. Must be safe to call from a worker thread."""

class ReolinkSnapshotSource:
    """Wraps a CameraController for the Reolink HTTP snapshot endpoint."""
    def __init__(self, controller: 'CameraController', camera_id: str): ...
    def fetch(self) -> Optional[bytes]:
        return self._controller.take_snapshot(self._camera_id)

class CameraSnapshotPoller:
    """Periodically polls a SnapshotSource and pushes FrameResults to a ring buffer.

    Implements the same start/stop/recent_frames/is_running/camera_name surface
    as CameraCapture so FrameCaptureManager can treat them interchangeably.
    """
    def __init__(
        self,
        camera_name: str,
        source: SnapshotSource,
        snapshot_interval: float = 5.0,
        night_snapshot_interval: Optional[float] = None,
        is_night_window: Optional[Callable[[], bool]] = None,
        on_frame: Optional[Callable[[FrameResult], None]] = None,
        buffer_size: int = 10,
    ): ...
```

### `FrameResult` extension

```python
@dataclass
class FrameResult:
    frame: np.ndarray            # decoded BGR for YOLO; possibly downscaled to _TARGET_WIDTH
    camera_name: str
    timestamp: float
    original_width: int
    original_height: int
    jpeg_bytes: Optional[bytes] = None   # camera-encoded JPEG when available, for zero-loss display
```

### Poller loop

```
while not stop:
    interval = night_snapshot_interval if (night_snapshot_interval and is_night_window and is_night_window()) else snapshot_interval
    jpeg = source.fetch()
    if jpeg is None:
        log.warning("Camera '<name>' — snapshot fetch returned None; will retry in <interval>s")
        wait(interval)
        continue
    arr = np.frombuffer(jpeg, np.uint8)
    raw = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if raw is None:
        log.warning("Camera '<name>' — JPEG decode failed (rare)")
        wait(interval)
        continue
    h, w = raw.shape[:2]
    frame = downscale_to_target_width(raw)  # reuse existing helper
    result = FrameResult(frame, name, time.time(), w, h, jpeg_bytes=jpeg)
    buffer.append(result)
    if on_frame: on_frame(result)
    wait(interval)
```

No reconnect logic, no exponential backoff, no decode-garbage filter — none of those failure modes apply to camera-encoded JPEGs over a single HTTP request.

### Detection cadence

Detection still runs through the same `on_frame` callback in `guardian.py`. The night-window gate is unchanged. Only difference: at night the poller speeds up to `night_snapshot_interval` (default 2s) so YOLO has more chances per minute to catch a slow-moving nocturnal predator.

Default rates:
- **Day:** snapshot every 5s. Dashboard updates 12 times per minute. Detection callback fires 12 times per minute but the night-window gate skips inference.
- **Night (during detection window):** snapshot every 2s. Dashboard updates 30 times per minute. YOLO runs 30 times per minute.

A fox/coyote/raccoon/bobcat/possum lingers in frame 10–30s. 2s polling = 5–15 chances to detect. Plenty.

If Boss wants higher detection sensitivity later, drop `night_snapshot_interval` to 1.0s or even 0.5s — at 4K the camera handles it fine.

### `FrameCaptureManager.add_camera()` dispatch

```python
def add_camera(
    self, camera_name, *,
    rtsp_url=None, rtsp_transport=None, device_index=None,
    frame_interval=None,
    snapshot_source=None,
    snapshot_interval=None,
    night_snapshot_interval=None,
    is_night_window=None,
):
    if snapshot_source is not None:
        cap = CameraSnapshotPoller(
            camera_name=camera_name,
            source=snapshot_source,
            snapshot_interval=snapshot_interval if snapshot_interval is not None else 5.0,
            night_snapshot_interval=night_snapshot_interval,
            is_night_window=is_night_window,
            on_frame=self._on_frame,
        )
    else:
        # existing CameraCapture path unchanged
        cap = CameraCapture(...)
    self._captures[camera_name] = cap
    cap.start()
```

### `guardian.py` setup loop changes

Where the loop currently builds the cameras into the manager, add a branch for `source == "snapshot"`:

```python
source_kind = (cam_cfg.get("source") if cam_cfg else None) or "rtsp"
if source_kind == "snapshot":
    method = cam_cfg.get("snapshot_method", "reolink")
    if method == "reolink":
        snap_src = ReolinkSnapshotSource(self._camera_ctrl, cam.name)
    else:
        log.error("Camera '%s' has unknown snapshot_method=%r — skipping", cam.name, method)
        continue
    self._capture_manager.add_camera(
        cam.name,
        snapshot_source=snap_src,
        snapshot_interval=cam_cfg.get("snapshot_interval", 5.0),
        night_snapshot_interval=cam_cfg.get("night_snapshot_interval"),
        is_night_window=self._detection_window_open,
    )
elif cam.source == "usb" and cam.device_index is not None:
    ...   # existing USB path
elif cam.rtsp_url:
    ...   # existing RTSP path
```

The dispatch order must check `source == "snapshot"` *before* falling through to the RTSP path, so the camera's RTSP URL (still discovered via ONVIF for completeness) is ignored.

### `dashboard.py` zero-loss serving

```python
@app.get("/api/cameras/{name}/frame")
async def camera_frame(name: str):
    frame_result = _service._capture_manager.get_latest_frame(name)
    if frame_result:
        if frame_result.jpeg_bytes is not None:
            return StreamingResponse(iter([frame_result.jpeg_bytes]),
                                     media_type="image/jpeg",
                                     headers={"Cache-Control": "no-cache, no-store"})
        # fall through to the existing re-encode path for RTSP cameras
        _, jpeg = cv2.imencode(".jpg", frame_result.frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return StreamingResponse(iter([jpeg.tobytes()]),
                                 media_type="image/jpeg",
                                 headers={"Cache-Control": "no-cache, no-store"})
```

Same shape change for the MJPEG `/stream` generator.

### Config schema

```jsonc
{
  "name": "house-yard",
  "ip": "192.168.0.88",
  "port": 80,
  "username": "admin",
  "password": "...",
  "onvif_port": 8000,
  "type": "ptz",
  "rtsp_transport": "tcp",      // unused in snapshot mode but harmless
  "source": "snapshot",          // NEW — switches to HTTP snapshot polling
  "snapshot_method": "reolink",  // NEW — picks the SnapshotSource adapter
  "snapshot_interval": 5.0,      // NEW — seconds between snapshots (was overloaded for non-detection RTSP)
  "night_snapshot_interval": 2.0, // NEW — overrides snapshot_interval during night detection window
  "detection_enabled": true
}
```

### Things to re-use, not rewrite

- `CameraController.take_snapshot()` — already implemented and tested via the REST endpoint.
- `_TARGET_WIDTH = 1920` downscale logic — same call.
- `FrameCaptureManager` lifecycle, the ring buffer, the lock pattern.
- `_detection_window_open()` callable already exists on `GuardianService`.

---

## TODOs (ordered, with verification)

1. **Extend `FrameResult`** in `capture.py` with `jpeg_bytes: Optional[bytes] = None`. Verify nothing breaks: current `CameraCapture` doesn't set this field, default is None, dashboard fall-through path handles None. Unit-test by importing.

2. **Add `SnapshotSource` Protocol + `ReolinkSnapshotSource`** in `capture.py`. The adapter is ~10 lines.

3. **Add `CameraSnapshotPoller`** in `capture.py`. Mirrors `CameraCapture`'s public surface. Uses the same `_TARGET_WIDTH` downscale helper (extract `_process_frame` to a module-level function `_downscale_to_target_width(raw)` so both classes can use it without copying).

4. **Update `FrameCaptureManager.add_camera()`** to accept `snapshot_source`, `snapshot_interval`, `night_snapshot_interval`, `is_night_window`. Dispatch to either class.

5. **Update `guardian.py`** setup loop with the snapshot-mode branch (in both initial setup and the periodic re-scan path — there are two call sites). Pass `self._camera_ctrl` and `self._detection_window_open` through.

6. **Update `dashboard.py`** `/frame` and `/stream` handlers to prefer `frame_result.jpeg_bytes` when present.

7. **Update `config.json`** — switch `house-yard` to snapshot mode (`source: "snapshot"`, `snapshot_method: "reolink"`, `snapshot_interval: 5.0`, `night_snapshot_interval: 2.0`). Keep `rtsp_transport: "tcp"` and `rtsp_stream: "sub"` as harmless leftover (they're ignored in snapshot mode but the docs/operator might expect them).

8. **Update `config.example.json`** the same way.

9. **Update file headers** for `capture.py`, `dashboard.py`, `guardian.py`. Bump version mentions to v2.18.0.

10. **Update `CLAUDE.md`** — Reolink description should now say "polls 4K HTTP snapshots, not RTSP". Remove the line about "Pulls the ONVIF sub-stream" added in v2.17.0.

11. **Update `CHANGELOG.md`** with v2.18.0 entry. Cite this plan doc.

12. **Restart Guardian** and verify:
    - `pgrep -fl guardian.py` shows one process.
    - Local + tunnel both 200.
    - `curl -o /tmp/snap.jpg http://localhost:6530/api/cameras/house-yard/frame` returns a ~1.3MB JPEG (vs ~100KB on sub-stream).
    - `python -c 'from PIL import Image; print(Image.open("/tmp/snap.jpg").size)'` shows 3840×2160.
    - `grep -E "house-yard.*snapshot|garbage|hung" guardian.log | tail` shows snapshot polling activity and **zero** decode-garbage rejections (because there are no RTSP frames anymore).
    - During the night window, observe the cadence change (interval drops to ~2s). If testing during the day, temporarily flip `night_snapshot_interval` to a lower value to see it kick in via a manual `_detection_window_open()` override (or just trust the implementation and verify the next night).

13. **Commit + push.** Per the user's standing rule (`Always commit and push`). Use a verbose commit message that names the version, reasons, and validation. Push to `main`.

14. **Update CLAUDE.md TODO list** — Phase A is complete; note that Phase B + Phase C plans are in `docs/`.

---

## Risks / things to think about while implementing

- **Snapshot fetch latency vs polling interval.** A 4K JPEG takes ~1.1s end-to-end through `take_snapshot`. With `snapshot_interval=5.0` that's fine. With `night_snapshot_interval=2.0` that's also fine (2s − 1.1s = ~0.9s sleep between attempts). Don't go below 1.5s or fetches will overlap. Add a guard: if interval < 1.5s, log a warning and clamp.

- **Camera_controller thread safety.** `CameraController._run_async()` schedules onto a single asyncio event loop in a dedicated thread. Concurrent `take_snapshot` calls from multiple pollers (we only have one Reolink today, but plan ahead) serialize through the event loop. That's correct behavior — Reolink HTTP API is not bottomlessly concurrent anyway.

- **PTZ + snapshot collision.** When patrol is moving the camera, snapshots taken mid-move will be motion-blurred. That's acceptable; patrol does step-and-dwell with 8s dwells, and a 5s snapshot interval almost always lands inside a dwell. If quality matters during patrol, add a check `if patrol_pause_event.is_set(): skip` — but probably not needed.

- **Token expiry inside reolink_aio.** The library refreshes its own auth token. If a snapshot fails with auth error, it auto-retries. We just see None back occasionally on the first try after a long idle. Not worth special handling — the next interval picks up.

- **Detection at 2s vs 4fps.** The previous architecture ran YOLO at 4fps when night window was open. The new one runs YOLO at 2s (0.5fps) — 8× lower load. For nocturnal slow-movers this is fine. Document in CHANGELOG so future operators don't think detection is "broken".

- **No live video for things that move fast.** A bird flying across at speed will be in frame for <1s and we'll never see it. That's an accepted tradeoff per Boss's directive — detection at night is for ground predators, not flying birds. (Hawk/raptor concern is daytime, and Phase C's motion-triggered bursts can address it if needed later.)

- **Backwards compat.** Cameras without `source: "snapshot"` continue on RTSP/USB exactly as before. gwtc/s7-cam/usb-cam are untouched.

---

## Docs / Changelog touchpoints

- `CHANGELOG.md` — new top entry for v2.18.0 (this work).
- `CLAUDE.md` — Reolink description, module-list note about CameraSnapshotPoller, possibly the "Recent Changes" section.
- `docs/` — this plan, plus the Phase B and Phase C plans (siblings).
- `config.example.json` — new fields documented by example.
