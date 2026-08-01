#!/bin/bash
# Author: Claude Opus 4.8 (Bubba)
# Date: 01-August-2026 (rev3 — power source corrected to Qi; see POWER below)
# PURPOSE: Farm Guardian S7 phone-cam (Galaxy S7 / IP Webcam app) liveness watchdog, run by
#   launchd every 600s. Pulls a live frame; logs whether the feed is alive or stalled, and
#   re-applies camera settings when it's alive.
#
#   GROUND TRUTH (Boss-corrected 2026-06-25; POWER section rewritten 2026-08-01):
#   - The S7 is a STANDALONE Wi-Fi camera running the IP Webcam app, reachable at
#     192.168.0.249:8080.
#   - POWER (changed 2026-08-01): the phone now charges on a **Qi WIRELESS PAD**, not the
#     wall brick it used from April to July 2026. The phone got wet; afterwards its
#     micro-USB port stopped working for BOTH power and data. Confirmed 2026-08-01 with a
#     known-good DATA cable plugged DIRECTLY into the Mac mini: the phone does not
#     enumerate on the USB bus at all — no Samsung vendor ID (1256 / 0x04E8) anywhere in
#     `ioreg -rc IOUSBHostDevice`. Qi is the only charging path that works. Note Qi on an
#     SM-G930F is ~5W, and this phone has a documented history of browning out on weak
#     power, so keep the screen off/dim and treat power as the first suspect on any new
#     stall. Diagnostic: tools/s7-charge-diagnose.sh.
#   - PORT IS SETTLED — DO NOT RE-TEST. Every theory is closed: known-good data cable,
#     plugged direct into the mini (no hub), port physically cleaned with a toothpick, and
#     a forced restart (Vol Down + Power — the sealed-battery equivalent of a battery pull,
#     which rebuilds charge-controller state from scratch). The USB bus was polled every 2s
#     for 12 min straight through that reboot and the Samsung VID never appeared once. Note
#     the deeper firmware remedies (Odin reflash, recovery sideload) all REQUIRE working USB
#     and are therefore foreclosed by the very fault they'd be fixing.
#   - It is NOT USB-tethered to any computer, and cannot be — see POWER. There is therefore
#     NO adb path at all: adb-over-USB needs a working port, and adb-over-network is refused
#     (5555 closed) because Android 8.0.0 predates wireless-debugging pairing and enabling
#     `adb tcpip` would itself require one working USB session. GWTC (192.168.0.68) is
#     PERMANENTLY DECOMMISSIONED (down since 2026-06-07) and must NOT be load-bearing in
#     anything. The old ssh-GWTC + adb reopen branch was obsolete nonsense and is removed.
#   - This watchdog therefore CANNOT self-heal a stall. Its job is DETECT + LOG. The durable,
#     ONE-TIME fix for the recurring "app dies" problem is physical, on the phone (NOT settable
#     over the IP Webcam HTTP /settings API — that only exposes focus/exposure/torch/etc., no
#     wakelock or background). Set these once and they persist across restarts:
#       (1) IP Webcam app -> Settings -> enable "Disable lock screen" / "Keep screen on".
#       (2) Android Settings -> Battery -> App optimization -> IP Webcam -> "Unrestricted".
#       (3) Samsung's own app-freezer must be told to leave IP Webcam alone. ⚠ USE THE
#           ANDROID 8 MENU NAMES. This phone is SM-G930F on Android 8.0.0 / Samsung
#           Experience 9.0 — its LAST firmware, it never went further. The "Sleeping apps"
#           / "Deep sleeping apps" lists everyone writes about are One UI (Android 9+) and
#           DO NOT EXIST here. On this phone the equivalents are:
#             a. Settings -> Device maintenance -> Battery -> "Unmonitored apps"
#                (may be behind the ⋮ menu) -> Add apps -> IP Webcam.
#             b. Settings -> Apps -> ⋮ -> Special access -> "Optimize battery usage"
#                -> set the dropdown to "All apps" -> toggle IP Webcam OFF.
#             c. Settings -> Device maintenance -> Battery -> Power mode: NOT a
#                power-saving mode.
#           This freezer, not Doze, is what explains the observed stall: during the
#           2026-07-30 wedge the phone was ON THE CHARGER and still frozen, and AOSP Doze
#           does not engage while charging. Signature: TCP 8080 still ACCEPTS instantly
#           (kernel holds the listening socket) but no HTTP response ever comes back, and
#           ICMP is dropped entirely — the process lives while its threads are frozen.
#     (Fix sourced from Horst's pydroid-ipcam + HA research, 2026-06-25. See provenance.)
#   - File MUST stay outside ~/Documents (macOS TCC blocks launchd from reading scripts there).
# SRP/DRY check: Pass — sole S7 liveness watchdog; detection-only, no dead recovery paths.
set -u

S7="http://192.168.0.249:8080"          # S7 / IP Webcam app — .249 per config.json + HARDWARE_INVENTORY (was wrongly .250 in June rev2 → 2 weeks of false STALLs); DHCP-reserve on the router

PHOTO="/photo.jpg"                       # immediate snapshot endpoint (verified live 2026-06-25)
LOG=/tmp/s7-settings-watchdog.log
stamp(){ date -u +%Y-%m-%dT%H:%M:%SZ; }

apply(){ fm=0; or=0; pr=0
  /usr/bin/curl -sS -m 5 "$S7/settings/focusmode?set=continuous-picture" >/dev/null 2>&1 && fm=1
  /usr/bin/curl -sS -m 5 "$S7/settings/orientation?set=portrait"         >/dev/null 2>&1 && or=1
  /usr/bin/curl -sS -m 5 "$S7/settings/photo_rotation?set=90"            >/dev/null 2>&1 && pr=1
  echo "$(stamp) settings fm=$fm or=$or pr=$pr" >> "$LOG"; }

# Liveness by BYTE COUNT — the stall mode returns HTTP 200 with ~0 bytes, so TCP-up / HTTP-200
# are not honest tests. A real frame is tens-to-hundreds of KB+.
bytes=$(/usr/bin/curl -sS -m 15 "$S7$PHOTO" -o /dev/null -w "%{size_download}" 2>/dev/null || echo 0)
if [ "${bytes:-0}" -gt 10000 ]; then
  echo "$(stamp) frame_ok url=$S7$PHOTO bytes=$bytes" >> "$LOG"
  apply
else
  # No auto-recovery exists: standalone Wi-Fi cam, no USB host, GWTC gone. A stall needs the
  # app reopened on the phone, or its background/keep-awake config restored so it stops dying.
  echo "$(stamp) STALL url=$S7$PHOTO bytes=$bytes — NO auto-recovery (no USB host; GWTC decommissioned); needs app reopen / background-config fix" >> "$LOG"
fi
