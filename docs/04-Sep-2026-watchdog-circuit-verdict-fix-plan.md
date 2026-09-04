# 04-Sep-2026 — Fix `birdcatraz-watchdog`'s false "circuit tripped" verdict

## Problem
On 03-Sep-2026 the watchdog told Boss to walk out and flip the Birdcatraz breaker. The circuit
was fine. He made the trip for nothing. Full evidence in
`docs/04-Sep-2026-farm-pi5-sd-card-failure-and-rebuild.md`.

`classify_outage()` is:
```python
verdict = "circuit" if down else "pi-only"
```
**One un-retried TCP probe failing on any single outdoor device flips the verdict to "circuit".**

Timing rules out packet loss as the mechanism: 6.018s elapsed for three sequential probes at
`PROBE_TIMEOUT_S = 6.0`, so at most one probe timed out and the other failed *immediately* — a
refusal, most likely connection-slot contention with Guardian's own polling. **Raising the
timeout would therefore not have prevented it.** A ">= 2 devices down" quorum would not have
either: two devices were reported down.

## Scope
**In:** `classify_outage()` and its helpers in `tools/birdcatraz-watchdog/watchdog.py`.
**Out:** the alert/latch/recovery shape, the probe of the Pi itself, the plist cadence, message
wording beyond naming the evidence used. No change to what an alert *says to do*.

## Architecture
Add a second, independent source of positive evidence: **did this camera actually archive a
frame recently?** That is a local SQLite read of `image_archive` — no network, no contention —
and it is the signal this repo already trusts over liveness flags (cf. `/api/cameras
online=true` while capture logged its 2,830th consecutive failure).

**Rule: a device is UP if EITHER it archived a frame within `3x` its median cadence OR its TCP
port answers. Only the absence of BOTH counts as down.** Both are independent positive proof of
power; only their joint absence is evidence of power loss.

The OR direction matters and is not symmetric — `s7-cam` on 03-Sep was genuinely not producing
frames (its charging lane) but the phone had power and answered TCP. Frame-recency alone would
have called it down. TCP alone called `house-yard`/`duo2` down. Each covers the other's blind spot.

Measured median cadences (04-Sep-2026): `house-yard` 45s, `duo2` 10s, `s7-cam` 5s.

Also: **retry each TCP probe once** before believing a failure. Cheap, and it independently
defends against the single-shot fragility that caused this.

Reuse: `tcp_open()` unchanged; `load_outdoor_devices()` unchanged. Stdlib-only constraint
preserved — `sqlite3` is stdlib. DB opened **read-only with a short timeout**, and any failure
(locked, missing, corrupt) degrades to TCP-only rather than raising, so a broken DB can never
stop an alert.

## Verification
Replay the 03-Sep 14:31:51Z moment: expect `house-yard` UP (frame 27s old), `duo2` UP (frame 0s
old), `s7-cam` UP (frame stale, TCP open) -> `down = []` -> verdict **"pi-only"**.
Then confirm a true circuit trip still classifies as "circuit" by simulating all three
unreachable with no fresh frames.

## Docs/Changelog
`CHANGELOG.md` v2.71.8; CLAUDE.md's watchdog banner updated to say the verdict is now
corroborated (replacing the 04-Sep "NOT trustworthy" warning); incident doc cross-linked.
