#!/usr/bin/env python3
# Author: Claude Opus 4.8 (1M); Claude Opus 5 (14-Aug-2026 — reactions never expire)
# Date: 23-Jul-2026; 14-Aug-2026
#
# ⚠️ 14-Aug-2026 (v2.71.1) — REACTIONS NEVER EXPIRE. DO NOT REINTRODUCE A
#          SCAN WINDOW AS THE ELIGIBILITY TEST. This job used to find diary
#          posts by scanning #farm-2026 back a fixed 72h. React on day 4 and
#          the post fell outside the scan permanently — nothing else ever
#          looked at it again, and the loss was silent. Measured cost: 44
#          diary entries written, 3 ever promoted.
#          Each post's Discord message id is now remembered in the state file
#          and re-checked directly by id, so a reaction added weeks later is
#          still honoured. The channel scan survives only to DISCOVER new
#          posts; it is no longer what decides eligibility. It widens once to
#          seed ids for older un-reacted entries, then narrows again — days
#          proven to have no post are recorded in `no_post` so the seeding
#          pass converges instead of re-reading history every 30 minutes.
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
from typing import Optional

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import discord_harvester as dh  # noqa: E402  (token/API/CHANNEL_ID/git plumbing)
from tools.pipeline import daily_reel_runner as drr  # noqa: E402  (_diary_date == caption path)

FARM_2026 = Path.home() / "GitHub" / "farm-2026"
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

def load_state() -> dict:
    """Load the promotion ledger.

    Schema (14-Aug-2026 — `known` and `no_post` added so reactions never expire):
      promoted : [iso_day]        already published as a field note
      known    : {iso_day: msg_id} the Discord post for that day, remembered
                                   forever so its reactions stay checkable
      no_post  : [iso_day]        a full-history scan found NO Discord post for
                                   this day; stops the seeding scan re-widening
                                   for it on every run

    Back-compatible: an old file carrying only `promoted` loads fine and the new
    keys start empty.
    """
    state = {"promoted": [], "known": {}, "no_post": []}
    if STATE_FILE.exists():
        try:
            loaded = json.loads(STATE_FILE.read_text())
            state["promoted"] = list(loaded.get("promoted", []))
            state["known"] = dict(loaded.get("known", {}))
            state["no_post"] = list(loaded.get("no_post", []))
        except (ValueError, OSError):
            log.warning("state file unreadable; treating as empty")
    return state


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"promoted": sorted(set(state.get("promoted", []))),
                    "known": dict(sorted(state.get("known", {}).items())),
                    "no_post": sorted(set(state.get("no_post", []))),
                    "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                   indent=2) + "\n"
    )


def pending_days(promoted: set[str]) -> list[str]:
    """ISO days that have a diary file on disk but no published field note yet.

    This is the work-list the seeding scan sizes itself against — the promoter
    only ever needs to look back as far as the oldest entry still awaiting a
    reaction, not a fixed number of hours.
    """
    days: set[str] = set()
    if not DIARY_DIR.is_dir():
        return []
    for p in DIARY_DIR.glob("*.md"):
        d = drr._diary_date(p)
        if d is None:
            continue
        iso_day = d.isoformat()
        if iso_day in promoted:
            continue
        if any(FIELD_NOTES_DIR.glob(f"{iso_day}-*.mdx")):
            continue
        days.add(iso_day)
    return sorted(days)


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

def fetch_message(token: str, message_id: str) -> Optional[dict]:
    """Fetch ONE message by id, with its current reactions. None if it is gone.

    This is what makes reactions non-expiring: once a diary post's id is
    remembered, it stays checkable forever for the cost of a single request,
    with no dependence on how far back a channel scan happens to reach.
    """
    url = f"{dh.DISCORD_API}/channels/{dh.CHANNEL_ID}/messages/{message_id}"
    resp = _discord_get(url, dh.discord_headers(token), allow_404=True)
    return None if resp is None else resp.json()


def _discord_get(url: str, headers: dict, *, allow_404: bool = False,
                 max_attempts: int = 6):
    """GET with Discord rate-limit handling.

    ⚠️ REQUIRED, not defensive padding. Discord 429s on sustained pagination and
    the seeding scan can now run for hundreds of pages, so an unhandled 429
    aborts the whole run — which is exactly what happened on the first live
    attempt (14-Aug-2026). The response carries `retry_after` in seconds; honour
    it and retry the SAME page rather than skipping it, or the scan silently
    loses a window of history and entries look like they have no post.

    Returns the response, or None for a 404 when allow_404.
    """
    for attempt in range(max_attempts):
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 429:
            try:
                wait = float(resp.json().get("retry_after", 1.0))
            except ValueError:
                wait = 1.0
            # Escalate slightly per attempt so a persistently throttled bucket
            # backs off instead of hammering at the minimum interval.
            wait = min(30.0, max(0.5, wait) * (attempt + 1))
            log.info("discord rate-limited; sleeping %.1fs then retrying", wait)
            time.sleep(wait)
            continue
        if allow_404 and resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise RuntimeError(f"discord {resp.status_code}: {resp.text[:200]}")
        return resp
    raise RuntimeError(f"discord: still rate-limited after {max_attempts} attempts")


def fetch_recent_messages(token: str, hours: int, max_pages: int = 15) -> list[dict]:
    """Newest-first pagination over #farm-2026 back to the cutoff.

    `hours` is normally short (discovery of NEW posts only) — the promoter no
    longer depends on this reaching far enough, because known message ids are
    re-checked directly by fetch_message(). It only widens on the one-time
    seeding pass for entries whose message id isn't remembered yet, and
    max_pages is sized to match so the widened scan isn't silently truncated.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    headers = dh.discord_headers(token)
    out: list[dict] = []
    before = None
    for _page in range(max_pages):  # hard stop
        url = f"{dh.DISCORD_API}/channels/{dh.CHANNEL_ID}/messages?limit=100"
        if before:
            url += f"&before={before}"
        page = _discord_get(url, headers).json()
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
                    help="How far back to scan for NEW diary posts (default 72). "
                         "Known posts are re-checked by id regardless of this.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be promoted; write and push nothing.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    token = dh.load_bot_token()
    state = load_state()
    promoted = set(state["promoted"])
    known: dict[str, str] = state["known"]
    no_post = set(state["no_post"])

    # ---- Scan span -------------------------------------------------------
    # Normally short: the scan only has to DISCOVER new posts, because anything
    # already known is re-checked by id below and therefore never expires. It
    # widens only for entries still awaiting a reaction whose message id we
    # have never recorded — the one-time seeding pass. Days proven to have no
    # post are remembered in `no_post` so they cannot hold the scan wide
    # forever; without that the seeding pass would degrade into a full-history
    # re-read every 30 minutes.
    awaiting = pending_days(promoted)
    unseeded = [d for d in awaiting if d not in known and d not in no_post]
    hours, max_pages = args.hours, 15
    if unseeded:
        span_days = (datetime.now(timezone.utc).date() - date.fromisoformat(min(unseeded))).days
        hours = max(args.hours, (span_days + 2) * 24)
        # ~170 messages/day observed in #farm-2026 => ~1.7 pages/day. Triple it
        # for headroom and floor at the old 15 so a short scan is unchanged.
        max_pages = max(15, min(400, int((span_days + 2) * 5.1) + 1))
        log.info("seeding: %d entr(ies) awaiting a reaction have no known post id "
                 "(oldest %s) — widening scan to %dh / %d pages",
                 len(unseeded), min(unseeded), hours, max_pages)

    messages = fetch_recent_messages(token, hours, max_pages=max_pages)
    diary_posts = [
        m for m in messages
        if str((m.get("author") or {}).get("id", "")) == BUBBA_BOT_ID
        and DIARY_TITLE_RE.search(m.get("content") or "")
    ]
    log.info("scanned %d messages, found %d diary posts in the last %dh",
             len(messages), len(diary_posts), hours)

    # ---- Remember every post we can see, keyed by its diary day ----------
    def day_of(msg: dict) -> Optional[str]:
        """The diary day a post refers to. The date is the stable key: the
        writer's slug drifts on --force and its filename format changed
        (ISO -> DD-Mon-YYYY in v2.51.11), so the post stem can be either form.
        Parsed with the caption path's own parser so both behave identically."""
        match = DIARY_TITLE_RE.search(msg.get("content") or "")
        if not match:
            return None
        parsed = drr._diary_date(Path(f"{match.group(1).strip()}.md"))
        return parsed.isoformat() if parsed else None

    for m in diary_posts:
        iso_day = day_of(m)
        if iso_day:
            known[iso_day] = str(m["id"])

    # A widened scan covered the full span, so anything still unseeded genuinely
    # has no post — record that once instead of re-scanning for it forever.
    if unseeded:
        for day in unseeded:
            if day not in known:
                no_post.add(day)
                log.info("%s has a diary file but no Discord post; will not re-scan for it", day)

    # ---- Candidates: every unpublished day whose post we know about ------
    # This is the fix. Eligibility comes from HAVING a remembered post, not from
    # that post still falling inside a scan window, so a reaction added weeks
    # later is still honoured.
    candidates: list[tuple[str, dict]] = []
    for iso_day in pending_days(promoted):
        message_id = known.get(iso_day)
        if not message_id:
            continue
        msg = next((m for m in diary_posts if str(m["id"]) == message_id), None)
        if msg is None:
            try:
                msg = fetch_message(token, message_id)
            except RuntimeError as exc:
                log.warning("could not re-check %s (%s); leaving for a later run", iso_day, exc)
                continue
            if msg is None:
                log.warning("post for %s no longer exists on Discord; dropping it", iso_day)
                known.pop(iso_day, None)
                no_post.add(iso_day)
                continue
            time.sleep(0.25)  # be polite; this loop is small but runs every 30 min
        candidates.append((iso_day, msg))

    log.info("%d entr(ies) awaiting a reaction; %d have a checkable post",
             len(awaiting), len(candidates))

    published = 0
    for iso_day, m in candidates:
        diary_file = find_diary_file_for_day(iso_day)
        if diary_file is None:
            log.info("no diary file on disk for %s; skipping", iso_day)
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
        # Saved even when nothing published: `known`/`no_post` are what make the
        # next run cheap and non-expiring, so they must persist regardless.
        save_state({"promoted": sorted(promoted), "known": known, "no_post": sorted(no_post)})
    log.info("done: %d newly published", published)
    return 0


if __name__ == "__main__":
    sys.exit(main())
