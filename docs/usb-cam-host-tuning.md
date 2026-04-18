# usb-cam-host tuning reference

**Author:** Claude Opus 4.7 (1M context) — 18-Apr-2026
**Status:** DURABLE REFERENCE. Every knob on the usb-cam-host service, what it does, the measured effect, and the tuning recipe. Read this before editing the MBA plist or opening the usb_cam_host.py post-processing code.

## What usb-cam-host is, in one paragraph

`tools/usb-cam-host/usb_cam_host.py` is a FastAPI HTTP service that wraps a local UVC webcam. A daemon thread holds the camera open at ~2 Hz, buffers the latest BGR frame, and serves `/photo.jpg` + `/health`. Guardian and the VLM pipeline both pull from it. The service runs wherever the camera is physically plugged in — right now on the **MacBook Air at port 8089** (USB cam) and **port 8090** (FaceTime HD). There are two LaunchAgents on the MBA: `com.farmguardian.usb-cam-host` (primary USB cam) and `com.farmguardian.usb-cam-host-facetime` (built-in FaceTime). Both run the same script with different env vars.

## Why this doc exists

The USB cam under a brooder heat lamp produces frames that Boss keeps describing as "red, washed out, not sharp". The sensor clips the red channel to 255, and **macOS provides no userland path to change that** (see "What doesn't work" below). Everything we can do is post-processing. There are **six tuneable knobs** and their interactions are not obvious — change one and the others land differently.

## The six post-processing knobs

All are env vars. Set them in the LaunchAgent plist at `~/Library/LaunchAgents/com.farmguardian.usb-cam-host.plist` on whichever host the camera is plugged into. After editing the plist, full reload with `launchctl bootout` + `launchctl bootstrap` (kickstart -k does NOT re-read env vars).

| Env var | Default | Range | What it does |
|---|---|---|---|
| `USB_CAM_AUTO_WB` | `true` | `true`/`false` | Master switch for the gray-world WB pass. |
| `USB_CAM_WB_STRENGTH` | `0.8` (MBA: `0.5`) | `0.0 – 1.0` | Blend between identity (0) and full gray-world correction (1). |
| `USB_CAM_ORANGE_DESAT` | `0.75` | `0.0 – 1.0` | HSV saturation multiplier on orange hues (H=5..30). 1.0 = off, 0.0 = fully desaturated orange. |
| `USB_CAM_HIGHLIGHT_KNEE` | `0.75` | `0.0 – 1.0` | Fraction of full scale above which soft-clipping engages. 0.75 = starts at 191/255. |
| `USB_CAM_HIGHLIGHT_STRENGTH` | `0.6` | `0.0 – 1.0` | How hard to pull blown highlights back toward the knee. 0.0 = off. |
| `USB_CAM_SHARPEN_AMOUNT` | `0.8` | `0.0 – ∞` | Unsharp mask amount. 0.0 = off. Above ~1.5 gets crunchy. |
| `USB_CAM_SHARPEN_RADIUS` | `3` | odd int ≥ 1 | Gaussian blur kernel size for the unsharp mask. |

**Processing order** (inside `_process()` in `/photo.jpg` handler):

1. `_apply_highlight_rolloff` — tame blown highlights first
2. `_apply_gray_world_wb` — cool the global cast
3. `_apply_orange_desat` — pull chick pixels toward neutral
4. `_apply_unsharp_mask` — recover perceived sharpness last

The order matters. Sharpening before WB amplifies color fringes; WB after orange-desat undoes the desat; roll-off last is too late because WB already amplified the clipped regions.

## What doesn't work (pre-buried — do not retry)

Tested 18-Apr-2026 on both the Mac Mini (old host) and MacBook Air (current host):

- **`cv2.VideoCapture.set(CAP_PROP_AUTO_EXPOSURE | CAP_PROP_EXPOSURE)`**: `set_ok=False`. Also false for AUTOFOCUS, FOCUS, AUTO_WB, WB_TEMPERATURE, BRIGHTNESS, CONTRAST, SATURATION, GAIN. All ten properties return False on this generic UVC webcam via AVFoundation. Code plumbing still exists (`USB_CAM_AUTO_EXPOSURE`, `USB_CAM_EXPOSURE`, `USB_CAM_AUTOFOCUS`, `USB_CAM_FOCUS` env vars) because a different camera on a different host might honor them.
- **Swift `AVCaptureDevice.setExposureModeCustom(duration:iso:)` and `setFocusModeLocked(lensPosition:)`**: `@available(macOS, unavailable)`. iOS-only APIs. macOS's AVCaptureDevice can only set `exposureMode = .locked` / `.autoExpose` / `.continuousAutoExposure` — you can pick the mode but cannot drive the value.
- **`jtfrey/uvc-util`** (side-channel UVC control via IOKit): builds cleanly with `clang -fmodules` but segfaults on every invocation on modern macOS. Project is unmaintained; no maintained fork exists for Sequoia+.
- **ffmpeg with AVFoundation input**: does not expose UVC property setters on macOS.

Conclusion: on modern macOS, **there is no userland path to drive this UVC webcam's exposure / focus / WB values**. The camera's firmware runs auto-exposure internally and we see the output. All tuning is post-capture in Python.

## Measured behavior from the investigation doc (16-Apr-2026)

From `docs/16-Apr-2026-heat-lamp-orange-cast-investigation.md` appendix A — four variants shot of the same brooder scene, before the highlight-rolloff + sharpen knobs existed:

| `wb` | `os` | What it looks like |
|---|---|---|
| 0 | 0 | Chicks pure white, background pure red, no detail. **Worst.** |
| 0 | 0.75 | Still red, chicks recognizable as chicks. **Least bad of the variants.** |
| 0.3 | 0.5 | Magenta/yellow artifacts over clipped highlights. |
| 0.5 | 0.75 | **Nuclear pink/yellow.** Matches what Boss was seeing on the Mini. |

Key insight: **gray-world WB at any strength > 0 amplifies already-clipped channels past 255 and produces color disasters.** The only measured "least bad" variant is `wb=0 os=0.75` — WB off, orange desat on.

## Recommended starting config for the brooder

Set these in `~/Library/LaunchAgents/com.farmguardian.usb-cam-host.plist` on the MBA, full reload after editing:

```xml
<key>USB_CAM_AUTO_WB</key>       <string>false</string>
<key>USB_CAM_WB_STRENGTH</key>   <string>0.0</string>
<key>USB_CAM_ORANGE_DESAT</key>  <string>0.75</string>
<key>USB_CAM_HIGHLIGHT_KNEE</key>     <string>0.75</string>
<key>USB_CAM_HIGHLIGHT_STRENGTH</key> <string>0.6</string>
<key>USB_CAM_SHARPEN_AMOUNT</key> <string>0.8</string>
<key>USB_CAM_SHARPEN_RADIUS</key> <string>3</string>
```

This pairs the "least bad" WB finding with the new highlight-rolloff + sharpen passes. If it still looks bad:

- **Too dominant red:** bump `USB_CAM_ORANGE_DESAT` down to `0.5` or `0.3` (more aggressive desat).
- **Still blown on chick bodies:** raise `USB_CAM_HIGHLIGHT_STRENGTH` to `0.8` or `1.0`; lower `USB_CAM_HIGHLIGHT_KNEE` to `0.6` (catches more pixels).
- **Crunchy edges / over-sharpened artifacts:** drop `USB_CAM_SHARPEN_AMOUNT` to `0.4`; or set to `0.0` to disable.
- **Too soft even at defaults:** raise `USB_CAM_SHARPEN_AMOUNT` toward `1.2`, keep radius at 3.
- **"I want the raw camera output to compare against":** set `AUTO_WB=false`, `WB_STRENGTH=0`, `ORANGE_DESAT=1.0`, `HIGHLIGHT_STRENGTH=0`, `SHARPEN_AMOUNT=0`. That disables every post-pass. `/photo.jpg` then returns what the sensor actually captured.

## Live tuning without a restart

`/photo.jpg` accepts `?wb=X` and `?os=Y` query overrides that bypass the env-var defaults. Useful for A/B:

```bash
curl -o raw.jpg        'http://marks-macbook-air.local:8089/photo.jpg?wb=0&os=1.0'
curl -o desat-only.jpg 'http://marks-macbook-air.local:8089/photo.jpg?wb=0&os=0.75'
curl -o full.jpg       'http://marks-macbook-air.local:8089/photo.jpg?wb=0.5&os=0.75'
```

The highlight-rolloff + sharpen env vars do NOT have query overrides right now — they only apply at service-level from the plist. If we need live tuning for those, add query params for them in `_process()` the same way `wb` and `os_` work.

## /health exposes the live config

```bash
curl -s http://marks-macbook-air.local:8089/health | python3 -m json.tool
```

The response includes every knob's current effective value. Use this to verify a plist change took (after `launchctl bootout` + `bootstrap`).

## File layout

- Script (git-tracked): `tools/usb-cam-host/usb_cam_host.py`
- Install dir on MBA: `~/.local/farm-services/usb-cam-host/` and `~/.local/farm-services/usb-cam-host-facetime/`. The script is a symlink/copy from the git-tracked version — update via `scp` or `git pull` + `cp`, then `launchctl kickstart -k`.
- LaunchAgent plists on MBA: `~/Library/LaunchAgents/com.farmguardian.usb-cam-host.plist` (USB cam, :8089, device_index=0) and `com.farmguardian.usb-cam-host-facetime.plist` (FaceTime HD, :8090, device_index=1).
- Logs on MBA: `~/.local/farm-services/usb-cam-host/service.log` and `~/.local/farm-services/usb-cam-host-facetime/service.log`.
- Related doc: `docs/16-Apr-2026-heat-lamp-orange-cast-investigation.md` (the 4–5-time loop-breaker).

## Cross-references

- `AGENTS_CAMERA.md` — camera operations reference
- `HARDWARE_INVENTORY.md` — which physical device is which logical camera
- `CLAUDE.md` — dual config file warning (Guardian root + pipeline), camera naming rule

---

**done.**
