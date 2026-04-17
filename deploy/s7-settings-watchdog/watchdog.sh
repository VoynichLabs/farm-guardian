#!/bin/bash
# Re-asserts S7 IP Webcam settings (whitebalance + focusmode) to defend
# against the regression failure mode where a phone/app restart silently
# reverts the camera to whitebalance=auto + focusmode=macro. v2.27.7
# added these as Guardian startup-GETs — but those only fire when
# Guardian restarts, not when the phone does. This sidecar fills that
# gap. See docs/16-Apr-2026-heat-lamp-orange-cast-investigation.md
# "S7 regression recovery" for the full context.
set -u
S7=http://192.168.0.249:8080
LOG=/tmp/s7-settings-watchdog.log

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }

curl -sS -m 5 "$S7/settings/whitebalance?set=incandescent"     > /dev/null 2>&1 && wb_ok=1 || wb_ok=0
curl -sS -m 5 "$S7/settings/focusmode?set=continuous-picture" > /dev/null 2>&1 && fm_ok=1 || fm_ok=0
echo "$(stamp) wb_ok=$wb_ok fm_ok=$fm_ok" >> "$LOG"
