# 10-Sep-2026 — Run on whatever vision model LM Studio has loaded

> **Outcome (read this first):** shipped as v2.72.0 with one deliberate deviation from the
> original scope. The pipeline, reel captions and bird_photo_ingest run on **any** loaded vision
> model. **Guardian's night verifier does not.** It uses only `llm_verification.validated_models`,
> because `qwen3.5-9b` suppressed 14 of 20 real positives in testing, including a real person
> it called a spider web. See *Approach change* and *Verification results* below.

Author: Claude Opus 5 · Approved by Boss in chat 10-Sep-2026 ("just do what you need to do …
I want you to fix things"), after he stated the requirement on 09-Sep: *Farm Guardian should just
work with whatever model is loaded.*

## Why

LM Studio on this Mini is shared: Boss runs experiments on it (e.g. `qwen/qwen3.8-27b`), and
every Guardian-side consumer hard-pinned `qwen/qwen3-vl-4b`. While another model held the slot:

- **Pipeline** hard-skipped every frame. 08-Sep-2026: `qwen3.8-27b` sat loaded all day, ~14k
  frames went unscored, the carousel ran dry.
- **Guardian's night verifier** (`llm_verify.py`) reported *unavailable* and failed open.
  08-Sep 06:15 it did exactly that and posted UNVERIFIED alerts. (An estimate made while writing
  this plan, ~200 alerts a night, ignored gate ④'s 900s per-camera+class debounce and was wrong.
  Measured later: 1–3 a night typically, 24 on the worst recent night.)

Another agent (Claude Sonnet 5, Bubba) left an **uncommitted** fix in `vlm_enricher.py` on 08-Sep.
It is the right idea but has three defects:

1. **It filters on `/api/v0/models` `type == "vlm"`.** LM Studio's native `/api/v1/models`
   reports `type: "llm"` for *every* model and carries vision as a separate
   `capabilities.vision` flag. The qwen3_5-arch models (`qwen3.8-27b`, `bonsai-27b`,
   `qwen3.5-9b`) are vision-capable per v1, but nobody has checked what v0 labels them, so the
   fix may skip the exact model from the incident it cites.
2. **Provenance.** `orchestrator.py` and `iphone_lane/ingest.py` write
   `vlm_model = cfg["vlm_model_id"]`, so a frame scored by a fallback model gets recorded in
   `image_archive` and its JSON sidecar as scored by the preferred model.
3. It logs a WARNING on **every** substituted call, which is thousands of lines a day at the
   S7 cadence. This repo already has an 814 MB log-bloat entry.

Two more consumers were never touched: `llm_verify.py` (above) and the reel caption synthesis
in `daily_reel_runner.py`. And `ensure_model_loaded()` only checks whether *its own* model is
loaded before POSTing a load. A pipeline restart during an experiment would therefore put a
second model alongside Boss's.

## Scope

**In:** one shared resolver for every LM Studio consumer in this repo; provenance fix; Guardian
verifier; reel captions; non-stacking startup load; health-notice wording; lmstudio-watchdog
repo/installed drift; docs + CHANGELOG; restart Guardian + pipeline.

**Out:** the `llm_verification.timeout_seconds` (10s) value. Raising it raises alert latency on
real threats, and Boss has rejected that class of tuning. A too-slow model already degrades
gracefully: timeout → unavailable → UNVERIFIED alert. Also out: LM Studio's global
`defaultContextLength: max` setting. It's Boss's app default and it affects his experiments,
so it's his call. The watchdog co-tenant skip rule stays unchanged: it is what keeps the
watchdog from fighting experiments.

**Initially out, then done:** reloading the resident `qwen3-vl-4b`, which was at 262144 ctx /
parallel 4. *Correction to this doc's first draft:* that load did **not** come from `lms-cli` on
10-Sep 09:25. Those log lines were this session's own `lms ls` / `lms ps`. It came from a
bare `lms load qwen/qwen3-vl-4b` (no `--context-length`, so LM Studio's `max` default) on
**08-Sep 13:23**, during that day's recovery. It was reloaded at 16384 / parallel 1 because
verifier latency had gone from ~1.4s to 2.3–3.1s mean since that load. (A 42 GB "wired"
reading taken just after a refused 9b load was *not* this instance: wired was 5.6 GB right
before the reload. That attribution was dropped.)

## Architecture

All in `tools/pipeline/vlm_enricher.py`, the module every consumer already imports:

- `_loaded_vision_instances(lm_base)`: the **only** reader of model state. Native
  `GET /api/v1/models`; keeps entries with `capabilities.vision is True` and non-empty
  `loaded_instances`; returns `(model_key, instance_id, context_length)` per loaded instance.
- `resolve_loaded_vlm(lm_base, preferred)`: returns the preferred model's instance id if it
  is loaded, otherwise the first loaded vision instance. Raises `ModelNotLoaded` only when no
  vision-capable model is loaded. It always returns an id drawn from `loaded_instances`, so
  `/v1/chat/completions` can never auto-load (rule 3; JIT is also off). It logs a
  substitution only when the choice *changes*.
- `enrich()` returns `model_id` (what actually answered); callers store that.
- `ensure_model_loaded()` also reads via the helper. If a *different* vision model is
  already loaded it returns `"co-tenant-vlm-loaded"` and loads nothing. This also makes
  `bird_photo_ingest.py` safe with no edit there.

Consumers: `llm_verify.verify_detection` and `daily_reel_runner` caption synthesis switch to
`resolve_loaded_vlm`. Config keys (`vlm_model_id`, `llm_verification.model`) keep their names
and now mean *preferred*.

## Approach change (mid-execution)

Testing `qwen3.5-9b` as the answering model on 40 real Guardian cases showed that "any model"
is safe for **scoring** but not for **deciding whether to wake Boss**. It kept every
request well-formed (0 reasoning tokens, ~3s), but it suppressed 14 of the 20 cases
`qwen3-vl-4b` had confirmed. The frames for 06-Sep 21:48 and 07-Sep 06:42 show a man in a
hi-vis shirt at the coop, and it called them "spider web on lens" and "IR glare". CLAUDE.md's
own criterion is that a change silencing the real-person case is wrong regardless of anything
else. So the resolver gained an optional `allowed_models` list. The verifier passes
`llm_verification.validated_models` (default `["qwen/qwen3-vl-4b"]`) and fails open when none of
them is loaded. The pipeline passes no list. A model joins the list only after it keeps every
real positive alerting.

## TODOs

1. Implement the enricher helper, resolver, `enrich` return value and `ensure_model_loaded`.
2. Provenance: orchestrator + iphone_lane store `vlm_result["model_id"]`.
3. `llm_verify`: resolver, `VerificationResult.model`, model in the verdict log line.
4. `daily_reel_runner`: resolver. `alerts.py`: reword health notice.
5. Watchdog: sync repo copy to installed values, fix header, `cp` so they match.
6. Verify (real LM Studio, real frames, no mocks):
   a. resolver: preferred loaded → itself; preferred not loaded → falls back to `qwen3-vl-4b`.
   b. `ensure_model_loaded(preferred=<unloaded>)` while `qwen3-vl-4b` is resident →
      `co-tenant-vlm-loaded`, and `lms ps` shows nothing new. Run this before any load.
   c. load `qwen3.5-9b` (6.55 GB, qwen3_5 arch, reasoning default on — same family as the
      27Bs) at 16k / parallel 1; run `enrich` + `verify_detection` against it on real frames,
      record latency; unload that instance by id; `lms ps` back to one row.
   d. `scripts/replay-artifact-filter.py --with-vlm`. **CASE 2 (house-yard 21:44 real
      person) must still alert.**
   e. Restart Guardian + pipeline; pipeline startup says `already-loaded`; Guardian verdict
      lines carry the model name.
7. Docs: CLAUDE.md LM Studio + night-alert sections, LM Studio reference doc (JIT section,
   stale 2026-05-04 snippet, watchdog model name), CHANGELOG v2.72.0. Commit + push.

## Verification results

All against the live LM Studio (JIT off) with real frames and real DB rows. No mocks.

| Check | Result |
|---|---|
| resolver, preferred loaded | `qwen/qwen3-vl-4b` |
| resolver, preferred = `qwen3.8-27b` (not loaded) | falls back to `qwen/qwen3-vl-4b`, one WARNING, then silent |
| `ensure_model_loaded(preferred=qwen3.5-9b)` with 4b resident | `co-tenant-vlm-loaded`, `lms ps` unchanged |
| `ensure_model_loaded(preferred=qwen3-vl-4b)` | `already-loaded` |
| `enrich()` real S7 frame, fallback path | valid 23 fields, `model_id=qwen/qwen3-vl-4b` |
| `enrich()` answered by `qwen3.5-9b` | valid 23 fields, 0 reasoning tokens, 18.0s |
| verifier, 40 real cases, fallback path | 40/40 agree, 20/20 real positives alert, p50 1.9s |
| verifier, 40 real cases, answered by `qwen3.5-9b` | **26/40 agree, 6/20 real positives alert**, mean 3.0s: unsafe |
| allow-list: unvalidated model named while 4b loaded | refused → `no-validated-model-loaded`, fail-open |
| allow-list: fallback among validated models | resolves to the loaded validated model |
| verifier, 40 cases, Guardian's config path, after 16k reload | 40/40, 20/20, mean 0.8–1.3s |
| reload 4b at 16384 / parallel 1 | 8.7s without a model, one instance after |
| provenance: fallback enrich → `store()` (throwaway DB) | row + sidecar `vlm_model = qwen/qwen3-vl-4b` while the preference was `qwen3.8-27b` |
| restart pipeline + Guardian (10-Sep 09:50) | pipeline `VLM ensure-loaded qwen/qwen3-vl-4b at context>=16384: already-loaded`; Guardian up, no errors |
| `scripts/replay-artifact-filter.py --with-vlm` | **vacuous**: FAIL identically on committed and patched code; July frames pruned, 0 VLM calls, CASE 2's single alert is fail-open on a missing frame. Rebuild queued as a follow-up task |

**Not verifiable at restart:** a fresh production `image_archive` row. `s7-cam` is the only
camera that goes through the VLM, and it went dark at 09:40, ten minutes *before* the restart
(port 8080 closed, `farm-pi5` up, uptime segments not collapsing). That's a separate physical
fault. The provenance row above stands in until it is back.
