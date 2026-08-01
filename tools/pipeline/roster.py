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
import re
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("pipeline.roster")

# Resolved from tools/pipeline/config.json, never hardcoded (01-Aug-2026).
# This was `~/Documents/GitHub/farm-2026`, which is not a git checkout on this
# machine, so the roster silently read nothing — see git_helper.farm_2026_root().
from tools.pipeline.git_helper import farm_2026_root  # noqa: E402

FLOCK_PROFILES_PATH = farm_2026_root() / "content" / "flock-profiles.json"

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
    "verify visually", "tbd", "uncertain",
)

# Longest description we put in front of the VLM, per bird.
#
# The roster's color_description is deliberately rich — it feeds the website's
# /flock cards and is the farm's own written record — but this prompt block is
# prepended to EVERY captured frame on a 4B model, and prompt length there
# trades directly against the gem-scoring calibration that decides what
# reaches Discord. The 28-Jul-2026 rewrite (chick-down text replaced with
# photo-verified adult plumage) took the rendered block from 3,244 to 6,031
# characters; trimming to the leading sentences brings it back in line without
# losing anything, because every rewritten description is written to LEAD with
# the bird's single most discriminating feature.
_PROMPT_DESC_CHARS = 240


def _lead(text: str, limit: int = _PROMPT_DESC_CHARS) -> str:
    """First whole sentences of `text` that fit inside `limit`.

    Breaks on sentence boundaries only. A mid-clause cut is what made the old
    60-char truncation in daily_reel_runner actively harmful — it severed
    phrases like "...the key discriminator against X" and handed the model a
    description that fitted two birds at once. If even the first sentence is
    longer than `limit`, that whole sentence is kept: an over-long but complete
    description beats a truncated one.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    out = ""
    for chunk in re.split(r"(?<=[.!?])\s+", text):
        if out and len(out) + 1 + len(chunk) > limit:
            break
        out = f"{out} {chunk}".strip()
    return out or text


# Local mirror of the band table, for when the farm-2026 checkout beside us is
# missing or mid-write. It is a FALLBACK, never the primary: flock-profiles.json
# is where Boss records a new band, and this file is a copy of it. Committed to
# this repo on 28-Jul-2026 — before that it was read by nothing at all.
_LOCAL_BANDS_PATH = Path(__file__).resolve().parents[2] / "config" / "flock_bands.json"


def _local_bands() -> list[dict]:
    """Read config/flock_bands.json — the offline fallback band table.

    Note the key difference from flock-profiles.json, which is a live trap: this
    file spells the side `leg`, the roster spells it `side`. Anything reading
    both must normalise, and `resolve_band` ignores the side anyway.
    """
    try:
        raw = json.loads(_LOCAL_BANDS_PATH.read_text())
        return [
            {
                "name": b.get("name", ""),
                "color": (b.get("color") or "").strip().lower(),
                "number": b.get("number"),
                "leg": (b.get("leg") or "").strip().lower() or None,
            }
            for b in raw.get("bands", [])
            if b.get("confirmed") and (b.get("color") or "").strip()
        ]
    except Exception as exc:  # noqa: BLE001 — fallback is best-effort by definition
        log.warning("roster: local band fallback unavailable (%s)", exc)
        return []


def get_confirmed_bands() -> list[dict]:
    """Every LIVING bird wearing a confirmed, coloured leg band.

    Each entry: {"name", "color" (lowercased), "number" (int|None), "leg"
    ("left"/"right"/None)}. Deceased birds are excluded deliberately —
    Henrietta wore an unnumbered purple band and resolving a live purple
    sighting to a bird that died on 2026-06-05 would be worse than not
    resolving it at all.

    Falls back to config/flock_bands.json when the farm-2026 roster cannot be
    read, so band identification survives that checkout being absent. A
    disagreement between the two is logged rather than silently preferred —
    the roster always wins, and a warning means the local copy needs a refresh.
    """
    out: list[dict] = []
    for bird in _load_raw():
        if bird.get("status") != "active" or bird.get("deceased_date"):
            continue
        band = bird.get("leg_band")
        if not isinstance(band, dict) or not band.get("confirmed"):
            continue
        color = (band.get("color") or "").strip().lower()
        if not color:
            continue
        out.append({
            "name": bird.get("name", ""),
            "color": color,
            "number": band.get("number"),
            "leg": (band.get("side") or "").strip().lower() or None,
        })
    return out


def resolve_band(
    color: Optional[str],
    leg: Optional[str] = None,
    number: Optional[int] = None,
) -> Optional[str]:
    """Turn an OBSERVED band into a bird name — or into nothing.

    This is the half of band identification that the vision model must never
    do. It observes a coloured ring; this function decides who wears it, from
    a fixed dozen-row table that cannot be misremembered.

    Measured over 17,617 s7 frames (22-Jul → 28-Jul-2026), the prose approach
    that preceded this produced ZERO identifications from 440 band sightings,
    while publishing five band combinations that exist on no bird at all
    ("green #1", "pink #1", "purple #1"). The reason was the matching rule:
    it demanded colour + number + leg together, and the number was legible in
    only 4% of sightings while the leg was legible in 39%. So:

    - **A number, when given, must match exactly.** Colour+number is unique
      across all twelve living banded birds — there are no collisions — so it
      is the whole key. An impossible pair returns None rather than falling
      back to a looser match: a misread number means the reading is unreliable,
      not something to route around.
    - **Without a number we still resolve, but only on a unique colour.**
      Green, red, purple and blue each belong to exactly one living bird, so
      the colour alone is enough. Orange, pink, white and yellow are each worn
      by two birds and correctly return None until a number is read.

    ⚠️ **`leg` IS DELIBERATELY IGNORED FOR MATCHING. Do not "improve" this by
    filtering on it.** Measured 28-Jul-2026 against qwen3-vl-4b on the six
    handheld banding portraits: the model read the colour on 5 of 6 and read
    three numbers exactly — and got the leg wrong on 5 out of 5, answering
    "right" for birds that all wear their band on the left. An earlier version
    of this function let a contradicting leg veto the match, and that single
    rule took a run that should have identified four birds down to zero. The
    model cannot work out a bird's own left from its right, and the leg is not
    needed anyway. It is still recorded in `band_leg` so a future model can be
    re-measured, but it must never decide anything.

    Returns the canonical roster name, or None. None is the common and
    expected answer; callers must treat it as "say nothing about a band".
    """
    del leg  # see the warning above — recorded, never matched on

    color = (color or "").strip().lower()
    if not color or color == "none":
        return None

    # -1 is the schema's "band present but number not legible" sentinel.
    if number is not None and number < 0:
        number = None

    candidates = [b for b in get_confirmed_bands() if b["color"] == color]
    if not candidates:
        return None  # no living bird wears this colour — a misread

    if number is not None:
        exact = [b for b in candidates if b["number"] == number]
        if len(exact) == 1:
            return exact[0]["name"]
        return None  # this colour exists but not with that number

    return candidates[0]["name"] if len(candidates) == 1 else None


def _format_band(leg_band: Optional[dict]) -> str:
    """DELIBERATELY RETURNS "" — bands are no longer shown to the VLM.

    ⚠️ Do not "restore" this. From 22-Jul-2026 this rendered each bird's band
    into the prompt ("Wears a purple leg band #12 on the left leg") so the
    model could match a sighting against the list itself. Measured over 17,617
    s7 frames to 28-Jul: 440 band sightings, ZERO birds ever identified from a
    band, and five published band claims that exist on no bird ("green #1",
    "pink #1", "purple #1"). Handing the model the answer key is what caused
    that — knowing Ingebird wears green invites reporting green once the model
    has decided, on plumage, that it is looking at Ingebird.

    The model now reports the ring it can see into `band_color`/`band_leg`/
    `band_number` (see prompt.md) and `resolve_band()` above does the lookup in
    Python, where the table cannot be misremembered and an impossible reading
    is rejected instead of published.

    Kept as a no-op rather than deleted so the reasoning survives next to the
    call site. The signature stays intact for the same reason.
    """
    return ""


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
        desc = _lead(desc)
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
