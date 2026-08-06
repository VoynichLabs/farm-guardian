# 05-Aug-2026 — Birdcatraz Pi 5 bring-up log

**Author:** Claude Opus 5
**Date:** 05-Aug-2026 (evening)
**Status:** Pi is UP, reachable, both cameras attached and enumerated. **No camera-host
software installed yet** — this is bare OS plus verified hardware.

Companion to [`05-Aug-2026-birdcatraz-pi5-camera-host-architecture-plan.md`](05-Aug-2026-birdcatraz-pi5-camera-host-architecture-plan.md).
That doc is the design; this is what actually happened and what the next agent needs to know.

---

## Current state — verified, not assumed

| Fact | Value |
|---|---|
| Hostname | **`farm-pi5`** |
| IP | **`192.168.0.17`** (DHCP — static lease NOT yet reserved) |
| **MAC for the static lease** | **`88:a2:9e:a2:e6:23`** (`eth0`) |
| Hardware | Raspberry Pi 5 Model B Rev 1.1, 4 GB |
| OS | Debian GNU/Linux 13 (**trixie**), `2026-06-18-raspios-trixie-arm64-lite` |
| Disk | 57 GB, 8% used — rootfs auto-expanded on first boot |
| SSH user | `markb`, Mini's `id_ed25519` installed in `~/.ssh/authorized_keys` |
| Console password | `12345` — deliberately trivial, see below |
| sudo | **needs the password** (`echo 12345 \| sudo -S …`), NOT passwordless |

```bash
ssh -i ~/.ssh/id_ed25519 markb@192.168.0.17
```

### ⚠️ `eth1` is a decoy — the Pi uses its NATIVE port

The USB hub at Birdcatraz is a multiport dock: it contains a Genesys Logic hub, a **Realtek
RTL8153 Ethernet adapter**, and an SD card reader. That Realtek adapter appears as `eth1` and
is **DOWN and carries nothing**. Boss uses the Pi's own Ethernet jack.

```
eth0   driver=macb    88:a2:9e:a2:e6:23   UP     <- native Pi 5 port, default route
eth1   driver=r8152   00:e0:4c:68:01:46   DOWN   <- the dock's port, unused
wlan0  driver=brcmfmac                    DOWN
```

**Do not conclude from `lsusb` that networking runs through the hub.** An earlier claim in this
session did exactly that and was wrong — seeing an Ethernet adapter in the USB tree says nothing
about which interface holds the route. Check `ip route` and the driver, never the USB topology.
Consequence: **the hub is not load-bearing for connectivity** and can be unplugged safely.

---

## 🔴 `custom.toml` DOES NOT WORK on this image — use `userconf.txt`

**This cost the better part of an hour and a physical trip. Do not repeat it.**

The documented first-boot customization file `custom.toml` (sections `[system]`, `[user]`,
`[ssh]`, `[wlan]`, `[locale]`; schema confirmed against `RPi-Distro/raspberrypi-sys-mods`
`init_config`) was written to the root of the FAT `bootfs` partition. On boot:

- ✅ firstboot **ran** — it consumed its own `init=/usr/lib/raspberrypi-sys-mods/firstboot`
  trigger from `cmdline.txt`, and expanded the rootfs from 2.4 GB to 62 GB.
- ❌ It **did not process `custom.toml`** — the file was still sitting on the partition,
  unconsumed, and **no user account was created**.
- Symptom: sshd answered but every login was refused with
  *"Please note that SSH may not work until a valid user has been set up. See http://rptl.io/newuser"*.

**What actually worked: `userconf.txt`.** One line, `username:sha512-hash`, at the root of
`bootfs`. It is handled by a different service than firstboot, so it works even on a card that
has already completed its first boot — **no re-flash required.**

```bash
python3 -c "
import subprocess, pathlib
h = subprocess.run(['openssl','passwd','-6','12345'],capture_output=True,text=True,check=True).stdout.strip()
pathlib.Path('/Volumes/bootfs/userconf.txt').write_text(f'markb:{h}\n')
pathlib.Path('/Volumes/bootfs/ssh').write_text('')"
```

**⛔ NEVER build the password hash through a shell.** A `$6$…` crypt hash contains `$6`, which
bash expands as a positional parameter and silently eats, leaving a corrupt hash that matches
no password. That happened here and would have shipped an unloggable card to the coop. **Write
these files with Python**, then verify by re-hashing the plaintext against the stored salt and
comparing. Do that check before the card leaves the desk.

### Diagnosing a Pi that boots but won't let you in

One line settles it — firstboot deletes its own trigger when it runs:

```bash
grep -q "init=/usr/lib/raspberrypi-sys-mods/firstboot" /Volumes/bootfs/cmdline.txt \
  && echo "firstboot NEVER RAN (bad image write)" || echo "firstboot RAN (it rejected your config)"
```

Also: a Pi whose card has been pulled keeps answering TCP on port 22 from cached RAM for a
while, but **resets the connection mid-key-exchange**. That is not a live host. A stale ARP
entry will corroborate the illusion — `sudo arp -d <ip>` and re-probe before believing it.

---

## Cameras — both attached, identified by SERIAL

This is the payoff of the whole plan. Each camera has a stable, hardware-derived path:

```
/dev/v4l/by-id/usb-Jieli_Technology_USB_PHY_2.0-video-index0            -> video0   (dashcam)
/dev/v4l/by-id/usb-USB_CAMERA_USB_CAMERA_240725172848-video-index0      -> video2   (1080p webcam)
```

That `240725172848` is the same serial recorded for this webcam in `HARDWARE_INVENTORY.md`.
These paths survive replug, reboot, and plug order. **The 04/05-Aug identity-collision class
cannot occur here** — no index guessing, no name substring matching, no picture test, no
`PREFER_EXTERNAL`.

| Camera | Formats | Verified |
|---|---|---|
| `jieli-dashcam` | MJPG 1280x720 / 800x600 / 640x480 | ✅ **Real night frame captured** — coop, light, Boss visible. Works. |
| `usb-webcam-1080p` | MJPG **1920x1080** / 1280x1024 / 1280x960 / 1280x720, plus YUYV | ⚠️ **Inconclusive — retest in daylight** |

### The webcam: enumerates on Linux, and its gain was pinned at 0

Two genuinely new data points on this long-broken camera:

1. **On Linux its video interface enumerates cleanly.** On macOS it repeatedly vanished from
   the AVFoundation video list while remaining on the USB bus; on Windows/GWTC it served pure
   black. Here it presents full MJPG modes up to 1920x1080 and streams data.
2. **`gain` was `0` against a default of `32`.** A gain of zero produces a black frame on any
   host, which matches this camera's entire history of black output.

Setting `gain=32` with `auto_exposure=3` (Aperture Priority) moved the frame mean from
**1.1 → 3.5** — a real change, but the test ran at ~20:45 local in the dark, so it proves
nothing either way.

**⛔ Do NOT record this camera as fixed, and do NOT record it as dead.** Both would be
overclaims; the "dead camera" call has already been made and retracted once. **Retest in
daylight**, and if it is still black with gain restored, the gain theory is dead too:

```bash
ssh markb@192.168.0.17 'D=/dev/v4l/by-id/usb-USB_CAMERA_USB_CAMERA_240725172848-video-index0
v4l2-ctl -d $D --set-ctrl=gain=32 --set-ctrl=auto_exposure=3
v4l2-ctl -d $D --set-fmt-video=width=1920,height=1080,pixelformat=MJPG \
  --stream-mmap --stream-count=25 --stream-skip=24 --stream-to=/tmp/wc.jpg'
```

**Always warm up.** `--stream-count=1` grabs the very first frame, before auto-exposure
converges, and will read as black on a perfectly good camera. Use `--stream-skip` to discard
~24 frames first — the macOS host used a 15-frame warmup for the same reason.

---

## ✅ 06-Aug-2026 daylight retest — the webcam is FIXED, the dashcam has a new problem

### `usb-webcam-1080p` — CONFIRMED WORKING. The gain theory was right.

Full 1920x1080 daylight frame: grass, poultry netting, the truck, sharp and correctly exposed —
**mean 128.9, std 30.8, 0.0% clipped**. It is archiving again after roughly a day of nothing.

**The camera was never broken.** Its V4L2 `gain` was pinned at **0** against a default of 32,
which blackens the output on any host, and that single fact explains its entire recorded history
— the pure-black frames on GWTC and the "video interface present but useless" behaviour on the
Air. `FARMCAM_V4L2_CTRLS=gain=32,auto_exposure=3` in `/etc/farmcam/usb-webcam-1080p.env` is the
whole fix.

**Update `HARDWARE_INVENTORY.md` and `CLAUDE.md` accordingly** — every "intermittent / loses its
video function / needs a physical replug" warning about this camera predates the gain discovery
and should no longer send anyone out to replug it. Watch it for a few days before deleting those
notes entirely, but it is not the flaky camera it was believed to be.

### 🟡 `jieli-dashcam` — badly overexposed in daylight, and NOT fixable from software

Night frames are excellent. The first daylight frame is washed out: **mean ~220, ~41% of pixels
clipped white**. Everything below was measured on the live endpoint and **none of it helped**:

| Attempt | Result |
|---|---|
| `gain` swept 128 → 6 | mean stayed 221.1–221.7. No effect. |
| `brightness` swept 128 → 0 | mean stayed 221.9–226.0. No effect. |
| `FARMCAM_FOURCC=auto` | mean 218.5, 40.1% clipped |
| `FARMCAM_FOURCC=YUYV` | mean 220.1, 41.4% clipped |

**Its exposure controls are stubs.** `v4l2-ctl --list-ctrls` reports `auto_exposure`,
`exposure_time_absolute` and `exposure_dynamic_framerate` all as `min=0 max=0`,
`read-only, write-only`. `gain` and `brightness` accept writes and are simply ignored. This is a
car dashcam with a fixed internal auto-exposure tuned for night driving; there is no UVC lever to
pull. Config has been returned to defaults — do not leave it looking "tuned" when nothing tuned it.

**⛔ RETRACTED: a libv4l/YUYV path does NOT fix this.** An intermediate finding in this session
claimed MJPG gave mean 223.6 while a YUYV capture gave 114.3, and called it decisive. **It was a
measurement error.** The camera advertises *only* MJPG, so
`v4l2-ctl --set-fmt-video=pixelformat=YUYV` never produced clean YUYV — the captured file was not
a whole multiple of a 640×480×2 frame, so slicing `[0::2]` as a "Y plane" was averaging compressed
JPEG bytes, which land near 114 by coincidence. Re-tested properly through the HTTP endpoint, YUYV
is no better than MJPG. **Do not re-run this experiment expecting a different answer, and do not
trust a pixel statistic from a file whose size is not a whole multiple of the frame size.**

**`auto_exposure` was tried too and the camera REFUSES it.** `v4l2-ctl --set-ctrl=auto_exposure=<0|1|2|3>`
returns **`Error setting controls: Permission denied (VIDIOC_S_EXT_CTRLS)`** for every value. The
control is advertised but not implemented. There is no software exposure lever on this camera —
that is now exhaustively established, so **do not spend another session looking for one.**

**Proof that the host is NOT processing the image** (asked directly by Boss, 06-Aug-2026).
Captured with the service stopped, straight off the sensor with `v4l2-ctl`, versus what
`/photo.jpg` served seconds later:

```
RAW off the sensor (camera_host.py not involved)   mean 192.1  std 67.7
served by /photo.jpg                                mean 192.7  std 63.1
```

Identical within re-encode and a few seconds of scene drift. **The washout is the camera.** If
this question comes up again, re-run that comparison rather than arguing about it.

**What is actually left to try** (all physical or downstream, in rough order of cost):
1. **Re-aim it.** Boss re-aims this camera often. It is currently pointing across a bright
   sky-and-treeline scene; the well-exposed macOS daylight frames from 05-Aug were a shadier
   garden view. This may be nothing more than a hard backlit aim.
2. A neutral-density filter or shade hood over the lens.
3. Accept it: it is time-lapse material, never a gem, and it is excellent after dark.

**Do NOT reintroduce an image-processing layer to claw the highlights back.** Boss removed all
processing from this host deliberately, and 41% of the pixels are clipped to pure white — that
data is gone, and no amount of tone-mapping invents it back.

## Not done yet

1. **Static DHCP lease** — reserve `192.168.0.17` against `88:a2:9e:a2:e6:23` on the Archer
   AX55, and add an `/etc/hosts` entry on the Mini. Boss approved this; it is not done.
2. **`ffmpeg` is not installed**, nor any camera-host service. Frames above were captured with
   `v4l2-ctl`, which ships with the image.
3. **`camera_host.py` (Linux, path-only identity) + `farmcam@.service`** — plan TODOs 5–7.
4. **Repoint both config files** at the Pi, and **reduce the MacBook Air to one camera**
   (plan TODO 8/8b).
5. **USB power budget** — plan TODO 3. The dock is powered, which is what matters, but this has
   not been measured on the Pi.

## Live consequence right now

**Both USB cameras have physically left the MacBook Air**, whose device list is now just its
built-in FaceTime plus screen capture. Until the Pi serves them:

- `jieli-dashcam` — **offline**, its config still points at `192.168.0.50:8091`
- `usb-webcam-1080p` — **offline**, config points at `192.168.0.50:8090`
- `macbook-air-facetime` — unaffected, still serving on `:8089`

Also noted: **the Mac Mini's own IP has drifted to `192.168.0.217`** (docs said `.54`, before
that `.71`).
