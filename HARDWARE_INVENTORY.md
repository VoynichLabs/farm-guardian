# Hardware Inventory — Farm Guardian Cameras

**Last verified end-to-end:** 2026-04-13 19:10 ET (Claude Opus 4.6 — v2.24.1, post `nestbox` rename)
**Why this file exists:** The frontend devs found camera-name mismatches (the backend said `gwtc` while the stream URL said `nestbox`; thumbnail labels said "Brooder" for three different cameras pointed at the brooder). This is the single source of truth for the **hardware** side: what each camera is, what machine hosts it, where its frames flow, and the naming rules that prevent the mismatches from reappearing. If something here disagrees with `config.json`, a source file, or a frontend registry, **this file is the ground truth you bring the others in line with** — not the other way around. Re-verify the "Last verified" stamp any time you change a camera.

## The Five Cameras

| `name` (config) | Camera hardware | Host machine | Host IP | Source URL (how Guardian pulls) | Capture method | Detection | Currently aimed at |
|---|---|---|---|---|---|---|---|
| `house-yard` | Reolink E1 Outdoor Pro (4K, PTZ, ONVIF, WiFi) | _itself — standalone IP camera_ | `192.168.0.88` | HTTP snapshot: `http://192.168.0.88/cgi-bin/api.cgi?cmd=Snap&...` (via the `reolink-aio` library; native 4K JPEG) | `source: snapshot`, `snapshot_method: reolink` | **on** (predator detection; night window 20:00-09:00 ET runs 2s polls, daytime runs 5s polls) | The yard, sky, and coop approach |
| `s7-cam` | Samsung Galaxy S7 phone (SM-G930F, Android 8.0.0) running the IP Webcam Android app | _itself_ | `192.168.0.249` | `rtsp://192.168.0.249:5554/camera` (UDP transport — the `/camera` path is fixed by the IP Webcam app, not configurable) | `rtsp_url_override`, OpenCV `VideoCapture` — plus the v2.24.0 HTTP-snapshot-source alternative at `http://192.168.0.249:8080/photo.jpg` for battery-saving (see CHANGELOG v2.24.0 and `docs/13-Apr-2026-s7-phone-setup.md`) | off | Coop area (was offline as of 13-Apr-2026 PM — IP Webcam app not running; re-seat per phone setup doc) |
| `usb-cam` | Generic USB webcam (1920×1080) | Mac Mini "Bubba" (locally attached USB) | `192.168.0.71` (WiFi, currently — see "Mac Mini network drift" note below) | AVFoundation device index `0`, no network | `source: snapshot`, `snapshot_method: usb`, 5s interval, 3-frame warmup, auto-WB strength 0.8, autofocus on | off | Brooder interior (heat-lamp lit) |
| `gwtc` | Built-in webcam on the Gateway laptop ("Hy-HD-Camera", 720p max) | Gateway laptop (Windows 11) | `192.168.0.68` (DHCP — drifts on reboot; find by service signature on `:8554`, see "Finding a drifted host" below) | `rtsp://192.168.0.68:8554/gwtc` (TCP — published by `ffmpeg` via DirectShow → MediaMTX v1.12.2) | `rtsp_url_override`, OpenCV `VideoCapture` | off | Coop interior |
| `mba-cam` | Built-in FaceTime HD webcam (Apple VendorID `0x106B`, ProductID `0x1570`, 720p max — supports 30fps only, 15fps is not a valid capture rate) on the MacBook Air 2013 (MacBookAir6,2) | MacBook Air 2013 (macOS Big Sur 11.7.11) | `192.168.0.50` (DHCP) | `rtsp://192.168.0.50:8554/mba-cam` (TCP — published by `ffmpeg` via AVFoundation index 0 → MediaMTX v1.13.1) | `rtsp_url_override`, OpenCV `VideoCapture` | off | Brooder |

**Live frame sizes (2026-04-13 19:10 — as pulled through `/api/cameras/<name>/frame`):** `house-yard` ~1.4 MB (native 4K JPEG); `usb-cam` ~420 KB (1080p, libjpeg quality 95); `gwtc` ~120 KB (720p H.264 re-encoded); `mba-cam` ~115 KB (720p H.264 re-encoded); `s7-cam` currently 404 (phone offline, pre-existing).

## What Runs Where

| Machine | LAN IP | OS | Services running for Guardian | Other services (for context) |
|---|---|---|---|---|
| **Mac Mini "Bubba"** | `192.168.0.71` (WiFi/en1, currently — see drift note) | macOS 26.3, 14-core M4 Pro, 64 GB | `guardian.py` (manual `nohup`, PID varies — all 5 camera consumers + YOLOv8 + the FastAPI dashboard on `:6530`); `tools.pipeline.orchestrator` daemon (manual `nohup`, VLM enrichment of archived frames); `cloudflared` tunnel publishing `:6530` to `guardian.markbarney.net` (outbound, no port forward needed) | LM Studio on `:1234` (GLM-4.6v-Flash + others); dev loop for this repo and `farm-2026` |
| **Gateway laptop ("GWTC")** | `192.168.0.68` (WiFi, DHCP) | Windows 11 Home 10.0.22631 (hostname `653Pudding`) | `mediamtx` Shawl service on `:8554` (declares the `gwtc` path in `C:\mediamtx\mediamtx.yml`); `farmcam` Shawl service (wraps `C:\farm-services\start-camera.bat` → ffmpeg dshow `Hy-HD-Camera` → push to `rtsp://localhost:8554/gwtc`); `farmcam-watchdog` Shawl service (auto-recovery for the post-reboot dshow-zombie pattern — see `docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md`); Windows OpenSSH Server | LM Studio on `:9099` (non-standard — NOT 1234). Windows Firewall is DISABLED per `network.md`. |
| **MacBook Air 2013** | `192.168.0.50` (WiFi, DHCP) | macOS Big Sur 11.7.11 (hardware ceiling — no upgrade possible), Intel Core i5 Haswell 1.3 GHz, 8 GB | `com.farmguardian.mediamtx` LaunchAgent on `:8554` (MediaMTX v1.13.1 — v1.16.3+ link against macOS 12 SDK and dyld-fail on Big Sur); `com.farmguardian.mba-cam` LaunchAgent (ffmpeg 8.1-tessus static, AVFoundation `0` at 720p30 → re-rate `-r 15` → libx264 ultrafast → push to `rtsp://127.0.0.1:8554/mba-cam`) | Screensaver disabled (`idleTime=0`, `askForPassword=0`); `pmset sleep=0 disksleep=0 displaysleep=0 standby=0 powernap=0 hibernatemode=0 autorestart=1`. Lid must stay open (clamshell firmware sleep cannot be overridden on this 2013 hardware). |
| **Reolink E1 Outdoor Pro** | `192.168.0.88` (WiFi) | Reolink firmware | The camera itself — ONVIF on `:8000`, HTTP API on `:80`, RTSP on `:554`. Uses HTTP snapshot path now (RTSP was abandoned — lossy WiFi mangled HEVC reference packets; see CHANGELOG v2.16.0-v2.18.0). | Camera auto-spotlight and auto-tracking run on the camera itself. Guardian layers YOLO detection + coordinated Discord alerts on top. |
| **Samsung Galaxy S7** | `192.168.0.249` (WiFi) | Android 8.0.0 + IP Webcam app | The phone itself — RTSP on `:5554` (continuous H.264 stream; battery-intensive) or HTTP `/photo.jpg` on `:8080` (pull-on-demand, battery-sparing, v2.24.0 path). | Was offline 13-Apr-2026 PM because continuous RTSP drained the worn battery faster than USB could charge it. Phone-setup doc covers the recovery. |

**Not Guardian hosts but on the LAN** (per `~/bubba-workspace/memory/reference/network.md`): Boss's MSI Katana 15 HX at `192.168.0.3` (primary workstation); Larry's MSI laptop at `192.168.0.194` (OpenClaw node, separate project); Boss's iPhone at `192.168.0.134`; Boss's Apple Watch at `192.168.0.227`. None of these participate in Guardian.

## Where Each Camera's Frame Lands in the Stack

```
┌─────────────────┐   ┌────────────────────────────┐   ┌────────────────────┐   ┌───────────────────┐
│  Camera         │ → │  Host machine              │ → │  Mac Mini          │ → │  Public website   │
│  (hardware)     │   │  (publishes if needed)     │   │  Guardian / API    │   │  farm.markbarney  │
└─────────────────┘   └────────────────────────────┘   └────────────────────┘   └───────────────────┘

house-yard ─── Reolink's own HTTP /cgi-bin Snap ───► ReolinkSnapshotSource ───► /api/cameras/house-yard/frame ──► Cloudflare tunnel ──► frontend
s7-cam (RTSP) ─ phone's IP Webcam RTSP :5554/camera► RTSP OpenCV ────────────► /api/cameras/s7-cam/frame
s7-cam (HTTP) ─ phone's IP Webcam HTTP :8080/photo.jpg (v2.24.0 battery path) ► HttpUrlSnapshotSource ► /api/cameras/s7-cam/frame
usb-cam ────── AVFoundation device 0 (no network) ─► UsbSnapshotSource ──────► /api/cameras/usb-cam/frame
gwtc ───────── ffmpeg dshow → MediaMTX :8554/gwtc (Gateway laptop) ──────────► RTSP OpenCV ──────► /api/cameras/gwtc/frame
mba-cam ────── ffmpeg avfoundation → MediaMTX :8554/mba-cam (MacBook Air) ────► RTSP OpenCV ──────► /api/cameras/mba-cam/frame
```

`guardian.markbarney.net` is a Cloudflare Tunnel from the Mac Mini — outbound-only, no port forwarding, no inbound firewall rule. The tunnel exposes `:6530` (FastAPI dashboard + REST API) to the public internet; the frontend at `farm.markbarney.net` embeds JPEGs from `<tunnel>/api/cameras/<name>/frame` every ~1.2 s.

## Naming Rules (NON-NEGOTIABLE — mirrored in Bubba auto-memory `feedback_camera_naming.md`)

1. **Camera names are device-only.** `mba-cam`, `s7-cam`, `usb-cam`, `gwtc`. The grandfathered exception is `house-yard` (predates the rule). **Never** `brooder-cam`, `nestbox`, `coop-cam`, `incubator-cam`, or any other "where it is today" string.
2. **The rule applies to every layer.** The `name` field in `config.json`, RTSP paths, MediaMTX `paths:` declarations, ffmpeg push URLs, LaunchAgent labels, Shawl service names, log filenames, dashboard labels, the frontend `lib/cameras.ts` entries (`label`, `shortLabel`, `device`), MDX roster tables, thumbnail captions, stage overlays — **every string a user or future agent can see**. The 13-Apr-2026 incident that hit hardest: `lib/cameras.ts` had `shortLabel: "Brooder"` for `usb-cam`, `"S7 brooder"` for `s7-cam`, `"MBA brooder"` for `mba-cam`, `"Nestbox"` for `gwtc` — three thumbnails all said "brooder," frontend devs found them indistinguishable. Fix was to drop any `location` field entirely and label by hardware (`"USB"`, `"MBA"`, `"S7"`, `"GWTC"`, `"Reolink"`).
3. **The rule applies to publish paths too.** As of 2026-04-13 evening, the Gateway laptop's MediaMTX path was renamed `nestbox` → `gwtc` to match the device name (CHANGELOG v2.24.1). The MacBook Air's path was always `mba-cam`. If anyone adds a new ffmpeg → MediaMTX node, the path **must** equal the camera's `name` in `config.json`.
4. **"Where it's pointed today" is field-note material, not config material.** Put it in a `content/field-notes/*.mdx` entry, a CHANGELOG line, or a photo caption if needed. Don't put it in any struct that drives UI, routing, services, or file names. The rightmost column of this file ("Currently aimed at") is allowed precisely because it's in a doc that's read by humans once, not parsed by machines repeatedly.

## Adding a New Camera (checklist)

1. **Pick the device-name first.** Must not be a location. Short, lowercase, hyphenated. If you can't think of a device name, you haven't thought about it hard enough (`raspicam-1`, `eufy-coop`, `arlo-gate`, etc.).
2. **`config.json` + `config.example.json`** — add the entry. Required: `name`, `ip`, `port`, `username`/`password` if any, `type` (`ptz` or `fixed`), capture config (`source` + `snapshot_method` + method-specific keys OR `rtsp_url_override` + `rtsp_transport`), `detection_enabled` (default `false` until role is decided). `rtsp_transport: tcp` for any WiFi-published camera; only use `udp` if you have hard evidence UDP is stable on that specific camera.
3. **If the camera is published via ffmpeg → MediaMTX on a host machine** — the MediaMTX `paths:` block in the host's `mediamtx.yml` **must** declare the path, the ffmpeg push URL **must** push to that path, and **the path must equal the camera's `name`**. Save canonical copies of the host's config in `deploy/<host>/` so they're version controlled.
4. **Update this file.** Add the row to "The Five Cameras" (update the count in the section header if needed — currently "Five" — or just renumber mentally). Add the host to "What Runs Where" if it's a new machine. Update "Where Each Camera's Frame Lands in the Stack." Update the "Last verified" stamp at the top.
5. **Update `farm-2026/lib/cameras.ts`** — new entry with `name`, `label`, `shortLabel`, `device`, `aspectRatio`. **No `location` field.** Labels and short labels are hardware-only. Update `farm-2026/content/projects/guardian/index.mdx` cameras table.
6. **Restart Guardian** on the Mini (`kill <pid>; nohup ./venv/bin/python guardian.py >> guardian.log 2>&1 & disown`). Verify: `curl -s http://localhost:6530/api/cameras` should list the new camera with `online: true, capturing: true`. Then `curl -s -o /tmp/t.jpg -w "%{http_code} %{size_download}\n" http://localhost:6530/api/cameras/<name>/frame` should return `200` and a JPEG ≥5 KB within ~2 capture intervals.
7. **Restart the pipeline daemon too** (`tools.pipeline.orchestrator`) — it reads its own `tools/pipeline/config.json` at startup. If the new camera should be enriched by the VLM, add it there too.

## Moving an Existing Camera

**Don't rename anything.** The camera's `name`, RTSP path, MediaMTX path, LaunchAgent labels, Shawl service names, log filenames, and config entry all stay exactly as they were. The only things that change are:

1. The rightmost "Currently aimed at" column in this file.
2. A field-note MDX in `farm-2026/content/field-notes/` describing the new placement and why.
3. Optionally the `context` string in `tools/pipeline/config.json` (VLM prompt context — should still lead with the hardware, e.g., "MacBook Air 2013 (Big Sur, 192.168.0.50) built-in FaceTime HD webcam; currently aimed at...").

If you find yourself wanting to rename the camera because it moved: re-read rule #1. You're about to reintroduce the exact problem this file exists to prevent.

## Mac Mini Network Drift (note flagged 2026-04-13)

`~/bubba-workspace/memory/reference/network.md` states the intended Mac Mini config is **en0 Ethernet at `192.168.0.105` with WiFi OFF**. Actual runtime (verified 2026-04-13 19:08): **en1 WiFi at `192.168.0.71`, en0 Ethernet disconnected**. Everything still works — Guardian binds `0.0.0.0:6530` so it's reachable on whatever interface has a route, and the Cloudflare tunnel is outbound-only so it's transport-agnostic — but:

- ICMP-asymmetry rules in `CLAUDE.md` assume Mini-on-Ethernet ↔ laptop-on-WiFi. With both sides on WiFi, `ping` may actually work between the Mini and GWTC/Air, which **inverts the usual "TCP-only probes" guidance** for that specific pairing. Don't build diagnostic habits around the current state; the Ethernet cable might be plugged back in at any time.
- The pipeline daemon's reads of `/gwtc` show up in the Gateway laptop's mediamtx log as coming from `192.168.0.71`. That's the Mini on WiFi, not an unknown consumer.
- If the front-end dashboard (`farm-2026`) displays the Mini's IP anywhere in its system panel, it's pulling it from the Guardian API — which will report the current IP correctly.

**Not fixing this in code.** It's a physical-layer state that Boss controls. Flag for Boss's attention next time the Mini is within arm's reach.

## Finding a Drifted Host

`gwtc` and `mba-cam`'s hosts are both on DHCP. IPs drift after router reboots or long WiFi disassociations. Don't trust the IP in this file as a live value; trust the **service signature**:

```bash
# GWTC: distinctive services on :8554 (MediaMTX) or :9099 (LM Studio, non-standard)
for i in $(seq 2 254); do (nc -z -w 1 192.168.0.$i 8554 2>/dev/null && echo "192.168.0.$i has :8554") & done; wait

# MacBook Air: also publishes MediaMTX on :8554 (so both the Air and GWTC will show up;
# disambiguate by SSH user — Air is `markb@<ip>` with key auth, or by checking the RTSP
# path it serves: gwtc vs mba-cam).

# Mac Mini: reachable on the LAN, usually known; for belt-and-suspenders, sweep :6530
# (Guardian dashboard) or check the Cloudflare tunnel (publicly reachable).
for i in $(seq 2 254); do (nc -z -w 1 192.168.0.$i 6530 2>/dev/null && echo "192.168.0.$i has :6530 (Guardian)") & done; wait
```

Full writeup of why the MAC tables in the network doc are currently wrong and how this lookup recipe survives that: `docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md`.

## Cross-references

- **`CLAUDE.md`** — `Hardware Inventory` top-of-file pointer to this doc; `Network & Machine Access` section for router quirks (ICMP, DHCP drift, WSL2 routing bug) and host SSH recipes; `Multi-Machine Claude Orchestration` for spawning agents on target boxes over SSH.
- **`docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md`** — both GWTC failure modes (reachability and dshow zombie) with their diagnostic recipes and the auto-recovery watchdog.
- **`deploy/macbook-air/`** — canonical copies of the Air's `com.farmguardian.mediamtx.plist` and `com.farmguardian.mba-cam.plist` LaunchAgents.
- **`deploy/gwtc/`** — canonical copies of the Gateway laptop's `start-camera.bat`, `mediamtx.yml`, `farm-watchdog.ps1`, and `install-watchdog.md`.
- **`tools/pipeline/config.json`** — the multi-camera VLM enrichment pipeline's per-camera config. Must stay in sync with `config.json` here; in particular the `rtsp_url` entries for `gwtc` and `mba-cam` track the MediaMTX paths above.
- **`~/bubba-workspace/skills/macbook-air/SKILL.md`** — Air-specific operations (SSH, TCC, screensaver, power, Node.js/Claude Code install recipes).
- **`~/bubba-workspace/memory/reference/network.md`** — master device table including the non-Guardian machines on the LAN (with the known MAC-attribution error and the network-drift-since-doc-was-written status).
- **`farm-2026/lib/cameras.ts`** — frontend's camera registry. Must stay in sync with the "The Five Cameras" table above. Follows the same device-not-location naming rule.
- **`farm-2026/content/projects/guardian/index.mdx`** — public-facing project page, camera roster table.
- **`~/.claude` auto-memory `feedback_camera_naming.md`** — the device-not-location rule with rationale, the Apr-13 incident, and the addendum that every UI string must be hardware-only.
