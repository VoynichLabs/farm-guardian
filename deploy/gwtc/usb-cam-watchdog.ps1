# usb-cam-watchdog: restart usb-cam-host if port 8089 is dead
$port = 8089
$logFile = "C:\farm-services\usb-cam-watchdog.log"
$taskName = "usb-cam-host"

$tcp = New-Object System.Net.Sockets.TcpClient
try {
    $tcp.Connect("127.0.0.1", $port)
    $tcp.Close()
    exit 0
} catch {}

$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $logFile -Value "$ts  port $port dead, restarting $taskName"

# Kill any stuck python processes holding the camera before restarting
Get-Process python* -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
& schtasks.exe /run /tn $taskName | Out-Null
Start-Sleep -Seconds 20

$tcp2 = New-Object System.Net.Sockets.TcpClient
try {
    $tcp2.Connect("127.0.0.1", $port)
    $tcp2.Close()
    $ok = $true
} catch { $ok = $false }

$ts2 = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $logFile -Value "$ts2  post-restart ok=$ok"
