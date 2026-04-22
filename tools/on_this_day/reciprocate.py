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

# Discord webhook — reused from the farm-2026 channel pattern. Env
# var name matches what the existing archive-throwback + gem_poster
# scripts read (DISCORD_WEBHOOK_URL, loaded from .env via
# tools.pipeline.gem_poster.load_dotenv). That dotenv loader is the
# canonical way to get this on the Mac Mini — LaunchAgent env blocks
# don't inherit shell env, so we source the file explicitly.
DISCORD_WEBHOOK_ENV = "DISCORD_WEBHOOK_URL"

# How far back to scan. The Page is new (first FB post 2026-04-21), so
# 7 days is ample for now; widen later if we start scheduling fewer
# harvests.
DEFAULT_LOOKBACK_DAYS = 2

# Reaction-per-post page size. 25 is plenty for a Page with low
# volume; raise once we actually have high-engagement posts.
REACTION_PAGE_SIZE = 25
COMMENT_PAGE_SIZE = 25

# Top-N engagers to surface to Discord. Anything lower is unlikely to
# matter for reciprocation (they skimmed without clicking reactor
# ID); anything higher is Discord-message-length overkill.
DISCORD_TOP_N = 15

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


def format_discord_summary(
    engagers: list[dict],
    top_n: int,
    window_days: int,
) -> str:
    if not engagers:
        return (
            f"**Reciprocate worklist** · past {window_days}d · "
            "no identifiable engagers yet. (Likes without reactor identity "
            "don't expose name/profile-link via Graph API.)"
        )

    lines = [
        f"**Reciprocate worklist** · past {window_days}d · "
        f"{len(engagers)} engager(s) · top {min(top_n, len(engagers))} below."
    ]
    for row in engagers[:top_n]:
        name = row.get("name") or "(identity hidden by API)"
        url = row.get("profile_url") or "—"
        r = row["reactions"]
        c = row["comments"]
        badge = []
        if r:
            rt = ",".join(f"{k}:{v}" for k, v in row["reaction_types"].items())
            badge.append(f"{r}× reactions ({rt})")
        if c:
            badge.append(f"{c}× comments")
        sample = ""
        if row["comment_samples"]:
            sample = f"  _“{row['comment_samples'][0]}”_"
        lines.append(f"• **{name}** — {' / '.join(badge)} · <{url}>{sample}")

    lines.append(
        "\n_Click through to follow / friend / like-back manually — "
        "FB Graph does not expose those actions to Page tokens._"
    )
    return "\n".join(lines)


def post_to_discord(webhook_url: str, content: str) -> None:
    # Discord caps message content at 2000 chars — trim politely.
    if len(content) > 1900:
        content = content[:1900].rsplit("\n", 1)[0] + "\n…(truncated)"
    body = json.dumps({"content": content}).encode("utf-8")
    # Discord 403s the default Python-urllib User-Agent. Any non-bot
    # UA satisfies Cloudflare's WAF on the webhook endpoint.
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "farm-guardian-reciprocate/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"Discord webhook returned {resp.status}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _load_dotenv_if_available() -> None:
    """Source .env so DISCORD_WEBHOOK_URL is available inside a
    LaunchAgent (which inherits a bare environment). Reuses the
    canonical loader from tools.pipeline.gem_poster."""
    try:
        from tools.pipeline.gem_poster import load_dotenv as _ld
        _ld(_REPO_ROOT / ".env")
    except Exception as e:  # noqa: BLE001 — loader absence is non-fatal
        log.debug("dotenv loader unavailable: %s", e)


def run(lookback_days: int, top_n: int, notify: bool) -> int:
    _source_meta_env_file()
    _load_dotenv_if_available()
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

    if notify:
        webhook = os.environ.get(DISCORD_WEBHOOK_ENV)
        if not webhook:
            log.warning(
                "%s not set in env; Discord summary skipped", DISCORD_WEBHOOK_ENV,
            )
        else:
            try:
                post_to_discord(webhook, format_discord_summary(engagers, top_n, lookback_days))
                log.info("posted Discord summary (top %d)", top_n)
            except Exception:
                log.exception("Discord webhook failed")

    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Harvest FB Page engagers for manual reciprocation.",
    )
    p.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                   help=f"How many days back to scan. Default: {DEFAULT_LOOKBACK_DAYS}.")
    p.add_argument("--top-n", type=int, default=DISCORD_TOP_N,
                   help=f"How many engagers to surface to Discord. Default: {DISCORD_TOP_N}.")
    p.add_argument("--no-notify", action="store_true",
                   help="Skip Discord webhook; only write the JSON.")
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
        notify=not args.no_notify,
    )


if __name__ == "__main__":
    sys.exit(main())
