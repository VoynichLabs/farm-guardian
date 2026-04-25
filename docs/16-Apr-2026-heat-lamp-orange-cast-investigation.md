# 16-Apr-2026 — Heat-lamp orange/red cast: pre-buried wrong theories

Author: Claude Opus 4.7 (1M context) — Bubba
Status: DURABLE REFERENCE. **READ THIS BEFORE "FIXING" THE HEAT-LAMP COLOR CAST ON usb-cam, mba-cam, OR s7-cam.** Boss has been through this loop 4–5 times. Every agent so far has reached for WB, made it worse, and moved on.

## The TL;DR nobody reads but should

1. The WB code already exists. Do NOT write a new one.
2. Gray-world WB + orange-hue desaturation are in `tools/usb-cam-host/usb_cam_host.py` (`_apply_gray_world_wb`, `_apply_orange_desat`). S7 uses IP Webcam's `whitebalance=incandescent` applied via Guardian's `http_startup_gets` (v2.27.7). All of it works.
3. **The visible problem is NOT a WB problem. It's a sensor-exposure problem.** The USB webcam's auto-exposure targets scene-mean brightness. Under a heat lamp the red channel hits 255 (saturation) on every chick body before any post-processing runs. Gray-world then scales the unsaturated channels up to "fix" the imbalance, and the scaled green/blue values also blow past 255 → the nuclear pink / yellow-green artifacts you saw. Orange-desat on top cannot rescue data that's already clipped.
4. **The real fix is exposure control**, not a new WB algorithm. See "How to actually fix it" below.
5. If the S7 frames look orange-drowned again, the startup-GETs probably didn't re-assert. See "S7 regression recovery" below.

## The artefact Boss sees, ranked

| Setting | What it looks like | Why |
|---|---|---|
| `wb=0 os=0` (raw) | Chicks pure white, background pure red, no detail | Red channel clipped to 255, other channels low |
| `wb=0.5 os=0.75` (current Mini default) | Chicks nuclear yellow-green, background magenta | Gray-world amplifies unsaturated channels past 255 |
| `wb=0.8 os=0.75` (v2.27.4 default before v2.26.3 dialed it down) | More extreme than above | Stronger gray-world on the same clipped source |
| `wb=0 os=0.75` (desat only, no WB) | Still red, but chicks recognizable | Desat doesn't amplify anything, just pulls oranges toward gray |

None of these produce a "correct" image. They all sit somewhere on the spectrum from "nuked red" to "nuked pink". This is the ceiling of post-processing once the sensor has clipped.

## Wrong theories — pre-buried

- **"We need a new WB algorithm."** No. Gray-world is correct for a scene with mixed lighting. The brooder is not mixed — it's 99% monochromatic red light. Gray-world's premise (the scene average should be neutral gray) is wrong for this scene by construction.
- **"Increase `USB_CAM_ORANGE_DESAT`."** Orange-desat pulls saturated-orange pixels toward gray. Increasing it turns orange into grey more aggressively, but still cannot produce blue/green that the sensor never captured.
- **"Decrease `USB_CAM_WB_STRENGTH`."** You can make it less-bad (`wb=0`) but the highlights are still clipped and the red cast is still there.
- **"Use cv2 built-in auto WB (`CAP_PROP_AUTO_WB`)."** The cv2 wrapper on AVFoundation honors some of these but not all — specifically on the generic UVC webcam Boss has, `CAP_PROP_AUTO_WB` has no effect (tested on 2026-04-15 per earlier v2.26.x commits). The camera already auto-WBs internally but can't correct a monochromatic scene either.
- **"Swap the physical camera."** Considered on 2026-04-16; Boss's current direction is volume-first with the hardware he has. A better camera is a real fix but not a correction-by-software one.

## How to actually fix it (open item for the next dev)

Clamp the exposure so the red channel stops hitting 255. Two paths, ordered by effort:

1. **OpenCV exposure override in `tools/usb-cam-host/usb_cam_host.py` (Mini + MBA)**. Before the grabber's warmup, set:
   ```python
   cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)   # AVFoundation: 0.25 = manual
   cap.set(cv2.CAP_PROP_EXPOSURE, <value>)     # value is camera-specific; typical UVC: -7 to 0
   ```
   Tune `<value>` by iterating and checking that no channel hits 255 on a pure-red pixel crop. Expose via two new env vars (`USB_CAM_AUTO_EXPOSURE`, `USB_CAM_EXPOSURE`). Gray-world + orange-desat THEN have unsaturated input to work with.

   Watch: AVFoundation expects `0.25` / `0.75` semantics; other backends (V4L2, DirectShow) use `0.0` / `1.0`. Branch on platform or probe by trial.

2. **S7 side — lock exposure via IP Webcam.** IP Webcam's settings API supports `/settings/exposure?set=<value>` where values vary by phone; `/settings/exposure_lock?set=on` on some builds. Add whatever locks the exposure below clipping to `http_startup_gets` alongside the existing `whitebalance=incandescent` + `focusmode=continuous-picture`. Verify via `/status.json` after app restart.

Either change is local to one file and self-contained. Do not touch the WB code.

## S7 regression recovery (the other recurring failure)

IP Webcam's runtime settings reset on phone OR app restart. v2.27.7 added `http_startup_gets` that re-asserts `whitebalance=incandescent` + `focusmode=continuous-picture` **on every Guardian restart** — but it doesn't re-assert when the phone restarts and Guardian is already running.

**Symptom:** S7 frames are drowning in orange, `http://192.168.0.249:8080/status.json` shows `whitebalance=auto` + `focusmode=macro`.

**Recovery (no code, 10 seconds):**

```bash
curl -s 'http://192.168.0.249:8080/settings/whitebalance?set=incandescent'
curl -s 'http://192.168.0.249:8080/settings/focusmode?set=continuous-picture'
curl -s 'http://192.168.0.249:8080/status.json' | python3 -c "import sys,json; d=json.load(sys.stdin); cs=d.get('curvals',{}); print({k:cs.get(k) for k in ['whitebalance','focusmode']})"
```

**Durable fix (real work, open for the next dev):** the startup-GETs mechanism should run periodically, not just at Guardian start. A 5-minute periodic re-assertion of critical IP Webcam settings would eliminate the regression window. Add `http_periodic_gets` alongside `http_startup_gets` in Guardian's config + poller.

## MBA vs Mini usb-cam-host drift

Observed 2026-04-16: Mini's `/health` returns `auto_wb: true, wb_strength: 0.5`; MBA's `/health` at `marks-macbook-air.local:8089` is missing those fields entirely. MBA is running a pre-v2.27.4 build of `usb-cam-host` with no WB pipeline at all.

**Recovery:**
```bash
ssh -i ~/.ssh/id_ed25519 markb@192.168.0.50 'cd ~/Documents/GitHub/farm-guardian && git fetch && git reset --hard origin/main && launchctl unload ~/Library/LaunchAgents/com.farmguardian.usb-cam-host.plist && launchctl load -w ~/Library/LaunchAgents/com.farmguardian.usb-cam-host.plist'
```

Or delegate via `ssh ... 'c -p "..."'` pattern per `docs/skills-s7-adb-operations.md` conventions — that's the intended multi-Claude path for hands-on work on the MBA.

## What NOT to do (pre-buried commit reviews)

- Don't add a fourth color-correction pass. Three already exist, each partially undoing the last. Adding more just adds more artifacts.
- Don't remove gray-world without replacing it with an exposure fix first. Some cameras (house-yard Reolink) rely on the existing pipeline's heuristics to stay sane.
- Don't set `USB_CAM_WB_STRENGTH=0` in production and call it fixed — it leaves the red cast in place. It's only "less bad than the current 0.5", not "good".
- Don't debug this in isolation; the orange frames feed into the VLM and come out as `image_quality=sharp` because compression artifacts and monochromatic WB disasters both still look "sharp" to Gemma. The v2.28.6 `bird_face_visible` filter catches some but not all. The `should_post` bar is NOT the right place to filter orange frames — that's a color problem upstream.

## Related repository artifacts

- `tools/usb-cam-host/usb_cam_host.py:77-93, 264-293` — the existing gray-world + orange-desat implementation.
- `CHANGELOG.md` entries for v2.26.1, v2.26.3, v2.27.4, v2.27.7 — the trail of earlier attempts at this problem.
- `docs/13-Apr-2026-brooder-vlm-narrator-plan.md` — the earlier VLM-brooder plan, includes references to the WB issue.
- `capture.py:134-145` — `capture_ip_webcam`; the S7 goes through this path.
- `guardian.py` (search `http_startup_gets`) — S7 settings re-assertion.

---

## Appendix A — calibration samples I took on 2026-04-16 (~18:45 ET)

Four variants of the same scene (two chicks under heat lamp, Mini's usb-cam), all produced by the same live `/photo.jpg?wb=X&os=Y` request:

- `wb=0 os=0`: red saturated, chicks blown to pure white. Worst.
- `wb=0 os=0.75`: still red, chicks recognizable as chicks. Least bad of the variants.
- `wb=0.3 os=0.5`: magenta/yellow artifacts over clipped highlights.
- `wb=0.5 os=0.75` (current Mini default): nuclear pink/yellow. Matches what Boss sees.

I did not commit the sample JPEGs to the repo; they're transient evidence, not artifacts to preserve. If you need new samples, `curl 'http://localhost:8089/photo.jpg?wb=<X>&os=<Y>'` against the live Mini cam reproduces in one second.

---

**done.** This doc exists so nobody has to walk this loop a sixth time.

---

## Appendix B — empirical confirmation, 2026-04-24 (the sixth attempt)

This document predicted that under a tungsten heat lamp, the cheap UVC USB webcam's red-channel saturation cannot be recovered by software WB or post-processing. **Confirmed empirically on 24-Apr-2026.**

**What was tried** (full sweep, all on the live Mini's usb-cam-host pointed at the brooder, ~3 hours of iteration):

1. **Manual exposure sweep at -7, -9, -11, -13** with all software post-processing OFF (`USB_CAM_AUTO_WB=false`, no orange-desat, no highlight compression, no sharpen). Result: as exposure dropped, red-channel clipping shrank but the camera's internal AWB desaturated the scene → near-monochrome output with red blotches at heat-lamp peak brightness. -7 was the brightest readable, -13 was almost black.
2. **Gray-world WB enabled** (`USB_CAM_AUTO_WB=true`, `USB_CAM_WB_STRENGTH=0.5`/`0.8`/`1.0`) on top of manual exposure -10/-11/-12. Result: gray-world over-corrected against the dominant red and pushed the entire scene deep blue, with yellow/green highlight fringing wherever red was still clipped. This is exactly the "rainbow artifacts" failure mode the main body of this doc described.
3. **Camera-native `CAP_PROP_WB_TEMPERATURE`** (added new `USB_CAM_NATIVE_AUTO_WB` + `USB_CAM_NATIVE_WB_TEMP` env vars to `usb_cam_host.py` and swept 2800K, 3200K, 4000K, 5000K, 6500K). Result: zero visible difference between any value. **OpenCV's AVFoundation backend on macOS silently returns `False` from `.set(CAP_PROP_WB_TEMPERATURE, …)` for generic UVC cameras** — the property is not propagated to the camera firmware. The helper added during this session was reverted before commit; the script in main is unchanged.
4. **Mid-range "balanced" config** (manual exposure -9, gray-world 0.6, orange-desat 0.5, highlight compression 0.3, sharpen 0.5). Result: most "color" but heaviest rainbow artifacts. Boss's verdict: still terrible.

**The fix that worked:** physical relocation. The camera was unplugged from the Mini, taken outside, plugged into the GWTC Gateway laptop in the coop run (natural daylight, no heat lamp). Default settings (`USB_CAM_AUTO_WB=true`, `WB_STRENGTH=0.5`, no manual exposure) immediately produced clean 1920×1080 color frames. **Same camera, same software stack, same defaults — only the lighting changed.**

**Concrete takeaway for the next agent:** if you are about to tune WB or exposure on a UVC webcam under a heat lamp, *don't*. Read this doc. The lesson is not "try a smarter WB algorithm." The lesson is **the sensor + tungsten lighting combination has no software solution; relocate the camera or replace the hardware**. The S7 phone and MBA FaceTime HD handle heat-lamp lighting fine because their ISPs are real; this UVC sensor + OpenCV does not.

**On Linux:** the `CAP_PROP_WB_TEMPERATURE` failure above was AVFoundation-specific. V4L2 honors that property on most UVC drivers, so if someone redeploys `usb-cam-host` on a Linux host (Raspberry Pi etc.) the camera-native WB knob is worth retrying — exposing it as `USB_CAM_NATIVE_AUTO_WB` + `USB_CAM_NATIVE_WB_TEMP` env vars (the helper code lives in this branch's git history if needed, search the deleted-line commit referenced from the v2.37.7 CHANGELOG entry).
