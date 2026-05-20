#!/usr/bin/env python3
"""Codex-backed curation + caption for the s7-daily reel (PILOT, standalone).

Why this exists
---------------
Two complaints drove this: (1) the auto-generated reel captions are weak, and
(2) reels are the only thing worth posting, so the frame selection matters.
The local LM Studio model is needed full-time for per-frame bird judging at
capture, so we don't want to lean on it for the once-a-day caption synthesis.

This script puts the idle OpenAI Codex subscription (gpt-5.5, oauth, configured
in ~/.codex/config.toml) to work on the *judgment* layer only:
  - prune obviously weak / redundant frames from the selector's candidate set
  - write one warm, on-brand caption

It is deliberately a PILOT and intentionally inert:
  - It does NOT reorder frames. The s7 lane is a fixed-angle TIME-LAPSE; the
    chronological arc *is* the value. Codex only says which frames to DROP;
    survivors keep the selector's chronological order.
  - It does NOT stitch, post, or touch IG. It writes a plan JSON and prints it.
  - codex runs read-only (its default sandbox) and cannot modify any file.

Run:
    python3 tools/pipeline/codex_reel_curator.py            # uses live DB, now
    python3 tools/pipeline/codex_reel_curator.py --out /tmp/plan.json

Output JSON shape:
    {"keep_ids":[int,...],          # chronological, ready for the stitcher
     "drop_ids":[int,...],
     "caption":"...",
     "reasoning":"...",
     "candidate_count":int,
     "source":"codex"|"fallback",
     "generated_at":"<iso>"}
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.pipeline.ig_selection import select_s7_daily_reel_gems  # noqa: E402

log = logging.getLogger("codex_reel_curator")

# Fields handed to codex per frame. Pure metadata — codex never sees pixels;
# the per-frame VLM pass already scored these at capture time.
META_FIELDS = (
    "ts", "bird_count", "scene", "activity", "lighting",
    "composition", "share_worth", "any_special_chick",
    "apparent_age_days", "discord_reactions",
)

# JSON Schema passed to `codex exec --output-schema` so the final message is
# guaranteed to match this shape (no fragile prose parsing).
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "drop_ids": {"type": "array", "items": {"type": "integer"}},
        "caption": {"type": "string"},
        "reasoning": {"type": "string"},
    },
    "required": ["drop_ids", "caption", "reasoning"],
    "additionalProperties": False,
}

BRAND_RULES = (
    "BRAND RULES (hard):\n"
    "- The brand is adorable baby birds / cozy farm life. Warm, genuine, "
    "specific to what's actually in the frames. NOT cold tech-marketing.\n"
    "- NEVER frame the camera as a security / predator-detection system. No "
    "\"watching for hawks\" type lines. A predator on camera means a dead bird, "
    "not content.\n"
    "- Do not mention cameras, AI, or technology.\n"
    "- Do not dramatize loss or death.\n"
    "- Hashtags: include up to 6 real, common ones (e.g. #babychicks #farmlife "
    "#chickensofinstagram #homestead). No invented or branded tags.\n"
)


def _fallback_caption(meta: list[dict]) -> str:
    """Deterministic caption when codex is unavailable. Plain, never wrong."""
    ages = [m["apparent_age_days"] for m in meta
            if isinstance(m.get("apparent_age_days"), int) and m["apparent_age_days"] >= 0]
    if ages:
        return (f"A day in the coop with the {min(ages)}-day-olds. 🐥 "
                "#babychicks #farmlife #chickensofinstagram #homestead")
    return ("A day in the coop. 🐥 "
            "#babychicks #farmlife #chickensofinstagram #homestead")


def _fetch_metadata(db_path: Path, gem_ids: list[int]) -> list[dict]:
    """Return per-gem metadata in the same (chronological) order as gem_ids."""
    if not gem_ids:
        return []
    cols = ", ".join(("id",) + META_FIELDS)
    placeholders = ",".join("?" * len(gem_ids))
    with sqlite3.connect(str(db_path)) as c:
        c.row_factory = sqlite3.Row
        rows = {
            r["id"]: dict(r)
            for r in c.execute(
                f"SELECT {cols} FROM image_archive WHERE id IN ({placeholders})",
                gem_ids,
            ).fetchall()
        }
    # Preserve the selector's chronological ordering.
    return [rows[g] for g in gem_ids if g in rows]


def _build_prompt(meta: list[dict]) -> str:
    candidates = [
        {"id": m["id"], **{k: m.get(k) for k in META_FIELDS}}
        for m in meta
    ]
    return (
        "You are curating a short Instagram REEL for a family farm account, "
        "@pawel_and_pawleen. The reel is a fixed-camera TIME-LAPSE of the "
        "chicken coop over one day. The frames will play in chronological "
        "order — you do NOT choose the order. A vision model already scored "
        "each frame; you work from this metadata, not the images.\n\n"
        f"{BRAND_RULES}\n"
        "CANDIDATE FRAMES (already in chronological order):\n"
        f"{json.dumps(candidates, default=str)}\n\n"
        "YOUR JOB:\n"
        "1. Pick which frames to DROP — only clearly redundant or low-value "
        "ones (e.g. near-duplicate empty/none-visible frames). Keep the "
        "time-lapse dense; when in doubt, keep a frame. Drop conservatively.\n"
        "2. Write ONE warm Instagram caption (1-2 sentences) for the whole "
        "reel, grounded in what the frames actually show, plus hashtags.\n\n"
        "Return drop_ids (the ids to remove), caption, and a one-sentence "
        "reasoning. Do not return an ordering."
    )


CAPTION_BODY_SCHEMA = {
    "type": "object",
    "properties": {"caption_body": {"type": "string"}},
    "required": ["caption_body"],
    "additionalProperties": False,
}


def _run_codex(prompt: str, *, timeout: int = 240, schema: dict = OUTPUT_SCHEMA) -> dict | None:
    """Call `codex exec` with a schema-enforced output. None on any failure."""
    with tempfile.TemporaryDirectory() as td:
        schema_path = Path(td) / "schema.json"
        out_path = Path(td) / "last.json"
        schema_path.write_text(json.dumps(schema))
        cmd = [
            "codex", "exec",
            "--skip-git-repo-check",
            "--output-schema", str(schema_path),
            "--output-last-message", str(out_path),
        ]
        try:
            proc = subprocess.run(
                cmd, input=prompt, text=True, capture_output=True,
                timeout=timeout, cwd=str(REPO_ROOT),
            )
        except subprocess.TimeoutExpired:
            log.warning("codex exec timed out after %ss", timeout)
            return None
        if proc.returncode != 0:
            log.warning("codex exec failed (rc=%s): %s",
                        proc.returncode, proc.stderr[-400:])
            return None
        try:
            return json.loads(out_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("codex output unparseable (%s): %r",
                        exc, proc.stdout[-400:])
            return None


def generate_caption_body(
    drafts: list[str],
    farm_context: str = "",
    *,
    timeout: int = 120,
    log: logging.Logger | None = None,
) -> str | None:
    """Codex (gpt-5.5) writes ONE warm reel caption BODY from per-frame VLM
    drafts. Returns the body with NO hashtags — the caller appends those via
    the verified hashtags.yml library so the brand safety net stays engaged.
    Returns None on any failure so the caller can fall back to LM Studio.

    This is the function `daily_reel_runner._generate_reel_caption` calls to
    move caption synthesis off the local model (kept busy judging birds) and
    onto the otherwise-idle OpenAI Codex subscription.
    """
    _log = log or globals()["log"]
    if not drafts:
        return None
    drafts_block = "\n".join(f"- {d}" for d in drafts[:20])
    context_block = (
        "Recent farm diary (use for named birds, current hatches, or one "
        f"concrete win — reference concretely when it fits):\n{farm_context}\n\n"
        if farm_context else ""
    )
    prompt = (
        "You are writing the caption for an Instagram REEL from a small family "
        "farm, @pawel_and_pawleen — a fixed-camera time-lapse of the chicken "
        "coop over a day.\n\n"
        f"{BRAND_RULES}\n"
        f"{context_block}"
        "Frame descriptions (chronological):\n"
        f"{drafts_block}\n\n"
        "Write ONE warm caption BODY: 1-2 sentences, specific to what's in the "
        "frames, genuine (not tech-marketing). NO hashtags (those are added "
        "separately). Do not mention cameras, AI, or technology."
    )
    result = _run_codex(prompt, timeout=timeout, schema=CAPTION_BODY_SCHEMA)
    if not result:
        return None
    body = (result.get("caption_body") or "").strip()
    if body:
        _log.info("codex: caption body %r", body[:80])
    return body or None


def curate(db_path: Path, scheduled_cfg: dict, now: datetime | None = None) -> dict:
    gem_ids = select_s7_daily_reel_gems(db_path, scheduled_cfg, now)
    meta = _fetch_metadata(db_path, gem_ids)
    candidate_ids = [m["id"] for m in meta]
    generated_at = (now or datetime.now(timezone.utc)).isoformat()

    if not candidate_ids:
        return {"keep_ids": [], "drop_ids": [], "caption": "",
                "reasoning": "no candidates", "candidate_count": 0,
                "source": "fallback", "generated_at": generated_at}

    min_frames = int(scheduled_cfg.get("s7_daily_reel_min_frames", 12))
    result = _run_codex(_build_prompt(meta))

    if result is None:
        return {"keep_ids": candidate_ids, "drop_ids": [],
                "caption": _fallback_caption(meta),
                "reasoning": "codex unavailable; kept all frames",
                "candidate_count": len(candidate_ids),
                "source": "fallback", "generated_at": generated_at}

    drop = {int(x) for x in result.get("drop_ids", []) if int(x) in set(candidate_ids)}
    keep_ids = [g for g in candidate_ids if g not in drop]  # chronological

    # Safety: never let codex prune below the lane's minimum frame count.
    if len(keep_ids) < min_frames:
        log.info("codex dropped too many (%d left < min %d); keeping all",
                 len(keep_ids), min_frames)
        keep_ids, drop = candidate_ids, set()

    return {
        "keep_ids": keep_ids,
        "drop_ids": sorted(drop),
        "caption": (result.get("caption") or _fallback_caption(meta)).strip(),
        "reasoning": (result.get("reasoning") or "").strip(),
        "candidate_count": len(candidate_ids),
        "source": "codex",
        "generated_at": generated_at,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="PILOT: codex curation+caption for s7-daily reel (dry-run)")
    ap.add_argument("--out", type=Path, help="write plan JSON here (also printed)")
    ap.add_argument("--now", help="override 'now' as ISO8601 for testing")
    args = ap.parse_args()

    cfg = json.loads((REPO_ROOT / "tools/pipeline/config.json").read_text())
    db_path = REPO_ROOT / cfg["guardian_db_path"]
    scheduled_cfg = cfg.get("instagram", {}).get("scheduled", {})
    now = datetime.fromisoformat(args.now) if args.now else None

    plan = curate(db_path, scheduled_cfg, now)
    blob = json.dumps(plan, indent=2)
    if args.out:
        args.out.write_text(blob)
    print(blob)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
