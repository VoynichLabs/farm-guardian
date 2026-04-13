# Installing `farmcam-watchdog` on GWTC

**Date:** 13-April-2026
**Purpose:** Auto-detect and recover from the post-reboot dshow zombie pattern (see `docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md` "Addendum — Post-Reboot dshow Zombie Pattern" for the failure mode and history).

The watchdog probes `rtsp://localhost:8554/gwtc` every 30s. If no publisher is available **and** the ffmpeg process has been alive ≥60s (past startup grace), it kills ffmpeg by PID. Shawl's `--restart` policy on the existing `farmcam` service then respawns ffmpeg within ~3s, and the new instance opens dshow cleanly.

## Files

| File | Where it lives on GWTC | What it does |
|---|---|---|
| `farm-watchdog.ps1` | `C:\farm-services\farm-watchdog.ps1` | The watchdog script itself. |
| Log | `C:\farm-services\logs\watchdog.log` | Quiet by design — only logs startup, wedge detection, and kills. |

## One-time install (from the Mac Mini)

```bash
# 1. Copy the script to GWTC
scp -o StrictHostKeyChecking=no \
  ~/Documents/GitHub/farm-guardian/deploy/gwtc/farm-watchdog.ps1 \
  markb@192.168.0.68:C:/farm-services/farm-watchdog.ps1

# 2. Register as a Shawl-wrapped Windows service
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 \
  'sc create farmcam-watchdog binPath= "C:\shawl\shawl.exe run --name farmcam-watchdog --restart -- powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\farm-services\farm-watchdog.ps1" start= auto'

# 3. Start it
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'sc start farmcam-watchdog'

# 4. Verify
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'sc query farmcam-watchdog'
# Expected: STATE: 4 RUNNING

# 5. Watch the log appear after the first 30s probe
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 \
  'powershell -Command "Get-Content C:\farm-services\logs\watchdog.log -Tail 5"'
# Expected first line: ... watchdog started -- pid=NNNN, probe=30s, wedge_threshold=60s, target=rtsp://localhost:8554/gwtc
```

## Updating the script after edits

```bash
# Push new copy
scp -o StrictHostKeyChecking=no \
  ~/Documents/GitHub/farm-guardian/deploy/gwtc/farm-watchdog.ps1 \
  markb@192.168.0.68:C:/farm-services/farm-watchdog.ps1

# Bounce the service
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'sc stop farmcam-watchdog'
sleep 4
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'sc start farmcam-watchdog'
```

## Uninstall

```bash
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'sc stop farmcam-watchdog'
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'sc delete farmcam-watchdog'
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'del C:\farm-services\farm-watchdog.ps1'
```

## Important constraints

- **`.ps1` files must be ASCII or UTF-8 with BOM.** PowerShell 5.1 (Windows default) reads `.ps1` files as ANSI/Windows-1252 unless they carry a UTF-8 BOM. UTF-8 multibyte characters (em-dash, en-dash, smart quotes, etc.) get garbled and produce parser errors like `The string is missing the terminator: "`. The script in this repo intentionally uses ASCII-only punctuation (`--` not `—`). Do not "improve" it with typographic dashes.
- **The probe takes up to 5s** (`-timeout 5000000`). At 30s probe interval that's at most 17% duty cycle — fine.
- **Each probe opens an RTSP DESCRIBE on mediamtx**, which logs a `conn opened` / `conn closed` pair. This is the only ongoing log-noise contribution from the watchdog. Acceptable.
- **The watchdog cannot recover from a wedge that's not actually a wedge.** If mediamtx is dead, port 8554 closed, or the network is down, the watchdog will keep killing ffmpeg uselessly because no kill will fix those. That's why the watchdog does NOT also restart mediamtx or the network — it has one job and it does it. Other failure modes need other fixes.
- **The 60s wedge threshold avoids killing ffmpeg during legitimate startup.** Don't lower this without verifying ffmpeg's normal cold-start time on this hardware (which is on the order of 5-15s but can spike).

## How to test the wedge-recovery path (when you next have a chance)

The basic functionality is verified post-install (service runs, log appears). The actual wedge-recovery path can only be tested by producing a wedge:

1. **Most realistic test:** wait for the next GWTC reboot. If the dshow zombie reproduces, the watchdog should kill it within ~90s (worst case: 30s until probe + 60s wedge threshold) and Shawl will respawn ffmpeg cleanly. Verify by tailing `watchdog.log` and `mediamtx.log` after the reboot.
2. **Synthetic test:** suspend the ffmpeg process for >60s with a debugger or `pssuspend` (Sysinternals) — the publisher will disappear from mediamtx's perspective even though the process is alive. Watchdog should kill it. **Don't run this casually** — it disrupts the live brooder feed.
