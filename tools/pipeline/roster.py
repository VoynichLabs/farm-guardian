# Author: Claude Opus 4.8
# Date: 22-July-2026
# PURPOSE: Bridge to farm-2026's content/flock-profiles.json — the canonical
#          bird roster (names, breeds, hatch dates, the `ornitharch` named-
#          individual flag). farm-guardian never read this file before
#          v2.47.0; this module is the single place that does, so the VLM
#          prompt's named-individual guidance, reel captions, and Discord
#          reply-tagging (discord-reaction-sync.py) can all validate against
#          the same live roster instead of each hardcoding bird names.
#          Mirrors the existing FARM_DIARY_DIR path pattern in
#          daily_reel_runner.py (both read out of the farm-2026 checkout on
#          this same Mac Mini).
#
#          22-Jul-2026 (Claude Opus 4.8): the named-individual block now also
#          surfaces each bird's confirmed leg_band (color/number/side). The
#          flock was banded ~2026-07-21 and a legible band is a far more
#          reliable ID than plumage — it resolves the near-identical siblings
#          (Birdimir/Ingebird, Henridotta/Adelbird) the prompt used to hedge.
#          We render only the *confirmed*-band fact here; the anti-confabulation
#          rules (report only a band you can SEE, never infer one from plumage)
#          live globally in prompt.md.
# SRP/DRY check: Pass — single responsibility is loading + caching the
#                roster; callers (prompt-building, discord sync, reel
#                captions) own their own use of it.
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("pipeline.roster")

FLOCK_PROFILES_PATH = (
    Path.home() / "Documents" / "GitHub" / "farm-2026" / "content" / "flock-profiles.json"
)

# Re-read the file at most this often. The roster changes rarely (a rename,
# a new hatch) — no need to stat+parse JSON on every VLM call.
_CACHE_TTL_SECONDS = 300

_cache: dict = {"mtime": None, "loaded_at": 0.0, "birds": []}


def _load_raw() -> list[dict]:
    """Read + cache flock_birds from farm-2026/content/flock-profiles.json.

    Returns [] on any read/parse failure (missing checkout, malformed JSON,
    farm-2026 mid-write) rather than raising — every caller of this module
    treats an empty roster as "no roster data available" and degrades to
    its prior generic-label behavior. A silent 300s-old cache is a fine
    trade-off for a file that changes on the order of days, not seconds.
    """
    now = time.time()
    if _cache["birds"] and (now - _cache["loaded_at"]) < _CACHE_TTL_SECONDS:
        return _cache["birds"]

    try:
        mtime = FLOCK_PROFILES_PATH.stat().st_mtime
        if _cache["birds"] and mtime == _cache["mtime"]:
            _cache["loaded_at"] = now
            return _cache["birds"]
        data = json.loads(FLOCK_PROFILES_PATH.read_text())
        birds = data.get("flock_birds", [])
        if not isinstance(birds, list):
            raise ValueError("flock_birds is not a list")
        _cache.update(mtime=mtime, loaded_at=now, birds=birds)
        return birds
    except Exception as exc:  # noqa: BLE001 — roster is best-effort everywhere
        log.warning("roster: failed to load %s: %s", FLOCK_PROFILES_PATH, exc)
        return _cache["birds"]  # last-known-good, possibly []


def get_active_ornitharchs() -> list[dict]:
    """Named individuals (`ornitharch: true`) that are still alive
    (status != "deceased" / no deceased_date). Each dict has at least:
    name, breed, hatch_date, color_description, notes."""
    return [
        b
        for b in _load_raw()
        if b.get("ornitharch")
        and b.get("status") != "deceased"
        and not b.get("deceased_date")
    ]


def get_all_names(*, include_deceased: bool = True) -> list[str]:
    """Every bird/group `name` in the roster, for matching free text
    against. include_deceased=False restricts to living entries — deceased
    birds still get their name checked by default because a Discord reply
    or an old-photo caption may legitimately reference one."""
    birds = _load_raw()
    if not include_deceased:
        birds = [b for b in birds if b.get("status") != "deceased" and not b.get("deceased_date")]
    return [b["name"] for b in birds if b.get("name")]


def match_name(text: str) -> Optional[str]:
    """Case-insensitive exact match of `text` against every roster name
    (active and deceased — a Discord reply naming a deceased bird on an
    old photo is still a valid tag). Returns the canonical `name` as
    stored in flock-profiles.json, or None if nothing matches.

    Deliberately exact-match only (not fuzzy/substring) — this feeds
    Discord reply-tagging (E3), where a wrong guess writes a false
    identity into the archive. Callers wanting a "did you mean" nudge
    should build that on top rather than loosening the match here.
    """
    text = (text or "").strip().lower()
    if not text:
        return None
    for name in get_all_names():
        if name.lower() == text:
            return name
    return None


# Boss's own notes hedge some IDs as unconfirmed/contested (siblings that
# look near-identical, calls he later reversed). Surfacing those to the VLM
# as identification guidance would recreate the exact false-positive problem
# that got structured named-bird classification disabled in v2.38.2
# (docs/18-May-2026-birdadotta-s7-identification-note.md) — only birds with
# an unhedged description get into the prompt.
_HEDGE_MARKERS = (
    "disputed", "flip-flop", "not final", "low confidence",
    "unconfirmed", "verify visually", "tbd", "uncertain",
)


def _format_band(leg_band: Optional[dict]) -> str:
    """Render a bird's *confirmed* leg band as a short factual clause for the
    prompt — e.g. "Wears a purple leg band #12 on the left leg."

    Returns "" when there is no band, it isn't confirmed, or the color is
    missing: we never tell the VLM to look for a band that isn't verified on the
    bird. The band's color+number is unique per living bird, so that pair is the
    match key; `side` is included only as confirmation. The anti-confabulation
    rules (report only a band you can actually SEE, never infer one from
    plumage) live in prompt.md, which applies to every frame globally.
    """
    if not isinstance(leg_band, dict) or not leg_band.get("confirmed"):
        return ""
    color = (leg_band.get("color") or "").strip()
    if not color:
        return ""
    number = leg_band.get("number")
    side = (leg_band.get("side") or "").strip()
    num_part = f" #{number}" if number is not None else ""
    side_part = f" on the {side} leg" if side else ""
    article = "an" if color[:1].lower() in "aeiou" else "a"  # "an orange"
    return f"Wears {article} {color} leg band{num_part}{side_part}."


def format_named_individuals_block() -> str:
    """Render the VLM prompt's "Named individuals" section from the live
    roster, replacing the two hardcoded bird writeups that used to live
    directly in prompt.md (Birdadotta/Birdadette — the latter renamed
    Birddor in July and is exactly the kind of drift this module exists
    to prevent). Only the `color_description`/breed/hatch_date fields
    already meant for human reading go into the prompt — no VLM-only
    fields are invented here.

    Structured named-bird classification stays OFF per the v2.38.2
    lesson (docs/18-May-2026-birdadotta-s7-identification-note.md) —
    this is caption-only soft guidance, same as the text it replaces.
    Returns "" if the roster is unavailable, so the caller can fall back
    to a generic-labels-only instruction.
    """
    birds = get_active_ornitharchs()
    lines = []
    for b in birds:
        name = b.get("name", "")
        desc = (b.get("color_description") or "").strip()
        if not name or not desc:
            continue
        if any(marker in desc.lower() for marker in _HEDGE_MARKERS):
            continue  # Boss's own notes flag this ID as unconfirmed/contested
        breed = b.get("breed", "")
        hatch = b.get("hatch_date", "")
        bits = [p for p in (breed, f"b. {hatch}" if hatch else "") if p]
        header = f"**{name}**" + (f" ({', '.join(bits)})" if bits else "") + ":"
        # A confirmed leg band is appended as its own clause; the global
        # anti-confabulation rules in prompt.md govern how the VLM may use it.
        band_clause = _format_band(b.get("leg_band"))
        band_part = f" {band_clause}" if band_clause else ""
        lines.append(
            f"- {header} {desc}{band_part} "
            f"Matching this profile, you may say \"likely {name}.\""
        )
    if not lines:
        return ""
    return "\n".join(lines)
