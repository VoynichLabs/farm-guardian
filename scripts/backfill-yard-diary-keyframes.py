#!/usr/bin/env python3
# Author: Claude Sonnet 5 Extra
# Date: 03-Aug-2026
# PURPOSE: One-time backfill — register the existing data/yard-diary/*.jpg
#          files (house-yard, 3x/day since 17-Apr-2026, written by
#          scripts/yard-diary-capture.py and never previously tracked in
#          image_archive) as image_tier='keyframe' rows, so the new
#          house-yard weekly/monthly time-lapse Reels have a historical
#          seed immediately instead of waiting weeks/a month for fresh
#          keyframe capture to accrue. See
#          docs/03-Aug-2026-multi-day-timelapse-reels-plan.md.
#
#          Does NOT copy files: image_path is set to the existing
#          yard-diary path relative to db_path.parent (data/yard-diary/…),
#          which store.resolve_gem_image_path already resolves correctly
#          since that directory sits under data/ alongside guardian.db.
#          yard-diary-capture.py itself is untouched — this script only
#          reads its output and reads/writes image_archive.
#
#          Idempotent: a file already referenced by an image_archive row
#          (by image_path) is skipped, so re-running after new yard-diary
#          captures land only inserts the new ones.
#
#          ts is taken from each file's mtime (not a fixed 07:00/12:00/
#          16:00 assumed from the slot name) — actual capture times drift
#          by tens of minutes (e.g. an observed noon capture at 12:34,
#          another at 14:04), and the daylight filter in
#          ig_selection.select_multiday_timelapse_gems is minute-granular,
#          so the real capture time is worth keeping.
# SRP/DRY check: Pass — single responsibility is this one-time backfill.
#                Reuses store.ensure_schema and store._insert_bypass_row
#                rather than restating image_archive's INSERT a third
#                time (see store.py's 03-Aug-2026 refactor).

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.store import _insert_bypass_row, ensure_schema  # noqa: E402

YARD_DIARY_DIR = REPO_ROOT / "data" / "yard-diary"
CAMERA_ID = "house-yard"

_FNAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-(morning|noon|evening)\.jpg$")


def _laplacian_var(jpeg_bytes: bytes) -> float | None:
    """Sharpness proxy, same metric run_raw_cycle stores for every other
    camera — lets a future selector rank house-yard keyframes by
    sharpness if that's ever needed, though select_multiday_timelapse_gems
    doesn't score today (see its docstring)."""
    img = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def backfill(db_path: Path, dry_run: bool = False) -> dict:
    if not YARD_DIARY_DIR.is_dir():
        raise SystemExit(f"{YARD_DIARY_DIR} does not exist — nothing to backfill")

    ensure_schema(db_path)

    inserted = 0
    skipped_existing = 0
    skipped_unparsed = 0

    with sqlite3.connect(str(db_path), timeout=30) as c:
        c.row_factory = sqlite3.Row
        for jpg_path in sorted(YARD_DIARY_DIR.glob("*.jpg")):
            if not _FNAME_RE.match(jpg_path.name):
                print(f"backfill: unparsed filename, skipping: {jpg_path.name}")
                skipped_unparsed += 1
                continue

            image_path_rel = str(jpg_path.relative_to(db_path.parent))
            existing = c.execute(
                "SELECT 1 FROM image_archive WHERE image_path = ? LIMIT 1",
                (image_path_rel,),
            ).fetchone()
            if existing:
                skipped_existing += 1
                continue

            jpeg_bytes = jpg_path.read_bytes()
            ts_iso = datetime.fromtimestamp(
                jpg_path.stat().st_mtime, tz=timezone.utc
            ).isoformat(timespec="seconds")
            sha = hashlib.sha256(jpeg_bytes).hexdigest()
            img = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
            height, width = (img.shape[0], img.shape[1]) if img is not None else (None, None)
            lap = _laplacian_var(jpeg_bytes)

            if dry_run:
                print(
                    f"[dry-run] would insert {jpg_path.name} ts={ts_iso} "
                    f"{width}x{height} laplacian_var={lap}"
                )
                inserted += 1
                continue

            _insert_bypass_row(
                c, CAMERA_ID, ts_iso, image_path_rel, "keyframe", sha,
                width, height, len(jpeg_bytes),
                gate_metrics={"laplacian_var": lap},
            )
            inserted += 1

        if not dry_run:
            c.commit()

    return {
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "skipped_unparsed": skipped_unparsed,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=(
            "One-time backfill of data/yard-diary/*.jpg into image_archive "
            "as image_tier='keyframe' rows (house-yard weekly/monthly "
            "time-lapse Reel seed). Safe to re-run — idempotent on "
            "image_path."
        )
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be inserted without writing to the DB.",
    )
    args = ap.parse_args()

    cfg = json.loads((REPO_ROOT / "tools" / "pipeline" / "config.json").read_text())
    db_path = REPO_ROOT / cfg["guardian_db_path"]

    result = backfill(db_path, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
