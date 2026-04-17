#!/opt/homebrew/bin/python3
"""
Author: Claude Opus 4.7 (1M context)
Date: 17-Apr-2026
PURPOSE: Daily yard-diary capture. Pulls a 4K snapshot from Guardian's
         local Reolink endpoint three times a day (morning/noon/evening),
         stores the 4K master under farm-guardian/data/yard-diary/,
         publishes a 1920px-long-edge JPEG with a burned-in date label
         (DD-Mon-YYYY, Boss's standard format) into
         farm-2026/public/photos/yard-diary/, then commits + pushes so
         Railway redeploys.

         Slot is derived from the current hour so one script handles
         all three firings:
           - hour <  10 → morning
           - hour 10–13 → noon
           - hour >= 14 → evening

         Installed at ~/bin/ (NOT in ~/Documents) so launchd can execute
         it without tripping TCC's Documents-folder protection. File I/O
         into ~/Documents is fine because this launches under the
         com.farmguardian.* label family which already has the required
         grants (see farm-guardian CLAUDE.md on the label-rename TCC fix).

SRP/DRY check: Pass — single responsibility: one capture cycle end-to-end.
               No farm-guardian module reuse because the whole job is 60
               lines of curl + Pillow + git subprocess.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

GUARDIAN_URL = "http://localhost:6530/api/v1/cameras/house-yard/snapshot"
HOME = Path.home()
MASTERS_DIR = HOME / "Documents/GitHub/farm-guardian/data/yard-diary"
SITE_REPO = HOME / "Documents/GitHub/farm-2026"
PUBLISHED_DIR = SITE_REPO / "public/photos/yard-diary"
LOG_FILE = HOME / "Documents/GitHub/farm-guardian/data/pipeline-logs/yard-diary.log"
FONT_PATH = "/System/Library/Fonts/HelveticaNeue.ttc"

MIN_BYTES = 50_000
PUBLISHED_LONG_EDGE = 1920
JPEG_QUALITY = 88


def log(msg: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as f:
        f.write(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {msg}\n")


def slot_for_hour(h: int) -> str:
    if h < 10:
        return "morning"
    if h < 14:
        return "noon"
    return "evening"


def format_boss_date(d: datetime) -> str:
    # Boss's standard: DD-Mon-YYYY, e.g. "17-Apr-2026"
    return d.strftime("%d-%b-%Y")


def fetch_snapshot(dest: Path) -> int:
    req = urllib.request.Request(GUARDIAN_URL, headers={"accept": "image/jpeg"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    dest.write_bytes(data)
    return len(data)


def publish_with_date(master: Path, published: Path, date_text: str) -> int:
    with Image.open(master) as img:
        img = img.convert("RGB")
        # Resize to PUBLISHED_LONG_EDGE on the long edge, preserve aspect.
        w, h = img.size
        scale = PUBLISHED_LONG_EDGE / max(w, h)
        if scale < 1.0:
            img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
        draw = ImageDraw.Draw(img, "RGBA")

        # Font size ~= 2.2% of long edge; readable but not shouting.
        font_size = max(20, round(img.size[0] * 0.022))
        try:
            font = ImageFont.truetype(FONT_PATH, font_size)
        except OSError:
            font = ImageFont.load_default()

        # Position: bottom-right with a margin.
        margin = round(img.size[0] * 0.018)
        bbox = draw.textbbox((0, 0), date_text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = img.size[0] - text_w - margin
        y = img.size[1] - text_h - margin - bbox[1]

        # Semi-transparent dark pill behind the text so it stays legible
        # against blown-out sky, snow, or dark tree line alike.
        pad_x, pad_y = round(font_size * 0.55), round(font_size * 0.35)
        pill = (
            x - pad_x,
            y + bbox[1] - pad_y,
            x + text_w + pad_x,
            y + bbox[1] + text_h + pad_y,
        )
        draw.rounded_rectangle(pill, radius=font_size // 3, fill=(0, 0, 0, 140))
        draw.text((x, y), date_text, font=font, fill=(255, 255, 255, 240))

        img.save(published, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return published.stat().st_size


def git_publish(published: Path, date_iso: str, slot: str) -> None:
    rel = published.relative_to(SITE_REPO)
    result = subprocess.run(
        ["git", "-C", str(SITE_REPO), "status", "--porcelain", str(rel)],
        capture_output=True, text=True, check=True,
    )
    if not result.stdout.strip():
        log(f"no change to publish for {date_iso}-{slot}")
        return

    subprocess.run(["git", "-C", str(SITE_REPO), "add", str(rel)], check=True)
    subprocess.run(
        ["git", "-C", str(SITE_REPO), "commit", "-m", f"yard-diary: {date_iso} {slot}"],
        check=True, stdout=subprocess.DEVNULL,
    )
    push = subprocess.run(
        ["git", "-C", str(SITE_REPO), "push"],
        capture_output=True, text=True,
    )
    if push.returncode != 0:
        log(f"WARN: git push failed ({push.stderr.strip().splitlines()[-1] if push.stderr else 'unknown'})")
    else:
        log(f"pushed {date_iso}-{slot} to origin")


def main() -> int:
    now = datetime.now().astimezone()
    slot = slot_for_hour(now.hour)
    date_iso = now.strftime("%Y-%m-%d")
    date_label = format_boss_date(now)
    stem = f"{date_iso}-{slot}"

    MASTERS_DIR.mkdir(parents=True, exist_ok=True)
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)

    master = MASTERS_DIR / f"{stem}.jpg"
    published = PUBLISHED_DIR / f"{stem}.jpg"

    log(f"capture start ({stem})")
    try:
        bytes_in = fetch_snapshot(master)
    except Exception as e:
        log(f"ERROR: snapshot fetch failed: {e}")
        return 1

    if bytes_in < MIN_BYTES:
        log(f"ERROR: snapshot suspiciously small ({bytes_in} bytes) — aborting")
        master.unlink(missing_ok=True)
        return 1

    try:
        bytes_out = publish_with_date(master, published, date_label)
    except Exception as e:
        log(f"ERROR: publish/overlay failed: {e}")
        return 1

    log(f"captured {stem}: master={bytes_in}B published={bytes_out}B label={date_label!r}")

    try:
        git_publish(published, date_iso, slot)
    except subprocess.CalledProcessError as e:
        log(f"ERROR: git publish failed: {e}")
        return 1

    log(f"capture done ({stem})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
