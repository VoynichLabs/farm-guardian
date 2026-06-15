# Author: Claude Opus 4.8 (Bubba sub-agent)
# Date: 14-June-2026
# PURPOSE: Tests for tools/pipeline/golden_windows.py — the dynamic-sunrise two-
#          window helper that drives the usb-cam / dominator-cam time-lapse reels
#          (selection) and their raw-capture cadence (orchestrator). Verifies the
#          NOAA sunrise calc against known farm values, minute-granular window
#          membership (incl. the 19:30 and dynamic-sunrise boundaries), and the
#          enabled/camera gating that keeps s7/mba/gwtc/house-yard untouched.
# SRP/DRY check: Pass — pure-logic tests for one module; no DB, no network. Run as
#                `python -m tools.pipeline.test_golden_windows` or directly.

import sys
from datetime import date, datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.pipeline.golden_windows import (  # noqa: E402
    camera_golden_cfg,
    camera_uses_golden_windows,
    is_dt_in_golden_windows,
    minute_in_window,
    sunrise_minute,
    sunset_minute,
)
from zoneinfo import ZoneInfo  # noqa: E402

FARM_LAT, FARM_LON, TZ = 41.7558, -71.9789, "America/New_York"
ET = ZoneInfo(TZ)

GW = {
    "enabled": True,
    "cameras": ["usb-cam", "dominator-cam"],
    "timezone": TZ,
    "latitude": FARM_LAT,
    "longitude": FARM_LON,
    "windows": [
        {"start": "sunrise", "end": "09:00"},
        {"start": "19:30", "end": "20:30"},
    ],
}

_passed = 0


def check(label, cond):
    global _passed
    assert cond, f"[FAIL] {label}"
    _passed += 1
    print(f"  [PASS] {label}")


def main():
    # 1. NOAA sunrise/sunset vs known farm values (DST-aware via ZoneInfo).
    sr = sunrise_minute(date(2026, 6, 14), FARM_LAT, FARM_LON, TZ)
    ss = sunset_minute(date(2026, 6, 14), FARM_LAT, FARM_LON, TZ)
    check(f"summer sunrise ~05:11 ET (got {sr // 60:02d}:{sr % 60:02d})", 305 <= sr <= 317)
    check(f"summer sunset ~20:24 ET (got {ss // 60:02d}:{ss % 60:02d})", 1218 <= ss <= 1230)
    srw = sunrise_minute(date(2026, 12, 21), FARM_LAT, FARM_LON, TZ)
    check(f"winter sunrise ~07:13 ET (got {srw // 60:02d}:{srw % 60:02d})", 425 <= srw <= 445)

    # 2. minute_in_window primitive (normal + wrap + zero-width).
    check("minute_in_window normal", minute_in_window(600, 540, 660) and not minute_in_window(700, 540, 660))
    check("minute_in_window wrap midnight", minute_in_window(30, 1380, 120) and not minute_in_window(600, 1380, 120))
    check("minute_in_window zero-width matches nothing", not minute_in_window(540, 540, 540))

    # 3. Window membership at boundaries (the boss's spec).
    gwc = camera_golden_cfg("usb-cam", GW)
    cases = {
        (4, 30): False,   # pre-dawn
        (6, 0): True,     # morning
        (8, 59): True,    # last morning minute
        (9, 0): False,    # 09:00 exclusive end
        (13, 0): False,   # midday junk
        (19, 29): False,  # one minute before evening
        (19, 30): True,   # evening start (minute granularity)
        (20, 29): True,   # last evening minute
        (20, 30): False,  # 20:30 exclusive end
        (23, 0): False,   # night
    }
    for (h, m), expect in cases.items():
        got = is_dt_in_golden_windows(datetime(2026, 6, 14, h, m, tzinfo=ET), gwc)
        check(f"{h:02d}:{m:02d} in-window == {expect}", got == expect)

    # 4. Dynamic-sunrise boundary: 05:00 (pre-sunrise) out, 05:20 (post) in.
    check("05:00 ET before sunrise -> out", not is_dt_in_golden_windows(datetime(2026, 6, 14, 5, 0, tzinfo=ET), gwc))
    check("05:20 ET after sunrise -> in", is_dt_in_golden_windows(datetime(2026, 6, 14, 5, 20, tzinfo=ET), gwc))

    # 5. Camera gating.
    check("usb-cam opted in", camera_uses_golden_windows("usb-cam", GW))
    check("dominator-cam opted in", camera_uses_golden_windows("dominator-cam", GW))
    check("s7-cam NOT affected", not camera_uses_golden_windows("s7-cam", GW))
    check("house-yard NOT affected", not camera_uses_golden_windows("house-yard", GW))
    check("disabled block -> nobody opted in", not camera_uses_golden_windows("usb-cam", {**GW, "enabled": False}))

    # 6. UTC-naive input is treated as UTC, not crashed.
    check("naive UTC 10:00Z == 06:00 ET in-window", is_dt_in_golden_windows(datetime(2026, 6, 14, 10, 0), gwc))

    print(f"\ngolden_windows tests: {_passed} passed")


if __name__ == "__main__":
    main()
