# 10-Aug-2026 — New Galaxy S7 swapped in as `s7-cam` (execution log)

**Author:** Claude Opus 5 (Bubba)
**Status:** ✅ **LIVE and verified.** `s7-cam` is serving from the new handset at
`192.168.0.249:8080`. Guardian logs `Camera 's7-cam' online (http_url snapshot)`, the pipeline
wrote 534 archive rows in the first 20 minutes with VLM enrichment completing.
**Note:** the `s7-settings-watchdog` referenced below was subsequently RETIRED the same day —
see [`10-Aug-2026-s7-settings-watchdog-retired.md`](10-Aug-2026-s7-settings-watchdog-retired.md).
**Plan this executes:** [`10-Aug-2026-s7-galaxy-replacement-plan.md`](10-Aug-2026-s7-galaxy-replacement-plan.md).

---

## The new handset is NOT the same phone as the old one

Both are "a Galaxy S7," which makes it easy to assume the existing runbooks transfer. **Two
differences invalidate large parts of them.**

| | Old (retired) | New (live) |
|---|---|---|
| Model | SM-G930F (`herolte`), international | **SM-G930V (`heroqltevzw`), Verizon** |
| Android | 8.0.0 / Samsung Experience 9.0 | **6.0.1 (API 23)**, build `G930VVRS4APH1` |
| adb serial | `ce12160cec2f2f0901` | `4fad774d` |
| WiFi MAC | `8C-F5-A3-B6-5A-E5` | `2C-0E-3D-09-77-A4` |
| micro-USB port | **dead** (power + data), Qi-only | **works** — adb available again |
| Screen | 1440×2560 | 1440×2560 (same) |

**⚠️ The single most load-bearing consequence: ADB WORKS AGAIN.** Every doc in this repo that
says the S7 has no ADB path and can only be fixed by walking to the coop was true of the *old*
phone and is now **false**. `docs/skills-s7-adb-operations.md`'s "PERMANENTLY INAPPLICABLE"
banner and `reference_s7_power_chain`'s "ADB is gone permanently" both describe the retired
handset. On this phone the cold-boot black-camera failure is recoverable from the Mini
(`adb shell am force-stop com.pas.webcam` → relaunch) instead of requiring a drive.

**⚠️ Android 6.0.1 breaks three recipes the existing docs hand you:**

1. **`adb shell locksettings set-disabled true` does not exist** — `locksettings` arrived in
   Android 7. The lock screen has to be handled another way (see below).
2. **`svc power stayon true` is silently unavailable** to the shell user here; use
   `settings put system screen_off_timeout 2147483647` instead.
3. **The Android-8 battery-freezer menu names in the runbooks do not exist.** Neither do One
   UI's "Sleeping apps." On 6.0.1 the mechanism is plain AOSP Doze, handled with
   `dumpsys deviceidle whitelist +com.pas.webcam` (done, over adb — no menu needed).

## What was done

### 1. Stripped the phone (130 packages)

Boss's explicit instruction, repeated three times: remove everything, one app only. **This
deliberately diverges from the plan doc**, which said not to run an app-disabling pass. The
substance of that warning was preserved — see the keep-list below.

**`pm disable-user` is DENIED to the shell user on Android 6** (`SecurityException: Permission
Denial: attempt to change component state`). That's an Android 7+ capability. The route that
works is:

```bash
adb -s 4fad774d shell pm uninstall -k --user 0 <pkg>
```

**⚠️ This is less reversible than `disable-user`, and there is no per-app undo.** The APK stays
in `/system` (visible via `pm list packages -u`) but **`pm install-existing` does not exist on
API 23** — it was added in API 24. **Recovery for any single package is a factory reset.** That
was an acceptable trade here only because no account is signed in, so there is no FRP risk.

330 → 200 packages. Removed: all Verizon carrier apps (`vzw*`, `verizon*`, `vcast`, `asurion`,
`vznavigator`, `slacker`, `go90`, NFL), Amazon (`kindle`, `mShop`), Google consumer apps
(maps/photos/gm/music/talk/videos/youtube/docs/wallet/tts/talkback/syncadapters), the Samsung
consumer layer (gallery, video, themes, S Health, Galaxy Store, Kies, easyMover, S Voice,
memo, email, weather, spayfw, scloud), TV/VR/sharing (watchmanager, withtv, mirrorlink, hmt.vr*,
allshare, FileShare, easysetup, qconnect, beaconmanager), always-on-display, and the
Knox/diagnostics/telemetry/OTA-update stack (knox*, klmsagent, securitylogagent, bbcagent,
diagmonagent, `soagent`, `sdm`, `sdmviewer`).

**Killing `com.sec.android.soagent` + `com.samsung.sdm*` also removes the OTA updater**, which
is desirable: this phone is on its shipped 2016 firmware and should stay there.

**KEEP-LIST — verified still present after the strip.** Removing any of these breaks the camera
or the OS:

| Package | Why |
|---|---|
| `com.google.android.gms` | **IP Webcam depends on it.** Disabling it killed the camera on 07-Jul-2026; recovery was a full reboot. |
| `com.sec.imsservice`, `com.sec.ims` | Disabling `imsservice` causes an undismissable "IMS service has stopped" crash loop. Kept despite no SIM. |
| `com.android.vending` | Play Store. Kept (unused — see sideload below). |
| `com.sec.usbsettings` | USB/adb path. |
| `com.sec.android.app.camera` | Stock camera, kept for hands-on sanity checks. |
| `com.sec.android.inputmethod` | Keyboard — needed to type a WiFi password. |
| `com.android.bluetooth`, `com.android.nfc`, `com.sec.location.nsflp2` | Boss's standing instruction (07-Jul-2026): leave the radios and location ON regardless of battery cost. |

### 2. IP Webcam installed by sideload — no account ever touched the phone

The phone has **no Google or Samsung account signed in** and Boss wants it kept that way, which
rules out the Play Store. Version parity with the old phone was the goal, so the target was the
exact same build: **`com.pas.webcam` v1.14.37.759 (aarch64), versionCode 7595**.

Boss downloaded the APK from APKMirror (a `curl` fetch 403s behind Cloudflare; the browser
route works). **The signing certificate was verified before install** — this is the check that
matters, more than any file hash:

```bash
unzip -p <apk> META-INF/CERT.RSA | openssl pkcs7 -inform DER -print_certs \
  | openssl x509 -noout -subject -fingerprint -sha256
# subject=C=RU, ST=Russia, L=Moscow, O=Home, CN=Pas XL
# sha256 = 29C6216DB158F51E36593B2394A23BFDF173D951F41D22BD9B61E04B3724636C
```

That fingerprint matches APKMirror's published value exactly, and `CN=Pas XL` is the IP Webcam
developer's own key (matching the `com.pas.webcam` package). Then:

```bash
adb -s 4fad774d install -r <apk>
# Android 6 needs runtime permissions granted explicitly — they are NOT implied by install:
for p in CAMERA RECORD_AUDIO WRITE_EXTERNAL_STORAGE READ_EXTERNAL_STORAGE ACCESS_FINE_LOCATION; do
  adb -s 4fad774d shell pm grant com.pas.webcam android.permission.$p
done
adb -s 4fad774d shell dumpsys deviceidle whitelist +com.pas.webcam
adb -s 4fad774d shell settings put secure install_non_market_apps 1
```

### 3. On-device settings

Set in the **app menu** (these persist; the HTTP `/settings/` API is runtime-only):

| Setting | Value | Note |
|---|---|---|
| Video resolution | `1920x1080` | already the default |
| Photo resolution | `1920x1080` | already the default |
| Quality | `100` (reports `99`) | default was 50 |
| Focus mode | **"Aggressive, for taking photos"** | ⚠️ **there is no menu option called "continuous"** — `continuous-picture` is the *HTTP API value* this menu label maps to. Looking for a "continuous" label wastes time. |
| Orientation | `portrait` | |
| Audio mode | `Disabled` | was Enabled |
| Login / password | **empty** | Guardian's config expects no auth |
| Port | `8080` | unchanged |

Set over adb: lock screen (`settings put secure lockscreen.disabled 1`), screen timeout to
max, brightness to 10, Doze whitelist, pointer-location dev overlay off (it was on).

### 4. Addressing — static ON THE PHONE, not a router reservation

The phone first took `192.168.0.89` from DHCP, which is **actively dangerous**: line 135 of
`tools/pipeline/config.json` lists house-yard's Reolink at `reolink_base:
http://192.168.0.89`, so the pipeline would have pulled phone frames and filed them as
house-yard. (Probed and confirmed: house-yard really answers on `.88` with all four Reolink
ports 80/554/8000/9000 open; `.89` had nothing. **That `.89` line is a pre-existing bug —
see Open items.**)

Fix: Boss set a **static IP of `192.168.0.249` in the phone's WiFi settings**, matching the
retired handset. **Config churn is therefore ZERO** — `config.json`,
`tools/pipeline/config.json`, `deploy/s7-settings-watchdog/*` and every doc citing `.249`
remained correct and were left untouched.

**⚠️ Note for whoever reads `~/bubba-workspace/memory/reference/network.md`:** the DHCP
reservation table there still pins `.249` to the **old** phone's MAC (`8C-F5-A3-B6-5A-E5`).
That reservation is now for a retired device. The new phone holds `.249` by on-device static
config, **not** by router reservation — so the router table is misleading, not wrong. Nothing
depends on it; noted so nobody "fixes" a conflict that isn't there.

## Verification (all passed)

1. **Reboot test — the one the old phone always failed.** Rebooted; on boot IP Webcam
   **auto-started its server unattended** (`com.pas.webcam/.Rolling` in front, 8080 listening),
   static `.249` held, no crash in `logcat -b crash`. No screen touch required. The old phone's
   cold-boot black-camera bug does not reproduce here.
2. **`photo_rotation` still resets to `-1` on every boot** — unchanged behaviour, not a new
   fault. Re-pushed over HTTP → EXIF `Orientation=6` → `ImageOps.exif_transpose` yields a true
   **1080×1920** portrait. The 10-minute watchdog re-asserts it from here.
3. **Guardian**: `Camera 's7-cam' online (http_url snapshot)` logged **before** `registered in
   snapshot mode` (the ordering CLAUDE.md says to check), `online: True, is_live: True`,
   `/api/cameras/s7-cam/frame` → 382 KB. All 7 cameras active.
4. **Pipeline**: 534 `image_archive` rows in 20 minutes, `vlm_inference_ms` ≈ 3,960–4,280,
   hunt burst + YOLO presence gate (shadow mode) behaving.
5. **Watchdog**: `frame_ok bytes=247451` and `settings fm=1 or=1 pr=1`, ending the run of
   `STALL … bytes=00` lines it had been logging against the retired phone.

## Open items — deliberately NOT done

- **✅ RESOLVED — the sensor is a Samsung ISOCELL S5K2L1, NOT a Sony IMX260.** The VLM `context`
  string claimed the Sony; corrected. **The sysfs nodes are root-only** (`rear_sensorid_exif`,
  `rear_camtype` etc. all return `Permission denied` to the shell user, with an SELinux
  `avc: denied` in logcat), so read it from the kernel ring buffer instead — this works
  unprivileged:

  ```bash
  adb -s 4fad774d shell 'dmesg | grep -iE "imx|s5k|2l1|sensor_match_id"'
  # msm_sensor_match_id: s5k2l1sx read id: 0x20c1 expected id 0x20c1
  ```

  `s5k2l1sx`, chip id `0x20c1`. The S7 shipped with either sensor interchangeably and they
  are the same spec (12 MP, f/1.7, dual-pixel AF), so the prompt's substance — "the best
  camera in the fleet, judge it on its own merits" — still holds and was left untouched. Only
  the sensor name changed. Pipeline restarted; enrichment continues at ~4.2 s and
  `image_quality` is now returning `sharp`.
  ⚠ The same string still says "on a Qi charging pad", which may no longer be true now that
  this phone's USB port works — left alone because its power source at Birdcatraz isn't
  decided yet, and it has no bearing on how the VLM judges a photo.
- **Gem-tier baseline not yet compared.** Every frame so far is `share_worth=skip` with
  `image_quality=soft`/`blurred` — expected, because the phone was on a desk indoors pointed at
  nothing, not at birds. Per the plan's Verification #5, sample `share_worth` / `image_quality`
  against the pre-swap baseline once it is aimed at the flock at Birdcatraz, and flag a sharp
  jump or drop in `strong` rather than assuming noise.
- **`tools/pipeline/config.json` line 135: house-yard's `reolink_base` says `192.168.0.89`;
  it should be `192.168.0.88`.** Pre-existing, unrelated to this swap, and left alone to keep
  this change surgical. It is currently harmless *only* because nothing answers on `.89` now —
  it would silently mislabel frames the moment anything does.
- **Old handset retirement is physical** (power off, off the Qi pad) and is Boss's to do.
- **One full daily reel cycle (21:00) and the Discord gem lane** have not yet run against the
  new phone — the plan asks for that before calling the swap fully done.
