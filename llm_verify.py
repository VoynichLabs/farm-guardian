# Author: Claude Sonnet 4.6 (Bubba)
# Date: 23-July-2026
# PURPOSE: LLM second-opinion verifier for borderline YOLO detections. Sends a cropped
#          frame to OpenAI vision API and asks if the detection is real before firing an alert.
#          Fail-open: on any API/timeout error, returns True so real threats never get silently dropped.
# SRP/DRY check: Pass — single responsibility is LLM verification of a single detection frame.

import base64
import logging
import os

import cv2
import numpy as np
import requests

log = logging.getLogger("guardian.llm_verify")

# Default provider is OpenAI (gpt-4o-mini). The endpoint and key env var are
# overridable from config so the same code can front any OpenAI-compatible vision
# API (e.g. OpenRouter's openai/gpt-4o-mini) without touching this file.
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_TIMEOUT_S = 8


def _crop_with_padding(frame: np.ndarray, bbox, pad_pct: float = 0.20):
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in bbox)
    bw, bh = x2 - x1, y2 - y1
    px, py = int(bw * pad_pct), int(bh * pad_pct)
    x1 = max(0, x1 - px)
    y1 = max(0, y1 - py)
    x2 = min(w, x2 + px)
    y2 = min(h, y2 + py)
    if x2 <= x1 or y2 <= y1:
        return None
    return frame[y1:y2, x1:x2]


def verify_detection(
    frame: np.ndarray,
    class_name: str,
    confidence: float,
    bbox,
    model: str = "gpt-4o-mini",
    api_base: str = _OPENAI_URL,
    api_key_env: str = "OPENAI_API_KEY",
) -> bool:
    """Ask a vision LLM whether `class_name` is really present in the bbox crop.

    Returns True to fire the alert, False to suppress. Blocks up to `_TIMEOUT_S`
    seconds. Fail-open: any missing key, timeout, or API error returns True.
    """
    api_key = os.environ.get(api_key_env)
    if not api_key:
        log.warning("LLM verify: %s not set — failing open (alert fires) for %s", api_key_env, class_name)
        return True

    try:
        crop = _crop_with_padding(frame, bbox)
        if crop is None or crop.size == 0:
            log.warning("LLM verify: empty crop for %s — failing open", class_name)
            return True

        ok, jpeg = cv2.imencode(".jpg", crop)
        if not ok:
            log.warning("LLM verify: JPEG encode failed for %s — failing open", class_name)
            return True

        b64 = base64.b64encode(jpeg.tobytes()).decode("ascii")
        prompt = (
            f"You are a farm security camera reviewer. Is there actually a {class_name} "
            f"visible in this image? This is an IR/CCTV camera frame. Answer only YES or NO."
        )
        payload = {
            "model": model,
            "max_tokens": 3,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                    ],
                }
            ],
        }
        resp = requests.post(
            api_base,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=_TIMEOUT_S,
        )
        resp.raise_for_status()
        answer = resp.json()["choices"][0]["message"]["content"].strip().upper()
        confirmed = answer.startswith("Y")
        log.info(
            "LLM verify: %s @ %.2f -> model said '%s' -> %s",
            class_name, confidence, answer, "CONFIRM" if confirmed else "SUPPRESS",
        )
        return confirmed

    except requests.Timeout:
        log.warning("LLM verify: timeout after %ss for %s — failing open", _TIMEOUT_S, class_name)
        return True
    except Exception as exc:
        log.warning("LLM verify: error for %s (%s) — failing open", class_name, exc)
        return True
