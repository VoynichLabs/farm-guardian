# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Zero-login bootstrap for the Nextdoor automation. Near-clone of
#          the IG engage bootstrap, with three deltas:
#            - filters Chrome cookies by host LIKE '%nextdoor%'
#            - seeds a dedicated Playwright Chromium profile at
#              ~/Library/Application Support/farm-nextdoor/profile/
#            - lands on https://nextdoor.com/news_feed/ and confirms we're
#              not bounced to a /login or /register URL
#
#          Boss logs into Nextdoor via Apple Sign-In, so there is no
#          username/password pair on this machine and no alternative path —
#          the Chrome cookies are the session. See
#          ~/bubba-workspace/skills/farm-nextdoor-engage/SKILL.md for the
#          full picture.
#
# SRP/DRY check: Pass — single responsibility is "establish a Nextdoor-
#                logged-in Playwright profile". All crypto is imported from
#                tools/chrome_session/decrypt.py; no duplication.

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_REPO_TOOLS = Path(__file__).resolve().parents[1]
if str(_REPO_TOOLS) not in sys.path:
    sys.path.insert(0, str(_REPO_TOOLS))

from playwright.sync_api import sync_playwright  # noqa: E402

from chrome_session.decrypt import read_cookies_for_hosts  # noqa: E402

PROFILE_DIR = Path.home() / "Library" / "Application Support" / "farm-nextdoor" / "profile"
MARKER = PROFILE_DIR.parent / "bootstrap-ok.json"

# Session-identifying cookies — at least one must be present for us to
# consider this a real logged-in session. ndbr_idt is the RS256 JWT that
# carries the real identity; ndbr_at is the shorter access token.
REQUIRED_ANY = {"ndbr_idt", "ndbr_at"}


def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Profile dir: {PROFILE_DIR}")

    print("Reading Nextdoor cookies from Chrome's Default profile…")
    cookies = read_cookies_for_hosts(["%nextdoor%"])
    names = {c["name"] for c in cookies}
    print(f"  loaded {len(cookies)} Nextdoor cookies; session cookies in set: "
          f"{sorted(n for n in names if n in REQUIRED_ANY)}")
    if not (names & REQUIRED_ANY):
        print("  ABORT: no ndbr_idt or ndbr_at cookie. Log into Nextdoor in Chrome first.")
        return 1

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        ctx.add_cookies(cookies)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        print("Opening nextdoor.com/news_feed/ — watch the window.")
        page.goto("https://nextdoor.com/news_feed/", wait_until="domcontentloaded")
        time.sleep(6)  # Nextdoor has a slower post-login hydration than IG.

        final_url = page.url
        print(f"Landed on: {final_url}")
        bad_paths = ("/login", "/register", "/sign_up", "/sign-in", "/signin")
        if any(b in final_url for b in bad_paths):
            print(
                "FAIL: cookies were seeded but Nextdoor bounced to a login-ish URL.\n"
                "      Possibilities: (a) the fingerprint delta between Chrome and\n"
                "      Playwright Chromium was too large; (b) the SSO session needs\n"
                "      a round-trip to Apple we can't replay; (c) a feed-specific URL\n"
                "      is needed. First try a different entry path — e.g. the root\n"
                "      '/' or '/feed' — before giving up on the cookie-lift route."
            )
            try:
                input("Press Enter to close the window… ")
            except EOFError:
                pass
            ctx.close()
            return 2

        print("SUCCESS: Nextdoor feed loaded. Session baked into the profile dir.")
        MARKER.write_text(
            json.dumps(
                {
                    "ok": True,
                    "at": int(time.time()),
                    "cookies_seeded": len(cookies),
                    "landing_url": final_url,
                },
                indent=2,
            )
        )
        try:
            input("Press Enter to close the Chromium window… ")
        except EOFError:
            pass
        ctx.close()

    print("\nDone. Next: first attended session to capture Nextdoor UI selectors "
          "(see ~/bubba-workspace/skills/farm-nextdoor-engage/SKILL.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
