# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Launch Playwright codegen against an EXISTING persistent Chromium
#          profile (one of the session dirs bootstrapped by IG or Nextdoor or
#          any future track), so Boss can click through a live logged-in
#          session and Playwright emits the real aria/role/text selectors as
#          Python code. Those selectors then land in primitives.py for
#          whichever platform we're instrumenting.
#
#          Without this wrapper, `playwright codegen` opens a fresh browser
#          with no session — useless for authenticated surfaces. With this
#          wrapper, we reuse the bootstrapped profile so codegen runs inside
#          the same logged-in context the engager uses.
#
#          Usage:
#              python tools/chrome_session/codegen.py --profile ig
#              python tools/chrome_session/codegen.py --profile nextdoor
#              python tools/chrome_session/codegen.py --profile nextdoor \
#                  --url https://nextdoor.com/news_feed/
#
# SRP/DRY check: Pass — one responsibility: attach Playwright's recorder to
#                a named persistent profile. All known profiles registered
#                in PROFILES; add new tracks by extending that map.

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Map of short profile names → (persistent profile dir, default URL).
# Add a new track here when its bootstrap ships.
PROFILES: dict[str, tuple[Path, str]] = {
    "ig": (
        Path.home() / "Library" / "Application Support" / "farm-ig-engage" / "profile",
        "https://www.instagram.com/",
    ),
    "nextdoor": (
        Path.home() / "Library" / "Application Support" / "farm-nextdoor" / "profile",
        "https://nextdoor.com/news_feed/",
    ),
}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Launch Playwright codegen against a bootstrapped profile."
    )
    ap.add_argument("--profile", required=True, choices=sorted(PROFILES.keys()),
                    help="Which bootstrapped profile dir to reuse.")
    ap.add_argument("--url", default=None,
                    help="URL to open; defaults to the profile's home.")
    ap.add_argument("--target", default="python",
                    choices=["python", "python-async", "javascript", "playwright-test"],
                    help="Codegen output language.")
    args = ap.parse_args()

    profile_dir, default_url = PROFILES[args.profile]
    url = args.url or default_url

    if not profile_dir.exists():
        print(
            f"ERROR: profile dir {profile_dir} does not exist.\n"
            f"       Run the bootstrap for '{args.profile}' first.",
            file=sys.stderr,
        )
        return 1

    print(f"Launching codegen against {profile_dir}")
    print(f"URL: {url}")
    print("Every click/type/submit in the window becomes code in the side panel.")
    print("Copy the selector strings you need into the matching primitives.py.\n")

    # Find the playwright binary in the current venv, fall back to PATH.
    pw_bin = Path(sys.executable).parent / "playwright"
    if not pw_bin.exists():
        pw_bin = "playwright"  # type: ignore[assignment]

    cmd = [
        str(pw_bin),
        "codegen",
        "--browser", "chromium",
        "--target", args.target,
        # --load-storage takes a state file; for persistent context we use
        # --user-data-dir instead (added in Playwright 1.34+).
        "--user-data-dir", str(profile_dir),
        url,
    ]

    try:
        return subprocess.call(cmd)
    except FileNotFoundError:
        print("ERROR: playwright CLI not found. Install with:\n"
              "       pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
