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

- [ ] A1. Reset `detection.confidence_threshold` from `0.99` to `0.45` in `config.json`
- [ ] A2. Create `.gitignore` — `config.json`, `guardian.log`, `data/`, `events/`, `__pycache__/`, `venv/`, `*.pt`, `.claude/`
- [ ] A3. Sanitize `config.json` — replace camera password and Discord webhook with placeholders, then add to `.gitignore` so the live file is never tracked again
- [ ] A4. Verify `config.example.json` has no real credentials (it shouldn't, but confirm)
- [ ] A5. Mask password in `discovery.py` log output — the RTSP URL `rtsp://admin:bird2026@...` gets printed every 5 minutes during camera rescans

### Phase B — Fix the Stream (Critical, Day 1)

- [ ] B1. Switch RTSP transport from UDP to TCP in `capture.py` — HEVC over WiFi/UDP drops every ~30s. TCP adds latency but eliminates drops. Either set `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp` or append transport param to RTSP URL.
- [ ] B2. Tune OpenCV read timeout — current default is 30s which causes long hangs before reconnect
- [ ] B3. Run `python guardian.py` for 10+ minutes, verify stream stability in `guardian.log`

### Phase C — Resolve WIP Commit (Important, Day 1)

- [ ] C1. `git diff HEAD~1` — review all 969 lines in the WIP commit across 7 files
- [ ] C2. For each file: determine if the changes are complete and working, or half-done
- [ ] C3. Keep finished work, revert incomplete changes, make a clean commit

### Phase D — Tune Detection (Important, Day 2)

- [ ] D1. Review false positive tracks — bear/dog ghosts with 0.0s duration and 1 detection are being created. The `min_dwell_frames: 3` filter correctly marks them `predator=False`, but they still create tracks in the DB. Evaluate whether `tracker.py` should skip creating tracks for single-frame detections entirely.
- [ ] D2. Review `bird_min_bbox_width_pct: 8.0` — verify this threshold makes sense for the current camera mount height and yard distance
- [ ] D3. Monitor live detections for 1 hour after confidence is restored to 0.45

### Phase E — Enable Advanced Features (Enhancement, Day 2–3)

- [ ] E1. **Vision model** — Set `vision.enabled: true` in config. Verify LM Studio is installed and running with `glm-4.6v-flash` model. Test with real detections to confirm hawk/chicken disambiguation.
- [ ] E2. **eBird raptor early warning** — Populate full eBird config block from `config.example.json`, set `ebird.enabled: true`. Verify API key works, Discord raptor alerts fire.
- [ ] E3. **PTZ patrol** — Discuss with Mark. The 5 presets (yard-center, coop-approach, fence-line, sky-watch, driveway) are configured but patrol is off. Needs owner decision on whether auto-cycling is desirable vs keeping the camera pointed at the coop.

### Phase F — Remote Access (Enhancement, Day 3)

- [ ] F1. Try `cloudflared tunnel --protocol http2 run --token "$CLOUDFLARE_TUNNEL_TOKEN"` — the QUIC connections die within 10–15s, HTTP/2 fallback on port 443 should work
- [ ] F2. If tunnel connects, test from phone on cellular: visit `guardian.markbarney.net`
- [ ] F3. Set up LaunchAgent for persistence across reboots (template already in `CLOUDFLARE_TUNNEL.md`)
- [ ] F4. Add authentication — Cloudflare Access (free tier, up to 50 users) or FastAPI basic auth middleware before the dashboard goes public

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
