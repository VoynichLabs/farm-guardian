# GWTC Coop Camera + S7 Nesting Box — Plan

**Author:** Claude Opus 4.6
**Date:** 08-April-2026
**Goal:** Add the GWTC laptop as a third camera ("coop") streaming its built-in webcam to Farm Guardian. The S7 remains as the nesting box camera, powered via USB from the GWTC. Address S7 battery health and power management.

---

## Physical Layout

```
Chicken Coop
├── GWTC Laptop (inside coop, lid open, keyboard covered)
│   ├── Built-in webcam → "coop" camera (views interior of coop)
│   ├── USB port → powers the S7 phone
│   └── WiFi → streams to Mac Mini
│
└── Nesting Box (attached to coop)
    └── Samsung Galaxy S7 (inside nesting box)
        ├── Rear camera → "nesting-box" camera (views inside nesting box)
        ├── USB cable → to GWTC for power
        └── WiFi → streams RTSP to Mac Mini
```

**Three cameras total:**
1. **house-yard** — Reolink E1 Outdoor Pro (PTZ, ONVIF) — existing
2. **nesting-box** — S7 phone running RTSP Camera Server — existing, unchanged
3. **coop** — GWTC laptop webcam via ffmpeg + MediaMTX — NEW

---

## Scope

**In:**
- Install ffmpeg + MediaMTX on GWTC (Windows 11)
- Configure ffmpeg to capture the `Hy-HD-Camera` via DirectShow and push RTSP
- Set both up as auto-start Windows services via Shawl (survive reboots)
- Add "coop" camera entry to `config.json`
- Update dashboard to support three camera feeds
- Address S7 battery health (screen dimming, charge management)
- Update docs (GWTC_SETUP.md, CHANGELOG.md)

**Out:**
- Changes to the existing nesting-box (S7) camera config
- Detection on the coop camera (set `detection_enabled: false` initially)
- Network infrastructure changes (DHCP reservation, WiFi QoS)

---

## Part 1: GWTC Webcam → RTSP Stream

### Architecture

```
GWTC Laptop (192.168.0.68)
├── Hy-HD-Camera (built-in USB webcam, DirectShow)
├── ffmpeg — captures webcam, encodes H.264, pushes to localhost:8554
└── MediaMTX v1.16.3 — RTSP server, publishes at rtsp://192.168.0.68:8554/coop

Mac Mini (192.168.0.105)
└── Farm Guardian
    └── capture.py → connects to rtsp://192.168.0.68:8554/coop
```

### Why MediaMTX + ffmpeg

- **ffmpeg cannot act as an RTSP server** — it can only *push* (publish) to one. Verified: ffmpeg's RTSP output acts as a client, not a server. It needs an external server to accept the push and re-serve it to consumers.
- **MediaMTX** (bluenviron/mediamtx, v1.16.3) — single ~10MB binary, zero dependencies, zero config needed for basic RTSP re-serving. Actively maintained as of April 2026 with Windows-specific fixes. Pre-built Windows amd64 binaries on every release.
- **MediaMTX handles client reconnects** — when Guardian's capture.py disconnects and reconnects (which it does with exponential backoff), MediaMTX serves the stream seamlessly. No state to manage.

### Why not alternatives

| Alternative | Verdict | Reason |
|---|---|---|
| ffmpeg alone | **Cannot** | Not an RTSP server — can only push to one |
| VLC RTSP server | **Not recommended** | Brittle for 24/7 use; VLC is a media player, not a streaming server |
| go2rtc | **Viable alternative** | Simpler config, zero-dependency, used in Home Assistant ecosystem. Worth revisiting if MediaMTX causes issues. |
| GStreamer | **Overkill** | Higher complexity, steeper learning curve, no benefit here |

### ffmpeg Capture Command (verified)

```bash
# List available DirectShow devices (run once to confirm device name)
ffmpeg -list_devices true -f dshow -i dummy

# Capture webcam → push to MediaMTX RTSP
ffmpeg -f dshow -video_size 640x480 -framerate 15 -i video="Hy-HD-Camera" \
  -c:v libx264 -preset ultrafast -tune zerolatency \
  -f rtsp rtsp://127.0.0.1:8554/coop
```

Notes:
- `-video_size 640x480 -framerate 15` — constrain capture to reduce CPU/bandwidth. Cheap webcam is likely 720p max; 640x480@15fps is plenty for coop monitoring.
- `-preset ultrafast -tune zerolatency` — lowest possible encoding latency.
- Device name `"Hy-HD-Camera"` must match exactly what DirectShow reports. Will verify with `-list_devices` during setup.

### Windows Services (auto-start)

**Using Shawl** (not NSSM — NSSM's last release was 2017 and is abandoned).

Shawl is a single Rust binary, actively maintained (v1.7.0, Jan 2025), installable via `winget install shawl`. It wraps any executable as a proper Windows service.

```powershell
# Install Shawl
winget install shawl

# Create MediaMTX service
shawl add --name mediamtx -- C:\tools\mediamtx\mediamtx.exe

# Create ffmpeg service (depends on MediaMTX)
shawl add --name ffmpeg-coop --dependencies mediamtx -- C:\tools\ffmpeg\bin\ffmpeg.exe -f dshow -video_size 640x480 -framerate 15 -i video="Hy-HD-Camera" -c:v libx264 -preset ultrafast -tune zerolatency -f rtsp rtsp://127.0.0.1:8554/coop

# Set both to auto-start
sc config mediamtx start= auto
sc config ffmpeg-coop start= auto
```

### Farm Guardian Config Addition

```json
{
  "name": "coop",
  "ip": "192.168.0.68",
  "port": 8554,
  "username": "",
  "password": "",
  "onvif_port": 0,
  "type": "fixed",
  "rtsp_transport": "tcp",
  "rtsp_url_override": "rtsp://192.168.0.68:8554/coop",
  "detection_enabled": false
}
```

---

## Part 2: S7 Battery Health & Power Management

### The Problem

The S7 is plugged in 24/7 via USB to the GWTC laptop. On Android 8.0.0, Samsung's "Protect Battery" charge-limiting feature is **not available** (requires One UI 4.0+ / Android 12+). Running a lithium-ion battery at 100% charge continuously — especially in a coop environment that gets warm in summer — is a genuine **battery swelling and fire risk**.

### Options (ranked by feasibility)

| Option | Requires | Pros | Cons |
|---|---|---|---|
| 1. Smart plug on USB charger | Smart plug + automation | Safest; cycle charge between 20-80% | Needs Home Assistant or similar; phone goes offline when unplugged |
| 2. Root + Battery Charge Limit app | Rooting S7 | Hard-stops charging at set % automatically | Rooting voids warranty (irrelevant for $0 phone), risk of bricking |
| 3. Screen-off RTSP app | App swap | Reduces heat/power draw significantly | Doesn't solve overcharge; just reduces symptoms |
| 4. Manual USB disconnect | Physical access | Zero cost | Impractical for daily use |
| 5. Accept the risk + monitor | Nothing | Simplest | Battery could swell; fire risk in wooden coop |

### Recommended Approach

**Short-term (now):**
- Switch S7 to a screen-off-capable RTSP app (or verify current app supports screen-off streaming)
- Disable all unnecessary radios (Bluetooth, NFC, GPS, mobile data) — use airplane mode + WiFi only
- Reduce RTSP stream resolution/framerate in app settings to lower CPU load and heat

**Medium-term (soon):**
- Put the GWTC's USB port on a smart plug (or use a USB power switch controlled via the GWTC itself) to cycle the S7's power. When power is cut, the S7 runs on battery; when restored, it charges. This keeps the battery cycling between ~30-80%.
- Alternatively: control USB power from the GWTC via PowerShell (some laptops support per-port USB power control via `devcon` or BIOS settings). Needs investigation.

**The GWTC laptop itself can manage the S7's power** — since the S7 is plugged into the GWTC via USB, we could potentially write a scheduled task on the GWTC that disables/enables the USB port on a timer. This would be the cleanest solution since it requires no additional hardware.

### Screen Dimming on GWTC

The GWTC laptop screen should be dimmed to minimum (or turned off) to:
- Reduce power consumption
- Reduce heat in the coop
- Extend screen lifespan

```powershell
# Set brightness to 0% (screen stays on but fully dimmed)
(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, 0)

# Or turn display off entirely (webcam still works with display off)
powershell -Command "(Add-Type '[DllImport(\"user32.dll\")] public static extern int SendMessage(int h,int m,int w,int l);' -Name a -Pas)::a(-1,0x0112,0xF170,2)"
```

**Important:** Need to verify that the webcam continues to capture when the display is off. Most USB webcams operate independently of the display, but this should be tested.

---

## TODOs

### Part 1: GWTC Coop Camera

1. **Install ffmpeg on GWTC**
   - Download ffmpeg Windows build (gyan.dev, essentials)
   - Extract to `C:\tools\ffmpeg\`
   - Add `C:\tools\ffmpeg\bin` to system PATH

2. **Install MediaMTX on GWTC**
   - Download `mediamtx_v1.16.3_windows_amd64.zip` from GitHub releases
   - Extract to `C:\tools\mediamtx\`

3. **Test webcam capture**
   - Run `ffmpeg -list_devices true -f dshow -i dummy` to confirm device name
   - Run `ffmpeg -f dshow -i video="Hy-HD-Camera" -t 5 test.mp4` to test capture
   - Note actual resolution and framerate

4. **Wire ffmpeg → MediaMTX**
   - Start MediaMTX manually, then run ffmpeg push command
   - Verify from Mac Mini: `ffplay rtsp://192.168.0.68:8554/coop`

5. **Install Shawl and create services**
   - `winget install shawl`
   - Create mediamtx service, then ffmpeg-coop service with dependency
   - Set both to auto-start
   - Reboot GWTC and verify services start automatically

6. **Add coop camera to Farm Guardian config**
   - Add third camera entry to `config.json`
   - Set `detection_enabled: false`

7. **Update dashboard for three cameras**
   - Modify `static/index.html` layout for 3 feeds
   - Test all three feeds display correctly

8. **Verify end-to-end**
   - Restart Guardian, confirm all 3 camera feeds on dashboard
   - Test GWTC reboot → verify coop stream recovers
   - Test Guardian restart → verify reconnection to all 3 cameras

### Part 2: S7 Power Management

9. **Immediate: reduce S7 power draw**
   - Enable airplane mode + WiFi only on S7
   - Lower RTSP stream resolution/framerate in app settings
   - Test if current RTSP Camera Server app supports screen-off streaming

10. **Investigate USB power control from GWTC**
    - Check if GWTC BIOS or `devcon` supports per-port USB power toggle
    - If yes, write a PowerShell scheduled task to cycle USB power
    - If no, evaluate smart plug approach

11. **GWTC screen management**
    - Test webcam capture with display off
    - If webcam works with display off, add display-off command to startup script
    - If not, set brightness to 0%

### Docs

12. **Update GWTC_SETUP.md** — add coop camera streaming setup, S7 power notes
13. **Update CHANGELOG.md** — new camera addition
14. **Update CLAUDE.md** — add Camera 3 description, update architecture

---

## Docs / Changelog Touchpoints

- `docs/GWTC_SETUP.md` — Add coop camera streaming section; update S7 section with power management
- `CHANGELOG.md` — New entry for coop camera addition
- `CLAUDE.md` — Add Camera 3 (coop); update physical layout description
- `config.json` — Add coop camera entry
- `config.example.json` — Add example coop camera entry
- `static/index.html` — Three-camera layout

---

## Risk Notes

- **GWTC IP is DHCP** — if IP changes, both coop AND nesting-box streams break (since S7 connects through GWTC's network). Set a DHCP reservation on the router.
- **Webcam quality** — cheap laptop webcam, likely 720p. Fine for monitoring with detection disabled.
- **Heat** — laptop + phone in a coop in summer. Monitor for thermal throttling. Lid stays open for airflow. Screen dimming reduces heat significantly.
- **S7 battery** — lithium-ion swelling risk if kept at 100% charge continuously in warm environment. USB power cycling is the priority mitigation. **Do not ignore this — swelling battery in a wooden coop is a fire hazard.**
- **WiFi bandwidth** — three simultaneous RTSP streams. At 640x480@15fps H.264 (~1-2 Mbps each), total is ~3-6 Mbps. Well within WiFi capacity, especially with the Reolink on sub-stream (per adjacent plan).
- **Single point of failure** — if GWTC loses WiFi or power, both coop AND nesting-box cameras go offline (S7 loses power source). The Reolink house-yard camera remains independent.
