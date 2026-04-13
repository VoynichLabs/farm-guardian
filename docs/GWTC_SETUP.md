# GWTC — Coop Camera Computer Setup

**Author:** Claude Opus 4.6 (updated), Bubba / claude-sonnet-4-6 (original)
**Date:** 11-April-2026
**PURPOSE:** Instructions for the MSI Katana (GWTC) as a dedicated Farm Guardian camera node. Covers SSH access, webcam streaming via ffmpeg + MediaMTX, Windows services, power management, and bloatware removal.

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

SSH in and run this to uninstall common MSI/Windows crapware in one shot:

```powershell
$apps = @(
    "MSI.Center",
    "MSI.Dragon.Center",
    "MSI.App.Player",
    "MSI.Gaming.App",
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
- GWTC has WSL2 installed — do not remove it, but be aware it creates 172.x virtual IPs that are not routable from the Mac Mini
- Shawl installed via `winget install shawl` (v1.8.0) — wraps executables as Windows services
