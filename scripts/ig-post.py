#!/usr/bin/env python3
# Author: Claude Opus 4.7 (1M context)
# Date: 20-April-2026
# PURPOSE: Manually post a curated gem to Instagram @pawel_and_pawleen via
#          the Graph API. The V2.0 delivered capability of the ig_poster
#          pipeline: one command to replay what was done by hand for IG
#          posts #1–#3 (the manually-curl'd carousels from 2026-04-19/20).
#
#          Flow:
#            1. Look up the gem in Guardian's image_archive.
#            2. Copy the full-res JPEG into farm-2026/public/photos/brooder/
#               via git_helper (commit + push).
#            3. Create IG media container with the GitHub raw URL + the
#               caption supplied on the command line.
#            4. Poll container status until FINISHED.
#            5. Publish.
#            6. Fetch permalink; write it back to image_archive.ig_permalink.
#
#          --dry-run stops before step 2's git push and before any Graph
#          API call — useful for verifying which gem+caption would ship
#          without actually posting.
#
#          Pure stdlib (no venv needed): `python3 scripts/ig-post.py ...`
#          from anywhere on this Mac Mini. ig_poster.py and git_helper.py
#          are also stdlib-only (urllib, sqlite3, subprocess).
#
# SRP/DRY check: Pass — one responsibility: run one IG post from the CLI.
#                All Graph API and git logic lives in
#                tools/pipeline/ig_poster.py and tools/pipeline/git_helper.py
#                respectively. This script is a thin argparse + call wrapper.

"""
ig-post.py — manually post a gem to Instagram @pawel_and_pawleen.

Usage:
  scripts/ig-post.py --gem-id N --caption "..." [--dry-run]

Examples:
  # Dry-run: predict the raw URL without publishing
  scripts/ig-post.py --gem-id 6849 --caption "Chick portrait." --dry-run

  # Real post
  scripts/ig-post.py --gem-id 6849 --caption "$(cat caption.txt)"

  # Heredoc caption (multiline)
  scripts/ig-post.py --gem-id 6849 --caption "$(cat <<'EOF'
Brood around 11 days. Mixed heritage.

#babychicks #chickensofinstagram
EOF
)"

Exit codes:
  0 — success (publish landed, or dry-run printed a valid payload)
  1 — runtime failure (DB, git, Graph API) — see stderr for details
  2 — user input error (unknown gem-id, empty caption, caption too long)
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


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ig-post.py",
        description="Post a gem to Instagram @pawel_and_pawleen.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--gem-id",
        type=int,
        required=True,
        help="Integer gem id from Guardian's image_archive table.",
    )
    parser.add_argument(
        "--caption",
        type=str,
        required=True,
        help="Full caption (journal body + hashtags, if any). Max 2200 chars.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Predict the raw URL + caption without publishing or committing.",
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
    log = logging.getLogger("ig-post-cli")

    # Basic input validation — better error messages than letting the
    # underlying module raise.
    if not args.caption.strip():
        print("error: --caption cannot be empty or whitespace-only", file=sys.stderr)
        return 2
    if len(args.caption) > 2200:
        print(
            f"error: --caption is {len(args.caption)} chars; IG max is 2200",
            file=sys.stderr,
        )
        return 2
    if args.gem_id <= 0:
        print(f"error: --gem-id must be positive, got {args.gem_id}", file=sys.stderr)
        return 2
    if not args.db_path.exists():
        print(f"error: --db-path not found: {args.db_path}", file=sys.stderr)
        return 2
    if not args.farm_2026_repo.exists():
        print(
            f"error: --farm-2026-repo not found: {args.farm_2026_repo}",
            file=sys.stderr,
        )
        return 2

    # Import the module here so argparse errors don't need the venv.
    try:
        from tools.pipeline.ig_poster import post_gem_to_ig, IGPosterError
    except ImportError as e:
        print(f"error: cannot import ig_poster: {e}", file=sys.stderr)
        return 1

    try:
        result = post_gem_to_ig(
            gem_id=args.gem_id,
            full_caption=args.caption,
            db_path=args.db_path,
            farm_2026_repo_path=args.farm_2026_repo,
            dry_run=args.dry_run,
        )
    except IGPosterError as e:
        # Credential missing — this is a misconfiguration, not a runtime
        # glitch. Return code 3 so scripts can distinguish.
        print(f"error: {e}", file=sys.stderr)
        return 3

    # Print result as JSON for machine-readability and as human summary.
    print(json.dumps(result, indent=2))

    # Decide exit code from result
    if result.get("error"):
        print(f"\nFAILED: {result['error']}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\nDRY-RUN OK — no post was made. Drop --dry-run to publish.")
        return 0

    if not result.get("media_id"):
        # Non-error but no media_id — shouldn't happen on the success path
        # but defensive.
        print("\nFAILED: no media_id returned (unexpected)", file=sys.stderr)
        return 1

    print(f"\nPOSTED: {result['permalink']}")
    print(f"  media_id: {result['media_id']}")
    print(f"  posted_at: {result['posted_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
