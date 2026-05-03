# Hardware Inventory — Farm Guardian Cameras

**Last verified end-to-end:** 2026-05-03 ET (Claude Sonnet 4.6 — v2.38.6 — all five cameras live; added USB port sensitivity note for `usb-cam` on GWTC: camera goes `Present: False` in wrong port, port swap is the only fix)

> **Adding/removing a camera?** Use `scripts/add-camera.py` — it writes both `config.json` and `tools/pipeline/config.json` atomically, probes the URL before committing, and refuses duplicates. Hand-edit only for tweaks to an existing entry. Full walkthrough: `docs/19-Apr-2026-add-camera-cli.md`.
**Why this file exists:** The frontend devs found camera-name mismatches (the backend said `gwtc` while the stream URL said `nestbox`; thumbnail labels said "Brooder" for three different cameras pointed at the brooder). This is the single source of truth for the **hardware** side: what each camera is, what machine hosts it, where its frames flow, and the naming rules that prevent the mismatches from reappearing. If something here disagrees with `config.json`, a source file, or a frontend registry, **this file is the ground truth you bring the others in line with** — not the other way around. Re-verify the "Last verified" stamp any time you change a camera.

## The Five Cameras (was four until 2026-04-30 when `mba-cam` was recommissioned as optional brooder monitor; was five until 2026-04-15 09:16 ET when `mba-cam` was decommissioned)

| `name` (config) | Camera hardware | Host machine | Host IP | Source URL (how Guardian pulls) | Capture method | Detection | Currently aimed at |
|---|---|---|---|---|---|---|---|
| `house-yard` | Reolink E1 Outdoor Pro (4K, PTZ, ONVIF, WiFi) | _itself — standalone IP camera_ | `192.168.0.88` | HTTP snapshot: `http://192.168.0.88/cgi-bin/api.cgi?cmd=Snap&...` (via the `reolink-aio` library; native 4K JPEG) | `source: snapshot`, `snapshot_method: reolink` | **on** (predator detection; night window 20:00-09:00 ET runs 2s polls, daytime runs 5s polls) | The yard, sky, and coop approach |
| `s7-cam` | Samsung Galaxy S7 phone (SM-G930F, Android 8.0.0) running IP Webcam by Pavel Khlebovich (`com.pas.webcam` v1.14.37.759 aarch64). **Orientation: PORTRAIT, fixed (v2.35.2, 2026-04-21; watchdog added 2026-04-22).** IP Webcam settings `orientation=portrait` + `photo_rotation=90` emit 1920×1080 sensor-native pixels plus EXIF `Orientation=6`; `capture.py:_apply_exif_rotation` bakes the rotation in before `cv2.imdecode`, so every consumer sees 1080×1920 portrait. Physical phone rotation does NOT drive orientation — it's set via `curl http://192.168.0.249:8080/settings/orientation?set=portrait` and the equivalent `photo_rotation` call. These settings DO reset when the IP Webcam capture process dies (phone reboot / app kill); two re-apply layers handle that: `config.json → http_startup_gets` (on every Guardian restart) and `com.farmguardian.s7-settings-watchdog` LaunchAgent (every 10 min). **Decision:** portrait is the s7-cam's native aspect ratio for IG stories + reels (its primary destination), so portrait is deliberate. Backend helper reads whatever EXIF says, so flipping back to landscape requires only flipping the phone-side settings — the pipeline follows. | _itself_ | `192.168.0.249` | `http://192.168.0.249:8080/photo.jpg` — HTTP snapshot pull, **1080×1920 portrait** JPEG after EXIF-bake (~950 KB/frame) | `http_url` snapshot poll via `HttpUrlSnapshotSource` (v2.24.0, 5 s cadence; v2.35.2 adds EXIF rotation) | off | Nesting box (moved 2026-04-30) |
| `usb-cam` | Generic USB webcam (1920×1080), portable — plug it into whichever host | Any host running the `usb-cam-host` service. **Currently Gateway laptop ("GWTC")** (moved to GWTC 2026-04-24; ran briefly on MBA 2026-04-30 while GWTC was offline, returned to GWTC same day once GWTC came back). `device_index=1` on GWTC Windows because device 0 is the built-in Hy-HD-Camera held by MediaMTX for the `gwtc` RTSP path. **⚠️ USB PORT MATTERS (2026-05-03):** this camera (VID 32E6, PID 9221) is sensitive to which physical USB port it is plugged into on GWTC. If `/health` returns `camera_open: false` and `powershell "Get-PnpDevice -FriendlyName 'USB CAMERA' | Select-Object Present"` shows `Present: False`, the camera is not on the bus — unplug it and plug it into a different USB port. No driver fix, no software restart will recover it; only a port swap works. This is a Windows/driver quirk with this specific camera hardware. | `192.168.0.68:8089` | `http://192.168.0.68:8089/photo.jpg` — HTTP snapshot pull, 1920×1080 JPEG via the `usb-cam-host` **continuous-capture** FastAPI service (v2.27.0) at `tools/usb-cam-host/usb_cam_host.py` running as a Windows scheduled task (autologon `cam` user, `C:\farm-services\usb-cam-host\start.bat`). | `source: snapshot`, `snapshot_method: http_url` via `HttpUrlSnapshotSource` (v2.26.0, 5 s cadence) | off | Coop run (natural daylight) |
| `gwtc` | Built-in webcam on the Gateway laptop ("Hy-HD-Camera", 720p max) | Gateway laptop (Windows 11) | `192.168.0.68` (DHCP — drifts on reboot; find by service signature on `:8554`, see "Finding a drifted host" below) | `rtsp://192.168.0.68:8554/gwtc` (TCP — published by `ffmpeg` via DirectShow → MediaMTX v1.12.2) | `rtsp_url_override`, OpenCV `VideoCapture` | off | Roof of coop (overhead/approach view, 2026-04-30 onward) |
| `iphone-cam` | Boss's iPhone 16 Pro Max via Apple Continuity Camera (USB or wireless to the Mac Mini) — **opportunistic**, only present when the phone is hooked up | Mac Mini "Bubba" (`192.168.0.71`) running a second `usb-cam-host` instance | `127.0.0.1:8091` (loopback, mini-only) | `http://127.0.0.1:8091/photo.jpg` — same `usb-cam-host` binary as the Logitech path, but with `USB_CAM_DEVICE_NAME_CONTAINS=iPhone` so the grabber resolves the AVFoundation video device whose name contains "iPhone" instead of using a raw index. When no iPhone is enumerated by AVFoundation, `_open()` returns `None` and the grabber idles → `/photo.jpg` returns 503 → consumers retry, no spam. **Cannot fall through to "Capture screen 0"** thanks to the substring gate plus a defensive screen-name filter in the resolver. | `source: snapshot`, `snapshot_method: http_url` via `HttpUrlSnapshotSource` (10 s cadence — opportunistic, not surveillance) | off | Whatever Boss is pointing the phone at — typically birds for portraits |
| `mba-cam` | MacBook Air 2013 FaceTime HD (1280×720) | MacBook Air (`192.168.0.50`) | `192.168.0.50` | `http://192.168.0.50:8089/photo.jpg` — HTTP snapshot pull via `usb-cam-host` FastAPI service (`com.farmguardian.usb-cam-host` LaunchAgent). **device_index=0 on this MBA = FaceTime HD** (USB webcam is on GWTC, so FaceTime is the only camera on this box; AVFoundation enumerates it at index 0). DECOMMISSIONED 2026-04-15; **RECOMMISSIONED 2026-04-30** as optional brooder monitor. On/off = load/unload the LaunchAgent on MBA. When LaunchAgent is loaded, mba-cam appears live in Guardian within 30s. | `source: snapshot`, `snapshot_method: http_url` via `HttpUrlSnapshotSource` (5 s cadence) | off | Brooder (optional — load LaunchAgent on MBA to enable) |

**Live frame sizes (2026-04-14 11:02 — as pulled through `/api/cameras/<name>/frame`):** `house-yard` ~1.4 MB (native 4K JPEG); `usb-cam` ~420 KB (1080p, libjpeg quality 95); `gwtc` ~120 KB (720p H.264 re-encoded); `mba-cam` ~115 KB (720p H.264 re-encoded); `s7-cam` ~950 KB (1920×1080 IP Webcam JPEG, served via HTTP snapshot pull now that v2.24.0 is live on the phone).

## What Runs Where

| Machine | LAN IP | OS | Services running for Guardian | Other services (for context) |
|---|---|---|---|---|
| **Mac Mini "Bubba"** | `192.168.0.71` (WiFi/en1, currently — see drift note) | macOS 26.3, 14-core M4 Pro, 64 GB | `guardian.py` (LaunchAgent `com.farmguardian.guardian`, auto-starts on boot); `tools.pipeline.orchestrator` daemon (LaunchAgent `com.farmguardian.pipeline`); **`iphone-cam-host` LaunchAgent on `:8091` (`com.farmguardian.iphone-cam-host`, v2.28.x — serves `/photo.jpg` from Boss's iPhone via Continuity Camera, name-gated on substring "iPhone"; idles cleanly when no iPhone is enumerated)**; `usb-cam-host` LaunchAgent (`com.farmguardian.usb-cam-host`, **unloaded on Mini** — USB cam is now on GWTC; the plist remains on disk on the Mini for reference); `cloudflared` tunnel publishing `:6530` to `guardian.markbarney.net` (outbound, no port forward needed) | LM Studio on `:1234` (GLM-4.6v-Flash + others); dev loop for this repo and `farm-2026` |
| **Gateway laptop ("GWTC")** | `192.168.0.68` (WiFi, DHCP) | Windows 11 Home 10.0.22631 (hostname `653Pudding`) | `mediamtx` Shawl service on `:8554` (declares the `gwtc` path in `C:\mediamtx\mediamtx.yml`); `farmcam` Shawl service (wraps `C:\farm-services\start-camera.bat` → ffmpeg dshow `Hy-HD-Camera` → push to `rtsp://localhost:8554/gwtc`); `farmcam-watchdog` Shawl service (auto-recovery for the post-reboot dshow-zombie pattern — see `docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md`); Windows OpenSSH Server | LM Studio on `:9099` (non-standard — NOT 1234). Windows Firewall is DISABLED per `network.md`. |
| **MacBook Air 2013** | `192.168.0.50` (WiFi, DHCP) | macOS Big Sur 11.7.11 (hardware ceiling — no upgrade possible), Intel Core i5 Haswell 1.3 GHz, 8 GB, Python 3.8.9 from `/Library/Developer/CommandLineTools/` | **RECOMMISSIONED 2026-04-30 as optional brooder monitor.** `com.farmguardian.usb-cam-host` LaunchAgent is **loaded and running** on port 8089 — serving the built-in FaceTime HD at `device_index=0` (USB webcam is on GWTC). Unload the plist (`launchctl unload ~/Library/LaunchAgents/com.farmguardian.usb-cam-host.plist`) when brooder monitoring is no longer needed; mba-cam will disappear from Guardian within one roster refresh. `mba-cam` and `mediamtx` plists remain on disk; only `usb-cam-host` is loaded. Runtime at `~/.local/farm-services/usb-cam-host/` (venv + script). | Screensaver disabled (`idleTime=0`, `askForPassword=0`); `pmset sleep=0 disksleep=0 displaysleep=0 standby=0 powernap=0 hibernatemode=0 autorestart=1`. |
| **Reolink E1 Outdoor Pro** | `192.168.0.88` (WiFi) | Reolink firmware | The camera itself — ONVIF on `:8000`, HTTP API on `:80`, RTSP on `:554`. Uses HTTP snapshot path now (RTSP was abandoned — lossy WiFi mangled HEVC reference packets; see CHANGELOG v2.16.0-v2.18.0). | Camera auto-spotlight and auto-tracking run on the camera itself. Guardian layers YOLO detection + coordinated Discord alerts on top. |
| **Samsung Galaxy S7** | `192.168.0.249` (WiFi) | Android 8.0.0 + IP Webcam (`com.pas.webcam`) | The phone — serves HTTP `/photo.jpg` on `:8080` (pull-on-demand, battery-sparing v2.24.0 path). | **2026-04-14 correction:** prior docs and HARDWARE_INVENTORY said the phone had been running IP Webcam all along, but when Boss turned it on to flip to http_url mode, the actual installed app was **RTSP Camera Server (`com.miv.rtspcamera`)** — an RTSP-only app with **no** `/photo.jpg` endpoint and an auto-record-to-disk feature that had filled `/sdcard/RTSPRecords` with 19 GB of loops. That's the real reason "continuous RTSP drained the battery" — RTSP Camera Server was the wrong app. Recovery: adb over USB through the MBA, delete recordings, install IP Webcam from Aptoide (MD5-verified), launch, `svc power stayon true`, uninstall `com.miv.rtspcamera`, flip `config.json` to `http_url`. Documented in `docs/13-Apr-2026-s7-phone-setup.md` (updated with the correction). |

**Not Guardian hosts but on the LAN** (per `~/bubba-workspace/memory/reference/network.md`): Boss's MSI Katana 15 HX at `192.168.0.3` (primary workstation); Larry's MSI laptop at `192.168.0.194` (OpenClaw node, separate project); Boss's iPhone at `192.168.0.134`; Boss's Apple Watch at `192.168.0.227`. None of these participate in Guardian.

## Where Each Camera's Frame Lands in the Stack

```
┌─────────────────┐   ┌────────────────────────────┐   ┌────────────────────┐   ┌───────────────────┐
│  Camera         │ → │  Host machine              │ → │  Mac Mini          │ → │  Public website   │
│  (hardware)     │   │  (publishes if needed)     │   │  Guardian / API    │   │  farm.markbarney  │
└─────────────────┘   └────────────────────────────┘   └────────────────────┘   └───────────────────┘

house-yard ─── Reolink's own HTTP /cgi-bin Snap ───► ReolinkSnapshotSource ───► /api/cameras/house-yard/frame ──► Cloudflare tunnel ──► frontend
s7-cam        ─ phone's IP Webcam HTTP :8080/photo.jpg (v2.24.0, live 2026-04-14) ► HttpUrlSnapshotSource ► /api/cameras/s7-cam/frame
usb-cam ────── usb-cam-host FastAPI service on :8089 (whichever host the camera is plugged into — Mini today) ─► HttpUrlSnapshotSource ─► /api/cameras/usb-cam/frame
gwtc ───────── ffmpeg dshow → MediaMTX :8554/gwtc (Gateway laptop) ──────────► RTSP OpenCV ──────► /api/cameras/gwtc/frame
(mba-cam) ──── DECOMMISSIONED 2026-04-15 — MBA repurposed; agents unloaded
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
