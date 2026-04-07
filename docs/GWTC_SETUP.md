# GWTC — Nesting Box Camera Computer Setup

**Author:** Bubba (claude-sonnet-4-6)
**Date:** 07-April-2026
**PURPOSE:** Instructions for setting up the MSI Katana (GWTC) as the dedicated Farm Guardian nesting box camera computer. Covers SSH access, bloatware removal, and IP Webcam configuration.

---

## Machine Info

| Field | Value |
|---|---|
| Hostname | GWTC |
| OS | Windows 11 |
| User | markb (domain: 653pudding\markb) |
| IP | 192.168.0.68 (WiFi, DHCP — may change; see note below) |
| SSH | Port 22, key auth |
| Role | Farm Guardian nesting box camera node |

**⚠️ IP may change after router reboot.** If SSH fails, check router DHCP list at http://192.168.0.1 (Advanced → Network → DHCP Server) or run a subnet scan:
```bash
for i in $(seq 1 254); do (nc -z -w 1 192.168.0.$i 22 2>/dev/null && echo "192.168.0.$i") & done; wait
```

---

## SSH Access (from Mac Mini or any machine with the key)

```bash
ssh -o StrictHostKeyChecking=no markb@192.168.0.68
```

Key auth is configured — Bubba's `id_ed25519` is in `C:\ProgramData\ssh\administrators_authorized_keys`.

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

## IP Webcam Setup (Android phone on nesting box)

The nesting box camera runs IP Webcam on an Android phone. To configure:

1. Install IP Webcam APK (sideload — no Google account needed)
2. Open IP Webcam → Start Server
3. Default port: 8080
4. Stream URL: `http://<phone-ip>:8080/video`
5. Snapshot URL: `http://<phone-ip>:8080/shot.jpg`

The GWTC machine sits physically at the nesting box and connects the phone's stream into Farm Guardian on the Mac Mini.

---

## Farm Guardian Integration

Farm Guardian runs on the Mac Mini (192.168.0.105). GWTC is a camera node, not the compute node.

To add the nesting box camera to Guardian, edit `config.json` on the Mac Mini and add a camera entry:

```json
{
  "name": "NestingBox",
  "url": "http://<phone-ip>:8080/video",
  "snapshot_url": "http://<phone-ip>:8080/shot.jpg",
  "type": "ip_webcam",
  "location": "nesting_box"
}
```

Then restart Guardian: `python3 guardian.py`

---

## Notes

- WiFi password: `4136870990!` (SSID: `653 Pudding Hill 2G Private`)
- Windows Firewall is currently DISABLED on GWTC — do not re-enable without testing SSH still works
- sshd service is set to Automatic startup
- GWTC has WSL2 installed — do not remove it, but be aware it creates 172.x virtual IPs that are not routable from the Mac Mini
