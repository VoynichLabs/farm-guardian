# Farm Guardian — Full Cleanup & Stabilization Plan

**Author:** Mark Barney (Claude Opus 4.6)
**Date:** 04-April-2026
**Status:** IN PROGRESS — Phases A, B, C, F complete. D partially done. E, G outstanding.

---

## Context

The previous developer built all 15 modules (Phases 1–4) in 48 hours across multiple AI models. The architecture is sound and the code is real — no stubs or placeholders. However, the system was left in a half-operational state:

- ~~Detection is effectively disabled (confidence set to 0.99)~~ **FIXED**
- ~~RTSP stream drops every ~30 seconds over WiFi~~ **FIXED** (TCP transport + threaded timeout)
- ~~Camera credentials committed to git (no `.gitignore` exists)~~ **FIXED** (scrubbed + .env)
- ~~Cloudflare Tunnel broken (connections die within 10–15 seconds)~~ **FIXED** (HTTP/2 fallback)
- Vision model, eBird, and PTZ patrol all disabled — built but never operationally tested
- ~~HEAD commit is literally labeled "WIP" on main~~ **FIXED** (reviewed, all changes were complete)
- Zero test coverage across 5,075 lines of code

---

## Scope

### In scope
- ~~Restore detection to operational state~~ **DONE**
- ~~Fix RTSP stream stability~~ **DONE**
- ~~Scrub credentials from git and add `.gitignore`~~ **DONE**
- ~~Mask credentials in log output~~ **DONE**
- ~~Resolve WIP commit on main~~ **DONE**
- Review and tune false positive thresholds — **PARTIALLY DONE** (ghost tracks fixed, bbox/dwell tuning outstanding)
- Enable and verify vision model (GLM via LM Studio) — **OUTSTANDING**
- Enable and verify eBird raptor early warning — **OUTSTANDING**
- Discuss PTZ patrol with Mark (needs owner decision) — **OUTSTANDING**
- ~~Fix Cloudflare Tunnel~~ **DONE**
- ~~Add authentication before public exposure~~ **NOT NEEDED** (Mark: dashboard is intentionally public)
- Establish basic test coverage for critical path — **OUTSTANDING**

### Out of scope
- New features or modules
- Smart device integration (`docs/02-Apr-2026-smart-devices-plan.md` — future)
- PostgreSQL sync (Phase 5)
- Custom YOLO model training
- Second camera integration

---

## Architecture

No new modules. Changes made to existing files:

| File | Change | Status |
|------|--------|--------|
| `config.json` | Reset confidence 0.99 → 0.45, sanitize credentials | **DONE** |
| `.gitignore` | Added `.claude/`, `.env` | **DONE** |
| `.env` / `.env.example` | Created for secrets management with `python-dotenv` | **DONE** |
| `guardian.py` | Added `load_dotenv()`, env var overlay for secrets, `OPENCV_FFMPEG_CAPTURE_OPTIONS` for TCP | **DONE** |
| `capture.py` | TCP transport, threaded 10s read timeout (replaces hardcoded 30s), segfault-safe cap abandonment | **DONE** |
| `discovery.py` | Mask camera password in RTSP URL log output | **DONE** |
| `tracker.py` | Ghost tracks (< min_detections) deleted on close | **DONE** |
| `database.py` | Added `delete_track()` method | **DONE** |
| `detect.py` | Review bbox and confidence thresholds | **OUTSTANDING** |
| `static/app.js` | Reviewed WIP — all changes complete, no reverts | **DONE** |
| `static/index.html` | Reviewed WIP — all changes complete, no reverts | **DONE** |
| `CHANGELOG.md` | `[2.1.0]` entry with all cleanup work | **DONE** |
| `CLOUDFLARE_TUNNEL.md` | Updated status to WORKING, documented root cause + fix | **DONE** |
| `requirements.txt` | Added `python-dotenv>=1.0.0` | **DONE** |

---

## TODOs (ordered by priority)

### Phase A — Make It See Again ✅ COMPLETE

- [x] A1. Reset `detection.confidence_threshold` from `0.99` to `0.45` in `config.json`
- [x] A2. Updated `.gitignore` — added `.claude/`, `.env`. Already had `config.json`, `data/`, `events/`, etc.
- [x] A3. Sanitized `config.json` — replaced real password + Discord webhook with placeholders. Created `.env` for real secrets with `python-dotenv` integration.
- [x] A4. Verified `config.example.json` has no real credentials.
- [x] A5. Masked password in `discovery.py` log output — RTSP URLs now show `admin:***@`

### Phase B — Fix the Stream ✅ COMPLETE

- [x] B1. Forced TCP via `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp` env var at top of `guardian.py` (before any cv2 import). Verified via `lsof` — no UDP connections to camera.
- [x] B2. OpenCV's 30s interrupt callback is hardcoded and not configurable. Implemented threaded 10-second manual timeout on `cap.read()` — abandoned cap objects (not released, to avoid segfault) get GC'd. Reconnects in 10s instead of 30s.
- [x] B3. Verified: 10+ minute run, 508 frames, process stable, 0 frame failures. 6 reconnects from WiFi stalls (recovered in 10s each).

**Known limitation:** WiFi stalls cause ~6 reconnects per 10 minutes (10s each). This is a hardware/network issue, not a code issue. Options to improve: wired ethernet, camera sub-stream (720p), or H.264 instead of HEVC.

### Phase C — Resolve WIP Commit ✅ COMPLETE

- [x] C1. Reviewed all 969 lines across 7 files in WIP commit (edab3c5).
- [x] C2. All changes complete and production-ready: dashboard redesign, camera_control port fix, dashboard DB queries, CHANGELOG entry.
- [x] C3. No reverts needed — committed clean on top of WIP.

### Phase D — Tune Detection ⚠️ PARTIALLY DONE

- [x] D1. Ghost tracks (< `min_detections_for_track`) now deleted from DB on track close. Added `delete_track()` to `database.py`.
- [ ] D2. Review `bird_min_bbox_width_pct: 8.0` — verify this threshold makes sense for the current camera mount height and yard distance.
- [ ] D3. Monitor live detections for 1 hour after confidence is restored to 0.45. Check for false positive patterns.

### Phase E — Enable Advanced Features ❌ NOT STARTED

- [ ] E1. **Vision model** — Set `vision.enabled: true` in config. Verify LM Studio is installed and running with `glm-4.6v-flash` model. Test with real detections to confirm hawk/chicken disambiguation.
- [ ] E2. **eBird raptor early warning** — Populate full eBird config block from `config.example.json`, set `ebird.enabled: true`. Verify API key works, Discord raptor alerts fire. **Needs eBird API key in `.env`.**
- [ ] E3. **PTZ patrol** — Discuss with Mark. The 5 presets (yard-center, coop-approach, fence-line, sky-watch, driveway) are configured but patrol is off. **Needs owner decision** on whether auto-cycling is desirable vs keeping the camera pointed at the coop.

### Phase F — Remote Access ✅ COMPLETE

- [x] F1. `--protocol http2` fixed the tunnel. Root cause: QUIC/UDP port 7844 blocked by router/ISP; HTTP/2 on TCP 443 works.
- [x] F2. `guardian.markbarney.net` returns HTTP 200 through Cloudflare tunnel.
- [x] F3. LaunchAgent loaded and persistent (`com.cloudflare.tunnel.farm-guardian.plist`).
- [x] F4. Mark decided: no auth needed, dashboard is intentionally public.

### Phase G — Test Foundation ❌ NOT STARTED

- [ ] G1. Create `tests/` directory with `pytest` configuration
- [ ] G2. Unit tests for `detect.py` — confidence filtering, predator classification, dwell frame logic, bbox size filter
- [ ] G3. Unit tests for `tracker.py` — track creation, timeout, close, merge, duration calculation
- [ ] G4. Unit tests for `deterrent.py` — escalation levels, cooldown enforcement, effectiveness window
- [ ] G5. Integration test — frame → detection → track → alert pipeline (with real YOLO model, test images)

---

## Docs/Changelog Touchpoints

| Document | Status |
|----------|--------|
| `CHANGELOG.md` | ✅ `[2.1.0]` entry added — all cleanup work documented |
| `CLAUDE.md` | ✅ Updated (plan file references moved to `docs/`) |
| `config.example.json` | ✅ Verified clean — no real credentials |
| `CLOUDFLARE_TUNNEL.md` | ✅ Updated — status WORKING, root cause documented |
| `.env.example` | ✅ Created — template for all secrets |
| `README.md` | No changes needed — no user-facing behavior changes |

---

## Verification

| Phase | Success criteria | Result |
|-------|-----------------|--------|
| A | `grep -r "bird2026" *.py` returns nothing. `.gitignore` exists. `config.json` not tracked. | ✅ PASS |
| B | 10 min run with stable stream in `guardian.log` | ✅ PASS (508 frames, 0 crashes, 6 WiFi-stall reconnects) |
| C | `git log -1` shows a clean, descriptive commit (not "WIP") | ✅ PASS |
| D | No bear/dog ghost tracks in 1 hour of monitoring | ⚠️ Ghost track deletion coded, needs live monitoring |
| E | Logs show "Vision refinement:" entries and eBird poll cycles | ❌ NOT TESTED — features still disabled |
| F | `guardian.markbarney.net` loads live feed from phone on cellular | ✅ PASS |
| G | `python -m pytest` passes | ❌ NOT STARTED |

---

## Git History (this cleanup)

```
4b1f582 fix: RTSP stream stability — threaded read timeout, TCP transport, segfault fix
590a31d fix: Cloudflare tunnel working — HTTP/2 fallback, RTSP TCP at module level
9764d4f fix: stabilization cleanup �� restore detection, fix streams, scrub creds, add .env
```
