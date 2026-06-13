@echo off
REM dominator-cam = MSI Dominator built-in BisonCam NB Pro, bound by DirectShow FriendlyName.
REM Serves http://192.168.0.194:8089/photo.jpg. Name-binding (replug/reboot-proof) needs
REM ffmpeg at C:\ffmpeg\bin\ffmpeg.exe for the dshow device enumeration; usb_cam_host.py then
REM opens the matching camera via CAP_DSHOW. Started by the dominator-cam-bisoncam scheduled
REM task (ONLOGON trigger + run-now). Close window / end task to stop. Log: bisoncam.log.
setlocal enableextensions
set PYTHON_EXE=C:\farm-services\dominator-cam\venv\Scripts\python.exe
set SCRIPT=C:\farm-services\dominator-cam\usb_cam_host.py
set USB_CAM_PORT=8089
set USB_CAM_DEVICE_NAME_CONTAINS=BisonCam
set USB_CAM_DEVICE_INDEX=0
set USB_CAM_WIDTH=1920
set USB_CAM_HEIGHT=1080
set USB_CAM_WARMUP=15
set USB_CAM_JPEG_QUALITY=95
set USB_CAM_AUTO_WB=true
set USB_CAM_WB_STRENGTH=0.5
title dominator-cam BisonCam 8089 (close window to stop)
"%PYTHON_EXE%" "%SCRIPT%" > C:\farm-services\dominator-cam\bisoncam.log 2>&1
