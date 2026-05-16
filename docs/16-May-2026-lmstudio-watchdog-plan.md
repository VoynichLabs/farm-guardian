<!--
Author: Claude Opus 4.7 (1M context)
Date: 16-May-2026
PURPOSE: Plan for a tiny watchdog that auto-recovers the pipeline's VLM
         when LM Studio loses the qwen/qwen3.5-9b model. Eliminates the
         recurring "no model loaded → pipeline silently skips every cycle
         → human must click load in the UI" failure mode.
SRP/DRY check: Pass — does one thing (load qwen if missing), reuses the
               documented safe-load pattern from
               docs/13-Apr-2026-lm-studio-reference.md verbatim.
-->

# 16-May-2026 — LM Studio VLM watchdog plan

## Why this exists

Recurring failure: LM Studio drops the `qwen/qwen3.5-9b` model (app restart, manual UI swap, OOM, etc.), the pipeline keeps running but every VLM call returns `model_not_loaded`, the gem/caption/Discord flow goes silent, and Boss has to notice and re-load the model in the LM Studio UI. This has happened many times.

There is no built-in LM Studio behaviour that auto-reloads a specific model after an unload event (JIT is intentionally OFF per the reference doc — auto-load via the chat endpoint is what crashed the box on 2026-04-13). So if we want auto-recovery, we have to add it as a separate, safe, documented loop.

## Scope

**In:**
- Detect when `qwen/qwen3.5-9b` is not in LM Studio's loaded-model list and re-load it using the documented safe pattern.
- Run on a short interval (120 s) as a user LaunchAgent.
- Log every tick.

**Out:**
- Does NOT start, stop, restart, or otherwise touch the LM Studio app or server process.
- Does NOT unload other models — explicitly co-tenant-safe per the reference doc's coordination rule (if some other model is loaded, log and skip the cycle).
- Does NOT load any model other than `qwen/qwen3.5-9b`.
- Does NOT change context length, parallel, or any other server-wide setting beyond the documented per-load body.

## Architecture

One shell script + one LaunchAgent. Both live under the canonical `deploy/mac-mini/` tree in this repo; the runtime copies live under `~/Library/Application Support/farm-guardian/` and `~/Library/LaunchAgents/` (NOT `~/Documents/` — macOS TCC blocks launchd from executing scripts under `~/Documents`, exit 126 "Operation not permitted").

### Script: `lmstudio-watchdog.sh`

```
1. GET http://localhost:1234/v1/models
   - on connection failure: log "server unreachable — skipping" and exit 0
     (the watchdog does NOT restart the server; that's not in scope and
     conflicts with the "never poke LM Studio itself" rule)
2. parse the loaded list:
   - if qwen/qwen3.5-9b in loaded -> log "ok — already loaded", exit 0
   - elif loaded is empty -> proceed to free-memory gate, then load
   - elif loaded contains something else -> log "other model loaded
     (<id>), skipping per coordination rule", exit 0
3. free-memory gate (per reference doc step 3 of Safe model swap pattern):
   vm_stat pages free+spec+inactive * 16384 >= 6.55 GB * 1.4 ≈ 9.2 GB
   - if not enough: log "insufficient free memory (X.X GB) — skipping", exit 0
4. POST /api/v1/models/load with:
   {"model":"qwen/qwen3.5-9b","context_length":8192,
    "flash_attention":true,"parallel":1}
   - response written to log; non-200 = log and exit 0 (next tick retries)
5. verify with GET /v1/models — log final loaded set
```

Every action gated. If the script runs every 120 s on a healthy machine, it's a no-op `curl` + `grep` 720 times a day — negligible.

The load body matches exactly the pattern in `docs/13-Apr-2026-lm-studio-reference.md` "Safe model swap pattern" step 4, including `flash_attention: true` and `parallel: 1`. Context length 8192 matches what the pipeline expects (see the 2026-05-04 doc note on post-UI-swap slowness).

### LaunchAgent: `com.farmguardian.lmstudio-watchdog.plist`

- `Label`: `com.farmguardian.lmstudio-watchdog`
- `ProgramArguments`: `/bin/sh /Users/macmini/Library/Application Support/farm-guardian/lmstudio-watchdog.sh`
- `RunAtLoad: true` — recover on login/boot in case the model was missing when the Mini came up.
- `StartInterval: 120` — re-check every 2 minutes. Short enough that a VLM outage is at most ~2 min of skipped cycles; long enough that the curl + grep load is irrelevant.
- `StandardOutPath` / `StandardErrorPath`: `/tmp/lmstudio-watchdog.agent.log`
- No `KeepAlive` — the script is short-lived; the interval is the watchdog.

### Why a fresh label (not reusing `com.farmguardian.lmstudio`)

The previous attempt at this lived under `com.farmguardian.lmstudio` (ripped out 2026-05-14 because I quit LM Studio mid-pipeline during a test — wrong approach, not because the agent itself was wrong). Per the relabel-fixes-TCC pattern documented in CLAUDE.md ("Recent Changes (14-Apr-2026)" section), I'm using a distinct label `com.farmguardian.lmstudio-watchdog` so any lingering TCC denial on the old label doesn't follow.

## Ordered TODOs

1. Write canonical copies to `deploy/mac-mini/lmstudio-watchdog.sh` + `deploy/mac-mini/com.farmguardian.lmstudio-watchdog.plist`.
2. Update `deploy/mac-mini/README.md` with a section describing the watchdog, the install path, the log location, the off-switch.
3. Add top entry to `CHANGELOG.md` (SemVer minor bump for new capability).
4. Add a cross-reference paragraph at the bottom of `docs/13-Apr-2026-lm-studio-reference.md` pointing at this plan + the deployed script as the canonical implementation of the safe-load pattern.
5. Install: `mkdir -p ~/Library/Application\ Support/farm-guardian` ; copy script ; `chmod +x` ; copy plist to `~/Library/LaunchAgents/` ; `launchctl load`.
6. Verify: `launchctl kickstart -k`, tail `/tmp/lmstudio-watchdog.log` — confirm "ok — already loaded" line. Confirm `agent.log` is empty (no stderr).
7. Commit + push (per memory: solo-operator, push to main).

## Verification plan

- **Happy path (model already loaded — current state):** kickstart, expect log line "ok — already loaded", no API call to `/api/v1/models/load`, no churn.
- **Co-tenant path:** not actively triggered, but the code branch logs the right message — verified by code review.
- **Recovery path:** not deliberately triggered (won't unload the live model to test — that was the 2026-05-14 mistake). The path will be exercised in production the next time LM Studio drops the model, and the log line will record it. If it doesn't fire correctly when that happens, fix it then.
- **Off-switch verification:** `launchctl unload ~/Library/LaunchAgents/com.farmguardian.lmstudio-watchdog.plist && rm ~/Library/LaunchAgents/com.farmguardian.lmstudio-watchdog.plist` — same shape as every other Farm Guardian LaunchAgent.

## Docs / changelog touchpoints

- `deploy/mac-mini/README.md` — new section: `com.farmguardian.lmstudio-watchdog`
- `CHANGELOG.md` — top entry, SemVer minor
- `docs/13-Apr-2026-lm-studio-reference.md` — append a "Watchdog implementation" pointer at the very bottom

## Off-switch (always documented)

```
launchctl unload ~/Library/LaunchAgents/com.farmguardian.lmstudio-watchdog.plist
rm ~/Library/LaunchAgents/com.farmguardian.lmstudio-watchdog.plist
rm "$HOME/Library/Application Support/farm-guardian/lmstudio-watchdog.sh"
```

The repo copies stay (source of truth).

## Approval

Awaiting Boss's go-ahead before implementing TODOs 1–7.
