# Author: Claude Opus 4.8 (Bubba)
# Date: 12-June-2026
# PURPOSE: Canonical setup for the two Dominator camera feeds. Ensures inbound firewall rules,
#          verifies ffmpeg-based DirectShow name resolution (replug/reboot-proof binding), then
#          registers BOTH feeds as scheduled tasks with an AtLogOn trigger (auto-start after
#          reboot/login) + interactive logon (DirectShow needs a desktop session) + unlimited
#          runtime + auto-restart on failure, and starts them now. Idempotent.
#            dominator-cam (BisonCam) -> :8089 (USB_CAM_DEVICE_NAME_CONTAINS=BisonCam)
#            usb-cam (USB CAMERA)     -> :8090 (USB_CAM_DEVICE_NAME_CONTAINS=USB CAMERA)
# SRP/DRY check: Pass - provisioning only; reuses usb_cam_host.py + start-*.bat.
$ErrorActionPreference = 'Stop'

# --- 1. Firewall: inbound allow for 8089 + 8090 + venv python.exe (idempotent) ---
$pyExe = 'C:\farm-services\dominator-cam\venv\Scripts\python.exe'
function Ensure-PortRule($port) {
    $name = "dominator-cam $port"
    if (-not (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -Profile Any | Out-Null
        Write-Host "firewall: created '$name'"
    } else { Write-Host "firewall: '$name' present" }
}
foreach ($p in 8089,8090) { Ensure-PortRule $p }
if (-not (Get-NetFirewallRule -DisplayName 'dominator-cam python' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName 'dominator-cam python' -Direction Inbound -Action Allow -Program $pyExe -Profile Any | Out-Null
    Write-Host "firewall: created 'dominator-cam python'"
} else { Write-Host "firewall: 'dominator-cam python' present" }

# --- 2. ffmpeg name-resolution gate ---
$ff = 'C:\ffmpeg\bin\ffmpeg.exe'
if (-not (Test-Path $ff)) { throw "ffmpeg.exe missing at $ff (needed for name binding) - copy it first" }
$ErrorActionPreference = 'Continue'  # ffmpeg writes the device list to stderr; don't treat it as fatal
$o = (& $ff -hide_banner -f dshow -list_devices true -i dummy 2>&1 | Out-String)
$ErrorActionPreference = 'Stop'
$vids = @()
foreach ($l in ($o -split "`r?`n")) { if ($l -match '"([^"]+)"\s*\(video\)') { $vids += $matches[1] } }
Write-Host ("dshow video devices: " + ($vids -join ' | '))
$hasBison = @($vids | Where-Object { $_ -match 'BisonCam' }).Count -gt 0
$hasUsb   = @($vids | Where-Object { $_ -match 'USB CAMERA' }).Count -gt 0
if (-not ($hasBison -and $hasUsb)) { throw "name gate FAILED (BisonCam=$hasBison USB CAMERA=$hasUsb)" }
Write-Host "name gate OK: BisonCam + USB CAMERA both enumerate"

# --- 3. Stop/clear existing instances so the name-bound bats take effect ---
foreach ($t in 'dominator-cam-bisoncam','dominator-cam-usbcam') {
    schtasks /end /tn $t 2>$null | Out-Null
    schtasks /delete /tn $t /f 2>$null | Out-Null
}
Start-Sleep 2
foreach ($p in 8089,8090) {
    $c = Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue
    if ($c) { $c.OwningProcess | Select-Object -Unique | ForEach-Object { Write-Host "freeing port ${p}: kill PID $_"; Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue } }
}
Start-Sleep 2

# --- 4. Register both tasks: AtLogOn auto-start + interactive + unlimited + auto-restart ---
$user = "$env:COMPUTERNAME\$env:USERNAME"
function Make-Task($name, $bat) {
    $action    = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "' + $bat + '"')
    $trigger   = New-ScheduledTaskTrigger -AtLogOn -User $user
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $name
    Write-Host "task '$name' registered (AtLogOn auto-start) + started"
}
Make-Task 'dominator-cam-bisoncam' 'C:\farm-services\dominator-cam\start-bisoncam.bat'
Make-Task 'dominator-cam-usbcam'   'C:\farm-services\dominator-cam\start-usbcam.bat'

# --- 5. Verify ---
Write-Host "=== waiting 22s for camera warmup ==="
Start-Sleep 22
Get-NetTCPConnection -State Listen -LocalPort 8089,8090 -ErrorAction SilentlyContinue | Select-Object LocalPort,OwningProcess | Format-Table -AutoSize
foreach ($p in 8089,8090) {
    try {
        $j = (Invoke-WebRequest "http://127.0.0.1:$p/health" -UseBasicParsing -TimeoutSec 10).Content | ConvertFrom-Json
        Write-Host ("  :$p camera_open=" + $j.camera_open + " grabs=" + $j.total_grabs + " fails=" + $j.total_failures)
    } catch { Write-Host "  :$p ERROR $($_.Exception.Message)" }
}
Write-Host "=== name-resolution proof (per-camera logs) ==="
foreach ($f in 'bisoncam.log','usbcam.log') {
    $p = "C:\farm-services\dominator-cam\$f"
    if (Test-Path $p) { Write-Host "-- $f --"; Get-Content $p | Where-Object { $_ -match 'resolved|camera opened|ready' } | Select-Object -Last 3 }
}
Write-Host "=== DONE ==="
