# 25-Aug-2026 — `s7-cam` dark for 16½ hours: the phone joined the GUEST Wi-Fi

**Resolved 25-Aug-2026 12:06 EDT.** `Camera 's7-cam' — snapshots resumed after 3284 failures`.

---

## One-line cause

**The phone reconnected to `653 Pudding Hill 2G Guest` instead of `653 Pudding Hill 2G Private`,
and the guest SSID has client isolation — so the phone had working internet while being
firewalled off from every device on the farm LAN.**

Nothing was broken. Not the phone, not the app, not the camera, not the network.

---

## Timeline

| When | What |
|---|---|
| 24-Aug ~18:00 EDT | Phone reboots (uptime at diagnosis: 17h37m) |
| 24-Aug 18:24 EDT | `WifiConfigStore: loadConfiguredNetworks` — Wi-Fi re-selects after boot |
| **24-Aug 19:42:59 EDT** | **Last archived `s7-cam` frame.** Clean cutoff — 155 frames that hour, then zero |
| 24-Aug → 25-Aug | 3,284 consecutive `Host is down` failures. **No alert fired to anyone** |
| 25-Aug 12:06 EDT | Guest network forgotten on the phone → auto-joined Private → recovered instantly |

---

## Why it was invisible, and why the usual triage misleads

The standing guidance for a dead `s7-cam` is that a clean `Host is down` with all ports closed
"is consistent with the phone being off." **That inference is now known to be incomplete.** The
phone was fully on, fully online, and serving IP Webcam on :8080 the entire time — it was simply
on a network segment the Mac Mini is not permitted to reach.

Evidence captured on the bench over ADB:

```
SSID: 653 Pudding Hill 2G Guest   BSSID: 5e:a6:e6:16:f1:0f   MAC: 2c:0e:3d:09:77:a4
wlan0: inet 192.168.0.89/24            <- DHCP from the guest segment, NOT the static .249
ping 192.168.0.1  -> 0% packet loss    <- gateway reachable (this is the "it's on the internet")
ping 192.168.0.10 -> 100% packet loss  <- Mac Mini UNREACHABLE = guest client isolation
com.pas.webcam pid 4278, :::8080 LISTEN  <- app was never broken
```

Note the guest BSSID `5e:a6:e6:16:f1:0f` versus the AX55's LAN MAC `5c:a6:e6:16:f1:10` — the
**same physical router**, a second SSID with the locally-administered bit set. The guest network
is not a separate box and does not look like one.

### ⚠️ The trap that cost the most time: a LAN sweep CANNOT see this

`arp -an` after a full `/24` ping sweep did **not** show the phone, and a threaded probe of port
8080 across all 254 addresses found **nothing**. Both results are correct and both are
misleading — client isolation means a guest-segment device is invisible to LAN scanning **even
though it holds an address on the same 192.168.0.0/24 range** (it had `.89`, right next to
`house-yard` at `.88`).

**A device absent from an ARP sweep is not proof it is off the network.** It may be one SSID away.

---

## The fix

The static `192.168.0.249` config was never lost — on Android it is stored **per saved network**,
attached to the *Private* entry:

```
ID: 1 SSID: "653 Pudding Hill 2G Private"  PRIO: 3   IP assignment: STATIC -> 192.168.0.249/24
ID: 0 SSID: "653 Pudding Hill 2G Guest"    PRIO: 1   IP assignment: DHCP
```

So the entire fix was getting it back onto Private; `.249` returned by itself.

**What did NOT work:** `svc wifi disable && svc wifi enable`. Android re-selected Guest every
time across 44s of retries, despite Private having the higher `PRIO`. **Priority does not
decide this** — do not waste time toggling Wi-Fi.

**What worked — forget the Guest network entirely**, leaving Private as the only saved SSID:

```bash
adb shell am start -a android.settings.WIFI_SETTINGS
adb shell input swipe <guest-row-x> <guest-row-y> <same> <same> 1200   # long-press
adb shell input tap <forget-network-x> <forget-network-y>
```

Re-association to Private with `.249` took **under 4 seconds**. Guest is gone from the saved
list, so the phone can no longer roam onto it. `5G Guest` is visible in scans but was never
saved, so it is not a second trap.

⚠️ **Driving this phone's UI by ADB: the screen is LANDSCAPE** (`mCurrentOrientation=1`), so
`screencap` returns **2560x1440** while `wm size` reports the native portrait `1440x2560`.
`input tap` uses the landscape space. Screenshot first, compute coordinates from the image, or
every tap lands in the wrong place.

---

## Verified after the fix

- `nc -z 192.168.0.249 8080` → open from the Mini
- Live `/photo.jpg` → 417 KB, 1920x1080, EXIF `upper-right`
- `status.json` → `orientation=portrait`, `ip_address=192.168.0.249`, `focusmode=continuous-picture`
- Guardian → `snapshots resumed after 3284 failures`; 4 new archive rows in 45s at **1080x1920**
- Only one saved network remains: `653 Pudding Hill 2G Private`
- `com.pas.webcam` still doze-whitelisted (`user,com.pas.webcam,10191`)

**EXIF `upper-right` (=Orientation 6) is CORRECT and must not be "fixed"** — `force_portrait`
bakes the rotation in at capture. Archive rows landing at 1080x1920 is the proof.

Bench frames of carpet and cables are expected while the phone is plugged into the Mini indoors.

---

## 🔴 The real gap this exposed: NOTHING ALERTED

`s7-cam` was dead for **16½ hours** and the only record was `WARNING` lines accumulating in
`guardian.log`. `com.farmguardian.birdcatraz-watchdog` watches **`farm-pi5` only** — it has no
opinion about `s7-cam`, and it stayed green throughout (135 clean ticks).

Worse, **Guardian's own `/api/cameras` reported `s7-cam online=true`** while the capture layer
was logging its 2,830th consecutive failure. The dashboard actively said the camera was fine.
That is a real bug and it is why "everything seems normal" was a reasonable read.

**Recommended follow-ups (NOT done here — these are code changes needing their own plan):**
1. Make `online` reflect recent capture success instead of reporting `true` for a camera that
   has failed thousands of times consecutively.
2. Extend the watchdog (or add a sibling) to alert on **any** camera with no archive row in N
   minutes. The existing Discord alert path in `tools/birdcatraz-watchdog/watchdog.py` is the
   model — one alert per outage, one recovery notice.

## Note on the retired settings watchdog

For the record, since the question came up: `com.farmguardian.s7-settings-watchdog` could never
have caught this. Its own header, line 33: *"This watchdog therefore CANNOT self-heal a stall.
Its job is DETECT + LOG."* It only re-pushed camera settings over HTTP, which requires the phone
to already be reachable. Retired 10-Aug-2026.

**But the replacement handset changes what is possible.** All the "no ADB path exists, ever"
reasoning in that header describes the OLD drowned SM-G930F. This phone (SM-G930V, serial
`4fad774d`) has a working USB port and working ADB — this entire fix was performed over it. A
watchdog with a genuine recovery path is buildable for the first time, **but only from a host
that is physically USB-tethered to the phone.** The Mini is in the house; the natural candidate
is `farm-pi5`, already at Birdcatraz. Unresolved: whether a Pi 5 USB port can power an actively
streaming phone that currently runs off a 3 A charger. Measure before committing to it.

## Still open (deliberately not changed)

The phone remains on a **static** `192.168.0.249` attached to the Private network. The AX55's
DHCP reservation still points at the **retired** handset's MAC (`8C-F5-A3-B6-5A-E5`); the live
phone is `2C:0E:3D:09:77:A4`. Moving it to DHCP + a correct reservation is still the durable
play, but that is a **router change**, and CLAUDE.md requires Boss's approval for those. Not
done. See `docs/22-Aug-2026-new-internet-install-plan.md`.
