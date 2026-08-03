# S7 factory reset + rebuild runbook (01-Aug-2026)

Boss is factory-resetting the S7 phone-cam. **Everything here is hands-on at the phone —
there is no ADB and there never will be again** (the micro-USB port is dead for power and
data; see `01-Aug-2026-s7-usb-port-dead.md`). Nothing in this rebuild can be done remotely
until the phone is back on WiFi with IP Webcam serving.

Expectation-setting: **a factory reset will not fix the charging port.** That's hardware.
It is still worth doing for the *other* problem — it clears whatever Samsung-side app-freezer
setting has been wedging IP Webcam (three-day outage, 30-Jul → 01-Aug) and gives a clean
baseline.

---

## ⛔ STOP — do this before the reset or you may permanently brick the phone

**Factory Reset Protection (FRP).** If a Google account is signed in when the phone is wiped,
Android 8 will demand *that exact account's* username and password on first boot. Not any
account — that one. If nobody knows those credentials, the phone is locked forever and cannot
be recovered. Normally you'd clear FRP by flashing firmware over USB, but **that escape hatch
is gone here**, because the port is dead. That turns "we might replace this phone" into "we
definitely have to."

**Avoid it like this, in this order:**

1. **Settings → Cloud and accounts → Accounts → Google → ⋮ → Remove account.** Remove every
   Google account listed.
2. Remove the **Samsung account** the same way if one is present.
3. **Then** reset from **Settings → General management → Reset → Factory data reset**.
   Prefer this over the recovery-mode wipe — resetting from Settings with no accounts
   attached leaves FRP clear.

### Also before you start

- **Charge it full on the Qi pad first.** Setup runs with the screen on for a while, Qi is
  only ~5 W, and this phone browns out on weak power. Don't start the reset at 40 %.
- **Anything on the phone's internal storage is gone for good.** With no USB there is no way
  to pull files off it. Realistically nothing matters — Guardian pulls frames over HTTP and
  keeps them on the Mini — but if there are local recordings you care about, they have to be
  moved over WiFi *before* the wipe. (History: a previous app silently filled `/sdcard` with
  19 GB of recordings, so it's worth a glance at Settings → Device maintenance → Storage.)
- **Have the WiFi password written down.** You'll be typing it on the phone.

---

## Testing the "bugged moisture sensor" theory (raised 01-Aug-2026)

The theory: Samsung's moisture detection has latched and is refusing to charge even though
the port is dry. This is a real and very common Samsung fault, so it deserves a real test
rather than a hand-wave. Two things to know before spending effort on it.

**It is mostly a USB-C-era fault.** The famous "Moisture detected" lockout — and the
"clear USBSettings cache" fix that circulates for it — belongs to the S8/S9/S10/S20
generation, which detect water on the USB-C CC lines. This is a micro-USB SM-G930F. It does
have a MUIC that can flag a water/short fault, so the theory isn't impossible, just not the
same mechanism the online fixes were written for.

**The strongest counter-evidence is that the reset has effectively already been done.** A
latched moisture flag is kernel/MUIC driver state, and that state is rebuilt from scratch on
a cold boot. On 01-Aug a forced restart (Vol Down + Power — the sealed-battery equivalent of
a battery pull) was performed while the USB bus was polled every 2 s for 12 minutes straight
through it. The Samsung VID never appeared. A latched flag does not survive that.

Also note that a moisture lockout blocks *charging* while leaving USB **data** working —
users on affected phones routinely still transfer files while charging is refused. Here there
is no enumeration at all. That points at the connection rather than a refusal, though it is
suggestive rather than absolutely conclusive on its own.

### ANSWERED 01-Aug-2026: the warning fired, repeatedly

Boss confirms the phone **showed the moisture warning many times**, and that **clearing it
did not hold — it came straight back.** That is real evidence and it changes the picture in
two ways.

**It corrects the first qualifier above.** The moisture-detection system on this micro-USB
S7 is clearly live and engaged. Treating it as a USB-C-only feature was wrong.

**It also weakens the zero-enumeration argument.** When Samsung's MUIC decides the connector
is wet, it can shut down the **whole connector** — data included — to stop current crossing
wet pins. So a persistently-engaged moisture lockout is a plausible explanation for the phone
never appearing on the USB bus. The earlier claim that a moisture block "leaves data
enumerating" holds for many USB-C cases but should not have been stated as firmly as it was.

**And it blunts the cold-boot argument.** A reboot clears a *stale* latch. It does nothing
about a detector that is actively re-triggering. Since the warning kept returning after being
cleared, the reboot test cannot tell those two apart.

### But recurrence points at a real fault, not a bug

Here is the part that matters most, and it cuts against the "just bugged" reading:

**A moisture sensor that keeps re-firing after being cleared is usually detecting something
real.** These detectors work by sensing conductivity across the port pins. **Corrosion
residue is conductive, and electrically it is indistinguishable from water.** A port that
got wet, dried, and left a corrosion film behind will trip the moisture detector forever —
correctly, by the detector's own logic — because the conductive path never goes away.

That single story explains every observation at once: the soaking, the repeated warnings,
the clearing that wouldn't stick, the refusal to charge, and the total absence of USB
enumeration.

So the useful split is **not** "bugged sensor vs. dead port." It is:

| If… | Then… |
|---|---|
| The sensor is genuinely bugged | A factory reset / USBSettings clear stops the warnings **and** charging returns |
| The sensor is correctly reading corrosion residue | The warning returns immediately after any reset, forever — and **software cannot fix it, but cleaning can** |

### UPDATE — since the Qi recharge: no more moisture warnings, and still nothing on the port

Boss reports that since the phone was recharged on the Qi pad, **the moisture message has
stopped appearing entirely** — but the port still does nothing. No charge, no data, no
warning.

**Be careful with this one: it fits both competing theories equally well, and it is not the
software confirmation it first looks like.**

*Reading A — port is physically open.* The moisture detector fires only when it can sense
the pins. Earlier it was firing constantly, which means the port was electrically alive
enough to detect a fault. Total silence now — no insertion event, no charge, no data, no
warning — is what you get when the phone can't see anything on the connector at all. That's
the picture of corrosion progressing from a *short* (detectable, warns) to an *open* circuit
(nothing to detect). The warnings stopping would be a symptom of getting worse, not better.

*Reading B — firmware has latched the port off.* Once Samsung's firmware decides a connector
is unsafe, it can disable the USB path outright. A phone in that state would also go quiet on
every channel, including the warnings, because it isn't energising the port to check any
more. That is a coherent software fault and it explains the same observations.

**Both readings are live. This does not decide it.** What it does do is remove the moisture
warning as a usable signal going forward — it's gone either way.

### ⭐ The cheap test that actually separates them: a dumb wall charger

**This has not been tried since the port was cleaned and dried.** A plain wall brick and a
computer port are electrically different: a dumb charger shorts D+/D- together and asks for
no negotiation at all, while a Mac port requires the phone to negotiate a USB connection.

| Result | Meaning |
|---|---|
| Charges from a wall brick but not the Mac | The port conducts. Detection/negotiation is the problem → **software, Boss's theory** |
| Any reaction at all — charging icon, moisture warning, battery LED | The port is sensing something. Not fully open. |
| Absolutely no reaction to a wall brick | Nothing is reaching the phone → **the connection itself** |

Even a phone with a flat battery shows *something* on insertion. Zero reaction to a dumb
charger is about as close to conclusive as this gets without opening the phone. Try a second
cable too while you're at it.

### ⭐ The untried fix this points to: isopropyl alcohol

Boss cleaned the port **with a toothpick**. That removes lint and packed debris — it does
nothing to a corrosion film bonded to the pins. If the detector is reading a real conductive
path, **solvent is the fix, not mechanical scraping.**

1. **Power the phone off completely.** Do not skip this.
2. Use **99 % isopropyl alcohol** (not rubbing alcohol at 70 %, which is mostly water and
   defeats the purpose). Put a little on a soft brush or a lint-free swab that fits.
3. Work it into the port, gently, to dissolve and lift the residue. Isopropyl displaces water
   and carries dissolved salts out with it as it evaporates.
4. **Let it dry for several hours** — longer than feels necessary. Warm dry air is fine;
   no hot air guns, no rice.
5. Power on and test the cable again.

This is cheap, it's the one physical remedy that actually targets corrosion, and it has not
been tried. If the moisture warning stops coming back after this, that's the answer.

### The good news: the planned factory reset already tests this

**A factory reset is a strict superset of every software-level moisture-sensor reset.** It
clears app data and settings wholesale, including `USBSettings`. So there is no need to
choose between the two approaches — the reset that's already planned covers the software half
of this theory automatically. If a bugged software flag is the cause, the reset resolves it.

### Two extra things worth trying, since they're free

Both are a couple of minutes and there's no downside to doing them before the wipe:

1. **Clear the USB settings app.** Settings → Apps → ⋮ → **Show system apps** → find
   **USBSettings** (or **USB Settings**) → Storage → **Clear cache**, then **Clear data**.
   Reboot, then re-test with the cable. This is the canonical fix for the USB-C version of
   the bug and costs nothing to try here.
2. **Boot into safe mode** (hold Power, then long-press the "Power off" option until "Safe
   mode" appears) and test the cable there. Safe mode disables third-party apps; if it
   charges in safe mode, something installed is interfering rather than the hardware.

**After either, re-test from the Mini** — this reports whether the phone appears on the USB
bus at all:

```bash
bash ~/GitHub/farm-guardian/tools/s7-charge-diagnose.sh
```

If a Samsung device shows up on the bus, the theory was right and everything in
`01-Aug-2026-s7-usb-port-dead.md` needs revisiting. If it still shows nothing after a
factory reset — which wipes every software flag there is — then software has been eliminated
by exhaustion and the port is hardware-dead.

---

## Rebuild, in order

### 1. Setup wizard
Skip the Google account sign-in when offered — it isn't needed. IP Webcam does depend on
Google Play Services being *present*, but that's a system component and ships regardless of
whether an account is signed in.

Skipping also means no new FRP lock, which keeps the next reset safe.

### 2. WiFi + fixed address
Connect to the farm WiFi, then pin the address so Guardian's config keeps working:

**Settings → Connections → WiFi → long-press the network → Modify → Advanced → IP settings →
Static**

| Field | Value |
|---|---|
| IP address | `192.168.0.249` |
| Gateway | `192.168.0.1` |
| DNS | `8.8.8.8` |

`192.168.0.249` is what `config.json`, `HARDWARE_INVENTORY.md` and the watchdog all expect.
If it ends up somewhere else, find it from the Mini by its MAC — `8c:f5:a3:b6:5a:e5`:

```bash
arp -a -n | grep -i "8c:f5:a3:b6:5a:e5"
```

### 3. Kill the lock screen — this one is load-bearing
**Settings → Lock screen and security → Screen lock type → None.**

Not swipe. **None.** The swipe lock screen was the confirmed root cause of the cold-boot
black-camera bug: the keyguard blocked camera initialisation at boot, so the phone would come
up serving black frames. This was previously set with `adb shell locksettings set-disabled
true`, which is no longer possible — it has to be done in the GUI now.

### 4. Stay awake while charging
**Settings → About phone → Software information → tap "Build number" seven times** to unlock
Developer options, then **Settings → Developer options → Stay awake → ON.**

Replaces `adb shell svc power stayon true`, which is also no longer possible.

### 5. Stop Samsung freezing the app
⚠️ **Use the Android 8 menu names.** This phone is on Samsung Experience 9.0 / Android 8.0.0 —
its final firmware. The "Sleeping apps" and "Deep sleeping apps" lists that every guide online
refers to are One UI (Android 9+) and **do not exist here**.

Do all three *after* IP Webcam is installed in step 6:

1. **Settings → Device maintenance → Battery → "Unmonitored apps"** (may be behind the ⋮
   menu) → Add apps → IP Webcam
2. **Settings → Apps → ⋮ → Special access → "Optimize battery usage"** → change the dropdown
   to **"All apps"** → find IP Webcam → toggle **off**
3. **Settings → Device maintenance → Battery → Power mode** → not a power-saving mode

This is the fix for the recurring wedge. Fingerprint of that failure, for recognition later:
TCP 8080 accepts a connection instantly but no HTTP response ever comes back, and ICMP is
dropped entirely — the process is alive while its worker threads are frozen. It happened
*while the phone was on the charger*, which rules out AOSP Doze (that doesn't engage while
charging) and points squarely at the Samsung freezer.

### 6. Install IP Webcam
**`com.pas.webcam`, "IP Webcam" by Pavel Khlebovich.** Previously installed from **Aptoide**
(MD5-verified), not the Play Store — the Play Store route needs a Google account, which we
skipped.

To sideload, Android 8 needs per-app permission: **Settings → Apps → ⋮ → Special access →
Install unknown apps →** [the browser or Aptoide] **→ Allow from this source.**

### 7. Configure IP Webcam
| Setting | Value |
|---|---|
| Port | `8080` |
| Video resolution | `1920x1080` |
| Photo resolution | `1920x1080` |
| Quality | `99` |
| Focus mode | `continuous-picture` |
| Orientation | `portrait` |
| Login / password | **leave empty** — Guardian's config expects no auth |
| Disable lock screen / keep screen on | ON |
| Start server on boot | ON |

Then **Start server**.

### 8. Hand back to the Mini
Once it's serving, this can be verified and finished from the Mac:

```bash
curl -s -o /dev/null -w "%{http_code} %{size_download}\n" http://192.168.0.249:8080/photo.jpg
```

A real frame is 500 KB–1.5 MB. Then re-apply the runtime-only settings:

```bash
curl "http://192.168.0.249:8080/settings/photo_rotation?set=90"
curl "http://192.168.0.249:8080/settings/orientation?set=portrait"
curl "http://192.168.0.249:8080/settings/focusmode?set=continuous-picture"
```

**`photo_rotation` must be 90.** It is runtime-only, does not persist in the app, and resets
on every reboot — that's why frames come back landscape after a restart. Verify at the pixel
level rather than trusting `status.json`; the JPEG's EXIF `Orientation` tag must read **6**
(6 = portrait). Portrait is deliberate: the s7-cam feeds IG stories and reels, which are
native 9:16.

Guardian should then log `Camera 's7-cam' online`. The 10-minute watchdog
(`com.farmguardian.s7-settings-watchdog`) takes over re-asserting settings from there.

---

## ⛔ Do NOT do these

- **Never disable Google Play Services (`com.google.android.gms`).** IP Webcam depends on it;
  disabling it kills the camera and recovery is a full reboot. Learned 07-Jul-2026.
- **Never disable `com.sec.imsservice`** — triggers an undismissable OS crash loop.
- Don't bother with app cleanup / `pm disable-user` generally. That was an ADB workflow and
  ADB is gone. Boss wants location, Bluetooth and NFC left ON.
- Don't set an IP Webcam login/password unless you also update `config.json` — Guardian is
  configured for no auth.

## Still open after all this

The reset does not address the real question: **whether this phone is viable at all.** It
lives at the chicken coop and now charges only by Qi. If the pad can't live out there with
it, Boss's estimate of roughly an hour of footage a day stands and the S7 needs replacing as
the selfie camera. Nobody has yet measured whether Qi keeps up with the camera's draw.

One thing worth doing during the rebuild that would finally make that measurable: **turn on
IP Webcam's sensor data-logging while you're in its settings.** `/sensors.json` currently
returns `{}` because logging is off, which is why battery level can't be read over HTTP. With
it on, `tools/s7-battery-monitor/monitor.py` could finally be rewritten against the HTTP
endpoint — its standing TODO — and run on the Mini with no tethering. That is now the only
possible way to monitor this phone's battery.
