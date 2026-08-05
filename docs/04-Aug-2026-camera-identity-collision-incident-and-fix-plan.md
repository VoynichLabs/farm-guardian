# 04-Aug-2026 — Three camera services, one camera: identity-collision incident + fix plan

**Author:** Claude Opus 5
**Date:** 04-Aug-2026
**Status:** containment DONE (live), code fix AWAITING BOSS APPROVAL, GWTC re-wiring AWAITING one answer from Boss

---

## One-paragraph summary

The powered USB hubs went in today. During the reshuffle the generic 1080p USB webcam left the
MacBook Air (it is now on GWTC). All three `usb-cam-host` services on the Air restarted at
19:29–19:30 local, and **all three resolved to the same cv2 index** — so `macbook-air-facetime`,
`usb-webcam-1080p` and `jieli-dashcam` were every one of them serving the **built-in FaceTime
camera** for 23 minutes. The v2.57.0 identity gate, whose entire promise is "serves nothing
rather than guessing", waved two of them through. Containment is done and verified; the code
gap that allowed it is described below and is **not yet fixed**.

---

## What was actually observed (evidence, not inference)

### The Air is down to two cameras

`ffmpeg -f avfoundation -list_devices` on `192.168.0.50`:

```
[0] FaceTime HD Camera
[1] USB PHY 2.0 #3          <- the dashcam
[2] Capture screen 0
```

There is **no "USB CAMERA"** — the generic 1080p webcam is physically gone from the Air.
`system_profiler SPCameraDataType` agrees (FaceTime + USB PHY 2.0 #3 only).

### All three endpoints were serving one camera

Byte-level proof, gathered by fetching `/photo.jpg` from :8089/:8090/:8091 **concurrently**
(backgrounded curls, not a sequential loop) and hashing:

| round | 8089 | 8090 | 8091 |
|---|---|---|---|
| 0 | A | A | A |
| 1 | A | B | B |
| 2 | A | B | B |
| 4 | A | B | A |
| 5 | A | B | A |
| 6 | A | A | B |

Every pair matched **byte-identically** at some point. Two physically distinct cameras cannot
produce identical JPEG bytes; the shuffling pairings are just three readers of one device
holding their latest frame at different phase. This is the discriminator to reach for in
future — framing similarity proves nothing here, because the cameras on the Air overlook
overlapping ground.

### The logs show exactly how each one went wrong

```
# macbook-air-facetime, 15:47 — correct answer, but on a thin margin
cv2 index 0 differs from the 'FaceTime HD Camera' reference by 24.8
cv2 index 1 differs from the 'FaceTime HD Camera' reference by 14.5
-> cv2 index 1 (difference 14.5, next best 24.8)

# usb-webcam-1080p, 19:29 — its camera was NOT on the machine
no AVFoundation video device matches 'USB CAMERA' — device is not currently plugged in   (x4)
...
cv2 index 1 differs from the 'USB CAMERA #4' reference by 37.1
-> cv2 index 1 (difference 37.1, next best n/a)          <-- ACCEPTED A 37.1 MISMATCH

# jieli-dashcam, 19:30
cv2 index 1 differs from the 'USB PHY 2.0 #3' reference by 32.6
-> cv2 index 1 (difference 32.6, next best n/a)          <-- ACCEPTED A 32.6 MISMATCH
```

For scale, here is what a **true** match looks like, from the clean restart during containment:

```
cv2 index 0 differs from the 'USB PHY 2.0 #3' reference by 0.4
cv2 index 1 differs from the 'USB PHY 2.0 #3' reference by 31.7
-> cv2 index 0 (difference 0.4, next best 31.7)
```

**0.4 versus 31.7.** The signal is enormous and was available. It simply was not checked.

---

## Root cause

`tools/usb-cam-host/usb_cam_host.py::_resolve_verified_device_index()`, acceptance tail:

```python
runner_up = scored[1][0] if len(scored) > 1 else None
if runner_up is not None and runner_up - best_difference < _SIGNATURE_MIN_MARGIN:
    ...  return None
```

The only guard is a **relative** one: best must beat runner-up by 8.0. When `scored` has a
single entry, `runner_up is None`, the whole `if` is skipped, and `best_difference` is never
examined at all. **Any** difference is accepted — 37.1 included.

Why there was only one candidate: the sibling service already held the other cv2 index open,
so `cv2.VideoCapture(idx).isOpened()` was False for it and the loop `continue`d. The log's
`OpenCV: out device of bound (0-1)` lines confirm only indices 0–1 exist and only one was
reachable. So the failure is **contention-induced**: whichever service starts first takes an
index, and every later service is presented with exactly one openable candidate and rubber-
stamps it, however badly it scores.

Two aggravating factors worth writing down so nobody re-derives them:

1. **The picture test is at its weakest on this machine specifically**, because the Air's two
   cameras overlook overlapping ground. The code comment already predicted this ("Two cameras
   aimed at the same scene from the same spot would defeat this"). It is no longer a
   hypothetical.
2. **`macbook-air-facetime`'s legitimate self-match scores badly** — 14.5, 19.9, versus the
   dashcam's 0.4. A reference frame captured from a camera by name and compared against that
   same camera seconds later should score near zero. It does for the dashcam and not for
   FaceTime. That asymmetry is unexplained and matters, because it is what makes a simple
   absolute threshold hard to place (see Option A below).

---

## Containment — DONE, verified, live

Performed 19:52–19:55 local:

1. `launchctl bootout` `com.farmguardian.cam-usb-webcam-1080p` on the Air. Unambiguous: that
   camera is not on that machine, so the service had nothing legitimate to serve.
2. `bootout` both remaining services, then `bootstrap` them **one at a time**, so each resolved
   against an uncontended device list.
3. Result: `jieli-dashcam` → cv2 index 0 (difference **0.4**, next best 31.7);
   `macbook-air-facetime` → cv2 index 1.

Verified by the same concurrent-fetch test: over three rounds the two endpoints never once
produced matching bytes, `mean|diff| = 52.5`. Eyeball check confirms each now matches its
documented view — :8089 is the run, :8091 is the wide view.

`usb-webcam-1080p` is **left stopped** pending the decision below. Both config files still
point it at `192.168.0.50:8090`, which is now dead, so Guardian and the pipeline will log
capture failures for that one camera until it is repointed. That noise is deliberate and
preferable to publishing another camera's frames under its name.

---

## Archive contamination — bounded, zero downstream exposure

Rows written under a camera id that was not the camera:

| camera_id | rows | window (UTC) |
|---|---|---|
| `usb-webcam-1080p` | 642 | 2026-08-04T23:30:26 → 23:52:51 |
| `jieli-dashcam` | 46 | 2026-08-04T23:30:10 → 23:52:45 |

Both are actually **FaceTime footage**. `macbook-air-facetime` rows in the window are correct.
38 of these rows are exact sha256 duplicates across camera ids — the smoking gun in the DB.

**Nothing escaped.** Across the whole window, for both affected ids:
`discord_reactions = 0`, `discord_message_id` null, `ig_posted_at` null, `ig_story_posted_at`
null, `reel_posted_at` null. No reel was built from it; the 21:30 dashcam reel had not yet run.

Precedent handled the same way as the 21-Jul→23-Jul `mba-cam` case: recorded in
`HARDWARE_INVENTORY.md`, rows left in place rather than deleted.

### 🔴 Tonight's dashcam reel is HELD — restore it

`com.farmguardian.ig-jieli-dashcam-timelapse-reel` fires 21:30 local and
`select_timelapse_gems` takes `image_tier='raw'` frames from a **24h** window ranked by
sharpness. The 46 mislabeled rows sit at 19:30–19:52 local, inside both the 24h window and the
06:00–20:00 daylight filter, so tonight's run could have put FaceTime footage on Instagram
under the dashcam's name — the 23-Jul failure exactly. A documentation warning enforces nothing.

**Action taken:** the lane is booted out and its plist parked as
`com.farmguardian.ig-jieli-dashcam-timelapse-reel.plist.HELD-restore-after-05Aug2026-0130Z`.

Only **tonight** is affected. Tomorrow's 21:30 run has a cutoff of 2026-08-05T01:30Z, which is
already past the contaminated rows, so it is clean without intervention.

**Restore (after 2026-08-05T01:30Z):**

```bash
mv ~/Library/LaunchAgents/com.farmguardian.ig-jieli-dashcam-timelapse-reel.plist.HELD-restore-after-05Aug2026-0130Z \
   ~/Library/LaunchAgents/com.farmguardian.ig-jieli-dashcam-timelapse-reel.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.farmguardian.ig-jieli-dashcam-timelapse-reel.plist
```

**Alternative Boss may prefer:** purge the 46 mislabeled `jieli-dashcam` rows (and their raw
files) instead and let tonight's reel run. That trades one night's reel for an irreversible
delete of 46 junk rows, so it was not done unasked.

---

## Scope

**In scope**

- Close the single-candidate acceptance hole in `_resolve_verified_device_index()`.
- Make a contention-induced single candidate resolve to "unproven, retry", not "proven".
- Repoint `usb-webcam-1080p` to its new host once Boss confirms identity (below).
- Record the contamination window in `HARDWARE_INVENTORY.md`; CHANGELOG entry.

**Out of scope**

- Retries/thresholds/config options to paper over the USB power problem. The powered hub is
  the fix and it is now fitted.
- Touching the `os._exit(1)` self-heal from v2.61.0.
- The `gwtc` camera entry (the dead built-in on the MediaMTX/RTSP path). It stays disabled;
  it is a different device on a different code path and must not be reused as an id.
- Any change to the Mac Mini. See below — there is no camera on its bus to serve.

---

## Open decision for Boss (blocks the re-wiring, not the code fix)

GWTC (`192.168.0.69:8089`) is now serving a healthy **1920x1080** USB webcam
(`VID_32E6&PID_9221`, Status OK, 0 failures) — and **no config consumes it**. The Air lost its
1080p "USB CAMERA" at the same moment GWTC gained one, which strongly suggests Boss carried
the same unit over.

- **If it is the same physical webcam** → repoint the existing `usb-webcam-1080p` id at
  `http://192.168.0.69:8089` in both config files. Archive continuity preserved; this is the
  documented "camera moved hosts = change one URL in each file" path.
- **If it is a different webcam** → it needs its own id via `scripts/add-camera.py`, and
  `usb-webcam-1080p` should be retired or left down.

Guessing here is exactly the device-not-location naming mistake the inventory doc exists to
prevent, so it is a question, not an assumption.

---

## Mac Mini — nothing to do in software

The new hubs are on the bus (VIA Labs USB2.0/USB3.1, GenesysLogic USB2.1/USB3.1). **No camera
is.** `ioreg` shows only a wireless mouse, a USB keyboard, a Raycue SSD enclosure and a USB
PnP sound device; `SPCameraDataType` is empty. The Mini's own host plist is still parked at
`com.farmguardian.usb-cam-host.plist.idle-24apr2026`.

Per the repo's own triage rule, a camera absent from the bus is hands-on, not software. Do not
start a service for a camera that is not plugged in.

---

## Proposed fix — options, for Boss to pick

### Option A — absolute acceptance ceiling

Reject any best match above a fixed difference, regardless of runner-up.

- *For:* two lines, catches this exact incident (37.1 and 32.6 both rejected).
- *Against:* the threshold is hard to place. Legit matches measured 0.4 (dashcam) but also
  14.5 and 19.9 (FaceTime); wrong matches measured 24.8–37.1. The gap between 19.9 and 24.8 is
  thin, and a ceiling in it is one bad FaceTime reference frame away from refusing to resolve
  a camera that is sitting right there. Not safe on its own without first explaining the
  FaceTime asymmetry.

### Option B — contention-aware proof (recommended)

Count indices skipped because `isOpened()` was False. If any index was unreachable, a
single-candidate result is **not** proof: log it plainly and return `None` so the grabber
retries. The genuine one-camera-on-the-box case is unaffected — it already short-circuits
earlier via `other_names` being empty.

- *For:* attacks the actual mechanism. Needs no magic number. Turns "I had no choice so I took
  it" into "I could not see enough to be sure", which is the honest reading.
- *Against:* a sibling that holds its index forever means a later service can never probe it,
  so on its own this could leave a camera permanently unresolved. Needs pairing with the
  v2.61.0 stall self-heal (already shipped) and a startup ordering that lets each service
  resolve before the next begins — the plists' existing `USB_CAM_START_DELAY` (0/25/50) was
  meant to do this and was not sufficient today.

### Option C — B, plus fix the reference-frame quality first

**Lead worth chasing first (measured today, not yet conclusive):** an attempt to capture repeat
ffmpeg-by-name reference frames from both Air cameras **hung and had to be killed** — the live
`usb-cam-host` services hold those devices, so ffmpeg could not open them. That is exactly what
`_reference_signature_by_name()` does on every resolution attempt. If the reference capture is
racing a camera that is already held, a degraded or stale reference would explain FaceTime's
14.5–19.9 self-scores against the dashcam's 0.4 — and it would mean the comparison logic is
fine and the *reference* is the weak link. Re-run the probe during a maintenance window with
the services stopped: capture two references from the same camera seconds apart and score them
against each other. Two references from one camera differing by ~15 proves the reference is the
problem and reframes the whole fix.


Explain and fix why FaceTime self-scores 14.5–19.9 when the dashcam scores 0.4. If a good
reference makes every legitimate match land near zero, then A becomes safe too and the two
together are belt-and-braces.

- *For:* strongest end state; makes identity provable rather than comparative.
- *Against:* most work, and it is investigation before it is a patch.

**Recommendation: B now (it stops the bleeding without a magic number), then C as a follow-up.**
A alone is a trap given the numbers actually measured today.

---

## TODOs (ordered)

1. ~~Prove or disprove the collision with a concurrent byte-comparison~~ — done.
2. ~~Contain: stop the impossible service, restart the rest serially, verify distinct~~ — done.
3. ~~Bound the archive contamination and check downstream exposure~~ — done, zero exposure.
4. **Boss decides:** GWTC webcam identity (same unit or new one?).
5. **Boss decides:** fix option A / B / C.
6. Implement the chosen option in `tools/usb-cam-host/usb_cam_host.py`; update the file header.
7. Repoint or re-add `usb-webcam-1080p`; edit **both** `config.json` and
   `tools/pipeline/config.json`; `launchctl kickstart -k` both `com.farmguardian.guardian` and
   `com.farmguardian.pipeline`.
8. Verify: concurrent-fetch byte comparison across every live endpoint must show no matches;
   each `/health` must report the expected `resolved_device_name`; pull `/photo.jpg` and look
   at it.
9. Docs: `HARDWARE_INVENTORY.md` contamination record + new host for the 1080p webcam;
   `CLAUDE.md` camera roster row; CHANGELOG top entry.

---

## Verification recipe (reusable — this is the useful part)

Framing similarity is worthless on this farm because cameras overlook shared ground. Use bytes:

```bash
for p in 8089 8090 8091; do curl -s -o /tmp/c$p.jpg http://192.168.0.50:$p/photo.jpg & done; wait
md5 -q /tmp/c8089.jpg /tmp/c8090.jpg /tmp/c8091.jpg
```

The fetches **must** be concurrent. Any two hashes matching means two services are on one
camera. Repeat 3–5 rounds; a collision may not show on every round, but across rounds a
duplicated camera always reveals itself.

---

## Gotchas that cost time today

- Editing a plist env var on the Air needs `bootout` + `bootstrap`. `kickstart` re-runs from
  launchd's cached plist and silently ignores the edit.
- `/health`'s `resolved_device_name` is recorded at resolution time and is **not** re-verified.
  It happily reported `USB CAMERA #4` for a device that was no longer on the machine. Treat it
  as "what this process believed at startup", never as current truth.
- `acquire_stalled_s: 0.0` on all three throughout. The v2.61.0 self-heal is working as
  designed and is **not** relevant here: these services were not stalled, they were confidently
  serving the wrong camera. A different failure class, invisible to that watchdog.
