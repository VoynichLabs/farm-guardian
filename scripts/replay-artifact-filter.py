#!/usr/bin/env python3
# Author: Claude Opus 5
# Date: 25-July-2026
# PURPOSE: Replay harness for the v2.53.0 static-region artifact filter. Feeds REAL recorded
#          detections out of data/guardian.db through StaticArtifactFilter in timestamp order
#          (driving classify()'s clock with the recorded detected_at values) and reports what
#          would have been suppressed.
#
#          Two cases matter, and the second is the one that matters most:
#            1. duo2 person, 25-Jul-2026 00:00-07:00 — 2,222 detections that were all spider
#               webs on the lens. These must end up suppressed.
#            2. house-yard person, 24-Jul-2026 21:44 — a REAL person walking across the yard,
#               confirmed by the verifier at the time. This must NOT be suppressed. If this
#               regression check ever goes quiet, the filter is wrong no matter how good the
#               duo2 numbers look.
#
#          Run on the Mac Mini, where the database lives:
#              ./venv/bin/python scripts/replay-artifact-filter.py
# SRP/DRY check: Pass — verification only, imports the production StaticArtifactFilter rather
#                than reimplementing its logic. No mocks: real DB rows, real timestamps.

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from artifact_filter import StaticArtifactFilter  # noqa: E402

DB_PATH = "data/guardian.db"

# Config identical to the shipped config.json defaults.
CONFIG = {
    "artifact_filter": {
        "enabled": True,
        "iou_threshold": 0.6,
        "static_seconds": 600,
        "max_drift_px": 40,
        "decay_seconds": 300,
        "exclude_cameras": [],
    }
}


@dataclass
class ReplayDetection:
    """Minimal stand-in for detect.Detection — classify() reads only these two fields."""
    class_name: str
    bbox: tuple


def _epoch(iso_ts: str) -> float:
    return datetime.fromisoformat(iso_ts).timestamp()


# detection.alert_cooldown_seconds from config.json — the per-class debounce that turns
# surviving detections into actual Discord posts. Replayed here because the number Boss
# experiences is ALERTS, not detections.
ALERT_COOLDOWN_SECONDS = 90


def replay(conn, camera: str, class_name: str, start: str, end: str,
           with_vlm: bool = False, confidence_upper: float = 0.85) -> dict:
    # is_predator = 1 is essential for fidelity, not a convenience filter. detect.py clears
    # the flag on detections that have not yet met min_dwell_frames, and those never reach
    # the alert path in production (they also get no snapshot saved, which is how this was
    # spotted: 1,120 of the duo2 rows had is_predator=0 and zero snapshots). Replaying them
    # would inflate every number here and measure a pipeline that does not exist.
    rows = conn.execute(
        """SELECT detected_at, bbox_x1, bbox_y1, bbox_x2, bbox_y2, snapshot_path, confidence
           FROM detections
           WHERE camera_id = ? AND class_name = ? AND detected_at >= ? AND detected_at < ?
             AND is_predator = 1
           ORDER BY detected_at""",
        (camera, class_name, start, end),
    ).fetchall()

    filt = StaticArtifactFilter(CONFIG)
    suppressed = 0
    allowed = 0
    first_suppression_at: Optional[str] = None
    warmup_allowed = 0  # allowed while inside the mandatory static_seconds warm-up

    # Alert simulation, with and without the filter, both under the same 90s cooldown.
    alerts_with_filter = 0
    alerts_without_filter = 0
    last_alert_with = -1e9
    last_alert_without = -1e9
    window_start = _epoch(rows[0][0]) if rows else 0.0

    # Gate ③ accounting (only populated with --with-vlm)
    vlm_checked = 0
    vlm_suppressed = 0
    vlm_confirmed = 0
    vlm_no_snapshot = 0
    alerts_full_pipeline = 0
    last_alert_full = -1e9

    for detected_at, x1, y1, x2, y2, snapshot_path, confidence in rows:
        now = _epoch(detected_at)
        det = ReplayDetection(class_name=class_name, bbox=(x1, y1, x2, y2))
        reason = filt.classify(camera, det, now=now)

        if reason:
            suppressed += 1
            if first_suppression_at is None:
                first_suppression_at = detected_at
        else:
            allowed += 1
            if (now - window_start) < CONFIG["artifact_filter"]["static_seconds"]:
                warmup_allowed += 1

            # Gate ①: the alert cooldown. Only a detection that would actually POST is
            # worth a VLM round-trip — this is the reordering v2.53.0 introduces.
            if (now - last_alert_with) >= ALERT_COOLDOWN_SECONDS:
                alerts_with_filter += 1
                last_alert_with = now

                # Gate ③: real local VLM call on the real saved frame.
                if with_vlm and (now - last_alert_full) >= ALERT_COOLDOWN_SECONDS:
                    if confidence >= confidence_upper:
                        alerts_full_pipeline += 1
                        last_alert_full = now
                    elif snapshot_path and os.path.exists(snapshot_path):
                        import cv2
                        from llm_verify import verify_detection
                        frame = cv2.imread(snapshot_path)
                        if frame is None:
                            vlm_no_snapshot += 1
                            alerts_full_pipeline += 1
                            last_alert_full = now
                        else:
                            vlm_checked += 1
                            verdict = verify_detection(
                                frame, class_name, confidence, (x1, y1, x2, y2)
                            )
                            if verdict.available and not verdict.alert_worthy:
                                vlm_suppressed += 1
                            else:
                                vlm_confirmed += 1
                                alerts_full_pipeline += 1
                                last_alert_full = now
                    else:
                        # No saved frame to replay. Counted separately and treated as an
                        # alert, because fail-open is the production behaviour.
                        vlm_no_snapshot += 1
                        alerts_full_pipeline += 1
                        last_alert_full = now

        if (now - last_alert_without) >= ALERT_COOLDOWN_SECONDS:
            alerts_without_filter += 1
            last_alert_without = now

    return {
        "total": len(rows),
        "suppressed": suppressed,
        "allowed": allowed,
        "warmup_allowed": warmup_allowed,
        "first_suppression_at": first_suppression_at,
        "alerts_with_filter": alerts_with_filter,
        "alerts_without_filter": alerts_without_filter,
        "with_vlm": with_vlm,
        "vlm_checked": vlm_checked,
        "vlm_suppressed": vlm_suppressed,
        "vlm_confirmed": vlm_confirmed,
        "vlm_no_snapshot": vlm_no_snapshot,
        "alerts_full_pipeline": alerts_full_pipeline,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-vlm", action="store_true",
        help="Also replay gate ③ by calling the real local VLM on saved snapshots. "
             "Mini-only, ~1.2s per surviving detection.",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    failures = []

    print("=" * 78)
    print("CASE 1 — duo2 'person', 25-Jul 00:00-07:00 (spider webs on the lens)")
    print("=" * 78)
    webs = replay(conn, "duo2", "person", "2026-07-25T00:00", "2026-07-25T07:00",
                  with_vlm=args.with_vlm)
    print(f"  total detections     : {webs['total']}")
    print(f"  suppressed           : {webs['suppressed']}")
    print(f"  allowed through      : {webs['allowed']}"
          f"  (of which {webs['warmup_allowed']} in the mandatory 10-min warm-up)")
    print(f"  first suppression    : {webs['first_suppression_at']}")
    print()
    print("  ALERTS ACTUALLY POSTED (90s per-class cooldown applied) — the number Boss feels:")
    print(f"    before (no filter)      : {webs['alerts_without_filter']}")
    print(f"    after gates ①+②         : {webs['alerts_with_filter']}")
    if webs["with_vlm"]:
        print(f"    after gates ①+②+③ (VLM) : {webs['alerts_full_pipeline']}")
        print(f"      VLM calls made        : {webs['vlm_checked']} "
              f"(suppressed {webs['vlm_suppressed']}, confirmed {webs['vlm_confirmed']})")
        print(f"      no saved frame        : {webs['vlm_no_snapshot']} "
              f"(counted as alerts — fail-open)")
    if webs["total"]:
        pct = 100.0 * webs["suppressed"] / webs["total"]
        print(f"  detection suppression rate (gate ② alone) : {pct:.1f}%")
        # The pass criterion is ALERTS through the WHOLE chain, not raw detections at one
        # gate. Some detections legitimately pass gate ② — every region's first 10 minutes,
        # by design, which is exactly what guarantees a real predator is never muted on
        # arrival. Gate ③ is what judges those. Boss's complaint was 139 alerts in a night,
        # so alerts are what we measure.
        final = webs["alerts_full_pipeline"] if webs["with_vlm"] else webs["alerts_with_filter"]
        label = "full pipeline" if webs["with_vlm"] else "gates ①+② only"
        if final > 5:
            failures.append(
                f"duo2 would still post {final} alerts overnight ({label}; target: <= 5)"
            )
    else:
        failures.append("no duo2 rows found — window or DB is wrong, nothing was verified")

    print()
    print("=" * 78)
    print("CASE 2 — house-yard 'person', 24-Jul 21:44 (REAL person — regression check)")
    print("=" * 78)
    real = replay(conn, "house-yard", "person", "2026-07-24T21:44:00", "2026-07-24T21:46:00")
    print(f"  total detections     : {real['total']}")
    print(f"  suppressed           : {real['suppressed']}")
    print(f"  allowed through      : {real['allowed']}")
    print(f"  alerts posted        : {real['alerts_with_filter']} (must be >= 1)")
    if real["total"] and real["alerts_with_filter"] < 1:
        failures.append("REGRESSION: the real person would have produced no alert at all")
    if real["total"] == 0:
        failures.append("no house-yard rows found — the regression check did not run")
    elif real["suppressed"] > 0:
        failures.append(
            f"REGRESSION: {real['suppressed']} real-person detections were suppressed"
        )

    print()
    print("=" * 78)
    if failures:
        print("FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS — webs muted, real person still alerts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
