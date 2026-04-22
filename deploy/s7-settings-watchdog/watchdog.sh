#!/bin/bash
# Re-asserts S7 IP Webcam settings to defend against the regression failure
# mode where a phone/app restart silently reverts settings to defaults:
#   - whitebalance → auto       (we want incandescent for the heat-lamp scene)
#   - focusmode    → macro      (we want continuous-picture)
#   - orientation  → landscape  (we want portrait; s7-cam feeds IG/FB stories + reels, 9:16)
#   - photo_rotation → 0         (we want 90 so EXIF Orientation=6 tags /photo.jpg)
# v2.27.7 added whitebalance + focusmode as Guardian startup-GETs and v2.35.2
# added orientation + photo_rotation. Startup-GETs only fire when Guardian
# itself restarts, not when the phone does — this sidecar fills that gap.
# See docs/16-Apr-2026-heat-lamp-orange-cast-investigation.md and
# docs/skills-s7-adb-operations.md "Orientation" section for context.
set -u
S7=http://192.168.0.249:8080
LOG=/tmp/s7-settings-watchdog.log

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

curl -sS -m 5 "$S7/settings/whitebalance?set=incandescent"     > /dev/null 2>&1 && wb_ok=1 || wb_ok=0
curl -sS -m 5 "$S7/settings/focusmode?set=continuous-picture" > /dev/null 2>&1 && fm_ok=1 || fm_ok=0
curl -sS -m 5 "$S7/settings/orientation?set=portrait"         > /dev/null 2>&1 && or_ok=1 || or_ok=0
curl -sS -m 5 "$S7/settings/photo_rotation?set=90"            > /dev/null 2>&1 && pr_ok=1 || pr_ok=0
echo "$(stamp) wb_ok=$wb_ok fm_ok=$fm_ok or_ok=$or_ok pr_ok=$pr_ok" >> "$LOG"
