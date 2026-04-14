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
