# RTSP Sub-Stream & Stream Corruption Fix — Plan

**Author:** Claude Opus 4.6
**Date:** 08-April-2026
**Goal:** Fix massive RTSP decode errors on both cameras by switching to lighter streams and giving per-camera control over which stream profile is used.

---

## Problem

Both camera streams are producing severe decode errors:

- **Reolink (house-yard):** HEVC/H.265 4K main stream over WiFi+TCP. Errors: "Could not find ref with POC", "PPS id out of range", "cu_qp_delta out of range". The 4K HEVC stream is too bandwidth-heavy for WiFi — every dropped packet causes cascading decode failures.
- **Nesting-box S7:** H.264 errors: "corrupted macroblock", "cbp too large", "mb_type in P slice too large". Phone encoder struggling.
- **Both:** 762 "frame read hung >10s" reconnects, 6,704+ codec errors in one session. RTP sequence mismatches (bad cseq) = packet loss on WiFi.

## Root Cause

`discovery.py` line 196 always selects `profiles[0]` — the main (4K HEVC) ONVIF profile. There's no way to select the sub-stream.

Reolink ONVIF profiles:
- Profile 0 (`Preview_01_main`): 4K/5MP HEVC — current, too heavy
- Profile 1 (`Preview_01_sub`): ~640x360 H.264 — lighter, more resilient, sufficient for YOLO detection

---

## Scope

**In:**
- Add `rtsp_stream` config field per camera: `"main"` (default) or `"sub"`
- Update `discovery.py` to select the correct ONVIF profile based on `rtsp_stream`
- Allow `rtsp_url_override` on any camera (currently only non-ONVIF cameras use it) — this lets you hardcode the sub-stream URL if needed
- Update config.json: switch house-yard to sub-stream
- Update docs & changelog

**Out:**
- Reolink web UI codec changes (manual step, out of scope for code)
- S7/nesting-box phone app settings (manual, and will be replaced by GWTC webcam per adjacent plan)
- Network infrastructure changes (WiFi channel, router QoS)

---

## Architecture

### Config change (config.json)

```json
{
  "name": "house-yard",
  "ip": "192.168.0.88",
  "port": 80,
  "username": "admin",
  "password": "...",
  "onvif_port": 8000,
  "type": "ptz",
  "rtsp_transport": "tcp",
  "rtsp_stream": "sub"
}
```

`rtsp_stream` values:
- `"main"` — Use ONVIF profile index 0 (default, backwards compatible)
- `"sub"` — Use ONVIF profile index 1

If `rtsp_url_override` is set, it takes priority over both ONVIF discovery and `rtsp_stream`.

### Code change (discovery.py)

In `_get_rtsp_url()`, accept a `stream_preference` parameter. Instead of hardcoded `profiles[0]`:
- If `"sub"` and `len(profiles) >= 2`: use `profiles[1]`
- If `"sub"` but only one profile exists: warn and fall back to `profiles[0]`
- If `"main"` or unset: use `profiles[0]` (current behavior)

The stream preference is passed from `_probe_camera()` which reads it from the camera config.

### Why sub-stream is sufficient

- YOLO detection runs at 1920px downscaled anyway (`capture.py` line 26: `_TARGET_WIDTH = 1920`)
- Sub-stream is ~640x360 H.264 — actually gets *upscaled* or used as-is
- For the house-yard camera, detection accuracy on 640px is acceptable (birds/cats/dogs are large in frame at typical distances)
- Dashboard feed only needs ~640px for the half-width panel
- H.264 sub-stream is dramatically more resilient over WiFi than 4K HEVC

---

## TODOs

1. **Update `discovery.py`**
   - Pass `rtsp_stream` from camera config through `_probe_camera()` to `_get_rtsp_url()`
   - `_get_rtsp_url()` selects profile based on preference
   - Log which profile/stream was selected
   - Also: allow `rtsp_url_override` to work for ONVIF cameras (move the override check earlier in `_probe_camera`)

2. **Update `config.json` and `config.example.json`**
   - Add `rtsp_stream: "sub"` to house-yard camera
   - Document the field in config.example.json

3. **Update `CHANGELOG.md`**

4. **Update `CLAUDE.md`** — note the sub-stream switch

5. **Verify** — restart Guardian, confirm house-yard connects to sub-stream, check for reduced decode errors

---

## Docs / Changelog Touchpoints

- `CHANGELOG.md` — new entry for rtsp_stream support + sub-stream switch
- `CLAUDE.md` — update Reolink description to note sub-stream usage
- `config.example.json` — add `rtsp_stream` field with comment
