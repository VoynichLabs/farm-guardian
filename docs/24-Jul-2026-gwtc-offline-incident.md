# GWTC offline — 24-Jul-2026 incident diagnosis

**Status:** GWTC (`192.168.0.69`, Gateway GWTC116-2, coop laptop) is off the network.
Confirmed absent from the router's client list by Boss. `usb-cam` lane is down with it.

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

## Live hypotheses, ranked

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

## Independent of GWTC: restoring the turkey-pen lane today

The 1080p turkey-pen feed does not have to wait on GWTC. The MacBook Air
(`192.168.0.50`) is **up and healthy right now** — verified this session:

```
{"ok":true,"prefer_external":false,"resolved_device_index":0,
 "resolution":[1280,720],"grabber_alive":true,"camera_open":true,
 "latest_frame_age_ms":294,"total_grabs":217116}
```

Moving the USB camera to the MBA restores the lane, **but there is a trap.** Per CLAUDE.md
and confirmed by the `/health` output above, the MBA's plist now sets
`USB_CAM_PREFER_EXTERNAL=false`, so it would keep serving the built-in FaceTime HD at
**1280x720** and silently label it `usb-cam`. Getting exactly this wrong is what produced
the mislabeled 21-Jul → 23-Jul frames.

Required sequence:
1. Flip `USB_CAM_PREFER_EXTERNAL=true` in the MBA plist.
2. `launchctl bootout` **then** `bootstrap` — a `kickstart` re-runs from launchd's *cached*
   plist and silently ignores the edit.
3. `curl http://192.168.0.50:8089/health` and confirm `resolved_device_name` and
   `resolution` are the USB camera's **1920x1080**, not FaceTime's 1280x720, before
   trusting the label.
4. Update the `usb-cam` URL in **both** `config.json` and `tools/pipeline/config.json`
   (or use `scripts/add-camera.py`), then reload both LaunchAgents.

---

## Open questions

- Does GWTC's BIOS have "power on AC attach"? If yes, H1 is weakened. Unknown; check while
  a monitor is attached.
- Is the battery dead? Determines whether the box can ever survive a power blip unattended.
- Was outage #1 (02:27) the same root cause as #2? If GWTC is now failing twice a day, the
  USB-Ethernet fix moves from "nice" to "required."
