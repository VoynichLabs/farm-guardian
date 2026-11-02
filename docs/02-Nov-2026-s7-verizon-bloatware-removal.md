# 02-Nov-2026 — S7 (SM-G930V) leftover Verizon packages removed
Author: Claude Sonnet 5.5 Medium

## What was done
Phone plugged into the Mac Mini (adb serial 4fad774d), Android 6.0.1, no SIM (`gsm.sim.state=ABSENT`), on Private WiFi at .249.
`pm uninstall -k --user 0` (the only removal method the shell user has on Android 6 — `pm hide` and `pm disable-user` are denied; no per-app undo, only a factory reset restores) on, one at a time, each followed by a crash-log check and a real snapshot fetch:

- `com.qualcomm.ltebc_vzw` (Verizon LTE broadcast)
- `com.qualcomm.vzw_api` (Verizon API shim)
- `com.vzw.apnservice` (Verizon APN pusher, pointless with no SIM)

All three were idle (no running process) before removal.

## Already neutralised
`com.customermobile.preload.vzw` was already in the disabled state; left alone.

## Deliberately NOT touched
Play Services, `com.sec.ims`, `com.sec.imsservice`, `com.samsung.advp.imssettings` (see docs/skills-s7-adb-operations.md — removing them kills the camera or crash-loops the OS), and all Qualcomm/Samsung core services. Also left: `com.cequint.ecid` (caller ID) and `com.telecomsys.directedsms.android.SCG` (Verizon location agent) — Verizon-linked but not clearly safe, no benefit to removal.

## Verification
Snapshot at /photo.jpg returned HTTP 200 with a full 1920x1080 JPEG after each step; no FATAL/ANR in logcat; IP Webcam process alive; phone never rebooted. Landscape EXIF is the normal post-boot state (force_portrait handles it).
