# Author: Claude Opus 4.8 (Bubba)
# Date: 12-June-2026
# PURPOSE: Bring both Dominator camera feeds online durably: (1) ensure inbound Windows
#          Firewall allow rules for TCP 8089 (dominator-cam/BisonCam) + 8090 (usb-cam/USB
#          CAMERA) and for the venv python.exe; (2) create + start two interactive (/IT)
#          scheduled tasks that run the per-camera start .bat files, decoupled from the SSH
#          session so the feeds survive disconnect. Idempotent. No boot/logon trigger on
#          purpose (opportunistic posture - this is Larry's daily-driver + WSL host).
# SRP/DRY check: Pass - provisioning only; reuses existing usb_cam_host.py service.
$ErrorActionPreference = 'Continue'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
Write-Host "elevated session: $isAdmin"

# --- Firewall: inbound allow for 8089 + 8090, plus the python.exe app rule ---
$pyExe = 'C:\farm-services\dominator-cam\venv\Scripts\python.exe'
function Ensure-PortRule($port) {
    $name = "dominator-cam $port"
    if (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue) {
        Write-Host "firewall: '$name' already present"; return
    }
    try {
        New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -Profile Any -ErrorAction Stop | Out-Null
        Write-Host "firewall: created '$name'"
    } catch { Write-Host "firewall: FAILED '$name' -> $($_.Exception.Message)" }
}
foreach ($p in 8089,8090) { Ensure-PortRule $p }
if (-not (Get-NetFirewallRule -DisplayName 'dominator-cam python' -ErrorAction SilentlyContinue)) {
    try {
        New-NetFirewallRule -DisplayName 'dominator-cam python' -Direction Inbound -Action Allow -Program $pyExe -Profile Any -ErrorAction Stop | Out-Null
        Write-Host "firewall: created 'dominator-cam python' (app rule)"
    } catch { Write-Host "firewall: FAILED app rule -> $($_.Exception.Message)" }
} else { Write-Host "firewall: 'dominator-cam python' already present" }

# --- Scheduled tasks: one per camera, interactive, started immediately ---
$tasks = @(
    @{ Name='dominator-cam-bisoncam'; Bat='C:\farm-services\dominator-cam\start-bisoncam.bat' },
    @{ Name='dominator-cam-usbcam';   Bat='C:\farm-services\dominator-cam\start-usbcam.bat' }
)
foreach ($t in $tasks) {
    # Past one-time trigger so it never auto-re-fires; we start it manually with /run.
    schtasks /create /tn $t.Name /tr ('"' + $t.Bat + '"') /sc once /st 00:00 /sd 01/01/2020 /it /f 2>&1 | Out-Host
    Start-Sleep -Milliseconds 400
    schtasks /run /tn $t.Name 2>&1 | Out-Host
    Write-Host "task '$($t.Name)' created + run issued"
}

Write-Host "=== waiting 18s for camera warmup ==="
Start-Sleep -Seconds 18
Write-Host "=== listeners 8089/8090 ==="
Get-NetTCPConnection -State Listen -LocalPort 8089,8090 -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,OwningProcess | Format-Table -AutoSize
foreach ($p in 8089,8090) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$p/photo.jpg" -UseBasicParsing -TimeoutSec 12
        Write-Host ("localhost:{0}/photo.jpg -> HTTP {1}, {2} bytes" -f $p, $r.StatusCode, $r.RawContentLength)
    } catch { Write-Host ("localhost:{0}/photo.jpg -> ERROR {1}" -f $p, $_.Exception.Message) }
}
