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
└── MediaMTX v1.16.3 — RTSP server at rtsp://192.168.0.68:8554/nestbox

Mac Mini (192.168.0.105)
└── Farm Guardian
    └── capture.py → connects to rtsp://192.168.0.68:8554/nestbox
```

**Stream specs:** 1280x720, 15fps, H.264 (libx264 ultrafast), ~1 Mbps.

### Windows Services

Both run as auto-start Windows services that survive reboots and crashes:

| Service | Tool | Status | StartType |
|---|---|---|---|
| `mediamtx` | Shawl | Running | Automatic |
| `ffmpeg-nestbox` | Shawl | Running | Automatic |

**ffmpeg path:** `C:\Users\markb\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe`

**ffmpeg command (wrapped by Shawl):**
```
ffmpeg -f dshow -video_size 1280x720 -framerate 15 -i video="Hy-HD-Camera" -c:v libx264 -preset ultrafast -tune zerolatency -b:v 1000k -f rtsp rtsp://localhost:8554/nestbox
```

### Service Management (via SSH)

```powershell
# Check status
Get-Service mediamtx,ffmpeg-nestbox | Format-Table Name,Status,StartType

# Restart ffmpeg (if stream goes down)
Restart-Service ffmpeg-nestbox

# Restart both
Restart-Service mediamtx; Start-Sleep 2; Restart-Service ffmpeg-nestbox

# View ffmpeg process details
Get-WmiObject Win32_Process -Filter "Name='ffmpeg.exe'" | Select ProcessId,CommandLine
```

### Troubleshooting

If the stream returns 404 from MediaMTX but the machine is pingable:
1. ffmpeg likely lost the webcam handle (after sleep/crash)
2. `Restart-Service ffmpeg-nestbox` fixes it
3. Guardian's capture.py reconnects automatically (exponential backoff)

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
  "rtsp_url_override": "rtsp://192.168.0.68:8554/nestbox",
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
