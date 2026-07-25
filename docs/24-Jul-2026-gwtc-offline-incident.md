# GWTC offline — 24-Jul-2026 incident diagnosis

**Status: RESOLVED 25-Jul-2026 06:06 EDT. GWTC recovered and `usb-cam` is healthy.**

Verified 25-Jul ~12:00 EDT: ports 22 / 8089 / 8554 all open at `192.168.0.69`, ARP entry
present (`f0:35:75:81:2c:45`), and `/health` reports the **correct** camera —
`device_index: 1`, `resolution: [1920,1080]`, `grabber_alive: true`, `total_failures: 0`.
That is the external USB camera, not a built-in; the label is trustworthy. Frames resumed
at `2026-07-25T10:06:17Z`.

**Total outage: ~10 hours** (19:59:34 EDT 24-Jul → 06:06 EDT 25-Jul).

The diagnosis below is retained because the failure is recurring — twice in the 18 hours
before it went down for good — and the next occurrence should start from here rather than
from scratch.

**⚠️ Open question that determines the permanent fix:** did GWTC self-heal overnight, or
did a human press the power button around 06:06? Outage #1 self-healed in ~90 min with no
intervention, so self-healing is proven possible on this box. If #2 also self-healed, H1
(power-cycling leaves it off) is weakened and this is a pure weak-signal WiFi problem. If a
human recovered it, H1 stands. **Answer this before spending money on a fix.**

**This doc exists to stop the next agent (or me) from re-running three theories that
are already dead, and to correct a self-blame narrative that the logs disprove.**

---

## Corrected timeline — the outage PREDATES any human intervention

This is the single most important fact, and the first two rounds of diagnosis got it
backwards.

Evidence, from `guardian.log` (`Camera 'usb-cam' — snapshot returned None (consecutive=N)`)
cross-referenced against `image_archive`:

| Local time (EDT) | Event |
|---|---|
| 02:27:30 | **Outage #1 begins.** Failure counter starts at C=1. |
| ~02:52 → 03:45 | Counter climbs to C=370, with a reset at 03:00 (brief flap). |
| ~04:00 – 19:59 | **Self-recovered with zero intervention.** ~16 hours of clean frames. |
| 19:59:34 | **Last `usb-cam` frame ever written** (`2026-07-24T23:59:34+00:00` UTC). |
| 20:00:13 | **Outage #2 begins.** Counter restarts at C=1. |
| *after* 20:00 | Only *then* was the hub unplugged and the machine power-cycled (4×). |
| 20:48:49 → now | C=370 and climbing. `Errno 64 Host is down`. |

**Consequences:**

1. **The unplug/power-cycle advice did not cause this outage.** The failure began at
   19:59:34, before anyone touched the machine. The intervention was a *response* to an
   outage already in progress. (The USB-boot theory behind that advice was still wrong —
   see Dead theories — but it is not the trigger, and the incident should not be written
   up as though it were.)
2. **This is the second outage today, and the first one self-healed in ~90 minutes.**
   That materially changes the urgency calculus. Outage #2 is not yet obviously different
   in kind from outage #1 — it is different in that #1 recovered and #2 (so far) has not.
3. **The frame cadence cut off mid-stream.** Last 8 rows are 2–3 s apart with no
   degradation, then nothing. That is an instantaneous loss (power or radio), not a
   gradual failure.

---

## CONFIRMED ROOT CAUSE (25-Jul, from GWTC's own event logs)

The hypotheses below were superseded by direct evidence pulled over SSH once GWTC was
reachable. **Read this section, not them.**

### 1. The machine was UP the entire time we believed it was dead

`Get-WinEvent` boot/shutdown events and `EventLog 6013` (uptime 52171 s at 12:00 on 25-Jul)
put continuous uptime from **21:30 EDT 24-Jul**. Every probe, and all four "power cycles",
were aimed at a machine that was running fine. We had no way to know because the screen,
keyboard and touchpad are all deliberately disabled.

### 2. WiFi ASSOCIATED but never got a DHCP lease — it sat on APIPA for 8.5 hours

From `Microsoft-Windows-WLAN-AutoConfig/Operational`:

```
21:30:55  11000  Wireless network association started
21:30:58  11001  Wireless network association succeeded
21:30:58  11005  Wireless security succeeded
21:31:00   8001  WLAN AutoConfig has successfully connected
```

The radio link was good. What failed was IPv4 address acquisition — no lease, so Windows
self-assigned `169.254.x.x`. That state is invisible on `192.168.0.0/24` **and absent from
the router's DHCP lease list**, which is exactly what we observed and mis-read as "off the
network." This is hypothesis H3 below, confirmed.

**Recovery mechanism:** plugging the USB hub back in at 06:06 caused a USB bus
re-enumeration that bounced the WiFi NIC (`InstanceId: USB\VID_0BDA&PID_D723&MI_02\...` —
the adapter is genuinely on the USB bus). Link down/up forced a fresh DHCP request, which
got a lease. The hub plug fixed the network *incidentally*, by bouncing the NIC.

### 3. `farmcam-wifi-watchdog` is NOT broken — that claim is WITHDRAWN

An earlier revision of this doc asserted the watchdog was "DEAD" because
`C:\farm-services\wifi-watchdog.log` has no entries after `2026-07-24 02:27:26`. **That was
wrong, and it is the same inference-from-absence error that produced the dead-battery and
"16 hours clean" mistakes.** Verified state on 25-Jul:

| Check | Result |
|---|---|
| `Get-ScheduledTaskInfo` | `State: Ready`, `LastRun: 12:39:39`, `LastResult: 0`, `NextRun: 12:41:41`, `Missed: 0` |
| Runs on schedule? | Yes — every 2 minutes, as designed |
| Detection logic correct? | **Yes, tested empirically** (below) |

**The script only writes to its log when it detects failure** (`if ($fails -ge 3)`). There is
no heartbeat line. So log silence means "no failure detected," *not* "did not run."

**The `ping.exe` exit-code theory is also dead.** The obvious suspicion was that `ping.exe`
returns 0 in the no-route/APIPA state, blinding the check. Tested directly on GWTC:

```
A: no route to subnet (the APIPA case)  -> "Request timed out."  exit 1  -> counted as FAIL ✓
B: real gateway, currently up            ->                       exit 0  -> not a fail    ✓
C: unused IP on local subnet             -> "Request timed out."  exit 1  -> counted as FAIL ✓
```

The detection works correctly in exactly the state GWTC was in. Theory discarded.

**Whether it ran during the 21:30 → 06:06 window is UNKNOWABLE.**
`Microsoft-Windows-TaskScheduler/Operational` has `IsEnabled: False` on this box, so there
is no run history. A query over that window returns 0 events, which is meaningless — the
log is switched off. **Do not cite that zero as evidence of anything.**

**The real defect is observability, not the watchdog.** Two cheap fixes, both desk jobs
over SSH, that would have made this whole incident a five-minute diagnosis:

1. **Enable Task Scheduler history** (`wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true`)
   so "did it run?" is answerable.
2. **Add a heartbeat line to `wifi-watchdog.ps1`** — log every run, not only failures, so
   silence becomes meaningful instead of ambiguous. This is the single change that would
   have prevented three wrong conclusions in this session.

### 4. Signal strength is NOT the problem — the docs are wrong

`netsh wlan show interfaces` on 25-Jul: **Signal 88%**, 72.2 Mbps rx/tx, 802.11n, 2.4 GHz,
channel 5, BSSID `5c:a6:e6:16:f1:0f`. CLAUDE.md and
`docs/18-Apr-2026-gwtc-current-state-and-install-walkthrough.md` both assert the coop sits
at "~34% signal" and build a whole weak-signal-driver-wedge narrative on it. **At 88% that
narrative does not apply to this incident.** Stop diagnosing GWTC dropouts as signal
problems without re-measuring first.

### 5. Separate fault: two unclean shutdowns — CAUSE UNKNOWN, do not guess

Windows recorded two `Kernel-Power 41` / `EventLog 6008` unexpected shutdowns on 24-Jul
(≈11:19 and ≈19:43–19:59), with the machine off ≈11:19→19:03 and ≈19:43→21:30.
`Win32_Battery` reports "WB Lion Battery", `EstimatedChargeRemaining=100`,
`BatteryStatus=2` — and per the WMI enum, **2 means the system is on AC**. That matches
Boss: GWTC is never off AC.

**⛔ An earlier revision of this doc asserted "dead battery / the box can't survive an AC
interruption." That was an unsupported inference and Boss corrected it. It is withdrawn.**
Kernel-Power 41 means only "the OS stopped without a clean shutdown" — it does **not**
identify a cause. On a machine that never leaves AC, the candidates are a hard hang, a
thermal trip (Celeron N4020, chicken coop, late July, dust and feathers in the vents), or a
firmware/driver fault. **None of these has been tested.** Do not write any of them into a
narrative until someone pulls thermal events / `Get-CimInstance` thermal zones, and note
that some of the reboots in this window may simply be Boss's own four power cycles.

### 6. Correction: the 24-Jul "16 hours of clean frames" claim was WRONG

An earlier revision claimed `usb-cam` ran cleanly from ~04:00 to 19:59 on 24-Jul. It did
not. Hourly counts from `image_archive` show frames in **exactly one hour** that entire
day:

```
07-24 19  |  806        <- 19:03-19:59 only
07-25 06  | 1513
07-25 07  | 1643
07-25 08  | 1639
07-25 09-12 | ~20/hr    <- cadence drop, separate question
```

The error came from reading *absence of failure entries* in `guardian.log` as evidence of
success. It is not — Guardian only logs `snapshot returned None` when it polls and fails.
A silent window means "no failures logged," which is not the same as "frames delivered."
**Always confirm uptime against `image_archive` counts, never against the absence of log
lines.**

Note on timestamps: Event 6008's "previous shutdown at 19:43:37" is derived from a
periodically-flushed registry value and lags the true crash by up to ~15 min, which
reconciles it with the last archived frame at 19:59:34. GWTC's clock is in sync with the
Mini (both 12:06:51 on 25-Jul) — there is no clock skew.

---

## Live hypotheses, ranked (SUPERSEDED — kept for the reasoning trail)

### H1 — Physical power-cycling is leaving the laptop OFF (new, untested, best fit)

A laptop that has been fully powered down does **not** boot when AC is restored, unless
the BIOS has an explicit "power on AC attach" setting enabled. If GWTC's battery is dead
or degraded (a 2020-era budget Celeron that lives plugged in 24/7 — entirely plausible),
then "unplug, wait, plug back in" leaves it **off**, and it stays off until the power
button is physically pressed.

**Why this fits better than anything else:**
- Explains why 4 power cycles produced no association at all, ever.
- Explains why outage #1 self-healed (nobody power-cycled it — the WiFi watchdog or the
  driver recovered on its own) while outage #2 has not (the power cycles took it from
  "wedged but running" to "off").
- Requires nothing to have spontaneously broken.

**Critical enabler: we have no feedback channel.** GWTC's screen, keyboard, and
touchscreen are all *deliberately* disabled (chickens). So Boss cannot distinguish
"powered on with no network" from "completely off." We blinded the box on purpose and are
now debugging it blind. **The historical precedent for "power cycle fixes GWTC" is
weak** — the 5 recoveries on 2026-04-20 cited in CLAUDE.md were `farmcam-wifi-watchdog`
doing `Restart-NetAdapter`, not physical power cycles.

**Test:** any sign of life at all — fan noise, warmth at the vent, power/charge LED,
keyboard backlight. Then press the power button and wait 3 min.

### H2 — Pre-login WiFi gap (autologon broken)

GWTC relies on autologon to `cam` (blank password) to reach the desktop; the WLAN profile
is per-user, so pre-login there is no association. The known landmine is
`DevicePasswordLessBuildVersion` being reset by a Windows Update, which applies **on
reboot** — see `docs/18-Apr-2026-gwtc-current-state-and-install-walkthrough.md`.

Consistent with: no association, and `farmcam-wifi-watchdog` being unable to help
(`Restart-NetAdapter` is a no-op when the adapter is fine and simply has no profile to
associate with). Under this reading the *reboot* is proximate, not the unplug.

**Test:** external monitor + USB keyboard. A login/PIN/OOBE screen confirms H2; a normal
desktop refutes it.

### H3 — Associated but no DHCP lease (APIPA) — the only branch with a remote fix

If GWTC associated to the AP but DHCP failed (pool exhaustion, stale lease, conflict), it
self-assigns `169.254.x.x`: invisible on `192.168.0.0/24`, absent from the DHCP lease
table, but **present in the AP's associated-station list**.

**This has not been ruled out.** Boss checked the router's client/DHCP list. The
authoritative page for this is **Advanced → Wireless → Wireless Statistics**
(associated clients), which is a *different* page. Look for MAC `F0:35:75:81:2C:45`.

- Present there → H3. Fixable from the router (static reservation / free up the pool), no
  walk to the coop.
- Absent → H3 dead, and it's H1 or H2, both of which need hands.

Note the IP has drifted before (`.68` → `.69`), so the lease is not pinned — lease churn
is real on this network.

### H4 — Weak-signal driver wedge that the watchdog cannot clear

The documented recurring failure (`docs/18-Apr-2026-...`, CLAUDE.md). Coop signal ~34%.
Exactly matches outage #1's shape and self-recovery. Still plausible for #2, but the
4 power cycles argue against it persisting — unless H1 means those cycles never actually
restarted the machine, in which case H4 + H1 compound.

---

## Dead theories — do not re-run these

- **~~USB boot / removing the hub changed boot order~~.** My own theory from this session,
  and it was wrong. Removing USB devices makes boot *more* deterministic, not less. The BCD
  landmine from the Debian-wipe attempt is disarmed and the internal SD card is documented
  as harmless. Timeline also exonerates the unplug entirely (see above).
- **~~The WiFi is a removable USB dongle that came out with the hub~~.** The chipset is a
  Realtek **8723DU** — the `U` is a USB *bus interface*, but the module is **built into the
  chassis**. `docs/18-Apr-2026-gwtc-current-state-and-install-walkthrough.md:79` pre-buries
  this explicitly: "do not look for a removable adapter." Verified before asserting; theory
  discarded unpublished.
- **~~Plug it into the Ethernet switch~~.** Gateway GWTC116-2 has **no Ethernet port**
  (`docs/GWTC_SETUP.md:23`: "Realtek 8723DU USB WiFi (150 Mbps link, no ethernet)"). The
  viable version is a **USB-Ethernet adapter**, which does come up pre-login with a
  machine-scoped DHCP lease — that remains the strongest permanent fix for the whole class
  of pre-login/WiFi failures on this box.
- **~~Parallel 253-way `nc` sweeps of the /24~~.** Verified unreliable earlier in this
  session — produces false "closed" results. Use batched/sequential probes, or the router.

---

## One-trip checklist for the coop

Four power cycles happened because each round of diagnosis produced exactly one new guess.
Do all of this in a single visit instead.

**Before walking out** — 20 seconds at the router, may make the trip unnecessary:
- [ ] Router → **Advanced → Wireless → Wireless Statistics** → is `F0:35:75:81:2C:45`
      listed? If yes, it's H3 and fixable from the desk.

**Take with you:** external monitor + HDMI cable, USB keyboard, and a USB-Ethernet adapter
if one exists.

- [ ] **Any sign of life?** Fan, warmth, power LED, charge LED. (Answers H1.)
- [ ] **Press the power button.** Wait 3 minutes. Re-probe from the Mini.
- [ ] **Plug in the monitor + keyboard.** Login/PIN/OOBE screen → H2. Desktop → H2 dead.
- [ ] If at a login screen: log in as `cam` (blank password), then re-check
      `DevicePasswordLessBuildVersion` per the 18-Apr walkthrough.
- [ ] **Plug the USB camera + hub back in** either way.
- [ ] If a USB-Ethernet adapter is available, attach it — that permanently removes the
      pre-login blind spot.

---

## ⛔ The USB camera stays on GWTC — do NOT propose moving it to the MacBook Air

**Boss directive, 25-Jul-2026, explicit and unprompted: "We are absolutely not moving the
USB cam to the MacBook Air. Stop suggesting that."**

An earlier revision of this doc recommended relocating the camera to the MBA as an outage
workaround. That recommendation is **withdrawn**. Do not re-derive it, do not offer it as a
fallback during the next GWTC outage, and do not treat MBA health checks as evidence that
the move is a good idea. The camera lives on GWTC in the turkey pen; a GWTC outage is fixed
by fixing GWTC.

(The MBA also remains the host for `mba-cam` — its own separate lane, its own built-in
FaceTime HD at 1280x720. That is unchanged and unrelated.)

---

## Open questions

- Does GWTC's BIOS have "power on AC attach"? If yes, H1 is weakened. Unknown; check while
  a monitor is attached.
- Is the battery dead? Determines whether the box can ever survive a power blip unattended.
- Was outage #1 (02:27) the same root cause as #2? If GWTC is now failing twice a day, the
  USB-Ethernet fix moves from "nice" to "required."
