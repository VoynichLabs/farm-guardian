# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Main IG engagement session runner. Launches the Playwright
#          persistent profile built by bootstrap.py, checks the kill switch
#          and challenge cooldown, then runs a short session of:
#            - home feed scroll + selective likes
#            - targeted hashtag feed visit + selective likes + optional
#              contextual comment written by the local Qwen3.6 VLM
#            - a small number of story-tray emoji reactions
#          All within per-day caps (30 likes + 10 comments + 20 story
#          reactions) and per-session caps (15–25 actions, <5 minutes).
#
#          Session modes:
#            --headed            : open a visible Chromium window (default
#                                  for attended dry runs)
#            --headless          : no window (for the LaunchAgent)
#            --likes-only        : no comments this run (safe attended
#                                  mode while Boss watches)
#            --dry-run           : no likes, no comments, no reactions;
#                                  just exercise the navigation + selectors
#                                  and log what we WOULD do
#
# SRP/DRY check: Pass — orchestration only. Each primitive lives in
#                primitives.py; budget / challenge / comment copy live in
#                their own modules. This file wires them together.

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from pathlib import Path

# Make sibling modules importable without a package (dir name has a dash).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from playwright.sync_api import sync_playwright

import budget  # noqa: E402
import challenge  # noqa: E402
import comment_writer  # noqa: E402
import primitives  # noqa: E402

log = logging.getLogger("ig-engage.engage")

PROFILE_DIR = Path.home() / "Library" / "Application Support" / "farm-ig-engage" / "profile"
MARKER = Path.home() / "Library" / "Application Support" / "farm-ig-engage" / "bootstrap-ok.json"
LOG_DIR = Path("/Users/macmini/Documents/GitHub/farm-guardian/data/ig-engage")

TARGET_HASHTAGS = [
    "backyardchickens",
    "turkeypoults",
    "easteregger",
    "crestedchicken",
    "yorkielovers",
    "hamptonct",
    "easternct",
    "newenglandfarm",
]

# Extra stealth patches beyond what bootstrap already applies.
STEALTH_INIT = r"""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {
  get: () => [1,2,3,4,5].map(() => ({0: {type: 'application/pdf'}, length: 1}))
});
window.chrome = window.chrome || { runtime: {} };
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) => (
    parameters && parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
}
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


def run_session(
    headed: bool,
    likes_only: bool,
    dry_run: bool,
    max_actions: int,
    max_minutes: float,
) -> dict:
    """One attended-or-unattended engagement session. Returns a summary dict."""
    state = budget.load_state()
    summary = {
        "started_at": int(time.time()),
        "likes_done": 0,
        "comments_done": 0,
        "story_reactions_done": 0,
        "posts_seen": 0,
        "hashtag_visited": None,
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
            # 1) home feed pass
            primitives.goto_home(page)
            primitives.human_sleep(4.0, 8.0)

            actions_done = 0
            scrolls_left = 6
            while (
                actions_done < max_actions
                and time.time() < deadline
                and scrolls_left > 0
            ):
                scrolls_left -= 1
                articles = primitives.find_home_posts(page, max_posts=4)
                for art in articles:
                    if actions_done >= max_actions or time.time() >= deadline:
                        break
                    summary["posts_seen"] += 1
                    # Like roughly 1 in 3 posts we see on the home feed.
                    if (
                        random.random() < 0.35
                        and budget.remaining(state, "like") > 0
                    ):
                        if dry_run:
                            log.info("DRY-RUN would like feed post")
                            summary["likes_done"] += 1
                            actions_done += 1
                            primitives.human_sleep()
                            continue
                        if primitives.like_current_post(page, art):
                            budget.record(state, "like")
                            summary["likes_done"] += 1
                            actions_done += 1
                            log.info(
                                "liked feed post (%d/%d today)",
                                state.counts.get("like", 0),
                                budget.DEFAULT_CAPS["like"],
                            )
                            primitives.human_sleep()
                            challenge.inspect_page(page)
                primitives.scroll_feed(page, distance_px=random.randint(700, 1100))
                primitives.human_sleep(3.0, 6.0)

            # 2) one targeted hashtag visit
            if time.time() < deadline and actions_done < max_actions:
                tag = random.choice(TARGET_HASHTAGS)
                summary["hashtag_visited"] = tag
                log.info("visiting #%s", tag)
                primitives.visit_hashtag(page, tag)
                primitives.human_sleep(3.0, 6.0)
                # On tag feeds, individual post grids appear first — click
                # into one, then proceed. We simulate by pressing the first
                # tile.
                try:
                    tiles = page.locator("main a[href*='/p/']").element_handles()
                except Exception:
                    tiles = []
                if tiles:
                    random.choice(tiles[:6]).click(timeout=3000)
                    page.wait_for_timeout(random.randint(1500, 2500))
                    challenge.inspect_page(page)
                    # On the post page there's usually one article.
                    articles = primitives.find_home_posts(page, max_posts=1)
                    if articles:
                        art = articles[0]
                        summary["posts_seen"] += 1
                        if (
                            budget.remaining(state, "like") > 0
                            and random.random() < 0.5
                        ):
                            if dry_run:
                                log.info("DRY-RUN would like hashtag post")
                                summary["likes_done"] += 1
                                actions_done += 1
                            elif primitives.like_current_post(page, art):
                                budget.record(state, "like")
                                summary["likes_done"] += 1
                                actions_done += 1
                                log.info("liked #%s post", tag)
                                primitives.human_sleep()
                                challenge.inspect_page(page)
                        # Comment path — only if not likes_only and we have
                        # budget left.
                        if (
                            not likes_only
                            and not dry_run
                            and budget.remaining(state, "comment") > 0
                            and random.random() < 0.5
                        ):
                            img = primitives.fetch_image_bytes(page, art)
                            cap = primitives.extract_caption(art)
                            if img and cap:
                                text = comment_writer.write_comment(
                                    img, cap, recently_posted=recent_comments
                                )
                                if text:
                                    log.info("drafted comment: %r", text)
                                    if primitives.comment_on_post(page, art, text):
                                        budget.record(state, "comment")
                                        summary["comments_done"] += 1
                                        actions_done += 1
                                        recent_comments.add(text.lower())
                                        log.info("posted comment on #%s", tag)
                                        primitives.human_sleep(8.0, 15.0)
                                        challenge.inspect_page(page)

            # 3) story reactions — cheap & high-reciprocity
            if time.time() < deadline and budget.remaining(state, "story_react") > 0:
                react_count = random.randint(1, 3)
                for _ in range(react_count):
                    if budget.remaining(state, "story_react") <= 0 or time.time() >= deadline:
                        break
                    if dry_run:
                        log.info("DRY-RUN would react to a story")
                        summary["story_reactions_done"] += 1
                        continue
                    if primitives.open_first_story_and_react(page):
                        budget.record(state, "story_react")
                        summary["story_reactions_done"] += 1
                        log.info("reacted to a story")
                        primitives.human_sleep(5.0, 10.0)
                        challenge.inspect_page(page)

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
    p.add_argument("--headed", action="store_true", help="visible window")
    p.add_argument("--headless", action="store_true", help="no window")
    p.add_argument("--likes-only", action="store_true", help="no comments")
    p.add_argument("--dry-run", action="store_true", help="no writes; navigate only")
    p.add_argument("--max-actions", type=int, default=20)
    p.add_argument("--max-minutes", type=float, default=4.5)
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
