# 10-Aug-2026 — `com.farmguardian.s7-settings-watchdog` retired (all three of its jobs are redundant)

**Author:** Claude Opus 5 (Bubba)
**Status:** ✅ **DISABLED.** Booted out, and the plist renamed
`~/Library/LaunchAgents/com.farmguardian.s7-settings-watchdog.plist.disabled-10Aug2026`
(a `bootout` alone is not durable — the job reloads at next login).
**Trigger:** Boss's hypothesis, on watching the new phone boot: *"I think your watchdog might be
doing more harm than good. It looks like it's coming back up on its own."* He was right about the
premise. It is now off and is not to be restarted.

---

## What the watchdog did

Ran every 600 s (1,544 runs by the time it was stopped). Two jobs:

1. **Liveness check** — `curl /photo.jpg`, log `frame_ok` or `STALL`. **Detection only.** It had
   no recovery path: the old handset had no ADB host, so a stall was logged and nothing else
   happened. Its own header said so.
2. **Re-push three settings** over IP Webcam's HTTP API — `focusmode=continuous-picture`,
   `orientation=portrait`, `photo_rotation=90`.

## The evidence: a clean boot with the watchdog switched OFF

Watchdog booted out first, so this boot was genuinely unassisted. Polled `/photo.jpg` every 2 s
from the outside across a full power-cycle (10-Aug-2026, times UTC):

| Time | Observation |
|---|---|
| `18:10:35` | phone powered off — `/photo.jpg` goes dark |
| `18:15:25` | back on the LAN: errors change `Host is down` → **`Connection refused`**, i.e. TCP up, nothing listening on 8080 yet |
| `18:15:39` | **first frame: 851,668 bytes, greyscale stddev 66.7** — a real image |

**~14 seconds from network-up to a good frame, with no black-frame window and no zero-byte
window at all.** The old SM-G930F's cold-boot black-camera bug (`docs/16-Apr-2026-s7-ipwebcam-frozen-incident.md`,
fixed 2026-05-21 by killing its swipe keyguard) **does not reproduce on the new SM-G930V**, and
critically it recovered **with no watchdog running**.

### What survived the reboot on its own

Read straight from `/status.json` after boot, watchdog still off:

| Setting | After reboot | Needs a push? |
|---|---|---|
| `orientation` | `portrait` | **No** — persisted in the app |
| `focusmode` | `continuous-picture` | **No** — persisted in the app |
| `video_size` | `1920x1080` | No |
| `quality` | `99` | No |
| `photo_rotation` | **`-1`** | reverts every boot — but see below |

## Why all three pushes are redundant

- **`orientation` and `focusmode` persist in the app's own preferences** and came back correct
  unaided. Pushing them is a no-op in the good case. CLAUDE.md already noted this ("since focus +
  orientation are now persisted in-app (2026-05-21), the watchdog is redundant for those").
- **`photo_rotation` genuinely does revert to `-1` on every boot — and the code already handles
  it.** `capture.py:81` (and the mirror in `tools/pipeline/capture.py`):

  ```python
  needs_force_rot = force_portrait and im.width > im.height
  ```

  Both `config.json` and `tools/pipeline/config.json` set `force_portrait: true` for `s7-cam`, so
  **any frame arriving wider than tall is rotated 90° CW regardless of EXIF.** The docstring at
  `capture.py:59` states this was added (05-Jul-2026) precisely because the S7 reverts
  `photo_rotation` on reboot and `http_startup_gets` only fires at Guardian start. So the one
  setting that actually resets is already covered in both consumers.
- **The liveness half was worse than useless: it was actively misleading.** It logged
  `STALL … bytes=00` every 10 minutes for weeks against the retired phone while having no
  recovery path. A June revision had also pointed it at `.250` instead of `.249`, producing
  **1,907 consecutive false STALLs** with zero real signal (CHANGELOG, 20-Jul-2026). A detector
  that cries wolf on a schedule and can't act is negative value — a real outage was
  indistinguishable from the noise.

### ⚠️ Honest limit on this conclusion

**We proved the watchdog is UNNECESSARY. We did not prove it is HARMFUL.** A plausible mechanism
exists — IP Webcam restarts its video pipeline when `orientation` is set, so a tick could
interrupt a healthy camera — but the controlled test (force a tick against a working camera and
watch frames drop) **was not run.** The case for retiring it rests on redundancy, not on measured
damage. If someone later wants to argue it back, that experiment is the thing to run.

## Also corrected: an orientation misdiagnosis worth not repeating

During this session I saw `EXIF Orientation=1` after the boot and announced that every frame was
landscape and reaching the archive sideways. **That was wrong**, and Boss caught it: the frames
were fine, because `force_portrait` had already rotated them. I had read a low-level tag and
called it a defect without ever looking at the output.

**Rule, which this repo already states for the Reolink and applies just as much here: judge a
camera by the picture, not by the metadata.** `EXIF Orientation=1` on `s7-cam` is **expected**
after any reboot and is **not** a fault — `force_portrait` is the designed answer to it. Do not
"fix" it, and do not raise it as an incident.

## What replaces it

**Nothing, deliberately.** Coverage after retirement:

- **Orientation/focus** — persisted on the phone; plus `config.json → http_startup_gets` re-arms
  them on every Guardian restart.
- **`photo_rotation`** — `force_portrait` in both `capture.py` paths, per frame, independent of
  the phone's tagging.
- **Liveness** — Guardian's own snapshot poller logs failures, and the pipeline's
  `/tmp/pipeline.err.log` shows `ConnectionError … Host is down` when the phone is off. Both are
  real signals tied to real consumers, unlike a standalone curl on a timer.
- **Recovery** — genuinely new: **this phone's USB port works, so ADB exists again.** A wedged IP
  Webcam is now fixable from the Mini (`adb -s 4fad774d shell am force-stop com.pas.webcam`, then
  relaunch), which is the recovery path the watchdog never had. See
  `docs/10-Aug-2026-s7-galaxy-replacement-swap-log.md`.

## If you ever need it back

```bash
mv ~/Library/LaunchAgents/com.farmguardian.s7-settings-watchdog.plist.disabled-10Aug2026 \
   ~/Library/LaunchAgents/com.farmguardian.s7-settings-watchdog.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.farmguardian.s7-settings-watchdog.plist
```

The script itself is untouched at `deploy/s7-settings-watchdog/watchdog.sh` (repo) and
`~/Library/Application Support/farm-guardian/s7-settings-watchdog.sh` (deployed); both still
point at `192.168.0.249`, which remains correct. **Boss's instruction is that it stays off** —
don't restore it without him asking.
