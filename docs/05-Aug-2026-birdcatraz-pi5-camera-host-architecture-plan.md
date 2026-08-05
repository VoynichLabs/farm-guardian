# 05-Aug-2026 — Birdcatraz Pi 5 camera host: architecture plan

**Author:** Claude Opus 5
**Date:** 05-Aug-2026
**Hardware:** Raspberry Pi 5, 4 GB RAM, wired Ethernet, at **Birdcatraz**
**Status:** DRAFT — awaiting Boss approval. Nothing implemented.

---

## Why this is a rethink and not a port

Boss's call, and it is the right one. Porting `usb_cam_host.py` to the Pi as-is would carry
across a pile of workarounds that exist only because of constraints the Pi does not have. The
useful question is not "how do we run this on Linux" but "which of these mechanisms should
exist at all once the hardware stops fighting us."

Here is the accumulated stack, honestly stated:

| Mechanism | Why it exists | Still needed on the Pi? |
|---|---|---|
| `USB_CAM_DEVICE_INDEX` | original naive binding | **No** — identifying a camera by position is the root error |
| `USB_CAM_DEVICE_NAME_CONTAINS` | index proved unstable | **No** — matches a substring of a *manufacturer* string; the 1080p webcam's name is literally "USB CAMERA" |
| `PREFER_EXTERNAL` | guess which camera was meant on a laptop | **No** — the Pi has no built-in camera to disambiguate from |
| Unique-resolution probe | name matching proved unstable | **No** |
| **Picture-comparison test** | resolution probe proved unstable | **No** — and it is the one that failed on 04-Aug |
| `farmcam-wifi-watchdog` | Realtek USB WiFi wedges at ~34% signal | **No** — Ethernet |
| `farmcam-watchdog` | ffmpeg dshow zombie | **No** — no ffmpeg capture path, no dshow |
| MediaMTX + ffmpeg RTSP | the old `gwtc` lane | **No** — dead since June, `enabled: false`, delete it |
| `usb-cam-watchdog` scheduled task | Windows-specific supervision | **No** — systemd does this natively |
| `os._exit(1)` stall self-heal (v2.61.0) | in-process wedge with no ceiling | **Keep** — genuinely orthogonal, and systemd restarts it |

Five successive identity mechanisms, each added because the previous one was unreliable, none
removed. That is the definition of hacks on hacks, and it is worth stopping.

## The single root error

**Every one of those five identity mechanisms is a workaround for not using the thing that is
already unique: the USB serial number.**

The hardware has been telling us the answer the whole time. From the Air's own USB tree:

```
USB CAMERA:     Product ID 0x9221  Vendor ID 0x32e6  Serial Number: 240725172848
USB PHY 2.0:    Product ID 0x2825  Vendor ID 0x1224  (Jieli Technology)
```

That serial is what settled the 04-Aug "which webcam is this" question in one command, with
certainty, when a whole afternoon of picture-matching had produced a mislabel. macOS makes this
awkward to reach from OpenCV, which is why the codebase drifted into heuristics. **Linux does
not.** udev exposes it directly as a stable path:

```
/dev/v4l/by-id/usb-USB_CAMERA_USB_CAMERA_240725172848-video-index0
```

That path is the same across reboots, across replug, across plug order, and across adding a
second camera. Open it and you have — by construction, not by inference — the camera you meant.

**Architectural decision: on the Pi, a camera is identified by its `by-id` device path. Nothing
else.** No index, no name substring, no picture test. If the path is absent the camera is
absent, and the service serves 503 and says so. There is no guessing branch to get wrong,
because there is no guessing.

This also kills the 04-Aug collision class outright. Two services cannot land on one camera when
each opens a distinct, serial-derived path.

---

## Scope

**In scope**

- A Linux camera-host service on the Pi, identity-by-`by-id`-path, one systemd unit per camera.
- udev rules giving each camera a stable, human-readable symlink.
- Migrating the cameras currently at Birdcatraz onto the Pi; retiring the GWTC lane.
- Deleting the dead `gwtc` config entry and the Windows-only deploy artifacts it needs.
- Docs: `HARDWARE_INVENTORY.md`, `CLAUDE.md` roster + network table, `SOCIAL_MEDIA_MAP.md` if a
  reel lane changes host.

**Out of scope — explicitly staying on the Mac Mini**

- YOLO detection, the VLM, LM Studio, the pipeline, all publishing lanes, the database.
  A 4 GB Pi is a *capture appliance*. It captures and serves JPEG over HTTP; nothing else.
  Do not be tempted to move inference there because the Pi 5 "is fast enough" — it is not, the
  VLM alone needs the Mini's memory, and splitting the pipeline across two boxes would recreate
  the coordination problems this plan is trying to remove.
- The macOS camera host on the MacBook Air. It keeps its existing code path. **This plan does
  not fix the 04-Aug identity bug on macOS** — that is still open with its own three options in
  `docs/04-Aug-2026-camera-identity-collision-incident-and-fix-plan.md`. See "Relationship to
  the open macOS bug" below.

---

## Target architecture

```
Birdcatraz (Pi 5, 4 GB, Ethernet, static lease)
  │
  ├── udev rule  ──►  /dev/farmcam/<camera-id>   (stable symlink, keyed on USB serial)
  │
  ├── systemd: farmcam@<camera-id>.service   (one instance per camera, templated)
  │      └── camera_host.py  ── opens ONLY /dev/farmcam/<camera-id>
  │                           ── serves GET /photo.jpg, GET /health
  │                           ── port from the instance's config
  │
  └── (no MediaMTX, no ffmpeg capture, no watchdog scripts, no WiFi anything)

Mac Mini  ──HTTP──►  http://<pi>:<port>/photo.jpg     (unchanged consumer contract)
```

**Four properties worth stating, because each replaces a moving part:**

1. **Identity is structural, not inferred.** The service is handed one device path and opens it.
   It cannot serve the wrong camera, so it needs no code to check whether it did.
2. **Supervision is systemd**, not a bespoke watchdog. `Restart=always`, `RestartSec`,
   `WatchdogSec` if we want liveness. Three Windows watchdog scripts and a launchd `KeepAlive`
   convention collapse into unit-file directives.
3. **The wire contract does not change.** `GET /photo.jpg` and `GET /health` stay exactly as they
   are, so `capture.py`, `HttpUrlSnapshotSource`, `capture_ip_webcam` and both config files need
   nothing but a new URL. This is what makes the migration safe and reversible.
4. **Ethernet removes an entire failure mode.** No adapter wedge, no 34% signal, no 3-minute
   wait rule, no "is it hung or is it mid-bounce" ambiguity. Give it a static DHCP lease so the
   IP stops moving as well.

### Implementation choice to decide: fork or share?

`tools/usb-cam-host/usb_cam_host.py` is ~1,100 lines, most of it identity heuristics and
platform branches that the Pi does not want. Two options:

- **(A) A new, small `tools/camera-host-linux/camera_host.py`.** Maybe 250 lines: open a path,
  grab, encode, serve, exit if stalled. Keeps the Pi honest and readable. Cost: two codebases
  sharing an HTTP contract, and the image-processing options (WB, sharpen, exposure) would need
  either duplicating or extracting.
- **(B) Extend the existing file with a Linux identity path.** One codebase, no duplication.
  Cost: adds a fourth platform branch to a file whose branching is already the problem, and the
  Pi inherits code it will never run.

**Recommendation: (A), with the shared parts extracted.** The frame-processing helpers (WB,
highlight knee, sharpen, JPEG encode) and the `/health` shape are genuinely common and should
move to a small shared module both hosts import — that is real DRY. The *identity* code is
genuinely per-platform and should not pretend to be shared. This gives one honest split instead
of one file with four personalities.

---

## Relationship to the open macOS bug

These are separate and both should happen.

The Pi work makes the macOS bug *less load-bearing* — fewer cameras on the Air, fewer chances to
collide — but it does not fix it, and the Air still runs two cameras through the picture test.
The three options in the 04-Aug plan still need a decision.

**Worth noting for that decision:** if `by-id` is the right answer on Linux, it is worth one
afternoon checking whether macOS can be made to do the same thing. `AVCaptureDevice.uniqueID`
carries a stable per-device identifier (the Air reported `0x1424000012242825` for the dashcam
and `DJH4131MBP2F9TCC7` for the built-in), and those are exactly the kind of key the picture
test is a poor substitute for. If a small PyObjC helper can map `uniqueID` → the index OpenCV
wants, then **all three options in that plan become moot** and macOS gets the same structural
identity the Pi has. That is a better outcome than any of them and should be investigated first.

---

## Open questions for Boss

1. **Which cameras go on the Pi?** The 1080p webcam is intermittent and its behaviour is not yet
   attributed to camera vs cable vs host. Deciding what lives at Birdcatraz is a hardware call.
2. **Does the S7 change?** It is at Birdcatraz on a Qi pad with a dead micro-USB port and no ADB
   path. A wired Pi nearby does not fix that by itself, but it is worth asking whether the phone
   should stay in the plan at all long-term.
3. **Does anything else at Birdcatraz need the Pi** (sensors, a second camera angle, audio), or
   is it strictly a camera host? That changes whether 4 GB is comfortable or tight.
4. **Static IP or DNS name?** A static lease plus an `/etc/hosts` entry on the Mini would end the
   `.68 → .69` IP-drift pattern that has bitten repeatedly.

---

## TODOs (ordered; nothing starts before approval)

1. **Decide the questions above**, especially which cameras move.
2. Bring the Pi up: OS, Ethernet, static lease, SSH key from the Mini, `c` alias if it should
   join the multi-machine Claude pattern.
3. **Verify the power budget before trusting it.** The dashcam alone requests 500 mA. Pi 5 USB
   current is limited unless the supply advertises enough, and the exact figures are worth
   checking against current Raspberry Pi documentation rather than memory — **a web search here
   is warranted, this is recent hardware.** Plan on a powered hub regardless; it is cheap
   insurance and the farm already knows what starvation looks like.
4. Confirm each camera's `by-id` path on the Pi and write the udev rules. **Verification: unplug
   everything, replug in a deliberately different order, reboot, and confirm every symlink still
   resolves to the same physical camera.** That test is the whole point of the design — if it
   fails, stop, because nothing downstream is trustworthy.
5. Extract the shared frame-processing + `/health` module from `usb_cam_host.py`.
6. Write `camera_host.py` (Linux, path-only identity) and the `farmcam@.service` template.
7. Stand the cameras up **one at a time**, and verify with the concurrent byte-comparison check —
   the same test that caught the 04-Aug collision. Framing similarity is not evidence.
8. Repoint **both** `config.json` and `tools/pipeline/config.json`; reload **both** agents.
9. **Delete, don't leave lying around:** the `gwtc` camera entry in both configs, `deploy/gwtc/`
   Windows watchdog artifacts, `screen-on.ps1` / `register-screen-task.ps1` (already dead), and
   the GWTC troubleshooting sections in `CLAUDE.md` once the box is genuinely retired. A retired
   machine's runbook left in place is a trap for the next agent.
10. Docs + CHANGELOG.

---

## Risks

- **Deleting GWTC content too early.** Keep it until the Pi has run a full week unattended. The
  30-Jul Reolink case is the cautionary tale: something that looks dead is sometimes just
  unpowered, and the runbook is what tells you that.
- **A camera whose firmware reports no serial.** Cheap UVC devices sometimes do not. Then the
  `by-id` path falls back to a bus-path form (`usb-0:1.2:1.0`), which is stable per *port* but
  not per *device* — good enough if the camera never moves ports, and it must be documented as
  such rather than silently relied on.
- **Assuming the Pi fixes the intermittent webcam.** It may well not. That fault has followed
  the camera across two machines and two operating systems; a third host is not a diagnosis.
- **Scope creep onto the Pi.** It is a capture appliance. The moment it starts running inference
  or publishing, this plan has failed.
