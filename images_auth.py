# Author: Claude Opus 4.6 (1M context)
# Date: 14-April-2026
# PURPOSE: Bearer-token auth for the /api/v1/images/review/* endpoints.
#          One module-level token is set at register time (from the
#          GUARDIAN_REVIEW_TOKEN env var, surfaced into config by guardian.py's
#          load_config()). A FastAPI dependency compares Authorization headers
#          against it in constant time. If no token is configured, review
#          endpoints return 503 — the pipeline and public endpoints keep
#          running. Public endpoints never touch this module.
# SRP/DRY check: Pass — single responsibility is gating review mutations.

from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import Header, HTTPException

log = logging.getLogger("guardian.images.auth")

_REVIEW_TOKEN: Optional[str] = None


def set_review_token(token: Optional[str]) -> None:
    """Called once by register_api() at startup. Empty string and None both
    disable review endpoints (service returns 503)."""
    global _REVIEW_TOKEN
    _REVIEW_TOKEN = token if token else None
    if _REVIEW_TOKEN:
        log.info("Review token registered (length=%d)", len(_REVIEW_TOKEN))
    else:
        log.warning(
            "GUARDIAN_REVIEW_TOKEN not configured — /review/* endpoints will 503"
        )


async def require_review_token(authorization: Optional[str] = Header(None)) -> None:
    """FastAPI dependency: 503 if no token is configured on the server, 403
    on a missing / malformed / mismatched token. Never logs the provided
    header — it may contain the correct secret."""
    if _REVIEW_TOKEN is None:
        raise HTTPException(503, "Review endpoints disabled (no token configured)")
    expected = f"Bearer {_REVIEW_TOKEN}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(403, "invalid review token")
