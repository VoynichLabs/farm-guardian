# 13-Aug-2026 — Birdcatraz power watchdog (farm-pi5 quiet alert)

**Author:** Claude Opus 5
**Status:** ✅ SHIPPED 13-Aug-2026 (CHANGELOG v2.71.0). Live and loaded. See "Verification" at
the bottom for what was actually tested, and the User-Agent trap that nearly shipped broken.
**Requested by:** Boss, 13-Aug-2026, immediately after the Pi went down for ~70 minutes
unnoticed and was only found because he happened to ask.

## Why

`farm-pi5` has **no battery backup**. If it is unreachable, it is because it lost power —
which means the Birdcatraz circuit is out, which means the other outdoor devices are out too.
Boss's framing: *"That'll be the best indicator... if it's offline, a whole bunch of the other
cameras are."* The Pi is the cheapest, most reliable canary for an outdoor power event.

This is a real gap, not a hypothetical one. Tonight's outage:

| | |
|---|---|
| Last frame from both Pi cameras | 19:37 local |
| Boss asked about it | ~20:40 local |
| Pi rebooted after he walked out | 20:46 local |

**~70 minutes of silence, discovered by luck.** Nothing in the stack watches for a camera
host going quiet. CLAUDE.md's own top banner documents a 3¼-hour overnight outage on
07-Aug-2026 from the same circuit tripping on moisture — that one also went unnoticed until
morning.

## Scope

**In:**

- One small stdlib-only Python script, run on the Mac Mini by a LaunchAgent every 5 minutes.
- Probes `farm-pi5`'s two camera health endpoints. Alerts to Discord when it goes quiet,
  and again when it comes back.
- On failure, probes the other outdoor devices so the alert can say **"whole circuit"** vs
  **"just the Pi"** — this is the difference between a useful alert and a noisy one, and it
  is what Boss actually asked the alert to tell him.

**Out:**

- No changes to Guardian, the pipeline, or any camera host.
- No auto-remediation. A power cut cannot be fixed from the Mini; the alert exists so a human
  walks out sooner. (CLAUDE.md is explicit: *"Nothing on the Mini can fix this."*)
- No new dependency, no new database table, no new webhook.
- Not a general per-camera monitor. One host, one job. If Boss wants the other hosts watched
  later, the same script generalises — but scope creep here buys nothing, because the Pi is
  the canary that already implies the rest.

## Architecture

**New file:** `tools/birdcatraz-watchdog/watchdog.py` — stdlib only (`urllib`, `json`,
`socket`), no venv packages needed.

**Reuse, not reinvention.** The alert shape is lifted from
`tools/s7-battery-monitor/monitor.py`, which is the existing in-repo pattern for exactly this
job (poll → compare against a JSON state file → post a one-shot Discord message on a state
*transition*, and another on recovery). Reusing it means: no repeat alerts every 5 minutes
while an outage is ongoing, and a clear "back up" message that timestamps the recovery.

**Probe logic — deliberately belt-and-braces on addressing.** The Pi is on **DHCP with no
static lease reserved** (open TODO in the bring-up log). So:

- Try `farm-pi5.local` first — follows the host if its IP drifts.
- Fall back to `192.168.0.17` — survives an mDNS hiccup.
- Only declare the Pi down if **both** fail. Tonight's pipeline errors were mDNS name
  resolution failures; that was correct (host genuinely gone), but a name-only probe would
  false-alarm the first time mDNS alone gets flaky.

**Two consecutive failures before alerting** (≈10 minutes). One missed probe during a reboot
or a Wi-Fi blip is not an outage. The state file carries the failure count.

**Classification on failure** — probe `duo2` (192.168.0.155:554), `s7-cam`
(192.168.0.249:8080) and `house-yard` (192.168.0.88:80), addresses read from `config.json` so
there is one source of truth:

- Pi down **+ others down** → *"Birdcatraz circuit is out"*, and the message says the breaker
  needs flipping, per CLAUDE.md's top banner.
- Pi down **+ others fine** → *"just the Pi"* — its own power brick, cable, or the Pi itself.

**Alert destination:** `DISCORD_WEBHOOK_URL` (`#farm-2026`), posting as username
`farm-power`. **Safe for the gem reaction gate:** `discord-reaction-sync` only ingests
messages that carry an image attachment, and maps gem reactions by webhook username →
camera_id. A text-only post under an unmapped username is invisible to it.

**Schedule:** `com.farmguardian.birdcatraz-watchdog.plist`, `StartInterval` 300.

## TODOs

1. Write `tools/birdcatraz-watchdog/watchdog.py`.
2. Write the LaunchAgent plist; load it.
3. **Verify for real, not by reading the code:** run it against the live healthy Pi (expect
   silence), then simulate an outage by pointing it at a dead port and confirm exactly one
   Discord message arrives, that a second run stays quiet, and that recovery posts once.
4. Update `CLAUDE.md` (Pi section + LaunchAgent count) and `CHANGELOG.md`.
5. Note the still-open static-DHCP-lease TODO from the bring-up log — the watchdog works
   around it but does not fix it.

## Decisions (Boss, 13-Aug-2026) — APPROVED

1. **The outage alert @-mentions Mark** (`<@293569238386606080>`), so it pings his phone. A
   Birdcatraz power cut is worth waking up for — a quiet post is worth nothing at 3am. The
   **recovery message does NOT mention him**: "it's back" is not worth a ping, and mentioning
   on both halves is how an alert becomes noise people mute.
2. **Trigger after 2 consecutive failed checks (~10 minutes).** Rides out a reboot or a
   network blip, still catches an outage roughly an hour earlier than tonight's discovery.

## Verification (13-Aug-2026 — all run for real, not reasoned about)

| Check | Result |
|---|---|
| Healthy Pi → silent, exit 0 | ✅ `Pi alive — farm-pi5.local: usb-webcam-1080p, jieli-dashcam healthy` |
| Tick 1 of an outage | ✅ silent (`consecutive_failures: 1`) |
| Tick 2 | ✅ **exactly one** alert posted, `alerted: true` |
| Ticks 3 and 4 | ✅ still silent — the latch holds, no 5-minute alert spam |
| Recovery | ✅ one un-mentioned "back up" notice with the outage duration |
| Classification reads `config.json` | ✅ resolved `house-yard:80`, `s7-cam:8080`, `duo2:554` and probed them live |
| Runs under launchd (system python3, minimal env) | ✅ exit 0, empty stderr |
| Webhook loads with an empty environment | ✅ `.env` fallback works — this is the path that actually runs in production |
| End-to-end Discord delivery + @-mention | ✅ delivered, confirmed by Boss's phone |

**⚠️ Trap found during verification — the reason end-to-end testing was not optional.** The
first real delivery attempt returned **`HTTP 403 Forbidden`**. Not a bad webhook, not a
permissions problem: **Discord's edge rejects urllib's default `Python-urllib/3.x`
User-Agent.** Verified by posting the identical body twice, once with a custom UA (`200 OK`)
and once without (`403`). Every other Discord caller in this repo uses `requests`, which sets
its own UA, so this only bites stdlib callers — and it would have failed *silently at the
worst possible moment*, during a real outage, with the alert logged as sent-and-failed and
nobody watching. `watchdog.py` now sets an explicit User-Agent with a comment saying not to
remove it.

**Known latent instance of the same bug:** `tools/s7-battery-monitor/monitor.py` posts to
Discord through bare `urllib` with no User-Agent. It has been disabled since 26-Apr-2026, so
the bug has never surfaced — but anyone reviving that script must add the header first, or its
alerts will 403 into the void.

## Still open (not introduced here, worth doing)

- **`farm-pi5` has no static DHCP lease** (`88:a2:9e:a2:e6:23` → `192.168.0.17` on the Archer
  AX55) — an open TODO from the bring-up log. The watchdog works around it by probing the mDNS
  name *and* the known IP, but if the IP drifts the fallback silently stops being a fallback.
