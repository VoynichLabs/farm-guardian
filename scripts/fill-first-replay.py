#!/usr/bin/env python3
# Author: Claude Haiku 5.5 Medium
# Date: 05-November-2026
# PURPOSE: Replay the fill-first posting options in
#          docs/05-Nov-2026-s7-fill-first-posting-plan.md over every strong s7-cam frame
#          archived since 12-Aug-2026, era by era, and answer three questions per era:
#            1. How many frames does the rule in force today post (the gem gate's other
#               clauses plus the score floor in force then)?
#            2. Choose a fill threshold so a fill-first rule posts about the same number
#               (volume parity). How far does the set move: how many of today's frames
#               does it keep, how many new frames would it add, how many would it drop?
#            3. Among frames that were actually posted and carry a reaction label, what
#               happens to the reacted share in the kept group and the dropped group?
#          Candidate 1 gates on measured fill alone (the other gate clauses still apply).
#          Candidate 2 gates on measured fill plus a lower score floor (LOWER_FLOOR, an
#          example value only; the Boss chooses the real one).
#          Fill follows the live scorer: the detector's measured coverage where it is stored
#          or measured here, otherwise the VLM's coverage figure, because the live scorer
#          falls back to that when a frame has no confident box or is too dark.
#          The other gate clauses are the live gem_poster.should_post, called with the score
#          floor lifted, so no gate rule is re-typed here. Detector results are cached on the
#          SSD, so an interrupted run resumes. Reads guardian.db read-only and writes only to
#          the SSD folder. Nothing here changes the pipeline, its config or its posts.
#          Rollout days (12-Aug and 19-Sep, when the floor and the subject-size gate changed
#          mid-day; 2-Oct, when measured fill went live mid-day) are kept in the frame table but
#          left out of the era analysis. The subject-size gate is blanked for the A era, where it
#          was not yet live. Option A (fill axis raised) is re-run through the live scorer for
#          the fill-term eras only, after checking that the recomputation reproduces stored scores.
#          Run with the live checkout's interpreter (the worktree has no venv):
#          /Users/macmini/GitHub/farm-guardian/venv/bin/python scripts/fill-first-replay.py
#          Add --measure-limit N to measure only N frames (a timing check; writes no summary).
# SRP/DRY check: Pass — the detector is reused from scripts/s7-measured-fill.py (loaded as a
#                module, not copied) and the gate from tools/pipeline/gem_poster.py. This file
#                only builds the frame table, resolves fill, applies candidate rules and counts.
#                Verified 05-Nov-2026: no existing replay of these rules was found.

import argparse
import csv
import importlib.util
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)
from tools.pipeline import gem_poster  # noqa: E402  live gate; its other clauses are reused as-is
from tools.pipeline import orchestrator as orch  # noqa: E402  live scorer; re-run for option A

# Same database and weights as the rest of the measurement tooling. Read-only use only.
DB_PATH = "/Users/macmini/GitHub/farm-guardian/data/guardian.db"
BASE_DIR = "/Volumes/Team 4TB/kaggle/s7-reaction-20261105"
LABELLED_CSV = os.path.join(BASE_DIR, "s7_posts_labelled.csv")
FILL_LINKED_CSV = os.path.join(BASE_DIR, "fill_measured.csv")
FILL_REPLAY_CSV = os.path.join(BASE_DIR, "fill_replay_measured.csv")
FRAMES_OUT_CSV = os.path.join(BASE_DIR, "fill_first_replay_frames.csv")
SUMMARY_OUT = os.path.join(BASE_DIR, "fill_first_replay_summary.txt")
MEASURE_SCRIPT = os.path.join(REPO, "scripts", "s7-measured-fill.py")
MEASURE_FIELDS = ["frame_row_id", "measured_fill_pct", "status"]
PROGRESS_EVERY = 500

UNIVERSE_START = "2026-08-12"
# Era boundaries by frame capture date (UTC). 12-Aug (floor 65 to 80), 19-Sep (floor 80 to 82,
# subject-size gate switched on) and 2-Oct (measured fill switched on, 10:43 to 18:22 on the day)
# were rollout days: frames from those days mix old and new scoring, so no single moment separates
# them. Their frames stay in the frame table with era None and are left out of the era analysis.
ERAS = [
    ("A_no_fill", "2026-08-13", "2026-09-19"),
    ("B1_vlm_fill", "2026-09-20", "2026-10-02"),
    ("B2_measured_fill", "2026-10-03", "9999-12-31"),
]
# The subject-size gate for s7-cam (largest_subject_pct under 12) was not live before 19-Sep.
# The A-era replay therefore must not apply it, or it rejects posts that were really posted.
LARGEST_GATE_START = "2026-09-20"
# Score floor in force in each era: 80 from 12-Aug, 82 from 19-Sep (CHANGELOG v2.73.0).
ERA_FLOOR = {"A_no_fill": 80, "B1_vlm_fill": 82, "B2_measured_fill": 82}
# Example lower floor for candidate 2. Not a recommendation; the Boss picks the real value.
LOWER_FLOOR = 70
# Example fill-axis maximum for option A (today's live value is 25). Not a recommendation.
OPTION_A_FILL_POINTS = 40
# Reaction comparisons use confidently linked posts at least seven days old, as in the findings doc.
LABEL_CUTOFF_DATE = "2026-10-29"
CONFIDENT_LINKS = {"exact_stamp", "time_score_unique", "caption_tiebreak"}


def era_of(day):
    """Name of the scoring era a capture date falls in, or None if outside every era."""
    for name, start, end in ERAS:
        if start <= day < end:
            return name
    return None


def load_universe(conn):
    """Every strong s7-cam frame captured since UNIVERSE_START, with its stored metadata."""
    query = (
        "SELECT id, ts, image_path, exposure_p50, vlm_json FROM image_archive "
        "WHERE camera_id = 's7-cam' AND share_worth = 'strong' AND ts >= ? ORDER BY id"
    )
    frames = []
    for row_id, ts, image_path, exposure, vlm_raw in conn.execute(query, (UNIVERSE_START,)):
        day = ts[:10]
        frames.append({
            "id": row_id,
            "ts": ts,
            "day": day,
            "era": era_of(day),
            "image_path": image_path,
            "exposure": exposure,
            "meta": json.loads(vlm_raw or "{}"),
        })
    return frames


def read_fill_csv(path):
    """Detector results already on disk: frame id -> (measured fill or None, status)."""
    results = {}
    if not os.path.exists(path):
        return results
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            # A row cut short by a hard kill mid-write is missing fields (DictReader fills them with
            # None). Skip it, so the frame is measured again instead of trusting a half-written value.
            if None in row.values() or None in row:
                continue
            value = row["measured_fill_pct"]
            results[int(row["frame_row_id"])] = (float(value) if value else None, row["status"])
    return results


def resolve_measured(frame, cache):
    """Measured fill for one frame: the stored live value, a cached detector run, or None."""
    stored = frame["meta"].get("measured_coverage_pct")
    if isinstance(stored, int) and not isinstance(stored, bool):
        return float(stored)
    cached = cache.get(frame["id"])
    if cached is not None and cached[0] is not None:
        return cached[0]
    return None


def live_fill(measured, meta):
    """Fill as the live scorer reads it: measured when it exists, otherwise the VLM's figure."""
    if measured is not None:
        return measured
    vlm = meta.get("subject_coverage_pct")
    if isinstance(vlm, int) and not isinstance(vlm, bool):
        return float(vlm)
    return 0.0


def load_measure_module():
    """Import scripts/s7-measured-fill.py so its measure() is reused, not copied."""
    spec = importlib.util.spec_from_file_location("s7_measured_fill", MEASURE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def measure_missing(frames, cache, limit):
    """Run the detector on frames that have neither a stored nor a cached measurement.

    Resumable: each result is appended to FILL_REPLAY_CSV as it is produced.
    """
    missing = [f for f in frames if resolve_measured(f, cache) is None and f["id"] not in cache]
    todo = missing if limit is None else missing[:limit]
    print(f"frames in universe: {len(frames)}, need detector: {len(missing)}, this run: {len(todo)}",
          flush=True)
    if not todo:
        return
    module = load_measure_module()
    detector = module.presence.PresenceDetector(
        model_path=module.MODEL_PATH, conf=module.presence.DEFAULT_CONF)
    write_header = not os.path.exists(FILL_REPLAY_CSV) or os.path.getsize(FILL_REPLAY_CSV) == 0
    started = time.time()
    with open(FILL_REPLAY_CSV, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=MEASURE_FIELDS)
        if write_header:
            writer.writeheader()
        for index, frame in enumerate(todo, start=1):
            fill, status = module.measure(detector, frame["image_path"], frame["exposure"])
            writer.writerow({"frame_row_id": frame["id"],
                             "measured_fill_pct": "" if fill is None else fill,
                             "status": status})
            cache[frame["id"]] = (fill, status)
            if index % PROGRESS_EVERY == 0:
                fh.flush()
                rate = index / max(time.time() - started, 1e-9)
                print(f"measured {index}/{len(todo)} ({rate:.1f} frames/s)", flush=True)
    print(f"measured {len(todo)} frames in {time.time() - started:.0f}s", flush=True)


def other_clauses_pass(meta):
    """The live gem gate with its score floor lifted: every other clause, unchanged.

    gem_poster reads the floor from a module global at call time, so the replay sets it
    to zero for the call and restores it straight after.
    """
    saved = gem_poster._MIN_OVERALL_SCORE
    gem_poster._MIN_OVERALL_SCORE = 0
    try:
        return bool(gem_poster.should_post(meta, "strong", "s7-cam"))
    finally:
        gem_poster._MIN_OVERALL_SCORE = saved


def era_clauses_pass(frame):
    """Other gate clauses as they stood on the frame's own day.

    Before LARGEST_GATE_START the subject-size clause did not exist, so it is blanked for those
    frames (a missing value passes that clause). Every other clause is today's, unchanged.
    """
    meta = frame["meta"]
    if frame["day"] < LARGEST_GATE_START:
        meta = dict(meta)
        meta["largest_subject_pct"] = None
    return other_clauses_pass(meta)


def candidate_pass(frame, threshold, lower_floor):
    """Fill-first gate: other clauses, measured fill at or above the threshold, and optionally a score floor."""
    if not frame["other_pass"] or frame["fill"] < threshold:
        return False
    if lower_floor is None:
        return True
    score = frame["composite"]
    return isinstance(score, int) and score >= lower_floor


def parity_threshold(era_frames, target, lower_floor):
    """Fill threshold (0-100) whose candidate count is closest to the target count."""
    best_threshold, best_gap = 0, None
    for threshold in range(0, 101):
        count = sum(1 for f in era_frames if candidate_pass(f, threshold, lower_floor))
        gap = abs(count - target)
        if best_gap is None or gap < best_gap:
            best_threshold, best_gap = threshold, gap
    return best_threshold


def load_labels(frame_ids):
    """Confidently linked, old-enough posts that sit inside the universe: frame id -> reacted flag.

    One label per frame. A frame that was posted twice counts as reacted if either post drew a
    reaction, so a duplicate can never silently overwrite a reacted post with an unreacted one.
    """
    labels = {}
    skipped_young = 0
    with open(LABELLED_CSV, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["link_method"] not in CONFIDENT_LINKS or not row["frame_row_id"]:
                continue
            if row["posted_at"][:10] > LABEL_CUTOFF_DATE:
                skipped_young += 1
                continue
            frame_id = int(row["frame_row_id"])
            if frame_id in frame_ids:
                labels[frame_id] = labels.get(frame_id, False) or (row["reacted"] == "1")
    return labels, skipped_young


def share(numerator, denominator):
    return f"{100.0 * numerator / denominator:.1f}% ({numerator} of {denominator})" if denominator else "n/a"


def gap_text(hits_kept, n_kept, hits_dropped, n_dropped):
    """Kept minus dropped reacted share, in points, with an approximate 95% interval (normal approximation).

    The interval is wide when the dropped group is small, which is exactly when a reading should not be trusted.
    """
    if not n_kept or not n_dropped:
        return "n/a"
    p_kept = hits_kept / n_kept
    p_dropped = hits_dropped / n_dropped
    se = math.sqrt(p_kept * (1 - p_kept) / n_kept + p_dropped * (1 - p_dropped) / n_dropped)
    return f"{100.0 * (p_kept - p_dropped):+.1f} points, approximate 95% interval plus or minus {196.0 * se:.1f}"


def evaluate(frames, labels, thresholds_out):
    """Set the candidate flags on every frame and return the per-era summary lines."""
    lines = []
    for name, _, _ in ERAS:
        era_frames = [f for f in frames if f["era"] == name]
        if not era_frames:
            continue
        floor = ERA_FLOOR[name]
        for f in era_frames:
            score = f["composite"]
            f["current_pass"] = f["other_pass"] and isinstance(score, int) and score >= floor
        target = sum(1 for f in era_frames if f["current_pass"])
        lines.append(f"== {name}: frames {len(era_frames)}, floor {floor}, "
                     f"today's rule posts {target}")
        for label, lower in (("candidate 1 (fill only)", None),
                             (f"candidate 2 (fill + score >= {LOWER_FLOOR})", LOWER_FLOOR)):
            key = "cand1" if lower is None else "cand2"
            threshold = parity_threshold(era_frames, target, lower)
            thresholds_out[(name, key)] = threshold
            for f in era_frames:
                f[key] = candidate_pass(f, threshold, lower)
            kept = sum(1 for f in era_frames if f["current_pass"] and f[key])
            added = sum(1 for f in era_frames if f[key] and not f["current_pass"])
            dropped = sum(1 for f in era_frames if f["current_pass"] and not f[key])
            lines.append(f"  {label}: fill threshold {threshold}, posts "
                         f"{sum(1 for f in era_frames if f[key])}; of today's posts it keeps {kept}, "
                         f"drops {dropped}, adds {added} new frames")
            posted = [f for f in era_frames if f["id"] in labels]
            kept_posted = [f for f in posted if f[key]]
            dropped_posted = [f for f in posted if not f[key]]
            hits_kept = sum(labels[f["id"]] for f in kept_posted)
            hits_dropped = sum(labels[f["id"]] for f in dropped_posted)
            lines.append(f"    labelled posts kept: {share(hits_kept, len(kept_posted))} reacted; "
                         f"labelled posts dropped: {share(hits_dropped, len(dropped_posted))} reacted")
            lines.append(f"    kept minus dropped: {gap_text(hits_kept, len(kept_posted), hits_dropped, len(dropped_posted))}")
        posted = [f for f in era_frames if f["id"] in labels]
        unexplained = sum(1 for f in posted if not f["current_pass"])
        lines.append(f"  labelled posts: {len(posted)}, reacted {share(sum(labels[f['id']] for f in posted), len(posted))}; "
                     f"posts today's replay would not pass: {unexplained} (should be near zero)")
    return lines


def recomputed_score(meta, measured, fill_points=None):
    """Re-run the live score on a copy of a stored row, optionally with another fill weight.

    The measured fill is injected the way the live path writes it (rounded to a whole percent);
    with no measurement the VLM figure is used, as the live path does. The focus cap is re-applied
    because the live path applies it after scoring. fill_points, when given, replaces the fill
    axis maximum for this call only.
    """
    copy = dict(meta)
    if measured is not None:
        copy["measured_coverage_pct"] = int(round(measured))
    else:
        copy.pop("measured_coverage_pct", None)
    saved = orch._SUBJECT_MAX_POINTS
    if fill_points is not None:
        orch._SUBJECT_MAX_POINTS = fill_points
    try:
        orch._compute_overall_score(copy)
    finally:
        orch._SUBJECT_MAX_POINTS = saved
    orch._cap_score_for_focus(copy)
    return copy.get("overall_score")


def option_a_lines(frames, labels, cache):
    """Option A (re-weight): the fill axis maximum raised, floor set for volume parity.

    Run on the fill-term eras only (B1, B2). The A era had no fill term, so the candidate would
    add a term there rather than re-weight one. The baseline is first checked against the stored
    scores: recomputing today's score from the stored parts must reproduce the stored number.
    """
    lines = [f"Option A (fill axis maximum raised from {orch._SUBJECT_MAX_POINTS} to "
             f"{OPTION_A_FILL_POINTS}, other axes unchanged; floor set for parity; fill-term eras only)"]
    for name in ("B1_vlm_fill", "B2_measured_fill"):
        era_frames = [f for f in frames if f["era"] == name]
        # B1 predates the measured fill, so its stored score used the VLM figure; B2 used measured.
        for f in era_frames:
            measured = resolve_measured(f, cache) if name == "B2_measured_fill" else None
            f["baseline_recomputed"] = recomputed_score(f["meta"], measured)
            # Same fill source on both sides of the comparison: detector for B2 (the stored baseline
            # uses it), VLM figure for B1 (the stored baseline uses that). Mixing them would change
            # two things at once and read as a re-weight.
            candidate_measured = resolve_measured(f, cache) if name == "B2_measured_fill" else None
            f["composite_a"] = recomputed_score(f["meta"], candidate_measured,
                                                fill_points=OPTION_A_FILL_POINTS)
        matched = sum(1 for f in era_frames if f["baseline_recomputed"] == f["composite"])
        lines.append(f"  {name}: recomputed baseline matches stored score on {matched} of {len(era_frames)} frames")
        floor = ERA_FLOOR[name]
        for f in era_frames:
            f["current_pass"] = f["other_pass"] and isinstance(f["composite"], int) and f["composite"] >= floor
        target = sum(1 for f in era_frames if f["current_pass"])

        def option_a_pass(frame, threshold):
            score = frame["composite_a"]
            return frame["other_pass"] and isinstance(score, int) and score >= threshold

        best_threshold, best_gap = 0, None
        for threshold in range(0, 101):
            gap = abs(sum(1 for f in era_frames if option_a_pass(f, threshold)) - target)
            if best_gap is None or gap < best_gap:
                best_threshold, best_gap = threshold, gap
        for f in era_frames:
            f["optA"] = option_a_pass(f, best_threshold)
        kept = sum(1 for f in era_frames if f["current_pass"] and f["optA"])
        added = sum(1 for f in era_frames if f["optA"] and not f["current_pass"])
        dropped = sum(1 for f in era_frames if f["current_pass"] and not f["optA"])
        posted = [f for f in era_frames if f["id"] in labels]
        kept_posted = [f for f in posted if f["optA"]]
        dropped_posted = [f for f in posted if not f["optA"]]
        lines.append(f"  {name}: floor {best_threshold}, posts {sum(1 for f in era_frames if f['optA'])} "
                     f"(today {target}); of today's posts it keeps {kept}, drops {dropped}, adds {added} new frames")
        hits_kept = sum(labels[f["id"]] for f in kept_posted)
        hits_dropped = sum(labels[f["id"]] for f in dropped_posted)
        lines.append(f"    labelled posts kept: {share(hits_kept, len(kept_posted))} reacted; "
                     f"labelled posts dropped: {share(hits_dropped, len(dropped_posted))} reacted")
        lines.append(f"    kept minus dropped: {gap_text(hits_kept, len(kept_posted), hits_dropped, len(dropped_posted))}")
    return lines


def write_frames(frames, path):
    fields = ["frame_id", "ts", "era", "composite", "fill", "other_pass", "current_pass", "cand1", "cand2"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for f in frames:
            writer.writerow({
                "frame_id": f["id"], "ts": f["ts"], "era": f["era"], "composite": f["composite"],
                "fill": round(f["fill"], 2), "other_pass": f["other_pass"],
                # Rollout-day frames are never evaluated, so their flags stay blank.
                "current_pass": f.get("current_pass", ""), "cand1": f.get("cand1", ""),
                "cand2": f.get("cand2", ""),
            })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--measure-limit", type=int, default=None,
                        help="measure only N frames, as a timing check; writes no summary")
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        frames = load_universe(conn)
    finally:
        conn.close()

    # Both detector caches are read: the linked-frame run and any earlier replay run.
    cache = read_fill_csv(FILL_LINKED_CSV)
    cache.update(read_fill_csv(FILL_REPLAY_CSV))
    measure_missing(frames, cache, args.measure_limit)
    if args.measure_limit is not None:
        return

    fallback = Counter()
    for f in frames:
        measured = resolve_measured(f, cache)
        f["fill"] = live_fill(measured, f["meta"])
        f["composite"] = f["meta"].get("overall_score")
        f["other_pass"] = era_clauses_pass(f)
        if measured is None:
            fallback[f["era"] or "rollout"] += 1
    labels, skipped_young = load_labels({f["id"] for f in frames})

    thresholds = {}
    rollout_frames = sum(1 for f in frames if f["era"] is None)
    lines = [f"universe: {len(frames)} strong s7-cam frames since {UNIVERSE_START}; "
             f"rollout-day frames left out of the era analysis: {rollout_frames}",
             f"frames that use the VLM fallback fill (no confident box or too dark), by era: {dict(fallback)}",
             f"labelled posts used (confident, posted on or before {LABEL_CUTOFF_DATE}): {len(labels)}; "
             f"skipped as too recent: {skipped_young}"]
    lines += evaluate(frames, labels, thresholds)
    lines += option_a_lines(frames, labels, cache)
    write_frames(frames, FRAMES_OUT_CSV)
    text = "\n".join(lines) + "\n"
    with open(SUMMARY_OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)


if __name__ == "__main__":
    main()
