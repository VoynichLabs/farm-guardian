#!/usr/bin/env python3
# Author: Claude Sonnet 5.5 Medium
# Date: 07-November-2026 (first written 06-November-2026)
# PURPOSE: Build the Kaggle package for the S7 reaction dataset (plans:
#          docs/06-Nov-2026-kaggle-s7-dataset-publish-plan.md and
#          docs/07-Nov-2026-kaggle-s7-usability-and-honesty-plan.md) and verify it from disk.
#          Input : s7_posts_labelled.csv (built by scripts/s7-reaction-dataset.py) and
#                  fill_measured.csv (scripts/s7-measured-fill.py), both on the SSD, plus the
#                  archive pictures under /Volumes/Team 4TB/data/farm-guardian/, the archive's
#                  reaction counts (guardian.db, read-only) and the AI spot-check results saved
#                  under <SSD folder>/spot-check/.
#          Output: <SSD folder>/kaggle-package/ with labels.csv, caption_spot_check.csv,
#                  images/YYYY-MM/<frame_id>.jpg, README.md (the card), dataset-metadata.json
#                  (the file `kaggle datasets version` reads); and package_report.txt beside it.
#                  <SSD folder>/kaggle-meta/ holds what the Kaggle page needs but the version
#                  upload cannot carry: dataset-metadata.json in the `metadata --update` shape
#                  (private flag, long license name, sources, update frequency, file notes),
#                  provenance-sources.txt, provenance-methodology.txt and column-descriptions.json
#                  (column notes are typed into the page: the upload route does not store them).
#          07-Nov-2026 changes: reaction_people (distinct non-bot people who reacted, read from
#          the archive, never from Discord); caption_spot_check.csv and the honesty section of the
#          card; provenance text from docs/07-Nov-2026-kaggle-s7-dataset-provenance.md; file and
#          column descriptions; Kaggle column types limited to string/numeric/datetime.
#          What it does, in order:
#            1. keeps confidently linked posts made on or before the label cutoff (younger posts
#               have not had time to collect a reaction, so their zeros are not labels yet);
#            2. collapses to one row per frame (reacted if ANY post of that frame was reacted to);
#            3. drops the columns that would leak or mislead: link_method (every reacted row was
#               linked by the stamped message id, so it predicts the label perfectly), msg_id,
#               posted_at, and the two columns that are constant because of the posting gate;
#            4. adds measured_fill_pct, a rollout_day flag and a split by whole calendar days
#               shuffled within each scoring era (frames seconds apart are near-duplicates, so a
#               random split would inflate every score);
#            5. clone-copies the pictures (same volume, no extra space), scrubbing metadata only
#               if a file actually carries some;
#            6. renders the card (docs/06-Nov-2026-kaggle-s7-dataset-card.md) with the counts and
#               baseline tables computed from the shipped table, so the numbers cannot drift;
#            7. re-reads the finished package and fails loudly on any inconsistency.
#          Reads the archive and guardian data read-only. Never touches Discord, LM Studio, the
#          pipeline, or the internal disk (Boss directive: big data on /Volumes/Team 4TB).
# SRP/DRY check: Pass - reuses the two existing builders' CSV outputs instead of re-linking posts
#                or re-measuring fill; era dates and rollout days are the ones fill-first-replay.py
#                uses (kept in sync by the constants below, with the source named).
# Usage: /Users/macmini/GitHub/farm-guardian/venv/bin/python scripts/kaggle-s7-package.py

from __future__ import annotations

import csv
import json
import logging
import random
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

log = logging.getLogger("kaggle_s7_package")

REPO = Path(__file__).resolve().parent.parent
BASE_DIR = Path("/Volumes/Team 4TB/kaggle/s7-reaction-20261105")
PACKAGE_DIR = BASE_DIR / "kaggle-package"
REPORT_PATH = BASE_DIR / "package_report.txt"
LABELLED_CSV = BASE_DIR / "s7_posts_labelled.csv"
FILL_CSV = BASE_DIR / "fill_measured.csv"
IMAGE_ROOT = Path("/Volumes/Team 4TB/data/farm-guardian")
CARD_TEMPLATE = REPO / "docs" / "06-Nov-2026-kaggle-s7-dataset-card.md"
PROVENANCE_DOC = REPO / "docs" / "07-Nov-2026-kaggle-s7-dataset-provenance.md"
# data/ is gitignored, so the live archive lives only in the main checkout, not in this worktree
# (same path scripts/s7-reaction-dataset.py uses).
GUARDIAN_DB = Path("/Users/macmini/GitHub/farm-guardian/data/guardian.db")
SPOT_DIR = BASE_DIR / "spot-check"  # caption-check.json, orientation-check.json, sample-master.json
META_DIR = BASE_DIR / "kaggle-meta"

KAGGLE_OWNER = "markbarney"
KAGGLE_SLUG = "s7-chicken-photo-reactions"
KAGGLE_TITLE = "Chicken & Turkey Photos Labelled by Chat Reactions"
LICENSE_NAME = "CC-BY-4.0"  # how the card and `datasets version` spell it
# `datasets metadata --update` rejects the short spelling ("invalid license"); the page shows this one.
LICENSE_LONG_NAME = "Attribution 4.0 International (CC BY 4.0)"
UPDATE_FREQUENCY = "never"  # a frozen snapshot; the owner can change it on the page
# Spot-checked frames that show a person: 2522037 (a man's face, clearly), 1671839 (torso and legs) and
# 1464572 (a person bending behind the fence), all opened by hand on 07-Nov-2026. A floor: the readers
# were never asked to look for people, so two came from passing notes and one from a person detector.
N_PEOPLE_SEEN = 3
MAIN_VLM_MODEL = "qwen/qwen3-vl-4b"
# "birds" is not a Kaggle tag (the 06-Nov-2026 rehearsal upload said so); unknown tags are skipped.
KEYWORDS = ["animals", "image", "agriculture", "computer vision"]

# Posts are labelled once the reaction sync has had a week; younger posts' zeros are not labels.
# Same cutoff fill-first-replay.py uses (LABEL_CUTOFF_DATE).
LABEL_CUTOFF_DATE = "2026-10-29"
CONFIDENT_LINKS = {"exact_stamp", "time_score_unique", "caption_tiebreak"}
# Rollout days (capture date, UTC): the score formula changed mid-day, so frames from these days
# mix old and new scoring. 12-Aug, 19-Sep and 2-Oct are the days fill-first-replay.py leaves out of
# the era analysis. 7-Aug (lowest allowed score 70 -> 65) and 8-Aug (formula rewritten, v2.68.0)
# were added after the 06-Nov-2026 review showed the earliest era is not one regime.
ROLLOUT_DAYS = {"2026-08-07", "2026-08-08", "2026-08-12", "2026-09-19", "2026-10-02"}
# The source table calls the earliest era pre_floor_80, which reads backwards to a stranger.
ERA_RENAME = {"pre_floor_80": "early_older_rules"}
ERA_ORDER = ["early_older_rules", "A_no_fill", "B1_vlm_fill", "B2_measured_fill"]
# The detector's status words are internal; "too dark" is a gate label that does not always
# match how bright the picture looks, so the shipped words say what happened, not why.
FILL_STATUS_RENAME = {"ok": "ok", "no_confident_box": "no_bird_detected",
                      "abstained:too_dark_to_gate": "skipped_dark_gate"}
# Captions where the model echoed its own instructions, or slipped into another script.
# These are blanked (and counted on the card) rather than shipped.
LEAK_PATTERN = re.compile(r"BIRDCATRAZ:|\b\d{4}-\d{2}-\d{2} to\b|s7-cam", re.I)
FOREIGN_SCRIPT = re.compile(r"[\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024fЀ-ӿ؀-ۿḀ-ỿ　-鿿가-힯]")

SPLIT_SEED = 20261106
VAL_SHARE = 0.15
TEST_SHARE = 0.15
LOCAL_TZ = ZoneInfo("America/New_York")  # the farm's day, so an evening is not cut at UTC midnight

# Columns in the shipped table, in order. Anything not listed here is not shipped.
OUT_COLUMNS = [
    "frame_id", "image", "captured_at", "day", "scoring_era", "rollout_day", "split", "reacted", "reaction_people",
    "score", "composition", "scene", "lighting", "activity", "bird_count",
    "expression_score", "detail_score",
    "subject_coverage_pct", "largest_subject_pct", "measured_fill_pct", "fill_status", "caption",
]
# bird_face_visible is gone: it is 1 on every row (a consequence of the posting gate), so it is a
# constant dressed up as a feature. verify_package also fails on any constant shipped column.
# standout_bird: true on all but 10 of the 1,987 rows that have it, blank before 21 Sep 2026, so it only marks the date.
FORBIDDEN_COLUMNS = {"link_method", "msg_id", "posted_at", "share_worth", "image_quality",
                     "frame_row_id", "image_path", "bird_face_visible", "standout_bird"}
SCHEMA = [  # (name, type, description) for dataset-metadata.json; kept beside OUT_COLUMNS
    ("frame_id", "numeric", "Row number of the frame in the farm's archive; the join key. It rises with capture time, so use it to join, not as a feature."),
    ("image", "string", "Path of the picture relative to the dataset root, images/YYYY-MM/<frame_id>.jpg."),
    ("captured_at", "datetime", "When the phone captured the frame (UTC)."),
    ("day", "string", "US Eastern calendar date of capture (the farm's local day), YYYY-MM-DD. Group by this, not by the UTC date in captured_at: the suggested split uses these days."),
    ("scoring_era", "string", "Which scoring rules were live: early_older_rules, A_no_fill, B1_vlm_fill, B2_measured_fill. Never pool score or any vision-model column across eras. The vision-model prompt also changed on 6 August and 21 September, inside eras, so model columns can step on those days."),
    ("rollout_day", "numeric", "1 on days a scoring change went live part-way through the day (old and new rules mixed), else 0. Dated by the UTC capture date, so it can differ from day on a couple of frames."),
    ("split", "string", "Suggested split: train, val or test. Whole US Eastern calendar days, so no day is in two folds."),
    ("reacted", "numeric", "The label. 1 if at least one person reacted to the post of this frame, 0 if no reaction was recorded (not the same as a dislike)."),
    ("reaction_people", "numeric", "Part of the label, never an input: how many different people reacted to the post (0 to 3). Zero exactly when reacted is 0. Who reacted is not kept."),
    ("score", "numeric", "The 0-100 score printed on the post (visible to the person reacting). Computed by the farm's pipeline from the vision model's sub-scores and a fill term, not written by the model and not a human label."),
    ("composition", "string", "Vision model's framing label: portrait, group or wide. Unchecked guess."),
    ("scene", "string", "Vision model's scene label; mostly birdcatraz, the poultry enclosure. Not a reliable screen."),
    ("lighting", "string", "Vision model's lighting label; almost always natural-good."),
    ("activity", "string", "Vision model's activity label (none-visible, drinking, eating, ...). Unchecked guess."),
    ("bird_count", "numeric", "Vision model's bird count. In a spot check it was within one bird of the average of two AI readers' own counts for most frames."),
    ("expression_score", "numeric", "Vision model sub-score; observed range 10-30. Unchecked guess."),
    ("detail_score", "numeric", "Vision model sub-score; observed range 15-25."),
    ("subject_coverage_pct", "numeric", "Vision model's guess of how much of the frame the birds fill. Runs high next to measured_fill_pct."),
    ("largest_subject_pct", "numeric", "Vision model's guess of how much the largest bird fills."),
    ("measured_fill_pct", "numeric", "Detector-measured share of the frame the birds fill. Blank when fill_status is not ok."),
    ("fill_status", "string", "ok; no_bird_detected (the detector found no confident bird box); skipped_dark_gate (the detector's brightness gate skipped the frame)."),
    ("caption", "string", "Caption the vision model wrote for the post. Often wrong: a spot check by AI readers found most sampled captions made a claim the picture contradicts, most often that a bird looks at the camera. Early captions can be cut off mid-sentence, many repeat verbatim, a few are blank."),
]

# Kaggle accepts only string, numeric, boolean and datetime as column types; "integer" is not one of
# them (the 07-Nov-2026 rehearsal), so whole numbers are declared numeric above.

# caption_spot_check.csv: the AI spot check (frames sampled 40 per era), one row per checked frame.
SPOT_COLUMNS = ["frame_id", "caption_claims_eye_contact", "caption_eye_contact_wrong", "caption_species_wrong",
                "caption_other_claim_wrong", "caption_any_claim_wrong", "main_bird_orientation",
                "readers_split_on_caption", "readers_split_on_orientation"]
SPOT_SCHEMA = [
    ("frame_id", "numeric", "Joins to frame_id in labels.csv."),
    ("caption_claims_eye_contact", "numeric", "1 if the caption says or implies a bird looks at the camera or lens, else 0."),
    ("caption_eye_contact_wrong", "numeric", "1 if the caption claims eye contact and the readers found the bird was not facing the lens; 0 if the claim held; blank if the caption makes no such claim."),
    ("caption_species_wrong", "numeric", "1 if the caption names a species (in practice a turkey, always a background bird) that the readers could not find in the picture, else 0. The readers were not always sure."),
    ("caption_other_claim_wrong", "numeric", "1 if some other specific claim in the caption (activity, object, colour, count, sharpness) is plainly contradicted by the picture, else 0."),
    ("caption_any_claim_wrong", "numeric", "1 if any of the three caption_*_wrong flags is 1, else 0."),
    ("main_bird_orientation", "string", "Blind reading of the most prominent bird, made without seeing the caption (so not always the bird the caption describes): facing_lens, three_quarter, side_profile, rear_view, head_down or hidden_or_blurred."),
    ("readers_split_on_caption", "numeric", "1 if the first two AI readers disagreed about whether a caption claim held or was contradicted and a third reader decided, else 0."),
    ("readers_split_on_orientation", "numeric", "1 if the first two AI readers disagreed on the orientation and a third reader decided, else 0."),
]
# caption_spot_check_readers.csv: every AI reader's own answers, one row per frame per pass per reader,
# so nothing the readers produced is thrown away (the other file holds only the settled result).
READERS_COLUMNS = ["frame_id", "pass", "reader", "caption_claims_eye_contact", "bird_faces_lens", "species_named",
                   "species_found", "visible_bird_count", "other_claim_contradicted", "main_bird_orientation",
                   "main_bird_kind", "note"]
READERS_SCHEMA = [
    ("frame_id", "numeric", "Joins to frame_id in labels.csv."),
    ("pass", "string", "caption_check: the reader saw the caption beside the picture. blind_orientation: the reader saw only the picture."),
    ("reader", "string", "A or B: the first two independent readers. C: the third reader, asked only where A and B disagreed."),
    ("caption_claims_eye_contact", "numeric", "caption_check only: 1 if the reader judged the caption says or implies a bird looks at the camera, else 0."),
    ("bird_faces_lens", "string", "caption_check only: yes, no or unclear. Whether the bird the caption means actually faces the lens."),
    ("species_named", "string", "caption_check only: the species the caption names: chicken, turkey, both, other or none."),
    ("species_found", "string", "caption_check only: yes, no or unclear. Whether the species the caption names is really in the picture."),
    ("visible_bird_count", "numeric", "caption_check only: the reader's own count of whole or mostly visible birds, -1 if unsure."),
    ("other_claim_contradicted", "numeric", "caption_check only: 1 if the reader judged some other caption claim plainly contradicted by the picture, else 0."),
    ("main_bird_orientation", "string", "blind_orientation only: facing_lens, three_quarter, side_profile, rear_view, head_down or hidden_or_blurred."),
    ("main_bird_kind", "string", "blind_orientation only: chicken, turkey or unclear."),
    ("note", "string", "The reader's own short note. Not recorded for reader C in the blind pass."),
]
ORIENTATIONS = ["facing_lens", "three_quarter", "side_profile", "rear_view", "head_down", "hidden_or_blurred"]
FILE_NOTES = {
    "labels.csv": "One row per picture: the label (reacted, reaction_people), the vision model's descriptions and scores, the measured bird coverage and a suggested split by whole days.",
    "caption_spot_check.csv": "AI spot check of 160 sampled captions against their pictures: whether the caption's eye-contact, species and other claims held, and which way the main bird faces (read blind to the caption). Joins to labels.csv on frame_id.",
    "caption_spot_check_readers.csv": "Every AI reader's own answers behind caption_spot_check.csv: for each of the 160 frames, the first two readers' answers in both passes and the third reader's where they disagreed, with each reader's short note.",
    "README.md": "The dataset card: how to read the label, why the written descriptions are often wrong, scoring eras, the suggested split, baselines and caveats.",
}


def utc_date(timestamp: str) -> str:
    return timestamp[:10]


def local_date(timestamp: str) -> str:
    return datetime.fromisoformat(timestamp).astimezone(LOCAL_TZ).date().isoformat()


def to_flag(value: str) -> str:
    """'True'/'False' text from the source table -> 1/0; anything else stays blank (not recorded)."""
    return {"True": "1", "False": "0"}.get(value, "")


# ---------------------------------------------------------------- table


def load_frames() -> tuple[list[dict], dict]:
    """One row per confidently linked frame posted on or before the cutoff, plus counts."""
    fill = {}
    with FILL_CSV.open(newline="") as handle:
        for row in csv.DictReader(handle):
            fill[row["frame_row_id"]] = row
    stats = Counter()
    frames: dict[str, dict] = {}
    with LABELLED_CSV.open(newline="") as handle:
        for row in csv.DictReader(handle):
            stats["posts_total"] += 1
            if row["link_method"] not in CONFIDENT_LINKS or not row["frame_row_id"]:
                stats["posts_unlinked_or_ambiguous"] += 1
                continue
            if utc_date(row["posted_at"]) > LABEL_CUTOFF_DATE:
                stats["posts_too_recent"] += 1
                continue
            frame_id = row["frame_row_id"]
            if frame_id in frames:  # same frame posted twice: reacted if any post was
                stats["frames_posted_twice"] += 1
                if row["reacted"] == "1":
                    frames[frame_id]["reacted"] = "1"
                continue
            if frame_id not in fill:
                raise SystemExit(f"frame {frame_id} has no measured-fill row; rebuild fill_measured.csv")
            measured = fill[frame_id]
            ok = measured["status"] == "ok"
            captured = row["frame_captured_at"]
            caption = row["posted_caption"]
            if LEAK_PATTERN.search(caption) or FOREIGN_SCRIPT.search(caption):
                stats["captions_blanked"] += 1
                caption = ""
            frames[frame_id] = {
                "frame_id": frame_id,
                "src_path": row["image_path"],
                "image": f"images/{captured[:7]}/{frame_id}.jpg",
                "captured_at": captured,
                "scoring_era": ERA_RENAME.get(row["scoring_era"], row["scoring_era"]),
                "rollout_day": "1" if utc_date(captured) in ROLLOUT_DAYS else "0",
                "reacted": row["reacted"],
                "score": row["score"],
                "composition": row["composition"],
                "scene": row["scene"],
                "lighting": row["lighting"],
                "activity": row["activity"],
                "bird_count": row["bird_count"],
                "standout_bird": to_flag(row["standout_bird"]),
                "expression_score": row["expression_score"],
                "detail_score": row["detail_score"],
                "subject_coverage_pct": row["subject_coverage_pct"],
                "largest_subject_pct": row["largest_subject_pct"],
                "measured_fill_pct": measured["measured_fill_pct"] if ok else "",
                "fill_status": FILL_STATUS_RENAME[measured["status"]],
                "caption": caption,
            }
    stats["frames"] = len(frames)
    return sorted(frames.values(), key=lambda f: f["captured_at"]), dict(stats)


def attach_reaction_people(frames: list[dict]) -> None:
    """Add reaction_people: how many DIFFERENT non-bot people reacted to the frame's post.

    The reaction-sync job (scripts/discord-reaction-sync.py, _count_human_reactions) already wrote
    that count to image_archive.discord_reactions as the size of the set of distinct human reactor
    ids across every emoji, so it is read here from the archive (read-only): no Discord reads, no
    per-person look-ups. Who reacted is not stored anywhere in the archive and is not shipped.
    The count must agree with the label already built from the stamped message id; any
    disagreement stops the build rather than being papered over."""
    conn = sqlite3.connect(f"file:{GUARDIAN_DB}?mode=ro", uri=True)
    try:
        ids = [int(f["frame_id"]) for f in frames]
        counts: dict[int, int] = {}
        models: dict[int, str] = {}
        for start in range(0, len(ids), 500):  # stay under SQLite's variable limit
            chunk = ids[start:start + 500]
            query = "SELECT id, discord_reactions, vlm_model FROM image_archive WHERE id IN (%s)" % ",".join("?" * len(chunk))
            for row_id, count, model in conn.execute(query, chunk):
                counts[row_id] = int(count or 0)
                models[row_id] = model
    finally:
        conn.close()
    missing = [i for i in ids if i not in counts]
    if missing:
        raise SystemExit(f"{len(missing)} frames not found in the archive, e.g. {missing[:3]}")
    for frame in frames:
        frame["reaction_people"] = str(counts[int(frame["frame_id"])])
        frame["_vlm_model"] = models[int(frame["frame_id"])]  # used only for a card count, never shipped
        if (frame["reaction_people"] != "0") != (frame["reacted"] == "1"):
            raise SystemExit(f"frame {frame['frame_id']}: reacted={frame['reacted']} but "
                             f"archive reaction count={frame['reaction_people']}")


def assign_split(frames: list[dict]) -> dict:
    """Whole-day split, shuffled within each scoring era so every era reaches every fold."""
    days_by_era: dict[str, Counter] = defaultdict(Counter)
    for frame in frames:
        frame["_day"] = local_date(frame["captured_at"])
        days_by_era[frame["_day"]][frame["scoring_era"]] += 1
    era_of_day = {day: eras.most_common(1)[0][0] for day, eras in days_by_era.items()}
    rng = random.Random(SPLIT_SEED)
    fold_of_day = {}
    for era in ERA_ORDER:
        days = sorted(d for d, e in era_of_day.items() if e == era)
        rng.shuffle(days)
        n_test = max(1, round(TEST_SHARE * len(days)))
        n_val = max(1, round(VAL_SHARE * len(days)))
        for i, day in enumerate(days):
            fold_of_day[day] = "test" if i < n_test else "val" if i < n_test + n_val else "train"
    for frame in frames:
        frame["split"] = fold_of_day[frame["_day"]]
        frame["day"] = frame["_day"]  # shipped, so strangers can group on the same day the split used
    return fold_of_day


# ---------------------------------------------------------------- pictures


def copy_pictures(frames: list[dict]) -> Counter:
    """Clone-copy each picture; re-encode without metadata only when the file carries some."""
    stats = Counter()
    for index, frame in enumerate(frames, 1):
        src = IMAGE_ROOT / frame["src_path"]
        dst = PACKAGE_DIR / frame["image"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.stat().st_size > 0:
            stats["already_copied"] += 1
            continue
        with Image.open(src) as img:
            carries_metadata = bool(img.getexif()) or any(
                key in img.info for key in ("exif", "icc_profile", "comment", "xmp", "photoshop"))
            if carries_metadata:
                # Anything beyond the bare JFIF header (camera tags, location, comments) is stripped.
                img.convert("RGB").save(dst, "JPEG", quality=95, subsampling=0)
                stats["rescrubbed"] += 1
                continue
        # cp -c clones on APFS (instant, no second 12 GB); fall back to a plain copy elsewhere.
        if subprocess.run(["cp", "-c", str(src), str(dst)], capture_output=True).returncode != 0:
            shutil.copy2(src, dst)
            stats["plain_copy"] += 1
        else:
            stats["cloned"] += 1
        if index % 1000 == 0:
            log.info("pictures %d / %d", index, len(frames))
    return stats


# ---------------------------------------------------------------- baselines


def auc(rows: list[tuple[float, int]]) -> float | None:
    """Probability a random reacted row outranks a random unreacted one (0.5 = chance)."""
    positives = sum(label for _, label in rows)
    negatives = len(rows) - positives
    if positives == 0 or negatives == 0:
        return None
    ordered = sorted(rows, key=lambda r: r[0])
    rank_sum, i = 0.0, 0
    while i < len(ordered):
        j = i
        while j < len(ordered) and ordered[j][0] == ordered[i][0]:
            j += 1
        average_rank = (i + 1 + j) / 2  # ties share the average rank
        rank_sum += average_rank * sum(label for _, label in ordered[i:j])
        i = j
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def baseline_table(frames: list[dict]) -> str:
    """Per era: frames, reacted share, and how well score / measured fill / model fill rank reacted."""
    lines = ["| Scoring era | Frames | Reacted | Score | Measured fill (no bird detected = 0) | Measured fill (model's guess when no bird detected) | Model's fill guess |",
             "|---|---|---|---|---|---|---|"]
    for era in ERA_ORDER:
        rows = [f for f in frames if f["scoring_era"] == era and f["rollout_day"] == "0"]
        label = lambda f: int(f["reacted"])  # noqa: E731
        def value(key, fallback=None):
            # Blank measured fill = nothing measurable. Zero is the default reading (a frame with no
            # detectable bird fills none of it); the model's own guess is shown as the alternative.
            return [(float(f[key]) if f[key] != "" else (float(f[fallback]) if fallback else 0.0), label(f))
                    for f in rows]
        scores = auc([(float(f["score"]), label(f)) for f in rows])
        measured = auc(value("measured_fill_pct"))
        measured_fb = auc(value("measured_fill_pct", "subject_coverage_pct"))
        model_fill = auc(value("subject_coverage_pct"))
        reacted = sum(label(f) for f in rows)
        fmt = lambda x: "-" if x is None else f"{x:.2f}"  # noqa: E731
        lines.append(f"| {era} | {len(rows)} | {reacted} ({100 * reacted / len(rows):.0f}%) | "
                     f"{fmt(scores)} | {fmt(measured)} | {fmt(measured_fb)} | {fmt(model_fill)} |")
    return "\n".join(lines)


def era_table(frames: list[dict]) -> str:
    lines = ["| Scoring era | Captured | Frames | Reacted | Lowest score posted (rollout days excluded) |", "|---|---|---|---|---|"]
    for era in ERA_ORDER:
        rows = [f for f in frames if f["scoring_era"] == era]
        days = sorted(utc_date(f["captured_at"]) for f in rows)
        reacted = sum(int(f["reacted"]) for f in rows)
        # Rollout days still carry the old minimum for part of the day, so they would hide the new one.
        floor = min(int(f["score"]) for f in rows if f["rollout_day"] == "0")
        lines.append(f"| {era} | {days[0]} to {days[-1]} | {len(rows)} | {reacted} ({100 * reacted / len(rows):.0f}%) | {floor} |")
    return "\n".join(lines)


def day_stats(frames: list[dict]) -> dict:
    """Days covered and how widely the reacted share varies from day to day (days with 30+ frames)."""
    per_day: dict[str, list[int]] = defaultdict(list)
    for frame in frames:
        per_day[local_date(frame["captured_at"])].append(int(frame["reacted"]))
    rates = [100 * sum(v) / len(v) for v in per_day.values() if len(v) >= 30]
    return {"N_DAYS": str(len(per_day)), "N_BUSY_DAYS": str(len(rates)),
            "DAY_RATE_MIN": f"{min(rates):.0f}%", "DAY_RATE_MAX": f"{max(rates):.0f}%"}


def no_bird_stats(frames: list[dict]) -> dict:
    rows = [f for f in frames if f["measured_fill_pct"] == ""]
    reacted = sum(int(f["reacted"]) for f in rows)
    return {"N_NO_FILL": f"{len(rows):,}", "NO_FILL_REACTED": f"{100 * reacted / len(rows):.0f}%"}


def test_days_by_era(frames: list[dict]) -> str:
    """Days in the test fold within each scoring era, e.g. 'early_older_rules 5, A_no_fill 6, ...'."""
    parts = []
    for era in ERA_ORDER:
        days = {f["_day"] for f in frames if f["scoring_era"] == era and f["split"] == "test"}
        parts.append(f"{era} {len(days)}")
    return ", ".join(parts)


def split_table(frames: list[dict]) -> str:
    lines = ["| Split | Days | Frames | Reacted |", "|---|---|---|---|"]
    for fold in ("train", "val", "test"):
        rows = [f for f in frames if f["split"] == fold]
        reacted = sum(int(f["reacted"]) for f in rows)
        lines.append(f"| {fold} | {len({f['_day'] for f in rows})} | {len(rows)} | {reacted} ({100 * reacted / len(rows):.0f}%) |")
    return "\n".join(lines)


# ---------------------------------------------------------------- AI spot check


def flag(value) -> str:
    """True/False/None from the reader files -> 1/0/blank."""
    return "" if value is None else ("1" if value else "0")


def load_spot_check(frames: list[dict]) -> list[dict]:
    """One row per spot-checked frame, built from the two saved reader runs (caption-check.json:
    readers with the caption beside the picture; orientation-check.json: readers blind to it).
    Fails if a checked frame is not in the shipped table, so the file can never point at nothing."""
    caption_runs = json.loads((SPOT_DIR / "caption-check.json").read_text())["frames"]
    orientation_runs = {f["frame_id"]: f for f in json.loads((SPOT_DIR / "orientation-check.json").read_text())["frames"]}
    shipped = {f["frame_id"] for f in frames}
    rows = []
    for item in caption_runs:
        frame_id, final = item["frame_id"], item["final"]
        if frame_id not in shipped:
            raise SystemExit(f"spot-checked frame {frame_id} is not in the shipped table")
        orient = orientation_runs.get(frame_id)
        if orient is None or orient["final"] not in ORIENTATIONS:
            raise SystemExit(f"spot-checked frame {frame_id} has no usable orientation reading")
        claimed = final["claimed"] is True
        rows.append({
            "frame_id": frame_id,
            "caption_claims_eye_contact": flag(claimed),
            "caption_eye_contact_wrong": flag(final["wrongFacing"]) if claimed else "",
            "caption_species_wrong": flag(final["wrongSpecies"]),
            "caption_other_claim_wrong": flag(final["wrongOther"]),
            "caption_any_claim_wrong": flag(final["anyWrong"]),
            "main_bird_orientation": orient["final"],
            "readers_split_on_caption": flag(item["split"]),
            "readers_split_on_orientation": flag(not orient["agreed"]),
        })
    return sorted(rows, key=lambda r: int(r["frame_id"]))


def write_spot_check(rows: list[dict]) -> None:
    with (PACKAGE_DIR / "caption_spot_check.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SPOT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def readers_rows(frames: list[dict]) -> list[dict]:
    """Every reader's raw answer from the two saved runs, flattened to one row per frame/pass/reader."""
    shipped = {f["frame_id"] for f in frames}
    blank = {column: "" for column in READERS_COLUMNS}
    rows = []
    for item in json.loads((SPOT_DIR / "caption-check.json").read_text())["frames"]:
        for reader in ("a", "b", "c"):
            answer = item.get(reader)
            if not answer:
                continue
            rows.append({**blank, "frame_id": item["frame_id"], "pass": "caption_check", "reader": reader.upper(),
                         "caption_claims_eye_contact": flag(answer["caption_claims_facing_camera"]),
                         "bird_faces_lens": answer["facing_camera_in_picture"],
                         "species_named": answer["species_named_in_caption"],
                         "species_found": answer["species_correct"],
                         "visible_bird_count": str(answer["visible_bird_count"]),
                         "other_claim_contradicted": flag(answer["other_claim_contradicted"]),
                         "note": answer["note"]})
    for item in json.loads((SPOT_DIR / "orientation-check.json").read_text())["frames"]:
        for index, reader in enumerate(("a", "b", "c")):
            orientation = item.get(reader)
            if not orientation:
                continue
            kinds, notes = item["kinds"], item["notes"]
            rows.append({**blank, "frame_id": item["frame_id"], "pass": "blind_orientation", "reader": reader.upper(),
                         "main_bird_orientation": orientation,
                         "main_bird_kind": kinds[index] if index < len(kinds) else "",
                         "note": notes[index] if index < len(notes) else ""})
    if any(r["frame_id"] not in shipped for r in rows):
        raise SystemExit("a reader row points at a frame that is not in the shipped table")
    return sorted(rows, key=lambda r: (int(r["frame_id"]), r["pass"], r["reader"]))


def write_readers(rows: list[dict]) -> None:
    with (PACKAGE_DIR / "caption_spot_check_readers.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=READERS_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%"


def spot_values(rows: list[dict], frames: list[dict]) -> dict:
    """Card placeholders for the spot check, all computed from the shipped file so the card cannot drift."""
    era_of = {f["frame_id"]: f["scoring_era"] for f in frames}
    reacted_of = {f["frame_id"]: f["reacted"] == "1" for f in frames}
    split_of = {f["frame_id"]: f["split"] for f in frames}
    n = len(rows)
    claimed = [r for r in rows if r["caption_claims_eye_contact"] == "1"]
    wrong_eye = [r for r in claimed if r["caption_eye_contact_wrong"] == "1"]
    any_wrong = [r for r in rows if r["caption_any_claim_wrong"] == "1"]
    facing = [r for r in rows if r["main_bird_orientation"] == "facing_lens"]
    others = [r for r in rows if r["main_bird_orientation"] != "facing_lens"]
    orient_lines = [f"| The main bird was | Pictures | Share of the {len(claimed)} |", "|---|---|---|"]
    names = {"facing_lens": "facing the lens", "three_quarter": "turned three-quarters (partly toward the camera)",
             "side_profile": "in side profile (head turned away)", "rear_view": "seen from behind",
             "head_down": "head down (pecking, drinking)", "hidden_or_blurred": "hidden, cut off or too blurred to tell"}
    for key in ORIENTATIONS:
        count = sum(1 for r in claimed if r["main_bird_orientation"] == key)
        orient_lines.append(f"| {names[key]} | {count} | {percent(count, len(claimed))} |")
    era_lines = ["| Scoring era | Captions checked | Captions claiming eye contact | Of those, contradicted |", "|---|---|---|---|"]
    for era in ERA_ORDER:
        in_era = [r for r in rows if era_of[r["frame_id"]] == era]
        era_claims = [r for r in in_era if r["caption_claims_eye_contact"] == "1"]
        era_wrong = [r for r in era_claims if r["caption_eye_contact_wrong"] == "1"]
        era_lines.append(f"| {era} | {len(in_era)} | {len(era_claims)} | {len(era_wrong)} ({percent(len(era_wrong), len(era_claims))}) |")
    return {
        "SPOT_N": str(n),
        "SPOT_CLAIMED": str(len(claimed)),
        "SPOT_FACING_WRONG": str(len(wrong_eye)),
        "SPOT_FACING_WRONG_PCT": percent(len(wrong_eye), len(claimed)),
        # Lenient reading: a three-quarter turn still counts as eye contact (blind orientation only).
        "SPOT_FACING_LENIENT": str(sum(1 for r in claimed if r["main_bird_orientation"] not in ("facing_lens", "three_quarter"))),
        "SPOT_FACING_LENIENT_PCT": percent(sum(1 for r in claimed if r["main_bird_orientation"] not in ("facing_lens", "three_quarter")), len(claimed)),
        "SPOT_SPECIES": str(sum(1 for r in rows if r["caption_species_wrong"] == "1")),
        "SPOT_OTHER": str(sum(1 for r in rows if r["caption_other_claim_wrong"] == "1")),
        "SPOT_ANY": str(len(any_wrong)),
        "SPOT_ANY_PCT": percent(len(any_wrong), n),
        "SPOT_SPLIT": str(sum(1 for r in rows if r["readers_split_on_caption"] == "1")),
        "SPOT_ORIENT_SPLIT": str(sum(1 for r in rows if r["readers_split_on_orientation"] == "1")),
        "SPOT_NO_FACE": str(sum(1 for r in rows if r["main_bird_orientation"] in ("rear_view", "hidden_or_blurred"))),
        "SPOT_ONLY_EYE": str(sum(1 for r in rows if r["caption_any_claim_wrong"] == "1" and r["caption_species_wrong"] == "0" and r["caption_other_claim_wrong"] == "0")),
        "SPOT_OTHER_ANY": str(sum(1 for r in rows if "1" in (r["caption_species_wrong"], r["caption_other_claim_wrong"]))),
        "SPOT_BOTH_SPLIT": str(sum(1 for r in rows if r["readers_split_on_caption"] == "1" and r["readers_split_on_orientation"] == "1")),
        "SPOT_NOT_FACING": str(sum(1 for r in claimed if r["main_bird_orientation"] != "facing_lens")),
        "SPOT_ANY_SPLIT": str(sum(1 for r in rows if "1" in (r["readers_split_on_caption"], r["readers_split_on_orientation"]))),
        "SPOT_TRAIN": str(sum(1 for r in rows if split_of[r["frame_id"]] == "train")),
        "SPOT_VAL": str(sum(1 for r in rows if split_of[r["frame_id"]] == "val")),
        "SPOT_TEST": str(sum(1 for r in rows if split_of[r["frame_id"]] == "test")),
        "ORIENT_ALL_FACING": str(len(facing)),
        "ORIENT_AGREED": str(sum(1 for r in rows if r["readers_split_on_orientation"] == "0")),
        "ORIENT_TABLE": "\n".join(orient_lines),
        "SPOT_FACING_TABLE": "\n".join(era_lines),
        "SPOT_REACT_FACING": f"{sum(reacted_of[r['frame_id']] for r in facing)} of {len(facing)}",
        "SPOT_REACT_OTHER": f"{sum(reacted_of[r['frame_id']] for r in others)} of {len(others)}",
    }


def people_values(frames: list[dict]) -> dict:
    counts = Counter(f["reaction_people"] for f in frames)
    by_era = []
    for era in ERA_ORDER:
        reacted = [f for f in frames if f["scoring_era"] == era and f["reacted"] == "1"]
        multi = sum(1 for f in reacted if int(f["reaction_people"]) >= 2)
        by_era.append(f"{percent(multi, len(reacted))} in {era}")
    return {"N_PEOPLE_1": f"{counts['1']:,}", "N_PEOPLE_2": f"{counts['2']:,}", "N_PEOPLE_3": f"{counts['3']:,}",
            "PEOPLE_BY_ERA": ", ".join(by_era)}


def bird_count_values(frames: list[dict]) -> dict:
    """How well the model's bird_count agrees with the two caption-check readers' own counts.
    A reader count of -1 means unsure and is skipped; the readers' counts are averaged."""
    shipped = {f["frame_id"]: f for f in frames}
    runs = json.loads((SPOT_DIR / "caption-check.json").read_text())["frames"]
    pairs = []  # (model count, mean of the readers' counts)
    for item in runs:
        counts = [r["visible_bird_count"] for r in (item["a"], item["b"]) if r["visible_bird_count"] >= 0]
        model = shipped[item["frame_id"]]["bird_count"]
        if counts and model:
            pairs.append((int(model), sum(counts) / len(counts)))
    within_one = sum(1 for model, readers in pairs if abs(model - readers) <= 1)
    # Baseline: the best single constant guess, so "within one bird" is not read as more than it is.
    best_const, best_hits = max(((c, sum(1 for _, readers in pairs if abs(c - readers) <= 1)) for c in range(1, 11)), key=lambda t: t[1])
    return {"BIRD_COUNT_N": str(len(pairs)), "BIRD_COUNT_WITHIN1": str(within_one),
            "BC_CONST": str(best_const), "BC_BASE": str(best_hits)}


# ---------------------------------------------------------------- card, metadata, files


def write_table(frames: list[dict]) -> None:
    with (PACKAGE_DIR / "labels.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(frames)


def render_card(frames: list[dict], stats: dict, total_bytes: int, spot_rows: list[dict]) -> str:
    template = CARD_TEMPLATE.read_text()
    reacted = sum(int(f["reacted"]) for f in frames)
    days = sorted(utc_date(f["captured_at"]) for f in frames)
    values = {
        "N_FRAMES": f"{len(frames):,}",
        "N_REACTED": f"{reacted:,}",
        "REACT_SHARE": f"{100 * reacted / len(frames):.0f}%",
        "FIRST_DAY": days[0],
        "LAST_DAY": days[-1],
        "CUTOFF": LABEL_CUTOFF_DATE,
        "SIZE_GB": f"{total_bytes / 1e9:.1f}",
        "ERA_TABLE": era_table(frames),
        "BASELINE_TABLE": baseline_table(frames),
        "SPLIT_TABLE": split_table(frames),
        "N_TOO_RECENT": f"{stats['posts_too_recent']:,}",
        "N_UNLINKED": f"{stats['posts_unlinked_or_ambiguous']:,}",
        "N_POSTS": f"{stats['posts_total']:,}",
        "N_DUP_POSTS": f"{stats.get('frames_posted_twice', 0):,}",
        "N_BLANKED": f"{stats.get('captions_blanked', 0):,}",
        "LICENSE": LICENSE_NAME,
        **day_stats(frames),
        **no_bird_stats(frames),
        **people_values(frames),
        "N_PEOPLE_SEEN": str(N_PEOPLE_SEEN),
        "N_STRAIGHT": f"{sum(1 for f in frames if 'straight into the lens' in f['caption'].lower()):,}",
        "N_OTHER_MODEL": f"{sum(1 for f in frames if f['_vlm_model'] != MAIN_VLM_MODEL):,}",
        "N_SKIPPED_DARK": f"{sum(1 for f in frames if f['fill_status'] == 'skipped_dark_gate'):,}",
        "TEST_DAYS_BY_ERA": test_days_by_era(frames),
        **bird_count_values(frames),
        **spot_values(spot_rows, frames),
    }
    for key, text in values.items():
        template = template.replace("{{" + key + "}}", text)
    if "{{" in template:
        raise SystemExit("card template has an unfilled placeholder: " + template[template.index("{{"):][:40])
    return template


def provenance_sections() -> dict:
    """The Sources and Collection methodology texts from the provenance doc (the single source)."""
    text = PROVENANCE_DOC.read_text()
    sections = {}
    for title in ("Sources", "Collection methodology"):
        match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
        if not match or not match.group(1).strip():
            raise SystemExit(f"provenance doc has no '## {title}' section")
        sections[title] = match.group(1).strip()
    return sections


def write_metadata(card: str, frames: list[dict]) -> None:
    """Two metadata files, because Kaggle's two commands read different shapes.

    * kaggle-package/dataset-metadata.json is read by `kaggle datasets version`. Only that upload
      route stores the per-file descriptions (the page's "file information" check), and it carries
      the subtitle, description and tags. It ignores license, sources and update frequency.
    * kaggle-meta/dataset-metadata.json is read by `kaggle datasets metadata --update`, which sets
      sources, update frequency, license and the private flag. isPrivate MUST be true here: the
      command sends is_private=False when the key is missing, which would publish the dataset.
      The command also takes `image` for the cover picture; it is left out on purpose, because the
      owner already set his own cover and an update without `image` leaves it alone (rehearsed)."""
    reacted = sum(int(f["reacted"]) for f in frames)
    subtitle = f"{len(frames):,} chicken & turkey photos 🐔🦃 with reaction labels; captions often wrong"
    assert 20 <= len(subtitle) <= 80 and 6 <= len(KAGGLE_TITLE) <= 50, "Kaggle title/subtitle length"
    assert [n for n, _, _ in SCHEMA] == OUT_COLUMNS, "schema and column list drifted apart"
    assert [n for n, _, _ in SPOT_SCHEMA] == SPOT_COLUMNS, "spot-check schema and column list drifted apart"
    assert [n for n, _, _ in READERS_SCHEMA] == READERS_COLUMNS, "readers schema and column list drifted apart"
    fields = lambda schema: [{"name": n, "description": d, "type": t} for n, t, d in schema]  # noqa: E731
    version_metadata = {
        "title": KAGGLE_TITLE,
        "id": f"{KAGGLE_OWNER}/{KAGGLE_SLUG}",
        "subtitle": subtitle,
        "description": card,
        "licenses": [{"name": LICENSE_NAME}],
        "keywords": KEYWORDS,
        "resources": [
            {"path": "labels.csv", "description": FILE_NOTES["labels.csv"], "schema": {"fields": fields(SCHEMA)}},
            {"path": "caption_spot_check.csv", "description": FILE_NOTES["caption_spot_check.csv"],
             "schema": {"fields": fields(SPOT_SCHEMA)}},
            {"path": "caption_spot_check_readers.csv", "description": FILE_NOTES["caption_spot_check_readers.csv"],
             "schema": {"fields": fields(READERS_SCHEMA)}},
            {"path": "README.md", "description": FILE_NOTES["README.md"]},
        ],
    }
    (PACKAGE_DIR / "dataset-metadata.json").write_text(json.dumps(version_metadata, indent=2))

    sections = provenance_sections()
    update_metadata = {
        "title": KAGGLE_TITLE,
        "subtitle": subtitle,
        "description": card,
        "isPrivate": True,
        "licenses": [{"name": LICENSE_LONG_NAME}],
        "keywords": KEYWORDS,
        "expectedUpdateFrequency": UPDATE_FREQUENCY,
        "userSpecifiedSources": sections["Sources"],
        "data": [{"name": name, "description": note} for name, note in FILE_NOTES.items()],
    }
    META_DIR.mkdir(parents=True, exist_ok=True)
    (META_DIR / "dataset-metadata.json").write_text(json.dumps(update_metadata, indent=2))
    (META_DIR / "provenance-sources.txt").write_text(sections["Sources"] + "\n")
    (META_DIR / "provenance-methodology.txt").write_text(sections["Collection methodology"] + "\n")
    # The upload route does not store column notes; they are typed into the page from this file.
    (META_DIR / "column-descriptions.json").write_text(json.dumps({
        "labels.csv": [[n, d] for n, _, d in SCHEMA],
        "caption_spot_check.csv": [[n, d] for n, _, d in SPOT_SCHEMA],
        "caption_spot_check_readers.csv": [[n, d] for n, _, d in READERS_SCHEMA],
    }, indent=2))


# ---------------------------------------------------------------- verification


def verify_package(expected_frames: int) -> list[str]:
    """Re-read the finished package from disk; raise on any inconsistency, return report lines."""
    with (PACKAGE_DIR / "labels.csv").open(newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames
        rows = list(reader)
    problems = []
    if header != OUT_COLUMNS:
        problems.append(f"header differs from OUT_COLUMNS: {header}")
    if FORBIDDEN_COLUMNS & set(header or []):
        problems.append(f"forbidden columns shipped: {FORBIDDEN_COLUMNS & set(header)}")
    if len(rows) != expected_frames:
        problems.append(f"row count {len(rows)} != {expected_frames}")
    if len({r["frame_id"] for r in rows}) != len(rows):
        problems.append("duplicate frame_id")
    sizes, metadata_files, total_bytes = Counter(), 0, 0
    for row in rows:
        path = PACKAGE_DIR / row["image"]
        if not path.exists():
            problems.append(f"missing picture {row['image']}")
            continue
        total_bytes += path.stat().st_size
        with Image.open(path) as img:
            img.load()  # full decode, so a truncated file is caught
            sizes[img.size] += 1
            if img.getexif() or any(k in img.info for k in ("exif", "icc_profile", "comment", "xmp")):
                metadata_files += 1
    folds_of_day = defaultdict(set)
    for row in rows:
        folds_of_day[local_date(row["captured_at"])].add(row["split"])
    if any(len(folds) > 1 for folds in folds_of_day.values()):
        problems.append("a calendar day appears in more than one split")
    for fold in ("train", "val", "test"):
        eras = {r["scoring_era"] for r in rows if r["split"] == fold}
        if eras != set(ERA_ORDER):
            problems.append(f"split {fold} is missing eras {set(ERA_ORDER) - eras}")
    if not {r["reacted"] for r in rows} <= {"0", "1"}:
        problems.append("reacted is not 0/1")
    if not {r["reaction_people"] for r in rows} <= {"0", "1", "2", "3"}:
        problems.append(f"reaction_people has unexpected values: {sorted({r['reaction_people'] for r in rows})}")
    if any((r["reaction_people"] != "0") != (r["reacted"] == "1") for r in rows):
        problems.append("reaction_people and reacted disagree on some row")
    # The AI spot-check file: right header, every frame shipped, one row per frame, valid orientations.
    with (PACKAGE_DIR / "caption_spot_check.csv").open(newline="") as handle:
        spot_reader = csv.DictReader(handle)
        spot_header, spot_rows = spot_reader.fieldnames, list(spot_reader)
    if spot_header != SPOT_COLUMNS:
        problems.append(f"spot-check header differs: {spot_header}")
    shipped_ids = {r["frame_id"] for r in rows}
    if any(r["frame_id"] not in shipped_ids for r in spot_rows):
        problems.append("a spot-checked frame is not in labels.csv")
    if len({r["frame_id"] for r in spot_rows}) != len(spot_rows):
        problems.append("duplicate frame in the spot-check file")
    if not {r["main_bird_orientation"] for r in spot_rows} <= set(ORIENTATIONS):
        problems.append("spot-check file has an unknown orientation")
    card_text = (PACKAGE_DIR / "README.md").read_text()
    if "{{" in card_text:
        problems.append("README.md still has an unfilled placeholder")
    update_meta = json.loads((META_DIR / "dataset-metadata.json").read_text())
    if update_meta.get("isPrivate") is not True:
        problems.append("the update metadata does not say isPrivate=true: `metadata --update` would publish the dataset")
    # A constant column is a gate artefact dressed up as a feature (bird_face_visible was one).
    for column in OUT_COLUMNS:
        if column not in ("split",) and len({r[column] for r in rows}) < 2:
            problems.append(f"column {column} is constant")
    if {r["scoring_era"] for r in rows} != set(ERA_ORDER):
        problems.append(f"era names differ from ERA_ORDER: {sorted({r['scoring_era'] for r in rows})}")
    if any(LEAK_PATTERN.search(r["caption"]) or FOREIGN_SCRIPT.search(r["caption"]) for r in rows):
        problems.append("a caption that should have been blanked is still in the table")
    on_disk = {p.relative_to(PACKAGE_DIR).as_posix() for p in PACKAGE_DIR.rglob("*") if p.is_file()}
    expected = {r["image"] for r in rows} | {"labels.csv", "caption_spot_check.csv", "caption_spot_check_readers.csv",
                                             "README.md", "dataset-metadata.json"}
    with (PACKAGE_DIR / "caption_spot_check_readers.csv").open(newline="") as handle:
        readers_reader = csv.DictReader(handle)
        if readers_reader.fieldnames != READERS_COLUMNS:
            problems.append(f"readers file header differs: {readers_reader.fieldnames}")
        if any(r["frame_id"] not in shipped_ids for r in readers_reader):
            problems.append("a readers-file frame is not in labels.csv")
    if on_disk != expected:
        problems.append(f"unexpected files: {sorted(on_disk - expected)[:5]}; missing: {sorted(expected - on_disk)[:5]}")
    top_level = [p for p in PACKAGE_DIR.iterdir()]
    if len(top_level) > 50:
        problems.append("more than 50 top-level entries")
    if problems:
        raise SystemExit("PACKAGE VERIFICATION FAILED:\n  " + "\n  ".join(problems))
    return [f"verified: rows={len(rows)} pictures={len(rows)} bytes={total_bytes} ({total_bytes / 1e9:.2f} GB)",
            f"picture sizes: {dict(sizes)}",
            f"pictures still carrying metadata: {metadata_files}",
            f"top-level entries: {sorted(p.name for p in top_level)}"], total_bytes


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if not BASE_DIR.exists():
        sys.exit("Team 4TB drive is not mounted; refusing to fall back to the internal disk")
    PACKAGE_DIR.mkdir(parents=True, exist_ok=True)
    frames, stats = load_frames()
    attach_reaction_people(frames)
    assign_split(frames)
    spot_rows = load_spot_check(frames)
    log.info("frames=%d stats=%s", len(frames), stats)
    picture_stats = copy_pictures(frames)
    write_table(frames)
    write_spot_check(spot_rows)
    write_readers(readers_rows(frames))
    # The card needs the true byte total, so render it after the pictures are in place.
    total_bytes = sum((PACKAGE_DIR / f["image"]).stat().st_size for f in frames)
    card = render_card(frames, stats, total_bytes, spot_rows)
    (PACKAGE_DIR / "README.md").write_text(card)
    write_metadata(card, frames)
    report, _ = verify_package(len(frames))
    lines = [f"built {datetime.now().isoformat(timespec='seconds')}", f"table stats: {stats}",
             f"pictures: {dict(picture_stats)}", *report, "", era_table(frames), "",
             split_table(frames), "", baseline_table(frames)]
    REPORT_PATH.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
