# Author: Claude Opus 4.6 (1M context)
# Date: 13-April-2026
# PURPOSE: Send a captured JPEG to LM Studio's glm-4.6v-flash with a
#          structured-JSON prompt; return the parsed+validated metadata dict.
#          Enforces the LM Studio safety rules from
#          docs/13-Apr-2026-lm-studio-reference.md:
#            - verify the right model is loaded before any /v1/chat/completions
#            - never auto-load via chat endpoint (skip cycle instead)
#            - single in-flight via a module-level threading.Lock
#            - always pass context_length on load (caller's responsibility)
# SRP/DRY check: Pass — single responsibility is VLM round-trip. No capture,
#                no archive, no DB.

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("pipeline.vlm_enricher")

_VLM_LOCK = threading.Lock()  # single in-flight per process


class EnricherError(Exception):
    pass


class ModelNotLoaded(EnricherError):
    pass


class ValidationFailed(EnricherError):
    pass


_REQUIRED_KEYS = {
    "scene", "bird_count", "individuals_visible", "any_special_chick",
    "apparent_age_days", "activity", "lighting", "composition",
    "image_quality", "bird_face_visible", "share_worth", "share_reason",
    "caption_draft", "concerns",
}


def list_loaded_models(lm_base: str, timeout: int = 5) -> list[str]:
    r = requests.get(f"{lm_base}/v1/models", timeout=timeout)
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", [])]


def prompt_for(camera_name: str, camera_context: str, prompt_template: str) -> str:
    from datetime import date
    return (prompt_template
            .replace("{camera_name}", camera_name)
            .replace("{camera_context}", camera_context)
            .replace("{today}", date.today().isoformat()))


def _validate_response(obj: dict) -> None:
    missing = _REQUIRED_KEYS - set(obj.keys())
    if missing:
        raise ValidationFailed(f"missing keys: {missing}")
    if not isinstance(obj["bird_count"], int) or obj["bird_count"] < 0:
        raise ValidationFailed(f"bird_count invalid: {obj['bird_count']!r}")
    if not isinstance(obj["individuals_visible"], list):
        raise ValidationFailed("individuals_visible must be list")
    if not isinstance(obj["concerns"], list):
        raise ValidationFailed("concerns must be list")
    for field in ["scene", "activity", "lighting", "composition",
                  "image_quality", "share_worth", "share_reason",
                  "caption_draft"]:
        if not isinstance(obj[field], str):
            raise ValidationFailed(f"{field} must be string")
    if not isinstance(obj["any_special_chick"], bool):
        raise ValidationFailed("any_special_chick must be bool")
    if not isinstance(obj["bird_face_visible"], bool):
        raise ValidationFailed("bird_face_visible must be bool")
    age = obj["apparent_age_days"]
    if not (isinstance(age, int) and -1 <= age <= 365):
        raise ValidationFailed(f"apparent_age_days invalid: {age!r}")


def enrich(
    image_bytes: bytes,
    camera_name: str,
    camera_context: str,
    lm_base: str,
    model_id: str,
    prompt_template: str,
    schema: dict,
    max_tokens: int = 600,
    temperature: float = 0.2,
    timeout: int = 120,
) -> dict:
    """Single VLM round-trip. Raises ModelNotLoaded if the wrong model (or
    nothing) is loaded — caller should skip the cycle rather than auto-load
    to avoid contention with G0DM0D3 sweeps. Returns a validated metadata
    dict plus meta fields (inference_ms, prompt_hash, raw_response)."""
    loaded = list_loaded_models(lm_base)
    if model_id not in loaded:
        raise ModelNotLoaded(f"want {model_id!r}, loaded: {loaded!r}")

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"
    prompt_text = prompt_for(camera_name, camera_context, prompt_template)
    prompt_hash = "sha256:" + hashlib.sha256(prompt_text.encode()).hexdigest()[:16]

    # LM Studio only accepts response_format.type = 'json_schema' or 'text'
    # on this build. Use json_schema; we've normalized the schema to avoid
    # union types (apparent_age_days uses -1 instead of null for "n/a") so
    # all branches pass strict validation.
    body = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": {"type": "json_schema", "json_schema": schema},
    }

    with _VLM_LOCK:
        t0 = time.monotonic()
        try:
            r = requests.post(f"{lm_base}/v1/chat/completions", json=body, timeout=timeout)
            r.raise_for_status()
        except requests.HTTPError as e:
            body_snippet = ""
            try:
                body_snippet = (e.response.text or "")[:800]
            except Exception:
                pass
            raise EnricherError(f"LM Studio request failed: {e} | body: {body_snippet}") from e
        except requests.RequestException as e:
            raise EnricherError(f"LM Studio request failed: {e}") from e
        inference_ms = int((time.monotonic() - t0) * 1000)

    payload = r.json()
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise EnricherError(f"unexpected LM Studio response: {payload!r}") from e

    # Strip any markdown fencing the model might have added despite instructions
    content_stripped = content.strip()
    if content_stripped.startswith("```"):
        lines = content_stripped.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content_stripped = "\n".join(lines)

    try:
        obj = json.loads(content_stripped)
    except json.JSONDecodeError as e:
        raise ValidationFailed(f"response not valid JSON: {content!r}") from e
    _validate_response(obj)

    return {
        "metadata": obj,
        "inference_ms": inference_ms,
        "prompt_hash": prompt_hash,
        "raw_response": content,
    }


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    cfg_path = Path(__file__).parent / "config.json"
    schema_path = Path(__file__).parent / "schema.json"
    prompt_path = Path(__file__).parent / "prompt.md"
    cfg = json.loads(cfg_path.read_text())
    schema = json.loads(schema_path.read_text())
    prompt_template = prompt_path.read_text()

    image_path = sys.argv[1]
    camera_name = sys.argv[2] if len(sys.argv) > 2 else "usb-cam"
    cam_cfg = cfg["cameras"][camera_name]
    image_bytes = Path(image_path).read_bytes()

    result = enrich(
        image_bytes=image_bytes,
        camera_name=camera_name,
        camera_context=cam_cfg["context"],
        lm_base=cfg["lm_studio_base"],
        model_id=cfg["vlm_model_id"],
        prompt_template=prompt_template,
        schema=schema,
        max_tokens=cfg["vlm_max_tokens"],
        temperature=cfg["vlm_temperature"],
        timeout=cfg["vlm_timeout_seconds"],
    )
    print(f"inference_ms={result['inference_ms']}")
    print(json.dumps(result["metadata"], indent=2))
