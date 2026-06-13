@echo off
REM dominator-cam = MSI Dominator built-in BisonCam NB Pro (OpenCV device_index 0).
REM Serves http://192.168.0.194:8089/photo.jpg while this scheduled task / window runs.
REM NOTE: bound by OpenCV device index (Windows default MSMF backend). To make it
REM       name-bound and replug-proof instead, drop ffmpeg.exe at C:\ffmpeg\bin\ and add:
REM           set USB_CAM_DEVICE_NAME_CONTAINS=BisonCam
REM       (usb_cam_host.py then resolves the DirectShow FriendlyName and opens via CAP_DSHOW).
REM Started by the "dominator-cam-bisoncam" scheduled task. Close window / end task to stop.
setlocal enableextensions
set PYTHON_EXE=C:\farm-services\dominator-cam\venv\Scripts\python.exe
set SCRIPT=C:\farm-services\dominator-cam\usb_cam_host.py
set USB_CAM_PORT=8089
set USB_CAM_DEVICE_INDEX=0
set USB_CAM_WIDTH=1920
set USB_CAM_HEIGHT=1080
set USB_CAM_WARMUP=15
set USB_CAM_JPEG_QUALITY=95
set USB_CAM_AUTO_WB=true
set USB_CAM_WB_STRENGTH=0.5
title dominator-cam BisonCam 8089 (close window to stop)
echo Starting dominator-cam (BisonCam, device 0) on port 8089 - close window to stop.
"%PYTHON_EXE%" "%SCRIPT%"
echo.
echo dominator-cam stopped.
pause
