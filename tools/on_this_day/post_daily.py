# Author: Claude Opus 4.7 (1M context)
# Date: 21-April-2026
# PURPOSE: CLI orchestrator for the on-this-day Facebook pipeline.
#          Given a calendar date (default: today), select ranked
#          candidates from 2022/2024/2025, and either:
#            - --dry-run (default): write candidates to
#              data/on-this-day/{YYYY-MM-DD}-candidates.json so a
#              human (or a future reaction-gate implementation) can
#              review.
#            - --publish: export the top candidate's Photos master via
#              osxphotos, convert HEIC→JPEG if needed, commit to
#              farm-2026/public/photos/on-this-day/{YYYY-MM-DD}/ via
#              git_helper, and call fb_poster.crosspost_photo with
#              the resulting raw.githubusercontent URL + caption.
#
#          The split between --dry-run and --publish is the
#          quality gate for this pipeline. The camera-gem pipeline
#          uses Discord reactions; this one assumes a human (Boss or
#          another Claude) eyeballs the candidate JSON before
#          promoting it to a live post. A reaction-gate integration
#          is feasible but would require schema work on
#          image_archive; see the plan doc for why we deferred.
#
# SRP/DRY check: Pass — orchestration only. Reuses tools/pipeline/
#                git_helper.py (unchanged), tools/pipeline/fb_poster.py
#                (unchanged), tools/on_this_day/selector.py, and
#                tools/on_this_day/caption.py. The osxphotos export
#                step is a subprocess call — there is no Python
#                binding we prefer over the CLI.

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# Make tools.pipeline imports resolve when this module is run as
# `python3 -m tools.on_this_day.post_daily` from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.pipeline import fb_poster, git_helper  # noqa: E402

from .caption import CaptionSafetyError, compose as compose_caption  # noqa: E402
from .selector import Candidate, select_candidates  # noqa: E402

log = logging.getLogger("on_this_day.post_daily")

# --- Paths ---

FARM_GUARDIAN_ROOT = Path(__file__).resolve().parents[2]
FARM_2026_REPO = Path("/Users/macmini/Documents/GitHub/farm-2026")
CANDIDATES_DIR = FARM_GUARDIAN_ROOT / "data" / "on-this-day"
OSXPHOTOS_BIN = Path("/Users/macmini/.local/bin/osxphotos")

# Commit cap — one FB post a day is the cadence Boss wants.
DEFAULT_TOP_N = 5
DEFAULT_PUBLISH_N = 1


# ---------------------------------------------------------------------------
# Photos master export
# ---------------------------------------------------------------------------


def _osxphotos_export_uuid(uuid: str, dest_dir: Path) -> Path:
    """Export one Photos asset by UUID to dest_dir and return the path
    to the exported file. Raises RuntimeError on osxphotos failure.

    We use osxphotos rather than reading the source_path from the
    catalog directly because the catalog path points at the Photos
    Library package internals, which Apple treats as private (TCC may
    or may not let a subprocess read it depending on the LaunchAgent
    label's TCC history — see feedback_launchd_tcc_label_rename.md).
    osxphotos handles that permission dance correctly.
    """
    if not OSXPHOTOS_BIN.exists():
        raise FileNotFoundError(
            f"osxphotos not found at {OSXPHOTOS_BIN}. Install via "
            "`uv tool install osxphotos` or update the constant."
        )
    dest_dir.mkdir(parents=True, exist_ok=True)

    # --download-missing pulls cloud-only originals from iCloud on
    # demand. --skip-original-if-edited keeps the edited version when
    # Boss has curated a shot. --touch-file sets the filesystem mtime
    # to match EXIF so downstream tools see the real capture date.
    cmd = [
        str(OSXPHOTOS_BIN), "export",
        str(dest_dir),
        "--uuid", uuid,
        "--download-missing",
        "--skip-original-if-edited",
        "--touch-file",
        "--no-progress",
    ]
    log.info("osxphotos export %s → %s", uuid, dest_dir)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(
            f"osxphotos export failed (rc={proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )

    # Find the exported file. osxphotos uses the original filename by
    # default — we don't assume the extension because HEIC/JPEG/PNG
    # are all possible.
    candidates = [p for p in dest_dir.iterdir() if p.is_file() and not p.name.startswith(".")]
    if not candidates:
        raise RuntimeError(f"osxphotos export produced no files for uuid {uuid}")
    if len(candidates) > 1:
        # Pick the largest; Live Photo exports sometimes drop a
        # sidecar .mov + .jpg pair. The JPEG is always the bigger one
        # for modern iPhone photos, but we sort by size defensively.
        candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
    return candidates[0]


def _to_jpeg_if_needed(src: Path) -> Path:
    """HEIC → JPEG via sips (built-in macOS). PNG stays PNG — both are
    in git_helper's allow-list. Returns the path of the file that
    should be committed."""
    ext = src.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png"}:
        return src
    if ext != ".heic":
        raise ValueError(f"unsupported source extension {ext!r}: {src}")

    dst = src.with_suffix(".jpg")
    proc = subprocess.run(
        ["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0 or not dst.exists():
        raise RuntimeError(f"sips HEIC→JPEG failed: {proc.stderr.strip()}")
    return dst


# ---------------------------------------------------------------------------
# Dry-run output
# ---------------------------------------------------------------------------


def write_candidates_json(
    target_date: dt.date,
    candidates: list[Candidate],
    out_dir: Path = CANDIDATES_DIR,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{target_date.isoformat()}-candidates.json"

    payload = {
        "target_date": target_date.isoformat(),
        "eligible_years": [2022, 2024, 2025],
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "candidates": [],
    }
    for c in candidates:
        row = c.as_dict()
        # Also dry-compose the caption so the reviewer sees what would
        # be posted. Caption-safety failures show up in the output
        # rather than getting hidden.
        try:
            row["proposed_caption"] = compose_caption(c)
            row["caption_safe"] = True
        except CaptionSafetyError as e:
            row["proposed_caption"] = None
            row["caption_safe"] = False
            row["caption_reason"] = str(e)
        payload["candidates"].append(row)

    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info("wrote %d candidate(s) → %s", len(candidates), out_path)
    return out_path


# ---------------------------------------------------------------------------
# Publish path
# ---------------------------------------------------------------------------


def publish_candidate(
    candidate: Candidate,
    target_date: dt.date,
    dry_commit: bool = False,
) -> dict:
    """Export the candidate, commit to farm-2026, call fb_poster.
    Returns a dict with uuid, caption, image_url, fb_post_id, error.
    dry_commit=True exports the master locally but skips the
    farm-2026 git push + FB call — useful for smoke tests without
    creating a public artifact."""
    caption = compose_caption(candidate)  # raises CaptionSafetyError

    with tempfile.TemporaryDirectory(prefix="on-this-day-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        raw_master = _osxphotos_export_uuid(candidate.uuid, tmpdir_path)
        jpeg = _to_jpeg_if_needed(raw_master)

        # Rename to a stable, descriptive filename for farm-2026. The
        # UUID prefix guarantees uniqueness across years.
        stable_name = f"{target_date.isoformat()}-{candidate.year}-{candidate.uuid}{jpeg.suffix.lower()}"
        staged = tmpdir_path / stable_name
        shutil.copy2(jpeg, staged)

        if dry_commit:
            log.info("dry_commit: skipping farm-2026 push. exported=%s", staged)
            return {
                "uuid": candidate.uuid,
                "caption": caption,
                "image_url": None,
                "fb_post_id": None,
                "error": None,
                "dry_commit": True,
            }

        subdir = f"on-this-day/{target_date.isoformat()}"
        commit_msg = (
            f"on-this-day: {target_date.isoformat()} — {candidate.year} photo "
            f"{candidate.uuid[:8]} [score={candidate.score}]"
        )
        committed_path, raw_url = git_helper.commit_image_to_farm_2026(
            local_image=staged,
            subdir=subdir,
            repo_path=FARM_2026_REPO,
            commit_message=commit_msg,
        )
        log.info("committed to farm-2026: %s → %s", committed_path, raw_url)

        fb_result = fb_poster.crosspost_photo(image_url=raw_url, caption=caption)
        log.info("fb_poster result: %s", fb_result)

        return {
            "uuid": candidate.uuid,
            "caption": caption,
            "image_url": raw_url,
            "fb_post_id": fb_result.get("fb_post_id"),
            "error": fb_result.get("error"),
            "dry_commit": False,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_date(s: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="On-this-day Facebook publisher (historical iPhone photos).",
    )
    p.add_argument("--date", type=_parse_date, default=None,
                   help="Target calendar date YYYY-MM-DD. Default: today (local).")
    p.add_argument("--top-n", type=int, default=DEFAULT_TOP_N,
                   help=f"Candidates to rank. Default: {DEFAULT_TOP_N}.")
    p.add_argument("--publish", action="store_true",
                   help="Actually post to FB. Without this, --dry-run is implied.")
    p.add_argument("--publish-n", type=int, default=DEFAULT_PUBLISH_N,
                   help=f"How many of the top candidates to publish when --publish. "
                        f"Default: {DEFAULT_PUBLISH_N}.")
    p.add_argument("--uuid", type=str, default=None,
                   help="Publish a specific UUID (must be in today's candidate pool "
                        "AND be catalogued). Requires --publish.")
    p.add_argument("--include-rejected", action="store_true",
                   help="Dry-run only: include filtered rows with rejection_reason.")
    p.add_argument("--dry-commit", action="store_true",
                   help="Publish path: export + caption but skip farm-2026 push + FB call.")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _parse_args()
    target_date = args.date or dt.date.today()

    candidates = select_candidates(
        target_date=target_date,
        top_n=args.top_n,
        include_rejected=args.include_rejected and not args.publish,
    )

    if not candidates:
        log.warning("no candidates for %s across years 2022/2024/2025", target_date.isoformat())
        if not args.publish:
            # Still write an (empty) candidate JSON so ops can tell
            # the script ran and there was genuinely nothing to post.
            write_candidates_json(target_date, [])
        return 0

    # Dry-run path.
    if not args.publish:
        out = write_candidates_json(target_date, candidates)
        print(f"Wrote {len(candidates)} candidate(s) → {out}")
        print("Review the JSON, then re-run with --publish (optionally --uuid <Z>)")
        return 0

    # Publish path.
    to_publish: list[Candidate]
    if args.uuid:
        matched = [c for c in candidates if c.uuid == args.uuid]
        if not matched:
            log.error("requested --uuid %s not in today's candidate pool", args.uuid)
            return 4
        to_publish = matched
    else:
        # Only publish rows we didn't mark rejected.
        to_publish = [c for c in candidates if not c.rejected][: args.publish_n]

    results: list[dict] = []
    any_error = False
    for cand in to_publish:
        try:
            results.append(publish_candidate(cand, target_date, dry_commit=args.dry_commit))
        except CaptionSafetyError as e:
            log.warning("skipping %s: caption unsafe (%s)", cand.uuid, e)
            results.append({"uuid": cand.uuid, "error": str(e), "fb_post_id": None})
            any_error = True
        except Exception as e:
            log.exception("publish failed for %s", cand.uuid)
            results.append({"uuid": cand.uuid, "error": repr(e), "fb_post_id": None})
            any_error = True

    # Persist the publish result alongside the dry-run artifact so
    # there's an audit trail.
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    result_path = CANDIDATES_DIR / f"{target_date.isoformat()}-publish-result.json"
    result_path.write_text(json.dumps({
        "target_date": target_date.isoformat(),
        "published_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dry_commit": args.dry_commit,
        "results": results,
    }, indent=2, default=str), encoding="utf-8")
    log.info("wrote publish result → %s", result_path)

    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())
