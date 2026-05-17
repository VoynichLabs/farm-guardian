# Mac Mini — Bubba — deploy artifacts

Canonical copies of the LaunchAgents that run on Bubba (the Mac Mini M4 Pro running Farm Guardian). Source-controlled here so a future rebuild has a known-good config to return to.

## `com.cloudflare.tunnel.farm-guardian.plist`

The Cloudflare Tunnel that publishes `http://localhost:6530` at `https://guardian.markbarney.net`. Runs as a user LaunchAgent under `macmini`.

**Installed at:** `~/Library/LaunchAgents/com.cloudflare.tunnel.farm-guardian.plist`

**The token is REDACTED in this copy.** Never commit the real tunnel token — farm-guardian is a public repo. Before deploying from this file to a fresh machine:

1. Replace `REDACTED_TUNNEL_TOKEN` in the plist with the actual connector token from the Cloudflare Zero Trust dashboard (`Networks → Tunnels → farm-guardian → Connectors → install with token`).
2. Copy to `~/Library/LaunchAgents/`.
3. Load: `launchctl load ~/Library/LaunchAgents/com.cloudflare.tunnel.farm-guardian.plist`.

**Why `--protocol quic`** — see CHANGELOG v2.23.0 and the 13-Apr-2026 incident note: the earlier `--protocol http2` flag caused sustained `http2: stream closed` errors that dropped 70–90% of tunnel requests (visible to users as cameras flapping offline on the Railway-hosted frontend). QUIC is cloudflared's default and is substantially more reliable for this workload. Do not flip back to http2 without a reason.

**Logs:** `/tmp/cloudflared-guardian.log` (plist's `StandardErrorPath` / `StandardOutPath`). Grep for `level=error` / `stream closed` / `context canceled` if tunnel delivery misbehaves.

**Reload after edits:**

```bash
PLIST=~/Library/LaunchAgents/com.cloudflare.tunnel.farm-guardian.plist
launchctl unload "$PLIST"
launchctl load "$PLIST"
```

**Verify tunnel health:**

```bash
# 10 quick probes; anything less than 10/10 means the tunnel is misbehaving.
for i in $(seq 1 10); do
  curl -s -o /dev/null -w "  #$i: HTTP %{http_code} · %{time_total}s\n" --max-time 5 \
    "https://guardian.markbarney.net/api/status?t=$(date +%s%N)"
  sleep 0.5
done
```

## `com.farmguardian.lmstudio-watchdog.plist`

Auto-recovers the pipeline's VLM when `qwen/qwen3.5-9b` drops out of LM Studio. Solves the recurring "no model loaded → pipeline silently skips every cycle → human has to load it manually in the LM Studio UI" failure mode (the same one called out in CHANGELOG v2.40.14 where it ballooned `/tmp/pipeline.err.log` to 814 MB). Runs as a user LaunchAgent under `macmini`.

**Plan:** `docs/16-May-2026-lmstudio-watchdog-plan.md`. **Reference (Safe model swap pattern + the JIT-stays-OFF rule + the 2026-04-13 watchdog-reset incident):** `docs/13-Apr-2026-lm-studio-reference.md`.

**Files (canonical copies in this repo):**

- `lmstudio-watchdog.sh` — the watchdog script.
- `com.farmguardian.lmstudio-watchdog.plist` — the LaunchAgent.

**Installed at:**

- Script: `~/Library/Application Support/farm-guardian/lmstudio-watchdog.sh` (NOT `~/Documents/` — macOS TCC blocks launchd from executing files there; exit 126 "Operation not permitted").
- LaunchAgent: `~/Library/LaunchAgents/com.farmguardian.lmstudio-watchdog.plist`.

**Bootstrap:**

```bash
SUPPORT="$HOME/Library/Application Support/farm-guardian"
mkdir -p "$SUPPORT"
cp deploy/mac-mini/lmstudio-watchdog.sh "$SUPPORT/lmstudio-watchdog.sh"
chmod +x "$SUPPORT/lmstudio-watchdog.sh"
PLIST=~/Library/LaunchAgents/com.farmguardian.lmstudio-watchdog.plist
cp deploy/mac-mini/com.farmguardian.lmstudio-watchdog.plist "$PLIST"
launchctl bootstrap gui/$(id -u) "$PLIST"
```

**Cadence:** `RunAtLoad` (recovers on boot/login) + `StartInterval 120` (re-checks every 2 minutes). `ThrottleInterval 60` keeps it from being spammed by launchd.

**Logs:**

- `/tmp/lmstudio-watchdog.log` — the script's own log: one line per tick (`tick`, `ok — already loaded`, `loading...`, `load response`, `post-load loaded`, or `server unreachable / co-tenant / insufficient memory` if a guard fires).
- `/tmp/lmstudio-watchdog.agent.log` — launchd-captured stderr. Should be empty on a healthy install; anything in there is the LaunchAgent failing to even invoke the script (TCC denial, missing path, etc.).

**Hard rules the script obeys (do not relax without re-reading `docs/13-Apr-2026-lm-studio-reference.md`):**

1. Never restart, quit, or otherwise touch LM Studio itself — only the loaded-model state. If the server is unreachable, log and exit; this is not in scope to start the server.
2. Never unload another model. If some model other than `qwen/qwen3.5-9b` is loaded, log "co-tenant" and skip — see the coordination rule.
3. Always load via `POST /api/v1/models/load` with explicit `context_length=8192`, `flash_attention=true`, `parallel=1` (matches what the pipeline expects per the 2026-05-04 doc note on post-UI-swap slowness — UI loads at default 131k context, which makes inference 3–4× slower).
4. Free-memory gate before loading: `free + speculative + inactive` pages must clear ~1.4× the model footprint (≈9.2 GB for the 6.55 GB qwen).
5. Idempotent: on a healthy machine each tick is a `curl` + `grep` no-op.

**Off-switch (no behavior side-effects — the watchdog never modifies anything when healthy, so removing it just means losing the auto-recovery):**

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.farmguardian.lmstudio-watchdog.plist
rm ~/Library/LaunchAgents/com.farmguardian.lmstudio-watchdog.plist
rm "$HOME/Library/Application Support/farm-guardian/lmstudio-watchdog.sh"
```

**Verify health:**

```bash
launchctl list | grep com.farmguardian.lmstudio-watchdog   # col2 should be 0
tail -5 /tmp/lmstudio-watchdog.log                          # expect 'ok — already loaded'
cat /tmp/lmstudio-watchdog.agent.log                        # expect empty
```
