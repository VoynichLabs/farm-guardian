# 16-Apr-2026 — S7 IP Webcam "Frozen" Incident

Post-mortem for a recurring failure mode that will absolutely happen again. Read this first the next time Boss says the S7 looks frozen — the recovery is 30 seconds, but only if you skip the diagnostic rabbit hole.

## Symptom

- Guardian dashboard shows the same S7 frame for minutes at a time; Boss reports "S7 frozen."
- `GET http://localhost:6530/api/cameras/s7-cam/frame` still returns HTTP 200 with a reasonable JPEG — because Guardian is serving its **last cached good frame** from the snapshot poller's ring buffer, not a fresh pull.
- `guardian.log` shows a continuous stream of:
  ```
  HTTP snapshot fetch failed for http:s7-cam: ... [Errno 61] Connection refused
  ```
  against `http://192.168.0.249:8080/photoaf.jpg`.

## What it is NOT

- **Not** a dead phone. Dumpsys battery on 2026-04-16 during the incident: `level=100, status=5 (Full), temperature=37.2°C, USB powered=true, mWakefulness=Awake`. The phone itself is fine.
- **Not** the battery-dies-on-charger pattern (v2.24.0 / v2.27.7 addressed that). The phone has plenty of charge. This is a different failure mode.
- **Not** a WiFi / DHCP issue. `nc -z 192.168.0.249 8080 → refused`, not closed/unreachable. The phone is on the network; the specific TCP port has no listener.
- **Not** something Guardian or the config did wrong. Guardian is polling correctly; the phone just stopped answering.

## What it actually is

**The IP Webcam Android app is open, but has navigated to its Configuration (settings) screen, which halts the HTTP server.**

On Pavel Khlebovich's IP Webcam (`com.pas.webcam`), the HTTP server only runs when the app is in its `Rolling` activity (the "camera active" screen). Any time the user navigates into the settings UI — Configuration, OnvifConfiguration, or any sub-page — the Rolling activity stops and the server unbinds from port 8080. Port stays closed until someone taps **"Start server"** on the main Configuration screen.

Confirm via:

```bash
ssh markb@192.168.0.50 '~/.local/android/platform-tools/adb reconnect offline && \
  ~/.local/android/platform-tools/adb -s ce12160cec2f2f0901 shell \
  "dumpsys activity activities | grep -E \"mResumedActivity|pas.webcam/\" | head -3"'
```

If the top activity is `com.pas.webcam/.Configuration` or `.OnvifConfiguration` (or any other sub-page), the server is down. If it's `.Rolling`, the server is running and you should look elsewhere.

On 2026-04-16, Boss had opened the app to tweak the ONVIF settings. Navigating out of Rolling killed the server. Server stayed down until manual "Start server" tap. Guardian's cached last-good frame made the dashboard look frozen for minutes.

## Recovery — 30 seconds, by hand

1. Pick up the phone. IP Webcam is already open.
2. Press the back arrow until you're on the main Configuration screen.
3. Scroll to the bottom and tap **"Start server"**.

That's it. Port 8080 re-binds instantly. Guardian picks up the next poll tick within 60 s (one-minute interval from v2.27.7) and the log line `Camera 's7-cam' — snapshots resumed after N failures` confirms it.

## Why automated ADB recovery isn't worth pursuing

On 2026-04-16 I burned ~10 minutes trying to bring the server up remotely. Summary of what doesn't work, so the next agent doesn't repeat it:

- **`am start -n com.pas.webcam/.Rolling` directly** — throws a Binder exception. Rolling requires internal app state that can't be instantiated via `am start` from a cold activity stack.
- **Tasker broadcast intents** — `com.pas.webcam.CONTROL`, `com.pas.webcam.START_SERVER`, and variants (`--es action start`, `-e action start`, `com.pas.webcam/.Task` with extras) all accept the broadcast (`result=0`) but do not start the server when the app is sitting on Configuration. IP Webcam's `CONTROL` receiver may only process commands when Rolling is already active; or the Tasker extras format for this specific version of the app is different from the public docs. Either way, broadcasts are a dead end.
- **UI automation (`input keyevent BACK` + `uiautomator dump` + `input tap`)** — in principle the right approach. In practice the **S7's USB composite drops between every `adb shell` invocation** when the screen is on and the user is in an app. `adb reconnect offline` re-arms it, but the next shell call hits `device 'ce12160cec2f2f0901' not found` again. Packing all commands into a single `adb shell` heredoc sometimes works for one invocation but isn't reliable enough to chase across the UI. This is a hardware / firmware quirk of this particular Android 8 S7 on this particular cable — not fixable from the Mac side.
- **Force-stop + launcher intent** — works, but opens the app fresh on Configuration again. You still have to tap Start Server. Net zero.

If you really want to automate this in the future, the path is either (a) a root-only boot script on the phone that keeps Rolling as the resumed activity forever, or (b) Tasker on the phone itself watching for "Configuration activity resumed" and automatically switching back to Rolling. Neither is worth building for a 30-second manual fix.

## Prevention

On the S7, in IP Webcam settings:

- **Service control** → enable **"Run server in background"** (so backgrounding the app doesn't stop Rolling).
- **Power management** → enable **"Keep camera running when locked"** and **"Acquire wake lock."**
- If it exists on this version: **"Start server when the app opens"** / **"Automatic start on boot."**

Also, in Samsung Android's battery settings, find `IP Webcam` in the app list and mark it **"Never sleeping"** (a.k.a. remove from Adaptive Battery / battery optimization). Samsung's aggressive app killer will otherwise background IP Webcam after a while regardless of the in-app settings.

None of these are durable across a factory reset, a major Android update, or the user reinstalling the app. If the incident recurs and none of the above were ever toggled, that's the first thing to check.

## Cross-references

- v2.24.0 — switched S7 from RTSP streaming to HTTP snapshot to kill the battery-drain-on-charger pattern (different failure mode)
- v2.27.7 — re-applied `focusmode=continuous-picture` + `whitebalance=incandescent` via `http_startup_gets`. These persist across Guardian restarts but NOT across IP Webcam app restarts — if the app is force-stopped and reopened, Guardian will re-assert them on its next snapshot-poller construction (on Guardian's next restart), not immediately. So image quality may briefly revert to macro + auto-WB for up to one Guardian-restart cycle after an IP Webcam recovery; restarting Guardian explicitly (`launchctl kickstart -k gui/$UID/com.farmguardian.guardian`) is a belt-and-suspenders step if the post-recovery frames look "off."
- v2.27.8 — S7 battery monitor on the MBA. Note: the battery monitor polls via ADB on the MBA, which is independent of whether IP Webcam is serving HTTP. So battery alerts continue firing even when the camera is "frozen" in the sense described here — the phone's battery state is still visible. In fact the battery monitor is the authoritative answer to "is the phone itself alive" during an IP Webcam outage.
