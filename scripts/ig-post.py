#!/usr/bin/env python3
# Author: Claude Opus 4.7 (1M context)
# Date: 20-April-2026 (V2.0 photo CLI 20-Apr-2026; --mode story/reel dispatch 20-Apr-2026)
# PURPOSE: Manually post a curated gem to Instagram @pawel_and_pawleen via
#          the Graph API. Originally the V2.0 thin wrapper around
#          post_gem_to_ig (replay of the manually-curl'd IG posts #1-#3);
#          extended for Phase 2/3 (stories + reels) to a mode-dispatched
#          CLI.
#
#          Modes (mutually exclusive; --mode photo is the default and
#          preserves back-compat with every prior invocation):
#            --mode photo   (default): --gem-id N --caption "..."
#                           → post_gem_to_ig
#            --mode story : --gem-id N
#                           → post_gem_to_story (9:16 crop, no caption)
#            --mode reel  : --gem-ids N,N,N... --caption "..."
#                           → stitch_gems_to_reel + post_reel_to_ig
#                           (Phase 3; wired in when reel_stitcher lands)
#
#          --dry-run stops before any git push AND before any Graph API
#          call. Pure stdlib (no venv needed): `python3 scripts/ig-post.py
#          ...` from anywhere on this Mac Mini. ig_poster.py and
#          git_helper.py are stdlib-only; reel_stitcher shells out to
#          ffmpeg (already a runtime dep) and uses cv2 (already pulled in
#          by the pipeline).
#
# SRP/DRY check: Pass — one responsibility: dispatch an IG post from the
#                CLI. All Graph API, git, and ffmpeg logic lives in
#                tools/pipeline/ig_poster.py, tools/pipeline/git_helper.py,
#                and tools/pipeline/reel_stitcher.py respectively. This
#                script is argparse + validation + a dispatch switch.

"""
ig-post.py — manually post a gem to Instagram @pawel_and_pawleen.

Usage:
  # Photo (default mode; --mode photo is implicit)
  scripts/ig-post.py --gem-id N --caption "..." [--dry-run]

  # Story (24-hour ephemeral, no caption, 9:16 vertical crop)
  scripts/ig-post.py --mode story --gem-id N [--dry-run]

  # Reel (stitch N gems into an MP4 and post as a REEL)
  scripts/ig-post.py --mode reel --gem-ids N,N,N,N,N,N --caption "..." [--dry-run]

Examples:
  # Dry-run a photo — predict the public media URL without publishing
  scripts/ig-post.py --gem-id 6849 --caption "Chick portrait." --dry-run

  # Real story
  scripts/ig-post.py --mode story --gem-id 6849

  # Reel from a 6-frame brooder burst
  scripts/ig-post.py --mode reel --gem-ids 6849,6850,6853,6858,6860,6863 \\
    --caption "$(cat caption.txt)"

Exit codes:
  0 — success (publish landed, or dry-run printed a valid payload)
  1 — runtime failure (DB, git, Graph API, ffmpeg) — see stderr for details
  2 — user input error (unknown gem-id, empty caption, caption too long,
                         mode-specific arg combination rejected)
  3 — credential missing in env / env file — see stderr for fix recipe
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Make tools/pipeline importable without requiring the venv.
sys.path.insert(0, str(REPO_ROOT))

_IG_CAPTION_MAX_CHARS = 2200
_REEL_MIN_GEMS = 2
_REEL_MAX_GEMS = 10


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _parse_gem_ids(raw: str) -> list[int]:
    """Parse a comma-separated list of ints. Rejects empties, negatives,
    and duplicates. Caller enforces length bounds."""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        raise ValueError("--gem-ids is empty")
    try:
        ids = [int(p) for p in parts]
    except ValueError as e:
        raise ValueError(f"--gem-ids contains a non-integer: {e}") from e
    if any(n <= 0 for n in ids):
        raise ValueError("--gem-ids must all be positive")
    if len(ids) != len(set(ids)):
        raise ValueError("--gem-ids contains duplicates")
    return ids


def _validate_mode_args(args: argparse.Namespace) -> tuple[bool, str]:
    """Per-mode required/forbidden arg check. Returns (ok, err_msg).

    argparse can't natively express "required iff --mode=X", so we run
    the mode-specific rules after parsing.
    """
    mode = args.mode
    if mode == "photo":
        if args.gem_id is None:
            return False, "--mode photo requires --gem-id"
        if not args.caption or not args.caption.strip():
            return False, "--mode photo requires --caption (non-empty)"
        if args.gem_ids:
            return False, "--gem-ids is for --mode reel; --mode photo takes --gem-id (singular)"
        if len(args.caption) > _IG_CAPTION_MAX_CHARS:
            return False, f"--caption is {len(args.caption)} chars; IG max is {_IG_CAPTION_MAX_CHARS}"
        if args.gem_id <= 0:
            return False, f"--gem-id must be positive, got {args.gem_id}"
    elif mode == "story":
        if args.gem_id is None:
            return False, "--mode story requires --gem-id"
        if args.caption:
            return False, "--mode story does not take --caption (Graph API rejects captions on stories)"
        if args.gem_ids:
            return False, "--mode story takes a single --gem-id, not --gem-ids"
        if args.gem_id <= 0:
            return False, f"--gem-id must be positive, got {args.gem_id}"
    elif mode == "reel":
        if not args.gem_ids:
            return False, "--mode reel requires --gem-ids (comma-separated)"
        if args.gem_id is not None:
            return False, "--mode reel takes --gem-ids (plural), not --gem-id"
        if not args.caption or not args.caption.strip():
            return False, "--mode reel requires --caption (non-empty)"
        if len(args.caption) > _IG_CAPTION_MAX_CHARS:
            return False, f"--caption is {len(args.caption)} chars; IG max is {_IG_CAPTION_MAX_CHARS}"
        try:
            ids = _parse_gem_ids(args.gem_ids)
        except ValueError as e:
            return False, str(e)
        if not (_REEL_MIN_GEMS <= len(ids) <= _REEL_MAX_GEMS):
            return False, (
                f"--gem-ids count {len(ids)} out of range "
                f"[{_REEL_MIN_GEMS}, {_REEL_MAX_GEMS}]"
            )
        # Stash the parsed list back on args for the dispatcher.
        args.gem_ids_parsed = ids
    else:
        # argparse's choices= should have caught this already.
        return False, f"unknown --mode {mode!r}"
    return True, ""


def _dispatch_photo(args: argparse.Namespace) -> dict:
    from tools.pipeline.ig_poster import post_gem_to_ig
    return post_gem_to_ig(
        gem_id=args.gem_id,
        full_caption=args.caption,
        db_path=args.db_path,
        farm_2026_repo_path=args.farm_2026_repo,
        dry_run=args.dry_run,
    )


def _dispatch_story(args: argparse.Namespace) -> dict:
    from tools.pipeline.ig_poster import post_gem_to_story
    return post_gem_to_story(
        gem_id=args.gem_id,
        db_path=args.db_path,
        farm_2026_repo_path=args.farm_2026_repo,
        dry_run=args.dry_run,
    )


def _dispatch_reel(args: argparse.Namespace) -> dict:
    """Phase 3 — stitch N gems into an MP4 and post as a reel.

    reel_stitcher.stitch_gems_to_reel writes the MP4 under data/reels/,
    then post_reel_to_ig commits it to farm-2026/public/photos/reels/
    and publishes via the Graph API.
    """
    from tools.pipeline.reel_stitcher import stitch_gems_to_reel
    from tools.pipeline.ig_poster import post_reel_to_ig

    gem_ids = args.gem_ids_parsed  # populated by _validate_mode_args
    reel_cfg = _load_reel_config()
    reel_mp4_path = stitch_gems_to_reel(
        gem_ids=gem_ids,
        db_path=args.db_path,
        config=reel_cfg,
    )
    return post_reel_to_ig(
        reel_mp4_path=reel_mp4_path,
        caption=args.caption,
        db_path=args.db_path,
        farm_2026_repo_path=args.farm_2026_repo,
        associated_gem_ids=gem_ids,
        dry_run=args.dry_run,
    )


def _load_reel_config() -> dict:
    """Pull the reels config block from tools/pipeline/config.json.

    Falls back to sensible defaults if the block is missing — the CLI
    should still work on a pipeline config that predates Phase 3.
    """
    cfg_path = REPO_ROOT / "tools" / "pipeline" / "config.json"
    defaults = {
        "output_root": "data/reels",
        "seconds_per_frame": 1.0,
        "crossfade_seconds": 0.15,
        "frames_per_reel_default": 6,
    }
    try:
        cfg = json.loads(cfg_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return defaults
    return {**defaults, **((cfg.get("instagram") or {}).get("reels") or {})}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ig-post.py",
        description="Post a gem to Instagram @pawel_and_pawleen (photo / story / reel).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mode",
        choices=["photo", "story", "reel"],
        default="photo",
        help="Post type. 'photo' (default) preserves V2.0 CLI back-compat.",
    )
    parser.add_argument(
        "--gem-id",
        type=int,
        default=None,
        help="Integer gem id from Guardian's image_archive table (photo/story modes).",
    )
    parser.add_argument(
        "--gem-ids",
        type=str,
        default=None,
        help=f"Comma-separated gem ids ({_REEL_MIN_GEMS}-{_REEL_MAX_GEMS}) for reel mode.",
    )
    parser.add_argument(
        "--caption",
        type=str,
        default=None,
        help="Full caption (photo/reel modes). Max 2200 chars. Rejected in story mode.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Predict the public media URL + caption without publishing or committing.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=REPO_ROOT / "data" / "guardian.db",
        help="Guardian SQLite DB path (default: data/guardian.db under the repo).",
    )
    parser.add_argument(
        "--farm-2026-repo",
        type=Path,
        default=Path.home() / "Documents" / "GitHub" / "farm-2026",
        help="farm-2026 repo root (default: ~/Documents/GitHub/farm-2026).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    # Path existence checks — shared across all modes.
    if not args.db_path.exists():
        print(f"error: --db-path not found: {args.db_path}", file=sys.stderr)
        return 2
    if not args.farm_2026_repo.exists():
        print(
            f"error: --farm-2026-repo not found: {args.farm_2026_repo}",
            file=sys.stderr,
        )
        return 2

    # Mode-specific required/forbidden validation.
    ok, err = _validate_mode_args(args)
    if not ok:
        print(f"error: {err}", file=sys.stderr)
        return 2

    dispatch = {
        "photo": _dispatch_photo,
        "story": _dispatch_story,
        "reel": _dispatch_reel,
    }

    try:
        from tools.pipeline.ig_poster import IGPosterError
    except ImportError as e:
        print(f"error: cannot import ig_poster: {e}", file=sys.stderr)
        return 1

    try:
        result = dispatch[args.mode](args)
    except IGPosterError as e:
        # Credential missing — misconfiguration, not a runtime glitch.
        print(f"error: {e}", file=sys.stderr)
        return 3
    except ImportError as e:
        # reel_stitcher may not exist yet on a pre-Phase-3 checkout.
        print(f"error: cannot import module for --mode {args.mode}: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"error: unexpected {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    # Machine-readable result on stdout.
    print(json.dumps(result, default=str, indent=2))

    if result.get("error"):
        print(f"\nFAILED: {result['error']}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\nDRY-RUN OK — no post was made. Drop --dry-run to publish.")
        return 0

    # Per-mode success marker check.
    success_key = {
        "photo": "media_id",
        "story": "story_id",
        "reel": "media_id",
    }[args.mode]
    if not result.get(success_key):
        print(f"\nFAILED: no {success_key} returned (unexpected)", file=sys.stderr)
        return 1

    print(f"\nPOSTED ({args.mode}): {result.get('permalink')}")
    if args.mode == "story":
        print(f"  story_id: {result.get('story_id')}")
    else:
        print(f"  media_id: {result.get('media_id')}")
    print(f"  posted_at: {result.get('posted_at')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
