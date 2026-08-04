# 04-Aug-2026 — Make a camera host recover itself when the camera comes back

## Why

On 04-Aug the MacBook Air's built-in FaceTime camera was down for 11 hours. Those
11 hours are **two different problems**, and only the second one is ours to fix.

| Window | Length | What was true | Verdict |
|---|---|---|---|
| 04:12 → 12:47 | 8h 35m | Camera genuinely gone from the system — not in the AVFoundation list, not openable by anything | **Correct behaviour.** The service waited and retried. No software could have done better; the hardware was absent. |
| 12:47 → 15:29 | **2h 42m** | Camera **back and working** — a fresh process on the same machine opened it and read full 1280x720 | **This is the bug.** The running service could not open it and never would have. |

The boundary at 12:47 is not a coincidence. The dashcam log shows it re-acquired at
**12:46:56** — Boss walking out to replug the dashcam after it lost power. That replug
re-enumerated the USB bus and brought the built-in camera back with it, 8 seconds later.
So the camera was available again from 12:47. The service just couldn't take it.

**Nobody would have noticed.** The service answered `/health` the whole time, its grabber
thread was alive, and it logged busily every 4 seconds. It looked like a service working
hard on an absent camera. It was a service that could no longer see a camera sitting
right in front of it.

## What's actually missing

One choke point, `usb_cam_host.py::_open()`. Every acquisition path funnels through it,
and when it returns `None` the caller does exactly one thing, forever:

```
log "not currently available"  →  sleep 3s  →  try again        (usb_cam_host.py:1015-1031)
```

There is no ceiling on that loop. **A process that has become unable to acquire the
camera will retry in that same process until someone notices and restarts it.** It failed
13,179 times. The `READ_FAILURE_THRESHOLD` that already exists (5 strikes → release and
reopen) only covers reads on an *already-open* camera; nothing covers failing to open at all.

The plists already set `KeepAlive` — **launchd will hand us a fresh process the moment we
exit.** We never ask. That is the whole gap.

### Why not just fix the root cause instead

Because I can't name it honestly. I restarted the process, so the evidence of *why* that
particular process couldn't open the camera (leaked capture sessions? a poisoned
CoreMediaIO connection after the device was yanked mid-read?) is gone. What is certain and
reproducible is the shape: **fresh process works, old process doesn't, old process never
gives up.** The fix below doesn't require knowing which internal state rotted — it just
stops "retry forever in a process that will never succeed" from being an unbounded outage.
If the underlying cause resurfaces we'll have a second data point and can go deeper.

## Scope

**In:**
- Tell "camera is absent" apart from "camera is present but I can't acquire it". The code
  already knows the difference; it throws it away and logs one message for both.
- Give up and exit — only in the second case — after a bounded stall, so launchd restarts us.
- Surface the stall in `/health` so it's visible before it's an outage.

**Out:**
- **No change to the identity check.** It behaved correctly throughout: it refused to
  publish a camera it couldn't prove was FaceTime, which is exactly its job. Untouched.
- No change to any config, camera name, or reel lane.
- Not a substitute for the powered hub. This shortens an outage; it doesn't prevent one.

## Architecture

All three changes live in `tools/usb-cam-host/usb_cam_host.py`. No new module — this is
one loop gaining a ceiling, not a new responsibility. Deployed copy on the Air is
`~/.local/farm-services/usb-cam-host/`; all three cameras share the one file, so all three
get this.

**1. Distinguish absent from unacquirable.** `_resolve_verified_device_index()` returns
`None` for both "your camera isn't in the device list" and "it's in the list but no index
would open it". Return which. The caller currently logs *"device is not currently plugged
in"* in both cases — which was **flatly wrong for 2h42m on 04-Aug** and is precisely the
line that would send the next person looking at the hardware instead of the service. Fix
the message first; it has standalone value even if the rest is rejected.

**2. Bounded self-heal, gated on the camera being visible.**
- Camera **not in the device list** → current behaviour exactly, retry forever. Restarting
  would not have helped for one second of that 8h35m window, and a restart loop across it
  would be pure noise.
- Camera **in the list but unacquirable** for `USB_CAM_ACQUIRE_STALL_S` (default **300s**)
  → log loudly at ERROR, exit non-zero, let `KeepAlive` provide a fresh process.

Five minutes is chosen to sit well clear of normal transients: a successful identification
takes ~20-25s (ffmpeg reference capture plus index probes), and the sibling-instance
`START_DELAY` staggering is seconds. Genuine contention resolves inside one cycle.

**3. `/health` gets `acquire_stalled_s`** — how long we've been failing to acquire a camera
we can see, `0` when healthy. Makes the condition observable from the Mini without SSH.

### Risk

If a fresh process also can't acquire, we get a restart every ~5 minutes instead of one
silent 11-hour outage. That's noisy, self-announcing, and strictly better than the current
failure. launchd's own throttle keeps it cheap. The identity guarantee is unaffected — a
fresh process still refuses to publish a camera it can't prove.

## TODOs

1. Return a reason from `_resolve_verified_device_index()`; fix the misleading
   "not currently plugged in" log line. *(Standalone value — smallest useful change.)*
2. Add the stall timer to the grabber loop, gated on device-visible; exit non-zero past the
   threshold.
3. Expose `acquire_stalled_s` in `/health`.
4. Update the file header per repo standards.

## Verification (real hardware, no mocks)

1. **Absent camera must NOT restart-loop.** Point a scratch instance at a name that doesn't
   exist. Confirm it retries indefinitely and never exits — this is the 8h35m case and
   getting it wrong is the main way this change could make things worse.
2. **Reproduce the real fault.** Unplug the hub while the dashcam service is running to
   knock the built-in off (the 04-Aug trigger), replug, and confirm the FaceTime service
   recovers on its own within ~5 minutes with no human involvement. This is the actual test.
3. Confirm the recovered instance identifies by picture and serves the **right** camera —
   pull `/photo.jpg` and look at it. Resolution can't tell these two apart.
4. Confirm the other two services are undisturbed throughout.
5. Leave all three running overnight; confirm no spurious exits.

## Results — implemented and deployed 04-Aug-2026 (v2.61.0)

All five verification steps run against the live MacBook Air. Deployed file backed up to
`usb_cam_host.py.bak-20260804` on the Air.

| # | Test | Result |
|---|---|---|
| 1 | Absent camera must NOT restart-loop | **PASS** — scratch instance on a non-existent name, threshold 15s: alive past 75s (5×), `acquire_stalled_s: 0.0`, 23 "not currently available" logs, 0 stall logs. |
| 2 | Stall path exits | **PASS** — driven into the exact 04-Aug state (device visible, identification yields nothing): stall logged each second, `rc=1` at the 20s threshold. |
| 3 | Right camera served | **PASS** — both identified by picture (FaceTime → cv2 index 1, margin 13.1 vs 25.8) and confirmed **by eye**: 8089 is the run, 8091 is the wide garden view. Not swapped. |
| 4 | Other services undisturbed | **PASS** — all three redeployed and restarted; `macbook-air-facetime` and `jieli-dashcam` both `ok:true`, 0 failures. |
| 5 | No spurious exits overnight | **Outstanding** — check `acquire_stalled_s` and restart counts tomorrow. |

**Live confirmation of the dangerous case:** `usb-webcam-1080p` (physically off the USB bus
since 02-Aug) reports `acquire_stalled_s: 0.0` and sits quietly retrying. That is the 8h35m
scenario in production, behaving correctly — no restart loop.

**Ruled out along the way:** contention is not the mechanism. A second process opens the
built-in camera concurrently with the running service without trouble, measured. So the
04-Aug fault was in-process state, not another process holding the device.

**Not established:** why that process rotted. The evidence was destroyed by the restart. The
fix does not depend on it; if it recurs there will be a second data point.

## Docs / changelog

- `CHANGELOG.md` — new top entry, minor version (behaviour change: a service that can't
  acquire a visible camera now restarts itself).
- `HARDWARE_INVENTORY.md` — amend the severity-4 note added in `81315bb` to say this is now
  handled automatically, and how to tell if the self-heal itself is failing.
- This plan doc — record the verification results against it when done.
