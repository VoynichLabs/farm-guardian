# 06-May-2026 — Add `dominator-cam` (MSI Dominator GT72) as a manually-started camera

**Author:** Claude Opus 4.7 (1M context)
**Status:** Done — service deployed, Guardian roster updated, end-to-end verified.

## Scope

Add the MSI Dominator GT72 6QD laptop (Larry's box, `192.168.0.194`) as a sixth camera in the Farm Guardian roster, using the existing `usb-cam-host` HTTP snapshot pattern.

**In:** Deploy `usb-cam-host` to `C:\farm-services\dominator-cam\`, drop a desktop shortcut, register `dominator-cam` in both Guardian configs, restart services, update `HARDWARE_INVENTORY.md` + `farm-2026/lib/cameras.ts`.

**Out:** Auto-start on boot. Boss uses this laptop for daily work and Larry's WSL OpenClaw gateway lives there too — a permanently-running camera service is not appropriate. The camera is **opportunistic**: only live when Boss double-clicks the desktop shortcut. Closing the cmd window stops it. This is the same posture as the retired `iphone-cam`.

## Architecture

Reuse without modification:
- `tools/usb-cam-host/usb_cam_host.py` — FastAPI snapshot service (already cross-platform via OpenCV)
- `capture.HttpUrlSnapshotSource` — Guardian's HTTP snapshot adapter (used by every snapshot camera)
- `tools/pipeline/capture_ip_webcam` — pipeline-side capture path
- `scripts/add-camera.py` — atomic add to both `config.json` and `tools/pipeline/config.json`

New artifact:
- `C:\farm-services\dominator-cam\start.bat` — venv-rooted launcher; no `:loop` retry (closing the window means stop), env vars hardcoded for this host (port 8089, device index 0). Sourced from `deploy/usb-cam-host/start-usb-cam-host.bat` with edits.

## TODOs (all done)

1. Probe Dominator hardware/Python availability over SSH. ✓
2. Resolve "no camera detected" — turned out to be **Fn+F6 webcam toggle** (MSI SCM service handles it, kills USB power to BisonCam at the EC level, makes the cam invisible to Windows entirely). Boss pressed Fn+F6 to re-enable. ✓
3. Create `C:\farm-services\dominator-cam\` and venv at `D:\python\python.exe -m venv ...`. ✓
4. `pip install fastapi uvicorn opencv-python numpy requests` into the venv. ✓
5. `scp` `usb_cam_host.py` to the box. ✓
6. Probe device index — BisonCam NB Pro at idx 0, opens 1920×1080 cleanly. ✓
7. Write Dominator-specific `start.bat` (no `:loop`, friendly close-window-to-stop messaging). ✓
8. Smoke-test from Mini: `/health` shows `camera_open: true`, `/photo.jpg` returns a valid 1920×1080 JPEG (~585 KB). ✓
9. Stop service, create desktop shortcut at `C:\Users\User\OneDrive\Desktop\dominator-cam.lnk`. ✓
10. `scripts/add-camera.py add dominator-cam --url http://192.168.0.194:8089/photo.jpg --interval 10 --no-probe --context "..."`. ✓ (used `--no-probe` because the service is intentionally not running by default)
11. Restart Guardian + pipeline LaunchAgents. ✓
12. Verify `/api/cameras` includes `dominator-cam`. ✓ (online: true, capturing: true, is_live: false until Boss starts the service)
13. Update `HARDWARE_INVENTORY.md` — new camera row, new host row, "Last verified" stamp bumped to 2026-05-06. ✓
14. Update `farm-2026/lib/cameras.ts` — append `dominator-cam` overlay entry. ✓
15. Save Dominator hardware specs to `~/bubba-workspace/memory/reference/msi-dominator-specs.md`. ✓
16. Save the Fn+F6 trap as a Bubba auto-memory so the next agent doesn't burn 30 minutes re-probing. ✓
17. CHANGELOG entry, commit, push. ✓

## Hardware

- **Model:** MSI Dominator GT72 6QD (Skylake-era 2016 gaming laptop)
- **Hostname:** `Mark-MSI-Laptop`
- **OS:** Windows 10 Home, build 19045
- **CPU:** Intel Core i7-6700HQ (4C/8T)
- **RAM:** 64 GB
- **GPU:** NVIDIA GeForce GTX 970M (driver 32.0.15.7283)
- **Webcam:** BisonCam NB Pro (built-in, 1920×1080 max, USB-internal)
- **Network:** WiFi, `192.168.0.194` (DHCP), MAC `9C:B6:D0:06:AF:2F`
- **Python:** 3.13.3 at `D:\python\python.exe` (Boss's choice — keeps Python on the data drive)
- **Other tenants:** Boss's day-to-day Windows desktop (Visual Studio, etc.); Larry's WSL Ubuntu OpenClaw gateway under `/home/user/.openclaw/`. Webcam service does not contend with WSL — Windows owns USB devices.

## Operational notes

- **Start the camera:** double-click the `dominator-cam` shortcut on the Dominator's desktop. A cmd window opens; while it's open, Guardian sees the camera live within ~30s.
- **Stop the camera:** close the cmd window. Guardian flips `is_live: false` within ~30s on the next stale-frame check.
- **Camera disappears from Windows entirely:** Fn+F6 was probably pressed. Press it again. (See `feedback_msi_fnf6_webcam_toggle.md` in Bubba auto-memory for the full diagnostic trap.)
- **DHCP drift:** the Dominator currently has `.194`. If it drifts after a router reboot, find it on the LAN by sweeping for port `8089` while the service is running, or check the TP-Link DHCP client list. Boss has not promised to keep it on `.194`.

## What this plan deliberately doesn't do

- No Shawl service, no scheduled task, no auto-start. The camera is fully opt-in.
- No watchdog. Boss is sitting at the keyboard when the camera is in use; he'll notice if it dies.
- No detection enabled. This camera is for content/reactions/portraits when Boss aims it, not predator surveillance.
- No farm-2026 MDX update for the projects/guardian camera count — that file fetches the roster from Guardian at runtime per the dynamic-roster rule.
