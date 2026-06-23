<!--
Author: Claude Opus 4.8 (Bubba)
Date: 14-June-2026
PURPOSE: Durable record of the Boss's 14-Jun-2026 decision to add a Reolink Duo 2 WiFi
         (Amazon B0B2P9GH3C) to the farm camera fleet, the verified hardware specs, and the
         step-by-step plan to integrate it into Farm Guardian over RTSP when it physically
         arrives (~3 days out). Written at the Boss's explicit request to keep this on disk in
         the repo and not rely on agent session memory.
SRP/DRY check: Pass — no existing doc covers the Duo 2 purchase or its integration. Cross-refs
         existing fleet docs (farm-lan-map memory, macbook-air-camera-node-plan, dominator-camera-plan)
         instead of restating them.
-->

# Reolink Duo 2 WiFi — Fleet Addition & Guardian Integration Plan

**Decided:** Sun 14-Jun-2026 by the Boss in #meet-the-lobsters.
**Status:** Ordered, in transit (~a few days). Not yet received. Integration pending arrival.
**Owner:** Bubba (wire it into Guardian on arrival).

## Decision
- **Buy:** Reolink Duo 2 **WiFi** — Amazon ASIN **B0B2P9GH3C**.
- **Why:** A fixed, always-on, ultra-wide eye to **complement the existing Reolink PTZ (house-yard, .88)**. The PTZ moves/zooms and can only look one way at a time; the Duo 2 blankets a whole static zone (entry point / barn front / pen overview) with no moving parts to fail.
- **WiFi over PoE — Boss's call.** The wired option (Duo 3 PoE, 16MP) was considered and declined: "I don't think I'd ever really do that [cable run]. WiFi is fine." Recorded so it is not relitigated.

## Verified specs (Horst web-confirmed 14-Jun-2026)
- Fixed dual-lens, **180° panoramic** via two stitched sensors — **no pan/tilt/zoom motor**.
- **4K** (8MP combined across the two sensors).
- **WiFi 6**, dual-band 2.4 / 5 GHz.
- **IP67** weatherproof (outdoor-rated).
- Built-in person & vehicle detection.
- **RTSP supported** — port **554**, standard Reolink path, e.g. `rtsp://<user>:<pass>@<IP>:554/h264Preview_01_main`.
- **Dual-lens exposes TWO RTSP channels** — `01` and `02`. Guardian must either ingest both as two stream entries or point at whichever lens covers the target zone.
- **ONVIF supported.**

## Why it slots into Farm Guardian
Guardian already ingests the existing Reolink PTZ (.88) over RTSP (port 554) — see the per-camera RTSP transport + substream plans (06-Apr-2026). The Duo 2 speaks the same RTSP/ONVIF, so it drops into the same ingest path. It will be a **`type=fixed` secondary cam** (snapshot/photo pipeline), **NOT** a PTZ and **NOT** a Stage-0 hardware-motion source — the .88 remains the only PTZ / Stage-0 coop-motion camera (see camera roster roles, 12-Jun-2026).

## WiFi reliability caveat (carry this forward)
A Guardian feed runs 24/7. Continuous RTSP over WiFi can drop/lag — worse the farther the cam is from the router, and Reolink is known to throttle the RTSP substream on WiFi models. PoE would have eliminated this; since we went WiFi, **mount it where the WiFi signal is strong** and watch for frame gaps after install. Prefer the **main** stream only if bandwidth holds; fall back to the substream if it stutters.

## When-it-arrives checklist
- [x] Package received + camera wired into Guardian as `duo2` on 2026-06-17. (The one-shot 17-Jun ~1:11 PM reminder cron fired and self-deleted; no live cron remains.)
- [ ] Mount, power on, join WiFi; confirm strong signal at the mount point.
- [ ] Give it a **static DHCP lease** on the 192.168.0.0/24 LAN (TP-Link Archer AX55, .1) so its IP doesn't wander.
- [ ] Set RTSP credentials in the cam; verify both channel URLs (`_01_main`, `_02_main`) play.
- [ ] Add to Guardian `config.json` cameras + the camera registry as `type=fixed` (one or both channels).
- [ ] Verify frames ingest — check `http://127.0.0.1:6530/api/cameras` shows it `online` + `is_live` with a fresh `last_frame_age`.
- [ ] (Optional) decide with the Boss whether to add an Instagram time-lapse reel lane for it (pattern: `daily_reel_runner.py` lanes).

## Related / cross-refs
- **Inflatable tube man (predator deterrent)** — Amazon B098PGSQS7, ordered 14-Jun-2026; **arrived + installed** (Boss-confirmed 2026-06-17). (Correction: earlier on 2026-06-17 I wrongly struck this as a "hallucination" when the Boss said "no blower part" — that was my error; the tube man is real and installed.) **OPEN ISSUE:** the TP-Link Kasa smart plug powering/controlling it has never worked right — needs diagnosis (Boss-flagged 2026-06-17).
- **MBA-cam brooder reel fix (same day):** the MacBook Air brooder cam's time-lapse reel lane had never posted — root cause was the capture **`enabled` flag being off**, so zero raw frames were stored and the reel never built. Fixed by Bubba sub-agent — see `CHANGELOG.md` **v2.41.3**.
- Fleet map: `farm-lan-map` (memory). Existing cams: s7-cam (.249, nesting box), usb-cam (on GWTC), gwtc (coop roof, **currently DOWN** — dominator-cam covering), mba-cam (brooder, just fixed), dominator-cam (.194), Reolink PTZ house-yard (.88).
- Camera node history: `12-Apr-2026-macbook-air-camera-node-plan.md`, `06-May-2026-dominator-camera-plan.md`, `06-Apr-2026-per-camera-rtsp-transport-plan.md`.
