# Author: Claude Opus 4.7 (1M context)
# Date: 26-April-2026
# PURPOSE: Find iPhone photos added to the local Photos.app library in the
#          last N hours, run the VLM pipeline on each new one, persist into
#          image_archive (camera_id="iphone"), and post strong-tier results
#          to Discord #farm-2026 for Boss to react.
#
#          Reads Photos.sqlite read-only (mirrors on_this_day.selector
#          pattern). Resolves originals on disk by globbing the
#          originals/{first-uuid-char}/{uuid}.* path. Converts HEIC via
#          macOS `sips` (no new Python dependency, no pillow-heif).
#
#          Dedupe is a flat JSON ledger at data/iphone-lane/ingested.json
#          mapping uuid -> ingested_at_iso. Same disk pattern as
#          tools/on_this_day's posted.json.
#
#          IG auto-posting is gated by config.instagram.enabled (false in
#          production); strong gems just land in Discord and the existing
#          reaction-gated lanes pick them up like any camera gem.
# SRP/DRY check: Pass — orchestration only. VLM call, store, Discord post
#                are all pipeline.* primitives.

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import os.path
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import cv2

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.gem_poster import load_dotenv, post_gem, should_post
from tools.pipeline.quality_gate import passes_trivial_gate
from tools.pipeline.store import ensure_schema, store
from tools.pipeline.vlm_enricher import (
    EnricherError,
    ModelNotLoaded,
    ValidationFailed,
    enrich,
)

log = logging.getLogger("iphone_lane.ingest")

PHOTOS_LIBRARY = Path(
    "/Users/macmini/Pictures/Photos Library.photoslibrary"
)
PHOTOS_SQLITE = PHOTOS_LIBRARY / "database" / "Photos.sqlite"
ORIGINALS_ROOT = PHOTOS_LIBRARY / "originals"

# Cocoa epoch: ZDATECREATED is seconds since 2001-01-01 UTC.
COCOA_EPOCH_OFFSET = 978_307_200

LEDGER_REL = Path("data/iphone-lane/ingested.json")

CAMERA_ID = "iphone"
CAMERA_CONTEXT = (
    "Photo Boss took on his iPhone (curated by hand). Most are family / "
    "farm / pets / yard / coop scenes worth sharing. Some are routine "
    "(receipts, screenshots, app captures) — those should rate share_worth=skip."
)


def _open_photos_db_readonly(path: Path = PHOTOS_SQLITE) -> sqlite3.Connection:
    # Photos.app keeps this DB open in WAL mode. Open via URI in mode=ro
    # so we can never contend for a writer lock or interfere with sync.
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def find_recent_iphone_photos(
    since_hours: float,
    db_path: Path = PHOTOS_SQLITE,
) -> list[dict]:
    """Return every non-trashed, non-hidden photo added or imported in the
    last `since_hours` hours. Uses the MAX of ZDATECREATED and ZADDEDDATE so
    photos that arrive via iCloud later than they were taken still register.
    Videos (ZKIND=1) excluded — IG/FB story rendering on the gem path is
    image-only.
    """
    cutoff_unix = (
        dt.datetime.now(dt.timezone.utc).timestamp() - since_hours * 3600
    )
    cutoff_cocoa = cutoff_unix - COCOA_EPOCH_OFFSET

    sql = """
        SELECT
            ZUUID,
            ZDATECREATED,
            ZADDEDDATE,
            ZFILENAME,
            ZDIRECTORY
        FROM ZASSET
        WHERE ZTRASHEDDATE IS NULL
          AND ZHIDDEN = 0
          AND ZKIND = 0
          AND COALESCE(MAX(ZDATECREATED, COALESCE(ZADDEDDATE, ZDATECREATED)),
                       ZDATECREATED) >= ?
        ORDER BY COALESCE(ZADDEDDATE, ZDATECREATED) ASC
    """
    out: list[dict] = []
    with _open_photos_db_readonly(db_path) as conn:
        for row in conn.execute(sql, (cutoff_cocoa,)):
            try:
                created_utc = dt.datetime.fromtimestamp(
                    row["ZDATECREATED"] + COCOA_EPOCH_OFFSET,
                    dt.timezone.utc,
                )
            except (TypeError, ValueError, OSError):
                continue
            out.append({
                "uuid": row["ZUUID"],
                "date_taken_utc": created_utc,
                "filename": row["ZFILENAME"] or "",
                "directory": row["ZDIRECTORY"] or "",
            })
    log.info(
        "find_recent_iphone_photos: %d candidates in last %.1fh",
        len(out), since_hours,
    )
    return out


def resolve_original_path(uuid: str, filename: str = "") -> Optional[Path]:
    """Photos.app stores originals under originals/{first-uuid-char}/{uuid}.{ext}.
    The exact extension varies — .heic, .jpg, .png, .jpeg. If `filename` is
    given, prefer that; otherwise glob.
    """
    if not uuid:
        return None
    bucket = ORIGINALS_ROOT / uuid[0]
    if filename:
        # ZFILENAME is the original on-camera filename (IMG_1234.HEIC), not
        # the on-disk name. The on-disk name is always {uuid}.{ext}.
        ext = os.path.splitext(filename)[1].lower()
        if ext:
            candidate = bucket / f"{uuid}{ext}"
            if candidate.exists():
                return candidate
    # Fall back to glob — covers extension drift and missing ZFILENAME.
    matches = list(bucket.glob(f"{uuid}.*"))
    return matches[0] if matches else None


def heic_to_jpeg_bytes(src: Path, quality: int = 92) -> bytes:
    """Convert any image sips understands (HEIC, JPEG, PNG, etc.) to JPEG
    bytes. sips ships with macOS — no Python deps needed. EXIF orientation
    is honored by sips automatically (writes baked-in upright pixels)."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        result = subprocess.run(
            [
                "/usr/bin/sips",
                "-s", "format", "jpeg",
                "-s", "formatOptions", str(quality),
                str(src),
                "--out", str(tmp_path),
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"sips failed (rc={result.returncode}): "
                f"{result.stderr.decode('utf-8', 'replace')[:200]}"
            )
        return tmp_path.read_bytes()
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def load_ledger(repo_root: Path) -> dict:
    p = repo_root / LEDGER_REL
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        log.warning("ledger %s is corrupt JSON — starting fresh", p)
        return {}


def save_ledger(repo_root: Path, ledger: dict) -> None:
    p = repo_root / LEDGER_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, indent=2, sort_keys=True))


def load_pipeline_config(repo_root: Path) -> tuple[dict, dict, str]:
    pipeline_dir = repo_root / "tools" / "pipeline"
    cfg = json.loads((pipeline_dir / "config.json").read_text())
    schema = json.loads((pipeline_dir / "schema.json").read_text())
    prompt = (pipeline_dir / "prompt.md").read_text()
    return cfg, schema, prompt


def run(
    since_hours: float = 6.0,
    max_per_run: int = 8,
    dry_run: bool = False,
    repo_root: Path = REPO_ROOT,
) -> dict:
    """One ingest pass. Returns a summary dict."""
    cfg, schema, prompt = load_pipeline_config(repo_root)
    db_path = repo_root / cfg["guardian_db_path"]
    archive_root = repo_root / cfg["archive_root"]
    ensure_schema(db_path)

    # Webhook lives in repo .env; mirror gem_poster's loader.
    load_dotenv(repo_root / ".env")
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")

    ledger = load_ledger(repo_root)
    candidates = find_recent_iphone_photos(since_hours)

    summary = {
        "candidates": len(candidates),
        "skipped_already_ingested": 0,
        "skipped_no_original": 0,
        "skipped_decode": 0,
        "skipped_trivial_gate": 0,
        "skipped_vlm_error": 0,
        "ingested": 0,
        "posted_to_discord": 0,
        "tiers": {"strong": 0, "decent": 0, "skip": 0},
        "dry_run": dry_run,
    }

    processed_this_run = 0
    for cand in candidates:
        uuid = cand["uuid"]
        if uuid in ledger:
            summary["skipped_already_ingested"] += 1
            continue
        if processed_this_run >= max_per_run:
            log.info("max_per_run=%d reached — deferring rest", max_per_run)
            break

        src = resolve_original_path(uuid, cand["filename"])
        if src is None or not src.exists():
            log.info("uuid=%s: original missing on disk (filename=%r) — skipping",
                     uuid, cand["filename"])
            summary["skipped_no_original"] += 1
            # Don't ledger — try again next run in case iCloud finishes downloading.
            continue

        try:
            jpeg_bytes = heic_to_jpeg_bytes(src)
        except Exception as e:
            log.warning("uuid=%s: convert failed: %s", uuid, e)
            summary["skipped_decode"] += 1
            continue

        # Decode for trivial-gate metrics. Gate is informational on this lane
        # (Boss curates by hand) but the metrics row in image_archive expects
        # std_dev / laplacian_var / exposure_p50, so we always compute them.
        import numpy as np
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            log.warning("uuid=%s: cv2.imdecode returned None", uuid)
            summary["skipped_decode"] += 1
            continue
        _, gate_metrics = passes_trivial_gate(
            img, std_dev_floor=cfg.get("std_dev_floor", 5.0)
        )

        if dry_run:
            log.info("dry_run: uuid=%s filename=%r path=%s bytes=%d",
                     uuid, cand["filename"], src, len(jpeg_bytes))
            processed_this_run += 1
            continue

        try:
            vlm_result = enrich(
                image_bytes=jpeg_bytes,
                camera_name=CAMERA_ID,
                camera_context=CAMERA_CONTEXT,
                lm_base=cfg["lm_studio_base"],
                model_id=cfg["vlm_model_id"],
                prompt_template=prompt,
                schema=schema,
                max_tokens=cfg.get("vlm_max_tokens", 600),
                temperature=cfg.get("vlm_temperature", 0.2),
                timeout=cfg.get("vlm_timeout_seconds", 180),
            )
        except ModelNotLoaded as e:
            log.warning("uuid=%s: VLM model not loaded — %s. Aborting run so "
                        "we don't burn through the candidates.", uuid, e)
            break
        except (EnricherError, ValidationFailed) as e:
            log.warning("uuid=%s: VLM error: %s", uuid, e)
            summary["skipped_vlm_error"] += 1
            continue
        except Exception as e:
            log.warning("uuid=%s: VLM transient (%s): %s",
                        uuid, type(e).__name__, e)
            summary["skipped_vlm_error"] += 1
            continue

        try:
            store_result = store(
                db_path=db_path,
                archive_root=archive_root,
                camera_id=CAMERA_ID,
                jpeg_bytes=jpeg_bytes,
                gate_metrics=gate_metrics,
                vlm_result=vlm_result,
                vlm_model=cfg["vlm_model_id"],
                retention_days_strong=cfg.get("retention_days_strong", 365),
                retention_days_decent=cfg.get("retention_days_decent", 90),
                retention_days_concerns=cfg.get("retention_days_concerns"),
                downscale_decent_long_edge_px=cfg.get(
                    "downscale_decent_long_edge_px", 1920),
                downscale_decent_jpeg_quality=cfg.get(
                    "downscale_decent_jpeg_quality", 85),
            )
        except Exception as e:
            log.exception("uuid=%s: store failed", uuid)
            summary["skipped_vlm_error"] += 1
            continue

        tier = store_result["tier"]
        summary["ingested"] += 1
        summary["tiers"][tier] = summary["tiers"].get(tier, 0) + 1

        posted = False
        try:
            if should_post(vlm_result["metadata"], tier, camera_id=CAMERA_ID):
                posted = post_gem(
                    image_bytes=jpeg_bytes,
                    caption=vlm_result["metadata"].get("caption_draft", "") or "",
                    camera_name=CAMERA_ID,
                    webhook_url=webhook,
                )
                if posted:
                    summary["posted_to_discord"] += 1
        except Exception as e:
            log.warning("uuid=%s: discord post wrapper failed: %s", uuid, e)

        ledger[uuid] = {
            "ingested_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "tier": tier,
            "posted_to_discord": posted,
            "original_path": str(src),
            "date_taken_utc": cand["date_taken_utc"].isoformat(timespec="seconds"),
        }
        # Save after each successful ingest so a mid-run crash doesn't
        # cause double-posting on the next run.
        save_ledger(repo_root, ledger)
        processed_this_run += 1
        log.info("uuid=%s tier=%s posted=%s", uuid, tier, posted)

    return summary
