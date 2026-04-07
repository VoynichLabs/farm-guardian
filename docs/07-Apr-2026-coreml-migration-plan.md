# CoreML Migration — Drop PyTorch from Guardian

**Date:** 07-Apr-2026
**Goal:** Replace PyTorch-based YOLOv8 inference with CoreML, eliminating the massive PyTorch import and cutting startup from 60+ seconds to near-instant.

## Problem

Guardian takes 60+ seconds to start because `from ultralytics import YOLO` pulls in PyTorch (~2GB of native libraries). On cold boot or after a crash, the API and camera feeds are unavailable the entire time. PyTorch is only used for YOLO inference — the GLM vision model runs separately via LM Studio.

## Solution

Export the YOLOv8 model to CoreML format (`.mlpackage`). The `ultralytics` library supports CoreML inference natively on Apple Silicon via the Neural Engine — no PyTorch import needed.

## Scope

**In:**
- One-time model export: `yolov8n.pt` → `yolov8n.mlpackage` (or whichever model size Guardian uses)
- Update `detect.py` to load the `.mlpackage` instead of `.pt`
- Verify detection output format is identical (bboxes, classes, confidence scores)
- Remove `torch` and `torchvision` from `requirements.txt` if no other module needs them
- Update CHANGELOG

**Out:**
- No changes to the vision refinement pipeline (GLM stays as-is)
- No changes to tracking, deterrence, alerts, or any other module
- No model retraining — same weights, different runtime

## How

### Step 1: Export the model (one-time)

```python
from ultralytics import YOLO
model = YOLO("yolov8n.pt")  # or whatever model file Guardian uses
model.export(format="coreml", nms=True)
# Creates yolov8n.mlpackage in the same directory
```

Run this once. Commit the `.mlpackage` to the repo (or document where it lives).

### Step 2: Update detect.py

Change the model load path:

```python
# Before
self._model = YOLO("yolov8n.pt")

# After
self._model = YOLO("yolov8n.mlpackage")
```

`ultralytics` auto-detects the format and uses CoreML inference. The prediction API is identical — `model.predict()` returns the same `Results` objects.

### Step 3: Check what imports PyTorch

```bash
grep -r "import torch" *.py
grep -r "from torch" *.py
```

If only `detect.py` (via ultralytics) uses PyTorch, and we're now loading CoreML, PyTorch can be removed from `requirements.txt`. If `ultralytics` itself still imports torch at module level even for CoreML, we may need to keep it — check this.

### Step 4: Verify

1. `python guardian.py` — should start in <5 seconds
2. Point camera at something — detections should appear in the dashboard
3. Check detection format matches what `tracker.py` and `deterrent.py` expect
4. Run for an hour, confirm no crashes or memory issues

## Key Files

- `detect.py` — model load and inference (the only file that touches YOLO directly)
- `requirements.txt` — remove `torch`/`torchvision` if safe
- `config.json` — check if model path is configurable there vs hardcoded in detect.py

## Notes

- CoreML runs on the M4 Pro's Neural Engine — should be faster than MPS for inference
- The `.mlpackage` file is ~10-20MB vs the .pt file. Commit it or gitignore and document the export step.
- `ultralytics` is still needed — it's the inference wrapper. Only `torch`/`torchvision` might go away.
- If `ultralytics` insists on importing torch even for CoreML models, the alternative is `coremltools` for direct inference without ultralytics — but try the simple path first.
