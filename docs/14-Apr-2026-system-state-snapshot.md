# System State Snapshot — 2026-04-14 14:40 ET

**Author:** Claude Opus 4.6 (1M context)
**Date:** 14-April-2026
**PURPOSE:** Point-in-time record of what's running where, what's committed vs running-but-not-checked-in, which cameras are live, and what's knowingly broken. Written so that the frontend dev syncing `farm-2026` and any future agent picking up this repo can orient in one file rather than piecing it together from logs, `ps`, and `launchctl list`.
**SRP/DRY check:** Pass — single responsibility is "operational reality at this timestamp." Replaces nothing. Supersedes nothing. Complements CLAUDE.md (policy) and HARDWARE_INVENTORY.md (hardware ground truth) by documenting the *live wiring*.

This file decays. If you're reading it more than a few days after the date in the title, read `git log`, compare against `CHANGELOG.md`, and trust the code over this file where they disagree.

---

## 1. Git state

| Ref | Commit | Meaning |
|---|---|---|
| `origin/main` HEAD | `6f69306` | `v2.25.0: /api/v1/images/* REST surface for the image archive` |
| prior | `943e571` | `docs: modularization plan — 14-Apr-2026` |
| prior | `7348e35` | `v2.26.0: host-portable usb-cam via tools/usb-cam-host/ snapshot service` |
| prior | `c088953` | `v2.24.2: s7-cam cutover executed end-to-end — live on http_url` |

Local worktree is **clean** after the committer caught up on `v2.25.0`. Nothing unstaged, nothing untracked at the time of this snapshot.

Both `config.json` and `tools/pipeline/config.json` are `.gitignore`'d (they contain the Discord webhook and the Reolink password). `config.example.json` carries the canonical public shape and was updated in `v2.26.0` so a fresh clone reflects the `http_url` `usb-cam` pattern.

---

## 2. What's running on the Mac Mini right now

### 2a. Long-lived processes

| PID (at snapshot) | Uptime | Command | What it is |
|---|---|---|---|
| `31115` | ~60 min | `cloudflared tunnel ... --protocol quic run ...` | The outbound Cloudflare tunnel publishing `:6530` to `guardian.markbarney.net`. QUIC, not HTTP/2 — `http2` drops 70–90% of requests under load, see auto-memory `project_cloudflare_tunnel_quic.md`. Managed by `com.cloudflare.tunnel.farm-guardian` LaunchAgent. |
| `42575` | ~48 min | `./venv/bin/python guardian.py` | Farm Guardian main process. Serves the FastAPI dashboard + API on `:6530`, pulls frames from all five cameras, runs YOLOv8 on `house-yard`. **Running via `nohup`, NOT under launchd** — see §4 (known-broken) below. |
| `50321` | ~50 min | `python -m tools.pipeline.orchestrator --daemon` | The image archive pipeline daemon. Every N minutes per camera, captures a frame via the per-camera `capture_method`, runs the trivial gate, sends to `glm-4.6v-flash` via LM Studio, writes `image_archive` row + tiered JPEG to `data/archive/YYYY-MM/<camera>/`. |
| `86134` | ~56 min | `python tools/usb-cam-host/usb_cam_host.py` | `usb-cam-host` FastAPI service, `:8089`. Serves `/photo.jpg` + `/health`. Managed by `com.farmguardian.usb-cam-host` LaunchAgent (new in v2.26.0). This is what both Guardian and the pipeline pull `usb-cam` frames through now. |

### 2b. Ports

| Port | Holder | Purpose |
|---|---|---|
| `:6530` | `guardian.py` (42575) | FastAPI dashboard + all REST APIs — `/api/cameras/*`, `/api/v1/*`, `/api/v1/images/*` (v2.25.0). Exposed publicly via the Cloudflare tunnel. |
| `:8089` | `usb_cam_host.py` (86134) | `usb-cam-host` service. LAN-only. |
| `:1234` | LM Studio | VLM inference for the pipeline. **Guardian itself does not call LM Studio** since v2.17.0; only the pipeline does, via `tools/pipeline/vlm_enricher.py`. Safe-load pattern and the 2026-04-13 watchdog incident are documented in `docs/13-Apr-2026-lm-studio-reference.md`. |

### 2c. LaunchAgents

| Label | State | Notes |
|---|---|---|
| `com.cloudflare.tunnel.farm-guardian` | Running (PID 31115) | Healthy. |
| `com.farmguardian.usb-cam-host` | Running (PID 86134) | New in v2.26.0. Serves the USB camera over HTTP so the camera can move to any host. |
| `com.farm.guardian` | **Unloaded (plist still on disk at `~/Library/LaunchAgents/com.farm.guardian.plist`)** | See §4 — this agent cannot be bootstrapped right now; Guardian runs via `nohup` until it's fixed. |

No other Guardian-related agents. The pipeline orchestrator is a long-lived `nohup` daemon, not a LaunchAgent (parent PID 1, inherited from a pre-outage launch).

---

## 3. Camera inventory — live state (verified `curl http://localhost:6530/api/cameras/<name>/frame`)

| `name` | Host machine | Feed path (Guardian consumes via) | Last verified frame | Bytes | Notes |
|---|---|---|---|---|---|
| `house-yard` | Reolink E1 Outdoor Pro at `192.168.0.88` (self-hosted IP camera) | `ReolinkSnapshotSource` → Reolink `cmd=Snap` HTTP endpoint → native 4K JPEG | **HTTP 200** | 1,332,043 B (≈ 1.3 MB, 4K JPEG) | Healthy. Detection on. Night window runs 2 s polls, daytime 5 s. |
| `usb-cam` | Mac Mini (USB, will move to MBA) | `HttpUrlSnapshotSource` → `usb-cam-host` service at `http://192.168.0.71:8089/photo.jpg` → OpenCV-encoded JPEG (q95) | **HTTP 200** | 375,313 B (≈ 375 KB, 1080p) | **Switched to portable service in v2.26.0.** Laplacian variance consistently in the 14–50 range — "soft" per GLM's own verdict. That's physical (chick on the lens + blown heat-lamp red channel), not recoverable in software. See v2.26.0 plan §Scope-out. |
| `s7-cam` | Samsung Galaxy S7 at `192.168.0.249` (phone self-hosts) | `HttpUrlSnapshotSource` → IP Webcam `/photo.jpg` on port 8080 | **HTTP 404** (phone offline) | 44 B (error body) | Phone is currently down. Not a regression — the log at `guardian.log` shows `Host is down` since the session started. Will recover the moment Boss powers the phone back on. |
| `gwtc` | Gateway laptop at `192.168.0.68` (Windows 11) | RTSP `rtsp://192.168.0.68:8554/gwtc` via `CameraCapture` (OpenCV VideoCapture) | **HTTP 200** | 116,620 B (≈ 117 KB, 720p H.264 → JPEG) | Healthy. ffmpeg → MediaMTX on the Windows side, `farmcam-watchdog` covers the post-reboot dshow zombie pattern. |
| `mba-cam` | MacBook Air 2013 at `192.168.0.50` | RTSP `rtsp://192.168.0.50:8554/mba-cam` via `CameraCapture` | **HTTP 200** | 264,919 B (≈ 265 KB, 720p H.264 → JPEG) | Healthy. ffmpeg → MediaMTX on the MBA. Big Sur ceiling — MediaMTX pinned to v1.13.1. |

**Pipeline archive is live** — most recent `usb-cam` entries: `2026-04-14T18-31-25-strong.jpg` (14:31 ET) was classified as `share_worth: "strong"` (a bird visible with a chick on the lens — the rare one that punches through), and `decent`-tier rows every 3–4 minutes since the v2.26.0 flip. Proves the HTTP service path is production-good.

---

## 4. What's knowingly broken

### 4a. `com.farm.guardian` LaunchAgent — `posix_spawn ... Operation not permitted`

**When it broke:** some time during a mid-session house power outage on 2026-04-14. Guardian had been running happily under this agent earlier in the day (v2.24.2 live).

**The symptom:** `launchctl kickstart gui/501/com.farm.guardian` returns no error but the process never starts. `/usr/bin/log show --predicate 'process == "launchd"' --last 5m | grep com.farm.guardian` shows repeated:

```
launchd: [gui/501/com.farm.guardian [<pid>]:] Service could not initialize:
  posix_spawn(/Users/macmini/Documents/GitHub/farm-guardian/venv/bin/python),
  error 0x1 - Operation not permitted
```

**What it isn't:**
- Not a binary / entitlement issue on the Python itself. The brand-new `com.farmguardian.usb-cam-host` LaunchAgent installed in this same session uses the **exact same** `venv/bin/python` binary and starts cleanly. So the executable is fine and TCC/codesign for Python is intact.
- Not a config error in the plist. Bootout + bootstrap (which re-parses the plist) doesn't change the error.
- Not a port collision — `:6530` is free when the agent attempts to spawn.
- Not a disk-permission issue — same working directory, same stdout/stderr paths Guardian has written to all week.

**Current theory:** macOS's newer security posture (Sequoia-era App Management protection) appears to have flagged the specific `com.farm.guardian` *label* as unauthorized after the power-cycle interrupted it mid-exec. The grant is per-label for this class of denial, which is why the new `com.farmguardian.usb-cam-host` agent was never tainted.

**Workaround (currently in effect):** Guardian runs via `nohup ./venv/bin/python guardian.py >> guardian.log 2>&1 &`. Behaves identically from the consumer side.

**Fix path (in order of safety):**
1. Reboot the Mac Mini. The power-cycle that broke it is plausibly what a clean reboot will clear, and a reboot has minimal collateral (Boss does them regularly).
2. If the reboot doesn't restore launchd-managed startup, open **System Settings → Privacy & Security → App Management** and approve whatever process is flagged there. (Boss has to click — can't do this from a headless shell.)
3. If both of the above fail, rename the agent label (e.g. `com.farmguardian.main` to mirror the naming of the usb-cam-host agent) and reinstall. Label-renaming sidesteps the cached denial.

**Do not** attempt to work around this by making Guardian run as a LaunchDaemon instead of a LaunchAgent. Guardian opens the USB camera in the old path and will need Camera TCC again someday; LaunchDaemons cannot surface TCC prompts.

### 4b. `s7-cam` offline

Phone is powered off or the IP Webcam app isn't running. Guardian's polling keeps trying (5 s cadence); when the phone comes back the source recovers automatically. No code action needed.

---

## 5. `farm-2026` consumer surface (for the frontend dev)

You're building layer 2 against the tunnel at `https://guardian.markbarney.net`. The endpoints you care about for Gems / Recent / Retrospective:

- `GET /api/v1/images/ping` — liveness + row count. Good first call from the build script to confirm the tunnel is healthy and the image archive is populated.
- `GET /api/v1/images/gems` — `share_worth='strong'` rows, newest first. Filters: `camera`, `scene`, `activity`, `individual`, `since`, `until`, `order`. Cursor pagination; `limit` ≤ 100 (default 24).
- `GET /api/v1/images/gems/{id}` — single gem + up to 4 related gem IDs from the same camera within ±2h.
- `GET /api/v1/images/gems/{id}/image?size=thumb|1920|full` — JPEG bytes. ETag-backed; your client should honour `If-None-Match` for cheap cache hits.
- `GET /api/v1/images/recent` — same shape as `/gems`, tier in `{strong, decent}`.
- `GET /api/v1/images/stats` — aggregate counts for hero / badges.

**All public endpoints always exclude `has_concerns=1` rows at the SQL level.** You will never see Boss's private-notes entries through the public surface. The review endpoints that *can* see them require `Authorization: Bearer $GUARDIAN_REVIEW_TOKEN`, and 503 if the env var is unset on the Mini (which is the current state — intentional; nobody has turned review on yet).

**Camera-name contract (also non-negotiable for your frontend, see `HARDWARE_INVENTORY.md` §Naming Rules):** `house-yard`, `usb-cam`, `s7-cam`, `gwtc`, `mba-cam`. Labels in `farm-2026/lib/cameras.ts` are hardware-only (`"USB"`, `"MBA"`, `"S7"`, `"GWTC"`, `"Reolink"`). No `location` field. If you find a `"brooder"` label or a `"nestbox"` path anywhere, it's a bug — fix it.

---

## 6. What's deliberately pending (not broken, just not-yet-done)

- **Move `usb-cam` physically to the MacBook Air.** Boss plans to do this soon. Procedure is in `docs/14-Apr-2026-portable-usb-cam-host-plan.md` §6 and `deploy/usb-cam-host/install-macos.md`. Nothing blocks the move except Boss's time.
- **Brooder VLM narrator** — standalone tool, awaits Boss approval. Plan: `docs/13-Apr-2026-brooder-vlm-narrator-plan.md`. Will be revised to include a "pick the sharpest of N frames" call rather than blind 5-min sampling, which fits well with v2.26.0's single-frame service contract.
- **Phase C2 — motion-event-triggered snapshot bursts on `house-yard`.** Independent of v2.26.0. Plan: `docs/13-Apr-2026-phase-c-usb-highres-and-motion-bursts-plan.md` (C1 is superseded; read the STATUS header at the top).
- **Audio-triggered capture on `usb-cam`.** Plan in `docs/14-Apr-2026-audio-triggered-capture-plan.md`.
- **Modularization cleanup.** Plan in `docs/14-Apr-2026-modularization-plan.md`.
- **Restore the `com.farm.guardian` LaunchAgent.** See §4a.

---

## 7. Sanity-check recipes (for any agent landing here cold)

From the Mac Mini:

```bash
# Git head matches §1 above
cd ~/Documents/GitHub/farm-guardian && git log --oneline -4

# All four currently-online cameras return 200s
for c in house-yard usb-cam gwtc mba-cam; do
  curl -sS --max-time 3 -o /dev/null -w "$c: http=%{http_code} time=%{time_total}s\n" \
    http://localhost:6530/api/cameras/$c/frame
done

# usb-cam-host service is serving photos
curl -sS --max-time 15 http://localhost:8089/health | python3 -m json.tool
curl -sS --max-time 20 http://localhost:8089/photo.jpg -o /tmp/u.jpg && file /tmp/u.jpg

# Pipeline is archiving
ls -lt data/archive/2026-04/usb-cam/ | head -5

# Tunnel is healthy (public)
curl -sS https://guardian.markbarney.net/api/v1/images/ping | python3 -m json.tool
```

If any of these come back wrong, start at §4 (known-broken) and walk backwards through §2.
