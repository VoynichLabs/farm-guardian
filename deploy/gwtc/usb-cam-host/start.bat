@echo off
setlocal enableextensions
set USB_CAM_PORT=8089
set USB_CAM_DEVICE_INDEX=1
set USB_CAM_DEVICE_NAME_CONTAINS=USB CAMERA
set USB_CAM_WIDTH=1920
set USB_CAM_HEIGHT=1080
set USB_CAM_WARMUP=15
set USB_CAM_JPEG_QUALITY=95
set USB_CAM_AUTO_WB=false
set USB_CAM_WB_STRENGTH=0
:loop
"C:\farm-services\usb-cam-host\venv\Scripts\python.exe" C:\farm-services\usb-cam-host\usb_cam_host.py >> C:\farm-services\usb-cam-host\service.log 2>&1
echo [%DATE% %TIME%] usb-cam-host exited code %ERRORLEVEL%, restart in 5s >> C:\farm-services\usb-cam-host\service.log
timeout /t 5 /nobreak >/dev/null
goto loop
