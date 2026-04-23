# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Main Nextdoor ENGAGEMENT session runner (the inbound lane —
#          liking and occasional commenting on neighbors' posts). The
#          OUTBOUND cross-post lane lives in crosspost.py and fires on a
#          different schedule.
#
#          This runner:
#            - refuses to run if /tmp/nextdoor-off is present
#            - refuses to run if /tmp/nextdoor-cooldown-until is in the future
#            - launches Playwright at ~/Library/Application Support/
#              farm-nextdoor/profile/
#            - applies the same stealth patches the IG engager uses
#            - scrolls the news feed, likes ~1 in 3 neighbor posts, and
#              (if --likes-only not set) leaves 1–3 contextual comments
#              per session
#            - logs to data/nextdoor/engage.log
#
# SRP/DRY check: Pass — orchestration only.
#
# STATUS (2026-04-23): scaffolded; will not run productively until the
# selectors in primitives.py are filled from a real attended session.
# The runner itself works — it's the DOM selectors that are TODO.

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from playwright.sync_api import sync_playwright  # noqa: E402

import budget  # noqa: E402
import challenge  # noqa: E402
import comment_writer  # noqa: E402
import primitives  # noqa: E402

log = logging.getLogger("nextdoor.engage")

PROFILE_DIR = Path.home() / "Library" / "Application Support" / "farm-nextdoor" / "profile"
MARKER = Path.home() / "Library" / "Application Support" / "farm-nextdoor" / "bootstrap-ok.json"
LOG_DIR = Path("/Users/macmini/Documents/GitHub/farm-guardian/data/nextdoor")

STEALTH_INIT = r"""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {
  get: () => [1,2,3,4,5].map(() => ({0: {type: 'application/pdf'}, length: 1}))
});
window.chrome = window.chrome || { runtime: {} };
"""


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_DIR / "engage.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    root.addHandler(logging.StreamHandler())


def gate_checks() -> tuple[bool, str]:
    if budget.kill_switch_on():
        return False, f"kill switch present at {budget.KILL_SWITCH}"
    in_cd, end = budget.in_cooldown()
    if in_cd:
        mins = max(1, int((end - time.time()) / 60))
        return False, f"in challenge cooldown for another {mins} min"
    if not MARKER.exists():
        return False, f"bootstrap marker missing at {MARKER}; run bootstrap.py first"
    return True, "ok"


def run_session(headed: bool, likes_only: bool, dry_run: bool,
                max_actions: int, max_minutes: float) -> dict:
    state = budget.load_state()
    summary = {
        "started_at": int(time.time()),
        "likes_done": 0,
        "comments_done": 0,
        "posts_seen": 0,
        "ended_reason": "max_actions",
    }
    recent_comments: set[str] = set()
    deadline = time.time() + max_minutes * 60.0

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=(not headed),
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )
        ctx.add_init_script(STEALTH_INIT)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        try:
            primitives.goto_feed(page)
            primitives.human_sleep(4.0, 8.0)

            actions_done = 0
            scrolls_left = 5
            while (
                actions_done < max_actions
                and time.time() < deadline
                and scrolls_left > 0
            ):
                scrolls_left -= 1
                articles = primitives.find_feed_posts(page, max_posts=4)
                if not articles:
                    log.info("no posts found in feed — selector work pending")
                for art in articles:
                    if actions_done >= max_actions or time.time() >= deadline:
                        break
                    summary["posts_seen"] += 1
                    # Like ~1 in 3 neighbor posts.
                    if (
                        random.random() < 0.33
                        and budget.remaining(state, "like") > 0
                    ):
                        if dry_run:
                            log.info("DRY-RUN would like feed post")
                            summary["likes_done"] += 1
                            actions_done += 1
                            primitives.human_sleep()
                            continue
                        if primitives.like_post(page, art):
                            budget.record(state, "like")
                            summary["likes_done"] += 1
                            actions_done += 1
                            log.info("liked feed post (%d/%d today)",
                                     state.counts.get("like", 0),
                                     budget.DEFAULT_CAPS["like"])
                            primitives.human_sleep()
                            challenge.inspect_page(page)
                    # Comment on ~1 in 6 posts when not likes_only.
                    if (
                        not likes_only
                        and not dry_run
                        and random.random() < 0.15
                        and budget.remaining(state, "comment") > 0
                    ):
                        # For an MVP we'll use the fallback library (image
                        # fetch primitive intentionally omitted until first
                        # attended session tells us what the image URL
                        # attribute looks like on Nextdoor posts).
                        text = comment_writer.write_comment(
                            image_bytes=b"", caption="",
                            recently_posted=recent_comments
                        )
                        if text and primitives.comment_on_post(page, art, text):
                            budget.record(state, "comment")
                            summary["comments_done"] += 1
                            actions_done += 1
                            recent_comments.add(text.lower())
                            log.info("commented on feed post")
                            primitives.human_sleep(8.0, 14.0)
                            challenge.inspect_page(page)
                primitives.scroll_feed(page, distance_px=random.randint(650, 1000))
                primitives.human_sleep(3.0, 6.0)

            summary["ended_reason"] = "deadline" if time.time() >= deadline else "complete"

        except challenge.ChallengeHit as ch:
            log.error("session aborted on challenge: %s", ch)
            summary["ended_reason"] = f"challenge:{ch.matched}"
        finally:
            try:
                ctx.close()
            except Exception:
                pass

    return summary


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--headed", action="store_true")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--likes-only", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-actions", type=int, default=8)
    p.add_argument("--max-minutes", type=float, default=3.0)
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    headed = args.headed or not args.headless

    setup_logging()

    ok, why = gate_checks()
    if not ok:
        log.error("gate refused: %s", why)
        return 1

    summary = run_session(
        headed=headed,
        likes_only=args.likes_only,
        dry_run=args.dry_run,
        max_actions=args.max_actions,
        max_minutes=args.max_minutes,
    )
    log.info("session summary: %s", json.dumps(summary))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
