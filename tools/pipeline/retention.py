# Author: Claude Opus 4.6 (1M context); Claude Sonnet 4.6 (edits 27-April-2026 — sweep_raw() for vlm_bypass cameras, v2.37.13)
# Date: 13-April-2026
# PURPOSE: Daily retention sweep for the image archive. Deletes JPEGs whose
#          retained_until has passed, sets image_path to NULL on those rows,
#          and leaves metadata rows intact forever. Never touches rows with
#          has_concerns=1 or retained_until IS NULL.
# SRP/DRY check: Pass — single responsibility is pruning expired JPEGs.

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("pipeline.retention")


def sweep(db_path: Path, archive_root: Path, dry_run: bool = False) -> dict:
    today_iso = date.today().isoformat()
    deleted = 0
    freed_bytes = 0
    errors: list[str] = []
    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("""
            SELECT id, image_path, bytes FROM image_archive
            WHERE image_path IS NOT NULL
              AND retained_until IS NOT NULL
              AND retained_until <= ?
              AND has_concerns = 0
        """, (today_iso,)).fetchall()
        for row in rows:
            rel = row["image_path"]
            candidate = archive_root.parent / rel if not Path(rel).is_absolute() else Path(rel)
            sidecar = candidate.with_suffix(".json")
            try:
                if candidate.exists():
                    freed_bytes += candidate.stat().st_size
                    if not dry_run:
                        candidate.unlink()
                if sidecar.exists() and not dry_run:
                    sidecar.unlink()
                if not dry_run:
                    c.execute("UPDATE image_archive SET image_path = NULL WHERE id = ?", (row["id"],))
                deleted += 1
            except Exception as e:
                errors.append(f"id={row['id']} path={rel}: {e}")
        if not dry_run:
            c.commit()
    return {"deleted": deleted, "freed_bytes": freed_bytes, "errors": errors, "dry_run": dry_run}


def sweep_raw(db_path: Path, archive_root: Path, camera_id: str,
              retention_hours: int = 24, dry_run: bool = False) -> dict:
    """Rolling hour-granular pruner for vlm_bypass cameras (tier='raw').

    Deletes both the JPEG on disk and the image_archive row for rows where:
      - camera_id matches
      - image_tier = 'raw'
      - ts < now - retention_hours

    Unlike the daily sweep, raw rows are DROPPED from the DB entirely (not
    kept as metadata-only) — the raw path exists for transient on-disk
    storage, and an orphaned row with image_path=NULL serves no purpose.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
    cutoff_iso = cutoff.isoformat(timespec="seconds")
    deleted = 0
    freed_bytes = 0
    errors: list[str] = []
    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("""
            SELECT id, image_path FROM image_archive
            WHERE camera_id = ?
              AND image_tier = 'raw'
              AND ts < ?
        """, (camera_id, cutoff_iso)).fetchall()
        for row in rows:
            rel = row["image_path"]
            try:
                if rel:
                    candidate = archive_root.parent / rel if not Path(rel).is_absolute() else Path(rel)
                    if candidate.exists():
                        freed_bytes += candidate.stat().st_size
                        if not dry_run:
                            candidate.unlink()
                if not dry_run:
                    c.execute("DELETE FROM image_archive WHERE id = ?", (row["id"],))
                deleted += 1
            except Exception as e:
                errors.append(f"id={row['id']} path={rel}: {e}")
        if not dry_run:
            c.commit()
    return {"camera": camera_id, "deleted": deleted, "freed_bytes": freed_bytes,
            "errors": errors, "dry_run": dry_run, "cutoff": cutoff_iso}


if __name__ == "__main__":
    import json, sys
    logging.basicConfig(level=logging.INFO)
    from pathlib import Path as P
    cfg = json.loads((P(__file__).parent / "config.json").read_text())
    repo = P(__file__).resolve().parents[2]
    db = repo / cfg["guardian_db_path"]
    archive = repo / cfg["archive_root"]
    dry = "--dry-run" in sys.argv
    print(json.dumps(sweep(db, archive, dry_run=dry), indent=2))
