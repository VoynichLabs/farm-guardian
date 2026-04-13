# MacBook Air 2013 — Farm Guardian Camera Node Plan

**Date:** 12-April-2026
**Goal:** Bring the boss's 2013 MacBook Air (`Marks-MacBook-Air.local`, `192.168.0.50`) online as a fifth Farm Guardian camera node. The Air was brought up as a secondary Claude Code host earlier in the same session; this plan adds a camera role on the same hardware.

**Update 13-April-2026 (Claude Opus 4.6):** Implemented. RTSP path stays `mba-cam` (named after the device, per the device-not-location naming rule — see CHANGELOG v2.11.0 and the v2.22.0 entry below). Camera is aimed at the brooder for now but the name doesn't encode that. Screensaver disabled in addition to the original power settings. **MediaMTX v1.16.3 does not run on Big Sur** (`dyld: Symbol not found: _SecTrustCopyCertificateChain` — that symbol is macOS 12+); v1.13.1 is the latest darwin_amd64 build that runs on Big Sur 11. **FaceTime HD Camera does not support 15fps** at 720p — only 1.0fps and 30.0fps. Capture command updated to `-framerate 30 ... -r 15` (capture at 30, re-rate to 15 before encode). Built and bootstrapped both LaunchAgents successfully; ffmpeg is blocked at the AVFoundation capture-open call until **TCC Camera permission is granted at the Air's keyboard** — see CHANGELOG v2.22.0 Open Items for the unstick procedure.

---

## Physical Context

Boss's 2013 MacBook Air has been:
- Cleaned up (junk apps/files removed 03-April-2026).
- Login keychain reset (12-April-2026) — old `login.keychain` files moved aside, fresh one created on next login.
- Node.js 22.22.2 installed via tarball (`~/.local/node/`) — Homebrew is broken on Big Sur, tarball route works.
- Claude Code CLI 2.1.104 installed (`~/.local/npm-global/bin/claude`).
- Power settings locked down: `sleep=0 disksleep=0 displaysleep=0 standby=0 autopoweroff=0 hibernatemode=0 powernap=0 autorestart=1`.
- **Clamshell sleep cannot be overridden** on this firmware. Lid stays open, always. This is the operational constraint driving placement.

Full machine skill doc: `bubba-workspace/skills/macbook-air/SKILL.md`.

---

## Hardware

| Component | Detail |
|---|---|
| Host | MacBookAir6,2 (2013) — Intel Core i5 Haswell 1.3GHz, 2C, 8GB RAM, 113GB SSD |
| OS | macOS Big Sur 11.7.11 (hardware ceiling) |
| IP | 192.168.0.50 (DHCP, WiFi only — no ethernet adapter) |
| Camera | Built-in FaceTime HD Camera — Apple VendorID `0x106B`, ProductID `0x1570`, Unique ID `DJH4131MBP2F9TCC7`. 720p. |
| Power | Must be kept on AC (set `autorestart=1` so it survives outages). Lid open forever. |

---

## Architecture (mirrors GWTC pattern)

```
MacBook Air (192.168.0.50)
├── FaceTime HD Camera (built-in, AVFoundation)
├── ffmpeg — captures camera, encodes H.264, pushes to localhost:8554
└── MediaMTX — RTSP server, publishes at rtsp://192.168.0.50:8554/mba-cam

Mac Mini (192.168.0.105)
└── Farm Guardian
    └── capture.py → rtsp://192.168.0.50:8554/mba-cam
```

Same toolchain decisions as the GWTC plan (08-Apr-2026-gwtc-webcam-stream-plan.md):
- **ffmpeg** captures + pushes; cannot serve RTSP on its own.
- **MediaMTX** re-serves so Guardian's capture client can reconnect without tearing anything down.
- **No Homebrew.** Big Sur is unsupported by Homebrew. All binaries installed as static tarballs into `~/.local/bin/`.

---

## Scope

**In:**
- Install ffmpeg (static darwin-x64 build) + MediaMTX (darwin-amd64 release) into `~/.local/bin/` on the Air.
- Configure ffmpeg to capture the FaceTime HD Camera via AVFoundation and publish to local MediaMTX.
- Configure MediaMTX to serve `rtsp://192.168.0.50:8554/mba-cam`.
- launchd `LaunchAgent` plists to auto-start ffmpeg + MediaMTX on login, restart on crash.
- Add `mba-cam` camera entry to Guardian's `config.json`.
- Update dashboard to render the new feed.
- Update `CHANGELOG.md` and cross-link to `bubba-workspace/skills/macbook-air/SKILL.md`.

**Out (this plan):**
- Detection on the mba-cam (start with `detection_enabled: false`; enable after placement decision).
- Physical placement (boss decides where the Air lives).
- Any changes to the existing `house-yard`, `s7-cam`, `usb-cam`, or `gwtc` camera configs.

---

## Part 1: Install ffmpeg

The maintainable option on Big Sur is the static build. Pick one known-good source at install time (evermeet.cx / osxexperts) and drop the binary directly into `~/.local/bin/ffmpeg`.

```bash
# On the Air (via SSH from Mini):
mkdir -p ~/.local/bin
# download a static darwin-x64 ffmpeg build, unzip to ~/.local/bin/ffmpeg
chmod +x ~/.local/bin/ffmpeg
~/.local/bin/ffmpeg -version
```

(PATH `~/.local/bin` is already wired in `~/.zshrc`, `~/.bash_profile`, `~/.bashrc` from the Claude Code setup.)

**Listing AVFoundation devices:**

```bash
ffmpeg -f avfoundation -list_devices true -i ""
```

Expected output includes the FaceTime HD Camera with a numeric index. Use that index in the capture command.

---

## Part 2: Install MediaMTX

```bash
cd ~/.local/bin
curl -fsSLO "https://github.com/bluenviron/mediamtx/releases/download/v1.16.3/mediamtx_v1.16.3_darwin_amd64.tar.gz"
tar -xzf mediamtx_v1.16.3_darwin_amd64.tar.gz mediamtx mediamtx.yml
rm mediamtx_v1.16.3_darwin_amd64.tar.gz
# default mediamtx.yml is fine for a single unauthenticated local RTSP path
./mediamtx -version
```

**Default config** is sufficient: any path pushed to is served from the same path. We'll push `mba-cam` and Guardian will consume `mba-cam`.

---

## Part 3: ffmpeg capture command

```bash
~/.local/bin/ffmpeg \
  -f avfoundation -framerate 15 -video_size 1280x720 -i "0" \
  -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p \
  -g 30 -b:v 1500k \
  -f rtsp rtsp://127.0.0.1:8554/mba-cam
```

Notes:
- `-i "0"` = video device index 0 = FaceTime HD Camera (verify with `-list_devices`).
- 15 fps + 1.5 Mbps is plenty for a chicken-watching angle on 1.3GHz Haswell. The Air has no hardware encoder we can trust here — `libx264 ultrafast` keeps CPU reasonable.
- No audio capture — not useful for Guardian, saves cycles.

---

## Part 4: TCC camera permission (critical blocker)

**macOS will not let any process access the camera without TCC approval for the parent-responsible application.** This must be granted at the Air's keyboard once. Options:

1. **Launch ffmpeg from Terminal at the Air's keyboard the first time.** macOS prompts "Terminal would like to access the camera" → click Allow. After that, Terminal (and anything it spawns, including launchd agents that inherit that responsibility) has camera access.
2. **Or** launch as a launchd `LaunchAgent` and handle the TCC prompt when it fires on first invocation.

The SSH-driven install can set everything up, but the first camera-touching run has to happen at the Air's keyboard, or via `ssh -t` to a logged-in GUI session that can surface the TCC prompt.

---

## Part 5: launchd agents

Two plists in `~/Library/LaunchAgents/`:

- `com.farmguardian.mediamtx.plist` — runs `~/.local/bin/mediamtx ~/.local/bin/mediamtx.yml`, `KeepAlive: true`.
- `com.farmguardian.mba-cam.plist` — runs the ffmpeg capture command above, `KeepAlive: true`, depends on mediamtx (use a short start delay).

Load:
```bash
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.farmguardian.mediamtx.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.farmguardian.mba-cam.plist
```

**Log rotation:** both plists route stdout/stderr to `~/Library/Logs/farmguardian/*.log`. Rotate via logrotate-style cron or a tiny launchd StartCalendarInterval job — out of scope for this plan, open item.

---

## Part 6: Farm Guardian `config.json` entry

Append to the `cameras` array (between existing entries, keep valid JSON):

```json
{
  "name": "mba-cam",
  "ip": "192.168.0.50",
  "port": 8554,
  "username": "",
  "password": "",
  "type": "fixed",
  "rtsp_transport": "tcp",
  "rtsp_url_override": "rtsp://192.168.0.50:8554/mba-cam",
  "detection_enabled": false
}
```

- `rtsp_transport: tcp` because the Air is WiFi-only and UDP on this LAN is unreliable for the same reasons the Reolink runs TCP.
- `detection_enabled: false` until the boss decides placement and role.

---

## Part 7: Verification checklist

From the Mac Mini after install:

```bash
# 1. Confirm MediaMTX listening
nc -z -w 5 192.168.0.50 8554 && echo "mediamtx reachable"

# 2. Pull an RTSP snapshot
ffmpeg -rtsp_transport tcp -i rtsp://192.168.0.50:8554/mba-cam -vframes 1 -y /tmp/mba-cam-test.jpg
file /tmp/mba-cam-test.jpg   # should be JPEG data

# 3. Guardian restart and log check
# (via Guardian's standard restart path)
# Look for: "Camera mba-cam connected"
```

---

## Open Items

- [ ] Physical placement of the Air — where does it live, where does the lid-open camera point?
- [ ] TCC permission grant at the Air's keyboard (one-time).
- [ ] Log rotation strategy for the two LaunchAgent logs.
- [ ] Decision on `detection_enabled` once role is set. If aimed at run/coop perimeter, YOLO pipeline makes sense; if it's an indoor utility angle, leave off.
- [ ] Apply the same USB + Samsung Galaxy S7 ADB role to this node (separate plan).

---

## Cross-references

- `bubba-workspace/skills/macbook-air/SKILL.md` — full machine operations, including power settings, Claude Code install, Node-via-tarball recipe, keychain reset.
- `bubba-workspace/memory/reference/network.md` — device table, router creds, ICMP warning, MacBook Air row (updated 12-April-2026).
- `bubba-workspace/memory/2026-04-12-macbook-air-claude-setup.md` — session log for the Claude Code bring-up.
- `docs/08-Apr-2026-gwtc-webcam-stream-plan.md` — reference pattern for ffmpeg + MediaMTX camera node.
- `GWTC_SETUP.md` — similar machine-specific setup document in this repo.
