# 16-Apr-2026 — Max-volume capture plan + session handoff (Bubba side)

Author: Claude Opus 4.7 (1M context) — Bubba
Branch: `bubba/source-quality-plan-16-Apr`
Status: **ACTIVE** — handoff doc. Read the TL;DR first if you are the next session.

---

## TL;DR for the next session

1. **A stopgap frame scraper is running right now** at `/Users/macmini/farm-backlog/scraper.sh` (PID in `scraper.pid`). It pulls Guardian's `/api/cameras/<name>/frame` on a per-camera timer for all 5 cameras (gwtc, s7-cam, usb-cam, house-yard, mba-cam) and saves deduped JPEGs to `/Users/macmini/farm-backlog/<camera>/YYYY-MM-DD/`. Not a replacement for the pipeline — it exists because the pipeline has been stalled since 2026-04-15 ~22:59 ET and Boss wanted to stop losing frames. **Kill it** when the pipeline restarts: `pkill -f farm-backlog/scraper.sh`.
2. **No code changes in this branch.** Only this doc was added. The other dev was live in the repo during this session; I deliberately stayed on my own branch (`bubba/source-quality-plan-16-Apr`) and made zero edits to `capture.py`, `guardian.py`, `config.json`, pipeline code, or `usb-cam-host`.
3. **Boss's goal, verbatim:** max volume of pictures without overloading any machine. Gems emerge from sample count; junk is filtered by retention + pipeline grading. Boss is NOT asking for a quality overhaul — he's asking for throughput.
4. **Boss decided (this session):** (a) S7 is on AC via USB on the MBA — 10s cadence greenlit. (b) Don't retain `decent` tier for 90 days — **7 days** or drop the tier entirely. (c) Good-quality captures are coming out of GWTC and S7 right now; prioritize not losing them. (d) MBA cam quality improvement is in scope.
5. **The single biggest miss right now:** `tools/pipeline/config.json` has **`enabled: false`** on `s7-cam` AND `mba-cam`, with stale RTSP paths. The pipeline **has never captured a single S7 frame** (DB shows 0 rows for s7-cam, ever). S7 is the best-quality source in the fleet. Flipping these two to `enabled: true` with the correct HTTP-snapshot paths is the first real act of the next dev.
6. **Open — not yet landed:** per-camera cadence bumps, retention cut, stale pipeline config entries, orchestrator restart + LaunchAgent. All belong to the dev who owns the live repo today (author of v2.27.x). I flagged them, did not edit.

## Live state snapshot (taken 2026-04-16 ~21:20 UTC)

```
LM Studio loaded:  google/gemma-4-31b           (single model, ~98s per VLM call)
Guardian cameras:  5/5 online + capturing       (house-yard, s7-cam, usb-cam, mba-cam, gwtc)
usb-cam-host (Mini): healthy — 18,623 grabs, 5 failures, latest_frame_age_ms=226
Pipeline orchestrator: NOT RUNNING              (no process matching orchestrator)
LaunchAgents running:  com.farmguardian.guardian, com.farmguardian.usb-cam-host
LaunchAgents missing:  com.farmguardian.pipeline (needs creation)

DB row counts per camera (image_archive):
  gwtc         — 266 rows, latest 2026-04-16T02:59 (~12h stale)
  house-yard   — 292 rows, latest 2026-04-16T02:57 (~12h stale)
  usb-cam      — 1,312 rows, latest 2026-04-15T22:58 (~23h stale)
  mba-cam      — 403 rows, latest 2026-04-15T13:09 (~32h stale, MBA was decommed)
  s7-cam       — 0 rows, ever                     ← THE BIG MISS

Scraper backlog on disk (/Users/macmini/farm-backlog/, growing):
  All 5 cameras actively writing. Run `du -sh /Users/macmini/farm-backlog/*/` for current.
```

## Live diagnostics — commands to know

Run these in order on a fresh session. Expected outputs are the "happy path"; deviations are your first signal something changed.

```bash
# 1. Is the pipeline writing?
sqlite3 ~/Documents/GitHub/farm-guardian/data/guardian.db \
  "SELECT camera_id, MAX(ts) FROM image_archive GROUP BY camera_id;"
#    Happy: each camera's MAX(ts) is within the last 5-10 minutes.
#    Current: all stale >12h. s7-cam has no row at all.

# 2. Is LM Studio loaded with a vision model?
curl -s http://localhost:1234/v1/models | python3 -c \
  "import sys,json;d=json.load(sys.stdin);print([m['id'] for m in d.get('data',[])])"
#    Happy: list includes a *vl*, gemma-*, glm-*v*, qwen*vl* name.
#    Current: ['google/gemma-4-31b'].

# 3. Is Guardian's REST API serving frames?
curl -s http://localhost:6530/api/cameras | \
  python3 -c "import sys,json;print([(c['name'],c['online']) for c in json.load(sys.stdin)])"
#    Happy: 5 cameras, all online=True.

# 4. Is usb-cam-host healthy (Mini + MBA)?
curl -s http://localhost:8089/health                 # Mini's usb-cam
curl -s http://mba.local:8089/health                 # MBA's mba-cam (per v2.27.8)
#    Happy: grabber_alive=true, latest_frame_age_ms < 5000, total_failures growing slowly.

# 5. Is the stopgap scraper running?
ps -p "$(awk -F= '{print $2}' /Users/macmini/farm-backlog/scraper.pid)" -o pid,etime,command
tail -10 /Users/macmini/farm-backlog/scraper.log

# 6. Is the other dev still live in the repo?
cd ~/Documents/GitHub/farm-guardian && git status --short
cd ~/Documents/GitHub/farm-2026 && git status --short
#    Any non-empty output = someone is editing. Stay on your own branch until it clears.
```

## Config files + credentials — READ BEFORE EDITING ANYTHING

Two gitignored config files hold the live state. The tracked `config.example.json` and `tools/pipeline/config.example.json` are out of date the moment the next dev wants to land a cadence or retention change — always edit the live file AND update the example.

| File | Tracked? | What it controls | Note |
|---|---|---|---|
| `~/Documents/GitHub/farm-guardian/config.json` | NO (gitignored) | Guardian: cameras, detection, cadences, Discord, eBird | Copy of shape in `config.example.json` |
| `~/Documents/GitHub/farm-guardian/config.example.json` | YES | Contract for the above | Must match the live file's **shape**, not its secrets/URLs |
| `~/Documents/GitHub/farm-guardian/tools/pipeline/config.json` | NO (gitignored) | Pipeline: cameras, cadences, retention, VLM timeout | **Currently stale on s7-cam + mba-cam blocks** |
| `~/Documents/GitHub/farm-guardian/.env` | NO (gitignored) | `DISCORD_WEBHOOK_URL` etc. | Don't print; Discord skill doc explains |
| `~/Documents/GitHub/farm-guardian/data/guardian.db` | NO (gitignored) | SQLite, WAL mode, 8 tables | Back up before any schema change: `cp -a data/guardian.db /tmp/` |

Convention: any config change gets a matching update to `config.example.json` so the repo still describes reality. Per CLAUDE.md.

## Execution order for the next dev

Ship these in order. Each phase is landable on its own; don't jump ahead until the prior one is verified.

### Phase 1 — Make the pipeline safe to leave running (required before restart)

1. **Wrap `requests.ConnectionError` and `requests.Timeout`** in `tools/pipeline/vlm_enricher.py` (wherever `list_loaded_models()` and `requests.post()` are called — currently `vlm_enricher.py:52` and `vlm_enricher.py:136`). Catch → treat as `ModelNotLoaded` → skip cycle. Today a single LM Studio restart kills the daemon.
2. **Wrap the top-level `run_daemon()` loop** in `tools/pipeline/orchestrator.py` (around `orchestrator.py:234-258`) with a try/except that logs + continues on any unhandled exception except `KeyboardInterrupt`. Current loop will propagate unknown errors up and exit.
3. **Write `deploy/pipeline/com.farmguardian.pipeline.plist`** mirroring the pattern in `deploy/guardian/com.farmguardian.guardian.plist` (label, ProgramArguments, RunAtLoad, KeepAlive, WorkingDirectory, StandardOutPath=/tmp/pipeline.out.log, StandardErrorPath=/tmp/pipeline.err.log). **Use a fresh label** per the CLAUDE.md TCC rename gotcha — don't reuse anything that has ever been denied.

**Verify Phase 1:** `kill -9` the loaded LM Studio process while the pipeline daemon is running in a terminal; the daemon should log the skip and keep looping, not crash.

### Phase 2 — Fix pipeline config to reflect current hardware (required before meaningful captures)

4. **Flip `s7-cam` block** in `tools/pipeline/config.json`:
   - `enabled: true`
   - `capture_method: "ip_webcam"`
   - `ip_webcam_base: "http://192.168.0.249:8080"` (and replace `/shot.jpg` with `/photoaf.jpg` — see v2.27.7 commit)
   - `cycle_seconds: 10` (Boss's decision, phone is on AC)
   - Keep `burst_size: 1`
   - Delete the stale `rtsp_url`, `rtsp_transport`, `burst_interval_seconds`
   - Remove the stale `enabled_note`
5. **Flip `mba-cam` block** in `tools/pipeline/config.json`:
   - `enabled: true`
   - `capture_method: "ip_webcam"`
   - `ip_webcam_base: "http://mba.local:8089"` (mDNS — MBA is DHCP)
   - `cycle_seconds: 10`
   - Burst size 1, same cleanup
6. **Bump cadences** in the same file:
   - `usb-cam.cycle_seconds: 60 → 2` (host is ready for this; see usb-cam-host v2.27.0)
   - `gwtc.cycle_seconds: 60 → 20` (dshow watchdog handles the rare zombie)
7. **Retention cut**:
   - `retention_days_decent: 90 → 7` (Boss's decision)
   - OR, if you prefer the simpler architecture, stop writing `decent`-tier rows to DB entirely (disk-only archive) — verify `store.py` supports this without breaking `api.py` response shapes first.
8. **Smoke-test with `--once`:**
   ```bash
   cd ~/Documents/GitHub/farm-guardian && source venv/bin/activate
   python -m tools.pipeline.orchestrator --once --log-level INFO
   ```
   Expected: one cycle per enabled camera, `status: ok` with `inference_ms` around 60-100k ms, new rows in `image_archive`. If any camera returns `status: error`, read `result['reason']` and fix before daemon mode.

### Phase 3 — Start the pipeline under supervision + kill the scraper

9. `launchctl load -w ~/Library/LaunchAgents/com.farmguardian.pipeline.plist`
10. Confirm `launchctl list | grep pipeline` shows `PID != 0`.
11. Watch for 10-15 minutes: `tail -f /tmp/pipeline.out.log` + `sqlite3 ... SELECT MAX(ts) FROM image_archive` refreshing.
12. Kill the scraper: `pkill -f farm-backlog/scraper.sh`
13. Decide what to do with `/Users/macmini/farm-backlog/`:
    - **Option A:** delete it. Loses the backlog; frames are redundant with live capture.
    - **Option B:** write a one-off ingester that reads the backlog JPEGs, runs them through the pipeline (VLM + grade + store) with their original capture timestamps, then deletes. Ingester template: see `tools/pipeline/vlm_enricher.py:172-200` `__main__` block — adapt to loop over files.

### Phase 4 — Architecture (do after Phase 3 is stable)

14. **Decouple capture from VLM.** Capture writes JPEGs to an `inbox/` folder; VLM is a separate worker picking newest-per-camera with a drop-oldest policy. Required for sub-30s cadences to work under ~100s/call VLM latency. Details in the deleted earlier plan; happy to re-draft on request.
15. **Model-agnostic selection.** Replace exact-string `vlm_model_id` check with pattern-match fallback. `vlm_enricher.py:52,104`.
16. **Stop the VLM from grading itself.** Move `share_worth` + `share_reason` generation into a `grader.py` that reads the VLM's observational fields. Eliminates the prompt-regurgitation failure mode that produced 45/75 of today's "strong" gems with the literal prompt-definition as reason.
17. **Prompt context leakage.** Strip scene furniture (`heat lamp`, `feeder`, `brooder`) from `prompt.md`'s `{camera_context}`. 44% of captions parrot the word "heat lamp" back today.

## Rollback plan

If Phase 2 or 3 breaks production (e.g. new config crashes Guardian or fills disk):

```bash
# 1. Stop the pipeline
launchctl unload ~/Library/LaunchAgents/com.farmguardian.pipeline.plist
# 2. Revert pipeline config
cd ~/Documents/GitHub/farm-guardian && git checkout HEAD -- tools/pipeline/config.json  # if committed
# or restore from backup before edit
# 3. Restart scraper as safety net
nohup /Users/macmini/farm-backlog/scraper.sh > /Users/macmini/farm-backlog/scraper.log 2>&1 &
echo "PID=$!" > /Users/macmini/farm-backlog/scraper.pid
# 4. If Guardian itself crashed: LaunchAgent will auto-restart it. If it won't come back,
#    check /tmp/guardian.err.log — the TCC rename gotcha (see CLAUDE.md) may apply.
```

**Back up before any schema change:** `cp -a data/guardian.db /tmp/guardian-backup-$(date +%s).db`

## Coordination with other agents

- **The dev who wrote v2.27.x** is on this same Mac Mini, same working tree. His in-flight lives in `git status --short` output. Defer to him until his tree is clean; don't compete for files. Message handoff via `~/bubba-workspace/memory/` or the swarm-coordination repo (`project_swarm_coordination_repo.md` in Bubba memory).
- **OpenClaw Bubba** runs research sweeps against the same LM Studio instance. Loading a VLM for the pipeline must NOT collide with a G0DM0D3 text-model sweep — never call `/api/v1/models/load`; the pipeline is read-only on model state (`list_loaded_models()` only). CLAUDE.md and `docs/13-Apr-2026-lm-studio-reference.md` cover this.
- **farm-2026 frontend** has no active dev today but the contract (see below) must not break silently.

## The scraper — operator's guide

### What it is

Standalone bash loop outside the farm-guardian repo. Four parallel per-camera sub-loops polling `http://localhost:6530/api/cameras/<name>/frame`. sha1-dedups against the previous fetch; saves changed frames to `/Users/macmini/farm-backlog/<camera>/YYYY-MM-DD/<ISO8601-UTC>Z.jpg`.

### Why it's outside the repo

- Other dev's branch is live on the same working tree. Creating files in the repo risks collision.
- This is a stopgap, not a production capture path. Making it a committed tool would make it harder to retire.
- `/Users/macmini/farm-backlog/` is easy to wipe when the real pipeline takes over.

### Files

```
/Users/macmini/farm-backlog/
├── scraper.sh        # the loop (bash; `trap 'kill 0' EXIT INT TERM` for clean child cleanup)
├── scraper.pid       # PID of the top-level bash process (children are in its pgroup)
├── scraper.log       # stdout/stderr of all sub-loops
├── gwtc/<date>/      # 20s cadence, ~230 KB/frame, ~1 GB/day
├── s7-cam/<date>/    # 30s cadence, ~465 KB/frame, ~1.3 GB/day
├── usb-cam/<date>/   # 2s cadence,  ~380 KB/frame, ~16 GB/day raw (less after dedup)
├── house-yard/<date>/ # 5s cadence, ~1.2 MB/frame, ~21 GB/day raw (less after dedup)
└── mba-cam/<date>/   # 10s cadence, ~270 KB/frame, ~2.3 GB/day (added after v2.27.8 re-enable)
```

### Cadence rationale

Scraper cadence must match or be slightly denser than the source-side capture cadence. Polling faster than the source refreshes just bumps the dedup counter. Numbers above reflect that.

S7 is set to 30s here, **not** 10s, because I did not want to unilaterally push the phone harder than what v2.27.7 configured (60s on the Guardian side). The scraper samples every 30s to catch whatever the phone does produce. When the dev drops Guardian's S7 cadence to 10s or 30s, this scraper's cadence can follow.

### Commands

```bash
# status
ps -p "$(cat /Users/macmini/farm-backlog/scraper.pid | awk -F= '{print $2}')" >/dev/null && echo RUNNING || echo DEAD
tail -20 /Users/macmini/farm-backlog/scraper.log

# disk usage so far
du -sh /Users/macmini/farm-backlog/*/

# stop
pkill -f farm-backlog/scraper.sh

# restart (kills first)
pkill -f farm-backlog/scraper.sh; sleep 2
nohup /Users/macmini/farm-backlog/scraper.sh > /Users/macmini/farm-backlog/scraper.log 2>&1 &
echo "PID=$!" > /Users/macmini/farm-backlog/scraper.pid
```

### When to kill it

Kill the scraper once the pipeline orchestrator is back up and writing rows to `image_archive`. A simple check:

```bash
sqlite3 /Users/macmini/Documents/GitHub/farm-guardian/data/guardian.db \
  "SELECT MAX(ts) FROM image_archive;"
```

If `MAX(ts)` is within the last 5 minutes, the pipeline is live and the scraper is redundant. Kill it and wipe `/Users/macmini/farm-backlog/` after confirming the dev's pipeline has backfilled or chosen not to.

### Known limitations

- **No LaunchAgent** — if the Mini reboots or the shell session providing nohup is killed, scraper dies silently. Acceptable because this is a short-lived stopgap.
- **No retention** — just accumulates. At ~20 GB/day with 78 GB free, **3-4 days of runway**. Either kill it before that or add a cron-based cleanup.
- **Dedup is trivial (sha1 of bytes).** Frames that differ by a single JPEG-encoding bit pass through. Good enough for "don't save literally identical repeat pulls" but not real near-duplicate detection.
- **No backoff on Guardian errors** — if Guardian dies, each sub-loop logs FETCH FAIL once per cadence tick and keeps trying. That's fine.

## The goal, in Boss's words

> "I want lots of pictures. I want as many fucking pictures as I can get without overloading any machine. … a lot of them are going to be junk, but there's going to be a couple of really, like, gem ones in there. And that's what Farm 2026 has a front end to show off."

**Volume strategy.** Every camera as fast as its host allows. Cull junk via retention, surface gems via the pipeline, display gems via farm-2026. Earlier in the day I wrote a plan framed as a quality problem — it was wrong framing. This is a throughput problem; quality is an emergent property of sample count.

## What the other dev shipped in this window (read before re-recommending)

| Commit | What |
|---|---|
| v2.27.7 `6ddf850` | S7 `/photoaf.jpg`, continuous-picture AF, incandescent WB, startup-GETs to survive phone reboot. 60s cadence. |
| v2.27.6 `2e52930` | usb-cam stale IP fix. |
| v2.27.5 `4b68fa3` | Guardian LaunchAgent rename `com.farm.guardian` → `com.farmguardian.guardian`. |
| v2.27.4 `1356d8a` | usb-cam-host WB + orange-desat tuning. |
| v2.27.3 `cccc980` | gwtc pipeline cadence 600s → 60s. |
| v2.27.2 `0a9a0a5` | mba-cam decommissioned. |
| v2.27.0 `28f134e` | usb-cam-host continuous-capture: daemon thread keeps camera warm, `/photo.jpg` 75ms. |

Additionally, in-flight on the dev's working tree (not committed when I last checked):

- `tools/s7-battery-monitor/` — ADB-over-WiFi battery + temperature monitor for the S7. `monitor.py` + launchd plist template. Addresses the "battery telemetry" item flagged as open in the v2.27.7 commit message.
- `M config.example.json` — dev's config work.
- `docs/skills-farm-2026-discord-post.md` + `docs/skills-s7-adb-operations.md` + `docs/16-Apr-2026-s7-ipwebcam-frozen-incident.md` — per CLAUDE.md header update. Do not duplicate; these are his.
- v2.27.8 / v2.27.9 — referenced in the Discord-post skill but not yet in local git; presumably in his queue to commit.

**Rule for the next session:** `git fetch && git log origin/main --oneline -15` before proposing any change. Don't re-derive what he's already shipped.

## Per-camera max sustainable cadence

Numbers below are my read of the physics + v2.27.x measurements. The other dev should confirm before changing anything.

### S7 (IP Webcam on a worn S7)

Boss confirmed the S7 is on AC via USB → **battery no longer a blocker for sub-30s cadence**.

| Cadence | Images/day | Notes |
|---|---|---|
| 60s (today, v2.27.7) | 1,440 | |
| 30s | 2,880 | safe on AC, moderate phone thermals |
| **10s** (Boss's ask) | 8,640 | `/photoaf.jpg` has ~1s AF overhead; camera awake ~10% of wall-clock. Viable on AC. Monitor phone thermals via the dev's in-flight ADB skill. |
| 5s | 17,280 | camera awake ~20%; likely thermals itself down after an hour. Not recommended. |

**Data volume at 10s:** 677 KB × 8,640/day ≈ **5.6 GB/day**.

### usb-cam (generic USB webcam on the Mini, served by usb-cam-host)

Per v2.27.0: host camera is continuously warm, `/photo.jpg` responds in 75ms and is the latest frame from a 0.5s-interval grabber thread.

| Cadence | Images/day | Notes |
|---|---|---|
| 60s (pipeline default) | 1,440 | wildly under-sampled |
| **2s** | 43,200 | recommended; matches grabber's 0.5s interval with headroom |
| 0.5s | 172,800 | matches grabber; high disk burn |

**Bottleneck:** disk + retention, not the camera.

**Data volume at 2s:** ~400 KB × 43,200/day ≈ **17 GB/day** raw.

### gwtc (Gateway laptop webcam via MediaMTX RTSP)

v2.27.3 set cadence to 60s. Laptop has known post-reboot dshow-zombie issues auto-recovered by `farmcam-watchdog`.

| Cadence | Images/day | Notes |
|---|---|---|
| 60s (v2.27.3) | 1,440 | |
| **20-30s** | 2,880-4,320 | safe; the RTSP pull is cheap |
| 5-10s | 8,640-17,280 | unknown long-run stability. Would need watchdog attention. |

**Data volume at 20s:** 230 KB × 4,320/day ≈ **1 GB/day**.

### house-yard (Reolink E1 Outdoor PTZ, HTTP snapshot polling)

Already polls at 5s day / 2s night. Reolink firmware is the rate limit. Leave alone. ~35 GB/day uncapped.

### mba-cam (MacBook Air 2013 FaceTime HD, served by usb-cam-host on MBA)

Re-enabled in v2.27.8. Same `usb-cam-host` architecture as usb-cam — the MBA runs its own instance on :8089 serving `/photo.jpg`. Guardian consumes via `HttpUrlSnapshotSource` with `mba.local:8089` (mDNS, per v2.27.8 config rationale — MBA is DHCP and drifts). Per the Discord skill doc, the intended role is "wide overhead of the brooder — overview, not portraits."

| Cadence | Images/day | Notes |
|---|---|---|
| 60s (pipeline default if enabled) | 1,440 | |
| **10s** (scraper current) | 8,640 | safe; same usb-cam-host architecture can sustain this |

**Hardware ceiling:** 2013 FaceTime HD is 1280×720, fixed aperture ~f/2.4, small sensor. Quality ceiling is well below the S7.

**Tier-A improvements available (parallel to usb-cam):**
- Swap `cv2.VideoCapture` in `tools/usb-cam-host/usb_cam_host.py` for AVFoundation's `AVCapturePhotoOutput` on macOS (native photo path, not the video path). Unknown what extra controls the FaceTime HD exposes — needs probing.
- Multi-frame median blending (N=5, ~1s) for noise reduction at heat-lamp light. Changes only usb-cam-host.
- Manual exposure + manual WB via AVFoundation's `AVCaptureDevice.setExposureMode(.locked)` / `.setWhiteBalanceMode(.locked)`. FaceTime HD may or may not honor these.
- Burst-pick-sharpest within host (not Laplacian-graded output, just within-burst selection) — same trick GWTC uses in the pipeline's RTSP burst path.

**Data volume at 10s:** ~270 KB × 8,640/day = **~2.3 GB/day**.

## Where "overloading" actually bites

Ordered by painfulness when volume ramps:

1. **LM Studio / VLM inference (~98s/call on Gemma-4 31B)** — if every frame hits the VLM, the pipeline can't keep up. Queue + drop-oldest is the right architecture. Deferred while pipeline is stalled.
2. **Mac Mini disk (78 GB free now)** — at the proposed cadences across all four cameras (~50 GB/day worst case uncapped), we fill disk in ~2 days without aggressive retention.
3. **S7 thermals** at sub-10s cadence.
4. **GWTC laptop stability** at sub-30s cadence.
5. **Network** — not a real limit on this LAN.

## Boss's decisions (this session)

1. ✅ **S7 is on AC** via USB cable. 10s cadence is greenlit once the dev lands the config change.
2. ✅ **Retention on `decent` tier: 7 days** (down from 90d). Boss also said "don't store those decent ones for a year" — interpreted as "7d is acceptable, prefer aggressive." The dev may choose to drop `decent` row writes entirely instead of storing-then-aging-out; Boss would accept either.
3. ✅ **Prioritize: don't miss good frames** — scraper is running to cover the pipeline-down window.
4. ✅ **USB camera "photographic" ambition is parked** — earlier framing was overcomplicating. Volume-via-existing-hardware is the real ask.

## Open — for the next dev to land

In priority order:

1. **Restart the pipeline orchestrator** (`tools/pipeline/orchestrator.py --daemon`) once the S7 cadence + retention config lands. Also wire it to a LaunchAgent (pattern: `com.farmguardian.pipeline`, mirror `com.farmguardian.guardian`) so it survives reboots. Kill the scraper once the pipeline is writing rows.
2. **Config cadence changes:** S7 10s, usb-cam 2s, gwtc 20-30s, house-yard unchanged.
3. **Retention cut:** `retention_days_decent: 7` in `tools/pipeline/config.json`. Verify `retention.py` sweeps honor it.
4. **Fix orchestrator's crash-on-LM-Studio-restart** — `requests.ConnectionError` should be caught and treated as "skip cycle," not propagated. Wrap `list_loaded_models()` and the chat-completions POST.
5. **Model-agnostic VLM selection** — `vlm_model_id` should accept patterns / "any loaded model matching a vision-family pattern" instead of an exact string. Prevents breakage across model swaps. (A full writeup was in my earlier deleted draft; happy to re-draft when the base problem is stable.)
6. **Pipeline architecture change — decouple capture from VLM.** Capture lands JPEGs on disk at max cadence; VLM is a separate worker that picks newest un-captioned per camera and drops the backlog. Without this, sub-30s cadences on any camera starve the VLM.
7. **stale `s7-cam` AND `mba-cam` blocks in `tools/pipeline/config.json`** still have the old RTSP paths (`rtsp://192.168.0.249:5554/camera` and `rtsp://192.168.0.50:8554/mba-cam`). Both cameras are now HTTP-snapshot (Guardian's `config.example.json` has the correct paths — S7 on 8080 `/photoaf.jpg`, MBA on `mba.local:8089` `/photo.jpg`). The pipeline config drifted behind Guardian's. Harmless while `enabled: false`, but a landmine when re-enabled. Flip both to the new HTTP-snapshot shapes and `enabled: true`.
8. **mba-cam quality improvements (Tier-A)** — same menu as usb-cam: AVFoundation photo API vs cv2 video, median-blend of N frames, manual exposure/WB lock, within-burst sharpest-pick. Changes go in `tools/usb-cam-host/usb_cam_host.py` and apply to BOTH cameras that run the daemon (Mini's usb-cam + MBA's mba-cam). Hardware ceiling on the 2013 FaceTime HD is modest; don't expect S7-class output.
9. **USB `/focus` wastes 1.5s** in `capture_ip_webcam` — `usb-cam-host` doesn't implement `/focus`, returns 404, code still waits. Add `http_trigger_focus` knob (default false for usb-cam + mba-cam, current behavior for others).
10. **Minor:** `store.py:164` archive-path relative computation is fragile (falls through to absolute path if archive_root isn't a direct parent). Guard with explicit check.

Items 4-9 are lift-and-shift from my deleted quality plan; they're still real. Pipeline re-architecture (item 6) is the biggest lever.

## What I am NOT doing in this branch

- No edits to `capture.py`, `guardian.py`, `tools/pipeline/*`, `tools/usb-cam-host/*`, `config*.json`, `retention.py`, `store.py`.
- No LaunchAgent creation.
- No pipeline restart.
- No S7 / usb-cam / gwtc cadence edits.

## Cross-repo scope — farm-2026

Boss's reminder: this work spans both repos. `farm-guardian` produces the images + REST API; **`~/Documents/GitHub/farm-2026`** consumes them and renders gems at `farm.markbarney.net`. The contract between them lives in `farm-2026/app/components/guardian/types.ts`.

Current contract fields the pipeline writes and farm-2026 reads (as of this session):

- `caption_draft: string` (required)
- `caption_is_override?: boolean` (optional, v0.2 backend)
- `image_tier: "strong" | "decent"` (skip tier is filtered server-side before it reaches farm-2026)
- `share_worth: "skip" | "decent" | "strong"`
- `by_tier: Record<"strong" | "decent" | "skip", number>` (stats)

**What the next dev must not break:**

- If the pipeline prompt rewrite lands (split `caption_draft` → `what_is_happening` + `distinctive_observation`), the REST layer in `api.py` **must synthesize `caption_draft` from the new fields** before serving to farm-2026, OR the TS types + components get updated in lockstep in farm-2026. Don't break the public API silently.
- If `share_worth: "decent"` rows stop being written (Boss said "don't store those" as an option), farm-2026's `by_tier` counts for "decent" go to 0. Not a break — just a stat shift. Gems gallery is `image_tier: "strong"` only, so it's unaffected.
- Retention cut to 7d on `decent` tier changes the homepage rail's "recent" behavior if it shows decent-tier images. Check `farm-2026/app/components/guardian/*` and `lib/gems.ts` before landing.

**farm-2026 recent history (as of 2026-04-16):**

```
4989dce feat(cameras): dynamic roster from Guardian backend, not a hardcoded list
17284b8 feat(guardian): smart camera visibility — prioritize online, hide offline
fb34a74 docs(changelog): note v1.7.0 gems gallery pending review
52c3372 v1.7.0: gems gallery + homepage rail consuming farm-guardian layer 1 API
ce946c2 docs: cross-repo plan for exposing the Guardian image archive
```

Clean working tree. No in-flight dev work visible there. If the backend contract changes, farm-2026 needs a coordinated release — open a PR there referencing the farm-guardian commit that changes the contract.

**For the next session:** when picking up the pipeline work, `cd ~/Documents/GitHub/farm-2026 && git fetch && git log origin/main --oneline -10` before touching REST response shapes.

## Files changed in this branch

- `docs/16-Apr-2026-source-quality-plan.md` — this doc. Only file in the commits. No farm-2026 changes this session.

## Pickup checklist for the next session

When a fresh session opens this branch:

1. `git fetch && git log --all --oneline -15` — check what the dev has pushed since 2026-04-16 late afternoon ET. v2.27.8+ probably.
2. `ps -p "$(cat /Users/macmini/farm-backlog/scraper.pid | awk -F= '{print $2}')" >/dev/null && echo SCRAPER_UP || echo SCRAPER_DEAD`
3. `tail -5 /Users/macmini/farm-backlog/scraper.log`
4. `sqlite3 /Users/macmini/Documents/GitHub/farm-guardian/data/guardian.db "SELECT MAX(ts),COUNT(*) FROM image_archive WHERE ts > datetime('now','-1 hour');"` — if count > 0, pipeline is back. Kill scraper. If count = 0 and scraper is running, situation unchanged. If count = 0 and scraper is dead, frames are being lost — restart the scraper and tell Boss.
5. Read this doc's "Open — for the next dev" section and pick the highest-priority unblocked item.
6. Check `git status --short` — if the other dev's in-flight files are still there, he's still live; stay on my branch.

---

**done. scraper is running; handoff doc is in place; branch is safe to land.**
