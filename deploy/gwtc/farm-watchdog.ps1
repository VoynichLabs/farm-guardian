# farm-watchdog.ps1
# Author: Claude Opus 4.6
# Date: 13-April-2026
# PURPOSE: Detect and recover from the post-reboot dshow zombie pattern on GWTC.
#   ffmpeg can wedge on the dshow camera open after a Windows reboot -- process
#   stays alive, never produces frames, never registers as a publisher with
#   mediamtx, so the nestbox RTSP path 404s. Shawl's --restart never triggers
#   because wedged-ffmpeg never exits.
#   This watchdog probes the local RTSP publisher every 30s. If no publisher
#   is available AND the ffmpeg process has been alive long enough that it's
#   past startup grace (60s), kill it. Shawl will respawn ffmpeg within ~3s
#   and the new instance opens dshow cleanly.
#   Designed to be wrapped by Shawl as the `farmcam-watchdog` Windows service.
#   Logs to C:\farm-services\logs\watchdog.log.

$ErrorActionPreference = "SilentlyContinue"

$LogFile         = "C:\farm-services\logs\watchdog.log"
$RtspUrl         = "rtsp://localhost:8554/nestbox"
$FfmpegName      = "ffmpeg"
$FfprobePath     = "C:\Users\markb\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffprobe.exe"
$ProbeIntervalS  = 30      # how often we check
$WedgeThresholdS = 60      # ffmpeg must be alive at least this long without publishing before we'll kill it (avoids killing during legit startup)
$ProbeTimeoutUs  = 5000000 # ffprobe -timeout in microseconds (5s)

function Write-Log {
    param([string]$Msg)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts $Msg" | Out-File -FilePath $LogFile -Append -Encoding utf8
}

# Probe whether mediamtx has a publisher on the nestbox path.
# Returns $true if ffprobe can read stream info (publisher present), $false otherwise.
function Test-Publisher {
    $null = & $FfprobePath -v error -rtsp_transport tcp -timeout $ProbeTimeoutUs -i $RtspUrl -show_streams 2>&1
    return ($LASTEXITCODE -eq 0)
}

Write-Log "watchdog started -- pid=$PID, probe=${ProbeIntervalS}s, wedge_threshold=${WedgeThresholdS}s, target=$RtspUrl"

while ($true) {
    Start-Sleep -Seconds $ProbeIntervalS

    if (Test-Publisher) {
        # Healthy. Quiet. Don't spam the log every 30s when things are fine.
        continue
    }

    # No publisher. Check ffmpeg state.
    $ffmpeg = Get-Process -Name $FfmpegName -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $ffmpeg) {
        Write-Log "no publisher AND no ffmpeg process -- Shawl is presumably restarting it; no action this cycle"
        continue
    }

    $aliveSec = [int]((Get-Date) - $ffmpeg.StartTime).TotalSeconds
    if ($aliveSec -lt $WedgeThresholdS) {
        Write-Log "no publisher; ffmpeg pid=$($ffmpeg.Id) only alive ${aliveSec}s -- within startup grace, no action"
        continue
    }

    Write-Log "WEDGE DETECTED -- ffmpeg pid=$($ffmpeg.Id) alive ${aliveSec}s with no publisher; killing to force Shawl respawn"
    Stop-Process -Id $ffmpeg.Id -Force -ErrorAction SilentlyContinue
    Write-Log "killed pid=$($ffmpeg.Id) -- Shawl should respawn within ~3s, next probe in ${ProbeIntervalS}s"
}
