# Modularization Plan — 14-Apr-2026

**Goal:** no single Python file over ~500 lines, aiming for a 300–400 line comfort zone, so a coding agent loading a default 400-line window sees a whole unit. Improve SRP at the file level; eliminate the "god file" shape in `dashboard.py`, `database.py`, `capture.py`, and `guardian.py`.

This plan must be approved before any code is written (per `CODING_STANDARDS.md`).

---

## Current state

```
capture.py        916   god file — 7 classes, one responsibility family
guardian.py       872   orchestrator, leaning god-object
database.py       855   one class, 30+ methods, 8 aggregates
dashboard.py      654   one create_app() with 30+ route closures
camera_control.py 626   one class, ~30 methods, 6 feature areas
api.py            396   already a clean APIRouter factory
alerts.py         349   OK
discovery.py      344   OK
reports.py        293   OK
patrol.py         274   OK
tracker.py        250   OK
ebird.py          249   OK
detect.py         241   OK
deterrent.py      218   OK
logger.py         203   OK
```

Five files exceed the 500-line ceiling. The plan addresses them in ROI order.

---

## Scope

**In scope**

1. Split `dashboard.py`, `database.py`, `capture.py`, `guardian.py`, `camera_control.py` into packages or sibling modules so every resulting file is ≤ ~500 lines (target 300–400).
2. Preserve every existing public import and call site unless explicitly called out below. External touchpoints today:
   - `guardian.py` imports `FrameCaptureManager, FrameResult, HttpUrlSnapshotSource, ReolinkSnapshotSource, UsbSnapshotSource` from `capture`.
   - `guardian.py` imports `start_dashboard` from `dashboard`.
   - Seven files import `GuardianDB` from `database`.
   - `camera_ctrl.*` is called from `guardian.py`, `patrol.py`, `deterrent.py`.
3. Reconcile the duplicated surface between `dashboard.py` (web UI) and `api.py` (LLM tools) — see DRY note below.
4. Update headers per standards on every touched file; update `CHANGELOG.md`.

**Out of scope**

- No behavior changes. No bug fixes, no feature additions, no new dependencies.
- No renames of public functions/classes. No change to DB schema, wire formats, or config keys.
- No touching the `tools/pipeline/` offline pipeline — it is already modular and under 300 lines per file.
- No refactoring of files already under 400 lines (api.py, alerts.py, discovery.py, …). A DRY pass against `api.py` is flagged as a follow-up, not part of this work.

---

## Architecture

### 1. `dashboard.py` → `dashboard/` package (highest ROI, lowest risk)

FastAPI already supports the pattern — `api.py:create_api_router()` is the template. `dashboard.py` becomes a thin composer. The thirty-plus route closures move into domain routers.

```
dashboard/
  __init__.py           # re-exports start_dashboard so `from dashboard import ...` still works
  app.py                # create_app(): mounts static files + routers; ~80 lines
  server.py             # start_dashboard(service, config, ...); ~60 lines
  state.py              # module-level _service / _config accessors + FastAPI Depends shims; ~50 lines
  routers/
    __init__.py
    status.py           # /, /api/status                              ~80 lines
    cameras.py          # /api/cameras/* (list/stream/frame/capture)  ~180 lines
    detections.py       # /api/detections/*                           ~50 lines
    events.py           # /api/events/*, /api/snapshots/*             ~70 lines
    alerts.py           # /api/alerts/*                               ~40 lines
    config.py           # /api/config, /api/config/detection, alerts  ~100 lines
    ptz.py              # /api/ptz/*                                  ~110 lines
    deterrent.py        # /api/deterrent/status                       ~30 lines
    reports.py          # /api/reports/*                              ~70 lines
    tracks.py           # /api/tracks/active                          ~40 lines
```

No route paths change. `start_dashboard(...)` signature preserved. `from dashboard import start_dashboard` in `guardian.py` keeps working via the package `__init__`.

### 2. `database.py` → `database/` package

Two honest options. Boss picks.

**Option A — mixin composition (recommended for this codebase).**
Zero call-site changes. Every `db.<method>(…)` across the seven caller files keeps working.

```
database/
  __init__.py       # class GuardianDB(_Connection, _CamerasMixin, _DetectionsMixin, _TracksMixin,
                    #                  _AlertsMixin, _DeterrentsMixin, _AnalyticsMixin,
                    #                  _SummariesMixin, _EBirdMixin, _BackupMixin): pass
  _connection.py    # __init__, _init_db, close, _SCHEMA_SQL         ~160 lines
  cameras.py        # _CamerasMixin                                  ~80 lines
  detections.py     # _DetectionsMixin                               ~90 lines
  tracks.py         # _TracksMixin                                   ~130 lines
  alerts.py         # _AlertsMixin                                   ~50 lines
  deterrents.py     # _DeterrentsMixin                               ~70 lines
  analytics.py      # _AnalyticsMixin (counts/hours/patterns)        ~150 lines
  summaries.py      # _SummariesMixin                                ~60 lines
  ebird.py          # _EBirdMixin                                    ~60 lines
  backup.py         # _BackupMixin                                   ~60 lines
```

Trade-off: mixins are less IDE-discoverable than real repos. Pay-off: a one-session refactor with nothing to sweep across the rest of the code.

**Option B — repository facade (closer to the letter of `CODING_STANDARDS.md`'s "repositories/services pattern").**
Same file layout, but each module exposes a `Repository` class. `GuardianDB` holds them as attributes:

```python
self.cameras    = CameraRepo(self._conn, self._lock)
self.detections = DetectionRepo(self._conn, self._lock)
...
```

Every call site updates: `db.get_or_create_camera(...)` → `db.cameras.get_or_create(...)`. Grep-friendly, ~60 call-site edits across 7 files. Cleaner architecture; higher churn.

**Recommendation:** Option A. The standards also say "avoid over-engineering (small hobby project)" — mixins hit the file-size goal with no behavioral risk. If Boss prefers B, the file structure is the same, only the composition differs.

### 3. `capture.py` → `capture/` package

Internal structure is already clean; this is pure file partitioning. Re-export everything from the package root so `from capture import FrameCaptureManager, FrameResult, HttpUrlSnapshotSource, ReolinkSnapshotSource, UsbSnapshotSource` in `guardian.py` keeps working unchanged.

```
capture/
  __init__.py          # re-exports the public names                 ~30 lines
  frame.py             # FrameResult dataclass, _downscale helper    ~60 lines
  rtsp_capture.py      # CameraCapture (RTSP/USB via OpenCV)        ~280 lines
  sources.py           # SnapshotSource Protocol + Reolink + USB +
                       # HttpUrl adapters                            ~230 lines
  snapshot_poller.py   # CameraSnapshotPoller                        ~210 lines
  manager.py           # FrameCaptureManager                         ~110 lines
```

If `sources.py` exceeds 400, split `http_source.py` out (HttpUrlSnapshotSource is the biggest at ~83 lines and is the newest, so it owns its churn).

### 4. `guardian.py` → split siblings, keep thin orchestrator

Resist over-splitting the orchestrator. Extract the clearly-separate concerns and leave the `GuardianService` lifecycle intact.

```
guardian.py               # GuardianService.__init__/start/stop/_signal_handler +
                          # _on_frame + _get_camera_config                       ~500 lines
config.py                 # load_config, setup_logging, main (CLI entrypoint)    ~110 lines
detection_window.py       # _clock_to_minutes, _window_allows_minutes,
                          # _detection_window_open (pure functions)              ~60 lines
camera_registry.py        # _register_camera_capture, _rescan_loop,
                          # _motion_watch_loop, _cleanup_loop                    ~260 lines
```

`camera_registry.py` groups everything about "which cameras are wired in and which background loops tend them." Pure helpers move to `detection_window.py`. The CLI/bootstrap split (`config.py`) is the cheapest 110-line win and should happen first.

Caveat: `main()` today lives at the bottom of `guardian.py`. Moving it means the run command changes from `python guardian.py` to `python -m farm_guardian` or similar. Cheapest preservation: keep a 3-line `guardian.py` shim that calls `config.main()`, or keep `main()` in `guardian.py` and only move `load_config` + `setup_logging`. **Recommended: keep `main()` in `guardian.py`; move only `load_config` and `setup_logging`.** Entry point unchanged.

### 5. `camera_control.py` → facade + feature modules (lowest priority)

At 626 lines this is the least urgent. Extract private helpers that take a `Host` (and channel) as a parameter; `CameraController` stays as the public facade that owns the event loop, auth cache, and connection registry. Call sites (`camera_ctrl.ptz_move(...)`, etc.) stay identical.

```
camera_control.py         # CameraController facade: __init__/_run_loop/_run_async,
                          # connect/disconnect, _get_host, close,
                          # delegating thin wrappers                             ~260 lines
camera/
  __init__.py
  deterrents.py           # spotlight_on/off/timed, siren_on/off/timed          ~110 lines
  ptz.py                  # ptz_move/stop/goto/save, presets, patrol,
                          # pan/tilt/position/zoom                               ~260 lines
  autofocus_guard.py      # ensure_autofocus, trigger_autofocus,
                          # guard enable/disable/position                        ~110 lines
  snapshot.py             # take_snapshot, get_motion_state                      ~50 lines
```

Can be deferred until after the four above if time-boxed.

---

## DRY follow-up (flag, not fix in this plan)

`dashboard.py` exposes `/api/ptz/*`, `/api/alerts/*`, `/api/detections/*`, `/api/reports/*`, etc. `api.py` exposes `/api/v1/cameras/{id}/ptz`, `/api/v1/detections`, etc. There is real overlap — PTZ control, detection lists, reports all have two implementations, one keyed by camera **name** (dashboard, web-UI convention) and one by camera **id** (api, LLM-tool convention).

The dashboard router split will make this side-by-side obvious. After the split:

- Decide whether LLM tools should continue to route through `/api/v1/*` or migrate to calling the same routers.
- Extract a `camera_lookup.py` helper that resolves "name or id" to a camera record, used by both router sets.

This is deliberately out of scope for this refactor. Tracking in a follow-up plan.

---

## TODOs (ordered, each step is a commit)

1. **Land plan.** This doc, committed, pushed, awaiting Boss approval. *No code changes yet.*
2. **Dashboard router split.**
   1. Create `dashboard/` package skeleton (`app.py`, `server.py`, `state.py`, `routers/__init__.py`).
   2. Move routes into domain routers one router at a time, verifying with a local browser hit after each.
   3. Delete `dashboard.py`, leaving `dashboard/__init__.py` re-exporting `start_dashboard`.
   4. Verify: `python guardian.py --config config.json`, load the dashboard in a browser, click through each tab. Test `/api/config` save round-trip and one PTZ button.
3. **Database mixin split.** (Assumes Option A approval.)
   1. Create `database/` package with `_connection.py` and one mixin module at a time.
   2. Assemble `class GuardianDB(...)` in `database/__init__.py`.
   3. Delete `database.py`.
   4. Verify: service starts, schema init runs clean on a fresh `guardian.db`, a predator detection end-to-end writes rows to `cameras`, `detections`, `tracks`, `alerts`, `deterrent_actions`. Daily report generates.
4. **Capture package split.**
   1. Create `capture/` with re-exporting `__init__.py`.
   2. Move one class per commit (`frame.py` → `rtsp_capture.py` → `sources.py` → `snapshot_poller.py` → `manager.py`).
   3. Verify: RTSP camera (mba-cam), snapshot camera (reolink), and HTTP-URL camera (s7-cam) all stream to dashboard.
5. **Guardian split (conservative).**
   1. Extract `load_config` and `setup_logging` to `config.py`. Leave `main()` in `guardian.py`.
   2. Extract `_clock_to_minutes`, `_window_allows_minutes`, `_detection_window_open` to `detection_window.py`; `GuardianService._detection_window_open` becomes a one-line delegate.
   3. Extract `_register_camera_capture`, `_rescan_loop`, `_motion_watch_loop`, `_cleanup_loop` to `camera_registry.py` as free functions taking `self`-equivalent state, or as a `CameraRegistry` helper class `GuardianService` owns.
   4. Verify: cold start, rescan cycle (kill a camera mid-run, watch reconnect), hit the nightly cleanup at window boundary, SIGINT shutdown clean.
6. **Camera control split (optional, deferrable).**
   1. Extract `camera/deterrents.py`, `camera/ptz.py`, `camera/autofocus_guard.py`, `camera/snapshot.py` as helpers taking `(host, channel)`.
   2. Rewrite `CameraController` public methods as thin delegates.
   3. Verify: manual PTZ move from dashboard, spotlight on/off, siren test, preset goto, snapshot endpoint, autofocus trigger.
7. **Verification pass.**
   - Line-count audit: `find . -name '*.py' -not -path './.git/*' -exec wc -l {} + | sort -rn | head -20` — every entry ≤ 500.
   - Full boot of Guardian with real cameras; monitor a 30-minute window; check a real detection end-to-end (frame → detect → track → alert → Discord).
   - `CHANGELOG.md` entry per `CODING_STANDARDS.md`: major version bump (internal layout change; no public behavior change) with rationale (agent readability).

Each step lands as its own commit with the usual verbose message and a CHANGELOG entry. No step is declared done until Boss confirms a live check of the changed surface.

---

## Docs / Changelog touchpoints

- **This plan** — `docs/14-Apr-2026-modularization-plan.md` — approval gate.
- **CHANGELOG.md** — one top entry per commit in the sequence above (dashboard split, database split, capture split, guardian split, optional camera-control split). SemVer minor bumps; no public behavior change but a real architectural shift.
- **Follow-up plan** — dashboard/api DRY reconciliation — to be filed after step 2 lands, once the overlap is visible in the new router tree.
- **No CLAUDE.md / AGENTS*.md updates required** — current files don't reference internal module layout. If any do (to be grepped at step 1), update them in the matching step.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Hidden import of a private name (e.g. `from dashboard import create_app`) | Grep every moved symbol across the repo before deleting the old file; re-export from package `__init__` by default. |
| Mixin method resolution order surprises in `GuardianDB` | Each mixin touches a disjoint method set (verified in audit above); MRO is a straight left-to-right over disjoint names. Add a smoke test that instantiates `GuardianDB` and asserts every pre-split method name resolves. |
| `cv2` / threading import order regression in capture split | Preserve the `OPENCV_FFMPEG_CAPTURE_OPTIONS` env-set in `guardian.py` before any capture import; don't move that line. |
| Entry-point regression (`python guardian.py`) | Keep `main()` in `guardian.py`. Only helpers move out. |
| Call-site churn breaking something silently | One commit per split, with a live-camera smoke test after each. Refuse to batch. |
| Agents reading stale plans | On approval, this doc stays as the source of truth; any deviation recorded in CHANGELOG and a note appended here. |

---

## Decisions needed from Boss before step 2 begins

1. **Database split: Option A (mixins, zero call-site churn) or Option B (repo-facade, call-site sweep)?** Recommend A.
2. **Camera-control split (step 6): execute or defer?** It is the lowest-ROI item at 626 lines. Deferring keeps scope tight.
3. **DRY follow-up timing:** file the dashboard/api reconciliation plan now or after step 2 lands? Recommend after — the split makes the overlap concrete.

Awaiting approval.
