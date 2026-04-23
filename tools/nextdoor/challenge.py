# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Detect Nextdoor's challenge / rate-limit / re-auth prompts on the
#          currently-open Playwright page. Mirrors tools/ig-engage/challenge.py
#          but with a Nextdoor-specific string list. The initial list below is
#          SEEDED FROM GUESSES based on Nextdoor's public help docs — extend
#          it with real strings observed during the first attended session.
#          On any match we screenshot, notify Discord (if webhook is set),
#          write a 24h cooldown flag to /tmp/nextdoor-cooldown-until, and
#          raise ChallengeHit so the engager aborts cleanly.
#
#          When a new challenge string is spotted in the wild, add it to
#          CHALLENGE_STRINGS below — don't fork logic.
#
# SRP/DRY check: Pass — only responsibility is "did Nextdoor just tell us to
#                stop, and if so, stop loudly." Kept separate from the IG
#                challenge module so dialog-text additions on one platform
#                don't drift into the other.

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import Page

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import budget  # sibling module

log = logging.getLogger("nextdoor.challenge")

SCREENSHOT_DIR = Path(
    "/Users/macmini/Documents/GitHub/farm-guardian/data/nextdoor/challenges"
)

# Case-insensitive substrings we treat as "Nextdoor wants us to stop".
# Extend this when a real event surfaces new copy. Do NOT remove entries that
# you think are redundant — a duplicate match costs nothing but a missed
# match costs a 7-day ban.
CHALLENGE_STRINGS = [
    # Rate-limit / action-block family
    "temporarily restricted",
    "temporarily unavailable",
    "action unavailable",
    "too many requests",
    "you're posting too often",
    "slow down",
    "we've detected unusual activity",
    # Community-guidelines / content-review family
    "review our community guidelines",
    "violates our guidelines",
    "content was removed",
    "your post was removed",
    # Re-auth / verification family
    "please verify",
    "we couldn't verify",
    "verify your identity",
    "confirm it's you",
    "sign in again",
    "log in again",
    "session has expired",
    "for your security",
    # 2FA challenge family (if they ever prompt, we stop — don't try to solve)
    "enter the code we sent",
    "two-factor",
    "authentication code",
]

COOLDOWN_SECONDS = 24 * 60 * 60  # 24h, same as IG


class ChallengeHit(Exception):
    def __init__(self, matched: list[str], screenshot: Path | None):
        super().__init__(f"Nextdoor challenge detected: {matched}")
        self.matched = matched
        self.screenshot = screenshot


def _discord_webhook() -> str | None:
    return os.environ.get("DISCORD_FARM_2026_WEBHOOK")


def _notify_discord(message: str, screenshot: Path | None) -> None:
    url = _discord_webhook()
    if not url:
        log.warning("DISCORD_FARM_2026_WEBHOOK not set; challenge not notified")
        return
    try:
        files = None
        if screenshot and screenshot.exists():
            files = {"file": (screenshot.name, screenshot.read_bytes(), "image/png")}
        requests.post(
            url,
            data={"content": message, "username": "Nextdoor Engage Watchdog"},
            files=files,
            timeout=15,
        )
    except Exception as e:
        log.warning("discord notify failed: %s", e)


def inspect_page(page: Page) -> None:
    try:
        body_text = (page.locator("body").inner_text(timeout=2000) or "").lower()
    except Exception:
        return

    matched = [s for s in CHALLENGE_STRINGS if s in body_text]
    if not matched:
        return

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    shot = SCREENSHOT_DIR / f"challenge-{int(time.time())}.png"
    try:
        page.screenshot(path=str(shot), full_page=False)
    except Exception:
        shot = None

    end_epoch = budget.set_cooldown(COOLDOWN_SECONDS)
    end_iso = time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(end_epoch))
    log.error("Nextdoor challenge matched %s; cooldown until %s", matched, end_iso)

    msg_lines = [
        ":rotating_light: Nextdoor automation paused — challenge detected.",
        f"Matched phrases: {', '.join(matched[:3])}",
        f"Cooldown until: {end_iso}",
        f"Screenshot: {shot}" if shot else "No screenshot captured.",
        "Engager will refuse to run until the cooldown expires.",
        "To resume early after manual review: `rm /tmp/nextdoor-cooldown-until`",
    ]
    _notify_discord("\n".join(msg_lines), shot)

    raise ChallengeHit(matched, shot)
