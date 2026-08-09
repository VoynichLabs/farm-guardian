# Author: Claude Opus 5 (09-Aug-2026 — sweep_raw generalised with an image_tier param so the keyframe tier reuses it, v2.69.0); Claude Opus 4.6 (1M context); Claude Sonnet 4.6 (edits 27-April-2026 — sweep_raw() for vlm_bypass cameras, v2.37.13; 04-May-2026 — sqlite timeout=30 to fix DB lock errors, v2.40.2); Claude Sonnet 5 Extra (edits 03-Aug-2026 — comment-only: documented that image_tier='keyframe' rows are immune to both sweeps by construction, v2.60.0)
# Date: 13-April-2026 (last touched 03-Aug-2026)
# PURPOSE: Daily retention sweep for the image archive. Deletes JPEGs whose
#          retained_until has passed, sets image_path to NULL on those rows,
#          and leaves metadata rows intact forever. Never touches rows with
#          has_concerns=1 or retained_until IS NULL.
#
#          ⚠️ SUPERSEDED 09-Aug-2026 (v2.69.0) — the note below is kept only
#          so the change is legible. It used to say image_tier='keyframe'
#          rows were retained FOREVER by construction, because sweep() only
#          matches retained_until IS NOT NULL and sweep_raw() only matched
#          image_tier='raw', so a keyframe row matched neither query. That
#          was safe while keyframe capture ran at 3 frames/day.
#
#          v2.69.0 raised keyframe capture to one frame every 5 minutes of
#          daylight (~168/day/camera) so the weekly/monthly time-lapse Reels
#          have enough frames to play as continuous motion. Unbounded, that
#          is ~117 GB/year across house-yard and duo2. Keyframes are now
#          swept on a rolling window (keyframe_capture.retention_hours,
#          default 768h/32 days — sized to outlast the 30-day monthly reel).
#
#          The old note also said expiring keyframes must be a THIRD sweep
#          and that neither existing WHERE clause should be broadened. The
#          spirit of that is honoured: sweep()'s clause is untouched, and
#          sweep_raw() was not broadened — it takes an explicit image_tier
#          argument defaulting to 'raw', so it prunes exactly the one tier
#          its caller names and no caller's behaviour changed.
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
    with sqlite3.connect(str(db_path), timeout=30) as c:
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
              retention_hours: int = 24, dry_run: bool = False,
              image_tier: str = "raw") -> dict:
    """Rolling hour-granular pruner for vlm_bypass cameras (tier='raw').

    Deletes both the JPEG on disk and the image_archive row for rows where:
      - camera_id matches
      - image_tier matches (default 'raw')
      - ts < now - retention_hours

    Unlike the daily sweep, these rows are DROPPED from the DB entirely (not
    kept as metadata-only) — the raw path exists for transient on-disk
    storage, and an orphaned row with image_path=NULL serves no purpose.

    image_tier (09-Aug-2026, v2.69.0): the keyframe tier reuses this exact
    pruner rather than growing a second near-identical one. Keyframes were
    "permanent" when capture ran at 3/day, but v2.69.0 raised that to ~168/day
    for the dense time-lapse reels, so they now need the same rolling bound.
    Default 'raw' keeps every existing caller byte-identical.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
    cutoff_iso = cutoff.isoformat(timespec="seconds")
    deleted = 0
    freed_bytes = 0
    errors: list[str] = []
    with sqlite3.connect(str(db_path), timeout=30) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("""
            SELECT id, image_path FROM image_archive
            WHERE camera_id = ?
              AND image_tier = ?
              AND ts < ?
        """, (camera_id, image_tier, cutoff_iso)).fetchall()
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
    return {"camera": camera_id, "tier": image_tier, "deleted": deleted,
            "freed_bytes": freed_bytes, "errors": errors, "dry_run": dry_run,
            "cutoff": cutoff_iso}


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
