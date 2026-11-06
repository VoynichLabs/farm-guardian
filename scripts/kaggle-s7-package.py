#!/usr/bin/env python3
# Author: Claude Sonnet 5.5 Medium
# Date: 06-November-2026
# PURPOSE: Build the Kaggle package for the S7 reaction dataset (plan:
#          docs/06-Nov-2026-kaggle-s7-dataset-publish-plan.md) and verify it from disk.
#          Input : s7_posts_labelled.csv (built by scripts/s7-reaction-dataset.py) and
#                  fill_measured.csv (scripts/s7-measured-fill.py), both on the SSD, plus the
#                  archive pictures under /Volumes/Team 4TB/data/farm-guardian/.
#          Output: <SSD folder>/kaggle-package/ with labels.csv, images/YYYY-MM/<frame_id>.jpg,
#                  README.md (the card), dataset-metadata.json; and package_report.txt beside it.
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

KAGGLE_OWNER = "markbarney"
KAGGLE_SLUG = "s7-chicken-photo-reactions"
KAGGLE_TITLE = "Chicken Photos Labelled by a Farmer's Reactions"
LICENSE_NAME = "CC-BY-4.0"
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
FOREIGN_SCRIPT = re.compile(r"[Ѐ-ӿ؀-ۿḀ-ỿ　-鿿가-힯]")

SPLIT_SEED = 20261106
VAL_SHARE = 0.15
TEST_SHARE = 0.15
LOCAL_TZ = ZoneInfo("America/New_York")  # the farm's day, so an evening is not cut at UTC midnight

# Columns in the shipped table, in order. Anything not listed here is not shipped.
OUT_COLUMNS = [
    "frame_id", "image", "captured_at", "scoring_era", "rollout_day", "split", "reacted",
    "score", "composition", "scene", "lighting", "activity", "bird_count",
    "standout_bird", "expression_score", "detail_score",
    "subject_coverage_pct", "largest_subject_pct", "measured_fill_pct", "fill_status", "caption",
]
# bird_face_visible is gone: it is 1 on every row (a consequence of the posting gate), so it is a
# constant dressed up as a feature. verify_package also fails on any constant shipped column.
FORBIDDEN_COLUMNS = {"link_method", "msg_id", "posted_at", "share_worth", "image_quality",
                     "frame_row_id", "image_path", "bird_face_visible"}
SCHEMA = [  # (name, type, description) for dataset-metadata.json; kept beside OUT_COLUMNS
    ("frame_id", "integer", "Row number of the frame in the farm's archive; the join key. It rises with capture time, so use it to join, not as a feature."),
    ("image", "string", "Path of the picture relative to the dataset root."),
    ("captured_at", "datetime", "When the phone captured the frame (UTC)."),
    ("scoring_era", "string", "Which scoring rules (and vision-model prompt) were live: early_older_rules, A_no_fill, B1_vlm_fill, B2_measured_fill. Never pool score or any vision-model column across eras."),
    ("rollout_day", "integer", "1 on days a scoring change went live part-way through the day (old and new rules mixed)."),
    ("split", "string", "Suggested split: train, val or test. Whole US Eastern calendar days, so no day is in two folds."),
    ("reacted", "integer", "1 if a person reacted to the post of this frame, 0 if no reaction was recorded."),
    ("score", "integer", "The 0-100 score printed on the post (visible to the person reacting). A vision model's output, not a human label."),
    ("composition", "string", "Vision model's framing label: portrait, group or wide."),
    ("scene", "string", "Vision model's scene label; mostly birdcatraz, the poultry enclosure. Not a reliable screen."),
    ("lighting", "string", "Vision model's lighting label; almost always natural-good."),
    ("activity", "string", "Vision model's activity label (none-visible, drinking, eating, ...)."),
    ("bird_count", "integer", "Vision model's bird count."),
    ("standout_bird", "integer", "Vision model's standout flag (1/0). Blank until part-way through 21 Sep 2026: the field did not exist."),
    ("expression_score", "integer", "Vision model sub-score; observed range 10-30."),
    ("detail_score", "integer", "Vision model sub-score; observed range 15-25."),
    ("subject_coverage_pct", "integer", "Vision model's guess of how much of the frame the birds fill. Runs high next to measured_fill_pct."),
    ("largest_subject_pct", "integer", "Vision model's guess of how much the largest bird fills."),
    ("measured_fill_pct", "numeric", "Detector-measured share of the frame the birds fill. Blank when fill_status is not ok."),
    ("fill_status", "string", "ok; no_bird_detected (the detector found no confident bird box); skipped_dark_gate (the detector's brightness gate skipped the frame)."),
    ("caption", "string", "Caption the vision model wrote for the post. Early captions can be cut off mid-sentence, many captions repeat verbatim, and a few are blank (see the card)."),
]


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


def split_table(frames: list[dict]) -> str:
    lines = ["| Split | Days | Frames | Reacted |", "|---|---|---|---|"]
    for fold in ("train", "val", "test"):
        rows = [f for f in frames if f["split"] == fold]
        reacted = sum(int(f["reacted"]) for f in rows)
        lines.append(f"| {fold} | {len({f['_day'] for f in rows})} | {len(rows)} | {reacted} ({100 * reacted / len(rows):.0f}%) |")
    return "\n".join(lines)


# ---------------------------------------------------------------- card, metadata, files


def write_table(frames: list[dict]) -> None:
    with (PACKAGE_DIR / "labels.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(frames)


def render_card(frames: list[dict], stats: dict, total_bytes: int) -> str:
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
    }
    for key, text in values.items():
        template = template.replace("{{" + key + "}}", text)
    if "{{" in template:
        raise SystemExit("card template has an unfilled placeholder: " + template[template.index("{{"):][:40])
    return template


def write_metadata(card: str, frames: list[dict]) -> None:
    reacted = sum(int(f["reacted"]) for f in frames)
    subtitle = f"{len(frames):,} flock photos; the label is whether a person reacted ({reacted:,} did)"
    assert 20 <= len(subtitle) <= 80 and 6 <= len(KAGGLE_TITLE) <= 50, "Kaggle title/subtitle length"
    metadata = {
        "title": KAGGLE_TITLE,
        "id": f"{KAGGLE_OWNER}/{KAGGLE_SLUG}",
        "subtitle": subtitle,
        "description": card,
        "licenses": [{"name": LICENSE_NAME}],
        "keywords": KEYWORDS,
        "resources": [{
            "path": "labels.csv",
            "description": "One row per picture: the human label, the scoring details, the suggested split.",
            "schema": {"fields": [{"name": n, "description": d, "type": t} for n, t, d in SCHEMA]},
        }],
    }
    assert [n for n, _, _ in SCHEMA] == OUT_COLUMNS, "schema and column list drifted apart"
    (PACKAGE_DIR / "dataset-metadata.json").write_text(json.dumps(metadata, indent=2))


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
    # A constant column is a gate artefact dressed up as a feature (bird_face_visible was one).
    for column in OUT_COLUMNS:
        if column not in ("split",) and len({r[column] for r in rows}) < 2:
            problems.append(f"column {column} is constant")
    if {r["scoring_era"] for r in rows} != set(ERA_ORDER):
        problems.append(f"era names differ from ERA_ORDER: {sorted({r['scoring_era'] for r in rows})}")
    if any(LEAK_PATTERN.search(r["caption"]) or FOREIGN_SCRIPT.search(r["caption"]) for r in rows):
        problems.append("a caption that should have been blanked is still in the table")
    on_disk = {p.relative_to(PACKAGE_DIR).as_posix() for p in PACKAGE_DIR.rglob("*") if p.is_file()}
    expected = {r["image"] for r in rows} | {"labels.csv", "README.md", "dataset-metadata.json"}
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
    assign_split(frames)
    log.info("frames=%d stats=%s", len(frames), stats)
    picture_stats = copy_pictures(frames)
    write_table(frames)
    # The card needs the true byte total, so render it after the pictures are in place.
    total_bytes = sum((PACKAGE_DIR / f["image"]).stat().st_size for f in frames)
    card = render_card(frames, stats, total_bytes)
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
