#!/usr/bin/env python3
# Author: Claude Sonnet 5
# Date: 02-Aug-2026
# PURPOSE: Verification-only replay harness for prompt.md changes. Loads a
#          candidate prompt file from disk (does NOT touch the live prompt.md)
#          and re-runs the real vlm_enricher.enrich() against real archived
#          s7-cam images, reporting the new share_worth/overall_score next to
#          what's already stored for the same row. Never writes to
#          image_archive. Built for docs/02-Aug-2026-vlm-gem-scoring-
#          recalibration-plan.md — see that doc for the retention-driven
#          sampling limitation (skip-tier frames are never retained; this can
#          only test for regressions in strong/decent-tier frames, not
#          whether the fix rescues frames the old prompt threw away as skip).
# SRP/DRY check: Pass — verification only, imports the production enrich()
#                rather than reimplementing VLM-call logic. No mocks: real
#                DB rows, real archived JPEGs, real LM Studio calls.

import argparse
import json
import random
import sqlite3
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tools.pipeline.vlm_enricher import enrich  # noqa: E402
from tools.pipeline.orchestrator import _downscale_for_vlm  # noqa: E402

DB_PATH = REPO / "data/guardian.db"
CONFIG_PATH = REPO / "tools/pipeline/config.json"
SCHEMA_PATH = REPO / "tools/pipeline/schema.json"


def _sample_rows(camera_id: str, tier: str, since: str, limit: int, seed: int) -> list[dict]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, ts, image_path, share_worth, image_quality
        FROM image_archive
        WHERE camera_id = ? AND share_worth = ? AND ts >= ?
          AND image_path IS NOT NULL
        """,
        (camera_id, tier, since),
    ).fetchall()
    conn.close()
    rows = [dict(r) for r in rows]
    rng = random.Random(seed)
    rng.shuffle(rows)
    return rows[:limit]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prompt-file", required=True, help="Candidate prompt.md to test")
    ap.add_argument("--camera", default="s7-cam")
    ap.add_argument("--strong-n", type=int, default=40)
    ap.add_argument("--decent-n", type=int, default=40)
    ap.add_argument("--decent-since", default="2026-07-26",
                     help="decent-tier images are only retained 7 days; default matches that window")
    ap.add_argument("--strong-since", default="2026-07-13",
                     help="strong-tier images are retained 365 days; default is the low-strictness window start")
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()

    prompt_template = Path(args.prompt_file).read_text()
    schema = json.loads(SCHEMA_PATH.read_text())
    cfg = json.loads(CONFIG_PATH.read_text())
    camera_context = cfg["cameras"][args.camera]["context"]

    strong_rows = _sample_rows(args.camera, "strong", args.strong_since, args.strong_n, args.seed)
    decent_rows = _sample_rows(args.camera, "decent", args.decent_since, args.decent_n, args.seed)

    print(f"Sampled {len(strong_rows)} strong rows (since {args.strong_since}), "
          f"{len(decent_rows)} decent rows (since {args.decent_since})")
    print(f"Using prompt file: {args.prompt_file}")
    print()

    results = []
    for label, rows in (("strong", strong_rows), ("decent", decent_rows)):
        for i, row in enumerate(rows):
            # image_path is stored relative to "data/" (e.g. "archive/2026-08/s7-cam/...")
            candidate_path = REPO / "data" / row["image_path"]
            if not candidate_path.exists():
                print(f"  [{label} {i+1}/{len(rows)}] MISSING FILE: {row['image_path']} — skipped")
                continue
            # Archived files are full-resolution originals — the live pipeline
            # always downscales to vlm_input_long_edge_px before calling the
            # VLM (orchestrator.py:526). Skipping this step feeds the model a
            # meaningfully different image than production ever does, which
            # was caught 02-Aug-2026 when a first pass of this script (without
            # the downscale) produced spurious sharp->soft flips that had
            # nothing to do with the prompt change being tested.
            image_bytes = _downscale_for_vlm(
                candidate_path.read_bytes(), cfg.get("vlm_input_long_edge_px", 768)
            )
            t0 = time.monotonic()
            try:
                result = enrich(
                    image_bytes=image_bytes,
                    camera_name=args.camera,
                    camera_context=camera_context,
                    lm_base=cfg["lm_studio_base"],
                    model_id=cfg["vlm_model_id"],
                    prompt_template=prompt_template,
                    schema=schema,
                    max_tokens=cfg.get("vlm_max_tokens", 600),
                    temperature=cfg.get("vlm_temperature", 0.2),
                    timeout=cfg.get("vlm_timeout_seconds", 300),
                )
            except Exception as e:
                print(f"  [{label} {i+1}/{len(rows)}] ERROR: {type(e).__name__}: {e}")
                continue
            dt = time.monotonic() - t0
            new_meta = result["metadata"]
            old_sw = row["share_worth"]
            new_sw = new_meta.get("share_worth")
            _rank = {"skip": 0, "decent": 1, "strong": 2}
            if new_sw not in _rank:
                moved = "unknown"
            elif _rank[new_sw] > _rank[old_sw]:
                moved = "UP"
            elif _rank[new_sw] < _rank[old_sw]:
                moved = "DOWN"
            else:
                moved = "same"
            print(f"  [{label} {i+1}/{len(rows)}] id={row['id']} ts={row['ts']} "
                  f"{dt:.1f}s old={old_sw} new={new_sw} new_score={new_meta.get('overall_score')} [{moved}]")
            results.append({
                "id": row["id"], "ts": row["ts"], "old_tier": old_sw,
                "new_tier": new_sw, "new_score": new_meta.get("overall_score"),
                "moved": moved,
            })

    print()
    print("=== SUMMARY ===")
    for old_tier in ("strong", "decent"):
        subset = [r for r in results if r["old_tier"] == old_tier]
        if not subset:
            continue
        from collections import Counter
        dist = Counter(r["new_tier"] for r in subset)
        print(f"old={old_tier} (n={len(subset)}) -> new distribution: {dict(dist)}")

    out_path = Path("/private/tmp/claude-501/-Users-macmini-GitHub-farm-guardian/"
                     "1f06ed76-71ac-4fa8-9ab5-d956b3d5d393/scratchpad/replay-vlm-prompt-results.json")
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nFull results written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
