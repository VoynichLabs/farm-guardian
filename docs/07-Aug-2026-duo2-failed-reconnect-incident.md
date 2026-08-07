# 07-Aug-2026 — the Birdcatraz circuit tripped, and duo2 could not come back on its own

**Root cause (from Boss, who fixed it): the outdoor circuit at Birdcatraz tripped from moisture.**
It powers *all* the outside gear. It does not reset itself — Boss walked out and physically
flipped the outlet's breaker back on at ~05:57.

**Status: RESOLVED.** Power restored by Boss ~05:57. duo2 restored to Guardian by a restart at
06:10. **The software defect that stopped Guardian from picking the camera back up is FIXED in
v2.66.0** (this doc's "The fix" section).

---

## ⚠️ Correction to the first version of this doc

The first writeup said the camera "was up the whole time" and called it a 3.5-hour software
outage. **That was wrong**, and it mattered. The camera was up *when I probed it at ~06:05* —
which was after Boss had already restored power at ~05:57. For most of the window duo2 was
genuinely dead, because its circuit was off.

Correct split:

| Window | What was actually wrong |
|---|---|
| 02:42 → ~05:57 (3h 15m) | **Power.** Circuit tripped. Nothing software could have done. |
| ~05:57 → 06:10 (13m, and open-ended) | **Software.** Power was back, camera was on the LAN and serving 4K JPEGs to curl — Guardian served nothing and would never have retried. |

The software defect is real and would have kept duo2 dark indefinitely. It is not what caused the
first three hours.

## The trigger — a tripped GFCI at Birdcatraz, not a camera fault

Moisture trips the outdoor circuit that feeds everything outside at Birdcatraz. **It stays tripped
until someone physically flips it.** Signature seen here:

- Multiple *unrelated* outside devices die at the same instant — duo2 (Reolink, own power brick)
  **and** both `farm-pi5` cameras were timing out together at 02:43. Different hardware,
  different hosts, one power feed.
- Indoor gear is unaffected: the Mac Mini had been up since 30-Jul, no reboot.
- `farm-pi5` boot time confirms restoration: **05:57:08**, matching when Boss flipped it.

**Check `uptime -s` on farm-pi5 against the Mac Mini's boot time.** Outside devices rebooting
while the Mini did not = the circuit tripped. That is a two-command diagnosis:

```bash
ssh markb@192.168.0.17 'uptime -s'; sysctl -n kern.boottime
```

### ⛔ Do not misread this as any of the following

- **Not a Reolink power-adapter failure.** That trap (CLAUDE.md) is real but presents as *one*
  camera absent from the network. Here several devices on one circuit died together.
- **Not "the camera is probably powered off" being trustworthy.** Guardian's discovery logs that
  line whenever its *name sweep* misses the camera. It says nothing about the configured IP.
  When I probed at 06:05, all four Reolink ports were open and the camera served 3.9 MB JPEGs —
  while Guardian was still logging "probably powered off" every 5 minutes.
- **Not the stale-RTSP case from 05-Aug-2026.** That is `CameraCapture` holding a dead socket.
  duo2 is on the **snapshot** path (`source: snapshot`, `snapshot_method: reolink`), and this
  survived a process restart, which a stale in-process socket cannot.

## Why Guardian could not recover on its own

**A failed connect still registered the poller, and registration is what disables the retry.**

1. **02:42** — power dies. Calls start failing the 10s cap.
   Log: `camera_control: Async camera operation failed: ` — **empty message**. That is
   `concurrent.futures.TimeoutError`, whose `str()` is `''`, logged with `%s`. The single fact
   identifying the failure was erased.
2. **03:00** — Guardian restarted **on its nightly schedule**
   (`com.farmguardian.guardian-restart.plist`, `StartCalendarInterval` 03:00), landing squarely
   inside the outage. `connect_camera` timed out; no `Host` was stored:
   `Failed to connect to camera 'duo2' at 192.168.0.155: _run_async returned None`.
3. **03:00:44** — Guardian **registered duo2's snapshot poller anyway**.
   `_register_camera_capture` built a `ReolinkSnapshotSource` without checking whether the
   connect had succeeded.
4. **Then, silence.** `take_snapshot` found no host and returned `None` **with no log line at
   all** — hours of `snapshot returned None` and zero diagnostics naming the cause.
5. **05:57 — power comes back, and nothing changes.** The 300s re-scan only reconnects cameras
   matching `if cam.name not in active`. duo2 had been in `active_cameras` since step 3, so its
   reconnect branch was permanently dead. `house-yard` recovered by itself at 06:02 for exactly
   the opposite reason — it was *not* in `active`.

That step-3/step-5 interaction is the whole bug: **registering a broken camera is what prevents
it from ever being fixed.**

## The fix (v2.66.0)

Three defects — the root cause, the thing that hid it, and the thing that made the log useless.

1. **`guardian.py` `_register_camera_capture`** — refuses to register a `ReolinkSnapshotSource`
   when `CameraController.is_connected()` is False. Logs an error and returns `False`, leaving
   the camera out of `active_cameras` so the 300s re-scan retries `connect_camera` and registers
   it once the camera is actually back. **This is the root-cause fix**: with it, duo2 would have
   self-healed within 5 minutes of Boss flipping the breaker, with no restart.
2. **`camera_control.py` `take_snapshot`** — the no-host branch now emits a warning naming the
   camera and saying the connection was never established, throttled to one per camera per 300s
   (pollers tick every 2-5s; unthrottled this is ~17k lines/hour).
3. **`camera_control.py` `_run_async`** — catches `concurrent.futures.TimeoutError` explicitly
   and says so with the timeout value; all other exceptions now log `type(exc).__name__` too.
   `Async camera operation failed: ` with a bare colon is gone.

Also added: **`CameraController.is_connected(camera_id)`** — a public wrapper over `_get_host`,
so callers stop reaching into controller internals to answer this.

### Verified

- `py_compile` clean on both files.
- Unit: a controller with no host → `is_connected()` False, `take_snapshot()` returns `None`
  **and logs the new warning**.
- Integration, against the live farm: Guardian restarted, both Reolink cameras connected *before*
  registering, and both served real frames through Guardian's own API
  (`duo2` 7.5 MB, `house-yard` 1.3 MB).
- **Not verified end-to-end:** the negative path against a real outage — that would mean cutting
  power to duo2 deliberately. The logic is exercised at unit level only. Next time the circuit
  trips, confirm duo2 self-heals within ~5 minutes of power returning; if it does, this is proven.

### How to verify a healthy start — check ORDERING, not absence of warnings

```bash
grep -E "Connected to camera|registered in snapshot mode \(method=reolink\)" guardian.log | tail -4
```

`Connected to camera '<id>'` must appear **before** that camera's registration line. That is
exactly the ordering missing at 03:00. "No more `snapshot returned None`" is weaker — a freshly
restarted poller looks identical to a healthy one for its first few seconds.

## Still open

**The nightly 03:00 restart remains.** It is not the bug and v2.66.0 removes its sting — a camera
that fails to connect at 03:00 now reconnects on its own within 5 minutes. But it is worth asking
whether Guardian needs a nightly kickstart at all; it exists to paper over something nobody has
written down.

## Latent trap — duo2's password is NOT the one in `.env`

Verified 07-Aug-2026: `CAMERA_PASSWORD` in `.env` authenticates against **house-yard only**.
Against duo2 it returns `"detail": "password wrong", rspCode -7`. duo2's password lives only in
its `config.json` block; house-yard's block holds the placeholder `YOUR_CAMERA_PASSWORD`.

The overlay at `guardian.py` replaces a camera's password from `CAMERA_PASSWORD` only when it is
empty or contains `"YOUR_"`. duo2 survives purely because its literal value matches neither.

**⛔ Do NOT "sanitize" duo2's `config.json` entry to match the placeholder policy.** That would
stamp house-yard's password onto duo2 and kill it with `password wrong`. The overlay must become
per-camera first. (To be clear, this is about *correctness*, not secrecy — Boss is explicit that
these are chicken cameras and plaintext passwords in the repo are fine.)

## Unrelated

The `ptz_save_preset` / `get_presets` rework in `camera_control.py` (v2.65.0, 06-Aug-2026) was
already in the working tree and played no part in this failure. It is committed alongside these
changes but is a separate change.
