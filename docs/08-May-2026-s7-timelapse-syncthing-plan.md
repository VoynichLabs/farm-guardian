# s7-cam: replace IP Webcam with Open Camera timelapse + Syncthing

**Status:** TODO / FUTURE — not scheduled. Pick up when looking for something to do.
**Date:** 2026-May-08
**Trigger to revisit:** next time IP Webcam wedges on the S7 and Boss has to walk to the coop to unlock the phone and tap "Start server".

## Why

The S7 lives in the chicken coop, plugged into USB power, screen locked. The intent is **candid stills of chickens in the nesting box** using the S7's high-quality back camera — not live streaming. Forcing this through IP Webcam (a live-stream Android app) creates two recurring problems:

1. The IP Webcam HTTP server wedges (TCP port stays open, server returns zero bytes) after Android background-camera service interruptions. Recovery requires unlocking the phone in the coop and tapping "Stop server / Start server" — high-friction.
2. Whenever the S7 is plugged into a host that also runs Android USB-webcam mode (Dominator), the USB-webcam stack steals the camera and IP Webcam can't open it at all. Frames go landscape via the host's relay instead of phone-controlled portrait.

For "candid stills, phone behind chicken-wire, untouchable for weeks at a time", a **scheduled-stills-and-sync** model fits the use case better than a live-stream-and-poll model.

## The plan

### Bubba side

1. `brew install syncthing`.
2. LaunchAgent so Syncthing auto-starts at login. Use the `com.farmguardian.*` label family so it inherits the working TCC grant pattern (`com.farmguardian.syncthing` is fine).
3. Create the receive folder: `~/farm/cameras/s7-stills/`
4. **Write a small FastAPI shim that pretends to be an IP Webcam endpoint.** Pattern already exists in this repo at `tools/usb-cam-host/usb_cam_host.py` — same idea (FastAPI service exposes `GET /photo.jpg` on a known port), just with a different image source. The new service reads the most-recent `.jpg` from the receive folder and returns it. Live on `localhost:8087/photo.jpg` (8089 is taken by `usb-cam-host`). Suggest: `tools/s7-stills-host/s7_stills_host.py`. **This is the keystone of the whole plan** — by impersonating an IP Webcam endpoint, the existing pipeline source path (`HttpUrlSnapshotSource` / `capture_ip_webcam`) keeps working without modification.
5. LaunchAgent for the shim, watchdog'd. Reuse the launchd-relabel discipline from CLAUDE.md (label family `com.farmguardian.*` to inherit TCC grants).
6. **Update BOTH config files (the dual-config gotcha from CLAUDE.md):**
   - `config.json` (Guardian) — change `s7-cam`'s URL.
   - `tools/pipeline/config.json` — change `s7-cam.ip_webcam_base` from `http://192.168.0.249:8080` → `http://localhost:8087`.
   - Verify with: `grep -n 'http_base_url\|ip_webcam_base' config.json tools/pipeline/config.json`
7. Reload BOTH services:
   ```
   launchctl kickstart -k gui/$(id -u)/com.farmguardian.guardian
   launchctl kickstart -k gui/$(id -u)/com.farmguardian.pipeline
   ```
8. **Update `HARDWARE_INVENTORY.md`** to reflect the new s7-cam wiring (source = synced folder, not phone HTTP).

### S7 side (~5 minutes hands-on)

1. Install **Open Camera** (FOSS, Play Store).
2. In Open Camera settings: Photo timer = 30s, Repeat photos = unlimited, "Stop on screen lock" = off, max-resolution JPEG, save to `/sdcard/DCIM/s7-coop/`.
3. Install **Syncthing** — F-Droid build is more stable than the Play Store version.
4. Pair with Bubba; share `/sdcard/DCIM/s7-coop/` as **send-only**.
5. For both apps: Settings → Apps → Battery → **Unrestricted**.
6. Tap "Start" in Open Camera, lock the phone, walk away.

## What this gets you

- Full-sensor stills (the whole point of using the S7 over a cheap IP cam) — no streaming compression, no 1080p cap.
- Phone can stay locked and untouched indefinitely.
- No HTTP server on the phone → nothing to wedge.
- No camera-claim battles with USB-webcam mode → Boss can plug the S7 into anything for power.
- Syncthing is far more battle-tested for unattended file transfer than IP Webcam is for unattended streaming.

## Tradeoffs

- **No live frames-on-demand for the s7-cam slot.** Frames are interval-based (30s cadence, or whatever Open Camera is set to). For nesting-box chickens this is fine — they're not moving fast in there. The other 5 cameras stay on their current live paths.
- **One new moving piece on Bubba** (Syncthing + the shim) — but the shim is small and the IP-Webcam-impersonation trick keeps pipeline code untouched.
- **Open Camera's interval mode hasn't been load-tested in this setup yet.** First time it's deployed, watch it for a week and confirm it doesn't drift or stall. If it does, the same shim strategy works with Termux + `termux-camera-photo` on a cron, which is more script-controllable.

## Out of scope

- Other cameras (mba-cam, gwtc, dominator-cam, usb-cam, house-yard) — leave on their current paths. This is an s7-cam-only change.
- Replacing the S7 with a purpose-built indoor cam — possible, but loses the image-quality advantage that's the entire reason the S7 is in the coop.
