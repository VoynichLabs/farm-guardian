# SAM 2 Integration Plan — Farm Guardian

**Author:** Bubba (Claude Sonnet 4.6)
**Date:** 25-Jul-2026
**Status:** Draft — for Dr. Opus review and implementation
**Purpose:** Add Meta's Segment Anything Model 2 to the detection pipeline to reduce false positives and produce cleaner alert crops for the LLM verifier.

---

## Background

Current pipeline:
```
Camera frame → YOLOv8 (detect.py) → [llm_verify.py, currently disabled] → alert
```

YOLOv8 produces a bounding box and confidence score. The LLM verifier (llm_verify.py) was designed to be a second-opinion check on borderline detections — but it shipped pointing at OpenRouter (remote, costly) instead of localhost:1234 (LM Studio, load-bearing local vision model). It is currently disabled in config.

The problem: YOLOv8's bounding box crops include a lot of background — grass, fence, sky — which dilutes the signal when the crop gets handed to a vision model for verification. SAM 2 can segment the actual object out of the box, giving the verifier a much tighter, cleaner subject image.

---

## What SAM 2 Is

Meta's Segment Anything Model 2 (released Aug 2024). Given an image and a point/box prompt, it produces a pixel-accurate mask of the object at that location. Works on still images and video. Runs on Apple Silicon via PyTorch MPS. Needs ~4–6 GB VRAM/unified memory per inference — well within the M4 Pro's 64 GB.

Repo: https://github.com/facebookresearch/segment-anything-2

---

## Recommended Integration: Post-Processor Before LLM Verify

```
Camera frame
  → YOLOv8 (detect.py)          # bounding box + class + confidence
  → SAM 2 (new: sam_segment.py) # pixel mask from bbox prompt
  → crop masked region           # tight subject, background zeroed
  → llm_verify.py (re-enabled)  # LM Studio at localhost:1234 — NOT OpenRouter
  → alert
```

SAM 2 runs only on frames that already passed the YOLO threshold — not on every frame. This keeps it out of the hot path for non-detections.

### Why not pre-process?

Pre-processing (segment everything, then classify segments) would be more expensive — SAM 2 would run on every frame regardless of whether YOLO sees anything. Post-processing limits SAM 2 to frames that already have a candidate detection, which on a typical day is a small fraction of total frames.

---

## What to Build

### 1. `sam_segment.py` — SAM 2 service wrapper

A new module, analogous to `llm_verify.py`. Responsibilities:
- Load SAM 2 model once at startup (singleton, MPS device)
- Accept a frame (numpy array) + bounding box → return a binary mask (numpy array, same shape as frame)
- Produce a masked crop: original pixel values inside the mask, zeroed outside (or white background, whichever the verifier prefers)
- Configurable model checkpoint (sam2_hiera_small vs sam2_hiera_large — small recommended first)
- Fail-open: if SAM 2 errors or times out, pass the raw YOLO crop to the verifier unchanged, same as today

### 2. Update `llm_verify.py` — point at localhost:1234

The existing file reached for OpenRouter. The fix (already planned) is:
- Remove the remote endpoint branch entirely
- Hard-code to `http://localhost:1234/v1/chat/completions`
- Use the LM Studio env var/config if one exists, but no fallback to remote
- Fail-open behavior stays: on any error, return True (don't suppress real alerts)

### 3. Config additions to `config.json`

```json
"sam2": {
  "enabled": true,
  "checkpoint": "sam2_hiera_small.pt",
  "device": "mps",
  "confidence_threshold": 0.5,
  "timeout_seconds": 3
},
"llm_verify": {
  "enabled": true,
  "endpoint": "http://localhost:1234/v1/chat/completions",
  "model": "...",
  "timeout_seconds": 8
}
```

### 4. Pipeline wiring in `guardian.py` or wherever detect → alert handoff happens

Read the actual detection-to-alert flow before touching anything. The integration point is between `DetectionResult` being produced and an alert being dispatched.

---

## Model Setup (one-time, before implementation)

```bash
cd ~/GitHub/farm-guardian
pip install git+https://github.com/facebookresearch/segment-anything-2.git
# Download checkpoint — small is recommended to start:
# sam2_hiera_small.pt (~180MB) from https://github.com/facebookresearch/segment-anything-2#model-checkpoints
```

Checkpoint should live in the project root alongside `yolov8n.pt` / `yolov8s.pt`.

---

## What NOT to Touch

- `detect.py` — YOLOv8 pipeline is working, don't restructure it
- LM Studio config or the model loaded at localhost:1234 — it is a load-bearing production dependency, per CLAUDE.md
- Any existing alert paths, Instagram/social pipelines, or dashboard API shapes
- `config.json` without a timestamped backup first

---

## Acceptance Criteria

1. SAM 2 loads on startup, logs its device (should say `mps`), and is confirmed via `/api/status` or equivalent
2. A test frame with a YOLO detection can be passed through SAM 2 and produce a masked crop (visual sanity check)
3. llm_verify.py hits localhost:1234 — confirmed via LM Studio request log, not just code inspection
4. End-to-end: a real predator detection fires → SAM 2 mask produced → LM Studio called → alert fires (or correctly suppressed)
5. Fail-open verified: kill LM Studio mid-run → alert still fires (no silent drop)
6. No OpenRouter calls whatsoever under any code path

---

## Open Questions for Dr. Opus

- Which LM Studio model is loaded at localhost:1234 right now? Need to know what vision model to prompt correctly in llm_verify.py.
- Should the masked crop zero out the background or use a white fill? Depends on what the vision model was trained on.
- Is guardian.py the right integration point or does the alert handoff happen elsewhere? Read `guardian.py` + `alerts.py` before deciding.
- Sam2 small checkpoint is ~180MB — confirm this is installable to the project root without hitting a size issue.
