# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Shared helpers for decrypting Chrome's cookies on macOS and shaping
#          them for Playwright's context.add_cookies(). Originally inlined in
#          tools/ig-engage/bootstrap.py (2026-04-23); extracted here so the
#          IG engager, the Nextdoor automation, and any future Chrome-
#          cookie-lift track all import from a single source of truth.
#
#          Scope: read-only from Chrome's Default profile on this machine.
#          Never writes to Chrome's DB. Callers pass host-name LIKE patterns
#          (e.g. ['%instagram%'] or ['%nextdoor%']) and receive a list of
#          Playwright-ready cookie dicts.
#
#          Chrome internals covered:
#            - "Chrome Safe Storage" password from the macOS login keychain
#            - PBKDF2-HMAC-SHA1, salt "saltysalt", 1003 iterations, 16-byte key
#            - v10 prefix → AES-128-CBC with fixed IV and PKCS7 padding
#            - v11 prefix → AES-GCM with 12-byte nonce after the prefix
#            - Modern Chrome (~v130+) prepends a 32-byte SHA256 host-hash to
#              plaintext; strip it if the UTF-8 decode fails as-is.
#            - expires_utc (microseconds since 1601-01-01 UTC) → Playwright's
#              seconds-since-1970 (or -1 for session cookies).
#
# SRP/DRY check: Pass — one file, one responsibility: "read + decrypt Chrome
#                cookies for a given host filter". No browser-launching, no
#                HTTP. That lives with each caller.

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

CHROME_COOKIES = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Google"
    / "Chrome"
    / "Default"
    / "Cookies"
)

KEYCHAIN_SERVICE = "Chrome Safe Storage"
KEYCHAIN_ACCOUNT = "Chrome"

PBKDF_SALT = b"saltysalt"
PBKDF_ITERATIONS = 1003
KEY_LENGTH = 16
# v10 on macOS uses AES-CBC with a fixed 16-byte IV of ASCII spaces.
_V10_IV = b" " * 16

# Cookies in Chrome store expires_utc as microseconds since 1601-01-01 UTC.
# Playwright wants seconds since Unix epoch (or -1 for session cookies).
_EPOCH_DELTA_SECONDS = 11_644_473_600

# Chrome's samesite column maps to Playwright/W3C values.
_SAMESITE_MAP: dict[int, str] = {
    -1: "Lax",
    0: "None",
    1: "Lax",
    2: "Strict",
}


def get_chrome_safe_storage_password() -> bytes:
    """Pull the Chrome Safe Storage password out of the macOS login keychain.

    First invocation will trigger a keychain-permission dialog on the user's
    screen; clicking "Always Allow" makes subsequent runs silent. The password
    is the AES-PBKDF2 input — do NOT log it."""
    result = subprocess.run(
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
    return result.stdout.strip().encode("utf-8")


def derive_key(password: bytes) -> bytes:
    """Derive Chrome's 16-byte AES key from the keychain password."""
    kdf = PBKDF2HMAC(
        algorithm=SHA1(),
        length=KEY_LENGTH,
        salt=PBKDF_SALT,
        iterations=PBKDF_ITERATIONS,
    )
    return kdf.derive(password)


def decrypt_cookie(encrypted_value: bytes, key: bytes) -> str:
    """Decrypt a single Chrome cookie value (v10 CBC or v11 GCM).

    Modern Chrome (~v130+) prepends a 32-byte SHA256 host-hash to the
    plaintext as integrity binding; if the UTF-8 decode of the raw plaintext
    fails we transparently strip the first 32 bytes and retry."""
    if not encrypted_value:
        return ""
    prefix = encrypted_value[:3]
    plain: bytes
    if prefix == b"v10":
        from cryptography.hazmat.primitives.ciphers import (  # local import keeps cold-start cheap
            Cipher,
            algorithms,
            modes,
        )
        from cryptography.hazmat.primitives.padding import PKCS7

        cipher = Cipher(algorithms.AES(key), modes.CBC(_V10_IV))
        decryptor = cipher.decryptor()
        padded = decryptor.update(encrypted_value[3:]) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        plain = unpadder.update(padded) + unpadder.finalize()
    elif prefix == b"v11":
        nonce = encrypted_value[3:15]
        ct = encrypted_value[15:]
        plain = AESGCM(key).decrypt(nonce, ct, None)
    else:
        # Unknown scheme — caller can skip. Returning "" (rather than raising)
        # keeps a single bad row from poisoning a whole session bootstrap.
        return ""

    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        return plain[32:].decode("utf-8")


def _expires_to_playwright(expires_utc: int | None) -> float:
    """Convert Chrome's `expires_utc` microseconds-since-1601 value to
    Playwright's seconds-since-1970 (or -1 for session cookies)."""
    if not expires_utc or expires_utc <= 0:
        return -1
    return expires_utc / 1_000_000 - _EPOCH_DELTA_SECONDS


def read_cookies_for_hosts(
    host_patterns: Iterable[str],
    cookies_db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Read + decrypt all Chrome cookies matching any of the given SQL LIKE
    patterns, returned as Playwright-ready dicts.

    Example:
        >>> read_cookies_for_hosts(['%instagram%'])
        [{'name': 'sessionid', 'value': '…', 'domain': '.instagram.com', ...}, ...]

    Each host pattern is matched with a SQL LIKE against `cookies.host_key`,
    so use '%nextdoor%' or '%.instagram.com%' style wildcards."""
    cookies_db_path = cookies_db_path or CHROME_COOKIES
    if not cookies_db_path.exists():
        raise FileNotFoundError(f"Chrome Cookies DB not found at {cookies_db_path}")

    patterns = list(host_patterns)
    if not patterns:
        raise ValueError("host_patterns must not be empty")

    # Copy the DB to a tempfile so we don't fight Chrome's lock.
    tmpdir = Path(tempfile.mkdtemp(prefix="chrome-session-"))
    tmp_db = tmpdir / "Cookies.db"
    try:
        shutil.copy(cookies_db_path, tmp_db)

        where = " OR ".join("host_key LIKE ?" for _ in patterns)
        sql = (
            "SELECT host_key, name, path, expires_utc, is_secure, is_httponly, "
            "samesite, encrypted_value FROM cookies WHERE " + where
        )
        conn = sqlite3.connect(tmp_db)
        try:
            rows = conn.execute(sql, patterns).fetchall()
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    password = get_chrome_safe_storage_password()
    key = derive_key(password)

    out: list[dict[str, Any]] = []
    for host, name, path, expires_utc, secure, httponly, samesite, enc in rows:
        try:
            value = decrypt_cookie(enc, key)
        except Exception:
            # Silently skip bad rows so one cookie's weirdness never blocks a
            # session bootstrap. Callers can sanity-check the output count.
            continue
        if not value:
            continue
        out.append(
            {
                "name": name,
                "value": value,
                "domain": host,
                "path": path or "/",
                "expires": _expires_to_playwright(expires_utc),
                "httpOnly": bool(httponly),
                "secure": bool(secure),
                "sameSite": _SAMESITE_MAP.get(samesite, "Lax"),
            }
        )
    return out
