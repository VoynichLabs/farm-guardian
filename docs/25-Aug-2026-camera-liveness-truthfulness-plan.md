# 25-Aug-2026 — Make camera liveness tell the truth, and alert when it doesn't

**Status: PLAN, awaiting Boss approval. Not implemented.** Written immediately after the
`s7-cam` guest-network incident ([`docs/25-Aug-2026-s7-guest-network-incident.md`](docs/25-Aug-2026-s7-guest-network-incident.md)),
which was invisible for 16½ hours because of the two defects below.

## Scope

**In:** (1) make `online` reflect recent capture success; (2) alert when any camera goes stale.
**Out:** anything about the S7 itself — that incident is closed. No changes to camera configs,
no router changes, no new capture logic.

## Why this is not already done

Both are real bugs found on 25-Aug, but neither is a safe unattended edit:

- **`online` is a cross-repo semantic change.** `farm-2026`'s `app/components/guardian/types.ts`
  consumes this field, and CLAUDE.md requires API response changes be coordinated with it.
  Changing what `online` *means* changes the public site's camera tiles for all six cameras.
- **A new Discord alerter that is too chatty is worse than no alerter.** The whole design point
  of `birdcatraz-watchdog` is one alert per outage. That threshold deserves a decision, not a
  guess made while Boss is away.

## Defect 1 — `online` is discovery state, not liveness

`CameraInfo.online` (`discovery.py:77`) is set during discovery and read straight through to
`/api/cameras` (`dashboard.py:200`). Capture success never feeds back into it. Result: `s7-cam`
reported `online=true` through its **2,830th consecutive** `Host is down` failure.

**Proposed:** derive `online` from "produced a frame within the last N intervals" rather than
"discovery reached it once." Keep the field name and boolean type so the response shape is
unchanged — only the truth value changes.

**Decisions needed from Boss:**
- Staleness threshold. Suggest `max(3 × snapshot_interval, 60s)` so a slow camera is not flapped.
- Whether the public site should show a stale camera as offline (it would today show it online).

**Verification:** kill one camera's feed, confirm `/api/cameras` flips to `online=false` within
the threshold and back to `true` on recovery, and confirm the other five are unaffected.

## Defect 2 — nothing alerts on a stale camera

`com.farmguardian.birdcatraz-watchdog` watches **`farm-pi5` only**. It stayed green for the
entire outage — 135 clean ticks — because it was, correctly, reporting on a different device.
No component anywhere alerts on "a camera stopped producing frames."

**Proposed:** a sibling watchdog that queries the archive for the newest row per enabled camera
and alerts on any camera exceeding its staleness threshold. **Reuse
`tools/birdcatraz-watchdog/watchdog.py` wholesale as the model** — one alert per outage, one
recovery notice, state file, never a stream.

**Decisions needed from Boss:**
- Per-camera thresholds, since `s7-cam` (3s) and `house-yard` (5s) differ from the time-lapse lanes.
- Does it mention Boss? Suggest yes for a total blackout, no for a single camera.

**Traps already known — do not rediscover:**
- **Set an explicit `User-Agent`.** Discord 403s urllib's default `Python-urllib/3.x`.
  `tools/s7-battery-monitor/monitor.py` still carries this latent bug.
- The archive is the right liveness source, not `/api/cameras` — that is the field being fixed.

## TODOs (ordered)

1. Get Boss's answers on the four decisions above.
2. Implement Defect 1; verify against a deliberately-killed feed.
3. Coordinate the `types.ts` check in `farm-2026` before the change goes live.
4. Implement Defect 2 as a new tool + LaunchAgent, modelled on `birdcatraz-watchdog`.
5. Verify by killing a feed and confirming exactly one alert and one recovery notice.

## Docs/Changelog touchpoints

Both are behavior changes: CHANGELOG entry, the CLAUDE.md camera-roster/watchdog notes, and
`docs/SOCIAL_MEDIA_MAP.md` only if a new LaunchAgent lands in the daily schedule table.
