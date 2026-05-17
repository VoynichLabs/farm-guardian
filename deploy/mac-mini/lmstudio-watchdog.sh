#!/bin/sh
# Farm Guardian — LM Studio VLM watchdog
#
# Auto-recovers the pipeline's VLM when qwen/qwen3.5-9b drops out of LM
# Studio. Addresses the recurring "model not loaded -> pipeline silently
# skips every cycle" failure mode that has hit Boss many times (and the
# 814 MB log bloat called out in CHANGELOG v2.40.14).
#
# Plan:      docs/16-May-2026-lmstudio-watchdog-plan.md
# Reference: docs/13-Apr-2026-lm-studio-reference.md (Safe model swap pattern)
#
# Hard rules baked in:
#   - Never restart, quit, or touch LM Studio itself — only the loaded-
#     model state. If the server is unreachable, log and exit.
#   - Never unload another model (co-tenant rule from the reference doc).
#   - Always load via /api/v1/models/load with explicit context_length=8192,
#     flash_attention=true, parallel=1 (matches the pipeline's expectation
#     per the 2026-05-04 doc note on post-UI-swap slowness).
#   - Free-memory gate before loading: free+speculative+inactive pages
#     must clear ~1.4x the model footprint (per reference doc step 3).
#   - Idempotent: on a healthy machine each tick is a curl + grep no-op.
#
# INSTALLED COPY lives at:
#   ~/Library/Application Support/farm-guardian/lmstudio-watchdog.sh
# Must NOT run from ~/Documents — macOS TCC blocks launchd from executing
# files there (exit 126 "Operation not permitted").

HOST="http://localhost:1234"
MODEL="qwen/qwen3.5-9b"
CONTEXT=8192
MODEL_GB=6.55
LOG=/tmp/lmstudio-watchdog.log

ts() { date '+%Y-%m-%d %H:%M:%S'; }

echo "$(ts) --- tick ---" >> "$LOG"

# 1. Probe loaded models. Server unreachable = log + exit (not in scope to
#    start the server; that's LM Studio's own auto-server-start job).
LOADED_JSON=$(curl -s --max-time 4 "$HOST/v1/models")
if [ -z "$LOADED_JSON" ]; then
    echo "$(ts) server unreachable on :1234 — skipping (not in scope to start server)" >> "$LOG"
    exit 0
fi

# 2. Classify state.
LOADED_IDS=$(printf '%s' "$LOADED_JSON" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin).get('data', [])
    print(' '.join(m.get('id', '') for m in d))
except Exception:
    pass
" 2>/dev/null)

if printf '%s' "$LOADED_IDS" | tr ' ' '\n' | grep -qx -- "$MODEL"; then
    echo "$(ts) ok — $MODEL already loaded" >> "$LOG"
    exit 0
fi

if [ -n "$LOADED_IDS" ]; then
    echo "$(ts) co-tenant: other model(s) loaded ($LOADED_IDS) — skipping per reference-doc coordination rule" >> "$LOG"
    exit 0
fi

# 3. Free-memory gate. Need >= MODEL_GB * 1.4 in (free + speculative + inactive).
FREE_GB=$(python3 -c "
import re, subprocess
out = subprocess.check_output(['vm_stat']).decode()
def p(name):
    m = re.search(r'Pages '+name+r':\s+(\d+)', out)
    return int(m.group(1)) if m else 0
print('%.2f' % ((p('free') + p('speculative') + p('inactive')) * 16384 / 1024**3))
")
NEED_GB=$(python3 -c "print('%.2f' % ($MODEL_GB * 1.4))")
if ! awk -v f="$FREE_GB" -v n="$NEED_GB" 'BEGIN{exit !(f+0 >= n+0)}'; then
    echo "$(ts) insufficient free memory (${FREE_GB} GB < ${NEED_GB} GB needed) — skipping" >> "$LOG"
    exit 0
fi

# 4. Load via the native API with the documented body.
echo "$(ts) loading $MODEL (context=$CONTEXT, flash_attention=true, parallel=1; free=${FREE_GB} GB)" >> "$LOG"
LOAD_BODY=$(printf '{"model":"%s","context_length":%d,"flash_attention":true,"parallel":1}' "$MODEL" "$CONTEXT")
LOAD_RESP=$(curl -s --max-time 120 -X POST "$HOST/api/v1/models/load" \
    -H "Content-Type: application/json" \
    -d "$LOAD_BODY")
echo "$(ts) load response: $LOAD_RESP" >> "$LOG"

# 5. Verify and log the final loaded set.
sleep 2
VERIFY=$(curl -s --max-time 4 "$HOST/v1/models" | python3 -c "
import json, sys
try:
    print(','.join(m['id'] for m in json.load(sys.stdin).get('data', [])))
except Exception:
    print('(parse-error)')
" 2>/dev/null)
echo "$(ts) post-load loaded: $VERIFY" >> "$LOG"
