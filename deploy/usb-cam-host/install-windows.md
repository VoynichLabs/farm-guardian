# usb-cam-host — Windows install

Install steps for any Windows host (Gateway laptop "GWTC", or another Windows box) that becomes the physical home of the generic USB webcam. Canonical plan: `docs/14-Apr-2026-portable-usb-cam-host-plan.md`.

**Not validated yet.** As of v2.26.0 the camera is on the Mac Mini and this path has not been exercised end-to-end. The first person to run it should treat the service's logs as the source of truth and update this doc with any deltas.

## 1. Prerequisites

- Python 3.11 installed to `C:\Python311\` or equivalent (match what GWTC already has for MediaMTX / ffmpeg; Boss's convention is `C:\Users\markb\...` for farm services).
- The farm-guardian repo cloned under the logged-in user's home: `C:\Users\markb\farm-guardian`.
- A venv: `python -m venv C:\Users\markb\farm-guardian-venv` and `C:\Users\markb\farm-guardian-venv\Scripts\pip install -r deploy\usb-cam-host\requirements.txt`.
- **Windows Settings → Privacy → Camera → "Allow desktop apps to access your camera"** is On. (On GWTC, Windows Firewall is already disabled per `network.md`, so no firewall rule is needed. On any other Windows box, add an inbound rule for TCP `8089`.)

## 2. Verify the camera with OpenCV

Open `cmd` in the repo:

```bat
C:\Users\markb\farm-guardian-venv\Scripts\python -c "import cv2; cap=cv2.VideoCapture(0); print('open:', cap.isOpened()); ok, f = cap.read(); print('ok:', ok, 'shape:', None if f is None else f.shape); cap.release()"
```

Expected: `open: True`, `ok: True`, `shape: (1080, 1920, 3)`. OpenCV uses dshow by default on Windows.

Edge case: on some dshow-backed cameras the first `cap.read()` after open returns a blank frame — the warmup loop in the service absorbs that, but the one-shot probe above might see it. If the shape is `None` or the frame is solid black, run the same snippet twice in a row; the second run is authoritative.

## 3. Smoke-test the service manually

```bat
C:\Users\markb\farm-guardian-venv\Scripts\python C:\Users\markb\farm-guardian\tools\usb-cam-host\usb_cam_host.py
```

From another `cmd`:

```bat
curl http://localhost:8089/health
curl http://localhost:8089/photo.jpg -o C:\temp\u.jpg
```

Expected: health returns `"ok": true`, the JPEG is 1080p and viewable. Ctrl-C the service.

## 4. Install the Shawl service

Same pattern as the GWTC camera + watchdog services in `deploy/gwtc/`. From an elevated `cmd`:

```bat
REM Place the startup script somewhere Shawl can launch it
mkdir C:\farm-services
copy C:\Users\markb\farm-guardian\deploy\usb-cam-host\start-usb-cam-host.bat C:\farm-services\

REM Wrap it with Shawl
C:\shawl\shawl install --name usb-cam-host --cwd C:\farm-services --log C:\farm-services\logs\usb-cam-host.log --restart -- C:\farm-services\start-usb-cam-host.bat

REM Start it + set auto-start
sc start usb-cam-host
sc config usb-cam-host start= auto
```

Verify:

```bat
sc query usb-cam-host
curl http://localhost:8089/health
```

## 5. Tell Guardian where to pull from

Same edits as the macOS install doc step 5: `config.json` and `tools/pipeline/config.json` on the Mac Mini. `http_base_url` / `ip_webcam_base` point at the Windows host's LAN IP on port `8089`.

For GWTC specifically, remember its IP drifts on DHCP — find it by sweeping for port `8554` (MediaMTX) or `9099` (LM Studio), not by a hardcoded `.68` (see `CLAUDE.md` §Network). If both `gwtc` (MediaMTX RTSP) and `usb-cam` (HTTP snapshot) land on the same Windows box, the IP in both Guardian config entries is the same host — the difference is only the port.

## 6. Moving the camera off this host

Stop and uninstall the service:

```bat
sc stop usb-cam-host
C:\shawl\shawl uninstall --name usb-cam-host
```

The Guardian side doesn't need to know — it'll just start getting 503s from the now-dead URL. Flip `http_base_url` and `ip_webcam_base` on the Mini to the new host, restart Guardian and the pipeline.

## Troubleshooting

- **`sc query usb-cam-host` shows RUNNING but `curl http://localhost:8089/health` fails** — the `.bat`'s `:loop` is retrying the Python launch but Python is exiting immediately. Check `C:\farm-services\logs\usb-cam-host.log`. Most likely: wrong `PYTHON_EXE` path in the `.bat`, or the venv is missing deps.
- **`cv2.VideoCapture(0)` returns False at runtime but True in the smoke test** — Windows Camera privacy toggle is Off, or another app (Zoom, Teams, Skype) captured the device. Kill the other app or let Boss know.
- **403 / connection refused from the Mini** — Windows Firewall, or the service is bound to 127.0.0.1 only. The service defaults to `0.0.0.0`; if you've overridden `USB_CAM_HOST` to `127.0.0.1`, unset it.
