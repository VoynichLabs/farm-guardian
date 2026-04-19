# `scripts/add-camera.py` — One-shot Camera CLI

**Date:** 19-Apr-2026
**Author:** Claude Opus 4.7 (1M context) — "Bubba"
**Status:** Live, round-trip verified against the production configs

## Why this script exists

Farm Guardian has **two** camera config files that must stay in lock-step:

- `config.json` (root) — read by `guardian.py` (port 6530)
- `tools/pipeline/config.json` — read by the `com.farmguardian.pipeline` LaunchAgent

Every previous agent who added or moved a camera forgot one of the two at least once. The dashboard would show the camera; the VLM pipeline wouldn't archive it (or vice versa). CLAUDE.md warns about this trap explicitly. This script makes drift impossible.

## Quick reference

```bash
# Audit (run this first when picking up the project)
scripts/add-camera.py list

# Old Android phone running IP Webcam (cheapest high-quality camera path)
scripts/add-camera.py add brooder-phone \
    --url http://192.168.0.55:8080/photo.jpg \
    --interval 5 \
    --context "Pixel 4a in the brooder, USB-tethered for power"

# AVFoundation camera served locally by a *-cam-host instance
# (--no-probe: the device may not be plugged in yet)
scripts/add-camera.py add boss-ipad \
    --url http://127.0.0.1:8092/photo.jpg \
    --no-probe \
    --context "Boss's iPad over Continuity, opportunistic"

# RTSP camera (Reolink, MediaMTX path, etc.)
scripts/add-camera.py add coop-roof-cam \
    --rtsp rtsp://192.168.0.99:8554/roof \
    --interval 5 \
    --context "Reolink E1 mounted on the coop roof, sky-watching"

# Decommission
scripts/add-camera.py remove old-mba-cam
```

After **any** add or remove, kick the two services so they re-read configs:

```bash
launchctl kickstart -k gui/$(id -u)/com.farmguardian.guardian
launchctl kickstart -k gui/$(id -u)/com.farmguardian.pipeline
```

## What `add` does

1. Loads both config files.
2. Refuses if the camera name already exists in either (the duplicate guard — Guardian's `cameras` is a list, the pipeline's is a dict; either alone wouldn't catch all duplicates).
3. **HTTP-snapshot path (`--url`):** probes the URL with a 5-second `urllib` GET. Accepts `200` (live) or `503` (service up but device absent — normal for opportunistic cameras like an iPhone on Continuity that isn't plugged in yet). Anything else, or connection refused, is a hard fail with a clear message and the `--no-probe` escape hatch suggested. Splits the URL into `base + path` for both configs.
4. **RTSP path (`--rtsp`):** no probe (probing RTSP requires opening a stream, which is heavy). Writes `rtsp_url_override` to Guardian and `capture_method: reolink_snapshot` to the pipeline — the pipeline always pulls RTSP cameras through Guardian's snapshot API, never direct, because Guardian's ring buffer rides through transient publisher hiccups (see the gwtc context note in `tools/pipeline/config.json`).
5. Writes both files atomically (`.tmp` → rename) so a half-written file is never visible to a concurrently-reading service.
6. Prints the post-add restart commands and a reminder to update `HARDWARE_INVENTORY.md` (the configs alone don't tell the next assistant what hardware the camera actually is).

## What `remove` does

1. Removes from both configs.
2. Refuses with exit-1 if the camera was in neither.
3. Reminds you to also `launchctl bootout` and **rename out of `~/Library/LaunchAgents/`** any dedicated `*-cam-host` LaunchAgent — the LaunchAgents auto-load trap is documented in MEMORY.md and bites people every few months.

## What `list` does

Prints a name × config table and detects drift:

```
name                 guardian   pipeline
------------------------------------------
gwtc                 yes        yes
house-yard           yes        yes
iphone-cam           yes        yes
mba-cam              yes        yes
s7-cam               yes        yes
usb-cam              yes        yes
```

If a camera shows `MISSING` in one column, the script exits non-zero and tells you to fix the drift via `remove` + re-add.

## What `add` does **not** do

To stay focused on the recurring drift bug, the script deliberately does not:

- **Generate `*-cam-host` LaunchAgents.** When you're adding an AVFoundation-backed camera (USB webcam, iPhone, etc.) on a host that doesn't yet run a snapshot service, you still need to copy `~/Library/LaunchAgents/com.farmguardian.iphone-cam-host.plist` as a template, change the `Label`, `USB_CAM_PORT`, and `USB_CAM_DEVICE_NAME_CONTAINS`, then `launchctl bootstrap`. That's a one-time per-host operation; the configs are the recurring drift point.
- **Update `HARDWARE_INVENTORY.md`.** That file is documentation about *what each camera is in the real world*, not config — adding it programmatically would just produce filler text. The script ends with a reminder to do it by hand.
- **Restart Guardian / pipeline.** Both services need a kick after the configs change, but a script that mutates running services on its own is harder to reason about than a script that prints the two commands and lets you decide. Print the commands; let the operator run them.

## Adding new camera types

If you ever add a camera that doesn't fit the HTTP-snapshot or RTSP-via-Guardian patterns (e.g., a future Reolink ONVIF event-driven path, or a direct-RTSP-to-pipeline path), extend `cmd_add()` with a new branch — keep the atomic-write contract intact and add a new flag in the same mutually exclusive group as `--url`/`--rtsp`. The schemas for both configs are inlined in the script as Python dicts, so you can see exactly what gets written without grepping the codebase.

## On the broader "best cheap high-quality camera" question

Boss asked this on 2026-04-19: nothing in the UVC webcam space touches an iPhone sensor — the price gap exists because phone cameras have computational pipelines (Smart HDR, Deep Fusion, Night mode) that no $50–200 webcam replicates. The cheapest path to iPhone-class quality is **a used Android phone running the IP Webcam app** — exactly the `s7-cam` pattern. Used Pixel 4a/5a or Galaxy S9–S10 are $40–80 on eBay; all have flagship-class sensors with computational HDR. This script + the existing `HttpUrlSnapshotSource` + `capture_ip_webcam` paths mean adding one is config-only — no code, no plist, no service.
