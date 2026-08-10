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

## Boss's decision, 05-Aug-2026 — and why it closes the macOS bug too

**Decision: both USB cameras (`jieli-dashcam`, `usb-webcam-1080p`) move to the Pi. The MacBook
Air is left serving exactly one camera — its own built-in FaceTime HD — and nothing else.**

The Pi is the camera hub at Birdcatraz: secure, weatherproof, wired Ethernet. The Air stops
being a multi-camera host and becomes a dumb single-camera endpoint on `:8089`.

**⚠️ An earlier draft of this section said the Pi "does not fix" the macOS identity bug and
recommended an `AVCaptureDevice.uniqueID` workstream. That was wrong, and it was wrong because
it mislocated the hardware.** The MacBook Air *is at Birdcatraz* — it is the host for the run
and garden cameras. Treating "the Air" and "Birdcatraz" as two different sites made the Air look
like a separate ongoing problem the Pi would leave behind. It is not; it is the thing the Pi
replaces.

**With one permanently-attached camera on the Air, the collision class is gone — not patched,
structurally absent:**

- A collision needs two or more cameras on one host competing for OpenCV indices. One camera
  cannot collide with itself.
- Index renumbering was caused by USB cameras appearing and disappearing. With none attached,
  the built-in is index 0 forever.
- The `next best n/a` hole (see below) only produces a *wrong* answer when two or more physical
  cameras exist and just one happens to be openable. With genuinely one camera, accepting the
  sole candidate is the correct answer, not a guess.
- The destabiliser leaves the building. The 05-Aug collision was triggered by
  `usb-webcam-1080p` dropping its video interface while staying on the USB bus, which renumbered
  every camera underneath two running services. That camera moves to the Pi.

**Therefore the `AVCaptureDevice.uniqueID` / PyObjC work is NOT needed, and neither are the
three options in `docs/04-Aug-2026-camera-identity-collision-incident-and-fix-plan.md`.** Do not
start that workstream. Removing the precondition beats fixing the heuristic — the heuristic is
what this whole plan exists to stop writing.

**What the Pi does not fix, stated plainly:** `usb-webcam-1080p` is separately broken (its video
function drops and does not self-recover; the fault has followed it across two machines and two
operating systems). The Pi does not cure it. But it does make it fail *cleanly* — with `by-id`
paths, a camera whose video interface is gone is simply an absent path, so its service 503s and
it can no longer take a sibling camera down with it. That containment is worth having on its own.

### The 05-Aug collision, for the record

Live example of the un-fixed macOS hole, from the Air's `jieli-dashcam.log` (EDT):

```
12:14  'USB PHY 2.0'    -> cv2 index 0 (difference 0.9,  next best 37.2)   genuine
13:50  5 consecutive read failures — releasing camera and reopening
13:51  'USB PHY 2.0 #2' -> cv2 index 1 (difference 37.8, next best n/a)    garbage, ACCEPTED
```

A true match on this rig scores **0.9**. It accepted **37.8** because only one index was
openable, so the relative-margin gate had no second candidate and was skipped. Result: the
`jieli-dashcam` endpoint served the FaceTime camera from 13:51 EDT onward, and
`image_archive` rows with `camera_id='jieli-dashcam'` and `ts >= 2026-08-05T17:51:32Z` are
FaceTime frames.

**Triage correction — the documented byte-hash check gives FALSE NEGATIVES.** `CLAUDE.md` says
to fetch `/photo.jpg` from every endpoint concurrently and hash them, and that any two matching
means two services on one camera. Matching hashes do prove a collision, but **non-matching
hashes prove nothing**: each service runs its own independent grabber loop, so two services on
one lens still capture at different instants and never byte-match. This collision was caught by
*looking at the two pictures*, after the hash check had cleared it. Ground truth is
`ffmpeg -f avfoundation -i "<device name>"` on the host, which selects by name rather than index.

---

## Open questions for Boss

1. ~~**Which cameras go on the Pi?**~~ **ANSWERED BY BOSS 05-Aug-2026 — see "Boss's decision"
   below. Both USB cameras move to the Pi; the MacBook Air keeps only its own built-in FaceTime
   HD. This answer also closes the macOS identity bug, so read that section before acting on
   anything else in this plan.**
2. ~~**Does the S7 change?**~~ **ANSWERED 05-Aug-2026: the S7 STAYS long-term, and a replacement
   handset is already on the way.** Do not plan its retirement, do not fold `s7-cam` into the Pi,
   and do not treat the dead micro-USB port as a reason to decommission it. It keeps its own
   lane exactly as documented in `CLAUDE.md` (Qi pad, HTTP snapshot on `192.168.0.249:8080`,
   no ADB path of any kind). When the replacement arrives it inherits the `s7-cam` id.
   **Update 10-Aug-2026:** the replacement handset has arrived — see
   `docs/10-Aug-2026-s7-galaxy-replacement-plan.md` for the swap plan. It's a straight swap,
   not a second camera; the old phone is retired outright with no manual-spare role.
3. ~~**Does anything else at Birdcatraz need the Pi?**~~ **ANSWERED 05-Aug-2026: strictly a
   camera host. 4 GB is comfortable.** This hardens the "capture appliance only" rule in Scope
   from a recommendation into a decision — see Risks, "Scope creep onto the Pi."
4. ~~**Static IP or DNS name?**~~ **ANSWERED 05-Aug-2026: static lease.** Reserve it on the
   TP-Link Archer AX55 against the Pi's MAC and add an `/etc/hosts` entry on the Mini so configs
   can name the host rather than an address. This ends the `.68 → .69` drift class for this box.

---

## TODOs (ordered; nothing starts before approval)

1. ~~Decide which cameras move~~ — **DONE 05-Aug-2026: both USB cameras go to the Pi, the Air
   keeps only its built-in FaceTime HD.** Questions 2–4 (S7, scope, static IP) are still open.
2. Bring the Pi up: OS, Ethernet, static lease, SSH key from the Mini, `c` alias if it should
   join the multi-machine Claude pattern.
3. ~~Verify the power budget~~ — **VERIFIED 05-Aug-2026 against Raspberry Pi's own docs. The
   answer is worse than expected: a POWERED HUB IS MANDATORY, not insurance.**

   **A Pi 5 allows 600 mA TOTAL across all USB ports by default.** It only raises that to
   1600 mA if it successfully negotiates **5 V / 5 A** with a USB-PD supply (i.e. the official
   27 W unit). Any lesser brick — including a perfectly good 5 V/3 A phone charger — leaves you
   at 600 mA for every USB device combined.

   Now the farm's actual numbers, straight off the Air's USB tree:

   | Device | Current required |
   |---|---|
   | `jieli-dashcam` (Jieli USB PHY 2.0) | **500 mA** |
   | `usb-webcam-1080p` (USB CAMERA, 0x32e6) | **100 mA** |
   | **Total** | **600 mA — exactly the default ceiling, zero headroom** |

   **This is the MacBook Air's bus-powered-hub failure repeating on new hardware.** That hub
   supplied 500 mA against the same 600 mA of demand, and whichever camera came up second lost.
   Three cameras were lost to it in four days (dashcam 01-Aug, USB webcam 02-Aug, the built-in
   FaceTime 04-Aug). A default-configured Pi 5 gives 600 mA against 600 mA of demand — the same
   trap with 100 mA more rope.

   **Do not "solve" this with `usb_max_current_enable=1` in `/boot/firmware/config.txt`.** That
   forces the 1.6 A limit without the supply having proven it can deliver, and Raspberry Pi's
   own guidance is that it can brown out the SoC under load — trading a camera dropout for
   whole-box instability, at Birdcatraz, on a machine with no screen. The setting is applied
   automatically and safely when a genuine 5 V/5 A PD supply is negotiated; that is the only
   way it should ever be on.

   **Required, in order:** (a) the official 27 W USB-C PD supply for the Pi itself, (b) an
   **externally powered** USB hub for both cameras, so camera draw never touches the Pi's
   budget at all. Verify after assembly with `lsusb -v | grep -i MaxPower` and confirm both
   cameras deliver frames *simultaneously* — the Air's failure only ever showed up with both
   attached at once.

   Sources: [USB Power Delivery on Raspberry Pi 5 (white paper)](https://pip-assets.raspberrypi.com/categories/685-app-notes-guides-whitepapers/documents/RP-009856-WP-1-USB%20Power%20delivery%20on%20Raspberry%20Pi%205.pdf)
4. Confirm each camera's `by-id` path on the Pi and write the udev rules. **Verification: unplug
   everything, replug in a deliberately different order, reboot, and confirm every symlink still
   resolves to the same physical camera.** That test is the whole point of the design — if it
   fails, stop, because nothing downstream is trustworthy.
5. Extract the shared frame-processing + `/health` module from `usb_cam_host.py`.
6. Write `camera_host.py` (Linux, path-only identity) and the `farmcam@.service` template.
7. Stand the cameras up **one at a time**, and verify with the concurrent byte-comparison check —
   the same test that caught the 04-Aug collision. Framing similarity is not evidence.
8. Repoint **both** `config.json` and `tools/pipeline/config.json`; reload **both** agents.
   Note `usb-webcam-1080p` is currently pointed at GWTC `192.168.0.69:8089`, which serves a
   **black frame** while the physical camera sits on the Air — fix that in the same pass.
8b. **Reduce the MacBook Air to one camera.** Boot out `com.farmguardian.cam-jieli-dashcam` and
   leave only `com.farmguardian.cam-macbook-air-facetime` on `:8089`. This is the step that
   closes the identity bug, so do not skip it or leave a second agent parked-but-loadable.
   **Then check one residual:** the Air's AVFoundation list also contains `Capture screen 0`.
   The built-in FaceTime HD is documented as able to vanish while the lid is open — if it does,
   confirm the single-candidate acceptance path cannot latch onto the screen-capture device and
   serve a desktop screenshot as a camera. If it can, that one branch still needs a guard.
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
