# Restart the GWTC usb-cam-host to reopen a UVC camera that is delivering
# pure-black frames (device handle alive, sensor feed dead). 23-Jul-2026.
$ErrorActionPreference = 'SilentlyContinue'
Stop-ScheduledTask -TaskName 'usb-cam-host'
Get-Process python | Where-Object { $_.Path -like '*usb-cam-host*' } | Stop-Process -Force
Start-Sleep -Seconds 3
Start-ScheduledTask -TaskName 'usb-cam-host'
'restarted'
