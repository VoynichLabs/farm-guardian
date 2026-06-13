@echo off
REM usb-cam = external USB CAMERA (VID 32E6 PID 9221) on the MSI Dominator (OpenCV device_index 1).
REM Serves http://192.168.0.194:8090/photo.jpg while this scheduled task / window runs.
REM NOTE: bound by OpenCV device index (Windows default MSMF backend). To make it
REM       name-bound and replug-proof instead, drop ffmpeg.exe at C:\ffmpeg\bin\ and add:
REM           set USB_CAM_DEVICE_NAME_CONTAINS=USB CAMERA
REM       (usb_cam_host.py then resolves the DirectShow FriendlyName and opens via CAP_DSHOW).
REM Started by the "dominator-cam-usbcam" scheduled task. Close window / end task to stop.
setlocal enableextensions
set PYTHON_EXE=C:\farm-services\dominator-cam\venv\Scripts\python.exe
set SCRIPT=C:\farm-services\dominator-cam\usb_cam_host.py
set USB_CAM_PORT=8090
set USB_CAM_DEVICE_INDEX=1
set USB_CAM_WIDTH=1920
set USB_CAM_HEIGHT=1080
set USB_CAM_WARMUP=15
set USB_CAM_JPEG_QUALITY=95
set USB_CAM_AUTO_WB=true
set USB_CAM_WB_STRENGTH=0.5
title usb-cam (USB CAMERA on Dominator) 8090 (close window to stop)
echo Starting usb-cam (USB CAMERA, device 1) on port 8090 - close window to stop.
"%PYTHON_EXE%" "%SCRIPT%"
echo.
echo usb-cam stopped.
pause
