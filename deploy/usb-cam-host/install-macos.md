# usb-cam-host — macOS install

Install steps for any macOS host that will be the physical home of the generic USB webcam (Mac Mini "Bubba", MacBook Air 2013, or any future macOS box). Canonical plan: `docs/14-Apr-2026-portable-usb-cam-host-plan.md`.

## 1. Repo checkout + venv

The service shares the farm-guardian checkout for simplicity. On the Mac Mini it already exists at `/Users/macmini/Documents/GitHub/farm-guardian` with a working venv — nothing to do.

On the MacBook Air (or any other macOS host):

```bash
cd ~
git clone https://github.com/VoynichLabs/farm-guardian.git
cd farm-guardian
python3 -m venv venv
source venv/bin/activate
pip install -r deploy/usb-cam-host/requirements.txt
```

The service only needs `requirements.txt` from `deploy/usb-cam-host/` — not the full Guardian `requirements.txt`, which pulls YOLO / onvif / aiohttp and is much heavier.

## 2. Verify the camera

```bash
./venv/bin/python - <<'PY'
import cv2
cap = cv2.VideoCapture(0)
print("opened:", cap.isOpened())
ok, f = cap.read()
print("read ok:", ok, "shape:", None if f is None else f.shape)
cap.release()
PY
```

Expected: `opened: True`, `read ok: True`, `shape: (1080, 1920, 3)` (or whatever native resolution the camera reports). The **first** run will trigger a macOS Camera TCC prompt — approve it. If you click "Don't Allow" the grant is recorded as denied; re-enable under `System Settings → Privacy & Security → Camera` and toggle the approval for `Terminal` (or whatever process ran the python).

If `opened: False`: camera unplugged, another process holds it, or TCC is denied. `lsof | grep VDC.plugin` shows any process currently holding the device.

## 3. Smoke-test the service manually

```bash
./venv/bin/python tools/usb-cam-host/usb_cam_host.py
```

Should log `usb-cam-host ready: device=0 requested=1920x1080 warmup=15 ...` and `starting uvicorn on 0.0.0.0:8089`. From another terminal:

```bash
curl -sS http://localhost:8089/health | python3 -m json.tool
curl -sS http://localhost:8089/photo.jpg -o /tmp/u.jpg
file /tmp/u.jpg
```

Expected: health returns `"ok": true` + negotiated resolution. The JPEG is a 1920×1080 (or native) frame. Laplacian variance for a before/after comparison:

```bash
./venv/bin/python - <<'PY'
import cv2
img = cv2.imread("/tmp/u.jpg")
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
print("laplacian_var:", cv2.Laplacian(gray, cv2.CV_64F).var())
PY
```

(This number is a ranking signal — it does not map linearly to GLM's "sharp/soft" verdict, per today's plan doc. Use it to compare against the archive baseline, not as a pass/fail threshold.)

Ctrl-C the service.

## 4. Install the LaunchAgent

```bash
cp deploy/usb-cam-host/com.farmguardian.usb-cam-host.plist ~/Library/LaunchAgents/
# If you're installing on a host where the repo lives somewhere other than
# /Users/macmini/Documents/GitHub/farm-guardian, edit the two
# /Users/macmini/Documents/GitHub/farm-guardian paths in the plist before loading.
launchctl unload ~/Library/LaunchAgents/com.farmguardian.usb-cam-host.plist 2>/dev/null
launchctl load   ~/Library/LaunchAgents/com.farmguardian.usb-cam-host.plist
```

Verify:

```bash
launchctl list | grep usb-cam-host
tail -f /tmp/usb-cam-host.out.log /tmp/usb-cam-host.err.log
curl -sS http://localhost:8089/health | python3 -m json.tool
```

The LaunchAgent runs under the logged-in GUI user (`UserName` is implicit for agents in `~/Library/LaunchAgents/`). This matters because AVFoundation Camera access is a per-user TCC grant. A LaunchDaemon (root, `/Library/LaunchDaemons/`) cannot surface a Camera prompt and cannot inherit a user's Camera approval — do not change the install target.

## 5. Tell Guardian where to pull from

On the Mac Mini (the box that runs Guardian), edit `config.json`. Replace the `usb-cam` block with the HTTP snapshot variant pointed at the host IP you just installed on:

```jsonc
{
  "name": "usb-cam",
  "type": "fixed",
  "source": "snapshot",
  "snapshot_method": "http_url",
  "http_base_url": "http://<host-ip>:8089",
  "http_photo_path": "/photo.jpg",
  "http_trigger_focus": false,
  "snapshot_interval": 5.0,
  "detection_enabled": false
}
```

Then restart Guardian. Also update `tools/pipeline/config.json`'s `usb-cam` block:

```jsonc
"usb-cam": {
  "cycle_seconds": 180,
  "capture_method": "ip_webcam",
  "ip_webcam_base": "http://<host-ip>:8089",
  "context": "Generic USB webcam, host-portable via usb-cam-host service; currently aimed at the brooder, heat-lamp lit",
  "burst_size": 1,
  "enabled": true
}
```

Restart the pipeline orchestrator.

## 6. Moving the camera later

Plug the camera into a different macOS host. Repeat steps 1–4 on that host. Change `http_base_url` / `ip_webcam_base` in the two config files on the Mini to the new host's IP. Restart Guardian and the pipeline. Update `HARDWARE_INVENTORY.md`'s `usb-cam` row (host machine, source URL, and "Currently aimed at").

## Troubleshooting

- **`/health` returns 503 "camera not openable"** — camera unplugged, TCC denied, or another process holds the device. Check `lsof | grep VDC.plugin`.
- **First `/photo.jpg` after a reboot is slow (~2–3 s)** — expected. The webcam HAL cold-starts. Subsequent requests are ~1.1 s.
- **Service dies silently** — `tail /tmp/usb-cam-host.err.log`. Most common cause: OpenCV ImportError because the venv isn't the one the plist references. Edit the `ProgramArguments` paths in the plist, `launchctl unload` + `launchctl load`.
- **"Operation not permitted" from AVFoundation** — Camera TCC denied. `System Settings → Privacy & Security → Camera` — approve whatever process the plist launches (typically `Python`).
