@echo off
REM usb-cam = external USB CAMERA (VID 32E6 PID 9221) on the MSI Dominator, bound by DirectShow
REM FriendlyName. Serves http://192.168.0.194:8090/photo.jpg. Name-binding (replug/reboot-proof)
REM needs ffmpeg at C:\ffmpeg\bin\ffmpeg.exe for the dshow enumeration; usb_cam_host.py then opens
REM the matching camera via CAP_DSHOW. Started by the dominator-cam-usbcam scheduled task (ONLOGON
REM trigger + run-now). Close window / end task to stop. Log: usbcam.log.
setlocal enableextensions
set PYTHON_EXE=C:\farm-services\dominator-cam\venv\Scripts\python.exe
set SCRIPT=C:\farm-services\dominator-cam\usb_cam_host.py
set USB_CAM_PORT=8090
set USB_CAM_DEVICE_NAME_CONTAINS=USB CAMERA
set USB_CAM_DEVICE_INDEX=1
set USB_CAM_WIDTH=1920
set USB_CAM_HEIGHT=1080
set USB_CAM_WARMUP=15
set USB_CAM_JPEG_QUALITY=95
set USB_CAM_AUTO_WB=true
set USB_CAM_WB_STRENGTH=0.5
title usb-cam (USB CAMERA on Dominator) 8090 (close window to stop)
"%PYTHON_EXE%" "%SCRIPT%" > C:\farm-services\dominator-cam\usbcam.log 2>&1
