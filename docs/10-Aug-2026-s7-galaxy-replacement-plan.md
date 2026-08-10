# 10-Aug-2026 — Swap the dead-USB-port S7 for the new Galaxy S7

## Why

`s7-cam`'s micro-USB port died from water damage (confirmed 01-Aug-2026, see
`docs/01-Aug-2026-s7-usb-port-dead.md`). Since then it's been Qi-pad-charged only, and Boss
has confirmed running it while it charges drains it net-negative — so keeping it fed means
carrying it inside to power off, which takes `s7-cam` fully offline for a stretch (CLAUDE.md,
Camera 2 section). It's still the best sensor in the fleet (Sony IMX260, f/1.7 — see the VLM
`context` string in `tools/pipeline/config.json`) but the power situation is a standing
liability.

A replacement handset was flagged as inbound on 05-Aug-2026
(`docs/05-Aug-2026-birdcatraz-pi5-camera-host-architecture-plan.md`, "Open questions for
Boss" #2) and has now arrived (10-Aug-2026). It also happens to be a Galaxy S7.

**Boss's decision, this session:** straight swap. The new phone takes over the `s7-cam`
identity exactly as the 05-Aug doc already called for. The old phone is retired outright —
no manual-spare role, no dual-camera setup. Boss explicitly rejected keeping the old one on
hand as a manual fallback during outages ("that's also insane").

## Scope

**In:**
- Physically standing up the new phone as `s7-cam`: IP Webcam app, WiFi, the same on-device
  settings the old phone needed (orientation, focus mode, lock-screen).
- Getting Guardian and the pipeline pointed at it with the least possible config churn.
- Fully retiring the old handset — powered off, out of the charging/camera rotation, no
  ongoing role of any kind.
- Updating the docs that describe `s7-cam`'s current physical state once the swap is live and
  verified.

**Out:**
- Any new camera id, second S7 lane, or failover logic. There is exactly one `s7-cam` before
  and after this.
- Re-litigating the reel/gem/hunt pipeline built around `camera_id='s7-cam'` (adaptive
  sampling, daily/weekly reels, Discord tagging) — none of it changes; it's id-scoped, not
  device-scoped, and just keeps running once frames flow again.
- Restoring an ADB path. Whether the new phone's USB port works is unknown and irrelevant to
  this plan — everything here is built on the same WiFi/HTTP path the old phone used, per the
  repo's standing rule of never depending on ADB for this camera.
- Editing CLAUDE.md / `HARDWARE_INVENTORY.md` speculatively before the swap is real. Those
  files describe the *old* phone correctly today; they get updated once the new one is live
  and confirmed working (see Docs touchpoints below), not preemptively.

## Architecture

No code changes are expected. `s7-cam` is consumed through `HttpUrlSnapshotSource`
(Guardian) and `capture_ip_webcam` (pipeline) — both are already camera-agnostic HTTP-poll
adapters keyed on `http_base_url` / `ip_webcam_base`. Swapping the physical device behind
that URL is exactly what this abstraction is for; nothing downstream (detection-disabled
flag, EXIF portrait bake in `capture.py:_apply_exif_rotation`, the hunt/adaptive-sampling
config, the reel/gem/Discord pipeline) needs to know the phone changed.

The only real design choice is **how much config churn the swap causes**, and there's a
cheap way to make it zero:

- **Preferred: reserve the same IP, `192.168.0.249`, for the new phone's MAC** on the
  TP-Link Archer AX55, the same static-lease pattern already used for the Birdcatraz Pi (see
  the 05-Aug plan, "IP or DNS name?"). If the new phone answers on the same address, **none**
  of `config.json`, `tools/pipeline/config.json`, or
  `deploy/s7-settings-watchdog/watchdog.sh` (which hardcodes `192.168.0.249:8080`, see its
  header comment) need to change at all.
- **Fallback: if Boss doesn't want to touch the router**, the new phone keeps whatever IP
  DHCP hands it, and three files get one string updated each: `http_base_url` in
  `config.json`, `ip_webcam_base` in `tools/pipeline/config.json`, and the hardcoded IP in
  `deploy/s7-settings-watchdog/watchdog.sh`. Per the standing "TWO SEPARATE CONFIG FILES"
  rule in CLAUDE.md, both config files must move together, then both services reloaded.

Router changes need Boss's hand per the standing "never change router settings without
approval" rule — that step isn't something I can do remotely.

## TODOs

Ordered; steps marked **(hands-on)** need Boss physically at the phone or router, everything
else I can drive or verify remotely once the phone is on the network.

1. **(hands-on)** Charge the new phone, install IP Webcam (`com.pas.webcam`, same app the old
   phone ran) from the Play Store, join it to the farm WiFi.
2. **(hands-on)** Note the phone's MAC address (Settings → About phone → Status, or the
   router's DHCP client list once it joins) and decide: reserve `192.168.0.249` for it, or
   accept whatever address it gets. Reserving the old address is the one-line-change option
   and matches the Pi precedent — recommended unless Boss has a reason not to.
3. **(hands-on)** Apply the same on-device settings the old phone needed, since none of this
   carries over from a factory-fresh device:
   - Disable the swipe lock screen (the 2026-05-21 fix for the cold-boot black-screen bug —
     `capture.py` and Guardian have no way to unlock a screen remotely).
   - IP Webcam app settings: orientation → `portrait`, photo rotation → `90`, focus mode →
     continuous-picture / Aggressive. These persist in-app (unlike the old phone's pre-2026-05-21
     behavior), so this should only need doing once.
   - Leave Google Play Services and IMS enabled — do not run any app-disabling cleanup pass
     (the `docs/skills-s7-adb-operations.md` lesson about `com.google.android.gms` and
     `com.sec.imsservice` applies to this phone too, even though there's no ADB path to hit
     it with here — it's a phone-settings menu risk, not just an ADB one).
4. **(hands-on)** Place the phone at Birdcatraz in the same nesting-box position the old one
   used, powered from the Qi pad (or a fresh wall brick if the new phone's port works and
   Boss prefers it — worth a quick check, out of scope to decide here).
5. If the IP changed (step 2's fallback path): update `http_base_url` in `config.json`,
   `ip_webcam_base` in `tools/pipeline/config.json`, and the IP in
   `deploy/s7-settings-watchdog/watchdog.sh`; commit.
6. Reload both services: `launchctl kickstart -k gui/$(id -u)/com.farmguardian.guardian` and
   `launchctl kickstart -k gui/$(id -u)/com.farmguardian.pipeline`.
7. **(hands-on)** Power off the old phone and remove it from the Qi pad. It carries no
   further role — not a spare, not a backup power-on. If Boss wants it physically kept
   somewhere, that's a hardware-storage decision outside this repo's scope.

## Verification

1. `curl http://192.168.0.249:8080/photo.jpg` (or the new IP) returns a real JPEG — confirm
   with `file`/dimensions, not just a 200 status.
2. Confirm portrait output end-to-end: pull a frame through Guardian's own path (dashboard
   live feed or `/api/v1/cameras/s7-cam/snapshot`) and check it's 1080×1920, not landscape.
3. Confirm `com.farmguardian.s7-settings-watchdog` is hitting the right host and its 10-minute
   re-curls succeed (log shows `fm=1 or=1 pr=1`, not `fm=0 or=0 pr=0`).
4. Watch `image_archive` for a few pipeline cycles: new `s7-cam` rows arriving, VLM
   enrichment completing (`vlm_inference_ms` populated), hunt/adaptive-sampling behaving
   (cadence tightening when YOLO presence fires, per `docs/07-Aug-2026-s7-adaptive-sampling-and-selection-plan.md`).
5. Sample recent `share_worth` / `image_quality` / sharpness values against the pre-swap
   baseline — the new phone's sensor could differ from the IMX260 and shift the gem-tier mix;
   flag to Boss if `strong` suddenly jumps or drops sharply rather than assuming it's noise.
6. Let one full daily reel cycle (21:00 local) and the Discord gem lane run against the new
   phone before calling the swap done — that exercises the full pipeline, not just capture.

## Docs / Changelog touchpoints

To be done **after** the swap is physically live and step 1–4 of Verification pass — not
before, since editing these now would describe a device that isn't in service yet:

- `CLAUDE.md` — Camera 2 (`s7-cam`) section: replace the dead-USB-port/Qi-brownout narrative
  with the new phone's actual state (model if different, power source, any settings
  differences found in practice). Keep the historical detail about the old phone's failure —
  move it to be clearly past-tense/historical rather than deleting it, matching how this repo
  treats prior-incident material elsewhither.
- `HARDWARE_INVENTORY.md` — the `s7-cam` row: device model/serial, sensor if it differs from
  the Sony IMX260 note.
- `tools/pipeline/config.json` — the VLM `context` string for `s7-cam` currently says "Sony
  IMX260 sensor, 1920x1080, f/1.7"; correct it if the new phone's sensor differs. Treat this
  as a deliberate, isolated edit (per the standing caution in CLAUDE.md about not casually
  touching this scoring-calibrated prompt) — not bundled with unrelated prompt changes.
- `docs/05-Aug-2026-birdcatraz-pi5-camera-host-architecture-plan.md` — its open question #2
  already got a one-line addendum today pointing here; no further edit needed unless this
  plan's outcome changes that answer.
- `CHANGELOG.md` — add a top entry once the swap is live and verified (SemVer, what/why/how,
  model name), per the standing "behavior change requires a CHANGELOG entry" rule. Not added
  now because nothing has actually changed yet — this file is the plan, not the change.

## Open questions / risks

- **New phone's exact condition is unknown until step 1.** Model variant, Android version,
  battery health, and whether its USB port actually works are all unverified — this plan
  assumes "healthy enough to run IP Webcam over WiFi" and nothing more.
- **Sensor differences could shift VLM gem scoring** (see Verification #5) — not a blocker,
  but worth a deliberate look rather than assuming continuity with the IMX260-tuned baseline.
- **DHCP reservation needs router-admin access**, which this repo treats as Boss-only. If
  that step stalls, the fallback (new IP + three-file update) is a five-minute job and not a
  real blocker.
