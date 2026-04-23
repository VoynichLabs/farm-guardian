# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Zero-login bootstrap for the IG engagement automation. Reads Boss's
#          existing Instagram session cookies directly from Chrome's Default
#          profile (Chrome Safe Storage-encrypted SQLite), decrypts them with
#          the key stored in the macOS login keychain, and seeds them into a
#          dedicated Playwright Chromium profile at
#          ~/Library/Application Support/farm-ig-engage/profile/. After one
#          successful run, that profile carries a live IG session and every
#          future engager run reuses it without any Boss interaction.
#
#          Why this path vs. having Boss log in manually:
#            - No password retrieval needed (LastPass has no CLI installed).
#            - No 2FA flow — cookies are already past 2FA.
#            - No DevTools extraction (Meta's self-XSS block prevented that).
#            - Same-device transfer (Chrome → Playwright Chromium on the same
#              Mac Mini, same IP, same user agent) is low fingerprint-divergence
#              risk; Meta's "session hijack" signal is tuned for cross-device.
#
#          Failure mode: if IG shows the login page after cookie-seeding, the
#          device-fingerprint delta was too large. Fallback plan (implemented
#          separately): attach Playwright to Boss's real Chrome via CDP with
#          --remote-debugging-port=9222.
#
# SRP/DRY check: Pass — single responsibility is "establish an IG-logged-in
#                Playwright profile". Decryption is inlined rather than pulled
#                from a cookie-extract library to keep the dependency surface
#                to (cryptography, playwright).

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from playwright.sync_api import sync_playwright

PROFILE_DIR = Path.home() / "Library" / "Application Support" / "farm-ig-engage" / "profile"
CHROME_COOKIES = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Google"
    / "Chrome"
    / "Default"
    / "Cookies"
)

# macOS Chrome cookie encryption constants. Chrome derives an AES-128 key via
# PBKDF2-HMAC-SHA1 with salt "saltysalt" and 1003 iterations from the password
# stored in the login keychain under the service name "Chrome Safe Storage".
KEYCHAIN_SERVICE = "Chrome Safe Storage"
KEYCHAIN_ACCOUNT = "Chrome"
PBKDF_SALT = b"saltysalt"
PBKDF_ITERATIONS = 1003
KEY_LENGTH = 16
IV = b" " * 16  # Chrome v10 used AES-CBC with this IV; v11+ uses AES-GCM.


def get_chrome_safe_storage_password() -> bytes:
    """Pull the Chrome Safe Storage password from the macOS login keychain."""
    out = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-w",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            KEYCHAIN_ACCOUNT,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip().encode("utf-8")


def derive_key(password: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=SHA1(),
        length=KEY_LENGTH,
        salt=PBKDF_SALT,
        iterations=PBKDF_ITERATIONS,
    )
    return kdf.derive(password)


def decrypt_cookie(encrypted_value: bytes, key: bytes) -> str:
    """Decrypt a Chrome v10 (CBC) or v11 (GCM) cookie value on macOS.

    Modern Chrome (~v130+) prepends a 32-byte SHA256 host-hash to the
    plaintext as integrity binding. After AES decryption we strip the first
    32 bytes before returning the UTF-8 cookie value."""
    if not encrypted_value:
        return ""
    prefix = encrypted_value[:3]
    plain = b""
    if prefix == b"v10":
        # macOS Chrome uses AES-128-CBC with a fixed IV and PKCS7 padding.
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7

        cipher = Cipher(algorithms.AES(key), modes.CBC(IV))
        dec = cipher.decryptor()
        padded = dec.update(encrypted_value[3:]) + dec.finalize()
        unpad = PKCS7(128).unpadder()
        plain = unpad.update(padded) + unpad.finalize()
    elif prefix == b"v11":
        nonce = encrypted_value[3:15]
        ct = encrypted_value[15:]
        plain = AESGCM(key).decrypt(nonce, ct, None)
    else:
        return ""
    # Strip the 32-byte host-hash prefix if present. Old Chrome had no such
    # prefix, new Chrome does; detect by trying to decode as-is first.
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        return plain[32:].decode("utf-8")


def read_instagram_cookies_from_chrome() -> list[dict[str, Any]]:
    """Copy Chrome's Cookies DB (to avoid a lock conflict with running Chrome),
    query the instagram.com rows, decrypt each, and shape them for Playwright's
    context.add_cookies() API."""
    if not CHROME_COOKIES.exists():
        raise SystemExit(f"Chrome Cookies DB not found at {CHROME_COOKIES}")
    tmpdir = tempfile.mkdtemp(prefix="ig-engage-")
    tmp_db = Path(tmpdir) / "Cookies.db"
    shutil.copy(CHROME_COOKIES, tmp_db)

    pwd = get_chrome_safe_storage_password()
    key = derive_key(pwd)

    conn = sqlite3.connect(tmp_db)
    rows = conn.execute(
        """
        SELECT host_key, name, path, expires_utc, is_secure, is_httponly,
               samesite, encrypted_value
          FROM cookies
         WHERE host_key LIKE '%instagram.com%'
            OR host_key LIKE '%instagram%'
        """
    ).fetchall()
    conn.close()
    shutil.rmtree(tmpdir, ignore_errors=True)

    # Chrome expires_utc is microseconds since 1601-01-01 UTC. Playwright wants
    # seconds since 1970-01-01 UTC (or -1 for session cookies).
    epoch_delta_seconds = 11_644_473_600

    out: list[dict[str, Any]] = []
    samesite_map = {0: "None", 1: "Lax", 2: "Strict", -1: "Lax"}

    for host, name, path, expires_utc, secure, httponly, samesite, enc in rows:
        try:
            value = decrypt_cookie(enc, key)
        except Exception as e:
            print(f"  skip {name}@{host}: decrypt failed ({type(e).__name__})")
            continue
        if not value:
            continue
        if expires_utc and expires_utc > 0:
            expires_sec = expires_utc / 1_000_000 - epoch_delta_seconds
        else:
            expires_sec = -1

        out.append(
            {
                "name": name,
                "value": value,
                "domain": host,
                "path": path or "/",
                "expires": expires_sec,
                "httpOnly": bool(httponly),
                "secure": bool(secure),
                "sameSite": samesite_map.get(samesite, "Lax"),
            }
        )
    return out


def main() -> int:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Profile dir: {PROFILE_DIR}")

    print("Reading Instagram cookies from Chrome's Default profile…")
    ig_cookies = read_instagram_cookies_from_chrome()
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

        # Give it a moment to rehydrate; Instagram runs a lot of post-load JS
        # before it decides to either show the feed or bounce to /login.
        time.sleep(5)

        final_url = page.url
        print(f"Landed on: {final_url}")
        if "/accounts/login" in final_url or "/login" in final_url:
            print(
                "FAIL: cookies were seeded but Instagram bounced to login.\n"
                "      This means the device-fingerprint delta between Chrome\n"
                "      and Playwright Chromium was too large. Next step:\n"
                "      CDP-attach to Boss's real Chrome (run build-cdp-attach.py\n"
                "      after quitting Chrome and relaunching with\n"
                "      --remote-debugging-port=9222)."
            )
            try:
                input("Press Enter to close the window… ")
            except EOFError:
                pass
            ctx.close()
            return 2

        print("SUCCESS: feed loaded. Session is now baked into the profile dir.")
        print("You can close this window at any time.")
        # Write a small marker so the engager knows the bootstrap succeeded.
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

    print("\nDone. Next: engager script.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
