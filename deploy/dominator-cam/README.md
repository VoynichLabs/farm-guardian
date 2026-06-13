# dominator-cam + usb-cam on the MSI Dominator GT72 (`192.168.0.194`)

As of **2026-06-12** the MSI Dominator hosts **two** Guardian camera feeds at once, because
the portable `usb-cam` was physically plugged into it (alongside the laptop's built-in webcam):

| Guardian camera | Physical device | OpenCV index | Port | Snapshot URL |
|---|---|---|---|---|
| `dominator-cam` | built-in **BisonCam NB Pro** (VID 5986) | 0 | 8089 | `http://192.168.0.194:8089/photo.jpg` |
| `usb-cam`       | external **USB CAMERA** (VID 32E6 PID 9221) | 1 | 8090 | `http://192.168.0.194:8090/photo.jpg` |

Both are the same `usb_cam_host.py` FastAPI service (`C:\farm-services\dominator-cam\`), run as
**two instances** on two ports, each pinned to one camera via `USB_CAM_DEVICE_INDEX`.

## How they're started (and why they survive an SSH disconnect)

Each feed runs under its own **interactive scheduled task** created with `schtasks /IT` so it
runs in the logged-on desktop session (DirectShow capture needs that) but is owned by the Task
Scheduler — so it keeps running after the SSH session that launched it disconnects. The earlier
manual-shortcut/foreground approach died the moment its launching session closed.

- `dominator-cam-bisoncam` → `start-bisoncam.bat` → port 8089
- `dominator-cam-usbcam`   → `start-usbcam.bat`   → port 8090

`setup-cams.ps1` is idempotent: it adds the inbound Windows Firewall allow rules
(`dominator-cam 8089`, `dominator-cam 8090`, plus a `dominator-cam python` app rule — the
Dominator's firewall is ON, unlike GWTC), creates both tasks with a past one-time trigger
(so they never auto-re-fire), starts them, and verifies both `/photo.jpg` locally.

```powershell
# from an elevated session on the Dominator (or over SSH as the logged-on user):
powershell -NoProfile -ExecutionPolicy Bypass -File C:\farm-services\dominator-cam\setup-cams.ps1
```

## Start / stop / status

```powershell
schtasks /run /tn dominator-cam-bisoncam      # start the built-in feed (8089)
schtasks /run /tn dominator-cam-usbcam        # start the USB feed (8090)
schtasks /end /tn dominator-cam-usbcam        # stop one feed (or just close its console window)
schtasks /query /tn dominator-cam-* /v /fo list
```

## Posture & caveats

- **No boot/logon trigger on purpose.** This is Larry's daily-driver and hosts his WSL OpenClaw
  gateway; a permanently-running camera service isn't appropriate. The tasks survive an SSH
  disconnect but **do NOT survive a reboot or logoff** — re-run the two `schtasks /run` lines (or
  `setup-cams.ps1`) after a reboot. To make them permanent, add a logon trigger to each task.
- **Device labels are bound by index, not name.** The script supports name-binding
  (`USB_CAM_DEVICE_NAME_CONTAINS=BisonCam` / `USB CAMERA`), which is replug-proof, but it needs
  `ffmpeg.exe` on the box (`C:\ffmpeg\bin\`) for DirectShow enumeration. ffmpeg isn't installed
  here yet (the 104 MB download was too slow over the Dominator's WiFi on 2026-06-12). Until then,
  if the two feeds ever look swapped, swap the `USB_CAM_DEVICE_INDEX` values (0↔1) in the two bats.
  The index→device assumption (0 = built-in, 1 = USB CAMERA) matches the same pattern seen on GWTC.
- **Fn+F6 trap:** if a camera vanishes from Windows entirely, press Fn+F6 on the laptop once
  (hardware webcam kill toggle at the EC level). See `HARDWARE_INVENTORY.md`.
