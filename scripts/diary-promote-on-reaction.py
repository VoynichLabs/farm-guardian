#!/usr/bin/env python3
# Author: Claude Opus 4.8 (1M)
# Date: 23-Jul-2026
# PURPOSE: Make the daily farm diary VISIBLE. The nightly writer
#          (farm-diary-from-discord.py) posts each day's entry to
#          #farm-2026 and asks the Boss to react "if this is worth
#          keeping". Nothing consumed that reaction, so the entry only
#          ever fed reel captions (invisible) and sat in Discord. This
#          job closes that loop: it finds the bot's diary posts in
#          #farm-2026, checks whether the BOSS reacted, and for each
#          reacted-and-not-yet-published day converts that day's raw
#          farm-2026/content/diary entry into a published
#          content/field-notes/{iso-day}-{slug}.mdx, then commits+pushes
#          farm-2026 so it appears at /field-notes (Railway auto-deploys).
#          farm-2026 CLAUDE.md states the intent: content/diary is raw
#          source material (not published); field-notes is the published
#          surface. Promotion is the sanctioned, Boss-gated bridge.
#
#          Diary files come in two filename formats (ISO 2026-07-23-… from
#          the writer, and DD-Mon-YYYY 23-Jul-2026-… after the 23-Jul
#          normalise commit), so the on-disk entry is resolved by its
#          PARSED date via the same helper the caption path uses, never by
#          a filename glob.
# SRP/DRY check: Pass — reuses tools/discord_harvester (bot token, Discord
#          API, CHANNEL_ID, git-in-farm-2026 pattern) and
#          daily_reel_runner._diary_date (the caption path's own date
#          parser). Does not touch the writer or the caption consumer.

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import discord_harvester as dh  # noqa: E402  (token/API/CHANNEL_ID/git plumbing)
from tools.pipeline import daily_reel_runner as drr  # noqa: E402  (_diary_date == caption path)

FARM_2026 = Path.home() / "Documents" / "GitHub" / "farm-2026"
DIARY_DIR = FARM_2026 / "content" / "diary"
FIELD_NOTES_DIR = FARM_2026 / "content" / "field-notes"
CAROUSEL_DIR = FARM_2026 / "public" / "photos" / "carousel"

# Promotion ledger — which diary DAYS (ISO) have already been published as field
# notes. Lives under farm-guardian/data (gitignored), same as harvester-state.
STATE_FILE = REPO_ROOT / "data" / "diary-promote-state.json"

# Only the Boss's reaction publishes. Other reactors (Larry/Bubba/Egon, or
# anyone else) do not — mirrors the SOCIAL_MEDIA_MAP trust rule that only Mark's
# reaction is the quality gate.
BOSS_DISCORD_USER_ID = "293569238386606080"

# The diary writer posts as the Bubba bot with a fixed title line carrying the
# writer's (ISO) stem: **Farm diary — 2026-07-23-some-slug**
BUBBA_BOT_ID = "1474802169415733358"
DIARY_TITLE_RE = re.compile(r"\*\*Farm diary\s*[—-]\s*(.+?)\*\*")

_MONTHS = {
    "01": "january", "02": "february", "03": "march", "04": "april",
    "05": "may", "06": "june", "07": "july", "08": "august",
    "09": "september", "10": "october", "11": "november", "12": "december",
}

log = logging.getLogger("diary-promote")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def load_promoted() -> set[str]:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()).get("promoted", []))
        except (ValueError, OSError):
            log.warning("state file unreadable; treating as empty")
    return set()


def save_promoted(promoted: set[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"promoted": sorted(promoted),
                    "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                   indent=2) + "\n"
    )


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

def fetch_recent_messages(token: str, hours: int) -> list[dict]:
    """Newest-first pagination over #farm-2026 back to the cutoff. Diary posts
    needing promotion are always recent, so a bounded lookback is enough."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    headers = dh.discord_headers(token)
    out: list[dict] = []
    before = None
    for _page in range(15):  # hard stop
        url = f"{dh.DISCORD_API}/channels/{dh.CHANNEL_ID}/messages?limit=100"
        if before:
            url += f"&before={before}"
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code != 200:
            raise RuntimeError(f"discord {resp.status_code}: {resp.text[:200]}")
        page = resp.json()
        if not page:
            break
        stop = False
        for m in page:
            ts = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
            if ts < cutoff:
                stop = True
                continue
            out.append(m)
        if stop or len(page) < 100:
            break
        before = page[-1]["id"]
        time.sleep(0.4)
    return out


def boss_reacted(msg: dict, token: str) -> bool:
    """True iff the Boss is among the reactors on this message. Fetches the
    reactor list per emoji (Discord doesn't inline reactor ids)."""
    reactions = msg.get("reactions") or []
    if not reactions:
        return False
    headers = dh.discord_headers(token)
    for i, reaction in enumerate(reactions):
        if i > 0:
            time.sleep(0.35)
        emoji = reaction.get("emoji", {}) or {}
        name = emoji.get("name", "")
        eid = emoji.get("id")
        param = f"{name}:{eid}" if eid else name
        url = (
            f"{dh.DISCORD_API}/channels/{dh.CHANNEL_ID}/messages/"
            f"{msg['id']}/reactions/{urllib.parse.quote(param)}?limit=100"
        )
        resp = None
        for _attempt in range(4):
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 429:
                time.sleep(resp.json().get("retry_after", 2.0) + 0.25)
                continue
            break
        if resp is None or resp.status_code != 200:
            continue
        if any(str(u.get("id", "")) == BOSS_DISCORD_USER_ID for u in resp.json()):
            return True
    return False


# ---------------------------------------------------------------------------
# Diary -> field note
# ---------------------------------------------------------------------------

def find_diary_file_for_day(iso_day: str) -> Path | None:
    """The diary file whose PARSED date is iso_day, regardless of filename
    format (ISO 2026-07-23-… or DD-Mon-YYYY 23-Jul-2026-…). Uses the caption
    path's own parser so 'the entry for this day' means the same thing here as
    it does to the reel captions. Newest mtime wins on the rare tie."""
    try:
        target = date.fromisoformat(iso_day)
    except ValueError:
        return None
    matches = [p for p in DIARY_DIR.glob("*.md") if drr._diary_date(p) == target]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def _sanitize_mdx(body: str) -> str:
    """MDXRemote is strict: a stray '<' (JSX tag) or '{' (JSX expression) breaks
    the build. Diary prose never intends either, so escape all four. Rendered
    output is identical for prose."""
    return (body.replace("{", "&#123;").replace("}", "&#125;")
                .replace("<", "&lt;").replace(">", "&gt;"))


def _pick_cover(iso_day: str) -> str | None:
    """A cover from that day's published carousel gems, if any exist. The
    field-notes page guards `{cover && ...}`, so returning None is safe."""
    day_dir = CAROUSEL_DIR / iso_day
    if day_dir.is_dir():
        imgs = sorted(day_dir.glob("*.jpg")) + sorted(day_dir.glob("*.png"))
        if imgs:
            return f"/photos/carousel/{iso_day}/{imgs[0].name}"
    return None


def build_field_note(iso_day: str, diary_md: str) -> tuple[Path, str]:
    """Convert a raw diary entry into a published field-note .mdx. The field
    note is always ISO-named (2026-07-23-slug.mdx) to match the site's existing
    field-note convention, whatever the source diary file was named."""
    lines = diary_md.strip().splitlines()
    first = lines[0] if lines else iso_day
    # Title: strip leading '#', take the part before the date suffix.
    title = re.sub(r"^#+\s*", "", first).split("—")[0].split(" - ")[0].strip()
    title = title.replace('"', "'") or iso_day
    # Drop the leading H1 — the title lives in frontmatter and the [slug] page
    # renders it as the hero, so keeping it would duplicate the heading.
    body_lines = lines[1:] if first.lstrip().startswith("#") else lines
    body = _sanitize_mdx("\n".join(body_lines).strip())

    slug = (re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "farm-notes")
    cover = _pick_cover(iso_day)
    month = _MONTHS.get(iso_day[5:7], "")
    fm = ["---", f'title: "{title}"', f'date: "{iso_day}"']
    if cover:
        fm.append(f'cover: "{cover}"')
        fm += ["photos:", f'  - src: "{cover}"', f'    caption: "{title}"']
    tags = ["flock", "farm-diary"] + ([month] if month else [])
    fm.append(f"tags: [{', '.join(tags)}]")
    fm.append("---")
    text = "\n".join(fm) + "\n\n" + body + "\n"
    return FIELD_NOTES_DIR / f"{iso_day}-{slug}.mdx", text


def commit_push_file(path: Path, message: str) -> bool:
    """Stage ONLY `path` (never `git add -A` — farm-2026 has many async
    committers) and push. One rebase retry on a rejected push."""
    repo = FARM_2026

    def _run(args: list[str], check: bool = True):
        return subprocess.run(args, cwd=repo, check=check, capture_output=True, text=True)

    _run(["git", "add", str(path)])
    if _run(["git", "diff", "--cached", "--quiet"], check=False).returncode == 0:
        log.info("no change to commit for %s", path.name)
        return False
    _run(["git", "commit", "-m", message])
    for attempt in range(2):
        push = _run(["git", "push", "origin", "main"], check=False)
        if push.returncode == 0:
            return True
        log.warning("push rejected (attempt %d): %s", attempt + 1, (push.stderr or "")[:200])
        _run(["git", "pull", "--rebase", "origin", "main"], check=False)
    log.error("push failed for %s after rebase retry; will retry next run", path.name)
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=72,
                    help="How far back to scan #farm-2026 for diary posts (default 72).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be promoted; write and push nothing.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    token = dh.load_bot_token()
    promoted = load_promoted()
    messages = fetch_recent_messages(token, args.hours)

    diary_posts = [
        m for m in messages
        if str((m.get("author") or {}).get("id", "")) == BUBBA_BOT_ID
        and DIARY_TITLE_RE.search(m.get("content") or "")
    ]
    log.info("scanned %d messages, found %d diary posts in the last %dh",
             len(messages), len(diary_posts), args.hours)

    published = 0
    for m in diary_posts:
        stem = DIARY_TITLE_RE.search(m["content"]).group(1).strip()
        # The date is the stable key; the writer's slug drifts on --force and its
        # filename format changed (ISO -> DD-Mon-YYYY in v2.51.11), so the post
        # stem can be either form. Parse it with the caption path's own parser so
        # both are handled identically.
        d = drr._diary_date(Path(f"{stem}.md"))
        if d is None:
            log.info("post title %r carries no parseable date; skipping", stem)
            continue
        iso_day = d.isoformat()
        if iso_day in promoted:
            continue
        if any(FIELD_NOTES_DIR.glob(f"{iso_day}-*.mdx")):
            promoted.add(iso_day)  # already published (earlier this run, or by hand)
            continue
        diary_file = find_diary_file_for_day(iso_day)
        if diary_file is None:
            log.info("no diary file on disk for %s (post stem %s); skipping", iso_day, stem)
            continue
        if not boss_reacted(m, token):
            log.info("%s not yet Boss-reacted; leaving for a later run", iso_day)
            continue

        path, text = build_field_note(iso_day, diary_file.read_text(encoding="utf-8"))
        if args.dry_run:
            log.info("DRY-RUN would publish %s (%s) -> %s\n%s",
                     iso_day, diary_file.name, path.name, text[:600])
            continue
        path.write_text(text, encoding="utf-8")
        if commit_push_file(path, f"field-notes: publish Boss-reacted diary {iso_day} [diary-promote]"):
            promoted.add(iso_day)
            published += 1
            log.info("published field note: %s", path.name)
        else:
            # Roll back the local file so a failed push doesn't leave an
            # uncommitted orphan that a later `git add -A` committer would sweep.
            try:
                path.unlink()
            except OSError:
                pass

    if not args.dry_run:
        save_promoted(promoted)
    log.info("done: %d newly published", published)
    return 0


if __name__ == "__main__":
    sys.exit(main())
