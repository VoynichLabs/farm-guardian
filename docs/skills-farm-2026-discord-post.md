# Post Camera Frames to the #farm-2026 Discord Channel

**Last updated:** 16-April-2026
**Cross-refs:** `CHANGELOG.md` (v2.27.8 initial wiring, v2.27.9 incident context) · `docs/16-Apr-2026-s7-ipwebcam-frozen-incident.md`

## Purpose

Post a live camera frame from Farm Guardian to the **#farm-2026** Discord channel. Boss set this flow up on 2026-04-16 and explicitly asked that any future agent working in this repo know how to do it without having to reverse-engineer the wiring. Good S7 brooder portraits are the usual case (the S7 is the only real camera in the fleet — 12 MP Sony IMX260 on a Galaxy S7), but the same pattern works for any camera Guardian exposes via `/api/cameras/<name>/frame`.

**Broad strokes only — no credentials in this doc.** The webhook URL is a bearer token; it lives in `.env` (gitignored). Channel IDs and the webhook *name* are fine to put in writing (Boss confirmed); the URL itself is not. If you ever leak the URL into a committed file, the fix is (a) regenerate the webhook in Discord, (b) rewrite history to excise the leak.

---

## Quick reference (the 5-second version)

```bash
# 1. Pull the freshest frame for whichever camera you want.
curl -s -m 10 -o /tmp/post.jpg \
  "http://localhost:6530/api/cameras/s7-cam/frame"

# 2. Post to #farm-2026 with a multipart attachment.
source ~/Documents/GitHub/farm-guardian/.env  # loads DISCORD_WEBHOOK_URL
curl -s -m 20 \
  -F 'payload_json={"username":"S7 Brooder","content":"Latest from the brooder — S7, /photoaf.jpg, 1920x1080."}' \
  -F "file=@/tmp/post.jpg" \
  "$DISCORD_WEBHOOK_URL" \
  -w "\nHTTP:%{http_code}\n"
```

Expected: `HTTP:200`. The response body is the Discord message JSON with CDN URL. No 200 means something changed — don't retry blindly, read the error.

---

## Background you need once

### The webhook

- **Webhook name:** `Farm Guardian` (Boss-created)
- **Guild ID:** `1471632570616643657` (the farm Discord server — shared across agents)
- **Channel ID:** `1482466978806497522` — confirmed to be `#farm-2026` via the Discord webhook GET endpoint.
- **URL lives in:** the repo's `.env` file (gitignored), key `DISCORD_WEBHOOK_URL`. Also injected into the `com.farmguardian.s7-battery-monitor.plist` LaunchAgent's `EnvironmentVariables` on whichever Mac hosts the battery monitor. Never hardcode it anywhere else.
- **If it leaks:** regenerate in Discord → Channel Settings → Integrations → Webhooks, then update `.env` and the LaunchAgent plist. Any prior commit containing the leaked URL has to be rewritten out of history.

Verify the webhook's channel any time with:

```bash
source ~/Documents/GitHub/farm-guardian/.env
curl -s "$DISCORD_WEBHOOK_URL" | python3 -c "import json,sys;d=json.load(sys.stdin);print(f\"name={d['name']} channel_id={d['channel_id']} guild_id={d['guild_id']}\")"
```

If `channel_id` ever comes back as anything other than `1482466978806497522`, stop and re-check before posting — someone may have rewired the webhook.

### The frame source

Guardian's `/api/cameras/<name>/frame` returns the camera's latest good JPEG, re-encoded at server-default quality. Fleet as of v2.27.8:

| Camera | What you'll get | Best use |
|---|---|---|
| `s7-cam` | Samsung Galaxy S7 (Sony IMX260, 12 MP f/1.7), 1920×1080, AF-triggered per pull, heat-lamp-corrected WB. | Baby-bird portraits, gem-quality shots. This is the only real camera in the fleet. |
| `house-yard` | Reolink E1 Outdoor Pro, 4K snapshot. | Wide yard/sky shot — predator watch angle. |
| `mba-cam` | MacBook Air 2013 FaceTime HD, 1280×720. | Wide overhead of the brooder — overview, not portraits. |
| `usb-cam` | Generic USB webcam on the Mini, 1920×1080 nominal. | Low-end; only post if the angle is unique. |
| `gwtc` | Laptop built-in webcam, 1280×720. | Coop interior; rarely post-worthy. |

For the public channel, prefer `s7-cam`. `house-yard` is fine if there's an interesting sky / yard moment.

### The caption / username conventions

Match Boss's taste — terse, technical, honest. The original post that impressed him:

> **S7 Brooder:** Fresh S7 shot — v2.27.7 tuning (continuous-picture AF + incandescent WB + /photoaf.jpg + 60s cadence). Chicks are sharp, the wall reads as its actual neutral, no more orange drown.

Templates:

- **Routine gem:** `"Latest from the brooder — S7, /photoaf.jpg, 1920×1080."`
- **After a tuning change:** mention the specific change (as in the example above).
- **After a recovery:** `"S7 back up after <N> min — {brief reason}. First frame since recovery:"`

Username: match the camera — `S7 Brooder` for s7-cam, `Yard` for house-yard, `Brooder Overhead` for mba-cam. Keep it short; it's the bold lead the message opens with.

### Attachment size / format

Discord caps webhook attachments at 25 MB (actually lower if the guild isn't Nitro-boosted, ~10 MB safe default). Guardian frames are 200 KB – 1 MB, so we're fine. JPEG is the only format you need; don't re-encode.

---

## Full command, copy-paste-ready

```bash
#!/usr/bin/env bash
set -euo pipefail

CAMERA="${1:-s7-cam}"
USERNAME="${2:-S7 Brooder}"
CAPTION="${3:-Latest from the brooder — /photoaf.jpg}"
OUT=/tmp/farm2026-post-$$.jpg

# 1. pull fresh frame
curl -sS -f -m 10 -o "$OUT" "http://localhost:6530/api/cameras/$CAMERA/frame"

# 2. load webhook URL from the guardian .env (not hardcoded)
source ~/Documents/GitHub/farm-guardian/.env

# 3. post to #farm-2026
curl -sS -f -m 20 \
  -F "payload_json={\"username\":\"$USERNAME\",\"content\":\"$CAPTION\"}" \
  -F "file=@$OUT" \
  "$DISCORD_WEBHOOK_URL" \
  -o /dev/null -w "%{http_code}\n"

rm -f "$OUT"
```

Drop it anywhere on the host (e.g. `tools/farm-2026-discord-post/post.sh`), `chmod +x`, then:

```bash
./post.sh                            # defaults: s7-cam, "S7 Brooder" username
./post.sh house-yard "Yard"          # house-yard with custom username
./post.sh s7-cam "S7 Brooder" "Custom caption goes here"
```

---

## Failure modes (what I hit, so you don't)

- **Webhook 401 / 404:** the URL in `.env` is wrong or the webhook was regenerated. Re-pull from Discord → channel → integrations.
- **`/api/cameras/<name>/frame` returns 404:** Guardian hasn't captured a frame yet (first-poll-pending; happens after a restart, especially for 60 s-cadence cameras). Wait up to one interval and retry.
- **`/api/cameras/<name>/frame` returns a stale frame:** Guardian serves its cached last-good frame even when the actual camera source is down. If you suspect the image is old, cross-check `tail guardian.log | grep <camera>` for recent fetch failures. For the S7 specifically, the usual cause is the "IP Webcam on Configuration screen" failure — see `~/Documents/GitHub/farm-guardian/docs/16-Apr-2026-s7-ipwebcam-frozen-incident.md`.
- **`HTTP:200` but no message in Discord:** almost never happens. If it does, the webhook might be rate-limited (`429` masquerading, or the bot was removed from the channel). Check Discord directly.
- **Shell-quoting the caption:** the `payload_json` field is JSON inside a shell single-quoted string. If the caption contains a single quote, use the `post.sh` script's double-quoted form and escape accordingly, or pass via a temp JSON file:
  ```bash
  jq -n --arg u "S7" --arg c "Boss's favorite chick" '{username:$u,content:$c}' > /tmp/p.json
  curl -F "payload_json=<${tmp_json}" -F file=@$OUT "$DISCORD_WEBHOOK_URL"
  ```

## What NOT to do

- **Don't post non-gem frames just to prove the pipeline works.** Boss specifically flagged the USB cam as lower-tier on 2026-04-16 — the feed works, but the quality is not farm-2026 material. Stick to s7-cam + selective house-yard.
- **Don't post every hour on a timer without Boss's go-ahead.** The auto-forward (from VLM-scored "gems" in the pipeline) is a separate effort owned on a different branch; your role here is on-demand posting and one-offs unless Boss explicitly asks for a recurring schedule.
- **Don't hardcode the webhook URL in any committed file.** Always source `.env`.
- **Don't post images that contain Boss's name, location, or the farm's exact address.** `#farm-2026` is public-ish. Coop-only shots are fine; anything showing the house/yard from the Reolink, double-check for address placards or car plates. If in doubt, don't post.
