# Author: Claude Opus 5
# Date: 25-July-2026
# PURPOSE: Static-region artifact suppression for Farm Guardian's night alert path (gate ② of
#          docs/25-Jul-2026-night-alert-artifact-suppression-plan.md). Near-lens artifacts —
#          spider webs strung from the camera housing bridge to the lens glass, lit up by the
#          IR illuminator — read to YOLO as large, bright, roughly person-shaped blurs that sit
#          in EXACTLY the same pixels for hours. Real animals and people translate across the
#          frame; a web does not. This module tracks per (camera, class) bbox regions over time
#          and reports a suppression reason once a region has held position long enough to be
#          scenery rather than a visitor.
#
#          Measured basis (00:00-07:00 on 25-Jul-2026, duo2): 2,222 'person' detections, 100%
#          of them inside three frame-edge bbox clusters, the dominant one holding position for
#          five straight hours. Contrast a real person crossing house-yard at 21:44 on 24-Jul,
#          whose bbox x1 walked 774 -> 1338 in 44 seconds.
#
#          Deliberately conservative: the FIRST sighting of any region always passes through
#          (a region must persist for static_seconds before it can be called scenery), regions
#          decay after an absence so nothing is ever permanently blacklisted, and state is
#          in-memory only so a restart begins clean. Suppression stops the ALERT, never the
#          detection record — callers write the row with suppressed=1 so the dashboard, reports
#          and any later audit still see it.
#
#          Integration: guardian.py::_on_frame calls classify() for each predator detection
#          before the alert-cooldown and VLM gates. No network, no model, no I/O.
# SRP/DRY check: Pass — single responsibility is "has this bbox been sitting still long enough
#                to be scenery?". Reuses detect.Detection rather than defining a parallel shape;
#                does not duplicate detect.py's dwell tracking (that counts CONSECUTIVE frames
#                for alertability over seconds; this measures POSITIONAL persistence over
#                minutes, and the two answer different questions).

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("guardian.artifact_filter")

# Reason string written to detections.suppression_reason. Stable — dashboards and any future
# analysis will group on it, so do not reword casually.
SUPPRESSION_REASON = "static-region"


@dataclass
class _Region:
    """One tracked bbox region for a (camera, class) pair.

    Movement is measured over a ROLLING WINDOW of recent centroids (`samples`), not against a
    fixed anchor. The first cut of this module used peak-drift-since-first-sighting, and it
    failed on the real data: a single excursion — YOLO's box occasionally jumps ~122px on
    these web detections (p95 of the 25-Jul night) — permanently disqualified the region,
    and because no detection gap that night exceeded 88s the decay never reset it. Result:
    26.6% suppression where 95%+ was needed. A rolling window forgets old excursions.
    """
    bbox: tuple  # most recent bbox, used for IoU matching
    first_seen: float
    last_seen: float
    hits: int = 1
    announced: bool = False  # log once on the allow->suppress transition, not every frame
    # (timestamp, centroid_x, centroid_y), pruned to the static_seconds window
    samples: list = field(default_factory=list)


def _iou(box_a: tuple, box_b: tuple) -> float:
    """Intersection-over-union of two (x1, y1, x2, y2) boxes. 0.0 when disjoint."""
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = ix2 - ix1, iy2 - iy1
    if iw <= 0 or ih <= 0:
        return 0.0

    intersection = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def _centroid(bbox: tuple) -> tuple:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _median(values: list) -> float:
    ordered = sorted(values)
    count = len(ordered)
    mid = count // 2
    if count % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _spread_p90(samples: list) -> float:
    """90th-percentile distance of recent centroids from their median position.

    A percentile rather than a max because these detections jitter: on the 25-Jul night 90%
    of web centroids sat within 3px of the median while the p95 reached 122px. Taking the max
    would let one outlier mask five hours of a motionless web. A genuinely moving subject has
    MOST of its samples far from the median, so p90 rises immediately and suppression stops.
    """
    xs = [s[1] for s in samples]
    ys = [s[2] for s in samples]
    med_x, med_y = _median(xs), _median(ys)
    distances = sorted(
        ((x - med_x) ** 2 + (y - med_y) ** 2) ** 0.5 for x, y in zip(xs, ys)
    )
    index = min(len(distances) - 1, int(len(distances) * 0.9))
    return distances[index]


class StaticArtifactFilter:
    """Suppresses alerts for detections whose bbox has held position long enough to be scenery.

    Thread-safe: guardian.py calls this from per-camera capture threads.
    """

    def __init__(self, config: dict):
        cfg = config.get("artifact_filter", {})

        self._enabled = cfg.get("enabled", True)
        # How similar a bbox must be to an existing region to count as "the same thing".
        self._iou_threshold = float(cfg.get("iou_threshold", 0.6))
        # How long a region must persist before it is treated as scenery. Ten minutes is far
        # longer than any predator lingers in one spot, and far shorter than a web's all-night
        # tenure. The first ten minutes of any new region still alert normally.
        self._static_seconds = float(cfg.get("static_seconds", 600))
        # How far the centroid may wander from its anchor and still count as "not moving".
        self._max_drift_px = float(cfg.get("max_drift_px", 40))
        # Forget a region this long after its last sighting, so a cleared web does not mute
        # that patch of frame forever.
        self._decay_seconds = float(cfg.get("decay_seconds", 300))
        # Minimum centroid samples in the rolling window before the spread statistic is
        # trusted. Guards slow-polling cameras from being judged on two data points.
        self._min_samples = int(cfg.get("min_samples", 8))
        # Cameras that opt out entirely.
        self._exclude_cameras = set(cfg.get("exclude_cameras", []))

        # (camera_name, class_name) -> list[_Region]
        self._regions: dict[tuple, list] = {}
        self._lock = threading.Lock()

        log.info(
            "StaticArtifactFilter initialized — enabled=%s, iou>=%.2f, static>=%.0fs, "
            "drift<%.0fpx, decay=%.0fs, excluded=%s",
            self._enabled, self._iou_threshold, self._static_seconds,
            self._max_drift_px, self._decay_seconds,
            sorted(self._exclude_cameras) or "none",
        )

    def classify(self, camera_name: str, detection, now: Optional[float] = None) -> Optional[str]:
        """Return a suppression reason for this detection, or None to let it through.

        `detection` is a detect.Detection (only .bbox and .class_name are read).

        `now` overrides the clock, and exists so the replay harness
        (scripts/replay-artifact-filter.py) can drive this with the real recorded timestamps
        from data/guardian.db instead of monkeypatching time.time(). Production callers omit
        it.

        Returning a reason means "do not ALERT on this" — the caller still records the
        detection, marked suppressed. Never raises: on any unexpected internal error it
        returns None (allow), because failing toward an alert is always the safe direction.
        """
        if not self._enabled or camera_name in self._exclude_cameras:
            return None

        try:
            now = time.time() if now is None else now
            key = (camera_name, detection.class_name)
            bbox = tuple(float(v) for v in detection.bbox)
            cx, cy = _centroid(bbox)

            with self._lock:
                regions = self._regions.get(key, [])

                # Drop regions that have not been seen recently. This is what makes the
                # filter self-healing: clean the lens, the web stops being detected, and
                # within decay_seconds the region is gone.
                regions = [r for r in regions if (now - r.last_seen) <= self._decay_seconds]

                match = None
                best_iou = 0.0
                for region in regions:
                    overlap = _iou(bbox, region.bbox)
                    if overlap >= self._iou_threshold and overlap > best_iou:
                        match, best_iou = region, overlap

                if match is None:
                    # First sighting of something in this position — always allowed through.
                    regions.append(_Region(
                        bbox=bbox, first_seen=now, last_seen=now,
                        samples=[(now, cx, cy)],
                    ))
                    self._regions[key] = regions
                    return None

                # Known region: refresh it, then re-measure movement over the rolling window.
                match.bbox = bbox
                match.last_seen = now
                match.hits += 1
                match.samples.append((now, cx, cy))
                cutoff = now - self._static_seconds
                match.samples = [s for s in match.samples if s[0] >= cutoff]
                self._regions[key] = regions

                age = now - match.first_seen
                if age < self._static_seconds or len(match.samples) < self._min_samples:
                    # Too new, or too little evidence to call it. Alert normally.
                    return None

                spread = _spread_p90(match.samples)
                if spread >= self._max_drift_px:
                    # It is moving. If it had previously been muted, say so — a region that
                    # comes back to life is exactly the case Boss must not miss.
                    if match.announced:
                        match.announced = False
                        log.info(
                            "Artifact filter: '%s' %s region started moving again "
                            "(spread %.0fpx >= %.0fpx) — alerts un-muted",
                            camera_name, detection.class_name, spread, self._max_drift_px,
                        )
                    return None

                if not match.announced:
                    match.announced = True
                    log.info(
                        "Artifact filter: '%s' %s at bbox(%d,%d,%d,%d) has held position for "
                        "%.0f min (p90 spread %.0fpx over %d recent samples, %d detections "
                        "total) — treating as static scenery, muting alerts until it moves "
                        "or disappears",
                        camera_name, detection.class_name,
                        bbox[0], bbox[1], bbox[2], bbox[3],
                        age / 60.0, spread, len(match.samples), match.hits,
                    )
                return SUPPRESSION_REASON

        except Exception as exc:
            # Never let a bug in suppression cause a missed predator.
            log.error(
                "Artifact filter error on '%s' (%s) — allowing detection through: %s",
                camera_name, getattr(detection, "class_name", "?"), exc,
            )
            return None

    def active_regions(self) -> list[dict]:
        """Snapshot of currently-muted regions, for the dashboard and for debugging."""
        now = time.time()
        out: list[dict] = []
        with self._lock:
            for (camera_name, class_name), regions in self._regions.items():
                for region in regions:
                    if (now - region.last_seen) > self._decay_seconds:
                        continue
                    age = now - region.first_seen
                    spread = (
                        _spread_p90(region.samples)
                        if len(region.samples) >= self._min_samples else None
                    )
                    out.append({
                        "camera": camera_name,
                        "class": class_name,
                        "bbox": [round(v, 1) for v in region.bbox],
                        "age_seconds": round(age, 1),
                        "hits": region.hits,
                        "spread_p90_px": round(spread, 1) if spread is not None else None,
                        "muted": (
                            age >= self._static_seconds
                            and spread is not None
                            and spread < self._max_drift_px
                        ),
                    })
        return out
