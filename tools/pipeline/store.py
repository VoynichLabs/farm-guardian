# Author: Claude Opus 4.6 (1M context), Claude Opus 4.7 (1M context) — IG columns 20-Apr-2026
# Date: 13-April-2026 (last touched 20-April-2026)
# PURPOSE: Persist a captured + enriched image. Writes JPEG to disk per tier
#          (full-res for share_worth=strong, downscaled for decent, discard
#          for skip), writes a sidecar .json next to the JPEG, and inserts a
#          row into Guardian's SQLite image_archive table. Runs the table
#          migration idempotently on first use so this tool doesn't require
#          changes to database.py — the pipeline is strictly additive.
#
#          As of 20-Apr-2026 also provides the ig_permalink / ig_posted_at /
#          ig_skip_reason columns on image_archive (added via ALTER-IF-MISSING
#          for existing DBs). tools/pipeline/ig_poster.py writes these via
#          UPDATE after a successful Instagram post; the INSERT path here
#          leaves them NULL — Instagram metadata is post-hoc, not part of
#          the capture cycle.
# SRP/DRY check: Pass — single responsibility is durable storage of one
#                enriched image. No capture, no VLM, no scheduling, no
#                Instagram network I/O (that lives in ig_poster.py). The
#                IG columns are schema only; the writes happen elsewhere.

from __future__ import annotations

import hashlib
import io
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger("pipeline.store")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS image_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    image_path TEXT,
    image_tier TEXT NOT NULL,
    sha256 TEXT,
    width INT, height INT, bytes INT,
    std_dev REAL, laplacian_var REAL, exposure_p50 REAL,
    vlm_model TEXT, vlm_inference_ms INT, vlm_prompt_hash TEXT,
    vlm_json TEXT NOT NULL,
    scene TEXT, bird_count INT, activity TEXT, lighting TEXT,
    composition TEXT, image_quality TEXT, share_worth TEXT,
    any_special_chick INT, apparent_age_days INT, has_concerns INT,
    individuals_visible_csv TEXT,
    retained_until TEXT,
    ig_permalink TEXT,
    ig_posted_at TEXT,
    ig_skip_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_archive_camera_ts ON image_archive(camera_id, ts);
CREATE INDEX IF NOT EXISTS idx_archive_share    ON image_archive(share_worth, image_quality);
CREATE INDEX IF NOT EXISTS idx_archive_concerns ON image_archive(has_concerns);
CREATE INDEX IF NOT EXISTS idx_archive_retain   ON image_archive(retained_until);
"""

# Indexes that depend on late columns must run AFTER the ALTER TABLE step,
# otherwise executescript fails on pre-existing DBs where the column hasn't
# been added yet. Kept separate from _SCHEMA_SQL for that reason.
_LATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_archive_ig_posted ON image_archive(ig_posted_at);
"""

# Columns added post-initial-schema. Kept as a list so ensure_schema() can
# idempotently add them to DBs whose image_archive table pre-dates them.
# Pair each entry with the SQLite ALTER TABLE clause to apply if missing.
_LATE_COLUMNS = [
    # (column_name, full_column_def_for_ALTER)
    ("ig_permalink",    "ig_permalink TEXT"),
    ("ig_posted_at",    "ig_posted_at TEXT"),
    ("ig_skip_reason",  "ig_skip_reason TEXT"),
]

_DB_LOCK = threading.Lock()  # WAL mode still wants serialized writes from this process


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column_name: str, column_def: str) -> None:
    """SQLite lacks ALTER TABLE ... ADD COLUMN IF NOT EXISTS. Emulate it
    by reading PRAGMA table_info and only running the ALTER when needed.
    No-op on any error (fresh DBs where the column is already in the
    CREATE TABLE will have it; nothing to add). Idempotent."""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column_name in cols:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")


def ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as c:
        # Step 1: base schema. CREATE TABLE IF NOT EXISTS is idempotent; on a
        # pre-existing DB the table already exists and this is a no-op. Note
        # the CREATE TABLE definition includes the late columns — fresh DBs
        # get them from day one.
        c.executescript(_SCHEMA_SQL)
        # Step 2: catch up pre-existing DBs that were created before the late
        # columns joined _SCHEMA_SQL. On a fresh DB these are already present
        # and the helper is a no-op.
        for col_name, col_def in _LATE_COLUMNS:
            _add_column_if_missing(c, "image_archive", col_name, col_def)
        # Step 3: indexes that reference late columns must run AFTER the ALTER.
        c.executescript(_LATE_INDEX_SQL)


def _downscale_jpeg(jpeg_bytes: bytes, long_edge_px: int, quality: int) -> bytes:
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("downscale: source is not a decodable JPEG")
    h, w = img.shape[:2]
    if max(h, w) <= long_edge_px:
        return jpeg_bytes  # already small enough
    if w >= h:
        new_w = long_edge_px
        new_h = int(h * long_edge_px / w)
    else:
        new_h = long_edge_px
        new_w = int(w * long_edge_px / h)
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("downscale: re-encode failed")
    return bytes(buf)


def _image_dims(jpeg_bytes: bytes) -> tuple[int, int]:
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return 0, 0
    return img.shape[1], img.shape[0]


def store(
    db_path: Path,
    archive_root: Path,
    camera_id: str,
    jpeg_bytes: bytes,
    gate_metrics: dict,
    vlm_result: dict,
    vlm_model: str,
    retention_days_strong: int = 90,
    retention_days_decent: int = 90,
    retention_days_concerns: Optional[int] = None,
    downscale_decent_long_edge_px: int = 1920,
    downscale_decent_jpeg_quality: int = 85,
) -> dict:
    """Write JPEG per tier, insert DB row, return a summary dict.
    - share_worth='skip'   → no JPEG written; only metadata row.
    - share_worth='decent' → downscaled JPEG, 90d retention.
    - share_worth='strong' → full-res JPEG, 90d retention (then manual review).
    - concerns non-empty   → retained_until = NULL regardless of tier.
    """
    md = vlm_result["metadata"]
    tier = md["share_worth"]
    has_concerns = 1 if md["concerns"] else 0

    # Compute retained_until
    now = datetime.now(timezone.utc)
    ts_iso = now.isoformat(timespec="seconds")
    if has_concerns and retention_days_concerns is None:
        retained_until = None  # keep forever, flagged row
    elif tier == "strong":
        retained_until = (now + timedelta(days=retention_days_strong)).date().isoformat()
    elif tier == "decent":
        retained_until = (now + timedelta(days=retention_days_decent)).date().isoformat()
    else:
        retained_until = None  # skip rows have no JPEG to expire

    # Image bytes to persist (skip rows store nothing)
    stored_bytes: Optional[bytes] = None
    image_path_rel: Optional[str] = None

    if tier == "strong":
        stored_bytes = jpeg_bytes
    elif tier == "decent":
        stored_bytes = _downscale_jpeg(jpeg_bytes, downscale_decent_long_edge_px, downscale_decent_jpeg_quality)

    # Dimensions come from the bytes we actually store if we store any; else
    # from the source (for skip rows, still useful metadata).
    dims_source = stored_bytes if stored_bytes is not None else jpeg_bytes
    width, height = _image_dims(dims_source)

    sha = hashlib.sha256(stored_bytes or jpeg_bytes).hexdigest()

    if stored_bytes is not None:
        ym = now.strftime("%Y-%m")
        ts_compact = now.strftime("%Y-%m-%dT%H-%M-%S")
        sub = archive_root / ym / camera_id
        sub.mkdir(parents=True, exist_ok=True)
        fname = f"{ts_compact}-{tier}.jpg"
        jpath = sub / fname
        spath = sub / fname.replace(".jpg", ".json")
        jpath.write_bytes(stored_bytes)
        sidecar = {
            "camera_id": camera_id,
            "ts": ts_iso,
            "tier": tier,
            "vlm_model": vlm_model,
            "vlm_inference_ms": vlm_result["inference_ms"],
            "vlm_prompt_hash": vlm_result["prompt_hash"],
            "gate_metrics": gate_metrics,
            "metadata": md,
        }
        spath.write_text(json.dumps(sidecar, indent=2))
        image_path_rel = str(jpath.relative_to(archive_root.parent)) if archive_root.parent in jpath.parents or archive_root.parent == jpath.parent.parent else str(jpath)

        # Curation views: hardlink strong-tier into gems/ and concerns into private/.
        # Hardlinks share the inode with the archive copy — zero extra disk,
        # and they survive the 90-day archive retention sweep because unlinking
        # the archive entry doesn't delete the inode while another link exists.
        # That's the intended behavior: gems are kept indefinitely; archive
        # rotates. Private (concerns) rows are additionally exempt in retention.py.
        gems_root = archive_root.parent / "gems"
        private_root = archive_root.parent / "private"
        try:
            if tier == "strong":
                gsub = gems_root / ym / camera_id
                gsub.mkdir(parents=True, exist_ok=True)
                gpath = gsub / fname
                if not gpath.exists():
                    try:
                        gpath.hardlink_to(jpath)
                    except OSError:
                        # cross-device or filesystem disallows hardlinks → fall back to copy
                        gpath.write_bytes(stored_bytes)
                (gsub / fname.replace(".jpg", ".json")).write_text(spath.read_text())
            if has_concerns:
                psub = private_root / ym / camera_id
                psub.mkdir(parents=True, exist_ok=True)
                ppath = psub / fname
                if not ppath.exists():
                    try:
                        ppath.hardlink_to(jpath)
                    except OSError:
                        ppath.write_bytes(stored_bytes)
                (psub / fname.replace(".jpg", ".json")).write_text(spath.read_text())
        except Exception as e:
            log.warning("curation link failed for %s (archive row still written): %s", fname, e)

    # Insert DB row
    with _DB_LOCK, sqlite3.connect(str(db_path)) as c:
        c.execute("""
            INSERT INTO image_archive (
                camera_id, ts, image_path, image_tier, sha256,
                width, height, bytes,
                std_dev, laplacian_var, exposure_p50,
                vlm_model, vlm_inference_ms, vlm_prompt_hash, vlm_json,
                scene, bird_count, activity, lighting, composition,
                image_quality, share_worth, any_special_chick, apparent_age_days,
                has_concerns, individuals_visible_csv, retained_until
            ) VALUES (?, ?, ?, ?, ?,
                      ?, ?, ?,
                      ?, ?, ?,
                      ?, ?, ?, ?,
                      ?, ?, ?, ?, ?,
                      ?, ?, ?, ?,
                      ?, ?, ?)
        """, (
            camera_id, ts_iso, image_path_rel, tier, sha,
            width, height, len(stored_bytes) if stored_bytes else None,
            gate_metrics.get("std_dev"), gate_metrics.get("laplacian_var"), gate_metrics.get("exposure_p50"),
            vlm_model, vlm_result["inference_ms"], vlm_result["prompt_hash"], json.dumps(md),
            md["scene"], md["bird_count"], md["activity"], md["lighting"], md["composition"],
            md["image_quality"], md["share_worth"], int(md["any_special_chick"]), md["apparent_age_days"],
            has_concerns, ",".join(md["individuals_visible"]), retained_until,
        ))
        c.commit()

    return {
        "tier": tier,
        "image_path": image_path_rel,
        "retained_until": retained_until,
        "has_concerns": bool(has_concerns),
        "width": width, "height": height,
        "stored_bytes": len(stored_bytes) if stored_bytes else 0,
    }
