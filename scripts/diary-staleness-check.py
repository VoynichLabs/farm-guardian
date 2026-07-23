#!/usr/bin/env python3
# Author: Claude Opus 4.8 (1M)
# Date: 23-Jul-2026
# PURPOSE: The diary outage happened because the folder silently went stale for
#          weeks and nobody noticed until reel captions were visibly repeating
#          the same sentence. The nightly writer makes that less likely but does
#          not close the loop — if it dies quietly (claude CLI wedge, auth
#          hiccup, or a stretch of no farm chat) the captions starve again with
#          no warning. This canary measures the EXACT context the caption
#          pipeline receives (by importing daily_reel_runner's own eligibility
#          logic — not a copy that could drift) and posts a Discord alarm to
#          #farm-2026, mentioning the Boss, before captions go blank:
#            RED    - no usable entry right now / _load_farm_context() empty
#            YELLOW - last usable entry ages out within 3 days, or no diary file
#                     written in >2 days (the 20:00 writer is probably down)
#          Silent when healthy.
# SRP/DRY check: Pass — imports FARM_DIARY_DIR, FARM_CONTEXT_MAX_AGE_DAYS,
#          _diary_date, _RESOLVED_RE, _load_farm_context from
#          tools.pipeline.daily_reel_runner (verified light import, no side
#          effects) so the alarm can never diverge from what captions see.
#          Reuses tools/discord_harvester for the bot token + posting.

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import discord_harvester as dh  # noqa: E402
from tools.pipeline import daily_reel_runner as drr  # noqa: E402  (SSoT for eligibility)

BOSS_DISCORD_USER_ID = "293569238386606080"

# YELLOW thresholds.
WARN_AGEOUT_WITHIN_DAYS = 3   # last usable entry within N days of the freshness cliff
WRITER_SILENT_DAYS = 2        # no diary file written for N days => writer likely down

log = logging.getLogger("diary-staleness")

_RANK = {"OK": 0, "YELLOW": 1, "RED": 2}
_EMOJI = {"YELLOW": "🟡", "RED": "🔴"}


def assess() -> tuple[str, list[str], dict]:
    """Return (level, problems, stats). Mirrors _load_farm_context's own
    filters via the imported helpers so 'healthy' here == 'captions have
    context' there."""
    today = datetime.now(timezone.utc).date()
    max_age = drr.FARM_CONTEXT_MAX_AGE_DAYS

    files = list(drr.FARM_DIARY_DIR.glob("*.md")) if drr.FARM_DIARY_DIR.is_dir() else []
    entries = []  # (date, in_window, resolved)
    for p in files:
        d = drr._diary_date(p)
        if d is None:
            d = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).date()
        try:
            resolved = bool(drr._RESOLVED_RE.search(p.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            resolved = False
        entries.append((d, (today - d).days <= max_age, resolved))

    eligible_dates = sorted((d for d, inw, res in entries if inw and not res), reverse=True)
    # The ground truth: what the caption pipeline will actually build right now.
    context = drr._load_farm_context()

    level = "OK"
    problems: list[str] = []

    def bump(new_level: str, msg: str) -> None:
        nonlocal level
        if _RANK[new_level] > _RANK[level]:
            level = new_level
        problems.append(msg)

    if not entries:
        bump("RED", "no diary files exist at all.")
    else:
        newest_written = max(d for d, _, _ in entries)
        days_since_written = (today - newest_written).days
        if days_since_written > WRITER_SILENT_DAYS:
            bump("YELLOW", f"no new diary entry since {newest_written} "
                           f"({days_since_written}d ago) — the 20:00 writer is probably down.")

        if not context.strip():
            bump("RED", "captions have ZERO farm context right now — every in-window entry "
                        "is being filtered out (or none is in-window).")
        elif eligible_dates:
            days_left = max_age - (today - eligible_dates[0]).days
            if days_left <= WARN_AGEOUT_WITHIN_DAYS:
                bump("YELLOW", f"the last usable entry ({eligible_dates[0]}) ages out in "
                               f"{days_left}d — captions go generic after that.")

    stats = {
        "files": len(entries),
        "eligible": len(eligible_dates),
        "newest_eligible": str(eligible_dates[0]) if eligible_dates else "none",
        "context_chars": len(context),
    }
    return level, problems, stats


def build_alarm(level: str, problems: list[str], stats: dict) -> str:
    lines = [
        f"{_EMOJI[level]} <@{BOSS_DISCORD_USER_ID}> **Farm diary health: {level}**",
        "",
    ]
    lines += [f"• {p}" for p in problems]
    lines += [
        "",
        f"_State: {stats['eligible']} usable of {stats['files']} entries; "
        f"newest usable {stats['newest_eligible']}; caption context {stats['context_chars']} chars._",
        "Fix: mention the day's farm goings-on in #meet-the-lobsters (the 20:00 writer picks "
        "it up), or run `scripts/farm-diary-from-discord.py --force` now.",
    ]
    return "\n".join(lines)


def post_alarm(token: str, text: str) -> bool:
    resp = requests.post(
        f"{dh.DISCORD_API}/channels/{dh.CHANNEL_ID}/messages",
        headers=dh.discord_headers(token), json={"content": text[:1900]}, timeout=20,
    )
    if resp.status_code not in (200, 201):
        log.error("alarm post failed %s: %s", resp.status_code, resp.text[:200])
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the assessment + would-be alarm; post nothing.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    level, problems, stats = assess()
    log.info("assessment: level=%s stats=%s", level, stats)

    if level == "OK":
        log.info("diary healthy — no alarm.")
        return 0

    text = build_alarm(level, problems, stats)
    if args.dry_run:
        log.info("DRY-RUN would post to #farm-2026:\n%s", text)
        return 0

    token = dh.load_bot_token()
    post_alarm(token, text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
