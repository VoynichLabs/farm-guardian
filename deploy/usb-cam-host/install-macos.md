# usb-cam-host — macOS install

Install steps for any macOS host that will be the physical home of the generic USB webcam (Mac Mini "Bubba", MacBook Air 2013, or any future macOS box). Canonical plan: `docs/14-Apr-2026-portable-usb-cam-host-plan.md`.

## 1. Runtime location — NOT under `~/Documents/`

**Big Sur and later sandbox `~/Documents/`, `~/Desktop/`, and `~/Downloads/` against LaunchAgent access.** If the service lives under any of those paths, the agent boots into a `PermissionError: [Errno 1] Operation not permitted: .../pyvenv.cfg` loop. Install the service runtime outside those directories:

```bash
mkdir -p ~/.local/farm-services/usb-cam-host
# Copy just the two files the service needs — skip cloning all of farm-guardian
# on the target host unless you also need the rest of the repo there.
scp <mini>:/Users/macmini/Documents/GitHub/farm-guardian/tools/usb-cam-host/usb_cam_host.py \
    ~/.local/farm-services/usb-cam-host/

python3 -m venv ~/.local/farm-services/usb-cam-host/venv
source ~/.local/farm-services/usb-cam-host/venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --only-binary=:all: -r <path-to>/deploy/usb-cam-host/requirements.txt
```

**`--only-binary=:all:` is important on older macOS.** Without it, pip will try to build `opencv-python-headless` from source if the exact wheel isn't available — a half-hour cmake job on a 2013 MacBook Air. `requirements.txt` pins `opencv-python-headless==4.8.1.78` specifically because that version has a prebuilt wheel for macOS 11 + Intel + Python 3.8 (the MBA 2013 ceiling).

Mac Mini note: the Mini runs Python 3.13 from Homebrew and can use the farm-guardian checkout's existing venv directly — `~/Documents/GitHub/farm-guardian/venv` is fine on Sequoia because Full Disk Access is already granted to the Guardian agent's Python. The `~/.local/farm-services/` layout above is only needed on Big Sur hosts and any host where you haven't yet granted the Python binary Full Disk Access.

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
- **OpenCV logs `not authorized to capture video (status 0), requesting... can not spin main run loop from other thread`** — Camera TCC is denied for the service's Python *and* OpenCV can't surface the prompt from its worker thread. Fix: ensure `OPENCV_AVFOUNDATION_SKIP_AUTH=1` is set in the plist's `EnvironmentVariables` (it is by default in the repo plist), then grant Camera access to the Python binary manually via **System Settings → Privacy & Security → Camera** — toggle ON the `Python` / `Python.app` entry, or `+` → `/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.8/Resources/Python.app` on Big Sur (`3.13` on Sequoia via Homebrew). Service recovers within ~5 s of the toggle; no restart needed.
- **`PermissionError: pyvenv.cfg Operation not permitted`** — service was installed under `~/Documents/` (or `~/Desktop`, `~/Downloads`) which Big Sur+ sandboxes. Move to `~/.local/farm-services/usb-cam-host/` (see §1).
- **pip install sits on "Building wheel for opencv-python" for 30+ minutes** — you forgot `--only-binary=:all:` and pip is compiling from source. Kill pip, re-run with the flag. The `requirements.txt` in this repo is pinned to a version with a Big Sur Intel Python 3.8 wheel, but the flag is still needed to prevent any fallback to sdist.
- **First `/photo.jpg` after a reboot is slow (~2–3 s)** — expected. The webcam HAL cold-starts. Subsequent requests are ~1.1 s.
- **Service dies silently** — `tail /tmp/usb-cam-host.err.log`. Most common cause: OpenCV ImportError because the venv isn't the one the plist references. Edit the `ProgramArguments` paths in the plist, `launchctl unload` + `launchctl load`.
- **"Operation not permitted" from AVFoundation** — Camera TCC denied. `System Settings → Privacy & Security → Camera` — approve whatever process the plist launches (typically `Python`).
