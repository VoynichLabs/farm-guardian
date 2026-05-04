<!--
Author: Claude Opus 4.6
Date: 13-April-2026
PURPOSE: Single source of truth for any future Farm Guardian agent that
         needs to call LM Studio. Captures (a) the API surface, (b) the
         model-loading safety rules, (c) the locally available models,
         (d) the 13-April-2026 watchdog-reset incident and what we do
         differently because of it, and (e) coordination rules when LM
         Studio is shared with the G0DM0D3 research repo.
SRP/DRY check: Pass — first dedicated LM Studio reference in this repo.
               Some of this content was previously only in
               G0DM0D3-research/CLAUDE.md, which a Guardian agent
               wouldn't see.
-->

# LM Studio — Reference for Farm Guardian Agents

This is the single document any agent should read before adding code
that talks to LM Studio. **Read it in full once. Don't skim.**

## TL;DR — the rules that matter

1. **Never call `/api/v1/models/load` without first checking what's
   loaded.** Each load creates a new VRAM instance. Loading the same
   model twice doubles memory. Loading a 22 GB model on top of a 24 GB
   model crashed the box on 2026-04-13.
2. **Always pass `context_length` on load.** Default is the model's
   max — for `glm-4.6v-flash` that's 131,072 tokens, which reserves
   gigabytes of KV cache you don't need. Cap at 8,192–16,384 unless
   you have a reason.
3. **Never call `/v1/chat/completions` against a model name that
   isn't already loaded** unless you've explicitly verified the safe
   load conditions (see #1). The OpenAI-compatible chat endpoint
   **auto-loads** any requested model — that's how the box died on
   2026-04-13.
4. **The Mac Mini has 64 GB unified memory** shared between OS, Guardian,
   and LM Studio. Watch combined footprint, not just LM Studio.
5. **G0DM0D3-research may also be using LM Studio.** Coordinate
   (read-only check before writes) instead of contending.

If you only remember one thing, remember rule 3.

---

## What LM Studio is, in this context

LM Studio is a local LLM server running on the Mac Mini. It exposes
both an OpenAI-compatible API (`/v1/...`) and a native management API
(`/api/v1/...`) on `http://localhost:1234`. It can also serve over
the LAN at `http://192.168.0.105:1234` (Mac Mini's LAN IP).

**There is a second LM Studio instance on the LAN.** The GWTC
(Gateway) laptop runs LM Studio on a non-standard port: **9099**, not
1234. The model lineup on GWTC is different from the Mac Mini and
the laptop's IP drifts on DHCP — find it by scanning for port 9099
on the /24 (recipe in CLAUDE.md "Network & Machine Access" section)
or by reading `~/bubba-workspace/memory/reference/network.md`.
Everything in this document is about the Mac Mini's instance unless
explicitly noted; if you target the GWTC instance you are coordinating
with a different machine's resources, not this one's.

LM Studio holds models in VRAM as long-lived "instances" rather than
loading per-request. Loading is slow (5–30 s); inference is fast.
Loading is *also* dangerous — see the incident below.

## How Farm Guardian relates to LM Studio

**Currently:** Guardian does not call LM Studio. The original
`vision.py` (species refinement using `glm-4.6v-flash`) was removed in
v2.17.0 ("just show me the picture, no classification" — Boss). There
is no Guardian-side path that opens a connection to LM Studio at the
time of writing.

**Planned:** A standalone tool (not part of the main pipeline) that
samples brooder snapshots and sends them to `glm-4.6v-flash` for
narrative interpretation. See
`docs/13-Apr-2026-brooder-vlm-narrator-plan.md`. The plan codifies
the safety rules from this document into a small Python script.

**If you add new LM Studio integrations:** keep them out of the hot
path (capture / detect / alerts). Failure modes — slow inference,
model load delays, watchdog resets — should never block detection.

---

## API endpoints

### Native LM Studio API (`/api/v1/`)

Use this for any model-management work.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/v1/chat` | Native chat. Stateful chats, MCP, model-load streaming, prompt processing events, per-request context length. |
| `GET`  | `/api/v1/models` | List **all downloaded** models with full metadata (size, quant, max context, capabilities, currently-loaded instances). |
| `POST` | `/api/v1/models/load` | Load a model into VRAM. Returns an `instance_id` you'll need for unload. |
| `POST` | `/api/v1/models/unload` | Unload a model from VRAM. Requires the `instance_id`. |
| `POST` | `/api/v1/models/download` | Download a model from HuggingFace. |
| `GET`  | `/api/v1/models/download/status` | Check download progress. |

**Load body:**
```json
{
  "model": "zai-org/glm-4.6v-flash",
  "context_length": 8192,
  "flash_attention": true,
  "eval_batch_size": 512,
  "parallel": 1
}
```
Only `model` is required. **Always set `context_length`** explicitly
(see Rule 2). Set `parallel: 1` if you don't want concurrent
inferences each grabbing their own KV-cache slot — concurrent slots
were a contributing factor in the watchdog incident.

Response: `{"type":"llm","instance_id":"<key>","load_time_seconds":16.7,"status":"loaded"}`

**Unload body:**
```json
{"instance_id": "zai-org/glm-4.6v-flash"}
```

### OpenAI-compatible API (`/v1/`)

Use this for normal inference, **only** when you've verified the
target model is already loaded.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET`  | `/v1/models` | List currently **loaded** models only (OpenAI format). The `id` field is the model key. |
| `POST` | `/v1/chat/completions` | Chat inference. **Auto-loads if model not present** (DANGEROUS — see below). |
| `POST` | `/v1/responses` | OpenAI Responses API (stateful, MCP, custom tools). |
| `POST` | `/v1/embeddings` | Embedding inference. |
| `POST` | `/v1/completions` | Text completion. |

### Anthropic-compatible API (`/v1/`)

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/v1/messages` | Anthropic Messages API format. Streaming + custom tools supported. |

---

## Safe model swap pattern

Use this every time you need to change which model is loaded.

```bash
HOST=http://localhost:1234

# 1. Check what's loaded
LOADED=$(curl -s "$HOST/v1/models" | python3 -c "
import json, sys
d = json.load(sys.stdin).get('data', [])
print(d[0]['id'] if d else 'none')
")
echo "Currently loaded: $LOADED"

# 2. Unload if something is loaded
if [ "$LOADED" != "none" ]; then
    curl -s -X POST "$HOST/api/v1/models/unload" \
        -H "Content-Type: application/json" \
        -d "{\"instance_id\":\"$LOADED\"}" > /dev/null
    sleep 6   # let VRAM actually free; 2s was too short
fi

# 3. Free-memory gate (Apple Silicon: page size 16384)
FREE_BYTES=$(vm_stat | python3 -c "
import sys, re
free = spec = inact = 0
for line in sys.stdin:
    m = re.match(r'Pages free:\s+(\d+)', line);        free  = int(m.group(1)) if m else free
    m = re.match(r'Pages speculative:\s+(\d+)', line); spec  = int(m.group(1)) if m else spec
    m = re.match(r'Pages inactive:\s+(\d+)', line);    inact = int(m.group(1)) if m else inact
print((free + spec + inact) * 16384)
")
NEEDED=$((8 * 1024 * 1024 * 1024))   # 8 GB headroom example
if [ "$FREE_BYTES" -lt "$NEEDED" ]; then
    echo "Insufficient free memory; aborting load."
    exit 1
fi

# 4. Load with explicit context_length
curl -s -X POST "$HOST/api/v1/models/load" \
    -H "Content-Type: application/json" \
    -d '{"model":"zai-org/glm-4.6v-flash","context_length":8192,"flash_attention":true}' > /dev/null
sleep 5

# 5. Verify
curl -s "$HOST/v1/models" | python3 -c "
import json, sys
print(json.load(sys.stdin)['data'][0]['id'])
"
```

The sweep scripts in
`G0DM0D3-research/research/run_emoji_gap_fill.sh` and
`research/run_paper_v2_followups.sh` implement this same pattern
(after the bash 3.2 fix — see "Gotchas" below).

---

## Locally available models (as of 2026-04-13)

Source: `lms ls` and `GET /api/v1/models` on the Mac Mini.

| Model key | Params | Quant | Size | Max ctx | Vision | Tools | Reasoning |
|---|---|---|---|---|---|---|---|
| `openai/gpt-oss-20b` | 20B (MoE) | MXFP4 | 12.1 GB | 131,072 | no | yes | yes |
| `google/gemma-4-26b-a4b` | 26B | Q4_K_M | 18.0 GB | 262,144 | yes | yes | yes |
| `qwen/qwen3.5-35b-a3b` | 35B (MoE, 3B active) | Q4_K_M | 22.1 GB | 262,144 | yes | yes | yes |
| `qwen/qwen3.5-9b` | 9B | Q4_K_M | 6.5 GB | 262,144 | yes | yes | yes |
| `zai-org/glm-4.7-flash` | unknown | 6-bit | 24.4 GB | 202,752 | no | yes | yes |
| `zai-org/glm-4.6v-flash` | unknown | 4-bit | 7.1 GB | 131,072 | **yes** | yes | yes |
| `nvidia/nemotron-3-nano` | unknown (~30B) | 4-bit | 17.8 GB | 262,144 | no | yes | yes |
| `nvidia/nemotron-3-nano-4b` | 4B | Q4_K_M | 2.8 GB | 1,048,576 | no | yes | yes |
| `liquid/lfm2-24b-a2b` | 24B | 4-bit | 13.4 GB | 128,000 | no | yes | no |
| `qwen/qwen3-coder-next` | unknown | 4-bit | 44.9 GB | 262,144 | no | yes | no |

**For Guardian, the relevant model is `zai-org/glm-4.6v-flash`** —
it's the only locally-available vision model under ~10 GB. Don't
default to anything else without a reason.

**Memory budget reminder:** total VRAM is the same as system RAM
(64 GB on this Mac Mini). Combined footprint = OS (≈8 GB) +
Guardian (≈2.5 GB) + browser/other apps + LM Studio model + KV cache.
A 22 GB model + a 12 GB model loaded simultaneously will get you
within a kernel-stall window of OOM. **Never load two models at
once.**

---

## Image inference with `glm-4.6v-flash`

Standard OpenAI multi-modal chat shape:

```json
POST /v1/chat/completions
{
  "model": "zai-org/glm-4.6v-flash",
  "max_tokens": 200,
  "temperature": 0.7,
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "Describe what you see."},
        {"type": "image_url", "image_url": {
          "url": "data:image/jpeg;base64,<base64-bytes>"
        }}
      ]
    }
  ]
}
```

Multiple `image_url` blocks per message are supported; the model can
compare or rank them. Useful for "pick the most interesting image
from this batch" patterns.

Inference time on `glm-4.6v-flash` for a single 1080p image at 4-bit
quant is roughly **5–30 s** depending on prompt length and reasoning
depth. Plan cadence accordingly.

---

## Coordination with G0DM0D3-research

The other repo on this machine —
`~/Documents/GitHub/G0DM0D3-research/` — also uses LM Studio. Its
sweeps load models for hours at a time and may load any of the
models in the table above.

**The minimum coordination contract for Guardian-side LM Studio
work:**
1. Before any load, GET `/v1/models`. If something other than your
   target model is loaded, **log and skip the cycle**, do not unload
   somebody else's working model.
2. Never call `/v1/chat/completions` with a `model` field that
   doesn't match what's currently loaded — the auto-load will
   stack a second instance on top of the existing one and exhaust
   memory.
3. If you need exclusive access to LM Studio for a long-running
   Guardian task, surface that in the operator log. Do not
   indefinitely hold the model.

The watchdog incident below is the cautionary tale.

---

## The 2026-04-13 watchdog-reset incident

**Date:** 2026-04-13, 07:13:30 EDT. Mac Mini hard-reset; kernel came
back at 07:14:47.
**Diagnosis:** `kern.shutdownreason: wdog,reset_in_1` and no `.panic`
file. Apple Silicon hardware watchdog forced a reset because the
kernel stopped servicing heartbeats. **Not a power loss; not a
clean kernel panic.** The system locked up so hard it couldn't
flush a panic log to disk before the reset hit.

**Root cause:** Concurrent inference race between two LM Studio
clients targeting the same vision model.

- The (now-removed) `farm-guardian/vision.py` was calling LM Studio's
  `/v1/chat/completions` endpoint against `zai-org/glm-4.6v-flash`
  whenever a YOLO trigger class lit up. That endpoint **auto-loads**
  the requested model.
- Simultaneously, a G0DM0D3-research overnight sweep was running its
  Phase 3 (no_content_words × glm-4.6v-flash). That phase scheduled
  the same model.
- Two failure modes were possible (and one or both fired): (a)
  stacked-model load (LM Studio loading the same model twice,
  doubling memory), and/or (b) concurrent-inference KV-cache
  explosion (each request reserving a per-slot KV cache at the
  model's default 131,072-token max context).
- Either path drove unified memory close enough to the wall that the
  kernel scheduler stalled and the watchdog forced a reset.

**Why it took until v2.17.0 to surface:** The previous overnight
sweeps used models Guardian doesn't call (`glm-4.7-flash`,
`qwen-35b`, etc.). The 2026-04-13 sweep was the first overnight
that scheduled the same model Guardian's `vision.py` was
auto-loading. ~14 minutes of overlap was enough.

**What we changed in response:**
- `farm-guardian/vision.py` removed entirely (v2.17.0). Guardian no
  longer makes any LM Studio calls. The hot-path safety problem is
  resolved by not having a hot path.
- The `G0DM0D3-research/run_*.sh` sweep scripts were hardened with:
  context_length cap on every load, longer post-unload sleep
  (6 s instead of 2 s), and a `vm_stat`-based free-memory gate that
  refuses to load a model unless `(free + speculative + inactive)
  pages × 16 KB ≥ model_size × 1.4`.
- This document was written so future Guardian agents understand
  why `vision.py` is gone, what would have to change to bring it
  back safely, and what to never do.

**Full post-mortem:**
`~/Documents/GitHub/G0DM0D3-research/docs/13-Apr-2026-watchdog-reset-postmortem.md`
(in the other repo).

---

## Gotchas the harness scripts taught us

### macOS ships bash 3.2

`declare -A` (associative arrays) is bash 4+. Anything that uses
associative arrays in a `#!/usr/bin/env bash` script will fail at
parse time with `<key>: unbound variable`. Use a `case` statement
instead:

```bash
model_bytes() {
    case "$1" in
        zai-org/glm-4.6v-flash) echo 7623069696 ;;
        zai-org/glm-4.7-flash)  echo 26199301324 ;;
        *)                      echo 0 ;;
    esac
}
```

The G0DM0D3 sweep scripts now use this pattern. If you copy bash
patterns from `G0DM0D3-research/research/run_emoji_gap_fill.sh`,
you'll inherit the right approach.

### Python 3.14 has no spacy wheels yet

The Mac Mini's default `python3` is Homebrew Python 3.14. spaCy
doesn't ship 3.14 wheels and the source build chokes on cython
extensions. If you need a POS tagger, either provision a venv with
Python 3.12 or use a regex/keyword heuristic. (G0DM0D3-research
prototyped a spacy version of its constraint compliance checker
and left it dormant for this reason — see
`G0DM0D3-research/research/constraint_compliance_check_pos.py`.)

### `pip install` outside a venv hits PEP 668

System-Python pip refuses to install into Homebrew's site-packages
without `--break-system-packages`. Use the per-repo venv at
`./venv` (or `./.venv`) and its `bin/pip`, never system pip.

### LM Studio's chat endpoint silently auto-loads

Worth restating because this is the rule the watchdog incident
broke. `POST /v1/chat/completions` with a `model` field that's not
loaded **does not error** — it loads the model first, then completes
the request. Convenient in single-tenant use; dangerous in
multi-tenant or concurrent use. Always verify what's loaded before
issuing inference requests if you can't guarantee single-tenant.

### `kern.shutdownreason` is your post-incident tool

If the box reboots unexpectedly, run `sysctl kern.shutdownreason`
*before doing anything else*. The value persists across the reboot
and is your only forensic signal.

| Value | Meaning |
|---|---|
| `0x0` (or empty) | Clean shutdown |
| `pwr_btn` / `pwr_loss` | Power button / power loss |
| `wdog,reset_in_1` | Hardware watchdog reset (kernel stalled) |
| `panic` + a `.ips`/`.panic` file in `/Library/Logs/DiagnosticReports/` | Software kernel panic |

A `wdog` value with no `.panic` file means the system was too
unresponsive to write a panic log — the failure was deeper than a
software crash. On this machine, that almost certainly means
memory pressure from LM Studio.

---

## Disabling reasoning / thinking on OpenAI-compat endpoint (v2.40.0)

**TL;DR: pass `"reasoning_effort": "none"` in the request body. It works.**

LM Studio 0.4.8+ added `reasoning_effort` to the `/v1/chat/completions` endpoint.
Documented values are `low`, `medium`, `high`, `max`. The undocumented value `"none"`
is also accepted and fully disables the thinking/reasoning block.

Empirically verified 2026-04-26 against Nemotron and Qwen3.6-35b-A3B:

```bash
curl -s -X POST http://localhost:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "nvidia/nemotron-3-nano-omni", "reasoning_effort": "none", ...}'
```

Response: `reasoning_content: ""`, `reasoning_tokens: 0`, `finish_reason: stop`.

**Why not `"reasoning": "off"`?** That field exists on the *native* `/api/v1/chat`
endpoint but is silently ignored on `/v1/chat/completions`. Using it on the
OpenAI-compat endpoint causes the model to burn its full reasoning budget, return
the reasoning block in `reasoning_content`, and emit empty `content` — which
breaks the JSON response validator. Confirmed broken 2026-04-26. Do not revert.

The pipeline sets `reasoning_effort: "none"` in every request body in
`tools/pipeline/vlm_enricher.py`. This is model-agnostic — works on any reasoning
model loaded in LM Studio without any preset-level thinking-disable flag.

The Birds preset also carries `ext.virtualModel.customField.nvidia.nemotron3NanoOmni.enableThinking: false`
as a load-time belt-and-suspenders for Nemotron specifically, but the API-call
param is the reliable cross-model solution.

---

## Reference and pointers

- This document: `farm-guardian/docs/13-Apr-2026-lm-studio-reference.md`
- Brooder narrator plan (the planned Guardian use of LM Studio):
  `farm-guardian/docs/13-Apr-2026-brooder-vlm-narrator-plan.md`
- Watchdog post-mortem (other repo):
  `G0DM0D3-research/docs/13-Apr-2026-watchdog-reset-postmortem.md`
- Hardened sweep script template (other repo, illustrative):
  `G0DM0D3-research/research/run_emoji_gap_fill.sh`
- Constraint-aware compliance checker (other repo, lessons about
  VLM-output evaluation that would matter if Guardian ever needs to
  classify VLM responses):
  `G0DM0D3-research/research/constraint_compliance_check.py`
- Auto-memory record of the resolved Guardian/sweep VRAM race:
  `~/.claude/projects/-Users-macmini/memory/project_lm_studio_guardian_vram_race.md`

---

**If you're an agent reading this for the first time:** before adding
any LM Studio code to Guardian, also read the brooder narrator plan
and the watchdog post-mortem. The narrator plan shows the safe
pattern in code form; the post-mortem shows what happens when you
deviate.
