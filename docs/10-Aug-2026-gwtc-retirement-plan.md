# 10-Aug-2026 — Retire `gwtc`

## Why

Boss: same call as `dominator-cam` earlier today — the Birdcatraz Pi (`farm-pi5`) now covers
what these laptop-hosted cameras used to, so neither is needed anymore. `gwtc`'s own built-in
webcam ("Hy-HD-Camera") has been documented as hardware-absent from the USB bus for weeks
(`Present: False`, needs an Fn+F6 press or hands-on diagnosis CLAUDE.md already describes at
length), and its companion camera role (`usb-webcam-1080p`) already left this box for the Pi
back on 04-Aug-2026. There's nothing left on GWTC worth keeping wired into Guardian.

## Scope

**In:** remove `gwtc` from both Guardian config files; update CLAUDE.md and
`HARDWARE_INVENTORY.md`; CHANGELOG entry.

**Out — blocked by reachability, not by choice:** disabling the on-box services (`mediamtx`,
`farmcam`, `farmcam-watchdog`, `farmcam-wifi-watchdog` Shawl services / scheduled tasks) the
way the Dominator's scheduled task was disabled over SSH earlier today. **GWTC is currently
unreachable** — confirmed twice in this session (port 22 and port 8554 both closed/timeout,
`arp -a` shows an incomplete entry, and a full `/24` sweep for port 8554 found no host on the
LAN at all). There is no way to reach it right now to flip anything off. This is not a new
fault — CLAUDE.md already documents this box's chronic WiFi flakiness and self-healing
watchdogs — but it does mean the retirement is repo-side only today.

## TODOs

1. `scripts/add-camera.py remove gwtc` — handles the top-level entry in both config files.
2. Hand-fix a reference the removal tool doesn't reach: `tools/pipeline/config.json`'s
   `timelapse_reel_daylight_only_cameras` list also named `gwtc`.
3. Reload Guardian + pipeline.
4. Update CLAUDE.md (roster table, count line) and `HARDWARE_INVENTORY.md` (row, running count).
5. CHANGELOG entry noting the on-box services are NOT yet disabled, so a future agent doesn't
   assume this is fully closed out.

## Follow-up (not done here)

Next time GWTC is reachable — or someone's physically at it — stop and disable `mediamtx`,
`farmcam`, `farmcam-watchdog`, and `farmcam-wifi-watchdog` (all Shawl-managed Windows services;
`sc stop <name>` / `sc config <name> start=disabled`, or uninstall via Shawl if that's cleaner).
Not urgent: the camera feed is already out of Guardian, so nothing consumes what those services
produce even while they keep running unattended on a laptop that's off the LAN anyway.

## Results

Executed same session, 10-Aug-2026, alongside the `dominator-cam` retirement. Config-side
removal done and verified (`scripts/add-camera.py list` shows six cameras, both files agree).
On-box service disable deferred per the reachability note above. See CHANGELOG top entry.
