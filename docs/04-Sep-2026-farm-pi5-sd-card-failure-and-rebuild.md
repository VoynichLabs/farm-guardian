# 04-Sep-2026 — `farm-pi5` SD card went blank; card rebuilt, camera host restored

## What happened

`farm-pi5` stopped dead at **Thu 03-Sep-2026 10:26:29 local** and stayed down 27+ hours.

**Root cause: the SD card lost its entire contents.** Read back on the Mac Mini, sector 0 and
the whole first gigabyte were `0xFF` — erased flash. No partition table, no FAT32 `bootfs`.
The card read fast with **zero I/O errors**, so the hardware is probably fine; the data is
simply gone. Nothing was recoverable.

It stayed down after Boss's reboot because he had already pulled the card to inspect it. A Pi
with no card cannot boot — that part was mechanical, not a second fault.

### ⚠️ macOS "uninitialized" is normally a RED HERRING — but not this time
macOS cannot read ext4, so a *healthy* Pi card routinely shows as uninitialized. That was
checked first and **ruled out**: the FAT32 boot partition, which macOS reads perfectly well,
was also absent. Always check for the FAT32 `bootfs` before concluding a card is dead.

---

## 🔴 The watchdog told Boss to flip the breaker. It was WRONG.

`birdcatraz-watchdog` posted at 03-Sep 10:31:52:
> "Birdcatraz power is out … `house-yard`, `duo2` also down … the breaker needs flipping by hand."

**The circuit never tripped.** Boss made the trip for nothing.

Evidence, from `image_archive`:
- `duo2` archived a frame at **exactly 14:31:51Z — the same second it was declared down.**
  Its largest inter-frame gap that entire hour was **13s** (median 10s).
- `house-yard` max gap that hour was **46s**, which is its normal cadence (median 45s).
- Both kept serving continuously for the next 27 hours with no breaker touched.

### Why it misfired
`classify_outage()` in `tools/birdcatraz-watchdog/watchdog.py`:

```python
verdict = "circuit" if down else "pi-only"
```

**Any single outdoor device failing ONE un-retried TCP probe flips the verdict to "circuit".**
No retry, no corroboration, no cross-check against whether the device is producing frames.

**Mechanism was a fast refusal, not packet loss** — and the timing proves it. Pi-probe-fail
logged `10:31:45,888`, classification logged `10:31:51,906` = **6.018s** for three sequential
probes with `PROBE_TIMEOUT_S = 6.0`. Two timeouts would need ≥12s, so at most one probe timed
out and the other failed *immediately* — refused/unreachable. Probable cause is connection-slot
contention (Guardian holds a persistent RTSP session to `duo2:554` and polls `house-yard:80`
every ~45s). **Consequence: raising the timeout would NOT have prevented this. Do not "fix" it
that way.** The false negative is fact; the contention mechanism is unconfirmed.

### A quorum does NOT fix it (rejected)
`down = ['house-yard','duo2']` — two devices. Any ">= 2 devices down" rule still returns
"circuit" and still fires the wrong alert.

### ✅ FIXED in v2.71.8 — implemented same day
Plan: `docs/04-Sep-2026-watchdog-circuit-verdict-fix-plan.md`.

A device counts as UP if **EITHER**:
1. it produced an `image_archive` frame within ~3x its median cadence (house-yard 45s, duo2 10s), **OR**
2. its TCP port answers.

Only when **neither** holds is it down. Both are independent positive proof of power; only
their joint absence is evidence of power loss. The frame check is a local SQLite read — no
network, no contention — and it is the signal this repo already trusts over liveness flags
(cf. `/api/cameras online=true` while capture logged its 2,830th consecutive failure).

Replayed against the 03-Sep moment: house-yard UP (frame 27s old), duo2 UP (frame 0s old),
s7-cam UP (frame stale but TCP 8080 open) → `down = []` → **"pi-only"**. Correct.
Note s7-cam genuinely wasn't producing frames then (the charging lane) but the phone had power
and answered TCP — which is exactly why the rule must be an OR, not frame-recency alone.

---

## The rebuild

- Image: **`2026-06-18-raspios-trixie-arm64-lite`** (Debian 13 trixie, same base as before).
  SHA256 verified against the published checksum.
- Written to the card behind a guard requiring `size == 62560665600` **and** Removable **and**
  USB, so it could not touch the 2 TB Samsung SSD sharing the bus.
- Seeded on `bootfs`: `userconf.txt` (`markb` + `$6$` sha512 hash) and `ssh`.
  Hash built and written **with Python, never a shell**, then verified by re-deriving it with
  `openssl passwd -6 -salt <salt>` and comparing. (`crypt` is gone in Python 3.13 — the
  bring-up log's verification snippet no longer runs; use openssl.)
- Staged `/boot/firmware/farmcam-rebuild/` with `camera_host.py`, `farmcam@.service`, both
  `.env` files and a setup script, as a keyboard-only fallback if SSH misbehaved.

### Post-boot
Hostname set to `farm-pi5` **and `/etc/hosts` `127.0.1.1` updated to match** — without the
second step every `sudo` warns `unable to resolve host`. `avahi-daemon` restarted so
`farm-pi5.local` resolves; Guardian and the pipeline address the cameras by that name, so this
is required, not cosmetic.

---

## ⚠️ Two traps found in our own docs

**1. The firstboot diagnostic in the bring-up log is STALE for trixie images.**
`docs/05-Aug-2026-birdcatraz-pi5-bringup-log.md` says to run:
```bash
grep -q "init=/usr/lib/raspberrypi-sys-mods/firstboot" /Volumes/bootfs/cmdline.txt
```
This image's `cmdline.txt` ends in `resize` instead, so that check now reports a false
**"firstboot NEVER RAN (bad image write)"** on a perfectly good card. Do not trust it on
2026-era images.

**2. `camera_host.py` needs `fastapi` and `uvicorn`, and nothing in the repo said so.**
There is no requirements file for `tools/camera-host-linux/`. Installing only `opencv-python-headless`
+ `numpy` leaves the service in a restart loop with `ModuleNotFoundError: No module named 'fastapi'`.
Full runtime dependency set: **`opencv-python-headless`, `numpy`, `fastapi`, `uvicorn`**
(system: `python3-venv`, `v4l-utils`, `libgl1`, `libglib2.0-0`).

---

## Final state

| camera | state |
|---|---|
| `usb-webcam-1080p` | ✅ **live** — 1920x1080, `camera_open:true`, 0 failures, `gain=32,auto_exposure=3` applied, frames archiving again |
| `jieli-dashcam` | ✅ **live** — replugged and re-enabled same day, 1280x720, 0 failures |

**The dashcam is a SEPARATE, EARLIER fault.** Its last archived frame is
`2026-09-02T22:14:57Z` — roughly **16 hours before** the card failed. It was already gone.
Do not attribute it to the SD card.

Cameras bind by USB serial via `/dev/v4l/by-id/`, so the old index-collision class still
cannot occur.

## Card suspicion
A card that erases itself is suspect — it may have been collateral from whatever killed the Pi,
or it may be failing. It bench-tests fine either way. **If it blanks again within weeks, replace
the card** rather than re-diagnosing from scratch.
