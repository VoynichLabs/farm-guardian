# Author: Claude Opus 4.7 (1M context)
# Date: 22-April-2026
# PURPOSE: Harvest engagement on the Yorkies FB Page (likes/reactions,
#          comments, shares) across recent Page posts AND Page
#          Stories, rank engagers by interaction count, and emit a
#          reciprocation worklist Boss can act on.
#
#          Why not auto-follow? FB Graph API does NOT expose a
#          Page-follows-user or Page-likes-user-post action; Meta
#          deliberately keeps those one-directional from the Page's
#          side. So "follow back" has to be a human click. What this
#          tool does is surface the click list — profile name + FB
#          URL + engagement summary — to Discord so Boss works a
#          reviewed inbox instead of hunting through the FB app.
#
#          Outputs:
#            1. data/on-this-day/engagers-{YYYY-MM-DD}.json — canonical
#               record of who engaged on that calendar day (for future
#               ML / trend analysis).
#            2. A Discord webhook post to the existing #farm-2026
#               channel listing top N engagers with clickable profile
#               links.
#
# SRP/DRY check: Pass — single responsibility is "list the humans
#                engaging with the Page so Boss can reciprocate." Does
#                not touch the posting lane, does not re-fetch story
#                content, does not persist historical impressions
#                (that's a separate insight-scraper).

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.pipeline.fb_poster import (  # noqa: E402
    GRAPH_API_BASE,
    _load_fb_credentials,
    _source_meta_env_file,
)

log = logging.getLogger("on_this_day.reciprocate")

# Discord destination for the engager summary. This is NOT
# `#farm-2026` — that channel is the IG-gem reaction-quality-gate
# (Boss's reactions there trip the IG pipeline, see CLAUDE.md
# "Instagram posting" section). Per Boss 2026-04-22, engager
# worklists go to a separate channel in the same guild. We post via
# the Bubba bot token (already in ~/.openclaw/openclaw.json under
# channels.discord.token — same source discord_harvester.py reads)
# rather than via a webhook, so no new webhook setup is required.
DISCORD_ENGAGERS_CHANNEL_ID = "1476787165638951026"
DISCORD_API = "https://discord.com/api/v10"
OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"

# How far back to scan. The Page is new (first FB post 2026-04-21), so
# 7 days is ample for now; widen later if we start scheduling fewer
# harvests.
DEFAULT_LOOKBACK_DAYS = 2

# Reaction-per-post page size. 25 is plenty for a Page with low
# volume; raise once we actually have high-engagement posts.
REACTION_PAGE_SIZE = 25
COMMENT_PAGE_SIZE = 25

# Top-N engagers to surface in the plain-text summary. Anything lower
# is unlikely to matter for reciprocation; anything higher is noise.
PLAINTEXT_TOP_N = 15

OUTPUT_DIR = _REPO_ROOT / "data" / "on-this-day"


class GraphError(RuntimeError):
    """Raised on a non-2xx Graph API response with the body content."""


def _graph_get(path: str, params: dict) -> dict:
    url = f"{GRAPH_API_BASE}{path}?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise GraphError(f"{e.code} {e.reason}: {body}") from e


# ---------------------------------------------------------------------------
# Post-level harvests
# ---------------------------------------------------------------------------


def fetch_recent_posts(page_id: str, token: str, since_ts: int) -> list[dict]:
    """Return Page posts (feed + carousels) created at/after since_ts."""
    fields = (
        "id,created_time,"
        f"reactions.summary(true).limit({REACTION_PAGE_SIZE})"
        "{name,id,type,username,link},"
        f"comments.summary(true).limit({COMMENT_PAGE_SIZE})"
        "{from{id,name},message,created_time,id}"
    )
    resp = _graph_get(
        f"/{page_id}/posts",
        {"fields": fields, "since": since_ts, "limit": 50, "access_token": token},
    )
    return resp.get("data", [])


def fetch_recent_stories(page_id: str, token: str, since_ts: int) -> list[dict]:
    """Return Page Stories with reaction/comment edges where available.

    FB Stories expose a narrower surface than feed posts. The
    `/page-id/stories` edge was added in v22.0 and returns
    `{id, creation_time, media_type, media_id, status}`; reactions and
    replies come via `/story-id/reactions` + `/story-id/comments`
    secondary calls because they are NOT exposed as edges on the
    parent object as of Graph v25.0. This function absorbs the 2-step
    dance so the caller gets a uniform list of story+engagement dicts.
    """
    try:
        resp = _graph_get(
            f"/{page_id}/stories",
            {
                "fields": "id,creation_time,media_type,status,post_id",
                "since": since_ts,
                "limit": 50,
                "access_token": token,
            },
        )
    except GraphError as e:
        # The /stories edge requires an elevated scope (page_read_engagement
        # + business_management). If it 403s, we skip stories rather
        # than 500 the whole harvest.
        log.warning("stories edge unavailable (%s) — skipping story engagement", e)
        return []

    stories: list[dict] = []
    for story in resp.get("data", []):
        # FB's /stories edge occasionally returns rows without `id`
        # (stale rows, archived stories, or rows the token can't
        # deref). Skip anything we can't follow up on.
        if not story.get("id"):
            continue
        engagement = {"reactions": [], "comments": []}
        for edge in ("reactions", "comments"):
            try:
                edge_fields = (
                    "name,id,type" if edge == "reactions"
                    else "from{id,name},message,created_time,id"
                )
                er = _graph_get(
                    f"/{story['id']}/{edge}",
                    {
                        "fields": edge_fields,
                        "limit": REACTION_PAGE_SIZE if edge == "reactions" else COMMENT_PAGE_SIZE,
                        "access_token": token,
                    },
                )
                engagement[edge] = er.get("data", [])
            except GraphError as e:
                log.debug("story %s %s edge skipped: %s", story["id"], edge, e)
        story["engagement"] = engagement
        stories.append(story)
    return stories


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate_engagers(
    posts: list[dict], stories: list[dict]
) -> list[dict]:
    """Collapse every reactor / commenter across posts + stories into a
    per-user summary, sorted by total interactions desc.

    A note on the API's user-identity quirk: for Page posts, reactor
    `id`/`name` are populated only when the reactor has granted the
    Page's app visibility (Page admins, people the Page already
    interacts with, a user who's messaged the Page before). Strangers
    liking the Page show up in `summary.total_count` but NOT in
    `data[]`. Commenters always reveal their `from{id,name}` because
    comments are public content.
    """
    by_user: dict[str, dict] = defaultdict(
        lambda: {
            "user_id": None,
            "name": None,
            "profile_url": None,
            "reactions": 0,
            "comments": 0,
            "reaction_types": defaultdict(int),
            "comment_samples": [],
            "sources": [],  # list of (kind, id) tuples
        }
    )

    def _ingest_reactor(reactor: dict, source_kind: str, source_id: str) -> None:
        # Fallback key when API suppresses identity — we can still
        # count anonymised likes so Boss sees totals even if profile
        # links aren't resolvable.
        uid = reactor.get("id") or f"anon:{reactor.get('type', 'LIKE')}:{source_id}"
        bucket = by_user[uid]
        bucket["user_id"] = reactor.get("id")
        bucket["name"] = reactor.get("name") or bucket["name"]
        bucket["reactions"] += 1
        bucket["reaction_types"][reactor.get("type", "UNKNOWN")] += 1
        bucket["sources"].append({"kind": source_kind, "id": source_id})
        if reactor.get("id"):
            bucket["profile_url"] = f"https://www.facebook.com/{reactor['id']}"
        elif reactor.get("username"):
            bucket["profile_url"] = f"https://www.facebook.com/{reactor['username']}"

    def _ingest_commenter(comment: dict, source_kind: str, source_id: str) -> None:
        sender = comment.get("from") or {}
        uid = sender.get("id") or f"anon:comment:{source_id}:{comment.get('id')}"
        bucket = by_user[uid]
        bucket["user_id"] = sender.get("id")
        bucket["name"] = sender.get("name") or bucket["name"]
        bucket["comments"] += 1
        msg = (comment.get("message") or "").strip()
        if msg and len(bucket["comment_samples"]) < 3:
            bucket["comment_samples"].append(msg[:140])
        bucket["sources"].append({"kind": source_kind, "id": source_id})
        if sender.get("id"):
            bucket["profile_url"] = f"https://www.facebook.com/{sender['id']}"

    for post in posts:
        for reactor in (post.get("reactions") or {}).get("data", []):
            _ingest_reactor(reactor, "post", post["id"])
        for comment in (post.get("comments") or {}).get("data", []):
            _ingest_commenter(comment, "post", post["id"])

    for story in stories:
        engagement = story.get("engagement") or {}
        for reactor in engagement.get("reactions", []):
            _ingest_reactor(reactor, "story", story["id"])
        for comment in engagement.get("comments", []):
            _ingest_commenter(comment, "story", story["id"])

    out = []
    for uid, bucket in by_user.items():
        bucket_out = dict(bucket)
        bucket_out["user_id"] = bucket_out["user_id"] or uid
        bucket_out["reaction_types"] = dict(bucket["reaction_types"])
        bucket_out["total_interactions"] = bucket["reactions"] + bucket["comments"]
        out.append(bucket_out)

    out.sort(key=lambda r: (r["total_interactions"], r["comments"]), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Discord summary
# ---------------------------------------------------------------------------


def _load_bot_token() -> Optional[str]:
    """Read the shared Bubba Discord bot token from OpenClaw config.

    Returns None if the config is absent or malformed — the caller
    treats that as "skip Discord post this run" rather than failing
    the whole harvest.
    """
    if not OPENCLAW_CONFIG.exists():
        log.warning("Discord bot token: %s missing — skipping notify", OPENCLAW_CONFIG)
        return None
    try:
        cfg = json.loads(OPENCLAW_CONFIG.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Discord bot token: failed to read %s (%s)", OPENCLAW_CONFIG, e)
        return None
    token = cfg.get("channels", {}).get("discord", {}).get("token")
    if not token:
        log.warning("Discord bot token: channels.discord.token missing in config")
        return None
    return token


def post_bot_message(channel_id: str, content: str) -> bool:
    """Send a plain-content message to a Discord channel via the bot
    API. Returns True on 2xx, False on anything else. Never raises."""
    token = _load_bot_token()
    if not token:
        return False

    # Discord channel messages cap at 2000 characters. Trim politely
    # at the last newline we can find before the limit.
    if len(content) > 1990:
        content = content[:1990].rsplit("\n", 1)[0] + "\n…(truncated)"

    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    body = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bot {token}",
            "User-Agent": "farm-guardian-reciprocate (https://github.com/VoynichLabs/farm-guardian, 1.0)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8", errors="replace")
        log.warning("Discord post %s: %s %s", channel_id, e.code, body_err)
        return False
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        log.warning("Discord post %s: %s", channel_id, e)
        return False


def _plaintext_summary(
    engagers: list[dict],
    top_n: int,
    window_days: int,
) -> str:
    """Human-readable engager worklist written alongside the JSON so
    Boss can skim it without a JSON viewer. Lives at
    data/on-this-day/engagers-YYYY-MM-DD.txt. NO Discord output."""
    if not engagers:
        return (
            f"Reciprocate worklist — past {window_days}d\n"
            "No identifiable engagers yet. (FB Graph suppresses reactor\n"
            "id/name for non-app-connected users; likes still tally in\n"
            "post summary.total_count but not in data[].)\n"
        )

    lines = [
        f"Reciprocate worklist — past {window_days}d",
        f"{len(engagers)} engager(s) · top {min(top_n, len(engagers))} below.",
        "",
    ]
    for row in engagers[:top_n]:
        name = row.get("name") or "(identity hidden by API)"
        url = row.get("profile_url") or "—"
        r = row["reactions"]
        c = row["comments"]
        badge = []
        if r:
            rt = ",".join(f"{k}:{v}" for k, v in row["reaction_types"].items())
            badge.append(f"{r} reactions ({rt})")
        if c:
            badge.append(f"{c} comments")
        lines.append(f"• {name} — {' / '.join(badge)}")
        lines.append(f"    {url}")
        for sample in row["comment_samples"]:
            lines.append(f'    "{sample}"')
        lines.append("")

    lines.append(
        "FB Graph does not expose Page→user follows or likes — "
        "click through and act manually in the FB app."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def run(lookback_days: int, top_n: int) -> int:
    _source_meta_env_file()
    creds = _load_fb_credentials()
    page_id = creds["page_id"]
    token = creds["page_token"]

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=lookback_days)
    since_ts = int(since.timestamp())

    log.info("harvesting posts + stories since %s", since.isoformat())
    posts = fetch_recent_posts(page_id, token, since_ts)
    log.info("posts: %d", len(posts))
    stories = fetch_recent_stories(page_id, token, since_ts)
    log.info("stories: %d", len(stories))

    engagers = aggregate_engagers(posts, stories)
    log.info("engagers: %d distinct", len(engagers))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    out_path = OUTPUT_DIR / f"engagers-{today}.json"
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "posts_scanned": len(posts),
        "stories_scanned": len(stories),
        "engagers": engagers,
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info("wrote %s", out_path)

    # Plain-text summary alongside the JSON so a human browsing
    # data/on-this-day/ can read the worklist at a glance without
    # a JSON viewer. NO Discord side-channel — #farm-2026 is the
    # IG-gem curation channel and this content doesn't belong there.
    summary_text = _plaintext_summary(engagers, top_n, lookback_days)
    summary_path = OUTPUT_DIR / f"engagers-{today}.txt"
    summary_path.write_text(summary_text, encoding="utf-8")
    log.info("wrote %s", summary_path)

    # Notify Discord — engager channel only. Never #farm-2026.
    if post_bot_message(DISCORD_ENGAGERS_CHANNEL_ID, summary_text):
        log.info("posted summary to Discord channel %s", DISCORD_ENGAGERS_CHANNEL_ID)
    else:
        log.info("Discord notify skipped (bot token missing or post failed)")

    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Harvest FB Page engagers for manual reciprocation.",
    )
    p.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                   help=f"How many days back to scan. Default: {DEFAULT_LOOKBACK_DAYS}.")
    p.add_argument("--top-n", type=int, default=PLAINTEXT_TOP_N,
                   help=f"How many engagers to include in the plain-text "
                        f"summary. Default: {PLAINTEXT_TOP_N}.")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _parse_args()
    return run(
        lookback_days=args.lookback_days,
        top_n=args.top_n,
    )


if __name__ == "__main__":
    sys.exit(main())
