# Author: Claude Fable 5
# Date: 16-Jul-2026
# PURPOSE: Build a LOCAL "watch the flock grow" timelapse MP4 from s7-cam's
#          strong-tier gem archive (April 2026 -> today). This is the E2 item
#          from farm-2026's docs/16-Jul-2026-birdcatraz-era-refresh-plan.md:
#          "stitch the flock timelapse locally and review before any
#          publish." It is explicitly NOT a posting lane — nothing in this
#          module calls ig_poster/fb_poster, touches Discord, or writes into
#          farm-2026. Output is a local MP4 for Boss to review; a future,
#          separately-approved change would wire the reviewed result into an
#          actual publish/embed step.
#
#          s7-cam is VLM-enriched (not a vlm_bypass camera), so its usable
#          frames carry image_tier IN ('strong','decent') with share_worth
#          populated — NOT image_tier='raw'. That means
#          ig_selection.select_timelapse_gems() (built for vlm_bypass lanes
#          like mba-cam/gwtc/usb-cam/dominator-cam, which score on
#          laplacian_var because they have no VLM columns) is the wrong tool
#          here: it would never match a single s7-cam row. select_growth_frames()
#          below runs its own query against the VLM-enriched columns
#          (share_worth='strong', image_quality='sharp', has_concerns=0)
#          instead.
#
#          Bucketing: one frame per calendar day if the archive spans <= 90
#          days; otherwise the span is divided into ceil(total_days / 90)
#          -day windows so the frame count still respects reel_stitcher's
#          _MAX_FRAMES (90) cap. Within a bucket, the gem with the most
#          Discord reactions wins (tie -> earliest ts), matching the
#          "human reaction is the quality signal" convention used by
#          ig_selection.py's other selectors.
#
#          stitch_gems_to_reel() does the actual encode (portrait, since
#          s7-cam has been portrait-native since v2.35.2 — landscape=False
#          is correct here, not the landscape=True mode used for the
#          vlm_bypass 16:9 time-lapse lanes).
#
# SRP/DRY check: Pass — checked ig_selection.select_timelapse_gems (wrong
#                tier filter for a VLM-enriched camera, per task framing
#                above) and reel_stitcher.stitch_gems_to_reel (reused
#                directly, no re-implementation of ffmpeg/cv2 stitching).
#                _parse_ts below duplicates ig_selection._parse_archive_ts's
#                five lines rather than importing a private (underscore)
#                helper across modules; _MAX_FRAMES is imported from
#                reel_stitcher rather than restated, since it's the
#                authoritative cap the stitcher itself enforces.

from __future__ import annotations

import logging
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.reel_stitcher import _MAX_FRAMES, stitch_gems_to_reel  # noqa: E402

log = logging.getLogger("pipeline.growth_timelapse")

# Local scratch location outside both git repos — never committed, never
# posted. Falls back to /tmp if this path can't be created on the host.
_SCRATCH_DIR = Path("/Users/macmini/bubba-workspace/scratch")
_FALLBACK_SCRATCH_DIR = Path("/tmp")

_DEFAULT_SINCE = "2026-04-16"
_DEFAULT_CAMERA_ID = "s7-cam"


def _parse_ts(ts_iso: str) -> datetime:
    """Parse an image_archive.ts value into a tz-aware UTC datetime.

    Same tolerance as ig_selection._parse_archive_ts (trailing 'Z',
    naive strings) — duplicated here rather than imported since that
    helper is private to its module.
    """
    clean = (ts_iso or "").replace("Z", "+00:00")
    dt = datetime.fromisoformat(clean)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def select_growth_frames(
    db_path: Path,
    camera_id: str = _DEFAULT_CAMERA_ID,
    since: str = _DEFAULT_SINCE,
) -> list[int]:
    """Select one representative strong/sharp s7-cam gem per day-bucket.

    Query: camera_id matches, share_worth='strong', image_quality='sharp',
    image_path IS NOT NULL (not yet swept), has_concerns=0, ts >= since.

    Bucketing:
      - total_days <= 90: one bucket per calendar day.
      - total_days > 90: bucket_size_days = ceil(total_days / 90); buckets
        are bucket_size_days-day windows starting from the earliest ts's
        date, so the frame count still fits reel_stitcher's 90-frame cap.

    Within each bucket the representative is the gem with the highest
    discord_reactions; ties resolve to the earliest ts (rows arrive from
    the query in ts-ascending order and max() keeps the first element on
    a tie, so no extra sort is needed).

    Returns gem ids oldest-first, capped defensively at
    reel_stitcher._MAX_FRAMES even if the bucketing math is off by one
    (e.g. a 90-day span can produce 91 distinct calendar-day buckets when
    both endpoint days have candidates).

    Returns [] if no rows match (handled gracefully — no exception).
    """
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, ts, discord_reactions
              FROM image_archive
             WHERE camera_id = ?
               AND share_worth = 'strong'
               AND image_quality = 'sharp'
               AND image_path IS NOT NULL
               AND has_concerns = 0
               AND ts >= ?
             ORDER BY ts ASC
            """,
            (camera_id, since),
        ).fetchall()

    if not rows:
        log.info(
            "select_growth_frames: no strong/sharp gems for camera_id=%s "
            "since=%s",
            camera_id, since,
        )
        return []

    items = [dict(row) for row in rows]
    earliest = _parse_ts(items[0]["ts"])
    latest = _parse_ts(items[-1]["ts"])
    total_days = (latest - earliest).days

    bucket_size_days = 1 if total_days <= 90 else math.ceil(total_days / 90)
    start_date = earliest.date()

    # dict preserves insertion order (Python 3.7+); items arrive ts-ascending
    # from the query, so buckets — and each bucket's internal row order —
    # come out chronological with no extra sort.
    groups: dict[object, list[dict]] = {}
    for item in items:
        item_date = _parse_ts(item["ts"]).date()
        if bucket_size_days == 1:
            bucket_key: object = item_date
        else:
            bucket_key = (item_date - start_date).days // bucket_size_days
        groups.setdefault(bucket_key, []).append(item)

    representatives = [
        max(group, key=lambda r: r["discord_reactions"] or 0)
        for group in groups.values()
    ]
    gem_ids = [r["id"] for r in representatives]

    if len(gem_ids) > _MAX_FRAMES:
        log.warning(
            "select_growth_frames: %d buckets exceeds reel_stitcher._MAX_FRAMES "
            "(%d); capping to the oldest %d",
            len(gem_ids), _MAX_FRAMES, _MAX_FRAMES,
        )
        gem_ids = gem_ids[:_MAX_FRAMES]

    log.info(
        "select_growth_frames: %d frame(s) for camera_id=%s "
        "(span=%dd, bucket_size=%dd, since=%s)",
        len(gem_ids), camera_id, total_days, bucket_size_days, since,
    )
    return gem_ids


def build_growth_timelapse(
    db_path: Path,
    output_path: Optional[Path] = None,
    since: str = _DEFAULT_SINCE,
) -> Path:
    """Select s7-cam growth frames and stitch them into a local MP4.

    This is a manual/reviewed tool, not a pipeline job: stitch failures
    (ReelStitcherError from reel_stitcher.stitch_gems_to_reel) are allowed
    to propagate uncaught so the caller sees the real cause, rather than
    being swallowed the way an automated posting lane would swallow them.

    output_path defaults to a local scratch location OUTSIDE both git
    repos: /Users/macmini/bubba-workspace/scratch/growth-timelapse-s7-
    {today}.mp4 (created if missing), falling back to /tmp if that
    directory can't be created on this host.
    """
    gem_ids = select_growth_frames(db_path, camera_id=_DEFAULT_CAMERA_ID, since=since)
    if len(gem_ids) < 2:
        raise RuntimeError(
            f"select_growth_frames returned {len(gem_ids)} candidate frame(s) "
            f"for camera_id={_DEFAULT_CAMERA_ID!r} since={since!r} — "
            f"reel_stitcher needs at least 2 frames to build a reel. Check "
            f"that {_DEFAULT_CAMERA_ID} has share_worth='strong', "
            f"image_quality='sharp' gems with image_path set on or after "
            f"{since}."
        )

    if output_path is None:
        try:
            _SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
            scratch_dir = _SCRATCH_DIR
        except OSError as exc:
            log.warning(
                "build_growth_timelapse: could not create %s (%s); "
                "falling back to %s",
                _SCRATCH_DIR, exc, _FALLBACK_SCRATCH_DIR,
            )
            scratch_dir = _FALLBACK_SCRATCH_DIR
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        output_path = scratch_dir / f"growth-timelapse-s7-{today}.mp4"
    else:
        output_path = Path(output_path)

    log.info(
        "build_growth_timelapse: stitching %d frame(s) -> %s",
        len(gem_ids), output_path,
    )
    return stitch_gems_to_reel(
        gem_ids,
        db_path,
        config={"seconds_per_frame": 1.2, "crossfade_seconds": 0.2},
        output_path=output_path,
        landscape=False,
    )


if __name__ == "__main__":
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    ap = argparse.ArgumentParser(
        description=(
            "Build a LOCAL s7-cam 'watch the flock grow' timelapse from "
            "strong/sharp gems since --since. Writes an MP4 to disk only — "
            "does not post to Instagram/Facebook and does not commit to "
            "farm-2026. Review the output before any separately-approved "
            "publish step."
        )
    )
    ap.add_argument(
        "--since",
        default=_DEFAULT_SINCE,
        help=f"ISO date lower bound for source gems (default: {_DEFAULT_SINCE})",
    )
    args = ap.parse_args()

    cfg = json.loads((REPO_ROOT / "tools/pipeline/config.json").read_text())
    db_path = REPO_ROOT / cfg["guardian_db_path"]

    frame_ids = select_growth_frames(db_path, since=args.since)
    output_path = build_growth_timelapse(db_path, since=args.since)

    date_range = "n/a"
    if frame_ids:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            first_ts = conn.execute(
                "SELECT ts FROM image_archive WHERE id = ?", (frame_ids[0],),
            ).fetchone()["ts"]
            last_ts = conn.execute(
                "SELECT ts FROM image_archive WHERE id = ?", (frame_ids[-1],),
            ).fetchone()["ts"]
        date_range = f"{first_ts} -> {last_ts}"

    print(f"output_path={output_path}")
    print(f"frame_count={len(frame_ids)}")
    print(f"date_range={date_range}")
