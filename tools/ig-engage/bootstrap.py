# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Zero-login bootstrap for the IG engagement automation. Reads Boss's
#          existing Instagram session cookies from Chrome's Default profile
#          via the shared tools/chrome_session/decrypt module, seeds them
#          into a dedicated Playwright Chromium persistent profile at
#          ~/Library/Application Support/farm-ig-engage/profile/, and
#          verifies instagram.com loads the feed (not /accounts/login).
#
#          Chrome cookie decrypt internals (keychain password, PBKDF2 salt/
#          iterations, v10 CBC / v11 GCM, 32-byte host-hash prefix strip,
#          expires_utc conversion) live in tools/chrome_session/decrypt.py
#          now, shared with the Nextdoor bootstrap. Do NOT re-inline that
#          code here — keep the one source of truth.
#
# SRP/DRY check: Pass — single responsibility is "establish an IG-logged-in
#                Playwright profile". All crypto reused from chrome_session.

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Make tools/ importable as a parent so we can do `from chrome_session ...`.
_REPO_TOOLS = Path(__file__).resolve().parents[1]
if str(_REPO_TOOLS) not in sys.path:
    sys.path.insert(0, str(_REPO_TOOLS))

from playwright.sync_api import sync_playwright  # noqa: E402

from chrome_session.decrypt import read_cookies_for_hosts  # noqa: E402

PROFILE_DIR = Path.home() / "Library" / "Application Support" / "farm-ig-engage" / "profile"


def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Profile dir: {PROFILE_DIR}")

    print("Reading Instagram cookies from Chrome's Default profile…")
    ig_cookies = read_cookies_for_hosts(["%instagram%"])
    session_present = any(c["name"] == "sessionid" for c in ig_cookies)
    print(
        f"  loaded {len(ig_cookies)} IG cookies "
        f"(sessionid present: {session_present})"
    )
    if not session_present:
        print("  ABORT: no sessionid. Log into Instagram in Chrome first.")
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
        ctx.add_cookies(ig_cookies)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        print("Opening instagram.com… watch the window.")
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        time.sleep(5)

        final_url = page.url
        print(f"Landed on: {final_url}")
        if "/accounts/login" in final_url or "/login" in final_url:
            print(
                "FAIL: cookies were seeded but Instagram bounced to login.\n"
                "      The device-fingerprint delta between Chrome and Playwright\n"
                "      Chromium was too large. Fallback: CDP-attach to Boss's\n"
                "      real Chrome via --remote-debugging-port=9222."
            )
            try:
                input("Press Enter to close the window… ")
            except EOFError:
                pass
            ctx.close()
            return 2

        print("SUCCESS: feed loaded. Session is now baked into the profile dir.")
        marker = PROFILE_DIR.parent / "bootstrap-ok.json"
        marker.write_text(
            json.dumps(
                {
                    "ok": True,
                    "at": int(time.time()),
                    "cookies_seeded": len(ig_cookies),
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

    print("\nDone. Next: engager script (tools/ig-engage/engage.py).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
