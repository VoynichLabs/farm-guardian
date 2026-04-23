# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Package marker for tools.chrome_session — shared Chrome cookie
#          decryption + Playwright-context-seeding helpers. Used by the IG
#          engager (tools/ig-engage/) and the Nextdoor automation
#          (tools/nextdoor/) so neither duplicates the crypto code.
# SRP/DRY check: Pass — marker only.

from .decrypt import (
    CHROME_COOKIES,
    KEYCHAIN_SERVICE,
    KEYCHAIN_ACCOUNT,
    derive_key,
    decrypt_cookie,
    get_chrome_safe_storage_password,
    read_cookies_for_hosts,
)

__all__ = [
    "CHROME_COOKIES",
    "KEYCHAIN_SERVICE",
    "KEYCHAIN_ACCOUNT",
    "derive_key",
    "decrypt_cookie",
    "get_chrome_safe_storage_password",
    "read_cookies_for_hosts",
]
