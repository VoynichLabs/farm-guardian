#!/usr/bin/env python3
# Author: Claude Fable 5
# Date: 23-Jul-2026
# PURPOSE: Write the daily farm diary entry by distilling the day's
#          #meet-the-lobsters Discord conversation, then post it to
#          #farm-2026 for Boss's reaction. Implements
#          docs/20-Jul-2026-daily-diary-from-discord-plan.md, which
#          diagnosed the real problem: the diary system already exists and
#          is already wired to reel captions via
#          daily_reel_runner._load_farm_context() — it went dead simply
#          because nothing writes it. On 23-Jul-2026 only ONE of 23 entries
#          was still inside the 21-day freshness window, so every reel
#          caption was rewriting the same 09-Jul roost anecdote, and that
#          entry ages out ~30-Jul leaving captions with no farm narrative
#          at all. This is the component that keeps it fed.
#          Distillation runs on Bubba's own model via the claude CLI in
#          print mode (best voice match, already authenticated, no new
#          dependency). Falls back to writing nothing rather than writing
#          something invented.
# SRP/DRY check: Pass — reuses the existing diary store, the existing
#          caption consumer, the Discord bot token + posting pattern from
#          tools/discord_harvester.py, and the reaction gate every other
#          lane already uses. Builds no new storage, no new scheduler
#          concept, and no second narrative system.

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DISCORD_API = "https://discord.com/api/v10"
OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"

# Source: the only channel wired requireMention=False, i.e. where the farm
# conversation actually happens rather than direct commands to Bubba.
SOURCE_CHANNEL_ID = "1471632572953006337"   # #meet-the-lobsters

# Destination: #farm-2026. Imported, NOT re-hardcoded — the first cut of this
# script hardcoded 1476787165638951026 from a CLAUDE.md passage and posted the
# diary into #swarm-coordination by mistake. That ID is the reciprocate
# harvester's channel; the docs even say "NOT #farm-2026" right next to it.
# tools/discord_harvester.CHANNEL_ID is the verified value
# (docs/skills-farm-2026-discord-post.md confirms it via the webhook endpoint),
# so take it from there and let there be exactly one source of truth.
from tools.discord_harvester import CHANNEL_ID as FARM_CHANNEL_ID  # noqa: E402

DIARY_DIR = Path.home() / "Documents" / "GitHub" / "farm-2026" / "content" / "diary"

MARK_DISCORD_USER_ID = "293569238386606080"

log = logging.getLogger("farm-diary-from-discord")


def _token() -> str:
    cfg = json.loads(OPENCLAW_CONFIG.read_text(encoding="utf-8"))
    token = (cfg.get("channels", {}).get("discord", {}) or {}).get("token")
    if not token:
        raise RuntimeError("no discord bot token in openclaw.json")
    return token


def _headers(token: str) -> dict:
    return {"Authorization": f"Bot {token}", "Content-Type": "application/json"}


def fetch_recent_messages(token: str, channel_id: str, hours: int) -> list[dict]:
    """Newest-first pagination back to the cutoff. Discord has no since= param,
    so we page until timestamps fall out of the window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: list[dict] = []
    before = None
    for _page in range(20):                       # hard stop; 2000 messages max
        url = f"{DISCORD_API}/channels/{channel_id}/messages?limit=100"
        if before:
            url += f"&before={before}"
        resp = requests.get(url, headers=_headers(token), timeout=20)
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
    out.reverse()                                  # chronological
    return out


def render_transcript(messages: list[dict], char_cap: int = 14000) -> str:
    """Compact transcript for the model. Keeps who-said-what (the farm facts
    come from Boss, not from the bots) and drops noise."""
    lines: list[str] = []
    for m in messages:
        content = (m.get("content") or "").strip()
        if not content:
            continue                               # image-only / embed-only
        author = (m.get("author") or {}).get("username") or "unknown"
        is_bot = bool((m.get("author") or {}).get("bot"))
        content = re.sub(r"<@!?\d+>", "@someone", content)
        content = re.sub(r"https?://\S+", "[link]", content)
        stamp = m["timestamp"][11:16]
        lines.append(f"[{stamp}] {author}{' (bot)' if is_bot else ''}: {content}")
    text = "\n".join(lines)
    if len(text) > char_cap:                       # keep the END of the day
        text = text[-char_cap:]
        text = text[text.index("\n") + 1:]
    return text


def build_prompt(transcript: str, sample_entry: str, day: str) -> str:
    return f"""You write the daily farm diary for a small rare-breed poultry farm. \
You are Bubba, the farm's AI assistant. Below is today's conversation from the \
farm's Discord channel. Distill ONLY the farm-relevant substance into one short \
diary entry.

HARD RULES:
- Report only what was actually said. Invent nothing — no bird names, events, \
counts, or outcomes that do not appear in the conversation.
- If something is uncertain in the conversation, say so plainly, the way the \
sample entry hedges IDs. Never resolve an ambiguity by guessing.
- If the conversation contains nothing about the farm, birds, coop, cameras, \
weather, or equipment, reply with exactly: NO-ENTRY
- Summarize. Never quote or paste the raw chat.
- No talk of the farm as a security or predator-detection system.
- Markdown. Open with "# <short title> — {day}". Close with "-Bubba" on its own \
line. Bold the genuinely notable facts. Aim for 80-180 words.

Here is a previous entry, for voice and shape only — do not reuse its content:
---
{sample_entry}
---

Today's conversation ({day}):
---
{transcript}
---

Write the diary entry now, or NO-ENTRY."""


def distill(prompt: str, timeout_s: int = 300) -> str | None:
    """Bubba's own model via the claude CLI in print mode."""
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        log.error("distill: claude CLI timed out after %ss", timeout_s)
        return None
    if proc.returncode != 0:
        log.error("distill: claude CLI exit=%s stderr=%s",
                  proc.returncode, (proc.stderr or "")[:300])
        return None
    body = (proc.stdout or "").strip()
    if not body:
        log.error("distill: empty response")
        return None
    return body


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return (s[:60] or "farm-notes").rstrip("-")


def post_to_discord(token: str, body: str, path: Path, dry_run: bool) -> str | None:
    msg = (
        f"**Farm diary — {path.stem}**\n"
        f"<@{MARK_DISCORD_USER_ID}> react if this is worth keeping "
        f"(a reaction promotes it toward the public field notes).\n\n"
        f"{body[:1600]}"
    )
    if dry_run:
        log.info("dry-run: would post %d chars to #farm-2026", len(msg))
        return None
    resp = requests.post(
        f"{DISCORD_API}/channels/{FARM_CHANNEL_ID}/messages",
        headers=_headers(token), json={"content": msg}, timeout=20,
    )
    if resp.status_code not in (200, 201):
        log.error("discord post failed %s: %s", resp.status_code, resp.text[:200])
        return None
    mid = resp.json().get("id")
    log.info("discord: posted diary entry, message_id=%s", mid)
    return mid


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the entry; do not write the file or post.")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite today's entry if one already exists.")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Filenames use the repo-wide dated convention, DD-Mon-YYYY (CLAUDE.md
    # "Plans (required)"). The diary folder had drifted into two formats —
    # 2026-07-09-... and 18-may-2026-... — and was normalised on 23-Jul-2026.
    # `day` is the human-facing date that goes in the entry heading; `stamp`
    # is the filename prefix.
    now = datetime.now()
    day = now.strftime("%Y-%m-%d")
    stamp = f"{now.day:02d}-{now.strftime('%b')}-{now.year}"
    existing = sorted(DIARY_DIR.glob(f"{stamp}-*.md"))
    if existing and not args.force and not args.dry_run:
        log.info("entry already exists for %s (%s); nothing to do",
                 stamp, existing[0].name)
        return 0

    token = _token()
    messages = fetch_recent_messages(token, SOURCE_CHANNEL_ID, args.hours)
    log.info("fetched %d messages from the last %dh", len(messages), args.hours)
    transcript = render_transcript(messages)
    if len(transcript) < 200:
        log.info("not enough conversation to distill (%d chars); skipping",
                 len(transcript))
        return 0

    samples = sorted(DIARY_DIR.glob("*.md"))
    sample_entry = samples[-1].read_text(encoding="utf-8")[:1200] if samples else ""

    body = distill(build_prompt(transcript, sample_entry, day))
    if body is None:
        return 1
    if body.strip().upper().startswith("NO-ENTRY"):
        log.info("model found nothing farm-relevant today; no entry written")
        return 0

    first = body.lstrip().split("\n", 1)[0]
    title = re.sub(r"^#+\s*", "", first).split("—")[0].strip()
    path = DIARY_DIR / f"{stamp}-{slugify(title)}.md"

    if args.dry_run:
        print(f"\n----- would write {path} -----\n{body}\n")
        post_to_discord(token, body, path, dry_run=True)
        return 0

    DIARY_DIR.mkdir(parents=True, exist_ok=True)
    # --force means "redo today", not "add a second entry for today". The
    # title (and therefore the slug) is model-generated and shifts as the
    # day's conversation moves on, so a naive rewrite leaves duplicates that
    # both feed the caption context. Clear the day first.
    if args.force:
        for stale in DIARY_DIR.glob(f"{stamp}-*.md"):
            if stale != path:
                stale.unlink()
                log.info("force: removed superseded entry %s", stale.name)
    path.write_text(body.rstrip() + "\n", encoding="utf-8")
    log.info("wrote %s (%d chars)", path, len(body))
    post_to_discord(token, body, path, dry_run=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
