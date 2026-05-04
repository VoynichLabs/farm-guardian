# VLM Preset Alignment Plan — 04-May-2026

**Goal:** Make the LM Studio Birds preset the single source of truth for the VLM prompt and schema, eliminating `prompt.md` and `schema.json` as separate pipeline inputs.

## Problem

The pipeline maintained three separate sources that had to stay in sync:
- `tools/pipeline/prompt.md` — 87-line user-message template (the detailed VLM guidance)
- `tools/pipeline/schema.json` — structured output schema
- `~/.lmstudio/config-presets/Birds.preset.json` — LM Studio UI preset for manual use

The Birds preset had diverged from the pipeline:
- `overall_score` field added in commit `141053d` was missing from the preset schema
- `caption_draft` maxLength was 200 in the preset vs 450 in schema.json
- System prompt was one sentence ("You are looking at pictures of chickens and turkeys.")

## Solution (implemented v2.40.0)

**Birds preset** becomes the single source of truth:
- `llm.prediction.systemPrompt` = full `prompt.md` content (with `{camera_name}`, `{camera_context}`, `{today}` template vars preserved for pipeline substitution)
- `llm.prediction.structured.jsonSchema` = full current schema from `schema.json` (all 17 required fields including `overall_score`)
- Temperature corrected from 0.38 → 0.2 to match pipeline config

**Orchestrator** (`tools/pipeline/orchestrator.py` `_load_configs()`):
- Reads `birds_preset_path` from `tools/pipeline/config.json`
- Parses preset fields by key, extracts `systemPrompt` as `prompt_template` and `jsonSchema` as `schema`
- Falls back to `schema.json` + `prompt.md` if preset path is missing or unconfigured

**Config** (`tools/pipeline/config.json`):
- Added `"birds_preset_path": "~/.lmstudio/config-presets/Birds.preset.json"`

## What does NOT change

- `reasoning_effort: "none"` in `vlm_enricher.py` — already confirmed working across all models (0 reasoning_tokens verified live on Nemotron 2026-04-26)
- `_SYSTEM_PROMPT` in `vlm_enricher.py` — the short role-setter stays as the system turn; the preset content is the user-turn template
- Schema enforcement via `response_format: json_schema` — unchanged
- `schema.json` and `prompt.md` kept as fallback; not deleted

## Scope out

- Updating `docs/13-Apr-2026-lm-studio-reference.md` with LM Studio 0.4.8+ `reasoning_effort` API docs — separate task
- Per-model thinking disable keys in the preset — model-agnostic via `reasoning_effort: "none"` in API call; Nemotron key kept but new models don't need it added

## Editing the prompt going forward

Edit `~/.lmstudio/config-presets/Birds.preset.json` `llm.prediction.systemPrompt` value. The pipeline picks it up on next restart. `prompt.md` is no longer the live source when `birds_preset_path` is set.
