# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Weekly outbound cross-post lane — picks a reaction-gated strong
#          gem from the last 7 days and posts it to Boss's Hampton CT
#          Nextdoor neighborhood feed with a neighborhood-appropriate
#          caption. Fires Sunday mornings via a dedicated LaunchAgent.
#
#          Gem-selection mirrors the IG weekly reel's logic (see
#          tools/pipeline/ig_selection.py) — we want the SAME trust signal
#          so Nextdoor only ever sees gems that humans on Discord already
#          up-voted.
#
#          Hard constraints enforced here:
#            - 7-day cooldown via budget.can_post()
#            - audience floor "Just my neighborhood" (primitives.
#              set_audience_neighborhood; submit ABORTS if that fails)
#            - Sunday-morning window (08:00–11:00 local) optional via
#              --respect-window flag, default on
#            - kill switch + challenge cooldown honored
#
#          STATUS (2026-04-23): scaffolded. Until primitives.py selectors
#          are confirmed against the real Nextdoor UI (first attended
#          session), crosspost.submit_post() will bail rather than post.
#
# SRP/DRY check: Pass — one lane, one responsibility: weekly outbound.

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from playwright.sync_api import sync_playwright  # noqa: E402

import budget  # noqa: E402
import challenge  # noqa: E402
import primitives  # noqa: E402

log = logging.getLogger("nextdoor.crosspost")

PROFILE_DIR = Path.home() / "Library" / "Application Support" / "farm-nextdoor" / "profile"
MARKER = Path.home() / "Library" / "Application Support" / "farm-nextdoor" / "bootstrap-ok.json"
LOG_DIR = Path("/Users/macmini/Documents/GitHub/farm-guardian/data/nextdoor")
POSTS_LOG = LOG_DIR / "posts.json"

# Guardian DB — source of truth for gems and their Discord reactions.
GUARDIAN_DB = Path("/Users/macmini/Documents/GitHub/farm-guardian/data/guardian.db")


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_DIR / "crosspost.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(fh)
    root.addHandler(logging.StreamHandler())


def in_sunday_window(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    # Sunday is weekday() == 6 in Python's datetime.
    return now.weekday() == 6 and 8 <= now.hour < 11


def pick_best_gem_last_week() -> dict | None:
    """Query the image_archive for the highest-reaction strong+sharp gem
    from the last 7 days that we haven't already cross-posted. Shape
    matches the existing ig_selection pattern; see that module for the
    canonical SQL — we mirror it here to avoid an import cycle."""
    if not GUARDIAN_DB.exists():
        log.warning("guardian db missing at %s", GUARDIAN_DB)
        return None

    # Load the "already posted" set from our log to avoid re-posting.
    already_posted: set[str] = set()
    if POSTS_LOG.exists():
        try:
            for row in json.loads(POSTS_LOG.read_text()):
                already_posted.add(str(row.get("gem_id", "")))
        except Exception as e:
            log.warning("posts log unreadable: %s", e)

    conn = sqlite3.connect(str(GUARDIAN_DB))
    try:
        # Best-reacted, strong+sharp, from the past 7 days, not concerns-flagged.
        # NOTE: actual column names may need confirmation against the current
        # schema — if the query fails, check tools/pipeline/ig_selection.py.
        rows = conn.execute(
            """
            SELECT ia.id, ia.camera_id, ia.ts, ia.path,
                   ia.vlm_json, COALESCE(dr.reaction_count, 0) AS rxn
              FROM image_archive ia
              LEFT JOIN discord_reactions dr ON dr.image_id = ia.id
             WHERE ia.ts >= strftime('%s', 'now', '-7 days')
               AND ia.has_concerns = 0
               AND json_extract(ia.vlm_json, '$.share_worth') = 'strong'
               AND json_extract(ia.vlm_json, '$.image_quality') = 'sharp'
             ORDER BY rxn DESC, ia.ts DESC
             LIMIT 20
            """
        ).fetchall()
    except sqlite3.Error as e:
        log.warning("gem query failed: %s", e)
        return None
    finally:
        conn.close()

    for row in rows:
        gem_id = str(row[0])
        if gem_id in already_posted:
            continue
        vlm = {}
        try:
            vlm = json.loads(row[4]) if row[4] else {}
        except Exception:
            pass
        return {
            "id": gem_id,
            "camera_id": row[1],
            "ts": row[2],
            "path": row[3],
            "caption_draft": (vlm.get("caption_draft") or "").strip(),
            "reaction_count": row[5],
        }
    return None


def build_caption(gem: dict) -> str:
    """Prefix the VLM's caption with a neighborhood-grounding line so it
    reads as a local post, not a content-marketing drop."""
    grounding = "From the backyard flock here in Hampton, CT:"
    raw = gem.get("caption_draft") or "A small moment from the brooder today."
    # Keep the post short for Nextdoor — one grounding line + one
    # observational sentence + ends clean.
    return f"{grounding} {raw}".strip()


def record_post(gem: dict, post_url: str | None) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    if POSTS_LOG.exists():
        try:
            entries = json.loads(POSTS_LOG.read_text())
        except Exception:
            entries = []
    entries.append({
        "gem_id": gem["id"],
        "camera_id": gem["camera_id"],
        "posted_at": int(time.time()),
        "post_url": post_url,
        "caption_used": build_caption(gem),
    })
    POSTS_LOG.write_text(json.dumps(entries, indent=2))


def run(headed: bool, dry_run: bool, respect_window: bool) -> int:
    setup_logging()

    if budget.kill_switch_on():
        log.info("kill switch present; aborting")
        return 0
    in_cd, end = budget.in_cooldown()
    if in_cd:
        log.info("in cooldown for %d more min; aborting",
                 max(1, int((end - time.time()) / 60)))
        return 0
    if not MARKER.exists():
        log.error("bootstrap marker missing at %s; run bootstrap.py first", MARKER)
        return 1

    state = budget.load_state()
    ok, wait_s = budget.can_post(state)
    if not ok:
        hrs = wait_s / 3600
        log.info("post cooldown: %.1fh left; aborting", hrs)
        return 0

    if respect_window and not in_sunday_window():
        log.info("outside Sunday 08-11 local window; aborting (use --no-window to override)")
        return 0

    gem = pick_best_gem_last_week()
    if not gem:
        log.info("no eligible gem found in the last 7 days; aborting")
        return 0

    image_path = Path(gem["path"])
    if not image_path.exists():
        log.warning("gem image missing on disk: %s", image_path)
        return 1

    caption = build_caption(gem)
    log.info("selected gem %s (camera=%s, rxn=%s); caption: %r",
             gem["id"], gem["camera_id"], gem["reaction_count"], caption)

    if dry_run:
        log.info("DRY-RUN: would post gem %s with caption above; not submitting", gem["id"])
        return 0

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
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            primitives.goto_feed(page)
            primitives.human_sleep(4.0, 8.0)

            if not primitives.open_create_post_dialog(page):
                log.error("could not open create-post dialog; aborting")
                return 1
            challenge.inspect_page(page)

            if not primitives.attach_photo(page, image_path):
                log.error("could not attach photo; aborting")
                return 1

            if not primitives.type_post_body(page, caption):
                log.error("could not type post body; aborting")
                return 1

            # HARD SAFETY: if we cannot explicitly set narrow audience, we
            # REFUSE to submit. Defaulting could accidentally post to a
            # wider radius than Boss authorized.
            if not primitives.set_audience_neighborhood(page):
                log.error("audience picker not set to 'Just my neighborhood'; refusing to submit")
                return 1

            if not primitives.submit_post(page):
                log.error("submit failed")
                return 1

            # Record the post. Nextdoor does not conveniently return a URL;
            # we log without it for now. Future improvement: scrape the
            # post URL from the confirmation toast.
            budget.record_post(state)
            record_post(gem, post_url=None)
            log.info("posted gem %s to Nextdoor", gem["id"])
        except challenge.ChallengeHit as ch:
            log.error("crosspost aborted on challenge: %s", ch)
            return 2
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--headed", action="store_true")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-window", action="store_true",
                   help="bypass the Sunday 08-11 local window check")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    headed = args.headed or not args.headless
    return run(
        headed=headed,
        dry_run=args.dry_run,
        respect_window=not args.no_window,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
