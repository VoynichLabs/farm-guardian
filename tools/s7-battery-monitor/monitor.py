# Author: Claude Opus 4.7
# Date: 16-April-2026
# PURPOSE: Poll S7 battery via ADB on whichever Mac the phone is USB-tethered to
#          (currently the MacBook Air). Emit Discord alerts on three transitions:
#          (1) battery level drops below LEVEL_ALERT (default 25%), (2) battery
#          temperature rises above TEMP_ALERT_TENTHS (default 48.0°C), (3) phone
#          comes off USB power unexpectedly. Every poll also goes to a rolling
#          local log so a past failure can be reconstructed. Alerts are deduped
#          via a tiny JSON state file — they fire on the transition *into* the
#          alert state, and a matching "recovered" message fires on the way out.
#          Rationale: Boss's S7 had been dying on the charger because the camera
#          load drained the battery faster than USB could top it up. After the
#          v2.27.7 camera tuning and the HTTP-snapshot path, draw is much lower,
#          but the phone is still old and the battery is worn — we want
#          visibility without spam.
# SRP/DRY: Self-contained. Stdlib only (urllib for Discord, subprocess for adb,
#          json for state). Doesn't share code with Guardian because it runs on
#          a different machine than Guardian (MBA, not Mini) and a cross-machine
#          import is not worth 10 lines of boilerplate.

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

SERVICE_ROOT = Path(os.environ.get(
    "SERVICE_ROOT", os.path.expanduser("~/.local/farm-services/s7-battery-monitor")
))
ADB = os.environ.get(
    "ADB_PATH", os.path.expanduser("~/.local/android/platform-tools/adb")
)
SERIAL = os.environ.get("S7_SERIAL", "ce12160cec2f2f0901")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")

LEVEL_ALERT = int(os.environ.get("LEVEL_ALERT", "25"))
TEMP_ALERT_TENTHS = int(os.environ.get("TEMP_ALERT_TENTHS", "480"))
STATE_PATH = SERVICE_ROOT / "state.json"
LOG_PATH = SERVICE_ROOT / "monitor.log"

SERVICE_ROOT.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("s7-battery-monitor")


def read_battery() -> Optional[dict]:
    # adb reconnect first — the S7 routinely drops its USB composite when the
    # screen sleeps; `adb reconnect offline` is the documented way to re-arm
    # without unplugging. Cheap no-op when the device is already live.
    subprocess.run([ADB, "reconnect", "offline"], capture_output=True, timeout=10)
    # Small settle so dumpsys sees the re-armed transport.
    time.sleep(1.0)
    result = subprocess.run(
        [ADB, "-s", SERIAL, "shell", "dumpsys", "battery"],
        capture_output=True,
        timeout=15,
        text=True,
    )
    if result.returncode != 0:
        log.warning("adb returncode=%d stderr=%s", result.returncode, result.stderr.strip())
        return None
    data = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if ":" in line:
            k, _, v = line.partition(":")
            data[k.strip()] = v.strip()
    return data


def parse(raw: Optional[dict]) -> Optional[dict]:
    if not raw:
        return None
    try:
        return {
            "level": int(raw["level"]),
            "temp_tenths": int(raw["temperature"]),
            "voltage_mv": int(raw["voltage"]),
            "status_code": int(raw["status"]),
            "usb_powered": raw.get("USB powered", "").lower() == "true",
            "ac_powered": raw.get("AC powered", "").lower() == "true",
        }
    except (KeyError, ValueError) as exc:
        log.warning("dumpsys parse failed: %s; raw=%r", exc, raw)
        return None


def post_discord(content: str, *, username: str = "S7 Battery") -> None:
    if not WEBHOOK:
        log.info("no webhook configured; skipping Discord post: %s", content)
        return
    body = json.dumps({"username": username, "content": content}).encode()
    req = urllib.request.Request(
        WEBHOOK,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        log.info("posted to Discord: %s", content)
    except urllib.error.URLError as exc:
        log.warning("Discord post failed: %s", exc)


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception as exc:
            log.warning("state load failed (%s); starting fresh", exc)
    return {"alerted": {}}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def main() -> int:
    raw = read_battery()
    data = parse(raw)
    state = load_state()
    alerts = state.setdefault("alerted", {})

    # ADB unreachable → treat as "phone offline." This is the most important
    # alert because it's the predecessor to every bad outcome (phone rebooted,
    # USB came loose, phone ran out of battery and shut itself off).
    if data is None:
        log.warning("could not read battery (adb unreachable or dumpsys parse failed)")
        if "offline" not in alerts:
            post_discord(
                "S7 is unreachable via ADB. Either the phone is asleep, USB came loose, "
                "or the phone rebooted. Guardian camera feed will confirm which."
            )
            alerts["offline"] = int(time.time())
            save_state(state)
        return 1

    # Coming back from offline: announce recovery so we know when it resolved.
    if "offline" in alerts:
        post_discord(
            f"S7 ADB back online. Battery {data['level']}%, "
            f"{data['temp_tenths']/10:.1f}°C, "
            f"{'charging' if data['usb_powered'] or data['ac_powered'] else 'on battery'}."
        )
        alerts.pop("offline", None)

    log.info(
        "level=%d%% temp=%.1fC v=%dmV status=%d usb_powered=%s ac_powered=%s",
        data["level"],
        data["temp_tenths"] / 10,
        data["voltage_mv"],
        data["status_code"],
        data["usb_powered"],
        data["ac_powered"],
    )

    # Alert 1: low battery (transition into alert).
    if data["level"] < LEVEL_ALERT:
        if "low" not in alerts:
            post_discord(
                f"S7 battery is at **{data['level']}%**. "
                f"Phone is {'on USB' if data['usb_powered'] else 'unplugged'} "
                f"({data['temp_tenths']/10:.1f}°C). "
                f"If the level keeps dropping the phone will reboot and the camera will go offline."
            )
            alerts["low"] = int(time.time())
    elif "low" in alerts:
        post_discord(f"S7 battery recovered — now at {data['level']}%.")
        alerts.pop("low", None)

    # Alert 2: high temperature (transition into alert).
    if data["temp_tenths"] >= TEMP_ALERT_TENTHS:
        if "hot" not in alerts:
            post_discord(
                f"S7 temperature is **{data['temp_tenths']/10:.1f}°C**. "
                f"Above ~45°C the battery degrades faster and the phone may throttle. "
                f"Check airflow around the phone or drop the camera duty cycle."
            )
            alerts["hot"] = int(time.time())
    elif "hot" in alerts:
        post_discord(f"S7 temperature back to {data['temp_tenths']/10:.1f}°C.")
        alerts.pop("hot", None)

    # Alert 3: unplugged unexpectedly. Phone is supposed to live on USB.
    if not data["usb_powered"] and not data["ac_powered"]:
        if "unplugged" not in alerts:
            post_discord(
                f"S7 is **not on USB power** (battery {data['level']}%). "
                f"Cable may be loose or the charger was disconnected."
            )
            alerts["unplugged"] = int(time.time())
    elif "unplugged" in alerts:
        post_discord(f"S7 back on USB power (battery {data['level']}%).")
        alerts.pop("unplugged", None)

    state["last_level"] = data["level"]
    state["last_temp_tenths"] = data["temp_tenths"]
    state["last_ts"] = int(time.time())
    save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log.exception("unhandled error")
        sys.exit(2)
