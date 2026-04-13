# Hardware Inventory — Farm Guardian Cameras

**Last verified end-to-end:** 2026-04-13 (v2.24.1 — post `nestbox` rename)
**Why this file exists:** Frontend devs found mismatches and confusion between camera names, the devices they run on, and where they're pointed. This is the single source of truth for the **hardware** side. Names are device-based per the project's longstanding rule (cameras get named after the hardware, never after the location they're aimed at — locations change, devices don't). Where a camera is pointed today is noted in the rightmost column **for context only** — never put that string in a UI label, a config name, or an RTSP path.

## The Five Cameras

| `name` (config) | Camera hardware | Host machine | Host IP | RTSP / source URL | Capture method | Detection | Currently aimed at |
|---|---|---|---|---|---|---|---|
| `house-yard` | Reolink E1 Outdoor Pro (4K PTZ, ONVIF, WiFi) | _itself — standalone IP camera_ | `192.168.0.88` | HTTP snapshot: `http://192.168.0.88/cgi-bin/api.cgi?cmd=Snap&...` (via `reolink-aio`) | `reolink_snapshot` (HTTP poll, native 4K JPEG) | **on** (predator detection) | The yard, sky, and coop approach |
| `s7-cam` | Samsung Galaxy S7 phone (SM-G930F) running IP Webcam app | _itself_ | `192.168.0.249` | `rtsp://192.168.0.249:5554/camera` (UDP — path `/camera` is fixed by the IP Webcam Android app, not configurable) | RTSP via OpenCV | off | Coop interior (when phone is online — was offline 13-Apr-2026 PM) |
| `usb-cam` | Generic USB webcam (1920×1080) | Mac Mini "Bubba" | `192.168.0.105` (local) | AVFoundation device index 0 | `usb_avfoundation` snapshot poll | off | Brooder (heat-lamp lit) |
| `gwtc` | Built-in webcam on the Gateway laptop ("Hy-HD-Camera", 720p) | Gateway laptop (Windows 11) | `192.168.0.68` (DHCP — drifts; find by service signature on `:8554`) | `rtsp://192.168.0.68:8554/gwtc` (TCP — published by `ffmpeg` via dshow → MediaMTX) | RTSP via OpenCV | off | Coop interior |
| `mba-cam` | Built-in FaceTime HD webcam (Apple `0x106B:0x1570`, 720p) on the MacBook Air 2013 (MacBookAir6,2) | MacBook Air 2013 (macOS Big Sur 11.7.11) | `192.168.0.50` (DHCP) | `rtsp://192.168.0.50:8554/mba-cam` (TCP — published by `ffmpeg` via AVFoundation → MediaMTX) | RTSP via OpenCV | off | Brooder |

## What Runs Where

| Machine | IP | OS | Runs |
|---|---|---|---|
| **Mac Mini "Bubba"** | `192.168.0.105` (Ethernet primary) | macOS 26.3 | Farm Guardian (`guardian.py`), the FastAPI dashboard at `:6530`, Cloudflare tunnel to `guardian.markbarney.net`, the `usb-cam` directly via AVFoundation, LM Studio (port 1234) |
| **Gateway laptop ("GWTC")** | `192.168.0.68` (WiFi, DHCP) | Windows 11 | `mediamtx` service on `:8554` (publishes path `gwtc`), `farmcam` Shawl-wrapped service (ffmpeg dshow capture), `farmcam-watchdog` Shawl-wrapped service (auto-recovery for the post-reboot dshow zombie pattern), LM Studio (port 9099 — non-standard) |
| **MacBook Air 2013** | `192.168.0.50` (WiFi, DHCP) | macOS Big Sur 11.7.11 | `com.farmguardian.mediamtx` LaunchAgent on `:8554` (publishes path `mba-cam`), `com.farmguardian.mba-cam` LaunchAgent (ffmpeg AVFoundation capture) |
| **Reolink E1 Outdoor Pro** | `192.168.0.88` (WiFi) | Reolink firmware | The camera itself — exposes ONVIF + HTTP snapshot endpoint. Standalone, no host. |
| **Samsung Galaxy S7** | `192.168.0.249` (WiFi) | Android 8.0.0 + IP Webcam app | The phone itself — exposes RTSP at `:5554/camera`. Standalone, no host. |
| **MSI Katana 15 HX (Boss's machine)** | `192.168.0.3` | Windows | Not part of Guardian; lives next to the camera control center. |
| **Larry's MSI laptop** | `192.168.0.194` | Windows | OpenClaw node (separate project). |

## Where Each Camera's Frame Lands in the Stack

```
┌─────────────────┐   ┌──────────────────────────┐   ┌────────────────┐   ┌──────────────────┐
│  Camera         │ → │  Host machine            │ → │  Mac Mini      │ → │  Public website  │
│  (hardware)     │   │  (publishes if needed)   │   │  guardian.py   │   │  farm.markbarney │
└─────────────────┘   └──────────────────────────┘   └────────────────┘   └──────────────────┘

house-yard ─── (HTTP snap from camera itself) ──────► capture.py / ReolinkSnapshotSource ──► /api/cameras/house-yard/frame ──► Cloudflare tunnel
s7-cam ─────── (RTSP from phone itself) ────────────► capture.py / RTSP OpenCV ────────────► /api/cameras/s7-cam/frame
usb-cam ────── (AVFoundation, local) ────────────────► capture.py / UsbSnapshotSource ──────► /api/cameras/usb-cam/frame
gwtc ───────── ffmpeg → MediaMTX on GWTC :8554/gwtc ─► capture.py / RTSP OpenCV ────────────► /api/cameras/gwtc/frame
mba-cam ────── ffmpeg → MediaMTX on Air :8554/mba-cam ► capture.py / RTSP OpenCV ────────────► /api/cameras/mba-cam/frame
```

## Naming Rules (NON-NEGOTIABLE — see `feedback_camera_naming.md` in Bubba memory)

1. **Camera names are device-only.** `mba-cam`, `s7-cam`, `usb-cam`, `gwtc`. The grandfathered exception is `house-yard` (predates the rule). Never `brooder-cam`, `nestbox`, `coop-cam`, `incubator-cam`, etc.
2. **The rule applies to every layer.** `config.json` `name`, RTSP paths, MediaMTX paths, LaunchAgent labels, log filenames, dashboard labels, frontend `lib/cameras.ts` entries, MDX roster tables, thumb captions, and any string a user can see. **A 13-Apr-2026 incident:** `lib/cameras.ts` had `shortLabel: "Brooder"`, `"S7 brooder"`, `"MBA brooder"`, `"Nestbox"` — three different cameras all said "brooder" in the UI. Fix: drop any `location` field and label by hardware (`"USB"`, `"MBA"`, `"S7"`, `"GWTC"`, `"Reolink"`).
3. **The rule applies to publish paths too.** As of 2026-04-13 evening, the Gateway laptop's MediaMTX path was renamed `nestbox` → `gwtc` (CHANGELOG v2.24.1). The MacBook Air's path is `mba-cam`. If anyone adds a new ffmpeg→MediaMTX node, the path must match the camera's device name.
4. **"Where it's pointed today" is field-note material.** Put it in a diary entry, a CHANGELOG line, or a comment on a specific photo if needed. Don't put it in any data structure that drives UI or routing.

## When You Add a New Camera

1. Pick the device-name first. Cannot be a location. Short, lowercase, hyphenated. Examples in the table above.
2. Add the entry to `config.json` (and `config.example.json`). `name`, `ip`, `port`, `username`/`password` if any, `type` (`ptz` or `fixed`), `source` or `rtsp_url_override`, `rtsp_transport` (`tcp` if WiFi-published, `udp` only if you have hard evidence UDP works on that camera), `detection_enabled` (default `false` until you know the role).
3. If the camera is published via ffmpeg→MediaMTX on a host machine: the MediaMTX path **must equal** the camera's `name` field. The ffmpeg push URL on the host **must** push to that same path. The host's MediaMTX `paths:` block in `mediamtx.yml` **must** declare it.
4. Add the new row to **this** file's "The Five Cameras" table and "What Runs Where" if it introduces a new host. If the row count is wrong, the table is wrong.
5. Add the entry to `farm-2026/lib/cameras.ts` registry — `name`, `label`, `shortLabel`, `device`, **no location field** (per rule #2 above), `aspectRatio`. Update `farm-2026/content/projects/guardian/index.mdx` cameras table.
6. Restart Guardian on the Mac Mini (it reads `config.json` at start). Verify with `curl http://localhost:6530/api/cameras` — the new camera should appear with `online: true, capturing: true`. Then `curl http://localhost:6530/api/cameras/<name>/frame` should return a JPEG within ~2 capture intervals.

## When You Move a Camera

You don't rename anything. You write a field note about the new placement. The camera's name, RTSP path, MediaMTX path, LaunchAgent labels, and config entry stay exactly as they were. The only thing that changes is the rightmost "Currently aimed at" column in this file (and the analogous prose in field notes / CHANGELOG). **If you find yourself wanting to rename the camera because it moved, re-read rule #1.**

## Cross-references

- `CLAUDE.md` "Network & Machine Access" — host IPs, SSH keys, router quirks.
- `~/bubba-workspace/memory/reference/network.md` — master device table including the non-Guardian machines.
- `~/bubba-workspace/skills/macbook-air/SKILL.md` — Air-specific operations (TCC, screensaver, install recipes).
- `farm-guardian/deploy/macbook-air/` — canonical copies of the Air's LaunchAgent plists.
- `farm-guardian/deploy/gwtc/` — canonical copies of the Gateway laptop's `start-camera.bat`, `mediamtx.yml`, watchdog script, and install recipe.
- `farm-2026/lib/cameras.ts` — the frontend's camera registry (must stay in sync with `config.json` here).
- `~/.claude` auto-memory `feedback_camera_naming.md` — the device-not-location rule with rationale and the 13-Apr-2026 incident summary.
