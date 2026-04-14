@echo off
REM  usb-cam-host startup script for Windows (dshow backend via OpenCV).
REM
REM  Mirrors the GWTC pattern in deploy\gwtc\start-camera.bat: launched by a
REM  Shawl service so it survives reboots, logs stdout/stderr to C:\farm-services\logs\.
REM
REM  Tailor per-host:
REM    - PYTHON_EXE: path to the venv's python.exe on that box
REM    - REPO: path to the farm-guardian checkout
REM    - USB_CAM_DEVICE_INDEX: 0 unless the host has multiple cameras
REM
REM  Camera TCC equivalent on Windows: none — Windows does not block console-run
REM  processes from the webcam. But note that Windows 10/11 Settings > Privacy >
REM  Camera has a per-app toggle; if "Allow desktop apps to access your camera"
REM  is Off, OpenCV opens will fail. Flip it On once per host.

setlocal enableextensions

set PYTHON_EXE=C:\Users\markb\farm-guardian-venv\Scripts\python.exe
set REPO=C:\Users\markb\farm-guardian

set USB_CAM_PORT=8089
set USB_CAM_DEVICE_INDEX=0
set USB_CAM_WIDTH=1920
set USB_CAM_HEIGHT=1080
set USB_CAM_WARMUP=15
set USB_CAM_JPEG_QUALITY=95
set USB_CAM_AUTO_WB=true
set USB_CAM_WB_STRENGTH=0.5

:loop
"%PYTHON_EXE%" "%REPO%\tools\usb-cam-host\usb_cam_host.py"
echo usb-cam-host exited with code %ERRORLEVEL%, restarting in 5s
timeout /t 5 /nobreak >nul
goto loop
