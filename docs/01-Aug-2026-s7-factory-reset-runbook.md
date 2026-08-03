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
