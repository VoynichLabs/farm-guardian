# Author: Claude Fable 5
# Date: 16-Jul-2026
# PURPOSE: Close the Instagram measurement loop (Part B of
#          farm-2026/docs/16-Jul-2026-birdcatraz-era-refresh-plan.md).
#          Today the pipeline is open-loop on Instagram — nothing ever
#          fetches how a post actually performed; the only signal is
#          Boss's Discord reaction on the PRE-post gem. This module adds:
#
#            - ensure_insights_schema()/fetch_media_insights()/
#              fetch_follower_count()/run_nightly_fetch() (B1): a nightly
#              per-media Graph API insights pull (likes, comments, reach,
#              saved, shares, plays) plus one daily follower-count
#              snapshot, written as a time series (one row per fetch, not
#              upserted) so engagement-over-time after posting is visible.
#            - build_weekly_digest()/post_weekly_digest() (B2): a short,
#              readable weekly recap posted to the #farm-2026 Discord
#              webhook — best/worst post, posts-by-surface, follower
#              delta.
#
#          Table ownership split (per implementing instructions): the
#          main session owns tools/pipeline/store.py and added the
#          ig_posted_captions ledger there today (id, posted_at, surface,
#          media_id, permalink, caption, tags_csv) — this module reads
#          that table but never migrates it. This module OWNS a new
#          table, ig_media_insights, and creates it itself via
#          ensure_insights_schema()'s idempotent CREATE TABLE IF NOT
#          EXISTS, called at the top of every entry point below. SQLite
#          doesn't care which .py module issues DDL, so a second module
#          owning a second table alongside store.py's tables is safe.
#
#          Metric names were verified live against the real
#          @pawel_and_pawleen account on 16-Jul-2026 (read-only GETs,
#          see the ig-insights-fetch.py --dry-run report) rather than
#          assumed from training-data recall — see the _REEL_METRICS
#          comment below for what changed and why.
#
# SRP/DRY check: Pass — single responsibility is IG performance
#                measurement (fetch + persist + summarize). Reuses
#                ig_poster.py's _load_credentials/_graph_request/
#                GRAPH_API_BASE/GRAPH_API_VERSION for all Graph API
#                access (no second HTTP client, no duplicated credential
#                loading), and the exact {"username","content"} Discord
#                webhook POST pattern already used by
#                scripts/pipeline-digest.py (no new posting helper).
#                Does not touch store.py, ig_posted_captions' schema, or
#                any posting/selection code — read-only consumer of the
#                ledger those own.

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

from tools.pipeline.ig_poster import GRAPH_API_VERSION, IGPosterError, _graph_request, _load_credentials

log = logging.getLogger("pipeline.ig_insights")

# ---------------------------------------------------------------------------
# Schema (owned by this module — see header note on the store.py split)
# ---------------------------------------------------------------------------

_INSIGHTS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ig_media_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id TEXT NOT NULL,
    surface TEXT,
    permalink TEXT,
    fetched_at TEXT NOT NULL,
    likes INTEGER,
    comments INTEGER,
    reach INTEGER,
    saved INTEGER,
    shares INTEGER,
    plays INTEGER,
    follower_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_media_insights_media_id ON ig_media_insights(media_id);
CREATE INDEX IF NOT EXISTS idx_media_insights_fetched_at ON ig_media_insights(fetched_at);
"""


def ensure_insights_schema(db_path: Path) -> None:
    """Idempotent CREATE TABLE IF NOT EXISTS — safe to call from every
    entry point below (fetch, digest, CLI) on every invocation."""
    with sqlite3.connect(str(db_path)) as c:
        c.executescript(_INSIGHTS_SCHEMA_SQL)
        c.commit()


# ---------------------------------------------------------------------------
# Graph API metric sets
# ---------------------------------------------------------------------------

# v21 Graph API metric names differ by IG media_product_type, and Meta's
# accepted list has drifted since older training data / docs were written
# (same family of change as the "impressions" deprecation the implementing
# task already called out). Verified LIVE on 16-Jul-2026 against a real
# reel media_id on @pawel_and_pawleen:
#   - "plays" is REJECTED by v21 with a 400 ("metric[0] must be one of the
#     following values: ... views ..."). "views" is the current name.
#   - "views,reach,likes,comments,shares,saved,total_interactions"
#     succeeds for REELS media_product_type.
#   - "reach,likes,comments,saved,shares" succeeds for FEED
#     (photo/CAROUSEL_ALBUM) media_product_type, exactly as specified.
# The ig_media_insights table column is still named "plays" (matches the
# agreed schema) — _parse_insights_response() below normalizes the Graph
# API's "views" metric name into our "plays" key at parse time, so the
# column name is our internal vocabulary and doesn't have to track
# whatever Meta calls it this API version.
_REEL_METRICS = ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions"]
_FEED_METRICS = ["reach", "likes", "comments", "saved", "shares"]  # photo + carousel
_SAFE_METRICS = ["reach", "likes", "comments"]  # fallback if the full set 400s

_DIGEST_DISCORD_USERNAME = "farm-pipeline"  # matches scripts/pipeline-digest.py's convention


def _metrics_for_surface(surface: str) -> list[str]:
    return _REEL_METRICS if surface == "reel" else _FEED_METRICS


def _describe_graph_error(e: Exception) -> str:
    """Best-effort extraction of the Graph API's JSON error body from an
    urllib HTTPError, for actionable log lines (e.g. which metric name
    got rejected). Falls back to str(e) for non-HTTP failures."""
    body = None
    reader = getattr(e, "read", None)
    if callable(reader):
        try:
            body = reader().decode("utf-8", errors="replace")
        except Exception:
            body = None
    return f"{e} — {body}" if body else str(e)


def _parse_insights_response(raw: dict) -> dict:
    """Flatten the Graph API's {data: [{name, values: [{value}]}]} shape
    into {metric_name: value}. Normalizes "views" -> "plays" (see
    _REEL_METRICS comment). Tolerant of missing/malformed entries —
    always returns whatever it could parse rather than raising."""
    flat: dict = {}
    for entry in (raw or {}).get("data") or []:
        name = entry.get("name")
        values = entry.get("values") or []
        if not name or not values:
            continue
        value = values[0].get("value")
        if name == "views":
            name = "plays"
        flat[name] = value
    return flat


def fetch_media_insights(media_id: str, surface: str, creds: dict) -> dict:
    """GET /{media_id}/insights with the metric set appropriate to
    surface ('reel' vs 'carousel'/'photo' — both feed types share the
    same metric set). Never raises: a media that's too old, or a metric
    unsupported for its type, can 400 the whole call (Meta's batch
    behavior varies) — on any failure this retries once with a reduced/
    safe metric set, and returns {} (not an exception) if that also
    fails, so one bad media never kills the nightly run."""
    metrics = _metrics_for_surface(surface)
    try:
        raw = _graph_request(
            "GET",
            f"/{media_id}/insights",
            params={"metric": ",".join(metrics), "access_token": creds["user_token"]},
        )
        return _parse_insights_response(raw)
    except Exception as e:
        log.warning(
            "ig_insights: full metric set failed for media_id=%s surface=%s (%s) — retrying with safe set",
            media_id, surface, _describe_graph_error(e),
        )

    try:
        raw = _graph_request(
            "GET",
            f"/{media_id}/insights",
            params={"metric": ",".join(_SAFE_METRICS), "access_token": creds["user_token"]},
        )
        return _parse_insights_response(raw)
    except Exception as e:
        log.warning(
            "ig_insights: safe metric set also failed for media_id=%s (%s) — returning partial (empty) data",
            media_id, _describe_graph_error(e),
        )
        return {}


def fetch_follower_count(creds: dict) -> Optional[int]:
    """GET /{ig_id}?fields=followers_count. Returns None (never raises)
    on any failure — a missed follower snapshot on one run just means
    that run's insight rows carry a null follower_count."""
    try:
        raw = _graph_request(
            "GET",
            f"/{creds['ig_id']}",
            params={"fields": "followers_count", "access_token": creds["user_token"]},
        )
        return raw.get("followers_count")
    except Exception as e:
        log.warning("ig_insights: fetch_follower_count failed (%s)", _describe_graph_error(e))
        return None


# ---------------------------------------------------------------------------
# Nightly fetch (B1)
# ---------------------------------------------------------------------------


def _insert_insight_row(
    db_path: Path,
    media_id: str,
    surface: Optional[str],
    permalink: Optional[str],
    insights: dict,
    follower_count: Optional[int],
) -> None:
    """Always INSERT a fresh row — this is a time series (engagement
    grows after posting), never an upsert."""
    with sqlite3.connect(str(db_path)) as c:
        c.execute(
            """
            INSERT INTO ig_media_insights
                (media_id, surface, permalink, fetched_at, likes, comments, reach, saved, shares, plays, follower_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                media_id,
                surface,
                permalink,
                datetime.now(timezone.utc).isoformat(),
                insights.get("likes"),
                insights.get("comments"),
                insights.get("reach"),
                insights.get("saved"),
                insights.get("shares"),
                insights.get("plays"),
                follower_count,
            ),
        )
        c.commit()


def run_nightly_fetch(db_path: Path, lookback_days: int = 14) -> dict:
    """B1 nightly job: for every distinct media_id posted (per
    ig_posted_captions) within lookback_days, fetch fresh insights and
    insert a new ig_media_insights row. Fetches the account's follower
    count ONCE per run and stamps it onto every row inserted this run
    (not once per media — that would be redundant Graph API load for a
    number that only changes at daily granularity). Rate-limited gently
    (0.3s between per-media calls). Never raises except IGPosterError at
    the credential gate — a single media's fetch failure is swallowed by
    fetch_media_insights() and recorded as an empty-insights row rather
    than aborting the run.
    """
    ensure_insights_schema(db_path)
    creds = _load_credentials()  # raises IGPosterError if misconfigured — loud on purpose

    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    with sqlite3.connect(str(db_path)) as c:
        rows = c.execute(
            """
            SELECT DISTINCT media_id, surface, permalink
              FROM ig_posted_captions
             WHERE media_id IS NOT NULL
               AND posted_at >= ?
            """,
            (cutoff,),
        ).fetchall()

    follower_count = fetch_follower_count(creds)

    checked = 0
    succeeded = 0
    failed = 0
    for media_id, surface, permalink in rows:
        checked += 1
        insights = fetch_media_insights(media_id, surface or "photo", creds)
        _insert_insight_row(db_path, media_id, surface, permalink, insights, follower_count)
        if insights:
            succeeded += 1
        else:
            failed += 1
        time.sleep(0.3)

    summary = {
        "checked": checked,
        "succeeded": succeeded,
        "failed": failed,
        "follower_count": follower_count,
        "lookback_days": lookback_days,
    }
    log.info(
        "ig_insights: nightly fetch complete — %d media checked, %d succeeded, %d failed (followers=%s)",
        checked, succeeded, failed, follower_count,
    )
    return summary


def _select_probe_media(db_path: Path) -> Optional[tuple]:
    """Most recently posted media_id/surface/permalink from
    ig_posted_captions, or None if the ledger has no media_id rows yet
    (e.g. freshly migrated DB, or no lane has posted since it landed)."""
    try:
        with sqlite3.connect(str(db_path)) as c:
            row = c.execute(
                """
                SELECT media_id, surface, permalink
                  FROM ig_posted_captions
                 WHERE media_id IS NOT NULL
                 ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        return tuple(row) if row else None
    except Exception:
        return None


def dry_run_probe(db_path: Path) -> dict:
    """CLI --dry-run entry point: prove the Graph API integration works
    end-to-end WITHOUT the full nightly loop or any DB insert. Boss
    doesn't want live testing on every dry run, so this makes at most
    two real, read-only Graph API calls (one follower-count fetch, one
    media-insights fetch) rather than looping the whole lookback window.

    If ig_posted_captions has no media_id rows yet, the media-insights
    leg is skipped and reported as such (not an error) — the
    follower-count leg still runs so credentials/API version are still
    exercised even between posts.
    """
    ensure_insights_schema(db_path)
    result: dict = {"dry_run": True, "graph_api_version": GRAPH_API_VERSION}

    creds = _load_credentials()  # let IGPosterError escape — CLI reports exit 3

    result["follower_count"] = fetch_follower_count(creds)

    probe = _select_probe_media(db_path)
    if probe is None:
        result["media_probe"] = "skipped — ig_posted_captions has no media_id rows yet"
        return result

    media_id, surface, permalink = probe
    insights = fetch_media_insights(media_id, surface or "photo", creds)
    result["media_probe"] = {
        "media_id": media_id,
        "surface": surface,
        "permalink": permalink,
        "insights": insights,
    }
    return result


# ---------------------------------------------------------------------------
# Weekly digest (B2)
# ---------------------------------------------------------------------------


def _posts_in_last_days(db_path: Path, days: int = 7) -> list[dict]:
    """Distinct posts (from ig_posted_captions) actually PUBLISHED in the
    last `days` days, by posted_at — this is the "posts this week"
    universe for the digest's surface counts and best/worst ranking."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            """
            SELECT media_id, surface, caption, posted_at
              FROM ig_posted_captions
             WHERE posted_at >= ?
               AND media_id IS NOT NULL
             ORDER BY posted_at ASC
            """,
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def _latest_insight_for_media(db_path: Path, media_id: str) -> Optional[dict]:
    """Latest (highest id) ig_media_insights row for one media_id,
    regardless of when it was fetched — a post from 6 days ago may only
    have been checked once, and that one check is still the best
    engagement estimate we have for it."""
    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            """
            SELECT * FROM ig_media_insights
             WHERE media_id = ?
             ORDER BY id DESC LIMIT 1
            """,
            (media_id,),
        ).fetchone()
    return dict(row) if row else None


def _follower_delta_last_days(db_path: Path, days: int = 7) -> Optional[int]:
    """First vs last follower_count SAMPLE (by fetched_at) within the
    window — None if fewer than two samples exist yet (can't compute a
    delta from a single snapshot)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with sqlite3.connect(str(db_path)) as c:
        rows = c.execute(
            """
            SELECT follower_count FROM ig_media_insights
             WHERE fetched_at >= ?
               AND follower_count IS NOT NULL
             ORDER BY fetched_at ASC
            """,
            (cutoff,),
        ).fetchall()
    values = [r[0] for r in rows]
    if len(values) < 2:
        return None
    return values[-1] - values[0]


def _excerpt(caption: str, max_chars: int = 80) -> str:
    """First line of a caption, trimmed to max_chars for a one-line
    Discord digest entry."""
    first_line = (caption or "").strip().split("\n")[0].strip()
    if len(first_line) <= max_chars:
        return first_line
    return first_line[:max_chars].rstrip() + "…"


def build_weekly_digest(db_path: Path) -> str:
    """Short, readable Discord message summarizing the last 7 days of
    Instagram performance: best/worst post by (likes+comments+saved),
    posts-by-surface, follower delta. Never raises on empty data —
    returns a "nothing to report" message instead."""
    posts = _posts_in_last_days(db_path, days=7)
    if not posts:
        return "📊 **Weekly Instagram digest** — no posts recorded this week."

    scored = []
    for p in posts:
        insight = _latest_insight_for_media(db_path, p["media_id"])
        engagement = 0
        if insight:
            engagement = (insight.get("likes") or 0) + (insight.get("comments") or 0) + (insight.get("saved") or 0)
        scored.append({**p, "engagement": engagement, "has_insight": insight is not None})

    ranked = sorted((s for s in scored if s["has_insight"]), key=lambda s: s["engagement"], reverse=True)

    surface_counts: dict[str, int] = {}
    for p in posts:
        key = p["surface"] or "unknown"
        surface_counts[key] = surface_counts.get(key, 0) + 1

    follower_delta = _follower_delta_last_days(db_path, days=7)

    lines = ["📊 **Weekly Instagram digest**", ""]

    if ranked:
        best = ranked[0]
        lines.append(f"Best post: **{best['surface']}** — {best['engagement']} engagement (likes+comments+saved)")
        excerpt = _excerpt(best.get("caption") or "")
        if excerpt:
            lines.append(f'  "{excerpt}"')
        if len(ranked) > 1 and ranked[-1]["media_id"] != best["media_id"]:
            worst = ranked[-1]
            lines.append(f"Worst post: **{worst['surface']}** — {worst['engagement']} engagement")
            excerpt = _excerpt(worst.get("caption") or "")
            if excerpt:
                lines.append(f'  "{excerpt}"')
    else:
        lines.append("No insights data collected yet for this week's posts.")

    lines.append("")
    surface_line = "  ·  ".join(f"{s} {n}" for s, n in sorted(surface_counts.items()))
    lines.append(f"Posts this week: {surface_line}  ({len(posts)} total)")

    if follower_delta is not None:
        sign = "+" if follower_delta >= 0 else ""
        lines.append(f"Followers: {sign}{follower_delta} this week")

    return "\n".join(lines)


def post_weekly_digest(db_path: Path) -> bool:
    """Build the digest and POST it to DISCORD_WEBHOOK_URL, reusing the
    exact {"username","content"} payload shape scripts/pipeline-digest.py
    already uses for non-urgent pipeline status messages (no @mention —
    informational, not urgent). Returns False (never raises) on any
    failure so a bad digest run doesn't take down whatever scheduled it."""
    message = build_weekly_digest(db_path)
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        log.error("ig_insights: DISCORD_WEBHOOK_URL missing from environment")
        return False

    payload = {"username": _DIGEST_DISCORD_USERNAME, "content": message}
    try:
        r = requests.post(webhook_url, json=payload, timeout=15)
        if 200 <= r.status_code < 300:
            log.info("ig_insights: weekly digest posted to Discord")
            return True
        log.warning("ig_insights: discord POST returned %d — %s", r.status_code, r.text[:200])
        return False
    except requests.RequestException as e:
        log.warning("ig_insights: discord POST failed: %s", e)
        return False


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    _cfg = json.loads((Path(__file__).parent / "config.json").read_text())
    _repo = Path(__file__).resolve().parents[2]
    _db = _repo / _cfg["guardian_db_path"]
    _dry = "--dry-run" in sys.argv

    if _dry:
        print(json.dumps(dry_run_probe(_db), indent=2))
    else:
        print(json.dumps(run_nightly_fetch(_db), indent=2))
