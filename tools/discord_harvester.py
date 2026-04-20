#!/usr/bin/env python3
"""
Author: Claude Opus 4.6 (Bubba sub-agent)
Date: 17-April-2026
PURPOSE: Harvest human-reacted images from Discord #farm-2026 channel, download them
         to the farm-2026 website repo, update gallery.json, and git commit+push so
         Railway auto-deploys. Tracks state to avoid re-downloading on subsequent runs.
SRP/DRY check: Pass — single responsibility (harvest Discord images), no duplication
               with existing farm-guardian tools.
"""

import argparse
import json
import os
import sys
import subprocess
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests library not found. Run with farm-guardian venv:")
    print("  ~/Documents/GitHub/farm-guardian/venv/bin/python3 discord_harvester.py")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHANNEL_ID = "1482466978806497522"
DISCORD_API = "https://discord.com/api/v10"

# Bot user IDs to exclude from reaction checks
BOT_USER_IDS = {
    "1468063932248883231",  # Larry
    "1474802169415733358",  # Bubba
    "1467951240121028862",  # Egon
}

# Paths
OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
FARM_2026_REPO = Path.home() / "Documents" / "GitHub" / "farm-2026"
PHOTOS_DIR = FARM_2026_REPO / "public" / "photos"
GALLERY_JSON = FARM_2026_REPO / "content" / "gallery.json"
STATE_FILE = Path.home() / "Documents" / "GitHub" / "farm-guardian" / "data" / "harvester-state.json"

# Image extensions we accept
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}

# Month name mapping
MONTH_NAMES = {
    1: "january", 2: "february", 3: "march", 4: "april",
    5: "may", 6: "june", 7: "july", 8: "august",
    9: "september", 10: "october", 11: "november", 12: "december",
}

MONTH_TITLE_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_bot_token() -> str:
    """Read Discord bot token from OpenClaw config."""
    if not OPENCLAW_CONFIG.exists():
        print(f"ERROR: OpenClaw config not found at {OPENCLAW_CONFIG}")
        sys.exit(1)
    config = json.loads(OPENCLAW_CONFIG.read_text())
    token = config.get("channels", {}).get("discord", {}).get("token")
    if not token:
        print("ERROR: Discord token not found at channels.discord.token in openclaw.json")
        sys.exit(1)
    return token


def discord_headers(token: str) -> dict:
    """Build auth headers for Discord Bot API."""
    return {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }


def load_state() -> dict:
    """Load harvester state (set of already-processed message IDs)."""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"harvested_message_ids": []}


def save_state(state: dict):
    """Persist harvester state to disk."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def load_gallery() -> dict:
    """Load gallery.json."""
    if GALLERY_JSON.exists():
        return json.loads(GALLERY_JSON.read_text())
    return {"sections": []}


def save_gallery(gallery: dict):
    """Write gallery.json."""
    GALLERY_JSON.write_text(json.dumps(gallery, indent=2) + "\n")


def get_month_folder(timestamp: str) -> str:
    """Convert Discord ISO timestamp to month-folder like 'april-2026'."""
    dt = datetime.fromisoformat(timestamp.replace("+00:00", "+00:00"))
    month_name = MONTH_NAMES[dt.month]
    return f"{month_name}-{dt.year}"


def get_month_title(timestamp: str) -> str:
    """Convert Discord ISO timestamp to section title like 'April 2026'."""
    dt = datetime.fromisoformat(timestamp.replace("+00:00", "+00:00"))
    month_name = MONTH_TITLE_NAMES[dt.month]
    return f"{month_name} {dt.year}"


def fetch_all_messages(token: str) -> list:
    """Fetch all messages from the channel using pagination."""
    headers = discord_headers(token)
    all_messages = []
    before = None

    while True:
        url = f"{DISCORD_API}/channels/{CHANNEL_ID}/messages?limit=100"
        if before:
            url += f"&before={before}"

        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            print(f"ERROR: Discord API returned {resp.status_code}: {resp.text[:200]}")
            break

        messages = resp.json()
        if not messages:
            break

        all_messages.extend(messages)
        before = messages[-1]["id"]
        print(f"  Fetched {len(all_messages)} messages so far...")

        # Small delay between pages to respect rate limits
        time.sleep(0.5)

        # Discord returns fewer than 100 = we've hit the end
        if len(messages) < 100:
            break

    return all_messages


def has_human_reaction(message: dict, token: str) -> bool:
    """
    Check if a message has at least one reaction from a real human
    (not a bot, not in BOT_USER_IDS).
    """
    reactions = message.get("reactions", [])
    if not reactions:
        return False

    headers = discord_headers(token)

    for i_react, reaction in enumerate(reactions):
        if i_react > 0:
            time.sleep(0.35)  # Respect rate limits between reaction fetches
        emoji = reaction.get("emoji", {})
        # Build emoji string for the API
        emoji_name = emoji.get("name", "")
        emoji_id = emoji.get("id")

        if emoji_id:
            # Custom emoji: name:id
            emoji_param = f"{emoji_name}:{emoji_id}"
        else:
            # Unicode emoji: URL-encode it
            emoji_param = emoji_name

        # Fetch users who reacted with this emoji (with retry on rate limit)
        encoded = urllib.parse.quote(emoji_param)
        url = f"{DISCORD_API}/channels/{CHANNEL_ID}/messages/{message['id']}/reactions/{encoded}?limit=100"

        resp = None
        for attempt in range(4):
            resp = requests.get(url, headers=headers)
            if resp.status_code == 429:
                retry_after = resp.json().get("retry_after", 2.0)
                print(f"  Rate limited — waiting {retry_after:.1f}s...")
                time.sleep(retry_after + 0.25)
                continue
            break

        if resp is None or resp.status_code != 200:
            status = resp.status_code if resp else "no response"
            print(f"  WARN: Could not fetch reactions for message {message['id']}, "
                  f"emoji {emoji_name}: {status}")
            continue

        users = resp.json()
        for user in users:
            user_id = user.get("id", "")
            is_bot = user.get("bot", False)

            if not is_bot and user_id not in BOT_USER_IDS:
                return True

    return False


def get_image_attachments(message: dict) -> list:
    """Return list of image attachments from a message."""
    attachments = message.get("attachments", [])
    images = []
    for att in attachments:
        filename = att.get("filename", "")
        ext = os.path.splitext(filename)[1].lower()
        if ext in IMAGE_EXTENSIONS:
            images.append(att)
    return images


def download_image(url: str, dest: Path) -> bool:
    """Download an image file from Discord CDN."""
    try:
        resp = requests.get(url, stream=True, timeout=30)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"  ERROR downloading {url}: {e}")
        return False


def add_to_gallery(gallery: dict, photo_entry: dict, month_title: str):
    """Add a photo entry to the correct section in gallery.json."""
    # Find existing section
    for section in gallery["sections"]:
        if section["title"] == month_title:
            # Check for duplicate
            for existing in section["photos"]:
                if existing["id"] == photo_entry["id"]:
                    return  # Already exists
            section["photos"].append(photo_entry)
            return

    # Create new section
    gallery["sections"].insert(0, {
        "title": month_title,
        "description": "",
        "photos": [photo_entry],
    })


def git_commit_push(repo_path: Path, message: str) -> bool:
    """Git add, commit, and push in the given repo."""
    try:
        subprocess.run(["git", "add", "-A"], cwd=repo_path, check=True, capture_output=True)

        # Check if there's anything to commit
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_path, capture_output=True,
        )
        if result.returncode == 0:
            print("  No changes to commit.")
            return False

        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=repo_path, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=repo_path, check=True, capture_output=True,
        )
        print(f"  Committed and pushed: {message}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ERROR in git operation: {e}")
        if e.stderr:
            print(f"  stderr: {e.stderr.decode()[:200]}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Harvest human-reacted images from Discord #farm-2026"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be harvested without downloading or modifying anything"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Discord Photo Harvester — farm-2026")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print("=" * 60)

    # Load token
    token = load_bot_token()
    print("✓ Discord bot token loaded")

    # Load state
    state = load_state()
    harvested_ids = set(state.get("harvested_message_ids", []))
    print(f"✓ State loaded — {len(harvested_ids)} previously harvested messages")

    # Load gallery
    gallery = load_gallery()
    print(f"✓ Gallery loaded — {len(gallery['sections'])} sections")

    # Fetch messages
    print("\nFetching messages from #farm-2026...")
    messages = fetch_all_messages(token)
    print(f"✓ Total messages fetched: {len(messages)}")

    # Filter: has image attachments + not already harvested
    candidates = []
    for msg in messages:
        msg_id = msg["id"]
        if msg_id in harvested_ids:
            continue
        images = get_image_attachments(msg)
        if images:
            candidates.append(msg)

    print(f"\n→ {len(candidates)} messages with new image attachments")

    if not candidates:
        print("\nNothing to harvest. Done.")
        return

    # Check reactions on candidates
    print("\nChecking reactions for human approval...")
    to_harvest = []
    for i_cand, msg in enumerate(candidates):
        if i_cand > 0:
            time.sleep(0.5)  # Pace reaction checks
        if has_human_reaction(msg, token):
            to_harvest.append(msg)
            images = get_image_attachments(msg)
            caption = msg.get("content", "")[:100]
            print(f"  ✓ Message {msg['id']} — {len(images)} image(s) — "
                  f"caption: \"{caption[:50]}{'...' if len(caption) > 50 else ''}\"")
        else:
            pass  # No human reaction, skip silently

    print(f"\n→ {len(to_harvest)} messages approved by humans")

    if not to_harvest:
        print("\nNo human-approved images found. Done.")
        return

    if args.dry_run:
        print("\n--- DRY RUN — would harvest these: ---")
        for msg in to_harvest:
            images = get_image_attachments(msg)
            month_folder = get_month_folder(msg["timestamp"])
            caption = msg.get("content", "")
            print(f"\n  Message {msg['id']} ({msg['timestamp'][:10]})")
            print(f"    Month folder: {month_folder}")
            print(f"    Caption: \"{caption[:80]}\"")
            for i, att in enumerate(images):
                ext = os.path.splitext(att["filename"])[1].lower()
                out_name = f"discord-{msg['id']}-{i}{ext}"
                print(f"    Image {i}: {att['filename']} → {out_name}")
        print("\n--- End dry run ---")
        return

    # Harvest!
    print("\nDownloading images...")
    downloaded_count = 0
    new_harvested_ids = []

    for msg in to_harvest:
        msg_id = msg["id"]
        images = get_image_attachments(msg)
        month_folder = get_month_folder(msg["timestamp"])
        month_title = get_month_title(msg["timestamp"])
        caption = msg.get("content", "")

        for i, att in enumerate(images):
            ext = os.path.splitext(att["filename"])[1].lower()
            out_name = f"discord-{msg_id}-{i}{ext}"
            dest = PHOTOS_DIR / month_folder / out_name

            if dest.exists():
                print(f"  SKIP (exists): {out_name}")
                continue

            print(f"  Downloading: {out_name} → {month_folder}/")
            if download_image(att["url"], dest):
                downloaded_count += 1

                # Add to gallery
                photo_entry = {
                    "id": f"discord-{msg_id}-{i}",
                    "filename": out_name,
                    "folder": month_folder,
                    "caption": caption,
                    "year": month_title,
                }
                add_to_gallery(gallery, photo_entry, month_title)

        new_harvested_ids.append(msg_id)

    # Save gallery
    if downloaded_count > 0:
        save_gallery(gallery)
        print(f"\n✓ Gallery updated with {downloaded_count} new photo(s)")

    # Update state
    harvested_ids.update(new_harvested_ids)
    state["harvested_message_ids"] = sorted(list(harvested_ids))
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    print(f"✓ State saved — {len(harvested_ids)} total harvested messages")

    # Git commit + push
    if downloaded_count > 0:
        print("\nCommitting to farm-2026 repo...")
        commit_msg = f"harvester: add {downloaded_count} photo(s) from Discord #farm-2026"
        git_commit_push(FARM_2026_REPO, commit_msg)

    print(f"\n{'=' * 60}")
    print(f"Done. Downloaded {downloaded_count} image(s).")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
