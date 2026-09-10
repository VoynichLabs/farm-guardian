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
#            - only ever call a model LM Studio already has loaded
#              (resolve_loaded_vlm, v2.72.0)
#            - never auto-load via the chat endpoint (skip the cycle when
#              no vision model is loaded at all)
#            - single in-flight via module-level threading.Lock
#
#          03-June-2026 (Claude Opus 4.8, 1M context): added
#          ensure_model_loaded() — the controlled startup-only load path the
#          orchestrator calls once so the model is up at the configured
#          context (16k) after a reboot/LM Studio restart. Checks what's
#          loaded first, never stacks, parallel=1 + flash_attention per
#          docs/13-Apr-2026-lm-studio-reference.md.
#          08-Sep-2026 (Claude Sonnet 5, Bubba): added resolve_loaded_vlm() —
#          enrich() now runs with whatever vision-capable model is actually
#          loaded instead of hard-skipping every frame when a different job
#          (bench run, ad-hoc chat) has LM Studio's slot. Per Boss after
#          qwen3.8-27b sat loaded all day and zero of ~14k frames got scored,
#          starving the carousel of candidates. vlm_model_id is now a
#          preference, not a hard requirement.
#          10-Sep-2026 (Claude Opus 5, v2.72.0): finished that change.
#          Model state now comes from ONE reader, _loaded_instances(), on
#          the native /api/v1/models API. It decides vision support from
#          `capabilities.vision`, NOT from v0's `type == "vlm"`: v1
#          reports `type: "llm"` for every model, and v0's label for the
#          qwen3_5-arch models Boss swaps in (qwen3.8-27b) was never checked.
#          The resolver returns the loaded INSTANCE id and logs substitutions
#          on change only (the old per-call warning meant thousands of lines
#          a day). enrich() returns the model that actually answered so
#          callers record true provenance, and ensure_model_loaded() no
#          longer loads ours next to a different vision model that is
#          already loaded. list_loaded_models() removed (no callers left).
#          Plan: docs/10-Sep-2026-any-loaded-vlm-plan.md.
# SRP/DRY check: Pass — single responsibility is VLM round-trip, choosing
#                which loaded model to talk to, and the startup ensure-load.
#                _loaded_instances() is the one reader of LM Studio model
#                state for every consumer in the repo (pipeline, Guardian's
#                llm_verify, reel captions, bird_photo_ingest).

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
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


def _loaded_instances(lm_base: str, timeout: int = 5) -> list[dict]:
    """Every model instance LM Studio currently has loaded, with its vision flag.

    This is the ONE reader of LM Studio model state in this repo. The
    pipeline, Guardian's llm_verify, reel caption synthesis and
    bird_photo_ingest all go through it (via resolve_loaded_vlm or
    ensure_model_loaded).

    Uses the native GET /api/v1/models because that is the endpoint that
    reports vision support: it lists every downloaded model with
    `capabilities.vision` and `loaded_instances`. Do NOT go back to
    /api/v0/models `type == "vlm"`. v1 reports `type: "llm"` for every
    model (verified live 10-Sep-2026), and v0's label for the qwen3_5-arch
    models (qwen3.8-27b, bonsai-27b, qwen3.5-9b) was never checked, so a
    v0 filter can silently skip exactly the model Boss has loaded.

    Returns [{"model", "instance_id", "context_length", "vision"}, ...] in LM
    Studio's listing order. `instance_id` is what /v1/chat/completions
    accepts; it differs from the model key once a second instance of the
    same model exists (e.g. "qwen/qwen3-vl-4b:2"). Raises requests
    exceptions on transport/HTTP failure. Each caller decides what
    "LM Studio unreachable" means for it.
    """
    r = requests.get(f"{lm_base}/api/v1/models", timeout=timeout)
    r.raise_for_status()
    instances: list[dict] = []
    for model in r.json().get("models", []):
        vision = (model.get("capabilities") or {}).get("vision") is True
        for inst in model.get("loaded_instances") or []:
            instances.append({
                "model": model.get("key"),
                "instance_id": inst.get("id") or model.get("key"),
                "context_length": int((inst.get("config") or {}).get("context_length") or 0),
                "vision": vision,
            })
    return instances


def _matches(instance: dict, model_id: str) -> bool:
    """True when `model_id` names this instance, by model key or instance id."""
    return model_id in (instance["model"], instance["instance_id"])


# The last (preferred, resolved) pair resolve_loaded_vlm logged. Substitutions
# are logged when the choice CHANGES, never per call: at the S7 cadence a
# per-call warning is thousands of lines a day, and this repo already has an
# 814 MB log-bloat entry (CHANGELOG v2.40.14). Threads can race on it; the
# worst case is one duplicate log line.
_last_resolution: tuple[str, str] | None = None


def resolve_loaded_vlm(
    lm_base: str,
    preferred_model_id: str,
    timeout: int = 5,
    allowed_models: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Return the instance id of a loaded vision-capable model to call.

    `preferred_model_id` is a preference, not a requirement (Boss,
    08/09-Sep-2026: "Farm Guardian should just work with whatever model is
    loaded"). LM Studio is shared with his experiments, which swap the loaded
    model. It is used when loaded. Otherwise the first loaded vision-capable
    instance is used instead of skipping. On 08-Sep, qwen3.8-27b sat loaded
    all day, ~14k frames went unscored, and Guardian's night verifier failed
    open.

    Every id returned comes from LM Studio's own loaded_instances list, so a
    /v1/chat/completions call with it can never auto-load a model (reference
    doc rule 3, the 2026-04-13 incident; JIT loading is also off).

    `allowed_models` (model keys or instance ids) restricts the candidates when
    given. Guardian's night verifier passes llm_verification.validated_models.
    Choosing *which* model decides whether to wake Boss is a safety call, and
    an untested model can be confidently wrong: measured 10-Sep-2026,
    qwen3.5-9b suppressed 14 of 20 real positives, calling a real person at
    the coop "spider web on lens". Scoring photos has no such stakes, so the
    pipeline passes no list and takes any vision model.

    Raises ModelNotLoaded when no eligible model is loaded: no vision model at
    all, or none from `allowed_models`. Either way there is nothing this
    caller may ask.
    """
    global _last_resolution
    loaded = _loaded_instances(lm_base, timeout=timeout)
    vision = [i for i in loaded if i["vision"]]
    if allowed_models is not None:
        vision = [i for i in vision if any(_matches(i, a) for a in allowed_models)]
    preferred = next((i for i in vision if _matches(i, preferred_model_id)), None)
    chosen = preferred or (vision[0] if vision else None)
    if chosen is None:
        wanted = ("vision-capable model" if allowed_models is None
                  else f"model from {list(allowed_models)!r}")
        raise ModelNotLoaded(
            f"no {wanted} loaded in LM Studio (preferred {preferred_model_id!r}; "
            f"loaded: {[i['instance_id'] for i in loaded]!r})"
        )

    resolved = chosen["instance_id"]
    pair = (preferred_model_id, resolved)
    if pair != _last_resolution:
        if chosen is not preferred:
            log.warning(
                "vlm: preferred model %r is not loaded; using loaded vision model %r "
                "(logged on change only)", preferred_model_id, resolved,
            )
        elif _last_resolution is not None:
            log.info("vlm: preferred model %r is loaded again; using it", resolved)
        _last_resolution = pair
    return resolved


def ensure_model_loaded(
    lm_base: str,
    model_id: str,
    context_length: int,
    timeout: int = 180,
) -> str:
    """Make sure a vision model is available, loading model_id via LM Studio's
    native API only when no vision-capable model is loaded at all.

    The per-cycle path is deliberately read-only (it skips when the model
    isn't loaded — see this module's header and docs/13-Apr-2026-lm-studio-
    reference.md). This is the ONE controlled exception: called once at daemon
    startup so a reboot/LM-Studio-restart doesn't leave the model JIT-loaded at
    a too-small context (which caused "Context size has been exceeded" drops on
    portrait S7 frames). It checks what's loaded first so it never stacks
    instances, and loads with parallel=1 (the pipeline is single-in-flight) +
    flash_attention, exactly as the reference doc prescribes.

    10-Sep-2026 (v2.72.0): if a DIFFERENT vision-capable model is already
    loaded (typically Boss's experiment), this returns "co-tenant-vlm-loaded"
    and loads nothing. Every consumer resolves to whatever vision model is
    loaded (resolve_loaded_vlm), so loading ours as well would only put a
    second model in memory. Before this, the check only looked for model_id
    itself, so a pipeline restart mid-experiment loaded a second model
    beside Boss's. A text-only co-tenant still gets ours loaded next to it,
    because nothing else could look at an image.

    Returns one of: "already-loaded", "loaded", "reloaded-for-context",
    "co-tenant-vlm-loaded". Raises on API failure; the caller treats that as
    non-fatal and lets the per-cycle skip handle a still-unloaded model.
    """
    vision = [i for i in _loaded_instances(lm_base, timeout=10) if i["vision"]]
    mine = next((i for i in vision if _matches(i, model_id)), None)
    if mine is None and vision:
        return "co-tenant-vlm-loaded"
    outcome = "loaded"
    if mine is not None:
        if mine["context_length"] >= context_length:
            return "already-loaded"
        # Our own model, loaded at too small a context. Unload that exact
        # instance before reloading so we don't stack a second one on top.
        requests.post(
            f"{lm_base}/api/v1/models/unload",
            json={"instance_id": mine["instance_id"]}, timeout=30,
        ).raise_for_status()
        time.sleep(6)  # let VRAM actually free (2s was too short per the doc)
        outcome = "reloaded-for-context"

    requests.post(
        f"{lm_base}/api/v1/models/load",
        json={
            "model": model_id,
            "context_length": context_length,
            "parallel": 1,
            "flash_attention": True,
        },
        timeout=timeout,
    ).raise_for_status()
    return outcome


# Clauses that mention a leg band, in the shapes the model actually produces:
# "with a green leg band #2", "wearing a purple band", "no leg band visible",
# ", one with a white leg band on its left leg,". Deliberately greedy about the
# leading preposition/conjunction so removing the clause doesn't strand "with"
# or "and" dangling at the end of a sentence.
_BAND_CLAUSE_RE = re.compile(
    r"""
    \s*
    (?:,\s*)?                                   # leading comma
    (?:\b(?:and|with|wearing|sporting|but|while|its?|has|having|there\s+is)\b\s*)*
    (?:\bno\b\s*)?                              # "...no leg band visible"
    (?:\ba\b\s*|\ban\b\s*|\bone\b\s*)?
    (?:\b\w+\b\s+){0,2}?                        # optional colour / adjective
    \bleg[-\s]?band\b | \bband\b                # the noun itself
    [^.;!?]*                                    # rest of the clause
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _strip_band_clauses(text: str) -> str:
    """Remove any sentence or clause mentioning a leg band from caption text.

    Sentence-level first (a sentence that is *about* a band goes entirely),
    then clause-level for bands mentioned mid-sentence. Falls back to returning
    the original text if stripping would leave nothing — an unedited caption
    that mentions a band is still better than an empty one, and the caller
    logs so it can be caught.
    """
    def tidy(s: str) -> str:
        s = re.sub(r"\s*,\s*,", ",", s)
        s = re.sub(r"\s+([.,;!?])", r"\1", s)
        s = re.sub(r"\s{2,}", " ", s).strip(" ,;—-")
        if s and not s.endswith((".", "!", "?")):
            s += "."
        return s

    # 1. Sentence level — a sentence that talks about a band goes entirely.
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept = [s for s in sentences if "band" not in s.lower()]
    if kept:
        return tidy(" ".join(kept))

    # 2. Every sentence mentioned a band (about 1 caption in 600). Strip at
    #    clause level instead.
    out = tidy(_BAND_CLAUSE_RE.sub("", text))
    if out and "band" not in out.lower():
        return out

    # 3. Last resort — split on commas and dashes too and drop any fragment
    #    naming a band. Returns "" if nothing survives; a caption is optional
    #    downstream, whereas a published false band claim is the whole bug.
    frags = [f for f in re.split(r"\s*[,;—]\s*|\s+-\s+", text) if "band" not in f.lower()]
    out = tidy(", ".join(f.strip() for f in frags if f.strip()))
    return out if "band" not in out.lower() else ""


def prompt_for(camera_name: str, camera_context: str, prompt_template: str) -> str:
    from datetime import date

    from tools.pipeline.roster import format_named_individuals_block

    # v2.47.0: the "Named individuals" section used to hardcode two birds
    # directly in prompt.md — one of them (Birdadette) got renamed Birddor
    # in July and the prompt drifted stale until someone noticed. The
    # section is now generated live from farm-2026's flock roster on every
    # call (roster.py caches for 5 minutes, so this is cheap).
    try:
        named_block = format_named_individuals_block()
    except Exception:
        named_block = ""
    if not named_block:
        named_block = "(No bird currently has a confirmed enough visual profile to name.)"

    return (prompt_template
            .replace("{camera_name}", camera_name)
            .replace("{camera_context}", camera_context)
            .replace("{today}", date.today().isoformat())
            .replace("{named_individuals_block}", named_block))


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

    model_id is a preference, not a requirement (08-Sep-2026, per Boss):
    runs against model_id when it's loaded, otherwise falls back to whatever
    vision-capable model IS loaded (resolve_loaded_vlm) rather than skip.
    Raises ModelNotLoaded only when nothing vision-capable is loaded at all
    — caller should skip the cycle rather than auto-load. Returns a
    schema-conforming metadata dict plus meta fields (model_id, inference_ms,
    prompt_hash, raw_response, reasoning_output_tokens). `model_id` in the
    result is the instance that ACTUALLY answered. Callers must store that,
    not their configured preference, or a fallback-scored frame is recorded
    under the wrong model.

    context_length is accepted for API compatibility but ignored — the
    OpenAI-compat endpoint uses the model's loaded context length. The
    daemon configures context at load time via vlm_load_context_length.
    """
    del context_length  # accepted for API compat; see docstring
    model_id = resolve_loaded_vlm(lm_base, model_id)

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
        # `reasoning_effort: "none"` is the OpenAI-compat-aligned switch
        # that LM Studio honors for thinking models. The earlier `reasoning:
        # "off"` field is the LM Studio NATIVE-API param (/api/v1/chat) and
        # is silently ignored on /v1/chat/completions for the current build
        # of qwen/qwen3.6-35b-a3b — the model burns its budget on a
        # reasoning_content block and returns empty `content`, which fails
        # the JSON validator. Verified live 2026-04-26 after the LM Studio
        # API shape change. Don't revert to `reasoning: "off"`.
        "reasoning_effort": "none",
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

    # --- Band resolution: the model observed a ring, Python decides whose ---
    # The model is told (prompt.md) to report the band into band_color/
    # band_leg/band_number and to keep it OUT of the caption. Here that
    # reading is checked against the roster: `band_bird` is a real bird's name
    # only when the reading can belong to exactly one living bird, and None
    # for every impossible, ambiguous or absent reading. Downstream consumers
    # must treat None as "say nothing about a band" — which is the common case
    # and by design, since these bands are rarely legible at camera distance.
    #
    # Wrapped best-effort: a roster that won't load must never fail an
    # otherwise-good enrichment. See roster.resolve_band for why the matching
    # does not happen inside the model.
    try:
        from tools.pipeline.roster import resolve_band

        obj["band_bird"] = resolve_band(
            obj.get("band_color"),
            obj.get("band_leg"),
            obj.get("band_number"),
        )
    except Exception as exc:  # noqa: BLE001 — band ID is never load-bearing
        log.warning("band resolution skipped: %s", exc)
        obj["band_bird"] = None

    # prompt.md tells the model never to mention a band in the caption. It
    # mostly obeys — and then doesn't, on about 5% of frames (measured over 100
    # real s7 frames, 28-Jul-2026, AFTER the instruction was added). A prose
    # claim we didn't parse is a claim we can't check, and unchecked band claims
    # in captions are the exact defect this whole change exists to remove, so
    # the instruction gets a deterministic backstop rather than another rewrite.
    for field in ("caption_draft", "share_reason"):
        text = obj.get(field)
        if isinstance(text, str) and "band" in text.lower():
            obj[field] = _strip_band_clauses(text)

    return {
        "metadata": obj,
        "model_id": model_id,
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
    camera_name = sys.argv[2] if len(sys.argv) > 2 else "usb-webcam-1080p"
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
    print(f"model={result['model_id']} inference_ms={result['inference_ms']} "
          f"reasoning_tokens={result['reasoning_output_tokens']}")
    print(json.dumps(result["metadata"], indent=2))
