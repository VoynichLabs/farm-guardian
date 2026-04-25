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

## 4 (alternate). Scheduled-task install path — when Shawl isn't already on the box

**Validated on GWTC 24-Apr-2026.** This is the path you actually want if `C:\shawl\shawl.exe` isn't pre-installed. Setting up Shawl is fine but takes another 5+ minutes; `schtasks` is in the box. The `start-usb-cam-host.bat` already has a `:loop` that restarts Python on exit, so we don't need Shawl's `--restart` semantics — the scheduled task just needs to launch the bat once and the bat handles its own respawn.

**Critical gotcha that cost ~10 minutes the first time and is the entire reason this section exists:** the task **must NOT be `/ru SYSTEM`**. SYSTEM is in Windows session 0, which is isolated from camera devices on Windows 11 — `cv2.VideoCapture(<idx>)` will return True but `read()` returns `MF_E_NOTACCEPTING`, the service spins forever opening the camera. Run as the actual GUI-logged-in user (`cam` on GWTC, autologon) interactively. **The `/it` flag bypasses the password requirement when the target user matches the current console session**, so no password is needed for an autologon box.

**The other gotcha:** if you install Python deps with `pip install --user`, they go into the SSH user's profile (`%APPDATA%\Python\…`) and are not visible to the scheduled task running as a different user. Either use a venv at `C:\farm-services\usb-cam-host\venv\` (recommended — readable by any user) or `pip install` system-wide from an elevated shell. The scheduled-task batch file should point at the venv's `python.exe`, not the system one.

```bat
REM 1. Stage the script + create a venv readable from the cam user's session
mkdir C:\farm-services\usb-cam-host
copy C:\Users\markb\farm-guardian\tools\usb-cam-host\usb_cam_host.py C:\farm-services\usb-cam-host\
"C:\Program Files\Python311\python.exe" -m venv C:\farm-services\usb-cam-host\venv
C:\farm-services\usb-cam-host\venv\Scripts\python.exe -m pip install fastapi uvicorn opencv-python numpy requests

REM 2. Drop a start.bat that uses the venv python and tunes USB_CAM_DEVICE_INDEX
REM    for whichever index OpenCV gives the external UVC camera (NOT the built-in
REM    if MediaMTX or another tenant already holds it). On GWTC it's index 1.
copy C:\Users\markb\farm-guardian\deploy\usb-cam-host\start-usb-cam-host.bat C:\farm-services\usb-cam-host\start.bat
REM Edit C:\farm-services\usb-cam-host\start.bat:
REM   - point PYTHON_EXE at C:\farm-services\usb-cam-host\venv\Scripts\python.exe
REM   - set USB_CAM_DEVICE_INDEX=1 (GWTC: built-in Hy-HD-Camera is 0, USB cam is 1)
REM   - leave USB_CAM_AUTO_WB=true and WB_STRENGTH=0.5 (defaults are fine outdoors)

REM 3. Create the task running as the autologon user, interactively
schtasks /create /f /tn usb-cam-host ^
  /sc onstart /ru "<PCNAME>\<autologon-user>" /it /rl HIGHEST ^
  /tr "cmd /c C:\farm-services\usb-cam-host\start.bat"

REM 4. Run it now
schtasks /run /tn usb-cam-host
```

Replace `<PCNAME>` (use `hostname` to find — on GWTC it's `653Pudding`) and `<autologon-user>` (`cam` on GWTC). After this, the task auto-runs at every boot in the autologon user's GUI session. The `:loop` inside the batch file handles transient camera failures.

**Verify** (from any LAN host):

```bash
curl -sS --max-time 4 http://<gwtc-ip>:8089/health
```

Expected: `"camera_open": true`, `total_grabs` > 0 within 30s of task start. If `camera_open: false` for more than ~30s, tail `C:\farm-services\usb-cam-host\service.log` and check the OpenCV error — usually `device_index` is wrong (try other indices) or another app is holding the camera.

**Find the correct device index quickly:** run this Python one-liner from any user's ssh:

```bat
"C:\farm-services\usb-cam-host\venv\Scripts\python.exe" -c "import cv2, time
for i in range(4):
    c = cv2.VideoCapture(i); c.set(3,1920); c.set(4,1080); time.sleep(0.5)
    ok, f = c.read()
    print(f'idx={i}: opened={c.isOpened()} read_ok={ok} shape={None if f is None else f.shape}')
    c.release(); time.sleep(0.3)"
```

The index that returns `read_ok=True` with a 1920×1080 shape AND isn't the built-in is your USB cam. On GWTC the built-in `read()` returns False with MSMF error `-1072875772` (because MediaMTX holds it), the USB cam reads at 1920×1080 on idx=1.

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
