# Author: Claude Opus 4.7 (1M context)
# Date: 20-April-2026 (v2.30.0 — OpenAI-compat + response_format grammar sampling)
# PURPOSE: Send a captured JPEG to LM Studio's currently-loaded VLM and return
#          a schema-conforming metadata dict.
#
#          v2.30.0 rewrite: switched from /api/v1/chat back to /v1/chat/completions
#          because LM Studio now enforces JSON Schema server-side via
#          `response_format: {type: "json_schema", ...}` — grammar sampling
#          guarantees the model cannot emit anything that doesn't match our
#          schema. That lets us delete:
#            - _build_enum_summary() + the OUTPUT FORMAT appendix (server
#              enforces enums)
#            - Most of _validate_response() (server enforces shape + enums)
#            - The "markdown fence stripping" in _strip_markdown_fences
#              (can't happen — schema enforcement prevents it)
#
#          Result: user prompt is just camera context + field-judgment
#          rubrics. No output-format instructions. The schema itself is
#          the contract.
#
#          Cost: none observed. `reasoning: "off"` is honored on the
#          OpenAI-compat endpoint for the currently-tested models (qwen
#          3.5 35B-A3B verified 2026-04-20 at 4.5s/call with reasoning
#          off + vision + response_format; Gemma-4-31b on the old native
#          path was ~38s/call). Models that don't honor `reasoning: "off"`
#          on OpenAI-compat will just send a reasoning block we ignore —
#          correctness unaffected.
#
#          LM Studio safety rules from docs/13-Apr-2026-lm-studio-reference.md
#          still apply:
#            - verify the right model is loaded before any call
#            - never auto-load via the chat endpoint (skip cycle instead)
#            - single in-flight via module-level threading.Lock
# SRP/DRY check: Pass — single responsibility is VLM round-trip.

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
from pathlib import Path

import requests

log = logging.getLogger("pipeline.vlm_enricher")

_VLM_LOCK = threading.Lock()  # single in-flight per process


class EnricherError(Exception):
    pass


class ModelNotLoaded(EnricherError):
    pass


class ValidationFailed(EnricherError):
    pass


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


_SYSTEM_PROMPT = (
    "You are a vision assistant for a small backyard-chicken farm camera pipeline. "
    "For every image, return exactly one JSON object that matches the schema. "
    "Be factual — describe only what is visible. Do not dramatize, narrate, or "
    "interpret mood."
)


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
    """Single VLM round-trip via LM Studio's /v1/chat/completions endpoint
    with response_format=json_schema grammar sampling.

    Raises ModelNotLoaded if the wrong model (or nothing) is loaded — caller
    should skip the cycle rather than auto-load. Returns a schema-conforming
    metadata dict plus meta fields (inference_ms, prompt_hash, raw_response,
    reasoning_output_tokens).

    context_length is accepted for API compatibility but ignored — the
    OpenAI-compat endpoint uses the model's loaded context length. The
    daemon configures context at load time via vlm_load_context_length.
    """
    del context_length  # accepted for API compat; see docstring
    loaded = list_loaded_models(lm_base)
    if model_id not in loaded:
        raise ModelNotLoaded(f"want {model_id!r}, loaded: {loaded!r}")

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"
    user_prompt = prompt_for(camera_name, camera_context, prompt_template)
    prompt_hash = "sha256:" + hashlib.sha256(
        (_SYSTEM_PROMPT + "\n" + user_prompt).encode()
    ).hexdigest()[:16]

    body = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        # `reasoning: off` is honored on OpenAI-compat for Gemma-4 and
        # Qwen3.5 per 2026-04-20 live test. Models that don't honor it
        # emit a reasoning block we ignore — correctness unaffected.
        "reasoning": "off",
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema.get("name", "farm_image_metadata"),
                "strict": "true",
                "schema": schema["schema"],
            },
        },
    }

    with _VLM_LOCK:
        t0 = time.monotonic()
        try:
            r = requests.post(
                f"{lm_base}/v1/chat/completions", json=body, timeout=timeout
            )
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
    try:
        msg = payload["choices"][0]["message"]
        content = msg.get("content") or ""
    except (KeyError, IndexError, TypeError) as e:
        raise EnricherError(f"unexpected LM Studio response: {payload!r}") from e

    # reasoning_output_tokens: if the model emitted a reasoning block
    # despite reasoning=off, account for it in the stats field. Not all
    # models surface this the same way.
    reasoning_tokens = 0
    usage = payload.get("usage", {}) or {}
    if isinstance(usage.get("completion_tokens_details"), dict):
        reasoning_tokens = int(
            usage["completion_tokens_details"].get("reasoning_tokens", 0) or 0
        )

    # Schema enforcement is server-side (response_format grammar sampling);
    # content is guaranteed to parse as the schema. A bare try/except here
    # catches the one-in-a-blue-moon case where the server returns a
    # malformed payload anyway.
    try:
        obj = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValidationFailed(
            f"response_format did not return valid JSON: {content!r}"
        ) from e

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
