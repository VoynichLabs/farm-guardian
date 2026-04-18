# 17-Apr-2026 — Tighten the pre-VLM gate (motion + exposure)

**Author:** Claude Opus 4.7 (1M context)
**Status:** Approved by Boss 2026-04-17, implementing items #1 + #2 only.

## Problem

Pipeline VLM inference runs ~43 s/call on Gemma-4-31B, single-flight under a module lock. 24-hour data (2026-04-17):

- **house-yard**: 115 frames analyzed, 107 (93%) `share_worth=skip`, activity `none-visible`. Reolink is paying 43 s of VLM every 10 minutes to confirm an empty yard.
- Across all cameras: 1322 frames analyzed, 905 (68%) returned `skip`.

Current pre-VLM gate (`quality_gate.py`) rejects only frames with luminance `std_dev < 5` — effectively only lens-cap / all-black / all-white frames. Blown-out, washed-out, and motion-less frames all sail through to the VLM.

## Scope

**In scope:**

1. **Frame-to-frame motion gate** on `house-yard` and `gwtc`. If the current frame is visually indistinguishable from the last one we captured for that camera, skip the VLM call entirely. Log it as `status=gated, stage=motion`.
2. **Exposure gate** on every camera. Reject frames where:
   - median luminance `p50 < 25` (near-black — night, lens covered)
   - median luminance `p50 > 230` (blown out — overexposed)
   - `std_dev < 15` (washed out — low contrast, no real content)

**Out of scope (Boss wants to discuss first):**

- Trimming the 14-field schema (item #3 from the report).
- Changing `context_length` from 8192 (item #4).
- Changing house-yard cadence (item #5).

## Architecture

`tools/pipeline/quality_gate.py` gets:

- A new `passes_exposure_gate(img, cfg)` helper with three thresholds, configurable via `config.json`. Returns `(ok, metrics, reason)`.
- The existing `passes_trivial_gate` stays as-is. The orchestrator calls both (exposure after trivial) so the log records which specific gate rejected a frame.

`tools/pipeline/orchestrator.py` gets a per-camera motion gate:

- New `MotionGate` class (module-scoped, lives in `quality_gate.py`) that holds a small downscaled grayscale thumbnail of the last frame per camera.
- API: `motion_gate.accept(camera_id, img) -> (bool, delta_metric)`. First frame for a camera always accepts. Subsequent frames are accepted if mean absolute pixel delta >= threshold (config: `motion_delta_threshold`, default 3.0 on 0-255 scale).
- Motion gate is per-camera and only *enabled* for cameras that list `"motion_gate": true` in their config block. Brooder cameras (`usb-cam`, `mba-cam`, `s7-cam`) leave it off — chicks move so continuously that frame-diff isn't a useful signal and we want the VLM on every frame. Outdoor/coop cameras (`house-yard`, `gwtc`) turn it on.
- Thumbnail size: 64x64 grayscale. Cheap (~1 ms), plenty of signal for yard-scale motion.

Orchestrator call order per cycle:

1. capture
2. decode
3. trivial gate (std_dev floor, existing)
4. **exposure gate (new)**
5. **motion gate (new, per-camera opt-in)**
6. VLM
7. store

Any gate failure short-circuits with `status=gated, stage=<name>, metrics=...`. No VLM call, no archive row (matches existing gate behavior).

## Config additions (`tools/pipeline/config.json`)

Top-level:
```json
"exposure_p50_floor": 25,
"exposure_p50_ceiling": 230,
"exposure_std_floor": 15.0,
"motion_delta_threshold": 3.0
```

Per-camera (house-yard, gwtc):
```json
"motion_gate": true
```

## TODOs

1. Add `passes_exposure_gate` + `MotionGate` to `quality_gate.py`.
2. Wire both into `orchestrator.run_cycle` between trivial gate and VLM.
3. Config: add new top-level thresholds; flip `motion_gate: true` on house-yard and gwtc.
4. File headers updated.
5. Reload `com.farmguardian.pipeline` LaunchAgent.
6. Verify via `/tmp/pipeline.err.log`: look for `stage=motion` and `stage=exposure` entries within the first 5 minutes. Confirm usb-cam and s7-cam still run every cycle (motion gate off).
7. 30-min sample: SQL count of new `image_archive` rows for house-yard should drop sharply; log count of `gated` cycles should rise.
8. CHANGELOG top entry (SemVer minor bump).

## Verification

```bash
# Before/after: how many house-yard VLM calls per hour
sqlite3 data/guardian.db "SELECT camera_id, strftime('%H', ts) AS hr, COUNT(*) \
  FROM image_archive WHERE created_at > datetime('now','-3 hours') \
  GROUP BY camera_id, hr;"

# Gate rejection counts in the log
grep -E 'stage=(motion|exposure|trivial_gate)' /tmp/pipeline.err.log | \
  awk '{print $NF}' | sort | uniq -c
```

Expected outcome: house-yard `image_archive` inserts drop from 6/hr to near-zero during quiet periods, VLM time freed up for brooder cameras, total VLM calls per hour drop ~40–60%.
