# Farm Guardian — Full Cleanup & Stabilization Plan

**Author:** Mark Barney (Claude Opus 4.6)
**Date:** 04-April-2026
**Status:** Plan — Awaiting approval

---

## Context

The previous developer built all 15 modules (Phases 1–4) in 48 hours across multiple AI models. The architecture is sound and the code is real — no stubs or placeholders. However, the system was left in a half-operational state:

- Detection is effectively disabled (confidence set to 0.99)
- RTSP stream drops every ~30 seconds over WiFi
- Camera credentials committed to git (no `.gitignore` exists)
- Cloudflare Tunnel broken (connections die within 10–15 seconds)
- Vision model, eBird, and PTZ patrol all disabled — built but never operationally tested
- HEAD commit is literally labeled "WIP" on main
- Zero test coverage across 5,075 lines of code

This plan addresses every issue in priority order. No new modules. No new features. Just making what exists actually work.

---

## Scope

### In scope
- Restore detection to operational state
- Fix RTSP stream stability
- Scrub credentials from git and add `.gitignore`
- Mask credentials in log output
- Resolve WIP commit on main
- Review and tune false positive thresholds
- Enable and verify vision model (GLM via LM Studio)
- Enable and verify eBird raptor early warning
- Discuss PTZ patrol with Mark (needs owner decision) 
- Fix Cloudflare Tunnel (try `--protocol http2`)
- Add authentication before public exposure
- Establish basic test coverage for critical path

### Out of scope
- New features or modules
- Smart device integration (`docs/02-Apr-2026-smart-devices-plan.md` — future)
- PostgreSQL sync (Phase 5)
- Custom YOLO model training
- Second camera integration

---

## Architecture

No new modules. All changes target existing files:

| File | Change | Why |
|------|--------|-----|
| `config.json` | Reset confidence 0.99 → 0.45, sanitize credentials | Detection is blind; creds in git |
| `.gitignore` | **Create** — config.json, data/, events/, logs, cache, models | File doesn't exist at all |
| `capture.py` | Switch RTSP to TCP transport, tune timeouts | Stream drops every ~30s over UDP/WiFi |
| `discovery.py` | Mask camera password in log output | Password logged in plaintext every 5 min |
| `tracker.py` | Evaluate skipping single-frame ghost tracks | Bear/dog false positives polluting DB |
| `detect.py` | Review bbox and confidence thresholds | May need tuning for current camera position |
| `static/app.js` | Review WIP changes — keep or revert | 796 lines changed in unfinished commit |
| `static/index.html` | Review WIP changes — keep or revert | 615 lines changed in unfinished commit |
| `CHANGELOG.md` | New `[2.1.0]` entry for cleanup work | Required per coding standards |
| `CLAUDE.md` | Update plan file references to `docs/` | Plans moved from root to docs/ |

---

## TODOs (ordered by priority)

### Phase A — Make It See Again (Critical, Day 1)

- [x] A1. Reset `detection.confidence_threshold` from `0.99` to `0.45` in `config.json`
- [x] A2. Updated `.gitignore` — added `.claude/`, `.env`. Already had `config.json`, `data/`, `events/`, etc.
- [x] A3. Sanitized `config.json` — replaced real password + Discord webhook with placeholders. Created `.env` for real secrets with `python-dotenv` integration.
- [x] A4. Verified `config.example.json` has no real credentials.
- [x] A5. Masked password in `discovery.py` log output — RTSP URLs now show `admin:***@`

### Phase B — Fix the Stream (Critical, Day 1)

- [x] B1. Forced TCP via `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp` env var at top of `guardian.py` (before any cv2 import). Verified via `lsof` — no UDP connections to camera.
- [x] B2. OpenCV's 30s interrupt callback is hardcoded and not configurable. Implemented threaded 10-second manual timeout on `cap.read()` — abandoned cap objects (not released, to avoid segfault) get GC'd. Reconnects in 10s instead of 30s.
- [x] B3. Verified: 10+ minute run, 508 frames, process stable, 0 frame failures. 6 reconnects from WiFi stalls (recovered in 10s each).

### Phase C — Resolve WIP Commit (Important, Day 1)

- [x] C1. Reviewed all 969 lines across 7 files in WIP commit (edab3c5).
- [x] C2. All changes complete and production-ready: dashboard redesign, camera_control port fix, dashboard DB queries, CHANGELOG entry.
- [x] C3. No reverts needed — committed clean on top of WIP.

### Phase D — Tune Detection (Important, Day 2)

- [x] D1. Ghost tracks (< `min_detections_for_track`) now deleted from DB on track close. Added `delete_track()` to `database.py`.
- [ ] D2. Review `bird_min_bbox_width_pct: 8.0` — verify this threshold makes sense for the current camera mount height and yard distance
- [ ] D3. Monitor live detections for 1 hour after confidence is restored to 0.45

### Phase E — Enable Advanced Features (Enhancement, Day 2–3)

- [ ] E1. **Vision model** — Set `vision.enabled: true` in config. Verify LM Studio is installed and running with `glm-4.6v-flash` model. Test with real detections to confirm hawk/chicken disambiguation.
- [ ] E2. **eBird raptor early warning** — Populate full eBird config block from `config.example.json`, set `ebird.enabled: true`. Verify API key works, Discord raptor alerts fire.
- [ ] E3. **PTZ patrol** — Discuss with Mark. The 5 presets (yard-center, coop-approach, fence-line, sky-watch, driveway) are configured but patrol is off. Needs owner decision on whether auto-cycling is desirable vs keeping the camera pointed at the coop.

### Phase F — Remote Access (Enhancement, Day 3)

- [x] F1. `--protocol http2` fixed the tunnel. QUIC/UDP port 7844 blocked by router/ISP; HTTP/2 on TCP 443 works.
- [x] F2. `guardian.markbarney.net` returns HTTP 200 through Cloudflare tunnel.
- [x] F3. LaunchAgent loaded and persistent (`com.cloudflare.tunnel.farm-guardian.plist`).
- [x] F4. Mark decided: no auth needed, dashboard is intentionally public.

### Phase G — Test Foundation (Nice to Have, Day 3+)

- [ ] G1. Create `tests/` directory with `pytest` configuration
- [ ] G2. Unit tests for `detect.py` — confidence filtering, predator classification, dwell frame logic, bbox size filter
- [ ] G3. Unit tests for `tracker.py` — track creation, timeout, close, merge, duration calculation
- [ ] G4. Unit tests for `deterrent.py` — escalation levels, cooldown enforcement, effectiveness window
- [ ] G5. Integration test — frame → detection → track → alert pipeline (with real YOLO model, test images)

---

## Docs/Changelog Touchpoints

| Document | Update needed |
|----------|---------------|
| `CHANGELOG.md` | New `[2.1.0]` entry at top — cleanup/stabilization work, credited to Mark Barney (Claude Opus 4.6) |
| `CLAUDE.md` | Update plan file references from root (`PLAN_V2.md`) to `docs/02-Apr-2026-v2-system-plan.md` |
| `config.example.json` | Verify no real credentials leaked; keep in sync with any config structure changes |
| `README.md` | Update if any user-facing behavior changes (port, endpoints, etc.) |
| `CLOUDFLARE_TUNNEL.md` | Update with resolution once tunnel is fixed |

---

## Verification

| Phase | Success criteria |
|-------|-----------------|
| A | `grep -r "bird2026" *.py` returns nothing. `.gitignore` exists. `config.json` not tracked. |
| B | 10 min run with zero RTSP drops in `guardian.log` |
| C | `git log -1` shows a clean, descriptive commit (not "WIP") |
| D | No bear/dog ghost tracks in 1 hour of monitoring |
| E | Logs show "Vision refinement:" entries and eBird poll cycles |
| F | `guardian.markbarney.net` loads live feed from phone on cellular |
| G | `python -m pytest` passes |
