# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Detect Instagram's challenge / action-block / re-auth dialogs on
#          the currently-open Playwright page. When any match is found we
#          screenshot the page, notify Discord via the farm-2026 webhook if
#          one is configured, write a 24h cooldown flag, and raise so the
#          engager aborts the session. This is the safety bedrock — never
#          push through a challenge.
#
# SRP/DRY check: Pass — one module, one responsibility: "is IG telling us
#                to stop right now, and if so, stop loudly."

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests
from playwright.sync_api import Page

import budget  # sibling module loaded via sys.path in engage.py

log = logging.getLogger("ig-engage.challenge")

SCREENSHOT_DIR = Path(
    "/Users/macmini/Documents/GitHub/farm-guardian/data/ig-engage/challenges"
)

# Case-insensitive substrings that mean "Meta wants us to stop".
CHALLENGE_STRINGS = [
    "suspicious activity",
    "suspicious login",
    "try again later",
    "we limit how often",
    "action blocked",
    "we restrict certain activity",
    "confirm it's you",
    "confirm its you",
    "please log in again",
    "enter the code we sent",
    "enter the code we texted",
    "enter the 6-digit code",
    "two-factor authentication",
    "verify your identity",
    "help us confirm",
    "help keep your account secure",
    "unusual login attempt",
]

COOLDOWN_SECONDS = 24 * 60 * 60  # 24h


class ChallengeHit(Exception):
    """Raised from inspect_page when IG shows a challenge dialog.
    The engager catches this, logs, and exits cleanly."""

    def __init__(self, matched: list[str], screenshot: Path | None):
        super().__init__(f"IG challenge detected: {matched}")
        self.matched = matched
        self.screenshot = screenshot


def _discord_webhook() -> str | None:
    return os.environ.get("DISCORD_FARM_2026_WEBHOOK")


def _notify_discord(message: str, screenshot: Path | None) -> None:
    url = _discord_webhook()
    if not url:
        log.warning("DISCORD_FARM_2026_WEBHOOK not set; challenge not notified to Discord")
        return
    try:
        files = None
        if screenshot and screenshot.exists():
            files = {"file": (screenshot.name, screenshot.read_bytes(), "image/png")}
        requests.post(
            url,
            data={"content": message, "username": "IG Engage Watchdog"},
            files=files,
            timeout=15,
        )
    except Exception as e:
        log.warning("discord notify failed: %s", e)


def inspect_page(page: Page) -> None:
    """Scan the currently-loaded page for challenge-dialog strings. On hit,
    screenshot, notify, set cooldown, raise ChallengeHit."""
    try:
        body_text = (page.locator("body").inner_text(timeout=2000) or "").lower()
    except Exception:
        # Page mid-navigation; skip this cycle, try again at the next action.
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
    log.error("challenge matched %s; cooldown until %s", matched, end_iso)

    msg_lines = [
        ":rotating_light: IG engagement paused — Meta challenge detected.",
        f"Matched phrases: {', '.join(matched[:3])}",
        f"Cooldown until: {end_iso}",
        f"Screenshot saved: {shot}" if shot else "No screenshot captured.",
        "Engager will refuse to run until the cooldown expires.",
        "To resume early after manual review: `rm /tmp/ig-engage-cooldown-until`",
    ]
    _notify_discord("\n".join(msg_lines), shot)

    raise ChallengeHit(matched, shot)
