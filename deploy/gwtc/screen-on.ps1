# screen-on.ps1 — keep the GWTC display awake and visible.
# Author: Claude Fable 5, 23-Jul-2026.
# Why: the coop laptop was "birdproofed" at some point and its screen reads as
# black, so Boss cannot tell from the coop whether the machine is running.
# Power timeouts are already 0/never, so this handles the two remaining causes:
# brightness pinned low, and the display being parked in a powered-off state
# that only an input/power event clears.
# Safe to re-run; run at logon so it survives reboots.

$ErrorActionPreference = 'SilentlyContinue'

# 1. Belt and braces on the power policy (persists across reboots).
powercfg /change monitor-timeout-ac 0
powercfg /change monitor-timeout-dc 0
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0

# 2. Force the monitor powered ON. SC_MONITORPOWER with -1 = on. Broadcasting
#    to all top-level windows is what actually clears a blanked display.
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Disp {
  [DllImport("user32.dll")]
  public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
  [DllImport("user32.dll")]
  public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint cButtons, UIntPtr dwExtraInfo);
}
"@
$HWND_BROADCAST = [IntPtr]0xffff
$WM_SYSCOMMAND  = 0x0112
$SC_MONITORPOWER = [IntPtr]0xF170
[Disp]::SendMessage($HWND_BROADCAST, $WM_SYSCOMMAND, $SC_MONITORPOWER, [IntPtr](-1)) | Out-Null

# 3. Nudge the input stack — a blanked display sometimes needs a real input
#    event, not just the power message, before it lights up.
[Disp]::mouse_event(0x0001, 1, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 150
[Disp]::mouse_event(0x0001, [uint32]4294967295, 0, 0, [UIntPtr]::Zero)

# 4. Push brightness up. Not all panels expose WMI brightness; if this throws
#    the display is still on from step 2, just at whatever brightness it had.
$b = Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightnessMethods
if ($b) {
  $b | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{ Timeout = 0; Brightness = 90 } | Out-Null
  "brightness set to 90"
} else {
  "WMI brightness not exposed by this panel (display still forced on)"
}

$cur = Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness
if ($cur) { "current brightness: " + $cur.CurrentBrightness }
"screen-on.ps1 done"
