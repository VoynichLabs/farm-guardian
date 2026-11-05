#!/usr/bin/env python3
# Author: Claude Haiku 5.5 Medium
# Date: 05-Nov-2026
# PURPOSE: Measure the frame fill the score's fill axis uses (the union of confident animal
#          boxes, as a percent of the frame) for every S7 frame linked in the labelled table,
#          so the fill-versus-reaction question can be asked across all scoring eras, not
#          only the frames the live pipeline measured from October on.
#
#          Reuses the pipeline's own detector: tools/pipeline/presence.py PresenceDetector
#          and PresenceResult.confident_coverage_pct(). Nothing about the detector is copied,
#          so a change to the pipeline's definition changes this measurement too. The model
#          is the same yolov8s weights at the same conservative threshold, and frames the
#          pipeline would treat as too dark to judge are recorded as abstained, not zero.
#
#          Modes:
#            --validate N   re-measure N frames that already carry the live pipeline's
#                           measured value, and report how closely the two agree.
#                           Run this first. It shows whether stored JPEGs reproduce the
#                           live measurement.
#            (default)      measure every linked frame, resumably, writing one row per
#                           frame to fill_measured.csv on the SSD.
#
#          Writes only to the SSD folder and reads guardian.db read-only. It does not touch
#          the pipeline, its config, or the live archive.
#          Integration: tools/pipeline/presence.py (detector), data/guardian.db (exposure and
#          stored measurements), the labelled CSV from scripts/s7-reaction-dataset.py.
#          Run with the project venv: venv/bin/python scripts/s7-measured-fill.py
# SRP/DRY check: Pass — the detector is imported, not reimplemented. This file only loops
#                over frames, calls the detector, and records the result.

import argparse
import csv
import importlib
import os
import sqlite3
import statistics
import sys
import time

import cv2

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)
presence = importlib.import_module("tools.pipeline.presence")

# Weights live only in the main checkout (gitignored), so the worktree cannot resolve them.
MODEL_PATH = "/Users/macmini/GitHub/farm-guardian/yolov8s.pt"
DB_PATH = "/Users/macmini/GitHub/farm-guardian/data/guardian.db"
DATA_DIR = "/Users/macmini/GitHub/farm-guardian/data"
BASE_DIR = "/Volumes/Team 4TB/kaggle/s7-reaction-20261105"
LABELLED_CSV = os.path.join(BASE_DIR, "s7_posts_labelled.csv")
OUT_CSV = os.path.join(BASE_DIR, "fill_measured.csv")
OUT_FIELDS = ["frame_row_id", "measured_fill_pct", "status"]
PROGRESS_EVERY = 500


def load_frames():
    """Distinct linked frames: (row_id, image path relative to the data folder)."""
    frames = {}
    with open(LABELLED_CSV, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["frame_row_id"]:
                frames.setdefault(int(row["frame_row_id"]), row["image_path"])
    return frames


def load_db_fields(row_ids):
    """Exposure and any stored live measurement for each frame, read-only."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    out = {}
    try:
        ids = list(row_ids)
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" * len(chunk))
            query = (
                "SELECT id, exposure_p50, json_extract(vlm_json,'$.measured_coverage_pct') "
                f"FROM image_archive WHERE id IN ({marks})"
            )
            for row_id, exposure, stored in conn.execute(query, chunk):
                out[row_id] = (exposure, stored)
    finally:
        conn.close()
    return out


def measure(detector, image_path, exposure):
    """Return (measured_fill_pct or None, status) for one frame, using the pipeline's rules."""
    full_path = os.path.join(DATA_DIR, image_path)
    image = cv2.imread(full_path)
    if image is None:
        return None, "unreadable"
    result = detector.detect(image, exposure_p50=exposure, daylight_only=True)
    if result.abstained:
        return None, f"abstained:{result.reason.split(' ')[0]}"
    fill = result.confident_coverage_pct()
    if fill is None:
        return None, "no_confident_box"
    return round(float(fill), 2), "ok"


def run_validation(frames, db_fields, detector, sample_size):
    """Re-measure frames that carry the live pipeline's stored measurement and compare."""
    stored_ids = [rid for rid in frames if db_fields.get(rid, (None, None))[1] is not None]
    stored_ids = stored_ids[:sample_size]
    diffs = []
    agree = 0
    skipped = 0
    for rid in stored_ids:
        exposure, stored = db_fields[rid]
        fill, status = measure(detector, frames[rid], exposure)
        if fill is None:
            skipped += 1
            continue
        diff = abs(fill - float(stored))
        diffs.append(diff)
        if diff <= 2.0:
            agree += 1
    print(f"validation: frames with a stored live value: {len(stored_ids)}", flush=True)
    print(f"  re-measured: {len(diffs)}, not re-measurable: {skipped}", flush=True)
    if diffs:
        print(f"  median absolute difference (points): {statistics.median(diffs):.2f}", flush=True)
        print(f"  within two points: {agree} of {len(diffs)}", flush=True)


def run_full(frames, db_fields, detector):
    """Measure every linked frame, resuming from any rows already written."""
    os.makedirs(BASE_DIR, exist_ok=True)
    done = set()
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV, encoding="utf-8") as fh:
            done = {int(row["frame_row_id"]) for row in csv.DictReader(fh)}
    pending = [rid for rid in frames if rid not in done]
    print(f"frames total: {len(frames)}, already measured: {len(done)}, to do: {len(pending)}", flush=True)
    write_header = not os.path.exists(OUT_CSV) or os.path.getsize(OUT_CSV) == 0
    started = time.time()
    with open(OUT_CSV, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUT_FIELDS)
        if write_header:
            writer.writeheader()
        for index, rid in enumerate(pending, start=1):
            exposure = db_fields.get(rid, (None, None))[0]
            fill, status = measure(detector, frames[rid], exposure)
            writer.writerow({"frame_row_id": rid, "measured_fill_pct": "" if fill is None else fill, "status": status})
            if index % PROGRESS_EVERY == 0:
                fh.flush()
                rate = index / max(time.time() - started, 1e-9)
                print(f"progress: {index}/{len(pending)} ({rate:.1f} frames/s)", flush=True)
    print(f"done: {len(pending)} frames written to {OUT_CSV}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", type=int, default=0, help="re-measure N stored frames and compare")
    args = parser.parse_args()

    frames = load_frames()
    db_fields = load_db_fields(frames.keys())
    detector = presence.PresenceDetector(model_path=MODEL_PATH, conf=presence.DEFAULT_CONF)
    if args.validate:
        run_validation(frames, db_fields, detector, args.validate)
    else:
        run_full(frames, db_fields, detector)


if __name__ == "__main__":
    main()
