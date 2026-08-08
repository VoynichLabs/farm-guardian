# Author: Claude Opus 5
# Date: 07-August-2026
# PURPOSE: Cheap "is there actually an animal in this frame?" gate that runs
#          BEFORE the VLM in the s7-cam gem-hunting path. YOLOv8 inference costs
#          ~16 ms on MPS; a Qwen3-VL-4B call costs ~5.2 s. Measured over 21 days
#          of s7-cam archive, 23.6% of every VLM call landed on a frame the VLM
#          itself scored bird_count=0, and NOT ONE of those 8,444 frames ever
#          became a gem. This module lets the orchestrator spend that ~24% of
#          its VLM budget on frames that actually contain birds instead.
#
#          Also returns box geometry, which the burst selector (frame_selector)
#          uses to rank frames by subject dominance and to penalise crowded
#          "photobomber" frames. That geometry is measured from real pixels, so
#          it is strictly better grounded than the 4b VLM's own eyeballed
#          `largest_subject_pct`.
#
#          RECALL IS THE WHOLE BALLGAME HERE, and the cost is asymmetric: a
#          false negative silently throws away a gem, while a false positive
#          costs one 5.2 s VLM call — i.e. exactly the status quo. So this is
#          tuned far toward recall. Measured on 200 randomly-sampled archived
#          s7-cam frames that the pipeline had already tiered `strong` (every
#          one of them provably contains a bird):
#
#              yolov8n  conf 0.25  ->  88.0%   yolov8s  conf 0.25  ->  93.5%
#              yolov8n  conf 0.10  ->  96.0%   yolov8s  conf 0.10  ->  98.0%
#              yolov8n  conf 0.03  ->  97.0%   yolov8s  conf 0.05  ->  99.5%  <-- default
#
#          Hence the defaults: yolov8s.pt at conf 0.05, ~16 ms/frame on MPS.
#          Re-run tools/pipeline/measure_presence_recall.py after any change to
#          the model, the threshold, or the camera's aim — the numbers above are
#          scene-specific and a re-aim can invalidate them.
#
#          CLASS SET: COCO has no "chicken". Chickens and turkeys land on
#          `bird`, but at this camera's angles they are also regularly called
#          `dog`, `cat`, `sheep`, and `teddy bear`. We accept any of them — this
#          is a presence gate, not a classifier, and the VLM downstream is what
#          actually decides what the frame contains.
#
#          NIGHT: the caller is expected to pass daylight_only=True and the
#          frame's exposure_p50 (already computed for free by quality_gate's
#          trivial gate). Below the floor the gate abstains and returns
#          "present" rather than skipping. This is deliberate — Boss's stated
#          reason for keeping YOLO out of this pipeline was that it goes
#          overactive at night, and s7-cam produced 0 gems outside 05:00-20:00
#          across 21 days, so there is nothing to win at night and a known
#          failure mode to avoid.
#
# SRP/DRY check: Pass — single responsibility is "does this frame contain an
#                animal, and where". Deliberately NOT reusing detect.py's
#                AnimalDetector: that class exists for security alerting and
#                layers on a consecutive-frame dwell filter, no-alert zones,
#                predator classification, and a bird_min_bbox_width_pct=8.0
#                size floor. Every one of those actively destroys recall for
#                gem hunting (a gem is often a single instantaneous frame, and
#                a small distant bird can still be the start of a cluster).
#                Reusing it would mean disabling most of what it does. The
#                shared piece — the ultralytics YOLO model itself — is loaded
#                here independently so the alerting detector's state is never
#                perturbed by pipeline traffic.

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger("pipeline.presence")

# COCO classes a chicken/turkey plausibly lands on at this camera's angles.
# Intentionally generous — see PURPOSE.
DEFAULT_ANIMAL_CLASSES = frozenset(
    {"bird", "dog", "cat", "sheep", "cow", "horse", "teddy bear"}
)

DEFAULT_MODEL = "yolov8s.pt"
DEFAULT_CONF = 0.05
# Below this median luminance we abstain rather than gate. quality_gate's
# exposure floor is 25.0; sitting just above it keeps the abstain band tight.
DEFAULT_MIN_EXPOSURE_P50 = 30.0


@dataclass
class Box:
    """One detected animal, in frame-relative terms."""
    class_name: str
    confidence: float
    area_pct: float          # % of total frame area
    cx: float                # centre x, 0..1
    cy: float                # centre y, 0..1


@dataclass
class PresenceResult:
    present: bool
    boxes: list[Box] = field(default_factory=list)
    abstained: bool = False   # True => too dark to judge; caller must NOT skip
    reason: str = ""

    @property
    def count(self) -> int:
        return len(self.boxes)

    @property
    def largest_area_pct(self) -> float:
        return max((b.area_pct for b in self.boxes), default=0.0)


class PresenceDetector:
    """Lazily-loaded YOLO presence gate. One model instance per process.

    The model load (~1 s) happens on first detect() rather than at import, so
    importing this module stays free for the --once / --retention-only paths
    and for any camera that never opts in.
    """

    def __init__(
        self,
        model_path: str = DEFAULT_MODEL,
        conf: float = DEFAULT_CONF,
        animal_classes: frozenset[str] | set[str] = DEFAULT_ANIMAL_CLASSES,
        min_exposure_p50: float = DEFAULT_MIN_EXPOSURE_P50,
    ):
        self._model_path = model_path
        self._conf = float(conf)
        self._classes = frozenset(animal_classes)
        self._min_exposure_p50 = float(min_exposure_p50)
        self._model = None
        self._device = "cpu"
        self._lock = threading.Lock()
        self._load_failed = False

    def _ensure_model(self) -> bool:
        """Load the model once. Returns False if it can't be loaded, in which
        case every subsequent detect() abstains — a missing model must degrade
        to 'always call the VLM' (today's behaviour), never to 'skip
        everything'."""
        if self._model is not None:
            return True
        if self._load_failed:
            return False
        with self._lock:
            if self._model is not None:
                return True
            if self._load_failed:
                return False
            try:
                import torch
                from ultralytics import YOLO

                # Resolve relative model paths against the repo root so the
                # daemon's WorkingDirectory isn't load-bearing.
                path = Path(self._model_path)
                if not path.is_absolute():
                    candidate = Path(__file__).resolve().parents[2] / path
                    if candidate.exists():
                        path = candidate
                model = YOLO(str(path))
                if torch.backends.mps.is_available():
                    self._device = "mps"
                self._model = model
                log.info(
                    "presence: loaded %s on %s (conf=%.2f, classes=%d)",
                    path, self._device, self._conf, len(self._classes),
                )
                return True
            except Exception:
                log.exception(
                    "presence: model load failed — gate will ABSTAIN "
                    "(every frame proceeds to the VLM, i.e. pre-gate behaviour)"
                )
                self._load_failed = True
                return False

    def detect(
        self,
        image_bgr: np.ndarray,
        exposure_p50: float | None = None,
        daylight_only: bool = True,
    ) -> PresenceResult:
        """Return whether this frame contains an animal.

        Never raises. Any failure — model missing, inference error, too dark —
        returns present=True with abstained=True, so the caller falls through
        to the VLM exactly as it did before this gate existed. The gate can
        only ever *save* work; it can never be the reason a frame is lost to
        an error.
        """
        if daylight_only and exposure_p50 is not None and exposure_p50 < self._min_exposure_p50:
            return PresenceResult(
                present=True, abstained=True,
                reason=f"too_dark_to_gate (p50={exposure_p50:.1f} < {self._min_exposure_p50})",
            )
        if not self._ensure_model():
            return PresenceResult(present=True, abstained=True, reason="model_unavailable")

        try:
            result = self._model.predict(
                image_bgr, verbose=False, device=self._device, conf=self._conf
            )[0]
        except Exception as exc:
            log.warning("presence: inference failed (%s) — abstaining", exc)
            return PresenceResult(present=True, abstained=True, reason="inference_error")

        height, width = image_bgr.shape[:2]
        frame_area = float(max(height * width, 1))
        boxes: list[Box] = []
        for raw in result.boxes:
            class_name = result.names.get(int(raw.cls[0]), "")
            if class_name not in self._classes:
                continue
            x1, y1, x2, y2 = (float(v) for v in raw.xyxy[0])
            boxes.append(
                Box(
                    class_name=class_name,
                    confidence=float(raw.conf[0]),
                    area_pct=100.0 * (x2 - x1) * (y2 - y1) / frame_area,
                    cx=(x1 + x2) / 2.0 / width,
                    cy=(y1 + y2) / 2.0 / height,
                )
            )
        return PresenceResult(
            present=bool(boxes),
            boxes=boxes,
            reason="" if boxes else "no_animal_detected",
        )


_SHARED: PresenceDetector | None = None
_SHARED_LOCK = threading.Lock()


def shared_detector(presence_cfg: dict | None = None) -> PresenceDetector:
    """Process-wide detector, built from config on first call. The orchestrator
    runs one VLM camera today, but the raw-capture threads are real threads —
    so this is guarded, and ultralytics inference itself is called under no
    lock because predict() is re-entrant for our read-only use."""
    global _SHARED
    if _SHARED is not None:
        return _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            cfg = presence_cfg or {}
            _SHARED = PresenceDetector(
                model_path=cfg.get("model", DEFAULT_MODEL),
                conf=float(cfg.get("conf", DEFAULT_CONF)),
                animal_classes=frozenset(
                    cfg.get("animal_classes", DEFAULT_ANIMAL_CLASSES)
                ),
                min_exposure_p50=float(
                    cfg.get("min_exposure_p50", DEFAULT_MIN_EXPOSURE_P50)
                ),
            )
    return _SHARED
