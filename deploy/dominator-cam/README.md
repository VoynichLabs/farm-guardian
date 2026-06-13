# dominator-cam + usb-cam on the MSI Dominator GT72 (`192.168.0.194`)

As of **2026-06-12** the MSI Dominator hosts **two** Guardian camera feeds at once, because
the portable `usb-cam` was physically plugged into it (alongside the laptop's built-in webcam):

| Guardian camera | Physical device | Bound by | Port | Snapshot URL |
|---|---|---|---|---|
| `dominator-cam` | built-in **BisonCam NB Pro** (VID 5986) | name `BisonCam` | 8089 | `http://192.168.0.194:8089/photo.jpg` |
| `usb-cam`       | external **USB CAMERA** (VID 32E6 PID 9221) | name `USB CAMERA` | 8090 | `http://192.168.0.194:8090/photo.jpg` |

Both are the same `usb_cam_host.py` FastAPI service (`C:\farm-services\dominator-cam\`), run as
**two instances** on two ports.

## Name-binding (replug/reboot-proof — labels can't swap)

Each instance is pinned to its physical camera by **DirectShow FriendlyName** via
`USB_CAM_DEVICE_NAME_CONTAINS` (`BisonCam` / `USB CAMERA`), not by USB index. `usb_cam_host.py`
resolves the name through `C:\ffmpeg\bin\ffmpeg.exe` (`-f dshow -list_devices`) and opens the
camera with `CAP_DSHOW`. This is immune to USB-port reshuffles and reboots reordering the
device indices — `dominator-cam` and `usb-cam` can never come back swapped.

`ffmpeg.exe` is a single self-contained binary (~204 MB, BtbN win64-gpl static build) at
`C:\ffmpeg\bin\` — a `_find_ffmpeg()` fixed-candidate path. It is **not committed** (too large);
fetch it on a fast machine and `scp` it over the LAN (the Dominator's own WiFi throttles and a
direct download hits a schannel cert-revocation error):

```bash
# on a fast box:
curl -L -o ffmpeg.zip 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
unzip -j ffmpeg.zip '*/bin/ffmpeg.exe' -d .
scp ffmpeg.exe user@192.168.0.194:'C:/ffmpeg/bin/ffmpeg.exe'
```

If `ffmpeg.exe` is ever missing, the cameras won't open (the name path returns no device). The
fallback is index-binding: drop `USB_CAM_DEVICE_NAME_CONTAINS` from the bats and set
`USB_CAM_DEVICE_INDEX` (0 = built-in, 1 = USB CAMERA) instead.

## How they're started (auto-start on login, survive SSH disconnect)

Each feed runs under its own **scheduled task** with an **AtLogOn trigger**, so both come back
automatically after a reboot/login. The tasks use `LogonType Interactive` (DirectShow needs a
desktop session), `ExecutionTimeLimit 0` (run forever), and auto-restart ×3 on failure. Because
they're Task-Scheduler-owned they also survive the SSH session that launched them disconnecting.

- `dominator-cam-bisoncam` → `start-bisoncam.bat` → port 8089
- `dominator-cam-usbcam`   → `start-usbcam.bat`   → port 8090

`setup-cams.ps1` is the idempotent provisioner: it adds the inbound firewall rules
(`dominator-cam 8089/8090` + a `dominator-cam python` app rule — the Dominator's firewall is ON,
unlike GWTC), runs the ffmpeg name-resolution gate (both cameras must enumerate), registers both
AtLogOn tasks, starts them, and verifies `/photo.jpg` + prints the name-resolution log lines.

```powershell
# from an elevated session on the Dominator (or over SSH as the logged-on user):
powershell -NoProfile -ExecutionPolicy Bypass -File C:\farm-services\dominator-cam\setup-cams.ps1
```

## Start / stop / status

```powershell
Start-ScheduledTask -TaskName dominator-cam-bisoncam      # start the built-in feed (8089)
Start-ScheduledTask -TaskName dominator-cam-usbcam        # start the USB feed (8090)
schtasks /end /tn dominator-cam-usbcam                    # stop one feed (or close its console window)
Get-ScheduledTask -TaskName dominator-cam-* | Format-List TaskName,State
Get-Content C:\farm-services\dominator-cam\usbcam.log -Tail 20   # per-camera logs (bisoncam.log / usbcam.log)
```

## Caveats

- **Auto-start fires at user login** (the `User` account on this box autologs in). If the laptop
  sits at a locked/login screen with nobody logged on, neither task runs until login — that's
  inherent to interactive DirectShow capture.
- **Fn+F6 trap:** if a camera vanishes from Windows entirely, press Fn+F6 on the laptop once
  (hardware webcam kill toggle at the EC level). See `HARDWARE_INVENTORY.md`.
- This box is also Boss's day-to-day Windows machine and hosts Larry's WSL OpenClaw gateway; the
  two camera instances are lightweight Python webcam servers and don't contend with WSL (Windows
  owns the USB devices).
