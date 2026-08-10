# Hardware Inventory — Farm Guardian Cameras

> ### ✅ 05-Aug-2026 — BOTH USB CAMERAS MOVED TO THE RASPBERRY PI 5 (`farm-pi5`) AT BIRDCATRAZ
>
> **This supersedes every host claim in the 04-Aug and 01-Aug notes below for these two
> cameras.** `jieli-dashcam` and `usb-webcam-1080p` no longer live on the MacBook Air or on
> GWTC. They are on a Raspberry Pi 5 (4 GB, wired Ethernet) in a weatherproof enclosure at
> Birdcatraz, served by `tools/camera-host-linux/camera_host.py` under systemd.
>
> | camera | URL | resolution |
> |---|---|---|
> | `jieli-dashcam` | `http://farm-pi5.local:8091/photo.jpg` | 1280x720 |
> | `usb-webcam-1080p` | `http://farm-pi5.local:8090/photo.jpg` | 1920x1080 |
>
> **Configs use the mDNS name `farm-pi5.local`, deliberately, not an IP.** This farm's hosts
> drift (`.68→.69`, `.54→.217`, `.71→.54`) and every drift has cost an outage. mDNS follows the
> drift with no edit and no router reservation. The Pi is at `192.168.0.17` today with MAC
> `88:a2:9e:a2:e6:23` on its **native** port (`eth0`, driver `macb`) — the Genesys dock's
> Realtek adapter is `eth1` and is DOWN and unused, so **do not** conclude from `lsusb` that
> networking runs through the hub.
>
> **🔑 IDENTITY IS NOW STRUCTURAL, AND THE COLLISION CLASS IS GONE.** Each service opens exactly
> one `/dev/v4l/by-id/` path derived from the camera's own USB serial:
>
> ```
> /dev/v4l/by-id/usb-Jieli_Technology_USB_PHY_2.0-video-index0            jieli-dashcam
> /dev/v4l/by-id/usb-USB_CAMERA_USB_CAMERA_240725172848-video-index0      usb-webcam-1080p
> ```
>
> That `240725172848` is the same serial recorded in the table below — same physical unit. These
> paths survive replug, reboot and plug order. There is **no index fallback, no name substring
> match, no unique-resolution probe, no picture test and no `PREFER_EXTERNAL`** on this host.
> Two services cannot land on one camera when each opens a distinct serial-derived path, so the
> mislabel incidents described in the notes below **cannot recur on the Pi**. ⛔ Do not add an
> index fallback "in case the path is missing" — a missing path means a missing camera, and
> guessing is the bug this replaced.
>
> **⛔ NO IMAGE PROCESSING on this host** (Boss directive). Frames are served exactly as the
> sensor produced them — no gray-world WB, no orange desaturation, no highlight roll-off, no
> unsharp mask. If a picture looks wrong here, it is the camera, the lens or the light. Do not
> reintroduce a processing layer; the whole point is that there is nothing left to blame.
> Camera-side V4L2 controls (gain, exposure) are not processing and live in
> `/etc/farmcam/<name>.env`.
>
> **The MacBook Air keeps ONLY its built-in FaceTime HD** on `:8089`. With one permanently
> attached camera it has nothing to collide with, which closes the open macOS identity bug by
> removing its precondition rather than patching it — the `AVCaptureDevice.uniqueID` work
> proposed in `docs/04-Aug-2026-camera-identity-collision-incident-and-fix-plan.md` is **not
> needed**.
>
> **✅ `usb-webcam-1080p` IS FIXED — and it was never broken.** Daylight-confirmed 06-Aug-2026:
> full 1920x1080, **mean 128.9, 0.0% clipped**, sharp. Its V4L2 `gain` had been pinned at **0**
> against a default of 32, which blackens the output on any host — and that one fact explains its
> entire recorded history, the pure-black frames on GWTC and the useless video interface on the
> Air alike. The whole fix is `FARMCAM_V4L2_CTRLS=gain=32,auto_exposure=3` in
> `/etc/farmcam/usb-webcam-1080p.env`. **Every "intermittent / needs a physical replug" warning
> about this camera elsewhere in this file predates that discovery — do not send anyone out to
> replug it.**
>
> **🟡 `jieli-dashcam` is overexposed in daylight and it is NOT fixable in software.** Night
> frames are excellent; daylight runs ~mean 220 with ~41% of pixels clipped white. `gain` and
> `brightness` sweeps changed nothing, and its `auto_exposure` / `exposure_time_absolute`
> controls are stubs (`min=0 max=0`, read-only) — it is a car dashcam with fixed internal AE
> tuned for night driving. Likeliest cause is the current aim across a bright sky-and-treeline
> scene. **Re-aim it, shade it, or accept it** (it is time-lapse material, never a gem).
> ⛔ Do not reintroduce image processing to recover the highlights — 41% of the pixels are pure
> white and that data is gone. Detail and the retracted YUYV red herring are in the bring-up log.
>
> Detail: [`docs/05-Aug-2026-birdcatraz-pi5-bringup-log.md`](docs/05-Aug-2026-birdcatraz-pi5-bringup-log.md),
> CHANGELOG v2.62.0 / v2.63.0.

> ### 🔴 04-Aug-2026 — POWERED HUBS FITTED; `usb-webcam-1080p` MOVED TO GWTC; 23-MINUTE MISLABEL WINDOW
>
> Boss fitted the powered USB hubs. In the reshuffle the 1080p USB webcam left the MacBook Air.
>
> **`usb-webcam-1080p` now lives on GWTC — `http://192.168.0.69:8089`, 1920x1080.** Identity is
> **proven, not assumed**: GWTC reports `USB\VID_32E6&PID_9221\240725172848`, and that serial
> `240725172848` is the exact one recorded for this camera in the table below. Same physical
> unit, new host. Both config files were repointed and both Mac Mini agents reloaded; the Air's
> plist is parked at `com.farmguardian.cam-usb-webcam-1080p.plist.moved-to-gwtc-04aug2026`.
> The GWTC service is the `usb-cam-host` **scheduled task with a boot trigger** (plus
> `usb-cam-watchdog` on a repeating timer), so it survives a reboot.
>
> **⚠️ MISLABEL WINDOW — `2026-08-04T23:30:10Z` → `23:52:51Z` (19:30–19:53 local).** All three
> `usb-cam-host` services on the Air restarted during the hub swap and **all three resolved to
> the same cv2 index**, so every one of them served the **built-in FaceTime camera**:
>
> | camera_id | rows in window | what the frames actually are |
> |---|---|---|
> | `usb-webcam-1080p` | 642 | FaceTime footage |
> | `jieli-dashcam` | 46 | FaceTime footage |
> | `macbook-air-facetime` | 147 | correct |
>
> **Nothing escaped** — across the window both affected ids have `discord_reactions = 0`, no
> `discord_message_id`, no `ig_posted_at`, no `ig_story_posted_at`, no `reel_posted_at`. The
> 21:30 dashcam reel had not yet run. Rows are left in place (same handling as the 21-Jul case);
> they are raw-tier with `raw_retention_hours=48`, so they sweep themselves by **2026-08-06
> ~23:53Z**. **Do not build anything from that window before then.**
>
> **The identity gate did not hold, and is not yet fixed.** `_resolve_verified_device_index()`
> guards only the *relative* margin between best and runner-up; when a sibling process already
> holds the other cv2 index, only one candidate is openable, `runner_up is None`, and the guard
> is skipped entirely — so differences of **37.1** and **32.6** were accepted. For scale, a true
> match measured **0.4**. Root cause, evidence and three fix options:
> [`docs/04-Aug-2026-camera-identity-collision-incident-and-fix-plan.md`](docs/04-Aug-2026-camera-identity-collision-incident-and-fix-plan.md).
> Until that lands, **restart the Air's camera services one at a time, never together.**
>
> **✅ How to check for this — bytes, not eyeballs.** The cameras on the Air overlook overlapping
> ground, so similar framing proves nothing. Fetch `/photo.jpg` from every endpoint
> **concurrently** and hash. Any two hashes matching = two services on one camera.
>
> ```bash
> for p in 8089 8091; do curl -s -o /tmp/c$p.jpg http://192.168.0.50:$p/photo.jpg & done
> curl -s -o /tmp/cg.jpg http://192.168.0.69:8089/photo.jpg & wait
> md5 -q /tmp/c8089.jpg /tmp/c8091.jpg /tmp/cg.jpg
> ```
>
> Verified clean over three rounds after containment on 04-Aug-2026.
>
> **`/health`'s `resolved_device_name` is NOT current truth.** It is recorded once at resolution
> time and never re-checked — it cheerfully reported `USB CAMERA #4` for a camera that was no
> longer on the machine. Read it as "what this process believed at startup".

> ### 🔴 CAMERAS RENAMED + DASHCAM ADDED — 01-Aug-2026 — THIS SUPERSEDES EVERY NAME BELOW
>
> **`usb-cam` and `mba-cam` no longer exist.** Every camera is now named for what it
> actually is. Old names survive below only inside historical incident write-ups; do not
> use them in config, code, or queries.
>
> | Old name | New name | What it actually is |
> |---|---|---|
> | `mba-cam` | **`macbook-air-facetime`** | MacBook Air 2013 built-in FaceTime HD, 1280x720. Port **8089** on `192.168.0.50` |
> | `usb-cam` | **`usb-webcam-1080p`** | Generic USB webcam, 1920x1080, VID `0x32e6` / PID `0x9221`, serial `240725172848`. No brand or model — the manufacturer string is literally "USB CAMERA". ⚠️ **Moved 04-Aug-2026: now port 8089 on GWTC `192.168.0.69`**, not 8090 on the Air. Serial is the identity check |
> | — new — | **`jieli-dashcam`** | Car dashcam in PC-camera mode. Jieli Technology "USB PHY 2.0", VID `0x1224` / PID `0x2825`, 1280x720 wide-angle. Port **8091** on `192.168.0.50`. **Best picture of the three.** **Aim changes often — do not describe what it is pointed at anywhere** |
>
> **(Superseded 04-Aug-2026 — only two of the three are on the Air now; see the block above.)**
> **All three cameras are on the MacBook Air**, on one VIA Labs USB hub, each served by its
> own `usb-cam-host` instance (LaunchAgents `com.farmguardian.cam-<name>`). The old single
> `com.farmguardian.usb-cam-host` plist there is suffixed `.replaced-01aug2026`.
> Archive rows were migrated: 23,078 `usb-cam` and 21,447 `mba-cam` rows now carry the new ids.
>
> **⚠️ The device-position trap, and why binding by name was not enough.** ffmpeg and OpenCV
> number the same cameras **differently on the same machine at the same moment** — measured
> quiescent on the Air: ffmpeg `[0] FaceTime [1] USB PHY [2] USB CAMERA`, OpenCV
> `[0] USB PHY [1] USB CAMERA [2] FaceTime`. The old name-binding looked a name up in
> ffmpeg's list and handed that number to OpenCV, so `NAME_CONTAINS=FaceTime` would have
> opened the **turkey-run camera** — the 21-23 Jul mislabel all over again. Position also
> shifted twice in one afternoon as cameras were plugged in.
>
> **Resolution is no longer a valid identity check.** The dashcam and FaceTime are both
> 1280x720. The old "any mba-cam row at 1920x1080 is mislabelled" test below still holds for
> historical rows but cannot identify a camera today. `usb_cam_host.py` now proves identity
> before serving (unique-resolution test, then a picture comparison against a frame captured
> by device name) and **serves nothing rather than guessing**. To check a camera yourself,
> pull `/photo.jpg` and look at it.
>
> **⚠️ The built-in camera can vanish from the system while everything looks fine.** On
> 01-Aug-2026 `FaceTime HD Camera` disappeared from both the AVFoundation device list and
> `system_profiler SPCameraDataType` for ~50 minutes, with the **lid open**
> (`ioreg -r -k AppleClamshellState` → `No`) and the camera daemons running. Its service
> correctly served 503 throughout rather than substituting another camera. **Re-seating the
> external USB hub brought it straight back.** The built-in FaceTime is an internal USB device
> on the same controller as that hub, so re-seating forces a root-hub re-enumeration that
> bounces it too — the same mechanism documented for GWTC's WiFi NIC. Two candidate triggers,
> not separated: an ffmpeg process killed mid-open on that camera minutes earlier, or the bus
> being loaded by a third camera (the dashcam alone requests the hub's entire 500 mA).
> **If the built-in goes missing, re-seat the hub before theorising** — and check the clamshell
> state before blaming the lid, which is a mistake that was made and published here first.
>
> **🔌 THE BUS-POWERED HUB CANNOT CARRY THREE CAMERAS — GET A POWERED HUB (01-Aug-2026).**
> Roughly two hours after the dashcam joined, both hub-attached cameras failed within ~2
> minutes of each other (`usb-webcam-1080p` ~18:23, `jieli-dashcam` ~18:25) while the Air's
> **internal** FaceTime camera never missed a frame. That split — everything on the hub dies,
> the internal camera is untouched — is the whole diagnosis.
>
> The two failed differently, which is what makes it power rather than software:
> - **`jieli-dashcam` fell off the USB bus entirely** — absent from `SPUSBDataType` and
>   `SPCameraDataType`. It does **not** come back on its own; it needs a replug.
> - **`usb-webcam-1080p` stayed enumerated but degraded to 640x480** instead of 1920x1080 —
>   a starved device failing to negotiate its high-bandwidth mode. A service restart brought
>   it straight back to full 1920x1080, so **the camera hardware is fine**.
>
> The dashcam alone requests the hub's entire budget (`Current Required: 500 mA` against
> `Current Available: 500 mA`), before the other two draw anything. With the USB webcam's
> 100 mA on top, demand is **600 mA against a 500 mA supply** — over budget on the spec
> sheet, not just in theory.
>
> **Demonstrated in both directions (02-Aug-2026), which settles it.** On 01-Aug the
> dashcam was the one that dropped off. Overnight the dashcam was restored to PC mode
> first — and by morning the **USB webcam** was the casualty instead, this time losing its
> camera interface entirely: still listed in `SPUSBDataType` as a USB device, but absent
> from `SPCameraDataType`, so nothing can open it as a camera. **A service restart did NOT
> recover it** (unlike the milder 640x480 degradation the day before). Whichever of the two
> comes up second loses. They cannot coexist on a 500 mA bus-powered hub.
>
> **Third casualty in four days, and this time it was the BUILT-IN (04-Aug-2026).** The
> blast radius is wider than 01-Aug and 02-Aug had observed: it is no longer only the two
> hub cameras. Sequence, from the service logs and `log show`:
>
> 1. **03:40 and 04:04** — repeated `kIOUSBPipeStalled` / `transaction timed out` from
>    `VDCAssistant` against `guid:0x1423000012242825`, which is the **dashcam**
>    (`SPCameraDataType` → `USB PHY 2.0 #2`). The hub device in distress, as usual.
> 2. **~04:10** — the dashcam goes unavailable in AVFoundation.
> 3. **04:12:17** — the **built-in FaceTime** starts failing reads, five consecutive, and
>    then vanishes from the AVFoundation list too. Two minutes later, in that order.
>
> This is the mechanism already recorded above — the built-in sits on the same USB
> controller as the hub, which is why re-seating the hub revives it — but 01-Aug only ever
> saw it recover *after* a physical re-seat and never caught the hub camera dragging it down
> live. It does. **Do not treat a built-in FaceTime dropout as unrelated to the hub.**
>
> **A fourth severity, distinct from the three above: the service process wedges.** The
> dashcam recovered on its own. FaceTime's service did not, for **11 hours** — its grabber
> stayed alive and kept probing every ~4s, logging `no cv2 index produced a frame`, while a
> **fresh** process on the same box opened both cv2 indices and read 1280x720 from each
> without trouble. So the camera was fine and cv2 was fine; the long-running process's
> AVFoundation state was poisoned when the device was yanked out from under it, permanently.
> `launchctl kickstart -k gui/$(id -u)/com.farmguardian.cam-macbook-air-facetime` cleared it
> in under 45s — identified by picture → cv2 index 1, margin 27.1 vs 12.8, 0 failures.
>
> Severity ladder, for telling these apart later:
> 1. Degraded resolution (640x480), service restart fixes it.
> 2. Off the camera list, still on the USB bus — restart does **not** fix it, needs a replug.
> 3. Off the USB bus entirely — needs a replug.
> 4. **Device is healthy, the service process is wedged** — a fresh process proves the
>    hardware is fine; restart the one service. Check this before touching anything physical.
>    **Handled automatically since v2.61.0 (04-Aug-2026).** A host that cannot take a camera
>    it can *see* now exits after 5 minutes and launchd starts a clean one. `/health` reports
>    `acquire_stalled_s` — `0.0` is healthy; climbing means it is wedged and about to restart
>    itself. It does **not** exit when the camera is genuinely absent (severities 2 and 3
>    above), because a restart cannot help there. So: **`acquire_stalled_s` climbing = software,
>    stays `0.0` while the camera is missing = hardware, go and replug.** If a host restarts
>    every ~5 minutes, the fault is not in-process — that is the hub, not the service.
>
> **Fix: an externally powered USB hub** — one with its own DC power brick, not merely a
> "USB 3.0" hub. USB 2.0 speed is plenty for these cameras; the power is the point. **This
> was identified on 01-Aug, re-confirmed on 02-Aug, and has still not been fitted; 04-Aug is
> the third camera lost to it.** Until it is fitted, expect this to keep happening, and
> expect the internal camera to be at risk too — it no longer gets a pass. If only one
> external camera can be kept, **keep the dashcam** — much better picture than the USB webcam.
>
> Do NOT diagnose this as a code or naming fault. The camera services behaved correctly
> throughout: each refused to substitute a different camera and served 503 instead.

> Plan: [`docs/01-Aug-2026-camera-rename-and-dashcam-plan.md`](docs/01-Aug-2026-camera-rename-and-dashcam-plan.md). CHANGELOG v2.57.0.

> ### ⚠️ Corrections applied 2026-07-22 — read these before the tables below
>
> The per-camera detail below is still the best hardware reference we have, but these specific facts were re-verified against the live config files, `launchctl`, and LM Studio on 2026-07-22 and **override anything the tables say**:
>
> | Claim below | Reality 2026-07-22 |
> |---|---|
> | "The Six Cameras" | **Seven** configured cameras (`house-yard`, `s7-cam`, `usb-cam`, `gwtc`, `mba-cam`, `dominator-cam`, `duo2`), plus a disabled `iphone-cam` row |
> | `usb-cam` is on the MSI Dominator `192.168.0.194:8090` | **The two config files disagree.** `config.json` points `usb-cam` at `Marks-MacBook-Air.local:8089`; `tools/pipeline/config.json` still points it at `192.168.0.194:8090`. Reconcile before trusting either |
> | GWTC at `192.168.0.68` | GWTC is at **`192.168.0.69`**, and `gwtc` is **`enabled: false`** in both config files. **23-Jul-2026: the laptop is healthy and SSH-reachable, but its `Hy-HD-Camera` reports `Present: False` — absent from the device bus — so ffmpeg crash-loops (dies in 0-1s, NOT the wedged-dshow zombie the watchdog handles) and the RTSP path 404s. Needs hands on the laptop; check for a function-key camera toggle first.** |
> | GWTC runs "LM Studio on :9099" | **False — retracted.** GWTC has never run LM Studio. The only LM Studio is the Mac Mini's on `localhost:1234`, currently serving `qwen/qwen3-vl-4b`. GWTC's signature is MediaMTX on `:8554` |
> | Mac Mini at `192.168.0.71` | Mac Mini is at **`192.168.0.54`** |
> | duo2 time-lapse reel "daily 21:20" | **15:00**, as one of three fixed daily camera reels (house-yard 09:00, s7 12:00, duo2 15:00) |
> | "`config.json` + `tools/pipeline/config.json` already use the live [Reolink] IPs" `.89`/`.156` | **False** — the configs contain `.88` (house-yard) and `.155` (duo2). Both cameras were confirmed capturing frames on 2026-07-22 at those addresses. Neither is pinned; give both a static lease |
> | `iphone-cam` / `iphone-cam-host` | Disabled — plists suffixed `.disabled-20apr2026` |
>
> Everything else (hardware quirks, the Fn+F6 trap, the USB-port trap, name-binding, the device-not-location naming rule) is unaffected and still correct.

> ### 🚨 `mba-cam` vs `usb-cam` — the label lied for two days (23-Jul-2026)
>
> **A camera name here means the DEVICE, never the host it is plugged into.** That rule was silently broken between **2026-07-21 13:31Z and 2026-07-23 12:55Z**: the USB camera was plugged into the MacBook Air, and because `usb-cam-host` defaults to `USB_CAM_PREFER_EXTERNAL=true` it served that external camera — while the pipeline was still filing the frames under `mba-cam`. So **8,682 archive rows labelled `mba-cam` in that window are actually USB-camera footage of the turkey pen**, not the MacBook Air's FaceTime HD.
>
> **How to tell them apart, definitively:** resolution. The 2013 FaceTime HD physically cannot exceed **1280x720**. The USB camera is **1920x1080**. Any `mba-cam` row at 1920x1080 is mislabelled. (A further 19 rows at 640x480 on 23-Jul are the degraded transition while the camera was being moved.)
>
> **Current state is correct and verified:** `mba-cam` = MacBook Air FaceTime HD @ 1280x720 (the MBA plist now sets `USB_CAM_PREFER_EXTERNAL=false`); `usb-cam` = the USB camera, now on GWTC @ 1920x1080.
>
> **Do not build anything from the contaminated window.** It is raw-tier with `raw_retention_hours=48`, so it sweeps itself by **2026-07-25 12:55Z** and leaves any 24h reel window by **2026-07-24 12:55Z**. Don't re-enable the `mba-cam` reel lane before then, or the reel will be USB-camera footage wearing an MBA label — which is exactly what happened to a build on 23-Jul.
>
> **The general trap:** whenever a USB camera is plugged into a host that also has a built-in camera, `PREFER_EXTERNAL` decides which one that host's endpoint actually serves — and nothing downstream can tell. After ANY physical camera move, verify with `curl http://<host>:8089/health` and check `resolved_device_name` and `resolution` before trusting the label.



**Last verified end-to-end:** 2026-06-22 ET (Claude Opus 4.7, Bubba sub-agent — v2.43.0 — tightened onboard motion-detection sensitivity on BOTH Reolinks (house-yard MD 25→18, duo2 MD 41→28; sensitivity only, AI/spotlight/siren/auto-track untouched); duo2 now cuts a daily time-lapse reel like the other cams (`com.farmguardian.ig-duo2-timelapse-reel`, daily 21:20). **Live Reolink IPs corrected: house-yard = `192.168.0.89`, duo2 = `192.168.0.156`** — the `.88`/`.14` values still appearing in the table/text below are STALE (both failed to connect 2026-06-22; `config.json` + `tools/pipeline/config.json` already use the live IPs)). Prior: 2026-06-12 ET (Claude Opus 4.8 — v2.41.2 — `usb-cam` moved onto the MSI Dominator at `192.168.0.194:8090` alongside `dominator-cam` at `:8089`; both feeds are **name-bound** by DirectShow FriendlyName and **auto-start on login** via AtLogOn scheduled tasks — survive reboot, can't swap). Prior: 2026-05-06 ET (Opus 4.7 — v2.40.5 — added `dominator-cam`: opportunistic, manually started by Boss via desktop shortcut on the MSI Dominator GT72)

> **Adding/removing a camera?** Use `scripts/add-camera.py` — it writes both `config.json` and `tools/pipeline/config.json` atomically, probes the URL before committing, and refuses duplicates. Hand-edit only for tweaks to an existing entry. Full walkthrough: `docs/19-Apr-2026-add-camera-cli.md`.
**Why this file exists:** The frontend devs found camera-name mismatches (the backend said `gwtc` while the stream URL said `nestbox`; thumbnail labels said "Brooder" for three different cameras pointed at the brooder). This is the single source of truth for the **hardware** side: what each camera is, what machine hosts it, where its frames flow, and the naming rules that prevent the mismatches from reappearing. If something here disagrees with `config.json`, a source file, or a frontend registry, **this file is the ground truth you bring the others in line with** — not the other way around. Re-verify the "Last verified" stamp any time you change a camera.

## The Cameras (SIX, as of 2026-08-10 — config-entry count and Guardian-dashboard count now
agree, after BOTH `dominator-cam` and `gwtc` were retired the same day. Boss's call on both:
the Birdcatraz Pi (`farm-pi5`) now covers what these two laptop-hosted cameras used to, so
neither is needed. See `docs/10-Aug-2026-dominator-cam-retirement-plan.md` and
`docs/10-Aug-2026-gwtc-retirement-plan.md`. (This line went through several wrong/incomplete
counts earlier the same day — eight, six, seven, a "seven configured but six visible" split —
before settling here; if a future edit ever needs to touch this again, cross-check
`scripts/add-camera.py list` against live `GET /api/cameras` rather than trusting either number
in isolation.) Was eight from 01-Aug-2026 when `jieli-dashcam` joined as a first-class camera,
v2.57.0; was seven as of 2026-07-22; was six until 2026-05-06 when `dominator-cam` was added as
opportunistic, manually-started; was four until 2026-04-30 when `mba-cam` was recommissioned as optional
brooder monitor; was five until 2026-04-15 09:16 ET when `mba-cam` was decommissioned)

**⚠️ This file is stale beyond the `dominator-cam` retirement above** — it still predates the
01-Aug-2026 camera renames (`usb-cam`→`usb-webcam-1080p`, `mba-cam`→`macbook-air-facetime`) and
the 05-Aug-2026 Birdcatraz Pi migration in several places below. CLAUDE.md's camera roster is
the fresher source until this file gets a full resync pass.

| `name` (config) | Camera hardware | Host machine | Host IP | Source URL (how Guardian pulls) | Capture method | Detection | Currently aimed at |
|---|---|---|---|---|---|---|---|
| `house-yard` | Reolink E1 Outdoor Pro (4K, PTZ, ONVIF, WiFi) | _itself — standalone IP camera_ | `192.168.0.88` | HTTP snapshot: `http://192.168.0.88/cgi-bin/api.cgi?cmd=Snap&...` (via the `reolink-aio` library; native 4K JPEG) | `source: snapshot`, `snapshot_method: reolink` | **on** (predator detection; night window 20:00-09:00 ET runs 2s polls, daytime runs 5s polls) | The yard, sky, and coop approach |
| `s7-cam` | **🔴 HANDSET REPLACED 10-Aug-2026 — now a Samsung Galaxy S7 `SM-G930V` (`heroqltevzw`, Verizon) on Android 6.0.1** (build `G930VVRS4APH1`, adb serial `4fad774d`, WiFi MAC `2C-0E-3D-09-77-A4`), holding `192.168.0.249` via a **static IP set on the phone** (not a router reservation — the router's `.249` reservation still points at the retired MAC `8C-F5-A3-B6-5A-E5`). **Its micro-USB port works, so ADB is available again** — unlike the retired handset. **Powered from a 3 A USB wall charger** (came with the phone), not a Qi pad — so unlike its predecessor it can run continuously instead of being carried indoors to charge. Stripped to 200 packages; Play Services + IMS deliberately kept. **Sensor confirmed 10-Aug-2026: Samsung ISOCELL S5K2L1** (`s5k2l1sx`, chip id `0x20c1`), NOT the Sony IMX260 the docs claimed — read from `dmesg | grep sensor_match_id`, since the `/sys/devices/virtual/camera/rear/*` nodes are root-only. Same spec as the Sony (12 MP, f/1.7, dual-pixel AF); the VLM `context` string was corrected to match. Full log: `docs/10-Aug-2026-s7-galaxy-replacement-swap-log.md`. **The predecessor was SM-G930F on Android 8.0.0, retired with a water-killed USB port.** Runs IP Webcam by Pavel Khlebovich (`com.pas.webcam` v1.14.37.759 aarch64 — same build on both handsets). **Orientation: PORTRAIT, fixed (v2.35.2, 2026-04-21; watchdog added 2026-04-22).** IP Webcam settings `orientation=portrait` + `photo_rotation=90` emit 1920×1080 sensor-native pixels plus EXIF `Orientation=6`; `capture.py:_apply_exif_rotation` bakes the rotation in before `cv2.imdecode`, so every consumer sees 1080×1920 portrait. Physical phone rotation does NOT drive orientation — it's set via `curl http://192.168.0.249:8080/settings/orientation?set=portrait` and the equivalent `photo_rotation` call. These settings DO reset when the IP Webcam capture process dies (phone reboot / app kill); this is handled per-frame in code by `force_portrait` (`capture.py:81` + the mirror in `tools/pipeline/capture.py`), which rotates any wider-than-tall frame 90° CW regardless of EXIF; `config.json → http_startup_gets` also re-arms it on every Guardian restart. **⚠️ `com.farmguardian.s7-settings-watchdog` was RETIRED 10-Aug-2026 — all three of its pushes were redundant and it had no recovery path; do not restart it (see `docs/10-Aug-2026-s7-settings-watchdog-retired.md`). An `EXIF Orientation=1` reading after a reboot is EXPECTED, not a fault.** **Decision:** portrait is the s7-cam's native aspect ratio for IG stories + reels (its primary destination), so portrait is deliberate. Backend helper reads whatever EXIF says, so flipping back to landscape requires only flipping the phone-side settings — the pipeline follows. | _itself_ | `192.168.0.249` | `http://192.168.0.249:8080/photo.jpg` — HTTP snapshot pull, **1080×1920 portrait** JPEG after EXIF-bake (~950 KB/frame) | `http_url` snapshot poll via `HttpUrlSnapshotSource` (v2.24.0, 5 s cadence; v2.35.2 adds EXIF rotation) | off | **Birdcatraz generally — it moves around and is no longer nesting-box-specific** (Boss, 10-Aug-2026); aimed at the flock, not one feature |
| `usb-cam` | Generic USB webcam (1920×1080), portable — plug it into whichever host | Any host running the `usb-cam-host` service. **Currently MSI Dominator GT72 (`192.168.0.194`)** — moved here 2026-06-12 when Boss plugged it into the Dominator (was on GWTC 2026-04-24 → 2026-06-12; ran briefly on MBA 2026-04-30). `device_index=1` on the Dominator because device 0 is the built-in **BisonCam NB Pro**, which is served separately as `dominator-cam` on `:8089` (same `device 0 = built-in / device 1 = USB CAMERA` pattern as GWTC). **⚠️ USB PORT MATTERS (2026-05-03):** this camera (VID 32E6, PID 9221) is sensitive to which physical USB port it is plugged into. If `/health` returns `camera_open: false` and `powershell "Get-PnpDevice -FriendlyName 'USB CAMERA' | Select-Object Present"` shows `Present: False`, the camera is not on the bus — unplug it and plug it into a different USB port. No driver fix, no software restart will recover it; only a port swap works. This is a Windows/driver quirk with this specific camera hardware. | `192.168.0.194:8090` | `http://192.168.0.194:8090/photo.jpg` — HTTP snapshot pull, 1920×1080 JPEG via the `usb-cam-host` FastAPI service at `C:\farm-services\dominator-cam\usb_cam_host.py`, started by the **`dominator-cam-usbcam` scheduled task** (`start-usbcam.bat`), **name-bound** via `USB_CAM_DEVICE_NAME_CONTAINS=USB CAMERA` (resolved by DirectShow FriendlyName through `C:\ffmpeg\bin\ffmpeg.exe`, opened with `CAP_DSHOW` — replug/reboot-proof, can't swap with `dominator-cam`). The task has an **AtLogOn trigger so it auto-starts after a reboot/login** (interactive session, unlimited runtime, auto-restart ×3). See `deploy/dominator-cam/README.md`. | `source: snapshot`, `snapshot_method: http_url` via `HttpUrlSnapshotSource` (5 s cadence) | off | Coop run (natural daylight) |
| `gwtc` | **🔴 RETIRED 10-Aug-2026 — no longer a Guardian camera.** Boss no longer wants it (Birdcatraz Pi covers this duty now); removed from both config files, including a `timelapse_reel_daylight_only_cameras` list reference the removal CLI doesn't reach. On-box services (`mediamtx`, `farmcam`, both watchdogs) NOT yet disabled — the laptop was unreachable at retirement time. See `docs/10-Aug-2026-gwtc-retirement-plan.md`. Row kept below for historical hardware detail only. Was: built-in webcam on the Gateway laptop ("Hy-HD-Camera", 720p max) | Gateway laptop (Windows 11) | `192.168.0.69` (DHCP — drifts on reboot; find by service signature on `:8554`, see "Finding a drifted host" below) | `rtsp://192.168.0.69:8554/gwtc` (TCP — published by `ffmpeg` via DirectShow → MediaMTX v1.12.2) | `rtsp_url_override`, OpenCV `VideoCapture` | off | Roof of coop (overhead/approach view, 2026-04-30 onward) |
| `iphone-cam` | Boss's iPhone 16 Pro Max via Apple Continuity Camera (USB or wireless to the Mac Mini) — **opportunistic**, only present when the phone is hooked up | Mac Mini "Bubba" (`192.168.0.71`) running a second `usb-cam-host` instance | `127.0.0.1:8091` (loopback, mini-only) | `http://127.0.0.1:8091/photo.jpg` — same `usb-cam-host` binary as the Logitech path, but with `USB_CAM_DEVICE_NAME_CONTAINS=iPhone` so the grabber resolves the AVFoundation video device whose name contains "iPhone" instead of using a raw index. When no iPhone is enumerated by AVFoundation, `_open()` returns `None` and the grabber idles → `/photo.jpg` returns 503 → consumers retry, no spam. **Cannot fall through to "Capture screen 0"** thanks to the substring gate plus a defensive screen-name filter in the resolver. | `source: snapshot`, `snapshot_method: http_url` via `HttpUrlSnapshotSource` (10 s cadence — opportunistic, not surveillance) | off | Whatever Boss is pointing the phone at — typically birds for portraits |
| `dominator-cam` | **🔴 RETIRED 10-Aug-2026 — no longer a Guardian camera.** Boss no longer wants it; removed from both config files (`scripts/add-camera.py remove dominator-cam`) and the `dominator-cam-bisoncam` scheduled task disabled on the box (`schtasks /change ... /disable`, not deleted — reversible if ever wanted back). See `docs/10-Aug-2026-dominator-cam-retirement-plan.md`. Row kept below for historical hardware detail only — treat everything past this point in the row as **past tense**. Was: BisonCam NB Pro (built-in 1080p webcam, OpenCV device 0) on the MSI Dominator GT72 6QD. **Opportunistic — as of 2026-06-12 started via the `dominator-cam-bisoncam` interactive scheduled task (survives SSH disconnect, NOT reboot); the manual `dominator-cam` desktop shortcut still works too.** Its companion `usb-cam` (external USB CAMERA, device 1) runs the same way on `:8090` via the `dominator-cam-usbcam` task — see that row and `deploy/dominator-cam/README.md`. Guardian will report `online: true, capturing: true` whenever the URL is configured, but `is_live: false` and stale-frame status when the script isn't running on the box (which is most of the time — Boss uses this laptop for other work and Larry's WSL gateway also lives there). **Fn+F6 trap (2026-05-06):** the GT72 has an Fn+F6 hardware webcam toggle that cuts USB power to the cam at the EC level — when it's off, the camera is invisible to Windows entirely (`Get-PnpDevice -Class Camera` returns nothing, no PnP entry under `usbvideo` service even with `-PresentOnly:$false`). If the camera "disappears" from the Dominator, **press Fn+F6 once on the laptop keyboard before chasing software/driver theories**. The MSI SCM service (`Micro Star SCM`) handles the toggle; there is no documented CLI to flip it remotely. | MSI Dominator GT72 6QD (Windows 10 Home, i7-6700HQ, 64 GB, GTX 970M) | `192.168.0.194` (DHCP) | `http://192.168.0.194:8089/photo.jpg` — HTTP snapshot pull via `usb-cam-host` FastAPI service on port `8089`, started by the **`dominator-cam-bisoncam` scheduled task** (`start-bisoncam.bat`), **name-bound** via `USB_CAM_DEVICE_NAME_CONTAINS=BisonCam` (DirectShow FriendlyName via `C:\ffmpeg\bin\ffmpeg.exe`, `CAP_DSHOW`). The task has an **AtLogOn trigger → auto-starts after a reboot/login** (interactive, unlimited runtime, auto-restart ×3). The manual `start.bat`/desktop shortcut still works; `schtasks /end /tn dominator-cam-bisoncam` stops it. No Shawl service. Co-tenants on the box: Boss's day-to-day Windows work, Larry's WSL Ubuntu OpenClaw gateway. | `source: snapshot`, `snapshot_method: http_url` via `HttpUrlSnapshotSource` (10 s cadence — opportunistic, not surveillance) | off | Whatever Boss has the laptop pointing at when he starts the service |
| `mba-cam` | MacBook Air 2013 FaceTime HD (1280×720) | MacBook Air (`192.168.0.50`) | `192.168.0.50` | `http://192.168.0.50:8089/photo.jpg` — HTTP snapshot pull via `usb-cam-host` FastAPI service (`com.farmguardian.usb-cam-host` LaunchAgent). **device_index=0 on this MBA = FaceTime HD** (USB webcam is on GWTC, so FaceTime is the only camera on this box; AVFoundation enumerates it at index 0). DECOMMISSIONED 2026-04-15; **RECOMMISSIONED 2026-04-30** as optional brooder monitor. On/off = load/unload the LaunchAgent on MBA. When LaunchAgent is loaded, mba-cam appears live in Guardian within 30s. | `source: snapshot`, `snapshot_method: http_url` via `HttpUrlSnapshotSource` (5 s cadence) | off | Brooder (optional — load LaunchAgent on MBA to enable) |
| `duo2` | **Reolink Duo 2 WiFi** (B0B2P9GH3C) — fixed dual-lens, 180° panoramic (two stitched sensors, **no PTZ**), 4K/8MP combined, WiFi 6, IP67. Added by Boss 2026-06-17 as the stationary wide-angle complement to the `house-yard` E1 PTZ ("eyeball" vs "stationary"). Stream presents as a **1920×720** stitched panoramic on lens 01. RTSP + ONVIF both enabled on the cam. **Onboard MD sensitivity 41→28 on 2026-06-22 (v2.43.0) per Boss's "too sensitive" order; AI sensitivity 60 untouched.** | _itself — standalone IP camera_ | **`192.168.0.156`** (live, verified 2026-06-22; DHCP — **still not pinned**, give it a static lease on the Archer AX55. The `.14` formerly here is stale.) | `rtsp://admin:***@192.168.0.156:554/h264Preview_01_main` (lens 01 main; lens 02 = `h264Preview_02_main`; substream `h264Preview_01_sub` if the 4K main stutters over WiFi). reolink-aio HTTP API also live on `:80` (used for MD-sensitivity tuning). | manual `--rtsp` — OpenCV `VideoCapture`, transport=tcp, 5 s cadence; added via `scripts/add-camera.py`. Pipeline ALSO archives duo2 via `reolink_snapshot` (8.5k raw frames/24h) feeding its time-lapse reel. | **off** (no Guardian YOLO yet — enable if Boss wants predator detection) | **Time-lapse reel: ON** as of 2026-06-22 (`com.farmguardian.ig-duo2-timelapse-reel`, daily 21:20, lane `DUO2_TIMELAPSE_LANE`). Zone still TBD — confirm mount/aim with Boss (intended for a fixed zone: entry / barn front / pen overview) |

**Live frame sizes (2026-04-14 11:02 — as pulled through `/api/cameras/<name>/frame`):** `house-yard` ~1.4 MB (native 4K JPEG); `usb-cam` ~420 KB (1080p, libjpeg quality 95); `gwtc` ~120 KB (720p H.264 re-encoded); `mba-cam` ~115 KB (720p H.264 re-encoded); `s7-cam` ~950 KB (1920×1080 IP Webcam JPEG, served via HTTP snapshot pull now that v2.24.0 is live on the phone).

## What Runs Where

| Machine | LAN IP | OS | Services running for Guardian | Other services (for context) |
|---|---|---|---|---|
| **Mac Mini "Bubba"** | `192.168.0.71` (WiFi/en1, currently — see drift note) | macOS 26.3, 14-core M4 Pro, 64 GB | `guardian.py` (LaunchAgent `com.farmguardian.guardian`, auto-starts on boot); `tools.pipeline.orchestrator` daemon (LaunchAgent `com.farmguardian.pipeline`); **`iphone-cam-host` LaunchAgent on `:8091` (`com.farmguardian.iphone-cam-host`, v2.28.x — serves `/photo.jpg` from Boss's iPhone via Continuity Camera, name-gated on substring "iPhone"; idles cleanly when no iPhone is enumerated)**; `usb-cam-host` LaunchAgent (`com.farmguardian.usb-cam-host`, **unloaded on Mini** — USB cam is now on GWTC; the plist remains on disk on the Mini for reference); `cloudflared` tunnel publishing `:6530` to `guardian.markbarney.net` (outbound, no port forward needed) | LM Studio on `:1234` (GLM-4.6v-Flash + others); dev loop for this repo and `farm-2026` |
| **Gateway laptop ("GWTC")** | `192.168.0.69` (WiFi, DHCP — drifted from `.68`) | Windows 11 Home 10.0.22631 (hostname `653Pudding`) | **🔴 Camera retired from Guardian 10-Aug-2026** — nothing consumes this box's feed anymore, but the services themselves are still believed running (not yet reachable to disable): `mediamtx` Shawl service on `:8554` (declares the `gwtc` path in `C:\mediamtx\mediamtx.yml`); `farmcam` Shawl service (wraps `C:\farm-services\start-camera.bat` → ffmpeg dshow `Hy-HD-Camera` → push to `rtsp://localhost:8554/gwtc`); `farmcam-watchdog` Shawl service (auto-recovery for the post-reboot dshow-zombie pattern — see `docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md`); Windows OpenSSH Server | **No LM Studio** (the `:9099` claim was wrong and is retracted). Windows Firewall is DISABLED per `network.md`. |
| **MacBook Air 2013** | `192.168.0.50` (WiFi, DHCP) | macOS Big Sur 11.7.11 (hardware ceiling — no upgrade possible), Intel Core i5 Haswell 1.3 GHz, 8 GB, Python 3.8.9 from `/Library/Developer/CommandLineTools/` | **RECOMMISSIONED 2026-04-30 as optional brooder monitor.** `com.farmguardian.usb-cam-host` LaunchAgent is **loaded and running** on port 8089 — serving the built-in FaceTime HD at `device_index=0` (USB webcam is on GWTC). Unload the plist (`launchctl unload ~/Library/LaunchAgents/com.farmguardian.usb-cam-host.plist`) when brooder monitoring is no longer needed; mba-cam will disappear from Guardian within one roster refresh. `mba-cam` and `mediamtx` plists remain on disk; only `usb-cam-host` is loaded. Runtime at `~/.local/farm-services/usb-cam-host/` (venv + script). | Screensaver disabled (`idleTime=0`, `askForPassword=0`); `pmset sleep=0 disksleep=0 displaysleep=0 standby=0 powernap=0 hibernatemode=0 autorestart=1`. |
| **MSI Dominator GT72 6QD ("Larry's box")** | `192.168.0.194` (WiFi, DHCP) | Windows 10 Home build 19045 (hostname `Mark-MSI-Laptop`), i7-6700HQ, 64 GB RAM, GTX 970M, Python 3.13.3 at `D:\python\python.exe` | TWO `usb-cam-host` FastAPI instances at `C:\farm-services\dominator-cam\` (one per camera): **`dominator-cam`** = built-in BisonCam (device 0) on `:8089`, and **`usb-cam`** = external USB CAMERA (device 1) on `:8090` after Boss plugged it in 2026-06-12. Each runs under its own scheduled task (`dominator-cam-bisoncam`, `dominator-cam-usbcam`) with an **AtLogOn trigger so both auto-start after a reboot/login** (interactive session — DirectShow needs a desktop — unlimited runtime, auto-restart ×3). Each is **name-bound** by DirectShow FriendlyName (`USB_CAM_DEVICE_NAME_CONTAINS=BisonCam` / `USB CAMERA`) via a self-contained `C:\ffmpeg\bin\ffmpeg.exe`, so a reboot/replug can never swap the two labels. Inbound firewall rules `dominator-cam 8089/8090` + `dominator-cam python` allow the Mini through (firewall is ON here, unlike GWTC). Deploy artifacts + runbook: `deploy/dominator-cam/`. (This supersedes the camera's earlier deliberately-opportunistic posture — the USB cam is permanently on this box now; Boss approved auto-start 2026-06-12.) The laptop is also Boss's day-to-day Windows machine and hosts Larry's WSL Ubuntu OpenClaw gateway, so a permanent always-on camera service is not appropriate here. | Whatever Boss does with this laptop. Larry runs OpenClaw inside WSL Ubuntu (see `~/bubba-workspace/skills/larry-access/SKILL.md`). |
| **Reolink E1 Outdoor Pro** | `192.168.0.88` (WiFi) | Reolink firmware | The camera itself — ONVIF on `:8000`, HTTP API on `:80`, RTSP on `:554`. Uses HTTP snapshot path now (RTSP was abandoned — lossy WiFi mangled HEVC reference packets; see CHANGELOG v2.16.0-v2.18.0). | Camera auto-spotlight and auto-tracking run on the camera itself. Guardian layers YOLO detection + coordinated Discord alerts on top. |
| **Samsung Galaxy S7 `SM-G930V`** | `192.168.0.249` (WiFi, static IP set on the phone) | Android 6.0.1 + IP Webcam (`com.pas.webcam` v1.14.37.759) | The phone — serves HTTP `/photo.jpg` on `:8080`. **🔴 Handset physically swapped 10-Aug-2026 (v2.70.0)** — the SM-G930F / Android 8.0.0 phone this row and the paragraph below describe was retired (water-killed micro-USB port). Live-probed in this session: `curl http://192.168.0.249:8080/photo.jpg` EXIF reads `model=SM-G930V`, fresh timestamp. See the main camera table's `s7-cam` row above (already updated for the swap) and `docs/10-Aug-2026-s7-galaxy-replacement-swap-log.md`. Paragraph below kept for historical reference only — describes the retired handset. | **2026-04-14 correction (historical, retired-handset era):** prior docs and HARDWARE_INVENTORY said the phone had been running IP Webcam all along, but when Boss turned it on to flip to http_url mode, the actual installed app was **RTSP Camera Server (`com.miv.rtspcamera`)** — an RTSP-only app with **no** `/photo.jpg` endpoint and an auto-record-to-disk feature that had filled `/sdcard/RTSPRecords` with 19 GB of loops. That's the real reason "continuous RTSP drained the battery" — RTSP Camera Server was the wrong app. Recovery: adb over USB through the MBA, delete recordings, install IP Webcam from Aptoide (MD5-verified), launch, `svc power stayon true`, uninstall `com.miv.rtspcamera`, flip `config.json` to `http_url`. Documented in `docs/13-Apr-2026-s7-phone-setup.md` (updated with the correction). |

**Not Guardian hosts but on the LAN** (per `~/bubba-workspace/memory/reference/network.md`): Boss's MSI Katana 15 HX at `192.168.0.3` (primary workstation); Larry's MSI laptop at `192.168.0.194` (OpenClaw node, separate project); Boss's iPhone at `192.168.0.134`; Boss's Apple Watch at `192.168.0.227`. None of these participate in Guardian.

## Where Each Camera's Frame Lands in the Stack

```
┌─────────────────┐   ┌────────────────────────────┐   ┌────────────────────┐   ┌───────────────────┐
│  Camera         │ → │  Host machine              │ → │  Mac Mini          │ → │  Public website   │
│  (hardware)     │   │  (publishes if needed)     │   │  Guardian / API    │   │  farm.markbarney  │
└─────────────────┘   └────────────────────────────┘   └────────────────────┘   └───────────────────┘

house-yard ─── Reolink's own HTTP /cgi-bin Snap ───► ReolinkSnapshotSource ───► /api/cameras/house-yard/frame ──► Cloudflare tunnel ──► frontend
s7-cam        ─ phone's IP Webcam HTTP :8080/photo.jpg (v2.24.0, live 2026-04-14) ► HttpUrlSnapshotSource ► /api/cameras/s7-cam/frame
usb-cam ────── usb-cam-host FastAPI service on :8089 (whichever host the camera is plugged into — Mini today) ─► HttpUrlSnapshotSource ─► /api/cameras/usb-cam/frame
gwtc ───────── ffmpeg dshow → MediaMTX :8554/gwtc (Gateway laptop) ──────────► RTSP OpenCV ──────► /api/cameras/gwtc/frame
(mba-cam) ──── DECOMMISSIONED 2026-04-15 — MBA repurposed; agents unloaded
```

`guardian.markbarney.net` is a Cloudflare Tunnel from the Mac Mini — outbound-only, no port forwarding, no inbound firewall rule. The tunnel exposes `:6530` (FastAPI dashboard + REST API) to the public internet; the frontend at `farm.markbarney.net` embeds JPEGs from `<tunnel>/api/cameras/<name>/frame` every ~1.2 s.

## Naming Rules (NON-NEGOTIABLE — mirrored in Bubba auto-memory `feedback_camera_naming.md`)

1. **Camera names are device-only, and descriptive.** `macbook-air-facetime`, `usb-webcam-1080p`, `jieli-dashcam`, `s7-cam`, `gwtc`. As of 01-Aug-2026 a name should say what the device *is*, not just which box it lives on — `mba-cam` and `usb-cam` were renamed because neither told you anything useful when three cameras ended up on one laptop. The grandfathered exception is `house-yard` (predates the rule). **Never** `brooder-cam`, `nestbox`, `coop-cam`, `incubator-cam`, or any other "where it is today" string.
2. **The rule applies to every layer.** The `name` field in `config.json`, RTSP paths, MediaMTX `paths:` declarations, ffmpeg push URLs, LaunchAgent labels, Shawl service names, log filenames, dashboard labels, the frontend `lib/cameras.ts` entries (`label`, `shortLabel`, `device`), MDX roster tables, thumbnail captions, stage overlays — **every string a user or future agent can see**. The 13-Apr-2026 incident that hit hardest: `lib/cameras.ts` had `shortLabel: "Brooder"` for `usb-cam`, `"S7 brooder"` for `s7-cam`, `"MBA brooder"` for `mba-cam`, `"Nestbox"` for `gwtc` — three thumbnails all said "brooder," frontend devs found them indistinguishable. Fix was to drop any `location` field entirely and label by hardware (`"USB"`, `"MBA"`, `"S7"`, `"GWTC"`, `"Reolink"`).
3. **The rule applies to publish paths too.** As of 2026-04-13 evening, the Gateway laptop's MediaMTX path was renamed `nestbox` → `gwtc` to match the device name (CHANGELOG v2.24.1). The MacBook Air's path was always `mba-cam`. If anyone adds a new ffmpeg → MediaMTX node, the path **must** equal the camera's `name` in `config.json`.
4. **"Where it's pointed today" is field-note material, not config material.** Put it in a `content/field-notes/*.mdx` entry, a CHANGELOG line, or a photo caption if needed. Don't put it in any struct that drives UI, routing, services, or file names. The rightmost column of this file ("Currently aimed at") is allowed precisely because it's in a doc that's read by humans once, not parsed by machines repeatedly.

## Adding a New Camera (checklist)

1. **Pick the device-name first.** Must not be a location. Short, lowercase, hyphenated. If you can't think of a device name, you haven't thought about it hard enough (`raspicam-1`, `eufy-coop`, `arlo-gate`, etc.).
2. **`config.json` + `config.example.json`** — add the entry. Required: `name`, `ip`, `port`, `username`/`password` if any, `type` (`ptz` or `fixed`), capture config (`source` + `snapshot_method` + method-specific keys OR `rtsp_url_override` + `rtsp_transport`), `detection_enabled` (default `false` until role is decided). `rtsp_transport: tcp` for any WiFi-published camera; only use `udp` if you have hard evidence UDP is stable on that specific camera.
3. **If the camera is published via ffmpeg → MediaMTX on a host machine** — the MediaMTX `paths:` block in the host's `mediamtx.yml` **must** declare the path, the ffmpeg push URL **must** push to that path, and **the path must equal the camera's `name`**. Save canonical copies of the host's config in `deploy/<host>/` so they're version controlled.
4. **Update this file.** Add the row to "The Five Cameras" (update the count in the section header if needed — currently "Five" — or just renumber mentally). Add the host to "What Runs Where" if it's a new machine. Update "Where Each Camera's Frame Lands in the Stack." Update the "Last verified" stamp at the top.
5. **Update `farm-2026/lib/cameras.ts`** — new entry with `name`, `label`, `shortLabel`, `device`, `aspectRatio`. **No `location` field.** Labels and short labels are hardware-only. Update `farm-2026/content/projects/guardian/index.mdx` cameras table.
6. **Restart Guardian** on the Mini (`kill <pid>; nohup ./venv/bin/python guardian.py >> guardian.log 2>&1 & disown`). Verify: `curl -s http://localhost:6530/api/cameras` should list the new camera with `online: true, capturing: true`. Then `curl -s -o /tmp/t.jpg -w "%{http_code} %{size_download}\n" http://localhost:6530/api/cameras/<name>/frame` should return `200` and a JPEG ≥5 KB within ~2 capture intervals.
7. **Restart the pipeline daemon too** (`tools.pipeline.orchestrator`) — it reads its own `tools/pipeline/config.json` at startup. If the new camera should be enriched by the VLM, add it there too.

## Moving an Existing Camera

**Don't rename anything.** The camera's `name`, RTSP path, MediaMTX path, LaunchAgent labels, Shawl service names, log filenames, and config entry all stay exactly as they were. The only things that change are:

1. The rightmost "Currently aimed at" column in this file.
2. A field-note MDX in `farm-2026/content/field-notes/` describing the new placement and why.
3. Optionally the `context` string in `tools/pipeline/config.json` (VLM prompt context — should still lead with the hardware, e.g., "MacBook Air 2013 (Big Sur, 192.168.0.50) built-in FaceTime HD webcam; currently aimed at...").

If you find yourself wanting to rename the camera because it moved: re-read rule #1. You're about to reintroduce the exact problem this file exists to prevent.

## Mac Mini Network Drift (note flagged 2026-04-13)

`~/bubba-workspace/memory/reference/network.md` states the intended Mac Mini config is **en0 Ethernet at `192.168.0.105` with WiFi OFF**. Actual runtime (verified 2026-04-13 19:08): **en1 WiFi at `192.168.0.71`, en0 Ethernet disconnected**. Everything still works — Guardian binds `0.0.0.0:6530` so it's reachable on whatever interface has a route, and the Cloudflare tunnel is outbound-only so it's transport-agnostic — but:

- ICMP-asymmetry rules in `CLAUDE.md` assume Mini-on-Ethernet ↔ laptop-on-WiFi. With both sides on WiFi, `ping` may actually work between the Mini and GWTC/Air, which **inverts the usual "TCP-only probes" guidance** for that specific pairing. Don't build diagnostic habits around the current state; the Ethernet cable might be plugged back in at any time.
- The pipeline daemon's reads of `/gwtc` show up in the Gateway laptop's mediamtx log as coming from `192.168.0.71`. That's the Mini on WiFi, not an unknown consumer.
- If the front-end dashboard (`farm-2026`) displays the Mini's IP anywhere in its system panel, it's pulling it from the Guardian API — which will report the current IP correctly.

**Not fixing this in code.** It's a physical-layer state that Boss controls. Flag for Boss's attention next time the Mini is within arm's reach.

## Finding a Drifted Host

`gwtc` and `mba-cam`'s hosts are both on DHCP. IPs drift after router reboots or long WiFi disassociations. Don't trust the IP in this file as a live value; trust the **service signature**:

```bash
# GWTC: distinctive service is MediaMTX on :8554 (there is no LM Studio on GWTC)
for i in $(seq 2 254); do (nc -z -w 1 192.168.0.$i 8554 2>/dev/null && echo "192.168.0.$i has :8554") & done; wait

# MacBook Air: also publishes MediaMTX on :8554 (so both the Air and GWTC will show up;
# disambiguate by SSH user — Air is `markb@<ip>` with key auth, or by checking the RTSP
# path it serves: gwtc vs mba-cam).

# Mac Mini: reachable on the LAN, usually known; for belt-and-suspenders, sweep :6530
# (Guardian dashboard) or check the Cloudflare tunnel (publicly reachable).
for i in $(seq 2 254); do (nc -z -w 1 192.168.0.$i 6530 2>/dev/null && echo "192.168.0.$i has :6530 (Guardian)") & done; wait
```

Full writeup of why the MAC tables in the network doc are currently wrong and how this lookup recipe survives that: `docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md`.

## Cross-references

- **`CLAUDE.md`** — `Hardware Inventory` top-of-file pointer to this doc; `Network & Machine Access` section for router quirks (ICMP, DHCP drift, WSL2 routing bug) and host SSH recipes; `Multi-Machine Claude Orchestration` for spawning agents on target boxes over SSH.
- **`docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md`** — both GWTC failure modes (reachability and dshow zombie) with their diagnostic recipes and the auto-recovery watchdog.
- **`deploy/macbook-air/`** — canonical copies of the Air's `com.farmguardian.mediamtx.plist` and `com.farmguardian.mba-cam.plist` LaunchAgents.
- **`deploy/gwtc/`** — canonical copies of the Gateway laptop's `start-camera.bat`, `mediamtx.yml`, `farm-watchdog.ps1`, and `install-watchdog.md`.
- **`tools/pipeline/config.json`** — the multi-camera VLM enrichment pipeline's per-camera config. Must stay in sync with `config.json` here; in particular the `rtsp_url` entries for `gwtc` and `mba-cam` track the MediaMTX paths above.
- **`~/bubba-workspace/skills/macbook-air/SKILL.md`** — Air-specific operations (SSH, TCC, screensaver, power, Node.js/Claude Code install recipes).
- **`~/bubba-workspace/memory/reference/network.md`** — master device table including the non-Guardian machines on the LAN (with the known MAC-attribution error and the network-drift-since-doc-was-written status).
- **`farm-2026/lib/cameras.ts`** — frontend's camera registry. Must stay in sync with the "The Five Cameras" table above. Follows the same device-not-location naming rule.
- **`farm-2026/content/projects/guardian/index.mdx`** — public-facing project page, camera roster table.
- **`~/.claude` auto-memory `feedback_camera_naming.md`** — the device-not-location rule with rationale, the Apr-13 incident, and the addendum that every UI string must be hardware-only.
