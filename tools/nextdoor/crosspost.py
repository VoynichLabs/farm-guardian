# Author: GPT-5.5
# Date: 03-May-2026
# PURPOSE: Nextdoor outbound cross-post lane. Two tick types, dispatched
#          by --lane:
#            - "today"     — 1 reacted LIVE-CAM gem from today; fires 18:30.
#            - "throwback" — DISABLED 2026-05-03 unless
#              FARM_NEXTDOOR_THROWBACK_ENABLED=1 is explicitly set.
#
#          Both lanes share primitives, the farm-nextdoor profile, the
#          challenge detector, and the kill switch. Captions come from the
#          currently-loaded LM Studio VLM (caption_writer.write_caption).
#          Dedup via image_archive.nextdoor_posted_at, added idempotently
#          at module import.
#
#          Hard constraints:
#            - audience floor = visibility-menu-option-2 ("Your neighborhood
#              · Hampton only"); submit aborts if selection fails.
#            - per-lane daily cap (1/day per lane via budget.py).
#            - kill switch /tmp/nextdoor-off honored.
#            - challenge cooldown /tmp/nextdoor-cooldown-until honored.
#            - single photo per post.
#
#          Plan: docs/23-Apr-2026-nextdoor-crosspost-plan.md.
#
#          2026-05-03 throwback deactivation: Boss rejected the current
#          throwback selection quality. The throwback lane must stay off
#          until it is redesigned as exact-date-only "on this day"
#          sourcing with strict provenance.
#
# SRP/DRY check: Pass — one file, one responsibility (orchestrate one
#                Nextdoor cross-post tick). No scheduling, no caption
#                authoring (delegated), no scoring (delegated to SQL).

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, unquote, urlparse

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

REPO_ROOT = _HERE.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import budget  # noqa: E402
import caption_writer  # noqa: E402

log = logging.getLogger("nextdoor.crosspost")

Lane = Literal["today", "throwback"]

DB_PATH = REPO_ROOT / "data" / "guardian.db"
PROFILE_DIR = Path.home() / "Library" / "Application Support" / "farm-nextdoor" / "profile"
MARKER = Path.home() / "Library" / "Application Support" / "farm-nextdoor" / "bootstrap-ok.json"
DATA_DIR = REPO_ROOT / "data" / "nextdoor"
POSTS_LOG = DATA_DIR / "posts.json"
SHOT_DIR = DATA_DIR / "shots"
_THROWBACK_ENABLE_ENV = "FARM_NEXTDOOR_THROWBACK_ENABLED"

STEALTH_INIT = r"""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {
  get: () => [1,2,3,4,5].map(() => ({0: {type: 'application/pdf'}, length: 1}))
});
window.chrome = window.chrome || { runtime: {} };
"""

LIVE_CAMERAS = (
    "s7-cam",
    "gwtc",
    "mba-cam",
    "usb-cam",
    "house-yard",
    "iphone-cam",
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Add nextdoor_* columns idempotently. Safe to call every tick."""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(image_archive)")
    cols = {row[1] for row in cur.fetchall()}
    if "nextdoor_posted_at" not in cols:
        cur.execute("ALTER TABLE image_archive ADD COLUMN nextdoor_posted_at TEXT")
        log.info("added image_archive.nextdoor_posted_at column")
    if "nextdoor_share_url" not in cols:
        cur.execute("ALTER TABLE image_archive ADD COLUMN nextdoor_share_url TEXT")
        log.info("added image_archive.nextdoor_share_url column")
    if "nextdoor_lane" not in cols:
        cur.execute("ALTER TABLE image_archive ADD COLUMN nextdoor_lane TEXT")
        log.info("added image_archive.nextdoor_lane column")
    conn.commit()


def pick_gem(conn: sqlite3.Connection, lane: Lane) -> dict | None:
    """Return one gem row to post, or None if nothing eligible."""
    cur = conn.cursor()
    if lane == "today":
        placeholders = ",".join("?" * len(LIVE_CAMERAS))
        today_local_start = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        sql = (
            f"SELECT id, camera_id, ts, image_path, vlm_json "
            f"FROM image_archive "
            f"WHERE camera_id IN ({placeholders}) "
            f"  AND discord_reactions > 0 "
            f"  AND nextdoor_posted_at IS NULL "
            f"  AND ts >= ? "
            f"ORDER BY discord_reactions DESC, ts DESC "
            f"LIMIT 1"
        )
        cur.execute(sql, (*LIVE_CAMERAS, today_local_start))
    else:
        cur.execute(
            "SELECT id, camera_id, ts, image_path, vlm_json "
            "FROM image_archive "
            "WHERE camera_id = 'discord-drop' "
            "  AND discord_reactions > 0 "
            "  AND nextdoor_posted_at IS NULL "
            "ORDER BY discord_reactions DESC, ts DESC "
            "LIMIT 1"
        )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "camera_id": row[1],
        "ts": row[2],
        "image_path": row[3],
        "vlm_json": row[4],
    }


def _extract_share_url(page: Page) -> str | None:
    """Pull the canonical share URL out of the success modal."""
    try:
        js = (
            "() => {"
            "  const a = document.querySelector('[data-testid=\"share_app_button_TWITTER\"]');"
            "  if (!a) return null;"
            "  return a.getAttribute('href');"
            "}"
        )
        href = page.evaluate(js)
    except Exception as e:
        log.warning("share URL evaluate failed: %s", e)
        return None
    if not href:
        return None
    try:
        q = parse_qs(urlparse(href).query)
        url_param = q.get("url", [None])[0]
        if not url_param:
            return None
        nd_url = unquote(url_param)
        # Strip share-action tracking params — keep only the canonical /p/{id}/
        p = urlparse(nd_url)
        canonical = f"{p.scheme}://{p.netloc}{p.path}"
        return canonical.rstrip("/") + "/"
    except Exception as e:
        log.warning("share URL parse failed: %s", e)
        return None


def record_post(
    conn: sqlite3.Connection,
    gem_id: int,
    lane: Lane,
    share_url: str | None,
) -> None:
    cur = conn.cursor()
    cur.execute(
        "UPDATE image_archive "
        "SET nextdoor_posted_at = ?, "
        "    nextdoor_share_url = ?, "
        "    nextdoor_lane = ? "
        "WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), share_url, lane, gem_id),
    )
    conn.commit()


def _append_posts_log(entry: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    if POSTS_LOG.exists():
        try:
            rows = json.loads(POSTS_LOG.read_text())
            if not isinstance(rows, list):
                rows = []
        except Exception:
            rows = []
    rows.append(entry)
    POSTS_LOG.write_text(json.dumps(rows, indent=2))


def _resolve_image_path(raw: str) -> Path | None:
    """image_archive.image_path stores paths in a few shapes depending on
    the ingest lane. Try absolute, repo-relative, and data/-relative."""
    if not raw:
        return None
    p = Path(raw)
    if p.is_absolute() and p.exists():
        return p
    for base in (REPO_ROOT, REPO_ROOT / "data"):
        cand = base / raw
        if cand.exists():
            return cand
    return None


def _shot(page, name: str, lane: Lane) -> None:
    try:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOT_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{lane}-{name}.png"
        page.screenshot(path=str(path), full_page=False)
    except Exception as e:
        log.warning("screenshot %s failed: %s", name, e)


def gate_checks(lane: Lane) -> tuple[bool, str]:
    if budget.kill_switch_on():
        return False, f"kill switch present at {budget.KILL_SWITCH}"
    in_cd, end = budget.in_cooldown()
    if in_cd:
        mins = max(1, int((end - time.time()) / 60))
        return False, f"in challenge cooldown for another {mins} min"
    if not MARKER.exists():
        return False, f"bootstrap marker missing at {MARKER}; run bootstrap.py first"
    state = budget.load_state()
    bucket = f"post_{lane}"
    if state.counts.get(bucket, 0) >= 1:
        return False, f"{bucket} already posted today"
    return True, "ok"


def run_tick(lane: Lane, dry_run: bool = False, headed: bool = False) -> dict:
    summary: dict = {
        "started_at": int(time.time()),
        "lane": lane,
        "dry_run": dry_run,
        "posted": False,
        "reason": None,
        "share_url": None,
        "gem_id": None,
    }

    if lane == "throwback" and os.environ.get(_THROWBACK_ENABLE_ENV) != "1":
        summary["disabled"] = True
        summary["reason"] = (
            "throwback lane disabled; set "
            f"{_THROWBACK_ENABLE_ENV}=1 only after exact-date sourcing is redesigned"
        )
        log.warning(summary["reason"])
        return summary

    ok, why = gate_checks(lane)
    if not ok:
        summary["reason"] = why
        log.info("gate refused: %s", why)
        return summary

    conn = sqlite3.connect(str(DB_PATH))
    try:
        ensure_schema(conn)
        gem = pick_gem(conn, lane)
        if not gem:
            summary["reason"] = "no eligible gem for this lane"
            log.info("no gem for lane=%s", lane)
            return summary

        image_path = _resolve_image_path(gem["image_path"])
        if not image_path:
            summary["reason"] = f"gem {gem['id']} image not on disk: {gem['image_path']}"
            log.warning(summary["reason"])
            return summary
        summary["gem_id"] = gem["id"]

        caption = caption_writer.write_caption(image_path, lane)
        summary["caption"] = caption
        log.info("caption for gem %s (%s): %r", gem["id"], lane, caption)

        import challenge  # noqa: E402
        import primitives  # noqa: E402
        from playwright.sync_api import sync_playwright  # noqa: E402

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
                time.sleep(2)
                if not primitives.open_create_post_dialog(page):
                    summary["reason"] = "could not open composer"
                    _shot(page, "compose-open-fail", lane)
                    return summary
                _shot(page, "01-composer-open", lane)

                if not primitives.type_post_body(page, caption):
                    summary["reason"] = "type caption failed"
                    _shot(page, "type-fail", lane)
                    return summary
                _shot(page, "02-caption-typed", lane)

                file_input = page.locator(
                    primitives.NEXTDOOR_SELECTORS["COMPOSER_PHOTO_INPUT"]
                ).first
                file_input.set_input_files(str(image_path))
                time.sleep(10)
                _shot(page, "03-photo-attached", lane)

                if not primitives.set_audience_neighborhood(page):
                    summary["reason"] = "could not set narrowest audience"
                    _shot(page, "audience-fail", lane)
                    return summary
                try:
                    label = page.locator(
                        primitives.NEXTDOOR_SELECTORS["COMPOSER_AUDIENCE_PICKER"]
                    ).first.inner_text()
                except Exception:
                    label = ""
                summary["audience_label"] = label
                if "Hampton" not in label and "neighborhood" not in label.lower():
                    summary["reason"] = f"audience readback unexpected: {label!r}"
                    _shot(page, "audience-readback-fail", lane)
                    return summary
                _shot(page, "04-audience-set", lane)

                if dry_run:
                    summary["reason"] = "dry-run: did not submit"
                    primitives.close_composer(page)
                    return summary

                if not primitives.submit_post(page):
                    summary["reason"] = "submit click failed"
                    _shot(page, "submit-fail", lane)
                    return summary
                time.sleep(6)
                _shot(page, "05-after-submit", lane)

                share_url = _extract_share_url(page)
                summary["share_url"] = share_url
                summary["posted"] = True

                record_post(conn, gem["id"], lane, share_url)
                state = budget.load_state()
                state.counts[f"post_{lane}"] = state.counts.get(f"post_{lane}", 0) + 1
                budget.save_state(state)

                _append_posts_log(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "lane": lane,
                        "image_archive_id": gem["id"],
                        "camera_id": gem["camera_id"],
                        "image_path": str(image_path),
                        "caption": caption,
                        "share_url": share_url,
                        "audience_label": label,
                    }
                )
                return summary
            except challenge.ChallengeHit as ch:
                summary["reason"] = f"challenge: {ch.matched}"
                log.error("challenge hit during cross-post: %s", ch)
                _shot(page, "challenge", lane)
                return summary
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    finally:
        conn.close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--lane", choices=("today", "throwback"), required=False)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--headless", action="store_true")
    return p.parse_args(argv)


def _pick_lane_by_clock() -> Lane:
    """If the operator didn't specify --lane, infer from local hour:
    morning ticks are throwback, afternoon/evening ticks are today."""
    h = datetime.now().hour
    return "throwback" if h < 12 else "today"


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    lane: Lane = args.lane or _pick_lane_by_clock()
    headed = args.headed and not args.headless
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(DATA_DIR / "crosspost.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(fh)
    summary = run_tick(lane=lane, dry_run=args.dry_run, headed=headed)
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary["posted"] or args.dry_run or summary.get("disabled") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
