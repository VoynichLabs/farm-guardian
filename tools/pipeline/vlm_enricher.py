# Author: Claude Opus 4.7 (1M context)
# Date: 16-April-2026 (v2.28.8 — native endpoint + reasoning off + enum validation)
# PURPOSE: Send a captured JPEG to LM Studio's currently-loaded VLM with a
#          structured-JSON prompt; return the parsed+validated metadata dict.
#
#          v2.28.8 change: switched from the OpenAI-compat /v1/chat/completions
#          endpoint to LM Studio's native /api/v1/chat. Two reasons:
#            1. /api/v1/chat accepts `reasoning: "off"` directly and honors it
#               on Gemma-4 models, where the OpenAI-compat endpoint's
#               reasoning_effort param is ignored on Gemma (LM Studio bug
#               #1743). With reasoning off, Gemma-4-31b goes from ~49-150s
#               per cycle to ~38s; a ~20% win, plus eliminates the "thinking
#               eats the max_tokens budget" failure where the JSON never
#               emits because all tokens got spent on reasoning.
#            2. /api/v1/chat is the recommended path per LM Studio v0.4.0+.
#
#          Cost: the native endpoint does NOT support `response_format:
#          json_schema` (GGUF/MLX grammar-sampling is only wired into the
#          OpenAI-compat path). So we're back to prompt-driven JSON output
#          and Python-side validation. The validator was hardened in this
#          version to enforce enum values from schema.json — strict on all
#          enum fields, so drift like "coop-run" vs "coop" or "close-up"
#          vs "portrait" raises ValidationFailed and the orchestrator
#          skips the cycle rather than storing bad data.
#
#          LM Studio safety rules from docs/13-Apr-2026-lm-studio-reference.md
#          still apply:
#            - verify the right model is loaded before any call
#            - never auto-load via the chat endpoint (skip cycle instead)
#            - single in-flight via module-level threading.Lock
# SRP/DRY check: Pass — single responsibility is VLM round-trip + validation.

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


def _build_enum_summary(schema: dict) -> str:
    """Produce a compact 'allowed-values' bullet list for every enum field
    in the JSON schema. Appended to the prompt so the VLM has the schema's
    enum vocabulary right next to the instructions — without this, Gemma-4
    drifts to plausible-but-invalid values like 'coop-run' or 'close-up'.
    Single source of truth = schema.json; this function renders it."""
    props = schema.get("schema", {}).get("properties", {})
    lines = []
    for key, spec in props.items():
        enum = spec.get("enum")
        if enum is None and spec.get("type") == "array":
            items = spec.get("items", {})
            if "enum" in items:
                lines.append(f"- {key}: array of any of [{', '.join(items['enum'])}]")
                continue
        if enum is not None:
            lines.append(f"- {key}: exactly one of [{', '.join(enum)}]")
    return "\n".join(lines)


def _validate_response(obj: dict, schema: Optional[dict] = None) -> None:
    """Structural + enum validation. Called on the parsed JSON before we
    commit it to the archive. Strict on enums — any drift fails the cycle.

    Pre-enum checks: required keys present, core types right.
    Enum checks: run only if the caller passed `schema`, so legacy callers
    (the __main__ smoke test) still work with just structural checks."""
    missing = _REQUIRED_KEYS - set(obj.keys())
    if missing:
        raise ValidationFailed(f"missing keys: {missing}")
    if not isinstance(obj["bird_count"], int) or obj["bird_count"] < 0:
        raise ValidationFailed(f"bird_count invalid: {obj['bird_count']!r}")
    if not isinstance(obj["individuals_visible"], list):
        raise ValidationFailed("individuals_visible must be list")
    if not isinstance(obj["concerns"], list):
        raise ValidationFailed("concerns must be list")
    # Coerce null → "" for optional-feeling string fields. Gemma-4 under
    # reasoning=off occasionally emits null/None for share_reason when it
    # has nothing to say. That's a Python type mismatch, but semantically
    # identical to an empty string — no need to waste a whole cycle.
    for field in ["share_reason", "caption_draft"]:
        if obj.get(field) is None:
            obj[field] = ""
    for field in ["scene", "activity", "lighting", "composition",
                  "image_quality", "share_worth", "share_reason",
                  "caption_draft"]:
        if not isinstance(obj[field], str):
            raise ValidationFailed(f"{field} must be string (got {type(obj[field]).__name__})")
    if not isinstance(obj["any_special_chick"], bool):
        raise ValidationFailed("any_special_chick must be bool")
    if not isinstance(obj["bird_face_visible"], bool):
        raise ValidationFailed("bird_face_visible must be bool")
    age = obj["apparent_age_days"]
    if not (isinstance(age, int) and -1 <= age <= 365):
        raise ValidationFailed(f"apparent_age_days invalid: {age!r}")

    if schema is None:
        return

    props = schema.get("schema", {}).get("properties", {})
    for key, spec in props.items():
        if key not in obj:
            continue
        if "enum" in spec and obj[key] not in spec["enum"]:
            raise ValidationFailed(
                f"{key}={obj[key]!r} not in {spec['enum']}")
        if spec.get("type") == "array" and "enum" in spec.get("items", {}):
            bad = [v for v in obj[key] if v not in spec["items"]["enum"]]
            if bad:
                raise ValidationFailed(
                    f"{key} contains out-of-enum values: {bad}")


def _strip_markdown_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    return s


def enrich(
    image_bytes: bytes,
    camera_name: str,
    camera_context: str,
    lm_base: str,
    model_id: str,
    prompt_template: str,
    schema: dict,
    max_tokens: int = 700,
    temperature: float = 0.2,
    timeout: int = 180,
    context_length: int = 8192,
) -> dict:
    """Single VLM round-trip via LM Studio's native /api/v1/chat endpoint.

    Raises ModelNotLoaded if the wrong model (or nothing) is loaded — caller
    should skip the cycle rather than auto-load. Returns a validated metadata
    dict plus meta fields (inference_ms, prompt_hash, raw_response,
    reasoning_output_tokens)."""
    loaded = list_loaded_models(lm_base)
    if model_id not in loaded:
        raise ModelNotLoaded(f"want {model_id!r}, loaded: {loaded!r}")

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"
    prompt_text = prompt_for(camera_name, camera_context, prompt_template)
    enum_summary = _build_enum_summary(schema)
    required_keys = ", ".join(sorted(_REQUIRED_KEYS))
    # Single-source-of-truth enum appendix: built from schema.json every
    # call, so editing schema.json is the only place enums need to change.
    structured_suffix = (
        "\n\n---\n"
        "OUTPUT FORMAT — read carefully.\n"
        "Return ONE valid JSON object and nothing else. No markdown fences, "
        "no preamble, no commentary. The JSON object MUST contain exactly "
        f"these keys: {required_keys}. No other keys.\n\n"
        "Every enum-valued field MUST use one of the listed values VERBATIM "
        "(case-sensitive, no rewording, no synonyms):\n"
        f"{enum_summary}\n\n"
        "If none of the listed values fits, pick the closest one. Never "
        "invent a new value."
    )
    full_prompt = prompt_text + structured_suffix
    prompt_hash = "sha256:" + hashlib.sha256(full_prompt.encode()).hexdigest()[:16]

    body = {
        "model": model_id,
        "reasoning": "off",
        "temperature": temperature,
        "max_output_tokens": max_tokens,
        "context_length": context_length,
        "system_prompt": (
            "You output exactly one JSON object that conforms to the user's "
            "instructions. No prose, no markdown fences, no explanations. "
            "Enum values are copied verbatim from the user's 'OUTPUT FORMAT' "
            "appendix — never invent new values."
        ),
        "input": [
            {"type": "image", "data_url": data_url},
            {"type": "text", "content": full_prompt},
        ],
    }

    with _VLM_LOCK:
        t0 = time.monotonic()
        try:
            r = requests.post(f"{lm_base}/api/v1/chat", json=body, timeout=timeout)
            r.raise_for_status()
        except requests.HTTPError as e:
            snippet = ""
            try:
                snippet = (e.response.text or "")[:800]
            except Exception:
                pass
            raise EnricherError(f"LM Studio request failed: {e} | body: {snippet}") from e
        except requests.RequestException as e:
            raise EnricherError(f"LM Studio request failed: {e}") from e
        inference_ms = int((time.monotonic() - t0) * 1000)

    payload = r.json()
    stats = payload.get("stats", {}) or {}
    reasoning_tokens = int(stats.get("reasoning_output_tokens", 0) or 0)
    try:
        outputs = payload["output"]
        # Find the first 'message' output (LM Studio may also emit tool /
        # reasoning blocks ahead of it; we only care about the assistant text).
        content = None
        for out in outputs:
            if out.get("type") == "message":
                content = out.get("content", "")
                break
        if content is None:
            raise EnricherError(f"no message output in LM Studio response: {payload!r}")
    except (KeyError, IndexError, TypeError) as e:
        raise EnricherError(f"unexpected LM Studio response: {payload!r}") from e

    content_stripped = _strip_markdown_fences(content)
    try:
        obj = json.loads(content_stripped)
    except json.JSONDecodeError as e:
        raise ValidationFailed(f"response not valid JSON: {content!r}") from e
    _validate_response(obj, schema=schema)

    return {
        "metadata": obj,
        "inference_ms": inference_ms,
        "prompt_hash": prompt_hash,
        "raw_response": content,
        "reasoning_output_tokens": reasoning_tokens,
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
        max_tokens=cfg.get("vlm_max_tokens", 700),
        temperature=cfg.get("vlm_temperature", 0.2),
        timeout=cfg.get("vlm_timeout_seconds", 180),
        context_length=cfg.get("vlm_load_context_length", 8192),
    )
    print(f"inference_ms={result['inference_ms']} reasoning_tokens={result['reasoning_output_tokens']}")
    print(json.dumps(result["metadata"], indent=2))
