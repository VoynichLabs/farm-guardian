#!/bin/bash
# Author: Claude Opus 5 (Bubba)
# Date: 01-August-2026
# PURPOSE: One-shot charge-path diagnosis for the S7 phone-cam (SM-G930F, Android 8.0.0).
#   Run this the moment the phone enumerates over USB. It answers, in order:
#     (1) Is the phone charging RIGHT NOW, and from which source (AC / USB / Wireless)?
#     (2) What does Android think the battery health and level are?
#     (3) Is a Samsung MUIC water/moisture flag latched? (the "permanently flipped" theory)
#     (4) Is the charge-blocking store flag or a battery-protect limit set?
#
#   BACKGROUND (01-Aug-2026): The phone got wet and stopped charging. Boss's theory was a
#   latched software flag. Counter-evidence found that day: with the phone cabled to the Mac
#   mini, it did NOT enumerate on the USB bus AT ALL (no Samsung VID 0x04E8 anywhere in
#   `ioreg -rc IOUSBHostDevice`). A moisture charge-block disables CHARGING but leaves the
#   USB DATA path alive, so zero enumeration points at the cable, the port, or the MUIC
#   itself -- not a flag reachable from software. This script exists to settle that question
#   with real numbers as soon as a DATA cable makes the phone visible.
#
#   Read-only. Prints findings; changes nothing on the phone.
#
# SRP/DRY: Self-contained diagnostic. Companion to s7-settings-watchdog (liveness) and
#          tools/s7-battery-monitor/monitor.py (alerting). Neither of those reads charge SOURCE.
set -u

SERIAL="${S7_SERIAL:-ce12160cec2f2f0901}"
ADB="${ADB_PATH:-$(command -v adb || echo /opt/homebrew/bin/adb)}"

say(){ printf '\n=== %s ===\n' "$1"; }

# --- 0. Is it even on the bus? -----------------------------------------------------------
say "USB bus (host side)"
if ioreg -rc IOUSBHostDevice -w0 2>/dev/null | grep -qi 'idVendor" = 1256'; then
  echo "OK  Samsung device present on the USB bus (VID 1256 / 0x04E8)."
else
  echo "FAIL  No Samsung VID (1256 / 0x04E8) on the USB bus."
  echo "      The phone is not electrically enumerating. Before blaming software, rule out:"
  echo "        a) charge-only micro-USB cable (no D+/D- wires) -- very common"
  echo "        b) lint / corrosion in the micro-USB port (water + nesting-box dust)"
  echo "        c) the hub chain -- plug DIRECTLY into the Mac mini, not a downstream hub"
  echo "      Everything below needs adb and will be empty until this line says OK."
fi

# --- 1. adb reachable? -------------------------------------------------------------------
say "adb"
"$ADB" devices -l 2>&1 | sed '/^$/d'
if ! "$ADB" devices 2>/dev/null | grep -q "^${SERIAL}[[:space:]]*device"; then
  echo
  echo "S7 ($SERIAL) is not in 'device' state."
  echo "If it shows 'unauthorized', unlock the phone and accept the USB-debugging prompt."
  echo "If nothing is listed at all, see the USB-bus section above -- stop here."
  exit 1
fi

A=("$ADB" -s "$SERIAL")

# --- 2. The actual question: is it charging, and from what? -------------------------------
say "dumpsys battery (charge source + level + health)"
"${A[@]}" shell dumpsys battery 2>&1

cat <<'EOF'

  How to read that:
    AC powered / USB powered / Wireless powered -- exactly ONE should be true while cabled.
      all three false while plugged in  -> charge path is blocked or the cable carries no power
      USB powered: true but level flat  -> negotiating, but current is being limited/refused
    status:  1 unknown  2 charging  3 discharging  4 not charging  5 full
      "4 not charging" WHILE plugged in is the classic latched-block signature.
    health:  2 good  3 overheat  4 dead  5 over voltage  6 unspecified failure  7 cold
      Water damage most often shows here as 5 (over voltage) or 6 (unspecified failure).
EOF

# --- 3. Samsung MUIC / moisture ("water detect") nodes ------------------------------------
# On Exynos Samsungs the micro-USB interface controller exposes its state under /sys/class/sec.
# Node names vary by firmware; probe the known candidates and print whatever exists.
say "Samsung MUIC / moisture nodes"
"${A[@]}" shell 'for n in \
    /sys/class/sec/switch/attached_dev \
    /sys/class/sec/switch/afc_disable \
    /sys/class/sec/switch/adc \
    /sys/class/sec/ccic/water \
    /sys/class/power_supply/battery/batt_misc_event \
    /sys/class/power_supply/battery/battery_health \
    /sys/class/power_supply/battery/store_mode \
    /sys/class/power_supply/battery/batt_slate_mode \
    /sys/class/power_supply/usb/online \
    /sys/class/power_supply/ac/online ; do
  if [ -e "$n" ]; then printf "%-58s = %s\n" "$n" "$(cat "$n" 2>/dev/null || echo "<permission denied>")"; fi
done' 2>&1

cat <<'EOF'

  batt_misc_event is the water/moisture bitfield on Samsung. Non-zero = an event is latched;
  bit 0x1 is the water-detect bit on most Exynos builds. It is kernel state, NOT a user
  setting -- it clears when the driver stops seeing the fault (i.e. when the port is truly
  dry and clean), or on reboot. There is no supported userspace command to force-clear it,
  and writing to these nodes needs root, which this phone does not have.

  batt_slate_mode / store_mode: if either reads 1, charging is deliberately suppressed.
  Those DO have a userspace cause and would genuinely be "something flipped".
EOF

# --- 4. Context ---------------------------------------------------------------------------
say "Device / build"
"${A[@]}" shell 'getprop ro.product.model; getprop ro.build.version.release; getprop ro.boot.bootloader' 2>&1

say "Uptime (has it rebooted since the soaking?)"
"${A[@]}" shell 'cat /proc/uptime' 2>&1 | awk '{printf "up %.1f hours\n", $1/3600}'

say "IP Webcam process state (why the camera feed is stalled)"
"${A[@]}" shell 'ps -A 2>/dev/null | grep -i "com.pas.webcam" || ps 2>/dev/null | grep -i "com.pas.webcam" || echo "com.pas.webcam NOT RUNNING"' 2>&1

echo
echo "Done. Nothing on the phone was modified."
