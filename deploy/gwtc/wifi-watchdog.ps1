# Author: Claude Opus 5
# Date: 25-July-2026
# PURPOSE: Network-reachability watchdog for GWTC (the coop laptop, 192.168.0.69). Runs
#   every 2 minutes as SYSTEM via the `farmcam-wifi-watchdog` scheduled task. Pings the LAN
#   gateway 3x; if all 3 fail, bounces the Wi-Fi adapter — a USB-attached Realtek 8723DU
#   (InstanceId USB\VID_0BDA&PID_D723) — to recover both from driver wedges and from the
#   DHCP-failure/APIPA state observed in the 24-Jul-2026 incident.
#
#   CHANGE 25-Jul-2026 — heartbeat logging. Previously this script wrote to its log ONLY on
#   failure, so a silent log was ambiguous between "ran, everything fine" and "never ran."
#   During the 24-Jul incident that ambiguity produced three separate wrong diagnoses,
#   including a confident claim that this watchdog was dead when it was in fact healthy.
#   It now logs EVERY run, and includes the adapter's current IPv4 — so the APIPA state
#   (ip=169.254.x.x = associated but no DHCP lease, the actual 24-Jul failure) is visible at
#   a glance instead of requiring forensics. Log is size-rotated so the volume stays bounded
#   (~36 KB/day, rotates at 1 MB ≈ 28 days, one previous file retained).
#
# SRP/DRY check: Pass — single responsibility (network reachability recovery). Deliberately
#   NOT merged with farm-watchdog.ps1 (ffmpeg/dshow zombie recovery) or usb-cam-watchdog.ps1
#   (camera grabber recovery); those cover different failure modes and share no logic.
#   Verified no existing helper duplicates this before adding.
#
# DEPLOY: this repo copy is the source of truth. Push with:
#   scp deploy/gwtc/wifi-watchdog.ps1 markb@192.168.0.69:C:/farm-services/wifi-watchdog.ps1
#   Prior to 25-Jul-2026 the script existed ONLY on GWTC and was untracked.
#
# CONSTRAINT: Windows PowerShell 5.1 only — do NOT use PS6+ syntax such as
#   `Test-Connection -TimeoutSeconds`. ping.exe is used because its exit code is 1 for BOTH
#   plain timeout and no-route/APIPA (verified empirically on GWTC 25-Jul-2026), which is
#   exactly the discrimination this watchdog needs.

$gateway     = "192.168.0.1"
$adapter     = "Wi-Fi"
$logFile     = "C:\farm-services\wifi-watchdog.log"
$maxLogBytes = 1MB

# Logging must never be able to take the watchdog itself down — a locked or unwritable log
# is strictly less bad than a watchdog that stops bouncing a wedged adapter.
function Write-Log([string]$message) {
    try {
        if ((Test-Path $logFile) -and ((Get-Item $logFile).Length -gt $maxLogBytes)) {
            Move-Item -Path $logFile -Destination "${logFile}.1" -Force -ErrorAction SilentlyContinue
        }
        $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        Add-Content -Path $logFile -Value ("{0}  {1}" -f $stamp, $message)
    } catch {
        # Intentionally swallowed. See above.
    }
}

# Returns the adapter's IPv4, or "none". A 169.254.x.x result means the radio associated but
# DHCP never handed out a lease — invisible on the /24 AND absent from the router's lease
# table, which is precisely what made the 24-Jul outage look like a powered-off machine.
function Get-AdapterIPv4 {
    try {
        $address = Get-NetIPAddress -InterfaceAlias $adapter -AddressFamily IPv4 -ErrorAction Stop |
                   Select-Object -First 1 -ExpandProperty IPAddress
        if ($address) { return $address }
        return "none"
    } catch {
        return "none"
    }
}

$fails = 0
for ($i = 0; $i -lt 3; $i++) {
    $null = & ping.exe -n 1 -w 2000 $gateway
    if ($LASTEXITCODE -ne 0) { $fails++ }
    Start-Sleep -Seconds 2
}

$currentIp = Get-AdapterIPv4

if ($fails -ge 3) {
    Write-Log "gateway 3/3 fail, bouncing $adapter (ip=$currentIp)"
    try {
        Restart-NetAdapter -Name $adapter -Confirm:$false -ErrorAction Stop
    } catch {
        # Surface the reason rather than silently failing to recover.
        Write-Log "ERROR Restart-NetAdapter failed: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 8
    $null = & ping.exe -n 1 -w 2000 $gateway
    $reachable = ($LASTEXITCODE -eq 0)
    Write-Log ("post-bounce reachable={0} ip={1}" -f $reachable, (Get-AdapterIPv4))
} else {
    # Heartbeat. Its presence proves the task ran; its ip= field shows lease state.
    Write-Log "ok fails=$fails/3 ip=$currentIp"
}
