# S7 Operations via ADB

**Last updated:** 16-April-2026
**Cross-refs:** `CHANGELOG.md` (v2.27.7 camera tuning, v2.27.8 battery monitor, v2.27.9 freeze incident) · `docs/16-Apr-2026-s7-ipwebcam-frozen-incident.md` · `tools/s7-battery-monitor/monitor.py`

## Purpose

How to check on the Samsung Galaxy S7 camera phone (`s7-cam`) via ADB — battery level, temperature, charging state, screen state, IP Webcam app state. This is the manual-operator companion to the automated `tools/s7-battery-monitor/` service. Use it when Boss asks a specific question about the phone or when the camera feed looks wrong and you need to diagnose whether the phone, the app, or Guardian is to blame.

**No credentials in this doc.** Serial numbers, ports, and package names are fine; user passwords and API tokens are not.

---

## Where the phone lives and how to reach it

- **Physical:** USB-tethered to the **MacBook Air** at `192.168.0.50` (as of 16-Apr-2026 — check `skills/macbook-air/SKILL.md` or the current system if unsure). Not plugged into the Mac Mini. This matters because ADB-over-USB is the only reliable path; ADB-over-WiFi hasn't been set up and on Android 8 the `adb tcpip 5555` listener doesn't survive phone reboots anyway.
- **SSH to the host:** `ssh markb@192.168.0.50` (Bubba's ed25519 key is in the host's `authorized_keys`).
- **ADB binary on the host:** `~/.local/android/platform-tools/adb` (installed 14-Apr-2026; not on `$PATH` by default — use the full path).
- **Device serial:** `ce12160cec2f2f0901`. Always `-s` it because other Samsung devices may be enumerated on the same bus.
- **Android version:** 8.0.0 (herolte / SM-G930F). Pre-Android-11, so no wireless-debugging pairing protocol. Pre-scoped-storage, so most older ADB recipes work fine.

## The one quirk you have to know

The S7's **USB composite interface drops between `adb shell` invocations** when the screen is on and the user is inside an app. Symptom: `adb devices` returns empty or the next `adb -s … shell` hits `device 'ce12160cec2f2f0901' not found`.

Fix: **run `adb reconnect offline` before every logical batch of commands.** The `monitor.py` service does this on every 5-minute tick; it's not a sign of bad hardware, just how this particular phone/cable/Android-8 combination behaves.

```bash
ADB=~/.local/android/platform-tools/adb
$ADB reconnect offline && sleep 1 && $ADB devices -l
```

If `adb reconnect offline` repeatedly returns empty and `lsof` / `system_profiler SPUSBDataType` on the host also fails to show the phone, the USB cable or port is genuinely bad — that's a hardware call, not software.

For anything involving a sequence of shell commands (e.g. navigating the UI, chained `dumpsys` calls), **pack them into a single `adb shell` invocation** so you only pay the USB-re-arm cost once:

```bash
$ADB -s ce12160cec2f2f0901 shell 'cmd1; cmd2; cmd3'
```

---

## Common one-liners

Run these from the MBA (`ssh markb@192.168.0.50 '...'` if you're driving from the Mini).

### Is the phone reachable at all?

```bash
$ADB reconnect offline && sleep 1 && $ADB devices -l
```

Expected: `ce12160cec2f2f0901     device usb:20-2 product:heroltexx model:SM_G930F …`. Status `device` = good. `unauthorized` = phone needs "Allow USB debugging" tap (shouldn't happen — it's been authorized — but if it does, unlock the screen). Empty = USB dropped or cable loose.

### Battery: level, temperature, voltage, charging state

```bash
$ADB -s ce12160cec2f2f0901 shell "dumpsys battery | grep -E 'level|temperature|voltage|status|USB powered|AC powered|health'"
```

Sample output (2026-04-16 reading):

```
  AC powered: false
  USB powered: true
  status: 5
  health: 2
  level: 100
  voltage: 4280
  temperature: 372
```

Parse:

- **`level`** = % charge (0–100).
- **`temperature`** = **tenths of degrees C** (so `372` = `37.2°C`, `441` = `44.1°C`). Samsung considers 45°C the "too warm" threshold; the monitor alerts at 48°C.
- **`voltage`** = millivolts (`4280` = `4.28V`; full Li-ion = ~4.3V, nominal = 3.7V, low = <3.5V).
- **`status`** = code: `1`=unknown, `2`=charging, `3`=discharging, `4`=not charging, `5`=full.
- **`health`** = code: `2`=good, others indicate overheating, overvoltage, etc.
- **`USB powered`** / **`AC powered`** = which input is actively powering the phone. Boss's rule: the S7 should be on USB whenever the camera is running. If both are `false` while the S7 is supposed to be capturing, something came unplugged.

### Screen state / is the phone awake?

```bash
$ADB -s ce12160cec2f2f0901 shell "dumpsys power | grep -E 'mWakefulness=|mHoldingDisplaySuspendBlocker='"
```

- `mWakefulness=Awake` = screen on.
- `mWakefulness=Asleep` / `Dozing` = screen off; IP Webcam *should* still serve if "Keep camera running when locked" is on, but Samsung's battery manager can still background the app.

### What activity is IP Webcam showing right now?

```bash
$ADB -s ce12160cec2f2f0901 shell "dumpsys activity activities | grep -E 'mResumedActivity|pas.webcam/' | head -5"
```

- `com.pas.webcam/.Rolling` = **server is running** (camera active, HTTP port 8080 bound).
- `com.pas.webcam/.Configuration` (or any sub-page like `.OnvifConfiguration`) = **server is NOT running.** This is the "frozen camera" failure mode — Boss or someone else opened the settings and the Rolling activity died. See `docs/16-Apr-2026-s7-ipwebcam-frozen-incident.md` for the full writeup, including which recovery approaches don't work and why.

### Is IP Webcam even running?

```bash
$ADB -s ce12160cec2f2f0901 shell "pidof com.pas.webcam"
```

Empty = the app isn't running at all (force-stopped or killed). Non-empty = running, but see the activity check above for whether the server is bound.

### Thermal sensors (if battery temp alone isn't enough)

```bash
$ADB -s ce12160cec2f2f0901 shell "cat /sys/class/thermal/thermal_zone*/type /sys/class/thermal/thermal_zone*/temp 2>/dev/null | paste -d' ' - -"
```

Temperatures in millidegrees (so `37000` = `37.0°C`). Zone names vary by hardware — the ones that matter are usually `battery`, `tsens_tz_sensor*` (SoC), and `pa_therm*` (power amp / radio). Read the zone type alongside the temp to know what you're looking at.

### CPU / memory / is the phone stressed?

```bash
$ADB -s ce12160cec2f2f0901 shell "top -b -n 1 -m 10"        # top 10 processes by CPU
$ADB -s ce12160cec2f2f0901 shell "dumpsys meminfo com.pas.webcam | head -30"
```

IP Webcam under normal camera load should sit around 10–20% CPU, ~200 MB RAM. If it's much higher, something is wrong (usually the server stuck trying to encode for a dead connection).

---

## What NOT to try

These are the dead ends I walked into on 2026-04-16 so you don't have to.

- **`am start -n com.pas.webcam/.Rolling`** — throws a Binder exception. Rolling needs internal app state it can't get from a cold intent.
- **Tasker-style broadcast intents** — `com.pas.webcam.CONTROL` with `action=start`, `com.pas.webcam.START_SERVER`, variants with `--es` or `-e` extras. All return `result=0` but don't actually start the server when the app is on a Configuration screen. Dead end on this version.
- **Force-stop + re-launch via LAUNCHER intent** — works, but the app opens fresh on Configuration. You still need a human tap on "Start server." Net zero.
- **UI automation via `input keyevent` + `uiautomator dump` + `input tap`** — in principle correct, in practice blocked by the USB composite dropping between commands. If you really need it, pack everything into one `adb shell` heredoc — I got partial results that way but it wasn't reliable enough to trust.

**The reliable recovery for "IP Webcam server stopped" is: ask Boss to tap "Start server."** 30-second manual fix; don't spend more than 5 minutes trying to automate it remotely.

---

## If you want to automate more

For a real hands-off setup, the path is IP Webcam's own settings on the phone (not ADB):

- **Service control → "Run server in background"** — so backgrounding the app doesn't stop the server.
- **Service control → "Keep camera running when locked"** + **"Acquire wake lock"** — so lock-screen doesn't kill Rolling.
- **(if available on this version) "Start server when the app opens"** — so reopening the app goes straight to Rolling instead of Configuration.
- **Samsung battery settings → mark IP Webcam as "Never sleeping"** (remove from Adaptive Battery / battery optimization). Even with the in-app toggles on, Samsung's Android layer will background IP Webcam after a few hours if it's not whitelisted.

These have to be set on the phone UI. They're not exposed via `dumpsys` or `am` — the app's preferences are in its protected data dir, not in a public settings provider. If an agent attempts to toggle them via adb, it will fail; the only path is Boss tapping them once.

## The automated monitor

`tools/s7-battery-monitor/monitor.py` runs on the MBA under launchd (`com.farmguardian.s7-battery-monitor`, `StartInterval=300`). It calls `adb reconnect offline` + `dumpsys battery` every 5 minutes, logs to `~/.local/farm-services/s7-battery-monitor/monitor.log`, and posts to #farm-2026 (via the `DISCORD_WEBHOOK_URL` env var in its plist) on three transitions:

- Battery `level` drops below `LEVEL_ALERT` (default 25%)
- Temperature (tenths) rises above `TEMP_ALERT_TENTHS` (default 480 → 48.0°C)
- `USB powered=false` *and* `AC powered=false` when we expect it to be on USB

Each alert fires once on entry into the bad state, fires a matching "recovered" message on exit, and is deduped in `~/.local/farm-services/s7-battery-monitor/state.json`. Tail the log if you want to see the drain curve over time — the row format is:

```
2026-04-16 16:03:00,211 INFO level=99% temp=41.6C v=4282mV status=2 usb_powered=True ac_powered=False
```
