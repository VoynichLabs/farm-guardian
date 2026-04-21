# Author: Claude Opus 4.7 (1M context)
# Date: 21-April-2026
# PURPOSE: Bring the master photo catalog at
#          ~/bubba-workspace/projects/photos-curation/photo-catalog/
#          master-catalog.csv up to parity with the full iPhone Photos
#          library (currently ~64k assets local; the 2026-03 pass
#          catalogued ~21k). This script is a thin, safe wrapper
#          around the existing vision-processor `run_all_folders.py`
#          that already has idempotent per-UUID skip-if-sidecar-exists
#          logic. We deliberately do not re-implement the Qwen 3.5-35B
#          vision call — there is exactly one canonical describer, and
#          it lives in the photos-curation project.
#
#          Two modes:
#            --status : count Photos.sqlite assets vs catalog, print
#                       the delta, do nothing else. Safe to run any
#                       time; no LM Studio dependency.
#            --run    : verify LM Studio is responding at localhost:1234,
#                       then shell out to run_all_folders.py (hours of
#                       work — LM-Studio-throughput-bound). Streams the
#                       processor's stdout to the caller's terminal.
#
#          There is no partial-run flag. run_all_folders.py is
#          resumable per-UUID by design (checks for the sidecar JSON
#          before re-describing), so "process N at a time" is the same
#          as Ctrl-C'ing whenever you've had enough.
#
# SRP/DRY check: Pass — delegates vision work to the existing
#                run_all_folders.py in bubba-workspace (unchanged).
#                This file is orchestration + reachability checks,
#                nothing more.

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .selector import (
    CATALOG_CSV,
    PHOTOS_SQLITE,
    enumerate_assets_for_month_day,  # only used transitively via the DB open helper
    load_catalog_index,
    _open_photos_db_readonly,
)

log = logging.getLogger("on_this_day.catalog_backfill")

# --- Paths to the external vision pipeline ---

PHOTO_CURATION_DIR = Path(
    "/Users/macmini/bubba-workspace/projects/photos-curation/photo-catalog"
)
RUN_ALL_FOLDERS = PHOTO_CURATION_DIR / "run_all_folders.py"
LM_STUDIO_URL = "http://localhost:1234/v1/models"


def count_library_assets(db_path: Path = PHOTOS_SQLITE) -> int:
    """Count non-trashed, non-hidden, non-screenshot photos in the
    Photos library. Mirrors the filters in selector.enumerate_* so the
    delta reflects what the selector would actually care about."""
    with _open_photos_db_readonly(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM ZASSET "
            "WHERE ZTRASHEDDATE IS NULL "
            "  AND ZHIDDEN = 0 "
            "  AND ZKIND = 0"
        ).fetchone()
    return int(row["n"])


def count_catalog_rows(catalog_csv: Path = CATALOG_CSV) -> int:
    if not catalog_csv.exists():
        return 0
    # load_catalog_index is authoritative for what's indexed.
    return len(load_catalog_index(catalog_csv))


def lm_studio_reachable(url: str = LM_STUDIO_URL, timeout: float = 3.0) -> bool:
    """Return True if LM Studio's model listing endpoint answers.
    Does NOT validate that the Qwen vision model is loaded — checking
    that would require a POST that could accidentally auto-load the
    model (see CLAUDE.md → LM Studio safety rules). Presence of the
    server is a sufficient pre-flight; run_all_folders.py will fail
    fast with a clear error if the model isn't loaded."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return False


def print_status() -> None:
    lib = count_library_assets()
    cat = count_catalog_rows()
    delta = lib - cat
    print("=== On-this-day catalog backfill — STATUS ===")
    print(f"  Photos library (filtered):  {lib:>7,} assets")
    print(f"  Master catalog rows:        {cat:>7,}")
    print(f"  Uncatalogued delta:         {delta:>7,}")
    if delta <= 0:
        print("\nCatalog is at parity. Nothing to backfill.")
    else:
        print(
            f"\nRun `python3 -m tools.on_this_day.catalog_backfill --run` "
            f"to describe the remaining {delta:,} photo(s). "
            "This is LM-Studio-throughput-bound (multi-hour for thousands)."
        )
    print("\nLM Studio reachable:", "yes" if lm_studio_reachable() else "no (start it before --run)")


def run_backfill() -> int:
    """Shell out to run_all_folders.py. Returns its exit code."""
    if not RUN_ALL_FOLDERS.exists():
        log.error("run_all_folders.py missing at %s", RUN_ALL_FOLDERS)
        return 2
    if not lm_studio_reachable():
        log.error(
            "LM Studio is not answering at %s. Start the app + load "
            "qwen/qwen3.5-35b-a3b with an explicit context_length "
            "before re-running. See CLAUDE.md LM Studio rules.",
            LM_STUDIO_URL,
        )
        return 3

    log.info(
        "delegating to %s — this will process every un-sidecar'd UUID "
        "under Photos Library originals/. Ctrl-C is safe (resumable).",
        RUN_ALL_FOLDERS,
    )
    # Inherit stdout/stderr so the caller sees live progress + Discord
    # notifications fire the same way they always have.
    proc = subprocess.run(
        [sys.executable, str(RUN_ALL_FOLDERS)],
        cwd=str(PHOTO_CURATION_DIR),
    )
    return proc.returncode


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Backfill the master photo catalog against the Photos library.",
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true",
                      help="Print catalog-vs-library delta and exit.")
    mode.add_argument("--run", action="store_true",
                      help="Shell out to run_all_folders.py and process everything missing.")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _parse_args()
    if args.status:
        print_status()
        return 0
    return run_backfill()


if __name__ == "__main__":
    sys.exit(main())
