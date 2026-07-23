# Register farmcam-screen-on with BOTH triggers: at logon (covers reboots, the
# case that matters most in the coop) and hourly (covers anything that blanks
# the panel mid-run — brightness was found at a non-visible level on
# 23-Jul-2026 despite monitor-timeout already being "never").
# Author: Claude Fable 5, 23-Jul-2026.

$ErrorActionPreference = 'Stop'
$name = 'farmcam-screen-on'

Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\farm-services\screen-on.ps1'

$logon = New-ScheduledTaskTrigger -AtLogOn
$hourly = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Hours 1)

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $name -Action $action `
    -Trigger @($logon, $hourly) -Settings $settings `
    -RunLevel Highest -Force | Out-Null

$t = Get-ScheduledTask -TaskName $name
"registered: $($t.TaskName)  state=$($t.State)  triggers=$($t.Triggers.Count)"
