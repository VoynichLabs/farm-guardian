# Author: Claude Opus 4.6
# Date: 03-April-2026
# PURPOSE: GLM vision model species refinement for Farm Guardian v2. When YOLO detects
#          an ambiguous class ("bird", "cat", "dog"), this module crops the bounding box
#          region, encodes it as JPEG, and sends it to the locally-running GLM vision model
#          (zai-org/glm-4.6v-flash via LM Studio at 127.0.0.1:1234) for species-level
#          identification. Distinguishes hawk from chicken, bobcat from house cat, etc.
#          Caches results per track to avoid redundant queries for the same animal visit.
#          Falls back to the original YOLO class on timeout or error (3s default).
# SRP/DRY check: Pass — single responsibility is vision model species refinement.

import base64
import io
import logging
import time
from typing import Optional

import cv2
import numpy as np
import requests

log = logging.getLogger("guardian.vision")

# Species prompts per ambiguous YOLO class
_BIRD_PROMPT = (
    "You are a wildlife identification system on a chicken farm in eastern Connecticut. "
    "Look at this image and identify the bird species. "
    "Is this a: (a) chicken, (b) hawk/raptor, (c) small songbird, (d) other bird? "
    "Respond with ONLY the category letter and species name, e.g. 'b hawk' or 'a chicken'."
)

_CAT_PROMPT = (
    "You are a wildlife identification system on a farm in eastern Connecticut. "
    "Look at this image and identify the animal. "
    "Is this a: (a) domestic house cat, (b) bobcat, (c) other wild cat? "
    "Respond with ONLY the category letter and animal name, e.g. 'a house cat' or 'b bobcat'."
)

_DOG_PROMPT = (
    "You are a wildlife identification system on a farm in eastern Connecticut. "
    "Look at this image and identify the animal. "
    "Is this a: (a) small domestic dog, (b) coyote, (c) fox, (d) other canine? "
    "Respond with ONLY the category letter and animal name, e.g. 'a small dog' or 'c fox'."
)

# Map YOLO class to prompt and response parsing
_CLASS_PROMPTS = {
    "bird": _BIRD_PROMPT,
    "cat": _CAT_PROMPT,
    "dog": _DOG_PROMPT,
}

# Map vision model response letters to normalized class names
_BIRD_CLASSES = {
    "a": "chicken",
    "b": "hawk",
    "c": "small_bird",
    "d": "other_bird",
}

_CAT_CLASSES = {
    "a": "house_cat",
    "b": "bobcat",
    "c": "wild_cat",
}

_DOG_CLASSES = {
    "a": "small_dog",
    "b": "coyote",
    "c": "fox",
    "d": "other_canine",
}

_RESPONSE_MAPS = {
    "bird": _BIRD_CLASSES,
    "cat": _CAT_CLASSES,
    "dog": _DOG_CLASSES,
}


class VisionRefiner:
    """Refines ambiguous YOLO detections using a local GLM vision model."""

    def __init__(self, config: dict):
        vision_cfg = config.get("vision", {})
        self._enabled = vision_cfg.get("enabled", False)
        self._endpoint = vision_cfg.get(
            "endpoint", "http://127.0.0.1:1234/v1/chat/completions"
        )
        self._model = vision_cfg.get("model", "zai-org/glm-4.6v-flash")
        self._timeout = vision_cfg.get("timeout_seconds", 3)
        self._trigger_classes = set(vision_cfg.get("trigger_classes", ["bird", "cat", "dog"]))
        self._max_tokens = vision_cfg.get("max_tokens", 50)
        self._temperature = vision_cfg.get("temperature", 0.1)
        self._fallback_on_error = vision_cfg.get("fallback_on_error", True)

        # Per-track cache: {track_id: (refined_class, confidence)}
        self._track_cache: dict[int, tuple[str, float]] = {}

        if self._enabled:
            log.info(
                "VisionRefiner initialized — model=%s, endpoint=%s, timeout=%ds",
                self._model, self._endpoint, self._timeout,
            )
        else:
            log.info("VisionRefiner disabled — using YOLO classes only")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def should_refine(self, class_name: str) -> bool:
        """Check if this YOLO class should be sent to the vision model."""
        return self._enabled and class_name in self._trigger_classes

    def refine(
        self,
        class_name: str,
        frame: np.ndarray,
        bbox: tuple,
        track_id: Optional[int] = None,
    ) -> tuple[str, float, str]:
        """
        Refine a YOLO detection using the vision model.

        Returns:
            (refined_class, confidence, model_name) — on success, the refined species
            and a pseudo-confidence from the vision model. On failure, returns the
            original YOLO class with the original model name.
        """
        if not self._enabled:
            return class_name, 0.0, "yolov8n"

        # Check track cache — don't re-query the same animal in the same visit
        if track_id is not None and track_id in self._track_cache:
            cached_class, cached_conf = self._track_cache[track_id]
            log.debug("Vision cache hit for track %d: %s", track_id, cached_class)
            return cached_class, cached_conf, self._model

        # Get the prompt for this YOLO class
        prompt = _CLASS_PROMPTS.get(class_name)
        if not prompt:
            return class_name, 0.0, "yolov8n"

        # Crop and encode the detection region
        image_b64 = self._crop_and_encode(frame, bbox)
        if not image_b64:
            return class_name, 0.0, "yolov8n"

        # Query the vision model
        start = time.monotonic()
        try:
            response_text = self._query_vision_model(image_b64, prompt)
        except Exception as exc:
            elapsed = time.monotonic() - start
            log.warning(
                "Vision model query failed after %.1fs: %s — falling back to '%s'",
                elapsed, exc, class_name,
            )
            return class_name, 0.0, "yolov8n"

        elapsed = time.monotonic() - start
        refined_class = self._parse_response(response_text, class_name)

        # Use 0.85 as pseudo-confidence when vision model gives a clear answer,
        # 0.5 when it falls back to original class
        confidence = 0.85 if refined_class != class_name else 0.5

        log.info(
            "Vision refined '%s' → '%s' (%.1fs) [raw: %s]",
            class_name, refined_class, elapsed, response_text.strip()[:60],
        )

        # Cache for this track
        if track_id is not None:
            self._track_cache[track_id] = (refined_class, confidence)

        return refined_class, confidence, self._model

    def clear_track_cache(self, track_id: int) -> None:
        """Remove a closed track from the cache."""
        self._track_cache.pop(track_id, None)

    def _crop_and_encode(self, frame: np.ndarray, bbox: tuple) -> Optional[str]:
        """Crop the bounding box region and encode as base64 JPEG."""
        try:
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = bbox

            # Clamp to frame bounds
            x1 = max(0, int(x1))
            y1 = max(0, int(y1))
            x2 = min(w, int(x2))
            y2 = min(h, int(y2))

            if x2 <= x1 or y2 <= y1:
                log.warning("Invalid bbox for vision crop: %s", bbox)
                return None

            crop = frame[y1:y2, x1:x2]

            # Encode as JPEG
            success, buffer = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not success:
                log.warning("Failed to encode crop as JPEG")
                return None

            return base64.b64encode(buffer.tobytes()).decode("utf-8")

        except Exception as exc:
            log.error("Crop/encode failed: %s", exc)
            return None

    def _query_vision_model(self, image_b64: str, prompt: str) -> str:
        """Send a vision query to LM Studio. Returns the model's text response."""
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}"
                            },
                        },
                    ],
                }
            ],
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
        }

        resp = requests.post(
            self._endpoint,
            json=payload,
            timeout=self._timeout,
        )
        resp.raise_for_status()

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise ValueError("Vision model returned empty choices")

        return choices[0].get("message", {}).get("content", "")

    def _parse_response(self, response_text: str, original_class: str) -> str:
        """
        Parse the vision model's response into a normalized class name.
        Falls back to the original YOLO class if parsing fails.
        """
        text = response_text.strip().lower()
        response_map = _RESPONSE_MAPS.get(original_class, {})

        # Try to find a category letter at the start: "b hawk", "a chicken", etc.
        if text and text[0] in response_map:
            return response_map[text[0]]

        # Try keyword matching as fallback
        if original_class == "bird":
            if "hawk" in text or "raptor" in text or "falcon" in text or "eagle" in text:
                return "hawk"
            if "chicken" in text or "hen" in text or "rooster" in text:
                return "chicken"
            if "songbird" in text or "sparrow" in text or "robin" in text:
                return "small_bird"
        elif original_class == "cat":
            if "bobcat" in text:
                return "bobcat"
            if "house" in text or "domestic" in text:
                return "house_cat"
        elif original_class == "dog":
            if "coyote" in text:
                return "coyote"
            if "fox" in text:
                return "fox"
            if "domestic" in text or "small dog" in text:
                return "small_dog"

        # Could not parse — return original
        log.debug("Could not parse vision response '%s' — keeping '%s'", text[:80], original_class)
        return original_class
