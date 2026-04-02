# Author: Cascade (Claude Sonnet 4)
# Date: 01-April-2026
# PURPOSE: YOLOv8 animal detection for Farm Guardian. Loads a YOLOv8 model (nano by default)
#          and runs inference on frames captured from RTSP streams. Implements the v1
#          false-positive suppression strategy from PLAN.md:
#          - Size filter: bird class requires minimum bounding box area (8% of frame width)
#          - Zone masking: configurable polygon no-alert zone (e.g. coop area)
#          - Minimum dwell time: animal must appear in 3+ consecutive frames before alerting
#          - Per-class confidence thresholds (default 0.45)
#          Uses MPS (Metal Performance Shaders) on Apple Silicon for fast inference.
# SRP/DRY check: Pass — single responsibility is frame analysis and detection filtering.

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from ultralytics import YOLO

log = logging.getLogger("guardian.detect")


@dataclass
class Detection:
    """A single filtered detection result."""
    class_name: str
    confidence: float
    bbox: tuple  # (x1, y1, x2, y2)
    is_predator: bool
    bbox_area_pct: float  # bounding box area as % of frame area
    frame_count: int  # how many consecutive frames this class has been seen


@dataclass
class DetectionResult:
    """All detections from a single frame, after filtering."""
    camera_name: str
    timestamp: float
    detections: list[Detection] = field(default_factory=list)
    frame: Optional[np.ndarray] = None  # the analyzed frame (for snapshots)

    @property
    def has_predators(self) -> bool:
        return any(d.is_predator for d in self.detections)

    @property
    def predator_detections(self) -> list[Detection]:
        return [d for d in self.detections if d.is_predator]


class AnimalDetector:
    """YOLOv8-based animal detector with false-positive suppression."""

    def __init__(self, config: dict):
        detection_cfg = config.get("detection", {})

        # Model setup
        model_path = detection_cfg.get("model", "yolov8n.pt")
        self._model = self._load_model(model_path)

        # Classification config
        self._predator_classes = set(detection_cfg.get("predator_classes", ["bird", "cat", "dog", "bear"]))
        self._ignore_classes = set(detection_cfg.get("ignore_classes", ["person", "car", "truck", "bicycle"]))

        # Per-class confidence thresholds — fall back to global default
        self._default_confidence = detection_cfg.get("confidence_threshold", 0.45)
        self._class_thresholds = detection_cfg.get("class_confidence_thresholds", {})

        # Size filter: minimum bbox width as percentage of frame width (bird class only)
        self._bird_min_bbox_pct = detection_cfg.get("bird_min_bbox_width_pct", 8.0)

        # Zone masking: polygon defining the no-alert zone (list of [x%, y%] points as % of frame)
        zone_points = detection_cfg.get("no_alert_zone", [])
        self._no_alert_zone = np.array(zone_points, dtype=np.float32) if zone_points else None

        # Dwell time: minimum consecutive frames before an alert fires
        self._min_dwell_frames = detection_cfg.get("min_dwell_frames", 3)

        # Track consecutive detections per camera per class
        # Key: (camera_name, class_name) -> count of consecutive frames
        self._dwell_tracker: dict[tuple[str, str], int] = defaultdict(int)
        # Track which classes were seen in the *previous* frame per camera
        self._prev_frame_classes: dict[str, set[str]] = defaultdict(set)

        log.info(
            "AnimalDetector initialized — model=%s, predators=%s, confidence=%.2f, "
            "bird_min_bbox=%.1f%%, dwell=%d frames",
            model_path,
            self._predator_classes,
            self._default_confidence,
            self._bird_min_bbox_pct,
            self._min_dwell_frames,
        )

    def _load_model(self, model_path: str) -> YOLO:
        """Load YOLOv8 model. Downloads if not present. Uses MPS on Apple Silicon."""
        log.info("Loading YOLO model: %s", model_path)
        model = YOLO(model_path)

        # Attempt MPS (Apple Silicon GPU), fall back to CPU
        try:
            model.to("mps")
            log.info("YOLO model using MPS (Apple Silicon GPU)")
        except Exception:
            log.info("MPS not available — YOLO model using CPU")

        return model

    def detect(self, frame: np.ndarray, camera_name: str) -> DetectionResult:
        """
        Run YOLO inference on a frame and apply all v1 suppression filters.
        Returns filtered detections with dwell tracking applied.
        """
        timestamp = time.time()
        h, w = frame.shape[:2]
        frame_area = h * w

        # Run YOLO inference — verbose=False suppresses per-frame console output
        try:
            results = self._model(frame, verbose=False)
        except Exception as exc:
            log.error("YOLO inference failed on '%s': %s — skipping frame", camera_name, exc)
            return DetectionResult(camera_name=camera_name, timestamp=timestamp, frame=frame)

        # Parse raw detections
        raw_detections = []
        if results and len(results) > 0:
            result = results[0]
            for box in result.boxes:
                class_id = int(box.cls[0])
                class_name = result.names.get(class_id, f"class_{class_id}")
                confidence = float(box.conf[0])
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                raw_detections.append((class_name, confidence, (x1, y1, x2, y2)))

        # Apply filters and build final detection list
        classes_seen_this_frame: set[str] = set()
        filtered: list[Detection] = []

        for class_name, confidence, bbox in raw_detections:
            # 1. Skip ignored classes
            if class_name in self._ignore_classes:
                continue

            # 2. Confidence threshold (per-class or global)
            min_conf = self._class_thresholds.get(class_name, self._default_confidence)
            if confidence < min_conf:
                continue

            x1, y1, x2, y2 = bbox
            bbox_w = x2 - x1
            bbox_h = y2 - y1
            bbox_area = bbox_w * bbox_h
            bbox_area_pct = (bbox_area / frame_area) * 100 if frame_area > 0 else 0
            bbox_w_pct = (bbox_w / w) * 100 if w > 0 else 0

            # 3. Size filter: bird class must meet minimum bbox width threshold
            if class_name == "bird" and bbox_w_pct < self._bird_min_bbox_pct:
                log.debug(
                    "Bird filtered out — bbox width %.1f%% < threshold %.1f%%",
                    bbox_w_pct, self._bird_min_bbox_pct,
                )
                continue

            # 4. Zone masking: suppress if bbox center falls inside no-alert zone
            if self._no_alert_zone is not None:
                cx = (x1 + x2) / 2
                cy = (y1 + y2) / 2
                # Convert pixel coords to percentage of frame for zone comparison
                cx_pct = (cx / w) * 100
                cy_pct = (cy / h) * 100
                if self._point_in_polygon(cx_pct, cy_pct, self._no_alert_zone):
                    log.debug("Detection '%s' suppressed — inside no-alert zone", class_name)
                    continue

            classes_seen_this_frame.add(class_name)
            is_predator = class_name in self._predator_classes

            # 5. Dwell time tracking — count consecutive frames
            key = (camera_name, class_name)
            # This will be incremented below after we update dwell tracker
            current_dwell = self._dwell_tracker[key] + 1

            filtered.append(Detection(
                class_name=class_name,
                confidence=confidence,
                bbox=(x1, y1, x2, y2),
                is_predator=is_predator,
                bbox_area_pct=round(bbox_area_pct, 2),
                frame_count=current_dwell,
            ))

        # Update dwell tracker: increment classes seen, reset classes not seen
        prev_classes = self._prev_frame_classes.get(camera_name, set())
        for cls in classes_seen_this_frame:
            self._dwell_tracker[(camera_name, cls)] += 1
        for cls in prev_classes - classes_seen_this_frame:
            self._dwell_tracker[(camera_name, cls)] = 0
        self._prev_frame_classes[camera_name] = classes_seen_this_frame

        # Mark predator detections that haven't met dwell threshold as non-alertable
        # by clearing their is_predator flag
        for det in filtered:
            if det.is_predator and det.frame_count < self._min_dwell_frames:
                log.debug(
                    "Predator '%s' on '%s' — dwell %d/%d (not yet alertable)",
                    det.class_name, camera_name, det.frame_count, self._min_dwell_frames,
                )
                det.is_predator = False

        return DetectionResult(
            camera_name=camera_name,
            timestamp=timestamp,
            detections=filtered,
            frame=frame,
        )

    @staticmethod
    def _point_in_polygon(px: float, py: float, polygon: np.ndarray) -> bool:
        """
        Ray-casting algorithm to test if point (px, py) is inside a polygon.
        Polygon is an Nx2 array of (x, y) vertices.
        """
        n = len(polygon)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = polygon[i]
            xj, yj = polygon[j]
            if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
                inside = not inside
            j = i
        return inside
