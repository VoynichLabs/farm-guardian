# Author: Claude Opus 4.7 (1M context)
# Date: 21-April-2026
# PURPOSE: Deterministic caption composer for the on-this-day Facebook
#          pipeline. Input: a selector.Candidate. Output: a single-line
#          caption string ready for fb_poster.crosspost_photo, or a
#          ValueError if the row can't produce a safe caption (sanity
#          gate). No LLM calls — the scene_description field produced
#          by Qwen 3.5-35B at catalog time is the source text.
#
#          Format: "On this day, {YYYY} — {first_sentence}". Falls back
#          to a subject+time-of-day skeleton when scene_description is
#          missing or implausibly short. Final caption passes a post-
#          composition keyword filter so banned content (hawk/predator/
#          paperwork/etc.) can't slip through a scorer oversight.
#
# SRP/DRY check: Pass — single responsibility is "catalog row → caption
#                string". Does not read files, does not hit the network,
#                does not call the LLM. The banned-keyword list is
#                duplicated with selector.py intentionally: this is a
#                belt-and-suspenders check run at publish time so even
#                a future selector regression can't leak bad content.

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a runtime circular import with selector
    from .selector import Candidate

log = logging.getLogger("on_this_day.caption")

# Last line of defense. Intentionally conservative; a false negative
# here just skips a post, which is cheap.
BANNED_CAPTION_KEYWORDS = (
    "hawk", "predator", "dead", "blood", "injury", "wound", "carcass",
    "accident", "damage", "receipt", "screenshot", "paperwork",
    "invoice", "meme", "text message",
)

# Min description length to treat as "real content". Below this we
# fall back to the subject skeleton.
MIN_DESCRIPTION_CHARS = 20

# Max caption length — FB Page posts have no hard limit but short
# posts perform better, and the feed Boss wants is "pretty picture +
# one sentence of context".
MAX_CAPTION_CHARS = 280


class CaptionSafetyError(ValueError):
    """Raised when the composed caption trips the banned-keyword filter.

    Caller (post_daily.py) should treat this as "skip this candidate,
    move to the next" rather than a hard pipeline failure.
    """


def _first_sentence(text: str) -> str:
    """Return the first sentence of text, stripped. Handles the common
    case where Qwen returns a single sentence already."""
    text = (text or "").strip()
    if not text:
        return ""
    # Split on sentence-terminating punctuation followed by whitespace.
    # Keeps the terminator attached to the sentence we return.
    m = re.search(r"[.!?](?=\s|$)", text)
    if m is None:
        return text
    return text[: m.end()].strip()


def _subject_skeleton(candidate_row: dict, year: int) -> str:
    """Build a fallback caption from primary_subjects + time_of_day.
    Used only when scene_description is missing or too short."""
    subj_raw = candidate_row.get("primary_subjects") or ""
    subject_phrase = "a moment from the farm"
    if subj_raw.startswith("["):
        try:
            subjects = json.loads(subj_raw)
            if subjects and isinstance(subjects, list):
                first = subjects[0]
                if isinstance(first, dict) and first.get("subject"):
                    subject_phrase = str(first["subject"]).strip()
        except (json.JSONDecodeError, TypeError):
            pass

    tod = (candidate_row.get("time_of_day") or "").strip().lower()
    if tod and tod not in {"unknown", ""}:
        subject_phrase = f"{subject_phrase} at {tod}"

    return f"On this day, {year} — {subject_phrase}."


def compose(candidate: "Candidate") -> str:
    """Return a single-line FB Page caption for the given Candidate.

    Raises:
      CaptionSafetyError — if the composed caption contains a banned
                           keyword. The caller should move on to the
                           next candidate rather than post anyway.
    """
    year = candidate.year
    desc = (candidate.catalog_row.get("scene_description") or "").strip()

    if len(desc) >= MIN_DESCRIPTION_CHARS:
        first = _first_sentence(desc)
        caption = f"On this day, {year} — {first}"
    else:
        caption = _subject_skeleton(candidate.catalog_row, year)

    # Collapse internal whitespace; strip trailing whitespace.
    caption = re.sub(r"\s+", " ", caption).strip()

    # Length cap — truncate at the last word boundary before the limit.
    if len(caption) > MAX_CAPTION_CHARS:
        truncated = caption[: MAX_CAPTION_CHARS].rsplit(" ", 1)[0]
        caption = truncated.rstrip(",;:— ") + "…"

    # Sanity gate — belt-and-suspenders against scorer oversights.
    low = caption.lower()
    for bad in BANNED_CAPTION_KEYWORDS:
        if bad in low:
            raise CaptionSafetyError(
                f"caption tripped banned keyword {bad!r}: {caption!r}"
            )

    return caption


__all__ = ["BANNED_CAPTION_KEYWORDS", "CaptionSafetyError", "compose"]
