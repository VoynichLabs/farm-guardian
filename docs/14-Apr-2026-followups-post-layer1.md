# Follow-ups surfaced while shipping v2.25.0 (image archive REST)

**Author:** Claude Opus 4.6 (1M context)
**Date:** 14-April-2026
**Status:** Open — neither is blocking farm-2026's frontend work, but both deserve a plan.

## 1. `guardian.py` is becoming a god-component

**Observation:** `guardian.py` is 879 lines. `class GuardianService` spans 64 → 760 (696 lines, one class). It owns:

- discovery, capture, detection, tracker, alerts, logger, deterrent, patrol, camera_control, ebird, reports, and dashboard lifecycle
- motion-event handler, periodic rescan, end-of-day report, daily backup thread, SIGTERM/SIGINT handlers
- config reload, status counters, test-alert helper, the runtime thread budget

Boss called it "monster god component" — correct. Adding the image-archive REST surface went into *new* modules on purpose (`images_api.py`, `images_auth.py`, `images_thumb.py`) rather than growing `GuardianService`, but the parent file is already at the size where new feature work tends to pile in.

**Proposed decomposition (for a separate plan, not this PR):**

| Concern | Would live in |
|---|---|
| Process lifecycle (`main()`, `setup_logging()`, signal handlers) | `guardian.py` (thin; ~150 lines) |
| Config loading + env overlay | `config_loader.py` |
| Module initialization (DB, alerts, logger, tracker, deterrent, reports, ebird, camera_control, discovery) | `GuardianService.__init__` → `ServiceBuilder` in `service_builder.py` |
| Per-camera worker orchestration (capture threads, motion watcher, periodic rescan) | `CameraRuntime` in `camera_runtime.py` |
| Detection + tracker + alerts + deterrent event loop | `DetectionPipeline` in `detection_pipeline.py` |
| Daily maintenance (backup, report, logs rotate) | `Maintenance` in `maintenance.py` |
| Dashboard registration + API wiring | stays in `dashboard.py` / `api.py` (already SRP-clean) |

Each extracted module gets its own `# Author / Date / PURPOSE / SRP/DRY check` header per `CODING_STANDARDS.md`. `GuardianService` becomes a thin composition root: instantiate builders in the right order, hand them to a coordinator, start, block on shutdown.

**Recommended next step:** draft a `docs/{date}-guardian-service-decomposition-plan.md` and get Boss's approval before touching the code. This is a refactor that touches the running service so it warrants a careful plan + a big verify-the-live-system step.

## 2. `com.farm.guardian` launchd service is crash-looping

**Observed 14-Apr-2026 during the v2.25.0 rollout:**

- After an hour or so of normal service, the launchd-managed Guardian process was left with `PPID=1` (parent detached). `launchctl kickstart -k` spawned a new instance, which exited with `LastExitStatus = 19968` (= exit code 78, `sysexits.h` `EX_CONFIG`). launchd's 10s `ThrottleInterval` kept respawning new instances, all of which exited almost immediately. The existing `PPID=1` process kept serving port 6530.
- **After I sent `SIGTERM` to that surviving process,** launchd's respawn *still* produced exit-78 processes that died before writing to `guardian.log` at all.
- **A foreground manual run** (`venv/bin/python guardian.py`) from an interactive shell booted fully, registered the API, held port 6530, and stayed stable. That's what's serving the image archive right now.
- One hand-spawned detached process (`nohup venv/bin/python guardian.py &`) is also stable.

**Hypothesis:** the failure is environmental, not code — launchd's sparse env (`PATH=/usr/bin:/bin:/usr/sbin:/sbin`, no HOME-derived brew paths, no `DYLD_LIBRARY_PATH`, no `.env` auto-load because we use `python-dotenv.load_dotenv()` which resolves cwd at runtime) is missing something that a subprocess Guardian needs after first boot. The `LastExitStatus = 19968` was present in `launchctl list com.farm.guardian` before I made any v2.25.0 changes, so this predates this PR.

**Things to check (not done yet):**

1. `launchctl debug gui/$(id -u)/com.farm.guardian --stdout - --stderr -` during a spawn to capture pre-logging stderr.
2. `EnvironmentVariables` in the plist — add `PATH`, `HOME`, and anything the `venv/bin/python` shim needs.
3. Does `load_dotenv()` find `.env` when cwd is set to the repo root? (It should, given `WorkingDirectory` is set in the plist.) Does a missing env var like `CAMERA_PASSWORD` throw a preflight assertion somewhere?
4. Is something (cloudflared healthcheck? a stale watchdog?) sending `SIGKILL` to the launchd-spawned processes once they come up? (Manual foreground runs survive — suggests something is specifically targeting launchd-managed PIDs.)
5. Does disabling `KeepAlive` briefly (`launchctl bootout`, restart manually, verify stability, re-bootstrap) isolate a launchd-vs-code issue?

**Current state left for Boss:** one manually-launched `guardian.py` serves port 6530 with the v2.25.0 image archive routes live. `com.farm.guardian` is bootstrapped but its respawns fail; they don't hurt the manual process (they exit fast without binding the port). On machine reboot, the manual process will be gone and launchd will resume respawning failed instances. Recommended fix window: next time Boss is at the box, investigate per the list above.

**Workaround if needed before then:** `nohup ~/Documents/GitHub/farm-guardian/venv/bin/python ~/Documents/GitHub/farm-guardian/guardian.py >> ~/Documents/GitHub/farm-guardian/guardian.log 2>&1 & disown` — that's the exact invocation running now.
