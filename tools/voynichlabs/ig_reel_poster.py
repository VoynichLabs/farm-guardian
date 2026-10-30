# Author: Claude Opus 5.5 Medium
# Date: 29-Oct-2026
# PURPOSE: Posts the next VoynichLabs music-video Reel to the @voynichlabs Instagram account, one per run.
#          The queue is captions.json in the music-videos instagram folder on the Samsung SSD (posting order,
#          file name, YouTube id, caption); the vertical cuts sit beside it in reels/. State of what has
#          already gone out is posted.json in the same folder.
#
#          Why it looks different from the farm lanes: the Boss's Facebook account is barred from linking
#          Pages to Instagram, so this account uses the Instagram API with Instagram login. Calls go to
#          graph.instagram.com with the Instagram user id and an Instagram user key read from
#          /Users/macmini/bubba-workspace/secrets/ai-literacy-meta.env (renewed weekly by
#          ai.bubba.voynichlabs-ig-token-refresh). There is no Facebook cross-post.
#
#          Hosting reuses the farm's reel-asset route (tools.pipeline.ig_poster._publish_reel_asset): the
#          MP4 is copied to data/reel-assets/ and served at guardian.markbarney.net/.../reel-assets/<name>.mp4
#          through the Cloudflare tunnel, then swept after 48 h by the farm's asset sweep.
#
#          Run by LaunchAgent ai.bubba.voynichlabs-ig-reel (weekly, Thursdays 19:00). --dry-run hosts nothing and posts nothing.
# SRP/DRY check: Pass - reuses the farm's asset hosting, MP4 sanity check and Discord mention id; the
#          Graph calls are separate because the farm path is hard-wired to graph.facebook.com and Page keys.
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.ig_poster import _ffprobe_sanity, _publish_reel_asset  # noqa: E402

QUEUE_DIR = Path("/Volumes/Samsung 9100 SSD/music-videos/instagram")
CAPTIONS_FILE = QUEUE_DIR / "captions.json"
POSTED_FILE = QUEUE_DIR / "posted.json"
# Reels Meta refused to process, with attempt counts; skipped after MAX_ATTEMPTS
# so one bad file cannot stall the whole queue.
FAILED_FILE = QUEUE_DIR / "failed.json"
MAX_ATTEMPTS = 3
ENV_FILE = Path("/Users/macmini/bubba-workspace/secrets/ai-literacy-meta.env")
PIPELINE_CONFIG = REPO_ROOT / "tools" / "pipeline" / "config.json"
# Reels can take minutes to process on Meta's side; the farm's 30 s photo
# timeout is far too short for a three-minute music video.
CONTAINER_TIMEOUT_S = 600
CONTAINER_POLL_S = 10

log = logging.getLogger("voynichlabs-ig-reel")


def _read_env(path: Path) -> dict:
    return {
        line.split("=", 1)[0]: line.split("=", 1)[1].strip()
        for line in path.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    }


def _load_posted() -> list[dict]:
    return json.loads(POSTED_FILE.read_text()) if POSTED_FILE.exists() else []


def _save_posted(posted: list[dict]) -> None:
    temp = POSTED_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(posted, indent=2, ensure_ascii=False))
    temp.replace(POSTED_FILE)


def _scrub(text: object) -> str:
    # requests puts the full URL, access_token included, into connection-error
    # messages; these go to Discord and the log, so blank any token first.
    return re.sub(r"access_token=[^&\s'\")]+", "access_token=<hidden>", str(text))


def _graph(method: str, url: str, **params) -> dict:
    try:
        resp = requests.request(method, url, data=params if method == "POST" else None,
                                params=params if method == "GET" else None, timeout=60)
    except requests.RequestException as exc:
        # Re-raise without the URL-bearing message so no caller can leak the key.
        raise RuntimeError(f"{method} {url.split('?')[0]} failed: {type(exc).__name__}: {_scrub(exc)}") from None
    body = resp.json() if resp.content else {}
    if resp.status_code != 200 or "error" in body:
        raise RuntimeError(f"{method} {url.split('?')[0]} -> {resp.status_code}: {body.get('error', body)}")
    return body


def _notify_discord(text: str) -> None:
    # Same webhook the farm digest uses; the farm .env holds it. Best-effort.
    from tools.pipeline.gem_poster import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        return
    try:
        requests.post(webhook, json={"username": "voynichlabs-instagram", "content": text}, timeout=15)
    except requests.RequestException as exc:
        log.warning("discord notice failed: %s", exc)


def run(dry_run: bool) -> int:
    if not QUEUE_DIR.is_dir():
        # Boss rule: big media lives on the Samsung SSD; never fall back to the internal drive.
        log.error("Samsung SSD not mounted (%s missing); skipping", QUEUE_DIR)
        return 2
    queue = json.loads(CAPTIONS_FILE.read_text())
    posted = _load_posted()
    done = {row["file"] for row in posted}
    failed = json.loads(FAILED_FILE.read_text()) if FAILED_FILE.exists() else {}
    pending = [row for row in queue if row["file"] not in done and failed.get(row["file"], 0) < MAX_ATTEMPTS]
    if not pending:
        log.info("queue empty: all %d reels posted", len(queue))
        return 0
    item = pending[0]
    mp4 = QUEUE_DIR / "reels" / item["file"]
    _ffprobe_sanity(mp4)
    env = _read_env(ENV_FILE)
    base, user_id, key = env["IG_API_BASE"], env["IG_USER_ID"], env["IG_ACCESS_TOKEN"]

    if dry_run:
        log.info("DRY RUN: would post %s with caption:\n%s", mp4.name, item["caption"])
        return 0

    db_path = REPO_ROOT / json.loads(PIPELINE_CONFIG.read_text())["guardian_db_path"]
    asset_path, public_url = _publish_reel_asset(mp4, db_path, mp4.name)
    # copy2 keeps the source's old mtime, and the farm's asset sweep deletes
    # anything older than 48 h by mtime, so without this touch a farm reel
    # running at the same moment could delete our file mid-ingest.
    os.utime(asset_path, None)
    log.info("hosted %s at %s", mp4.name, public_url)
    try:
        return _create_and_publish(item, posted, pending, base, user_id, key, public_url)
    finally:
        # Meta has its own copy once the container finishes (or failed); these
        # files are 25-110 MB, so do not leave them for the farm's 48 h sweep.
        asset_path.unlink(missing_ok=True)


def _create_and_publish(item: dict, posted: list[dict], pending: list[dict],
                        base: str, user_id: str, key: str, public_url: str) -> int:
    container = _graph("POST", f"{base}/{user_id}/media", media_type="REELS", video_url=public_url,
                       caption=item["caption"], share_to_feed="true", access_token=key)["id"]
    deadline = time.time() + CONTAINER_TIMEOUT_S
    status = ""
    while time.time() < deadline:
        status = _graph("GET", f"{base}/{container}", fields="status_code,status", access_token=key).get("status_code", "")
        if status in ("FINISHED", "ERROR", "EXPIRED"):
            break
        time.sleep(CONTAINER_POLL_S)
    if status != "FINISHED":
        log.error("container %s ended %s; not publishing", container, status or "TIMEOUT")
        failed = json.loads(FAILED_FILE.read_text()) if FAILED_FILE.exists() else {}
        failed[item["file"]] = failed.get(item["file"], 0) + 1
        FAILED_FILE.write_text(json.dumps(failed, indent=2))
        if failed[item["file"]] >= MAX_ATTEMPTS:
            _notify_discord(f"🚨 VoynichLabs: Instagram rejected {item['title']} {MAX_ATTEMPTS} times; skipping it")
        return 1

    media_id = _graph("POST", f"{base}/{user_id}/media_publish", creation_id=container, access_token=key)["id"]
    # Record before anything else can fail, so a later error never causes a second post of the same reel.
    posted.append({"file": item["file"], "media_id": media_id,
                   "posted_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    _save_posted(posted)
    permalink = ""
    try:
        permalink = _graph("GET", f"{base}/{media_id}", fields="permalink", access_token=key).get("permalink", "")
    except RuntimeError as exc:
        log.warning("posted, but permalink lookup failed: %s", exc)
    log.info("posted %s -> %s", item["title"], permalink or media_id)
    _notify_discord(f"🎬 VoynichLabs Instagram posted **{item['title']}** {permalink}  "
                    f"({len(pending) - 1} left in the queue)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post the next VoynichLabs Reel to Instagram.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would post; host and post nothing.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S%z")
    try:
        return run(args.dry_run)
    except Exception as exc:  # noqa: BLE001 - one line to the log and Discord beats a silent traceback
        log.error("voynichlabs reel post failed: %s: %s", type(exc).__name__, _scrub(exc))
        _notify_discord(f"🚨 VoynichLabs Instagram post failed: {type(exc).__name__}: {_scrub(exc)[:200]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
