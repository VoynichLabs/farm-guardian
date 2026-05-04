# 29-Apr-2026 — Pipeline Control GUI + LM Studio Preset Integration Plan

**Author:** Claude Sonnet 4.6  
**Status:** Part 2 (preset integration) DONE in v2.40.0 (04-May-2026). Part 1 (GUI pause/resume) still pending.  
**Target version:** v2.38.0 (original target); preset integration shipped as v2.40.0

---

## Implementation notes (v2.40.0, 04-May-2026)

Part 2 was implemented with two differences from the spec below:

1. **Prompt template moved to preset, not just system prompt.** The plan said "user prompt (from `prompt.md`) stays in `prompt.md`." Instead, the full `prompt.md` content was written into `llm.prediction.systemPrompt` in the preset. The orchestrator reads it as the *user-message* template (not the system turn — `vlm_enricher.py:_SYSTEM_PROMPT` stays as the system turn). Result: `prompt.md` is no longer the live source when `birds_preset_path` is configured.

2. **Schema stored in standard `llm.prediction.structured` field, not custom `ext.farmGuardian.responseSchema`.** The plan proposed a custom string-encoded field. Instead the existing standard LM Studio structured-output field (`llm.prediction.structured.jsonSchema`) is used directly — same field LM Studio's UI already reads. No custom field parsing needed.

3. **Config key is `birds_preset_path`, not `lm_preset_path`.** More explicit about which preset.

4. **Preset loading is in `orchestrator.py:_load_configs()`, not `vlm_enricher.py`.** Keeps `enrich()` signature unchanged.

To edit the VLM prompt going forward: edit `llm.prediction.systemPrompt` in `~/.lmstudio/config-presets/Birds.preset.json`. Pipeline picks it up on restart. See `docs/04-May-2026-vlm-preset-alignment-plan.md`.

---

## Context

Two pain points came up on 2026-04-29:

1. **Every pipeline pause requires a Claude session.** Swapping LM Studio models means asking an assistant to `launchctl unload / load` the pipeline LaunchAgent. Boss should be able to do this from the Guardian dashboard himself.

2. **Prompt and schema live in files disconnected from the model.** `tools/pipeline/prompt.md` and `schema.json` are static files with no awareness of which model is loaded. The LM Studio preset (`Birds.preset.json`) already stores model-specific inference settings — the system prompt and response schema should live there too, so a model swap carries its own prompt without any file edits.

---

## Scope

### In

- A **Pause / Resume** button for the VLM pipeline on the Guardian dashboard (port 6530).
- **Preset-driven prompt + schema**: the pipeline reads `llm.prediction.systemPrompt` and a custom schema field from a named LM Studio preset JSON instead of `prompt.md` / `schema.json`.
- Update `Birds.preset.json` with the full production system prompt and schema.
- Document the preset field conventions so future model presets can carry their own prompts.

### Out

- Changing the schema or prompt contents (separate concern).
- Auto-detecting which preset is currently loaded in LM Studio (not exposed by LM Studio's API; requires explicit config).
- Per-camera pause (always pause the whole pipeline for model swaps).
- Any changes to the VLM calling mechanism or `response_format` construction.

---

## Architecture

### Part 1 — Pipeline Pause/Resume GUI

#### Flag-file control plane

The orchestrator already runs as a long-lived process. The simplest and most reliable control mechanism is a **flag file**: if `/tmp/farm-pipeline.pause` exists, the orchestrator skips VLM inference on every cycle (still captures frames, returns `status: paused` in the cycle log so the log stays readable). Resume = delete the file.

This avoids any IPC socket setup, is crash-safe (flag file survives orchestrator restarts), and is instantly testable from the terminal: `touch /tmp/farm-pipeline.pause`.

#### Orchestrator change (`orchestrator.py`)

In `run_cycle()`, after the quality gates and before the VLM call, check:

```python
import pathlib
_PAUSE_FLAG = pathlib.Path("/tmp/farm-pipeline.pause")

if _PAUSE_FLAG.exists():
    result.update(status="paused", reason="pipeline paused via flag file")
    return result
```

Log the `paused` status the same way `gated` is logged — one INFO line per cycle. No spam if every cycle hits it.

#### Guardian API (`api.py`)

Two new endpoints, local-only (no auth needed — dashboard is LAN-only):

```
POST /api/v1/pipeline/pause   → touches /tmp/farm-pipeline.pause, returns {"paused": true}
POST /api/v1/pipeline/resume  → removes /tmp/farm-pipeline.pause, returns {"paused": false}
GET  /api/v1/pipeline/status  → returns {"paused": bool, "flag_path": "..."}
```

#### Dashboard (`static/index.html` + `static/app.js`)

Add a small **Pipeline** card to the dashboard (near the top, since it's operationally important). Shows:

- Status badge: green "Running" / amber "Paused"
- One button: **Pause pipeline** (when running) or **Resume pipeline** (when paused)
- Status refreshes on the same poll loop as camera status (every 5s).

No modal, no confirmation — it's a local tool, not a destructive action. Button click → POST → badge flips.

---

### Part 2 — LM Studio Preset Integration

#### Preset field conventions

LM Studio preset JSONs have two storage areas:

- `operation.fields[]` — inference parameters applied at prediction time (system prompt, temperature, etc.)
- `load.fields[]` — model-load parameters (context length, GPU layers, etc.)

We add two custom fields to `operation.fields` for farm-specific use:

| key | value type | purpose |
|-----|-----------|---------|
| `llm.prediction.systemPrompt` | string | The VLM system prompt sent as `role: system`. Standard LM Studio field — already in the Birds preset with a short placeholder. Replace with the full production system prompt from `vlm_enricher.py:_SYSTEM_PROMPT`. |
| `ext.farmGuardian.responseSchema` | string (JSON-encoded) | The full `response_format.json_schema` object, JSON-encoded as a string. Pipeline parses it at startup. |

The user prompt (field rubrics, camera context, today's date — from `prompt.md`) stays in `prompt.md` for now. It's per-camera-context, not per-model, so it belongs in the repo. System prompt and schema are model-specific and belong in the preset.

#### `vlm_enricher.py` change

Add a `preset_path: str | None = None` parameter to `enrich()`. When provided:

1. Read and parse the preset JSON.
2. Extract `llm.prediction.systemPrompt` → use as `_SYSTEM_PROMPT` (falling back to the hardcoded constant if the field is absent).
3. Extract `ext.farmGuardian.responseSchema` → JSON-parse it → use instead of the `schema` argument (falling back to the passed-in schema if absent).

The orchestrator passes `preset_path=cfg.get("lm_preset_path")` — a new optional key in `tools/pipeline/config.json`. If unset, behaviour is identical to today.

#### `tools/pipeline/config.json` addition

```json
"lm_preset_path": "/Users/macmini/.lmstudio/config-presets/Birds.preset.json"
```

This is the only place Boss needs to change when switching presets — same file where `vlm_model_id` lives.

#### `Birds.preset.json` update

Replace the placeholder system prompt with the full production text from `vlm_enricher.py:_SYSTEM_PROMPT`. Add the `ext.farmGuardian.responseSchema` field with the contents of `schema.json` JSON-encoded as a string.

---

## Implementation order (TODOs for the implementing agent)

1. **Read this doc in full first.** Then read `tools/pipeline/orchestrator.py`, `tools/pipeline/vlm_enricher.py`, `api.py`, `dashboard.py`, `static/index.html`, `static/app.js`, and `tools/pipeline/config.json`. Understand the existing patterns before touching anything.

2. **Orchestrator pause flag** — add `_PAUSE_FLAG` check in `run_cycle()`. One import, five lines. Test: `touch /tmp/farm-pipeline.pause`, confirm log shows `status: paused`; remove file, confirm normal operation resumes.

3. **Guardian API endpoints** — add three routes to `api.py`. No new dependencies.

4. **Dashboard card** — add Pipeline card to `index.html` / `app.js`. Match existing card style (Tailwind classes already in use). Poll `/api/v1/pipeline/status` every 5s.

5. **`vlm_enricher.py` preset_path support** — add optional `preset_path` param, add preset-read logic (stdlib `json` + `pathlib`, no new deps), fall back gracefully if fields absent.

6. **`Birds.preset.json` update** — replace system prompt field value; add `ext.farmGuardian.responseSchema` field with schema JSON as a string.

7. **`config.json` update** — add `lm_preset_path` pointing to Birds preset.

8. **Docs + CHANGELOG** — update `docs/13-Apr-2026-lm-studio-reference.md` with the new preset field conventions. CHANGELOG entry for v2.38.0.

9. **Verify** — pause from dashboard, swap model in LM Studio, resume, confirm VLM calls succeed with the preset's system prompt active. Check logs for `preset_path` being picked up.

---

## Files touched

| File | Change |
|------|--------|
| `tools/pipeline/orchestrator.py` | pause flag check in `run_cycle()` |
| `tools/pipeline/vlm_enricher.py` | `preset_path` param + preset-read logic |
| `api.py` | 3 new pipeline control endpoints |
| `dashboard.py` | wire the new endpoints if needed |
| `static/index.html` | Pipeline card HTML |
| `static/app.js` | Pipeline card JS (poll + button handler) |
| `tools/pipeline/config.json` | `lm_preset_path` key (gitignored, per-host) |
| `~/.lmstudio/config-presets/Birds.preset.json` | full system prompt + schema field |
| `docs/13-Apr-2026-lm-studio-reference.md` | preset field conventions |
| `CHANGELOG.md` | v2.38.0 entry |

---

## Non-goals / things to watch out for

- **Do not auto-detect the loaded model's preset.** LM Studio's `/v1/models` returns model IDs, not preset names. The config-based `lm_preset_path` is the explicit, reliable path.
- **Do not put the user prompt (prompt.md rubrics) in the preset.** It has `{camera_name}`, `{camera_context}`, `{today}` substitution tokens — it belongs in the repo.
- **Do not change the `response_format` construction logic** in `vlm_enricher.py` beyond wiring in the schema from the preset. Grammar sampling via `json_schema` stays exactly as it is.
- **The pause flag is not durable across reboots** by design — if the Mac Mini reboots, the pipeline comes back running. This is the right default (don't wake up to a permanently paused pipeline). If Boss needs a durable pause across reboots, that's a separate ask.
