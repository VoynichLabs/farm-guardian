#!/bin/bash
# Author: Claude Sonnet 4.6
# Date: 02-May-2026
# PURPOSE: S7 IP Webcam watchdog — two jobs in one script:
#   1. Detect the "black screen" boot race condition (HTTP server starts before
#      Android camera hardware is ready) and fix it via ADB on GWTC.
#   2. Re-assert portrait/WB/focus settings after any phone/app restart.
#
# Root cause of black screen: IP Webcam auto-starts on boot and its HTTP server
# begins accepting connections before the Android camera HAL finishes initialising.
# Frames are empty/black until the app is stopped and restarted. The only
# programmatic fix is ADB force-stop + relaunch.
#
# ADB path: SSH to GWTC (192.168.0.68) → run adb.exe from C:\farm-services\platform-tools\.
# PREREQUISITE: S7 must have granted USB debugging authorisation to GWTC at
# least once (phone shows "Allow USB debugging?" dialog when first connected).
# Until that's done, ADB falls back gracefully — settings are still applied.
#
# Runs every 10 min via com.farmguardian.s7-settings-watchdog LaunchAgent.

set -u

S7="http://192.168.0.249:8080"
GWTC="192.168.0.68"
ADB="C:\\farm-services\\platform-tools\\adb.exe"
LOG="/tmp/s7-settings-watchdog.log"
# Valid S7 frame is 200KB–1MB. Black/uninitialised frames are <1KB.
MIN_FRAME_BYTES=10000
# Seconds to wait after ADB restart for camera hardware to initialise.
ADB_BOOT_WAIT=60

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

apply_settings() {
    /usr/bin/curl -sS -m 5 "$S7/settings/whitebalance?set=incandescent"     > /dev/null 2>&1 && wb=1 || wb=0
    /usr/bin/curl -sS -m 5 "$S7/settings/focusmode?set=continuous-picture"  > /dev/null 2>&1 && fm=1 || fm=0
    /usr/bin/curl -sS -m 5 "$S7/settings/orientation?set=portrait"          > /dev/null 2>&1 && or=1 || or=0
    /usr/bin/curl -sS -m 5 "$S7/settings/photo_rotation?set=90"             > /dev/null 2>&1 && pr=1 || pr=0
    echo "$(stamp) settings applied: wb=$wb fm=$fm or=$or pr=$pr" >> "$LOG"
}

# --- Step 1: check if IP Webcam is serving a real frame ---
frame_bytes=$(/usr/bin/curl -sS -m 15 "$S7/photoaf.jpg" -o /dev/null -w "%{size_download}" 2>/dev/null || echo 0)

if [ "$frame_bytes" -gt "$MIN_FRAME_BYTES" ]; then
    # Serving valid frames — just keep settings correct.
    echo "$(stamp) frame_ok bytes=$frame_bytes" >> "$LOG"
    apply_settings
    exit 0
fi

# --- Step 2: black screen or server down — try ADB restart via GWTC ---
echo "$(stamp) black_screen_detected bytes=$frame_bytes — attempting ADB restart via GWTC" >> "$LOG"

adb_result=$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes \
    markb@"$GWTC" \
    "$ADB shell am force-stop com.pas.webcam 2>&1 && $ADB shell monkey -p com.pas.webcam -c android.intent.category.LAUNCHER 1 2>&1" \
    2>&1)
adb_rc=$?

if [ $adb_rc -eq 0 ]; then
    echo "$(stamp) adb restart ok — waiting ${ADB_BOOT_WAIT}s for camera hardware init" >> "$LOG"
    sleep "$ADB_BOOT_WAIT"
    # Verify it came back
    frame_bytes_after=$(/usr/bin/curl -sS -m 15 "$S7/photoaf.jpg" -o /dev/null -w "%{size_download}" 2>/dev/null || echo 0)
    echo "$(stamp) post_restart frame_bytes=$frame_bytes_after" >> "$LOG"
    apply_settings
else
    # ADB not available yet (phone not connected to GWTC USB, or auth not granted).
    # Apply settings anyway — sometimes this alone wakes a stuck server.
    echo "$(stamp) adb unavailable (rc=$adb_rc) — applying settings only. result: $adb_result" >> "$LOG"
    apply_settings
fi
