#!/usr/bin/env python3
# Author: Claude Opus 5.5 Medium
# Date: 29-Oct-2026
# PURPOSE: Catches up the daily time-lapse Reels that were built but never posted. From 9-Aug-2026 the
#          duo2, house-yard-cam and jieli-dashcam lanes (and from 8-Oct the S7 lane, after Facebook killed the key) built
#          their MP4 every day and then died before publishing: the caption step leaked one sqlite handle
#          per frame and hit launchd's 256 open-file cap (fixed in v2.79.0). The MP4s are still on disk under
#          data/reels/<lane>/ (the weekly and monthly duo2/house-yard reels too). Each run posts the newest
#          unposted video per lane, at most one per lane,
#          and only while the shared IG quota keeps RESERVE_SLOTS free for the regular daily lanes.
#          Each run writes data/reels/backlog-status.json; while it says work is left, the gem story
#          publisher drops to publisher_daily_cap_during_reel_backlog so this lane gets room.
#
#          A day counts as done when the lane's own posted/<day>.json exists (the daily lane posted it) or
#          the day is in data/reels/backlog-posted.json (this script posted it). Captions say which day the
#          video shows, because it goes up weeks late. Run by LaunchAgent com.farmguardian.ig-timelapse-backlog
#          twice a day. Stops on its own when every camera is caught up.
# SRP/DRY check: Pass - reuses post_reel_to_ig, the lanes' fallback captions and hashtag wrapper, the shared
#          quota ledger and Discord webhook; only the "which old MP4 is next" logic is new.
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline import daily_reel_runner as runner  # noqa: E402

REELS_ROOT = REPO_ROOT / "data" / "reels"
STATE_FILE = REELS_ROOT / "backlog-posted.json"
# Read by tools/social/publisher.py: while caught_up is false, gem stories use a
# lower cap so this lane gets quota (Boss ranks time-lapse reels first).
STATUS_FILE = REELS_ROOT / "backlog-status.json"
BACKLOG_START = date(2026, 8, 9)  # first day the dense lanes stopped posting
RESERVE_SLOTS = 5  # rolling-24h IG slots kept free for the 4 daily reels + the carousel
NAME_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})")

# (lane, folder under data/reels, caption label, file_offset, marker_offset).
# file_offset: local date of the MP4's UTC build stamp -> day the video shows.
# marker_offset: the lane's posted/<key>.json key (the UTC date it ran, or for
# S7 the day covered) -> day the video shows.
#   S7 runs 21:00 local, covers that same day, keys by day covered: 0 / 0.
#   duo2 (15:00) and house-yard (09:00) cover yesterday, same UTC date: -1 / -1.
#   jieli ran 21:30 local covering that same day (old daylight window), but
#   21:30 EDT is already tomorrow in UTC, so its marker key is one ahead: 0 / -1.
# Last day each camera's catch-up may post. Everything after it is covered by
# the fixed daily lanes, whose markers from 29-Oct on use the new midnight-to-
# midnight window and so a different day offset. Hard-stopping here keeps the
# offsets above valid (they describe the old builds only) and means a new-style
# marker can never be misread into a duplicate. Tonight (29-Oct) the daily lanes
# posted: S7 covering 29-Oct, duo2 and house-yard covering 28-Oct, and the
# dashcam's 21:30 run covers 28-Oct.
LAST_DAY = {
    "s7": date(2026, 10, 28),
    "duo2-timelapse": date(2026, 10, 27),
    "house-yard-cam-timelapse": date(2026, 10, 27),
    "jieli-dashcam-timelapse": date(2026, 10, 27),
    # Weekly (Sundays) and monthly (1st) multi-day reels hit the same caption
    # leak from 16-Aug; the next scheduled runs (1-Nov) use the fixed code.
    "duo2-weekly-timelapse": date(2026, 10, 25),
    "house-yard-weekly-timelapse": date(2026, 10, 25),
    "duo2-monthly-timelapse": date(2026, 10, 1),
    "house-yard-monthly-timelapse": date(2026, 10, 1),
}

# What each lane's video spans, for the caption: a daily reel shows the day
# itself; weekly/monthly reels run in the morning and end the day before.
SPAN = {
    "duo2-weekly-timelapse": "week",
    "house-yard-weekly-timelapse": "week",
    "duo2-monthly-timelapse": "month",
    "house-yard-monthly-timelapse": "month",
}

CAMERAS = [
    (runner.S7_DAILY_REEL_LANE, "s7", "S7 nest-box camera", 0, 0),
    (runner.DUO2_TIMELAPSE_LANE, "duo2-timelapse", "Duo2 panoramic camera", -1, -1),
    (runner.HOUSE_YARD_CAM_TIMELAPSE_LANE, "house-yard-cam-timelapse", "Guardian yard camera", -1, -1),
    (runner.JIELI_DASHCAM_TIMELAPSE_LANE, "jieli-dashcam-timelapse", "Dashcam", 0, -1),
    # Multi-day lanes run late morning local (same UTC date), keyed by run date:
    # "day" below is simply the run date, and the caption describes the span.
    (runner.DUO2_WEEKLY_TIMELAPSE_LANE, "duo2-weekly-timelapse", "Duo2 panoramic camera", 0, 0),
    (runner.HOUSE_YARD_WEEKLY_TIMELAPSE_LANE, "house-yard-weekly-timelapse", "Guardian yard camera", 0, 0),
    (runner.DUO2_MONTHLY_TIMELAPSE_LANE, "duo2-monthly-timelapse", "Duo2 panoramic camera", 0, 0),
    (runner.HOUSE_YARD_MONTHLY_TIMELAPSE_LANE, "house-yard-monthly-timelapse", "Guardian yard camera", 0, 0),
]

log = logging.getLogger("ig-timelapse-backlog")


def _covered_day(mp4: Path, day_offset: int) -> date | None:
    match = NAME_STAMP.search(mp4.name)
    if not match:
        return None
    # File names carry the UTC build time; convert to local before taking the date.
    built_utc = datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}:{match.group(3)}:{match.group(4)}+00:00")
    return built_utc.astimezone().date() + timedelta(days=day_offset)


def _load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def _save_state(state: dict) -> None:
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2, sort_keys=True))
    temp.replace(STATE_FILE)


def _next_unposted(folder: str, file_offset: int, marker_offset: int, done: set[str]) -> tuple[date, Path] | None:
    lane_dir = REELS_ROOT / folder
    # The lanes key posted/<key>.json by the UTC day they RAN (S7 keys by the
    # day it covers). Shift by marker_offset to get the day shown.
    posted_days = set()
    for marker in (lane_dir / "posted").glob("*.json"):
        try:
            posted_days.add((date.fromisoformat(marker.stem) + timedelta(days=marker_offset)).isoformat())
        except ValueError:
            continue
    newest_per_day: dict[date, Path] = {}
    for mp4 in lane_dir.glob("20*/*.mp4"):
        day = _covered_day(mp4, file_offset)
        if day is None or day < BACKLOG_START or day > LAST_DAY[folder]:
            continue
        if day.isoformat() in posted_days or day.isoformat() in done:
            continue
        # Several builds can exist for one day (manual retries); keep the latest.
        if day not in newest_per_day or mp4.name > newest_per_day[day].name:
            newest_per_day[day] = mp4
    if not newest_per_day:
        return None
    day = max(newest_per_day)  # newest first: recent days are the most interesting
    return day, newest_per_day[day]


def _caption(lane, folder: str, label: str, day: date, db_path: Path) -> str:
    span = SPAN.get(folder, "day")
    last = day - timedelta(days=1)
    if span == "week":
        when = f"the week ending {last.strftime('%A')} {last.strftime('%B')} {last.day}"
    elif span == "month":
        when = f"the month up to {last.strftime('%B')} {last.day}"
    else:
        when = f"{day.strftime('%A')} {day.strftime('%B')} {day.day}"
    body = f"{lane.caption_fallback}\n\n{label}, {when}, from the archive."
    return runner._wrap_caption_with_hashtags(db_path, [], body)


def _remaining(state: dict) -> int:
    total = 0
    for _lane, folder, _label, file_offset, marker_offset in CAMERAS:
        done = set(state.get(folder, []))
        while (nxt := _next_unposted(folder, file_offset, marker_offset, done)) is not None:
            done.add(nxt[0].isoformat())
            total += 1
    return total


def _write_status(state: dict) -> None:
    remaining = _remaining(state)
    temp = STATUS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps({
        "caught_up": remaining == 0,
        "remaining": remaining,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2))
    temp.replace(STATUS_FILE)


def run(dry_run: bool) -> int:
    from tools.pipeline.ig_poster import post_reel_to_ig

    runner._load_env()
    cfg = runner._load_config()
    db_path = runner._resolve_repo_path(cfg["guardian_db_path"])
    state = _load_state()
    posted_any = []

    for lane, folder, label, file_offset, marker_offset in CAMERAS:
        done = set(state.get(folder, []))
        nxt = _next_unposted(folder, file_offset, marker_offset, done)
        if nxt is None:
            log.info("%s: caught up", folder)
            continue
        ledger_path, slots_free = runner._ledger_status(log)
        if slots_free <= RESERVE_SLOTS and not dry_run:
            log.info("quota: only %d slots free (reserve %d); stopping this run", slots_free, RESERVE_SLOTS)
            break
        day, mp4 = nxt
        caption = _caption(lane, folder, label, day, db_path)
        if dry_run:
            log.info("DRY RUN %s %s -> %s\n%s", folder, day, mp4.name, caption)
            continue
        result = post_reel_to_ig(reel_mp4_path=mp4, caption=caption, db_path=db_path, dry_run=False)
        if result.get("error"):
            log.error("%s %s: post failed: %s", folder, day, result["error"])
            continue
        # Save state straight after the publish so no later step can cause a repeat post.
        state.setdefault(folder, []).append(day.isoformat())
        _save_state(state)
        runner._append_ledger(ledger_path, lane, f"backlog-{day.isoformat()}", result, False)
        posted_any.append(f"{label} {day.strftime('%b')} {day.day}: {result.get('permalink', '')}")
        log.info("%s %s posted -> %s", folder, day, result.get("permalink"))

    _write_status(state)

    if posted_any:
        import os
        import requests

        webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
        if webhook:
            try:
                requests.post(webhook, json={"username": "farm-pipeline",
                                             "content": "⏪ Time-lapse catch-up posted:\n" + "\n".join(posted_any)},
                              timeout=15)
            except requests.RequestException as exc:
                log.warning("discord notice failed: %s", exc)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post unposted time-lapse Reels from the backlog.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    runner._raise_open_file_limit("ig-timelapse-backlog")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S%z")
    return run(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
