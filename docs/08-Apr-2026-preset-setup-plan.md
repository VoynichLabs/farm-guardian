# Preset Setup Plan — 08-Apr-2026

**Author:** Claude Opus 4.6 (remote session)
**Date:** 08-April-2026
**Status:** Ready for implementation

---

## Goal

Save 3–4 named PTZ presets on the Reolink E1 Outdoor Pro so any assistant (remote or local) can instantly recall camera positions without unreliable move/stop cycles.

## Scope

**In scope:**
- Save presets 0–2 at the three cardinal monitoring positions (house, yard, stable)
- Verify each preset by recalling it and taking a snapshot
- Document final pan/tilt values for each preset

**Out of scope:**
- Preset 3 "sky" (hawk sky-watch) — tilt readback is broken, needs separate investigation
- Zoom adjustments — camera stays at zoom 0
- Patrol integration — presets are independent of patrol

## Why

The camera has no absolute pan/tilt positioning (Reolink firmware limitation). The only reliable way to move to a known position is presets. Without presets, remote assistants must use move/stop bursts that overshoot at ~85°/second over the Cloudflare tunnel.

## Preset Assignments

| Preset ID | Name | Target Pan | What's There |
|-----------|------|------------|-------------|
| 0 | `house` | ~180° (raw ~3600) | House, chickens, coop, truck. Primary monitoring view. **Default position.** |
| 1 | `yard` | ~90° (raw ~1800) | Green hillside, fire pit, treeline |
| 2 | `stable` | ~270° (raw ~5400) | Old stable foundation, property edge, corn field neighbor |

## Procedure (for each preset)

1. **Check current position:**
   ```bash
   curl -s https://guardian.markbarney.net/api/v1/cameras/house-yard/position
   ```

2. **Nudge camera to target** using short move/stop bursts (0.3–0.5s at speed 5). Check position after each burst. Get within ±5° of target.

3. **Verify the view** — autofocus, wait 3 seconds, snapshot:
   ```bash
   curl -s -X POST https://guardian.markbarney.net/api/v1/cameras/house-yard/autofocus
   sleep 3
   curl -s --max-time 15 https://guardian.markbarney.net/api/v1/cameras/house-yard/snapshot \
     --output /tmp/snap_preset_N_verify.jpg
   ```
   Read the image and confirm it shows the expected scene (use the world model in `AGENTS_CAMERA.md`).

4. **Save the preset:**
   ```bash
   curl -s -X POST https://guardian.markbarney.net/api/v1/cameras/house-yard/preset/save \
     -H "Content-Type: application/json" -d '{"id": N, "name": "NAME"}'
   ```

5. **Verify recall** — move camera away, then recall the preset and re-snapshot:
   ```bash
   # Nudge away
   curl -s -X POST https://guardian.markbarney.net/api/v1/cameras/house-yard/ptz \
     -H "Content-Type: application/json" -d '{"action":"move","pan":1,"tilt":0,"speed":5}'
   sleep 0.3
   curl -s -X POST https://guardian.markbarney.net/api/v1/cameras/house-yard/ptz \
     -H "Content-Type: application/json" -d '{"action":"stop"}'

   # Recall preset
   curl -s -X POST https://guardian.markbarney.net/api/v1/cameras/house-yard/preset/goto \
     -H "Content-Type: application/json" -d '{"id": N}'
   sleep 2

   # Snapshot to verify
   curl -s -X POST https://guardian.markbarney.net/api/v1/cameras/house-yard/autofocus
   sleep 3
   curl -s --max-time 15 https://guardian.markbarney.net/api/v1/cameras/house-yard/snapshot \
     --output /tmp/snap_preset_N_recall_verify.jpg
   ```
   Compare with the original snapshot. Should be the same view.

6. **Log the result** in `/tmp/camera_observations.md` with the actual pan/tilt values saved.

## Order of Operations

1. Save preset 0 "house" first — it's the most important and the default home position
2. Save preset 1 "yard"
3. Save preset 2 "stable"
4. Run `GET /cameras/house-yard/presets` to confirm all three are saved on the camera
5. Update `AGENTS_CAMERA.md` world model table with the actual saved pan/tilt values

## Prerequisites

- Guardian must be running on the Mac Mini
- Patrol must be stopped (it will fight manual positioning)
- Cloudflare tunnel must be active

## Verification

```bash
# Final check — all presets saved
curl -s https://guardian.markbarney.net/api/v1/cameras/house-yard/presets
# Should return: {"camera_id":"house-yard","presets":{"house":0,"yard":1,"stable":2}}
```

## Docs/Changelog

- Update `AGENTS_CAMERA.md` world model table with actual preset values
- No CHANGELOG entry needed — this is configuration, not code
