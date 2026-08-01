# S7 phone-cam — micro-USB port is dead (01-Aug-2026)

**Status: closed on the port question, OPEN on what to do about the phone.**

Read this before touching anything to do with `s7-cam` power, ADB, or phone recovery.

---

## What happened

The S7 (SM-G930F, Android 8.0.0, `192.168.0.249`) got wet at some point. Afterwards it
stopped charging through its micro-USB port. Boss's working theory was that something had
"hard-switched" — a latched flag that survived drying and could be reset.

That theory was tested to exhaustion on 01-Aug-2026 and is wrong. **The port is physically
dead, for both power and data.**

## The evidence

With the phone cabled to the Mac mini, it **does not enumerate on the USB bus at all** — no
Samsung vendor ID (1256 / `0x04E8`) anywhere in `ioreg -rc IOUSBHostDevice`. The bus shows
only the Apple root hubs, VIA Labs + GenesysLogic hubs, a C-Media USB audio device, a mouse,
a keyboard, and the Raycue SSD enclosure.

That single fact is what kills the latched-flag theory. **A moisture/charge block suppresses
charging but leaves the USB data path enumerating normally** — the phone would still appear
on the Mac, it just wouldn't draw power. Getting nothing at all is a dead connection, not a
refused one.

### Everything that was ruled out

| Ruled out | How |
|---|---|
| Charge-only cable | Boss confirmed a known-good **data** cable |
| Hub chain interference | Plugged **directly** into the Mac mini, no hub |
| Lint/debris in the port | Boss cleaned it out with a toothpick, re-tested |
| Latched firmware/charge-controller state | **Forced restart** (Vol Down + Power) — on a sealed-battery phone this is the equivalent of a battery pull, so the charge controller rebuilds its state from scratch on boot. USB bus polled **every 2 s for 12 minutes straight through the reboot**; the Samsung VID never appeared once. |

**Do not re-test these.** Four independent theories, all closed.

Corrosion is the likely mechanism but is an *inference* — confirming it would mean opening
the phone, and there's no reason to bother.

⚠️ To be precise, because this got muddled in conversation: it is the **S7's charging port**
that is dead. **The Mac mini's USB ports are fine.**

## Two consequences that constrain everything downstream

**1. There will never be ADB on this phone again.** adb-over-USB needs a working port.
adb-over-network is refused (5555 closed) because Android 8.0.0 predates wireless-debugging
pairing, and enabling `adb tcpip` would itself require one working USB session. Any doc
suggesting the ADB runbook is merely dormant "until the phone is re-tethered" is wrong and
has been corrected (`CLAUDE.md`, `docs/skills-s7-adb-operations.md`).

**2. Firmware-level remedies are foreclosed by the same fault they'd fix.** Odin reflashing
and recovery-mode sideloading both require a working USB connection. There is no wireless
path to firmware on an S7. What *is* still available without USB: forced restart, wipe cache
partition (Vol Up + Home + Power), and factory reset — all of which only clear *software*
state and therefore cannot help here.

## Charging now

**Qi wireless pad.** The SM-G930F has Qi built in, and it is the only path that works. This
is confirmed working — it's how Boss got the phone charged again.

Power-chain history: MBA-USB → standalone brick (26-Apr-2026) → GWTC-USB (02-May-2026) →
standalone brick again (≤06-May-2026) → **Qi pad (01-Aug-2026)**.

---

## ⚠️ THE ACTUAL OPEN PROBLEM — battery life, and possibly replacing the phone

**This is what the next assistant should be thinking about. The port is a closed question;
this one is not.**

Boss's assessment, in his words: the phone is *"not really back since it's going to have an
extremely short battery life now… probably only going to get like an hour's worth of footage
a day. So that's kind of a fucking disaster."* He is considering **replacing the phone
outright** as the selfie camera.

The problem is the deployment, not just the charging rate:

- **The phone lives out at the chicken coop.** It is not sitting next to the Mac mini.
- Charging requires the Qi pad. Whether the pad can live *at the coop* alongside the phone —
  power availability out there, weather, dust — is **the question that decides whether this
  phone is still viable**. If the phone has to be carried indoors to charge, Boss's
  hour-a-day estimate is right and the S7 is finished as a continuous camera.
- Qi on an SM-G930F is roughly 5 W, meaningfully less than the wall brick it used from April
  to July. This phone also has a **documented history of browning out on weak power**
  (see the `s7-settings-watchdog.sh` header and the 20-Jul-2026 74-minute WiFi dropout).
  Treat power as the first suspect on any new stall.

### What was NOT measured, and why

**Nobody has actually measured whether Qi keeps up with the camera's draw.** The plan was to
watch the battery level while the phone ran on the pad, but the phone had already gone back
out to the coop, so the measurement never happened. The hour-a-day figure is Boss's
projection, not an observation.

Getting a real number is blocked on telemetry:

- `http://192.168.0.249:8080/sensors.json` returns `{}`. IP Webcam only serves sensor data
  when data-logging is enabled **in the app**, which it isn't. `/battery.json` is a 404.
  Enabling sensor logging is a hands-on change at the phone.
- `tools/s7-battery-monitor/monitor.py` reads battery via `adb dumpsys battery` and is
  therefore **permanently dead** — see consequence 1. The long-standing TODO in its header
  (rewrite it against IP Webcam's HTTP endpoint so it runs on the Mini with no tethering) is
  now the *only* possible way to monitor this phone's battery, and it depends on someone
  enabling sensor logging in the app first.
- Cheapest test needing no telemetry at all: leave it running and see whether the feed
  survives 24 h. If it dies in hours, Qi doesn't keep up.

---

## Side effect worth knowing: every reboot comes back LANDSCAPE

The 30-Jul → 01-Aug camera outage (see below) was fixed by rebooting the phone, and it came
back **landscape**: `photo_rotation` reset to `-1` and the JPEG carried EXIF `Orientation=1`.

`photo_rotation` is runtime-only over HTTP and does **not** persist in the app across
restarts. Left alone it would have fed landscape frames into the IG stories/reels lane, which
is native 9:16 — see `project_s7cam_portrait_orientation` for why portrait is deliberate.

Fixed on 01-Aug by re-applying and verifying at the pixel level (EXIF `Orientation` back to
`6`):

```bash
curl "http://192.168.0.249:8080/settings/photo_rotation?set=90"
curl "http://192.168.0.249:8080/settings/orientation?set=portrait"
curl "http://192.168.0.249:8080/settings/focusmode?set=continuous-picture"
```

`com.farmguardian.s7-settings-watchdog` re-applies these every 10 min, so this self-heals
within one tick. **Expect sideways frames for up to 10 minutes after any reboot** — that's
normal, not a new fault.

## The outage that led here

`s7-cam` served nothing from **2026-07-30T23:39Z until 01-Aug** — three days, 215 consecutive
watchdog STALLs. Fingerprint of that wedge, worth recognising again:

- TCP 8080 **accepts instantly** (the kernel holds the listening socket) but no HTTP response
  ever comes back.
- ICMP dropped entirely.
- The process is alive; its worker threads are frozen.

Notably the phone was **on the charger and still frozen**, which rules out AOSP Doze (it does
not engage while charging) and points at Samsung's own app-freezer instead.

⚠️ The relevant menus are the **Android 8 / Samsung Experience 9.0** ones. The "Sleeping
apps" / "Deep sleeping apps" lists that everything online refers to are One UI (Android 9+)
and **do not exist on this phone** — Boss went looking and correctly reported they weren't
there. On this device:

1. Settings → Device maintenance → Battery → **"Unmonitored apps"** → Add apps → IP Webcam
2. Settings → Apps → ⋮ → Special access → **"Optimize battery usage"** → dropdown to
   "All apps" → toggle IP Webcam **off**
3. Settings → Device maintenance → Battery → Power mode: **not** a power-saving mode

These were **not** confirmed applied — the phone went back to the coop first.

## Tooling added

- **`tools/s7-charge-diagnose.sh`** — read-only. Checks the USB bus first and stops early
  with the rule-outs if the phone isn't enumerating; otherwise reports charge source
  (AC/USB/Wireless), battery status and health, the Samsung MUIC/moisture nodes including the
  `batt_misc_event` water bitfield, and `batt_slate_mode`/`store_mode`. Given the port is
  dead it will never get past its first check on this phone — it's kept for a replacement
  handset, or if the port is ever repaired.
- **`deploy/s7-settings-watchdog/watchdog.sh`** — resynced from the live LaunchAgent copy,
  which had drifted months ahead of it. The repo version was still the May-2026 script that
  SSHed into GWTC (decommissioned 07-Jun-2026) to run ADB recovery. That dead path is gone.

## Deliberately left alone

- **CHANGELOG and prior incident docs** — they describe the state at the time correctly and
  are history, not current fact.
- **`tools/pipeline/config.json`** VLM scene context still says "plugged into a USB wall
  brick." That prompt is scoring-calibrated and must not be casually edited (see
  `project_vlm_context_window_fix` — slimming it once caused Discord spam). The stale power
  detail is harmless to scoring; fix it only as part of a deliberate prompt revision.
