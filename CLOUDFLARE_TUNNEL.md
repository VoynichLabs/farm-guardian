# Cloudflare Tunnel Setup — Farm Guardian

**Author:** Bubba (Claude Sonnet 4.6)
**Date:** 04-April-2026
**Status:** WORKING — tunnel live via HTTP/2, LaunchAgent persists across reboots

---

## Goal

Expose the Guardian dashboard (running on Mac Mini at `localhost:6530`) to the public internet via Cloudflare Tunnel, so anyone can view the live camera feed at `guardian.markbarney.net`. No port forwarding, no router changes — outbound-only encrypted tunnel through Cloudflare's edge network.

## What's Already Done

### 1. cloudflared installed
```bash
brew install cloudflared
cloudflared --version  # 2026.3.0
```

### 2. Cloudflare Account
- **Account ID:** `81d30569eb85075e41114d4ba9aa8217`
- **Zone:** `markbarney.net` (Zone ID: `fc11fcbfd5d2b54eca2af64af8b3a15f`)
- **DNS is on Cloudflare** — nameservers active, all existing records managed there

### 3. API Tokens (stored in `~/.zshrc` on Mac Mini)
Two tokens were created. **Both will be rotated after initial setup — do not hardcode these anywhere.**

- `CLOUDFLARE_API_TOKEN` — has **Tunnel Edit** permission (Account scope). Used for tunnel CRUD.
- A second token with **DNS Edit** permission (Zone: markbarney.net). Used for DNS record creation.

The tunnel token expires **2026-04-18**. If you're reading this after that date, you need a new one from [dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens).

**Required token permissions:**
- Account → Cloudflare Tunnel → Edit
- Zone → DNS → Edit (for markbarney.net)

### 4. Tunnel Created
- **Tunnel Name:** `farm-guardian`
- **Tunnel ID:** `eb766f6f-6777-43a1-8017-22fca1ed8123`
- **Tunnel Token:** stored in `~/.zshrc` as `CLOUDFLARE_TUNNEL_TOKEN` (base64-encoded, contains account ID + tunnel ID + secret)
- **Created via API** — this is a remotely-managed tunnel (config lives on Cloudflare, not local file)

### 5. DNS Record Created
- **Record:** `guardian.markbarney.net` → CNAME → `eb766f6f-6777-43a1-8017-22fca1ed8123.cfargotunnel.com`
- **Proxied:** Yes (orange cloud)
- **DNS Record ID:** `aea2d27547d6001055696488c5044ac5`

### 6. Tunnel Ingress Config (set via API)
```json
{
  "ingress": [
    {"hostname": "guardian.markbarney.net", "service": "http://localhost:6530"},
    {"service": "http_status:404"}
  ]
}
```

### 7. Credentials File
Written to `~/.cloudflared/eb766f6f-6777-43a1-8017-22fca1ed8123.json` on the Mac Mini:
```json
{
  "AccountTag": "81d30569eb85075e41114d4ba9aa8217",
  "TunnelID": "eb766f6f-6777-43a1-8017-22fca1ed8123",
  "TunnelSecret": "<REDACTED — stored on disk only>"
}
```

Config file at `~/.cloudflared/config.yml`:
```yaml
tunnel: eb766f6f-6777-43a1-8017-22fca1ed8123
credentials-file: /Users/macmini/.cloudflared/eb766f6f-6777-43a1-8017-22fca1ed8123.json

ingress:
  - hostname: guardian.markbarney.net
    service: http://localhost:6530
  - service: http_status:404
```

---

## Root Cause (Resolved 04-April-2026)

QUIC connections on UDP port 7844 were being blocked — likely by the home router (TP-Link Archer AX55)
or ISP. Switching to `--protocol http2` (TCP port 443) fixed it immediately. All 4 connections
register to Cloudflare IAD data centers and stay alive indefinitely.

The fix was simply adding `--protocol http2` to the run command. The LaunchAgent plist already
had this flag but hadn't been reloaded after it was added.

---

## How to Run (Once Fixed)

### Quick test
```bash
cloudflared tunnel --protocol http2 run --token "$CLOUDFLARE_TUNNEL_TOKEN"
```

### As a LaunchAgent (persistent, survives reboot)
Create `~/Library/LaunchAgents/com.cloudflare.tunnel.farm-guardian.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.cloudflare.tunnel.farm-guardian</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/cloudflared</string>
        <string>tunnel</string>
        <string>--no-autoupdate</string>
        <string>--protocol</string>
        <string>http2</string>
        <string>run</string>
        <string>--token</string>
        <string>PASTE_TUNNEL_TOKEN_HERE</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/cloudflared-guardian.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/cloudflared-guardian.log</string>
</dict>
</plist>
```

Load it:
```bash
launchctl load ~/Library/LaunchAgents/com.cloudflare.tunnel.farm-guardian.plist
```

---

## Architecture Overview

```
[Camera 192.168.0.88]
      │ RTSP
      ▼
[Mac Mini - Guardian (localhost:6530)]
      │ MJPEG stream at /api/cameras/{name}/stream
      │ Dashboard UI at /
      │
      ▼
[cloudflared tunnel] ──outbound──▶ [Cloudflare Edge]
                                        │
                                        ▼
                              [guardian.markbarney.net]
                                        │
                                        ▼
                              [Public visitor's browser]
```

### Key Guardian Endpoints (exposed through tunnel)
- `GET /` — Dashboard UI
- `GET /api/cameras/{name}/stream` — MJPEG live feed (point `<img>` tag here)
- `GET /api/cameras/{name}/frame` — Single JPEG snapshot
- `GET /api/status` — System status JSON

### Farm Website Integration
The farm website (`farm.markbarney.net`, Next.js on Railway, repo: `VoynichLabs/farm-2026`) needs a "Live Cam" page that embeds:
```html
<img src="https://guardian.markbarney.net/api/cameras/FarmGuardian1/stream" alt="Live Feed" />
```

---

## Security TODO (Before Going Live)

1. **Add Cloudflare Access** — Put an auth layer in front of `guardian.markbarney.net` so only authorized users can view it. Free for up to 50 users. Configure at [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com/).
2. **Or add basic auth in Guardian** — Add HTTP Basic Auth middleware to the FastAPI app for the stream/dashboard endpoints.
3. **Rate limiting** — Consider Cloudflare rate limiting rules to prevent abuse of the MJPEG stream.

---

## Cloudflare API Quick Reference

```bash
# Verify token
curl "https://api.cloudflare.com/client/v4/accounts/81d30569eb85075e41114d4ba9aa8217/tokens/verify" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN"

# List tunnels
curl "https://api.cloudflare.com/client/v4/accounts/81d30569eb85075e41114d4ba9aa8217/cfd_tunnel" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN"

# Get tunnel status
curl "https://api.cloudflare.com/client/v4/accounts/81d30569eb85075e41114d4ba9aa8217/cfd_tunnel/eb766f6f-6777-43a1-8017-22fca1ed8123" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN"

# Update tunnel ingress config
curl -X PUT "https://api.cloudflare.com/client/v4/accounts/81d30569eb85075e41114d4ba9aa8217/cfd_tunnel/eb766f6f-6777-43a1-8017-22fca1ed8123/configurations" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"config":{"ingress":[{"hostname":"guardian.markbarney.net","service":"http://localhost:6530"},{"service":"http_status:404"}]}}'

# Delete tunnel (if starting over)
curl -X DELETE "https://api.cloudflare.com/client/v4/accounts/81d30569eb85075e41114d4ba9aa8217/cfd_tunnel/eb766f6f-6777-43a1-8017-22fca1ed8123" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN"

# List DNS records for markbarney.net
curl "https://api.cloudflare.com/client/v4/zones/fc11fcbfd5d2b54eca2af64af8b3a15f/dns_records" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN"
```

---

## Environment Variables (on Mac Mini, in ~/.zshrc)

```bash
export CLOUDFLARE_API_TOKEN="<rotated after setup — get new one from Cloudflare dashboard>"
export CLOUDFLARE_ACCOUNT_ID="81d30569eb85075e41114d4ba9aa8217"
# Tunnel token is base64-encoded JSON containing account ID, tunnel ID, and tunnel secret
export CLOUDFLARE_TUNNEL_TOKEN="<stored in ~/.zshrc on Mac Mini>"
```

---

## Files on Mac Mini

| Path | Purpose |
|------|---------|
| `~/.cloudflared/config.yml` | Tunnel config (hostname → service mapping) |
| `~/.cloudflared/eb766f6f-*.json` | Tunnel credentials (secret) |
| `~/.zshrc` | API tokens and tunnel token |
| `/opt/homebrew/bin/cloudflared` | cloudflared binary |

---

## Next Steps

1. ~~**Try `--protocol http2`**~~ — DONE, this was the fix
2. ~~**LaunchAgent**~~ — DONE, tunnel persists across reboots via `com.cloudflare.tunnel.farm-guardian.plist`
3. **Add auth** — Cloudflare Access (free tier) or Guardian-side basic auth before the dashboard goes fully public
4. **Live Cam page** — Add to `farm.markbarney.net` (VoynichLabs/farm-2026 repo)
5. **Test MJPEG stream performance** through the tunnel — may need to reduce frame rate or quality for public access
