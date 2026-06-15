# Author: Claude Opus 4.8 (Bubba sub-agent)
# Date: 14-June-2026
# PURPOSE: Shared "golden window" helper for the farm time-lapse pipeline. Defines
#          the two daily activity windows the Boss cares about (morning
#          sunrise->09:00, evening 19:30->20:30) and answers a single question for
#          both consumers: "is this timestamp inside a golden window?" Used by
#          (1) tools/pipeline/ig_selection.py to keep ONLY in-window frames in the
#          usb-cam / dominator-cam time-lapse reels, and (2)
#          tools/pipeline/orchestrator.py to capture THICK inside the windows and a
#          SPARSE heartbeat outside them. Sunrise is computed dynamically (NOAA
#          sunrise equation) for the farm lat/long so the morning window tracks the
#          season — no hardcoded morning hour. astral is not installed in the venv,
#          so this is a dependency-light stdlib-only implementation (math + zoneinfo).
# SRP/DRY check: Pass — single responsibility is "local-time window membership +
#                solar sunrise/sunset minute". The minute-granular window primitive
#                (minute_in_window) and the sunrise calc live here ONCE and are
#                imported by both ig_selection (selection) and orchestrator
#                (capture). No duplication of the solar math or the window logic.

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo

# Standard atmospheric-refraction-corrected solar altitude for sunrise/sunset
# (the sun's geometric center is 0.833 deg below the horizon at first/last light).
_SUNRISE_ALTITUDE_DEG = -0.833
_OBLIQUITY_DEG = 23.4397  # Earth's axial tilt (NOAA constant)
_J2000 = 2451545.0        # Julian date of the J2000.0 epoch (2000-01-01 12:00 TT)
_UNIX_EPOCH_JD = 2440587.5  # Julian date of 1970-01-01 00:00 UTC


def _julian_day_number(d: date) -> int:
    """Gregorian calendar date -> Julian Day Number (references 12:00 UT)."""
    a = (14 - d.month) // 12
    y = d.year + 4800 - a
    m = d.month + 12 * a - 3
    return (
        d.day
        + (153 * m + 2) // 5
        + 365 * y
        + y // 4
        - y // 100
        + y // 400
        - 32045
    )


def _solar_event_utc(
    d: date, latitude: float, longitude: float, *, sunset: bool
) -> Optional[datetime]:
    """Return the UTC datetime of sunrise (or sunset) for the given local-ish
    calendar date, via the NOAA / Wikipedia 'sunrise equation'.

    longitude is signed east-positive (the farm is -71.9789, i.e. west);
    latitude is north-positive. Returns None for polar day/night (no event).
    Accurate to ~1 minute, which is far finer than our window boundaries need.
    """
    # Mean solar noon (in days past the J2000 epoch). Solar noon in UTC is later
    # than 12:00 for western longitudes, so the longitude term is -longitude/360
    # using the SIGNED east-positive longitude (the farm's -71.9789 -> +0.1999d,
    # i.e. solar noon ~16:48 UTC / ~12:48 ET). Verified against the NOAA
    # reference for the farm (see __main__ self-test).
    n = float(_julian_day_number(d)) - _J2000 + 0.0008
    j_star = n - longitude / 360.0

    M = (357.5291 + 0.98560028 * j_star) % 360.0
    M_rad = math.radians(M)
    C = (
        1.9148 * math.sin(M_rad)
        + 0.0200 * math.sin(2 * M_rad)
        + 0.0003 * math.sin(3 * M_rad)
    )
    lam = (M + C + 180.0 + 102.9372) % 360.0
    lam_rad = math.radians(lam)

    j_transit = (
        _J2000
        + j_star
        + 0.0053 * math.sin(M_rad)
        - 0.0069 * math.sin(2 * lam_rad)
    )

    sin_delta = math.sin(lam_rad) * math.sin(math.radians(_OBLIQUITY_DEG))
    delta = math.asin(sin_delta)

    lat_rad = math.radians(latitude)
    cos_omega = (
        math.sin(math.radians(_SUNRISE_ALTITUDE_DEG)) - math.sin(lat_rad) * math.sin(delta)
    ) / (math.cos(lat_rad) * math.cos(delta))
    if cos_omega > 1.0 or cos_omega < -1.0:
        return None  # polar night (no sunrise) or polar day (no sunset)

    omega = math.degrees(math.acos(cos_omega))
    j_event = j_transit + (omega / 360.0 if sunset else -omega / 360.0)

    unix_seconds = (j_event - _UNIX_EPOCH_JD) * 86400.0
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=unix_seconds)


@lru_cache(maxsize=512)
def _solar_minute_cached(
    iso_date: str, latitude: float, longitude: float, tz_name: str, sunset: bool
) -> Optional[int]:
    d = date.fromisoformat(iso_date)
    event_utc = _solar_event_utc(d, latitude, longitude, sunset=sunset)
    if event_utc is None:
        return None
    local = event_utc.astimezone(ZoneInfo(tz_name))
    return local.hour * 60 + local.minute


def sunrise_minute(
    d: date, latitude: float, longitude: float, tz_name: str
) -> Optional[int]:
    """Local-time minute-of-day of sunrise for date `d` (None for polar night)."""
    return _solar_minute_cached(d.isoformat(), latitude, longitude, tz_name, False)


def sunset_minute(
    d: date, latitude: float, longitude: float, tz_name: str
) -> Optional[int]:
    """Local-time minute-of-day of sunset for date `d` (None for polar day)."""
    return _solar_minute_cached(d.isoformat(), latitude, longitude, tz_name, True)


def minute_in_window(minute: int, start: int, end: int) -> bool:
    """Minute-granular membership test for a [start, end) local-minute window.

    start/end are minute-of-day in [0, 1440). Supports windows that wrap past
    midnight (start > end). A zero-width window (start == end) matches nothing —
    callers that want 'whole day' semantics must guard for that themselves
    (see ig_selection._is_local_hour_in_window, which preserves its legacy
    start==end => full-day behavior before delegating here).
    """
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def _resolve_token(token, sunrise_min: Optional[int], sunset_min: Optional[int]) -> Optional[int]:
    """Resolve a window boundary token to a minute-of-day.

    Accepts the literal strings 'sunrise'/'sunset' (resolved dynamically),
    an 'HH:MM' string, or an int minute-of-day. Returns None when a solar
    token is requested but undefined (polar day/night)."""
    if isinstance(token, (int, float)):
        return int(token)
    tok = str(token).strip().lower()
    if tok == "sunrise":
        return sunrise_min
    if tok == "sunset":
        return sunset_min
    if ":" in tok:
        hh, mm = tok.split(":", 1)
        return int(hh) * 60 + int(mm)
    # Bare integer string fallback ("540").
    return int(tok)


def resolve_windows(local_date: date, gw_cfg: dict) -> list[tuple[int, int]]:
    """Resolve the configured golden windows into concrete (start_min, end_min)
    minute-of-day pairs for the given local date, computing sunrise/sunset
    dynamically where the config uses those tokens. Windows whose boundary is an
    undefined solar event (polar) are dropped."""
    tz_name = gw_cfg.get("timezone", "America/New_York")
    lat = float(gw_cfg.get("latitude", 41.7558))
    lon = float(gw_cfg.get("longitude", -71.9789))
    windows_cfg = gw_cfg.get("windows", []) or []

    # Only compute solar events if some window actually references them.
    needs_sun = any(
        str(w.get("start")).strip().lower() in ("sunrise", "sunset")
        or str(w.get("end")).strip().lower() in ("sunrise", "sunset")
        for w in windows_cfg
    )
    sr = sunrise_minute(local_date, lat, lon, tz_name) if needs_sun else None
    ss = sunset_minute(local_date, lat, lon, tz_name) if needs_sun else None

    resolved: list[tuple[int, int]] = []
    for w in windows_cfg:
        start = _resolve_token(w.get("start"), sr, ss)
        end = _resolve_token(w.get("end"), sr, ss)
        if start is None or end is None:
            continue
        resolved.append((start, end))
    return resolved


def is_dt_in_golden_windows(dt_aware: datetime, gw_cfg: dict) -> bool:
    """True when the tz-aware datetime falls inside any configured golden window.

    Converts to the configured local timezone, computes that local date's
    windows (with dynamic sunrise/sunset), and tests minute-of-day membership.
    Returns False when no windows are configured/resolvable.
    """
    tz_name = gw_cfg.get("timezone", "America/New_York")
    if dt_aware.tzinfo is None:
        dt_aware = dt_aware.replace(tzinfo=timezone.utc)
    local = dt_aware.astimezone(ZoneInfo(tz_name))
    minute = local.hour * 60 + local.minute
    return any(
        minute_in_window(minute, s, e) for s, e in resolve_windows(local.date(), gw_cfg)
    )


def camera_uses_golden_windows(camera_id: str, gw_cfg: Optional[dict]) -> bool:
    """Whether golden-window filtering/capture applies to this camera.

    Gated on gw_cfg['enabled'] and membership in gw_cfg['cameras'] (a list of
    camera ids). Returns False when gw_cfg is missing/disabled so callers fall
    back cleanly to legacy behavior for s7/mba/gwtc/house-yard."""
    if not gw_cfg or not gw_cfg.get("enabled", False):
        return False
    cams = gw_cfg.get("cameras", []) or []
    if isinstance(cams, str):
        cams = [c.strip() for c in cams.split(",") if c.strip()]
    return camera_id in set(cams)


def camera_golden_cfg(camera_id: str, gw_cfg: dict) -> dict:
    """Return the effective golden config for a camera: the shared defaults with
    any per-camera override block in gw_cfg['per_camera'][camera_id] overlaid.
    This is what makes the windows PER-LANE configurable while staying DRY —
    usb-cam and dominator-cam share defaults unless one is given an override."""
    merged = dict(gw_cfg)
    per_cam = (gw_cfg.get("per_camera") or {}).get(camera_id) or {}
    merged.update(per_cam)
    return merged


if __name__ == "__main__":
    # Self-test / reference check. Verifies the NOAA sunrise calc against a known
    # value for the farm (41.7558 N, 71.9789 W) and exercises window membership.
    FARM_LAT, FARM_LON, TZ = 41.7558, -71.9789, "America/New_York"

    sr = sunrise_minute(date(2026, 6, 14), FARM_LAT, FARM_LON, TZ)
    ss = sunset_minute(date(2026, 6, 14), FARM_LAT, FARM_LON, TZ)
    print(f"2026-06-14 farm sunrise: {sr // 60:02d}:{sr % 60:02d} ET (minute {sr})")
    print(f"2026-06-14 farm sunset : {ss // 60:02d}:{ss % 60:02d} ET (minute {ss})")
    # Mid-June sunrise at ~41.8N is ~05:11 ET; assert within a 6-minute tolerance.
    assert 305 <= sr <= 317, f"sunrise {sr} outside expected ~05:11 ET band"
    # Winter solstice sunrise should be far later (~07:13 ET).
    sr_winter = sunrise_minute(date(2026, 12, 21), FARM_LAT, FARM_LON, TZ)
    print(f"2026-12-21 farm sunrise: {sr_winter // 60:02d}:{sr_winter % 60:02d} ET")
    assert 425 <= sr_winter <= 445, f"winter sunrise {sr_winter} off"

    gw = {
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
    # 06:00 ET (after sunrise, before 09:00) -> in morning window.
    t_morning = datetime(2026, 6, 14, 6, 0, tzinfo=ZoneInfo(TZ))
    # 13:00 ET midday -> NOT in any window.
    t_midday = datetime(2026, 6, 14, 13, 0, tzinfo=ZoneInfo(TZ))
    # 20:00 ET -> in evening window.
    t_evening = datetime(2026, 6, 14, 20, 0, tzinfo=ZoneInfo(TZ))
    # 05:00 ET -> before sunrise (~05:11) -> NOT in window (pre-dawn dark).
    t_predawn = datetime(2026, 6, 14, 5, 0, tzinfo=ZoneInfo(TZ))
    assert is_dt_in_golden_windows(t_morning, gw) is True
    assert is_dt_in_golden_windows(t_midday, gw) is False
    assert is_dt_in_golden_windows(t_evening, gw) is True
    assert is_dt_in_golden_windows(t_predawn, gw) is False
    # camera gating
    assert camera_uses_golden_windows("usb-cam", gw) is True
    assert camera_uses_golden_windows("s7-cam", gw) is False
    print("golden_windows self-test: ALL PASS")
