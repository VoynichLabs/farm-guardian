# GWTC — Coop Camera Computer Setup

**Author:** Claude Opus 4.7 (1M context) — Bubba (header rewrite 17-Apr-2026); earlier revisions by Claude Opus 4.6 and sonnet-4-6.
**Date:** 11-April-2026 (hardware-identity correction + Debian-wipe reversal both landed 17-April-2026)
**PURPOSE:** The **single authoritative doc** for any Claude Code agent picking up GWTC. Covers what GWTC is (camera node + coop-side research speaker), SSH access, webcam streaming via ffmpeg + MediaMTX, the three Shawl services, the post-reboot watchdog, power / lock-screen quirks, and cross-links to the research-programme docs.

> ## 🟢 NEXT-CLAUDE READ-FIRST POINTER
>
> **If you are the next Claude Code agent assigned to GWTC, this is the only file you need to read first.** Everything else is a cross-reference.
>
> 1. **GWTC is NOT "just a camera box."** It's a Gateway laptop sitting in the chicken coop that currently does two things and is scoped to do more:
>    - hosts the `gwtc` camera feed into Farm Guardian (ffmpeg → MediaMTX RTSP on port 8554), and
>    - is the **speaker** for the flock acoustic-response study (`tools/flock-response/playback.py` triggers `System.Media.SoundPlayer.PlaySync()` on it over SSH).
>    - Planned next: screen as a visual-stimuli display for the visual arm of the study, ambient daytime dashboard, operant-conditioning sandbox. Those plans live at `docs/16-Apr-2026-gwtc-coop-node-capabilities-brainstorm.md` and `docs/16-Apr-2026-gwtc-visual-stimuli-plan.md`.
> 2. **Do NOT propose wiping / reimaging / OS-swapping GWTC** without reading `docs/17-Apr-2026-gwtc-windows-stabilization-plan.md` first. An earlier agent committed to a Debian wipe on 17-Apr-2026; it was armed, did not complete, and was reverted the same day because the entire research-programme tool scaffold is Windows-coded (PowerShell, `C:\Windows\Media\tada.wav`, Shawl services, the watchdog). That plan doc is the written record of why Windows stays.
> 3. **GWTC's biggest operational quirk is not a bug to fix — it's a quirk to accept.** Post-reboot the dshow webcam capture wedges for ~90 s; `farmcam-watchdog` auto-recovers it. Windows pre-login WiFi sometimes requires Boss to type PIN `5196` at the coop USB keyboard. Hard power-cycling the laptop is the approved first-line response (`feedback_gwtc_hard_reboot_freely.md`).
> 4. **Boss does not use this machine for anything else.** "It just needs to fucking work. It gets pooped on by chickens." No user data, no kid gloves, no waiting on Boss to do keyboard work at the coop if a hard reboot would cover the same failure mode.
>
> **⚠️ DO NOT CONFLATE TWO MACHINES.** An earlier version of this doc called GWTC an "MSI Katana." That was wrong.
> - **GWTC** (`192.168.0.68`) = old consumer **Gateway-brand laptop**, Windows 11, Celeron N4020 / 4 GB RAM / 60 GB eMMC, coop camera + audio-stimulus node. "GWTC" = Gateway + The Coop.
> - **MSI Katana 15 HX B14WGK** (`192.168.0.3`) = Boss's **primary workstation**, RTX 5070, 64 GB RAM. Has nothing to do with Farm Guardian.
>
> **Verified specs (17-Apr-2026, via `systeminfo` over SSH):** Gateway GWTC116-2 (manufactured by "GPU Company" OEM), Intel Celeron N4020 (2C/2T @ 1.1 GHz), 3.9 GB RAM, 60 GB Biwin eMMC (C:), 62 GB SD card in the internal SD slot (left over from the Debian-wipe attempt — harmless now that BCD is disarmed; do not ask Boss to remove it), Hy-HD-Camera built-in webcam, Realtek 8723DU USB WiFi (150 Mbps link, no ethernet), Windows 11 Home 22631.

---

## Machine Info

| Field | Value |
|---|---|
| Hostname | GWTC (653Pudding) |
| OS | Windows 11 |
| User | markb (domain: 653pudding\markb) |
| IP | 192.168.0.68 (WiFi, DHCP — may change; see note below) |
| SSH | Port 22, key auth |
| Role | Farm Guardian camera node — webcam streams to Mac Mini via RTSP |

**⚠️ IP may change after router reboot.** If SSH fails, check router DHCP list at http://192.168.0.1 (Advanced → Network → DHCP Server) or run a subnet scan:
```bash
for i in $(seq 1 254); do (nc -z -w 1 192.168.0.$i 22 2>/dev/null && echo "192.168.0.$i") & done; wait
```

---

## SSH Access (from Mac Mini)

```bash
ssh markb@192.168.0.68
```

Key auth is configured — `id_ed25519` is in `C:\ProgramData\ssh\administrators_authorized_keys`.

**If SSH times out:**
The WSL2 virtual network adapters (172.29.x / 172.21.x) poison the Windows routing table and break inbound SSH. Fix:

1. Open PowerShell as Administrator on GWTC
2. Paste:
```powershell
netsh winsock reset; netsh int ip reset
```
3. Reboot
4. SSH works immediately after reboot

Do NOT waste time on firewall rules, virtual adapter removal, or routing table hacks — the winsock/ip reset + reboot is the fix.

---

## Webcam Streaming (ffmpeg + MediaMTX)

The GWTC's built-in webcam (`Hy-HD-Camera`) streams to the Mac Mini via RTSP:

```
GWTC Laptop (192.168.0.68)
├── Hy-HD-Camera (built-in USB webcam, DirectShow)
├── ffmpeg — captures webcam, encodes H.264, pushes to localhost:8554
└── MediaMTX — RTSP server at rtsp://192.168.0.68:8554/gwtc

Mac Mini (192.168.0.105)
└── Farm Guardian
    └── capture.py → connects to rtsp://192.168.0.68:8554/gwtc
```

**Stream specs:** 1280x720, 15fps, H.264 (libx264 ultrafast), ~1 Mbps.

**MediaMTX version:** check `C:\mediamtx\mediamtx.exe --version` on the laptop. Constraints around acceptable versions on this host (in case of upgrade) are documented inline in `deploy/macbook-air/` writeups (the same dyld-symbol-floor concern applies on the Air; on the GWTC Win11 host, modern releases work fine).

**Path naming note:** The MediaMTX path was `nestbox` until 13-Apr-2026 evening; renamed to `gwtc` to match the device name we use everywhere else (locations change, devices don't). See `CHANGELOG.md` v2.23.1 for the cutover. **Don't reintroduce location-based stream paths** — see `HARDWARE_INVENTORY.md` rule #1.

### Windows Services

Three services run as auto-start, all wrapped by [Shawl](https://github.com/mtkennerly/shawl) so they survive reboots and crashes:

| Service | Wraps | Purpose | StartType |
|---|---|---|---|
| `mediamtx` | `C:\mediamtx\mediamtx.exe C:\mediamtx\mediamtx.yml` | RTSP server on `:8554` | Automatic |
| `farmcam` | `cmd /c C:\farm-services\start-camera.bat` | ffmpeg dshow capture, pushes to `rtsp://localhost:8554/gwtc` (with the `:loop` retry built into the bat file) | Automatic |
| `farmcam-watchdog` | `powershell -File C:\farm-services\farm-watchdog.ps1` | Detects + recovers the post-reboot dshow zombie pattern (kills wedged ffmpeg PID; Shawl respawns) | Automatic |

Canonical copies of `start-camera.bat`, `mediamtx.yml`, and `farm-watchdog.ps1` are version-controlled in this repo at `deploy/gwtc/`. Install / update / uninstall recipes for the watchdog are in `deploy/gwtc/install-watchdog.md`. The same recipe pattern applies to the other two services if you ever need to re-register them.

### Service Management (via SSH from the Mac Mini)

```bash
# Check all three
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'sc query mediamtx & sc query farmcam & sc query farmcam-watchdog'

# Restart the camera publisher (use this, not "restart farmcam" via sc — that hits the
# "1056: An instance of the service is already running" pattern because Shawl stays alive
# even when its child is killed by sc)
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'tasklist | findstr ffmpeg'      # find PID
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'taskkill /F /PID <pid>'         # Shawl respawns ffmpeg in ~3s

# Restart MediaMTX
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'sc stop mediamtx & sc start mediamtx'

# Restart watchdog
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'sc stop farmcam-watchdog & sc start farmcam-watchdog'

# Tail the live logs
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'powershell -Command "Get-Content C:\farm-services\logs\mediamtx.log -Tail 20"'
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'powershell -Command "Get-Content C:\farm-services\logs\watchdog.log -Tail 20"'
```

### Troubleshooting

If the stream returns 404 from MediaMTX but the machine is pingable: this is the post-reboot dshow zombie pattern (or a mid-operation wedge of the same shape). The `farmcam-watchdog` service auto-recovers within ~90s — **wait, don't intervene**. Full failure-mode writeup, manual fallback if the watchdog itself is broken, and "what does NOT help" list: `docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md` "Addendum -- Post-Reboot dshow Zombie Pattern" section.

If the machine itself is unreachable (port 8554 closed, SSH won't connect): that's the *reachability* incident, a separate failure mode. Use the diagnostic recipe at the top of the same troubleshooting doc (sweep `/24` for service signatures on `:8554` or `:9099` — don't trust pings or MAC tables). DO NOT confuse the two failure modes; the recoveries are completely different.

---

## Power Management

Sleep and hibernate are **disabled** — this is a 24/7 camera node:

```powershell
# Already configured (11-Apr-2026):
powercfg /change standby-timeout-ac 0    # Never sleep on AC
powercfg /change standby-timeout-dc 0    # Never sleep on battery
powercfg /change hibernate-timeout-ac 0  # Never hibernate on AC
powercfg /change hibernate-timeout-dc 0  # Never hibernate on battery
powercfg /change monitor-timeout-ac 5    # Display off after 5 min (saves power)
powercfg /change monitor-timeout-dc 5
```

The display turning off does NOT affect the webcam — USB webcams operate independently of the display.

---

## Farm Guardian Config Entry

In `config.json` on the Mac Mini:

```json
{
  "name": "gwtc",
  "ip": "192.168.0.68",
  "port": 8554,
  "username": "",
  "password": "",
  "type": "fixed",
  "rtsp_transport": "tcp",
  "rtsp_url_override": "rtsp://192.168.0.68:8554/gwtc",
  "detection_enabled": false
}
```

Named `gwtc` (device name, not location) per project convention — locations change, device names don't.

---

## Bloatware Removal

**Note:** The original list below included `MSI.Center`, `MSI.Dragon.Center`, etc. Those **do not exist on GWTC** — GWTC is a Gateway-brand laptop; those MSI entries were cross-wired from a different machine. They're left as no-ops (the `Get-AppxPackage -Name "*MSI.*"` pattern returns nothing on GWTC and harmlessly falls through). If you're updating this list, the OEM crapware actually on GWTC is Gateway/Acer/Walmart-tier (ExperienceIndexOK, various Realtek/Intel preload panels, whatever Microsoft Store prepopulates on budget Win 11 SKUs). Remove the MSI entries next time you touch this doc on the live machine.

```powershell
$apps = @(
    "XboxGameOverlay",
    "XboxGamingOverlay",
    "XboxSpeechToTextOverlay",
    "XboxIdentityProvider",
    "Xbox.TCUI",
    "MicrosoftTeams",
    "Clipchamp.Clipchamp",
    "Microsoft.BingNews",
    "Microsoft.BingWeather",
    "Microsoft.GetHelp",
    "Microsoft.Getstarted",
    "Microsoft.MicrosoftSolitaireCollection",
    "Microsoft.People",
    "Microsoft.WindowsFeedbackHub",
    "Microsoft.YourPhone",
    "Microsoft.ZuneMusic",
    "Microsoft.ZuneVideo",
    "SpotifyAB.SpotifyMusic",
    "Disney.37853D22215B2"
    # NOTE: MSI.* entries removed 17-Apr-2026 — GWTC is a Gateway laptop, not an MSI Katana.
)
foreach ($app in $apps) {
    Get-AppxPackage -Name "*$app*" | Remove-AppxPackage -ErrorAction SilentlyContinue
    Get-AppxProvisionedPackage -Online | Where-Object DisplayName -like "*$app*" | Remove-AppxProvisionedPackage -Online -ErrorAction SilentlyContinue
}
Write-Host "Done"
```

---

## Notes

- WiFi password: `4136870990!` (SSID: `653 Pudding Hill 2G Private`)
- Windows Firewall is currently DISABLED on GWTC — do not re-enable without testing SSH still works
- sshd service is set to Automatic startup
- GWTC has WSL2 installed — leave it alone unless Boss asks; it creates 172.x virtual IPs that are not routable from the Mac Mini. (Earlier writeup theories blamed WSL2 for the pre-login WiFi outage — that was a misdiagnosis; see `project_gwtc_offline_pre_login_wifi.md`.)
- Shawl installed via `winget install shawl` (v1.8.0) — wraps executables as Windows services

## Research-programme role (the part that makes GWTC more than a camera)

GWTC is the **coop-side node** for a flock-behaviour research programme. Three arms, all scoped in `docs/`:

- **Audio arm (scaffold committed on `main`).** `tools/flock-response/playback.py` triggers blocking playback on GWTC via SSH → `System.Media.SoundPlayer.PlaySync()`. Smoke-tests safely against `C:\Windows\Media\tada.wav`. Real stimuli get pushed to `C:\farm-sounds\` by `tools/flock-response/deploy/push-sounds-to-gwtc.sh`. Study design: `docs/16-Apr-2026-flock-acoustic-response-study-plan.md`. **Welfare floor applies** — do not pre-play real stimuli on the cohort; every pre-pilot playback contaminates the H5 habituation measurement.
- **Visual arm (plan only).** GWTC's 1366×768 screen as a display for silhouette / predator-image / food-cue stimuli. Blocked on daytime apparatus unblocks (screen orientation in the coop, SSH-to-interactive-desktop session probe, brightness / colour-as-light calibration, GPU footprint test). Design: `docs/16-Apr-2026-gwtc-visual-stimuli-plan.md`.
- **Broader brainstorm.** Night-light, sunrise simulation, ambient dashboard, operant-conditioning sandbox, BirdNET-on-coop-audio. Design space: `docs/16-Apr-2026-gwtc-coop-node-capabilities-brainstorm.md`. Most ideas have welfare-floor constraints that gate them behind daytime calibration.

**None of this means "wipe Windows to make room."** Every arm's committed or planned code path targets Windows (`System.Media.SoundPlayer`, `schtasks /Create /IT`, `[Console]::Beep`, Shawl services). The 17-Apr-2026 Debian-wipe attempt was reverted specifically because it would have invalidated that scaffold.
