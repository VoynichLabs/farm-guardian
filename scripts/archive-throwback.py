#!/usr/bin/env python3
# Author: Claude Opus 4.7 (1M context)
# Date: 20-April-2026
# PURPOSE: Slow-day content pipeline — post archive photos to Discord
#          #farm-2026 so Boss can react to the ones he wants surfaced
#          on Instagram. When Boss reacts, the existing drop-ingest
#          path (discord-reaction-sync.py v2.33.0) picks them up and
#          they flow into the scheduled IG lanes automatically. The
#          reaction gate stays the sole quality filter — throwback
#          just ensures there's ALWAYS material sitting in Discord
#          for Boss to curate, even on days when the brooder is quiet
#          or Boss is traveling.
#
#          Two content sources, both live:
#            1. Photos Library catalog — 21,640 photos cataloged by
#               LM Studio + Qwen at /Users/macmini/bubba-workspace/
#               projects/photos-curation/photo-catalog/master-catalog.csv
#               with full VLM metadata (scene, subjects, aesthetic
#               tags, etc). Filtered to farm/pet/family content by
#               keyword score.
#            2. farm-2026/public/photos/<dir>/ — photos already
#               curated to the farm's public website via the existing
#               discord_harvester flow. Safe to re-surface to IG.
#
#          State file tracks what's already been thrown back so we
#          don't repeat. Each day the LaunchAgent sends N candidates
#          (default 3 catalog + 2 gallery = 5 total) to Discord;
#          Boss reacts; IG lanes take it from there.
#
# SRP/DRY check: Pass — single responsibility is "pick N archive
#                photos and post them to Discord for curation." The
#                downstream flow (reaction sync -> image_archive ->
#                IG lanes) is entirely the existing machinery; this
#                script doesn't touch image_archive directly.

"""
archive-throwback.py — daily archive content → Discord for Boss curation.

Invocation:
  LaunchAgent cadence: daily 08:00 local
  (deploy/ig-scheduled/com.farmguardian.archive-throwback.plist).

  Manual (testing):
    venv/bin/python scripts/archive-throwback.py [--dry-run]
                   [--n-from-catalog N] [--n-from-gallery N]

Exit codes:
  0 — all posts succeeded (or dry-run)
  1 — one or more posts failed (logged individually)
  2 — config error (webhook missing, catalog unreachable)
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

CATALOG_CSV = Path(
    "/Users/macmini/bubba-workspace/projects/photos-curation/"
    "photo-catalog/master-catalog.csv"
)
GALLERY_ROOT = Path.home() / "Documents" / "GitHub" / "farm-2026" / "public" / "photos"
STATE_FILE = REPO_ROOT / "data" / "archive-throwback-state.json"

# Webhook username shown on each Discord throwback post. NOT mapped to a
# Guardian camera in gem_poster._USERNAME_BY_CAMERA, so discord-reaction-
# sync's _camera_for_username returns None and the post flows through
# the drop-ingest path when reacted.
_THROWBACK_USERNAME = "Archive"

# Keyword-based scoring for catalog content. Higher score = stronger
# farm-brand signal. _FARM_KEYWORDS_HIGH is the money tier (Boss and
# his specifically-named dogs). The whole catalog is keyword-searched
# across scene_description + primary_subjects + aesthetic_tags.
_KEYWORDS_HIGH = ["pawel", "pawleen"]
_KEYWORDS_MED = [
    "yorkie", "yorkshire", "chicken", "chick",
    "rooster", "hen", "coop", "brooder",
]
_KEYWORDS_LOW = [
    "farm", "barn", "dog", "puppy", "kitten", "cat",
    "garden", "orchard", "field", "tractor", "rural",
    "goat", "cow", "horse",
]

# Minimum combined score for a catalog row to qualify.
_MIN_SCORE = 2

# Gallery dirs to NEVER pull from — these are the IG-posting destinations
# we populate ourselves (would create a feedback loop), and yard-diary
# is the year-end timelapse stockpile that shouldn't drip out.
_GALLERY_BLOCKLIST = {
    "brooder", "carousel", "stories", "yard-diary", "guardian-detections",
}

# Month names — used to recognize month-year dirs like "april-2026".
_MONTH_NAMES = {
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
}

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic"}


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            logging.getLogger("archive-throwback").warning(
                "state file corrupt, starting fresh: %s", STATE_FILE,
            )
    return {
        "sent_catalog_uuids": [],
        "sent_gallery_paths": [],
        "last_run": None,
    }


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# Catalog scoring + reading
# ---------------------------------------------------------------------------


def _score_catalog_row(row: dict) -> int:
    """Combine scene_description, primary_subjects, aesthetic_tags
    into a single lowercase haystack and count keyword hits weighted
    by tier. Each hit adds; same keyword hitting multiple fields only
    counts once via the `in` test (cheap good-enough)."""
    haystack = " ".join([
        row.get("scene_description") or "",
        row.get("primary_subjects") or "",
        row.get("aesthetic_tags") or "",
    ]).lower()
    score = 0
    for kw in _KEYWORDS_HIGH:
        if kw in haystack:
            score += 10
    for kw in _KEYWORDS_MED:
        if kw in haystack:
            score += 5
    for kw in _KEYWORDS_LOW:
        if kw in haystack:
            score += 2
    return score


def _read_catalog(already_sent: set[str]) -> list[dict]:
    """Stream the catalog, score each row, keep ones that qualify and
    aren't in the already-sent set. Returns a list sorted by score
    descending. Catalog is 21k rows; streaming keeps memory flat."""
    log = logging.getLogger("archive-throwback")
    if not CATALOG_CSV.exists():
        log.warning("catalog not found at %s", CATALOG_CSV)
        return []
    out: list[dict] = []
    with CATALOG_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            uuid = row.get("uuid", "")
            if not uuid or uuid in already_sent:
                continue
            score = _score_catalog_row(row)
            if score < _MIN_SCORE:
                continue
            # Store score for sort; don't mutate original dict to keep
            # reading side-effect-free below.
            entry = dict(row)
            entry["_score"] = score
            out.append(entry)
    out.sort(key=lambda r: r["_score"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Gallery scanning
# ---------------------------------------------------------------------------


def _is_month_dir(name: str) -> bool:
    parts = name.split("-")
    return len(parts) == 2 and parts[0] in _MONTH_NAMES and parts[1].isdigit()


def _read_gallery(already_sent: set[str]) -> list[Path]:
    """Walk farm-2026/public/photos/, return every image file in a
    permitted subdirectory that isn't in the already-sent set.

    Permitted subdirs:
      - Any "monthname-YYYY" dir (harvester output).
      - Explicitly allowed curated dirs (birds, coop, enclosure, history).
    Blocked subdirs: brooder, carousel, stories, yard-diary,
    guardian-detections (see _GALLERY_BLOCKLIST)."""
    out: list[Path] = []
    if not GALLERY_ROOT.exists():
        return out
    for child in sorted(GALLERY_ROOT.iterdir()):
        if not child.is_dir():
            continue
        if child.name in _GALLERY_BLOCKLIST:
            continue
        if not (_is_month_dir(child.name) or child.name in {
            "birds", "coop", "enclosure", "history",
        }):
            continue
        for p in sorted(child.iterdir()):
            if p.suffix.lower() not in _IMAGE_EXTS:
                continue
            rel = f"{child.name}/{p.name}"
            if rel in already_sent:
                continue
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# HEIC conversion + Discord post
# ---------------------------------------------------------------------------


def _convert_to_jpeg(src: Path) -> Path:
    """If src is HEIC (or anything non-JPEG), produce a JPEG in /tmp
    via sips. JPEG sources pass through unchanged. Caller deletes
    the temp file if it's distinct from src."""
    ext = src.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return src
    out = Path(f"/tmp/throwback-{int(time.time() * 1000)}-{src.stem}.jpg")
    proc = subprocess.run(
        ["sips", "-s", "format", "jpeg", str(src), "--out", str(out)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(
            f"sips conversion failed for {src}: rc={proc.returncode} "
            f"stderr={proc.stderr.strip()}"
        )
    return out


def _post_to_discord(
    image_path: Path,
    caption: str,
    webhook_url: str,
    username: str = _THROWBACK_USERNAME,
    timeout: int = 30,
) -> bool:
    """POST the image + caption to Discord webhook. Returns True on
    2xx. Never raises — failures logged at caller level."""
    import requests
    content = (caption or "Archive throwback.").strip()
    if len(content) > 1900:
        content = content[:1900] + "…"
    try:
        with image_path.open("rb") as f:
            r = requests.post(
                webhook_url,
                files={"file": (image_path.name, f.read(), "image/jpeg")},
                data={"payload_json": json.dumps(
                    {"username": username, "content": content}
                )},
                timeout=timeout,
            )
    except requests.RequestException as e:
        logging.getLogger("archive-throwback").warning(
            "discord post request failed: %s", e,
        )
        return False
    if 200 <= r.status_code < 300:
        return True
    logging.getLogger("archive-throwback").warning(
        "discord post rejected http=%d body=%r",
        r.status_code, (r.text or "")[:200],
    )
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _gallery_caption(path: Path) -> str:
    """Simple default caption for a gallery file. The VLM already saw
    these when they were originally posted to Discord as gems; since
    we don't have that metadata here, keep the caption generic."""
    parent = path.parent.name.replace("-", " ").title()
    return f"From the archive — {parent}."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Daily Discord throwback of archive photos for Boss curation.",
    )
    parser.add_argument("--n-from-catalog", type=int, default=3)
    parser.add_argument("--n-from-gallery", type=int, default=2)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Select candidates + log without posting or updating state.",
    )
    args = parser.parse_args(argv)

    _setup_logging()
    log = logging.getLogger("archive-throwback")

    # Resolve webhook (shared with gem_poster — loaded from .env).
    from tools.pipeline.gem_poster import load_dotenv  # noqa: E402
    load_dotenv(REPO_ROOT / ".env")
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        log.error("DISCORD_WEBHOOK_URL missing from environment")
        return 2

    state = _load_state()
    sent_uuids = set(state.get("sent_catalog_uuids") or [])
    sent_paths = set(state.get("sent_gallery_paths") or [])
    log.info(
        "state: %d catalog uuids, %d gallery paths already sent",
        len(sent_uuids), len(sent_paths),
    )

    # Build candidate pools.
    catalog_all = _read_catalog(sent_uuids)
    log.info("catalog candidates: %d (farm-keyword-scored)", len(catalog_all))

    # Shuffle the top slice so we get variety, not always the same top-
    # scoring photos. Pool size = 5x the request count (or 20, whichever
    # is larger) — plenty of room for randomness without diluting quality.
    pool_size = max(args.n_from_catalog * 5, 20)
    catalog_pool = catalog_all[:pool_size]
    random.shuffle(catalog_pool)
    catalog_picks = catalog_pool[: args.n_from_catalog]

    gallery_all = _read_gallery(sent_paths)
    log.info("gallery candidates: %d", len(gallery_all))
    random.shuffle(gallery_all)
    gallery_picks = gallery_all[: args.n_from_gallery]

    log.info(
        "posting %d from catalog + %d from gallery (dry_run=%s)",
        len(catalog_picks), len(gallery_picks), args.dry_run,
    )

    posts_ok = 0
    posts_fail = 0
    picked_uuids: list[str] = []
    picked_paths: list[str] = []

    # --- Catalog ---
    for entry in catalog_picks:
        uuid = entry["uuid"]
        source_path = Path(entry["source_path"])
        score = entry.get("_score", 0)
        scene = (entry.get("scene_description") or "").strip()
        caption = scene[:500] if scene else "From the archive."
        log.info(
            "catalog throwback uuid=%s score=%d source=%s",
            uuid[:8], score, source_path.name,
        )
        if not source_path.exists():
            log.warning("source missing, skipping: %s", source_path)
            posts_fail += 1
            continue
        if args.dry_run:
            posts_ok += 1
            picked_uuids.append(uuid)
            continue
        jpg: Path | None = None
        try:
            jpg = _convert_to_jpeg(source_path)
            if _post_to_discord(jpg, caption, webhook_url):
                posts_ok += 1
                picked_uuids.append(uuid)
            else:
                posts_fail += 1
        except Exception as e:
            log.exception("catalog throwback uuid=%s failed: %s", uuid, e)
            posts_fail += 1
        finally:
            # Delete the temp JPEG if sips produced a distinct file.
            if jpg is not None and jpg != source_path and jpg.exists():
                try:
                    jpg.unlink()
                except OSError:
                    pass
        time.sleep(2)  # gentle pacing so Discord doesn't batch-collapse

    # --- Gallery ---
    for path in gallery_picks:
        rel = f"{path.parent.name}/{path.name}"
        caption = _gallery_caption(path)
        log.info("gallery throwback path=%s", rel)
        if args.dry_run:
            posts_ok += 1
            picked_paths.append(rel)
            continue
        jpg: Path | None = None
        try:
            jpg = _convert_to_jpeg(path)
            if _post_to_discord(jpg, caption, webhook_url):
                posts_ok += 1
                picked_paths.append(rel)
            else:
                posts_fail += 1
        except Exception as e:
            log.exception("gallery throwback path=%s failed: %s", rel, e)
            posts_fail += 1
        finally:
            if jpg is not None and jpg != path and jpg.exists():
                try:
                    jpg.unlink()
                except OSError:
                    pass
        time.sleep(2)

    # Persist state (only on real runs — dry-run leaves nothing changed).
    if not args.dry_run:
        state["sent_catalog_uuids"] = sorted(sent_uuids | set(picked_uuids))
        state["sent_gallery_paths"] = sorted(sent_paths | set(picked_paths))
        state["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _save_state(state)

    log.info(
        "done: posts_ok=%d posts_fail=%d dry_run=%s",
        posts_ok, posts_fail, args.dry_run,
    )
    return 0 if posts_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
