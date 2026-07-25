# Author: Claude Opus 5
# Date: 25-July-2026
# PURPOSE: Rewrite the usb-cam-host env block in
#   C:\farm-services\usb-cam-host\start.bat on GWTC and restart the service, so
#   camera tuning (exposure / WB / highlight / sharpen) can be done empirically
#   over SSH. Pass key=value pairs; each rewrites or adds the matching
#   `set USB_CAM_<KEY>=<VALUE>` line. Originally written 23-Jul-2026.
#
#   CHANGE 25-Jul-2026 — restart correctness. The previous version called
#   Stop-ScheduledTask and killed `python` filtered on `$_.Path -like
#   '*usb-cam-host*'`. Neither reliably kills the **cmd.exe running start.bat**,
#   and that batch sets the env ONCE at the top and then `:loop`s forever
#   respawning python. So the old env survived: after editing start.bat, /health
#   still reported the previous values and the tuning silently did nothing. This
#   version kills the cmd.exe batch host by matching its CommandLine, then any
#   usb_cam_host python, then restarts the task, then VERIFIES against /health.
#   (tune-usbcam.ps1 warned about this in a comment; the warning was not enough,
#   so the fix is in the tool.)
#
# SRP/DRY check: Pass — one job (rewrite env + restart cleanly). This is the
#   single supported way to change camera settings on GWTC; do not hand-edit
#   start.bat and do not write another restart helper. restart-usbcam.ps1 stays
#   separate on purpose: it recovers a black-frame grabber WITHOUT changing env,
#   and is the right tool when settings are already correct.
#
# DEPLOY: this repo copy is source of truth.
#   scp deploy/gwtc/set-cam.ps1 markb@192.168.0.69:C:/farm-services/set-cam.ps1
#
# USAGE: powershell -ExecutionPolicy Bypass -File C:\farm-services\set-cam.ps1 `
#          AUTO_EXPOSURE=manual EXPOSURE=-6
#
# CONSTRAINT: Windows PowerShell 5.1 only.

param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Pairs)

$ErrorActionPreference = 'Stop'
$bat = 'C:\farm-services\usb-cam-host\start.bat'
if (-not (Test-Path "$bat.orig-20260723")) { Copy-Item $bat "$bat.orig-20260723" }

# --- rewrite / insert the requested env lines -----------------------------
$lines = Get-Content $bat
foreach ($pair in $Pairs) {
    $key, $value = $pair -split '=', 2
    $envKey = "USB_CAM_$key"
    $setLine = "set $envKey=$value"
    if ($lines -match "^set $envKey=") {
        $lines = $lines -replace "^set $envKey=.*$", $setLine
    } else {
        # Insert after the last existing USB_CAM set line so everything stays
        # above the :loop label — below it, the assignment would never run.
        $idx = ($lines | Select-String '^set USB_CAM_' | Select-Object -Last 1).LineNumber
        $lines = $lines[0..($idx - 1)] + $setLine + $lines[$idx..($lines.Count - 1)]
    }
}
Set-Content $bat $lines -Encoding ASCII

'--- start.bat env now ---'
Select-String -Path $bat -Pattern '^set USB_CAM_' | ForEach-Object { $_.Line.Trim() }

# --- full restart ---------------------------------------------------------
# Order matters: stop the task first so it cannot respawn mid-kill, then kill
# the batch host (the thing actually holding the stale environment), then any
# surviving python. Matching on CommandLine rather than Path is deliberate —
# the python executable lives in a venv whose path does not always contain the
# service name, which is why the old Path filter missed it.
Stop-ScheduledTask -TaskName 'usb-cam-host' -ErrorAction SilentlyContinue

Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*usb-cam-host*' } |
    ForEach-Object {
        "killing batch host cmd.exe PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*usb_cam_host*' } |
    ForEach-Object {
        "killing python PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Start-Sleep -Seconds 4
Start-ScheduledTask -TaskName 'usb-cam-host'

# --- verify against the SERVICE, not the file -----------------------------
# start.bat contents prove nothing; only /health proves what the running
# process actually loaded. Warmup is 15 frames, so give it room.
$port = 8089
$deadline = (Get-Date).AddSeconds(45)
$health = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 5
        if ($health.ok) { break }
    } catch { }
}

if ($null -eq $health) {
    'WARNING: service did not answer /health within 45s -- check service.log'
} else {
    '--- live settings per /health ---'
    $health | Select-Object auto_wb, wb_strength, auto_exposure, exposure,
                            highlight_knee, highlight_strength,
                            sharpen_amount, grabber_alive, camera_open |
        Format-List | Out-String
}
