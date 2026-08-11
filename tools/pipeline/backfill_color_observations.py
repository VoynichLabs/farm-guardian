# Author: Claude Opus 5
# Date: 11-August-2026
# PURPOSE: ONE-TIME backfill converting each existing flock_birds[] entry's flat
#          `color_description` string in farm-2026/content/flock-profiles.json
#          into a dated `color_observations[0]` entry, per
#          farm-2026/docs/plans/2026-08-11-bird-observation-timestamps.md.
#
#          The bug being fixed: color_description carries no timestamp, so a
#          description written when a bird was three weeks old silently
#          outranked a correct leg-band match (twice, on 11-Aug-2026).
#
#          DATE SOURCING — the whole point of this script, and the part that
#          must not be clever. The roster's notes are full of dates, but almost
#          all of them are hatch dates, death dates or BANDING dates. Regexing
#          "any date near this bird" would have stamped Birdgit's plumage with
#          08-April-2026, the day a hawk killed her. So this only accepts dates
#          from phrases that explicitly anchor to a PLUMAGE observation:
#
#            "(Plumage from 23-Jun-2026 ...)"      -> 2026-06-23
#            "PLUMAGE CORRECTED 2026-07-29 ..."    -> 2026-07-29
#            "Boss-confirmed 29-Jul-2026 ..."      -> 2026-07-29  (color_description only)
#            "SILVER RESOLVED: ... confirmed 2026-07-21" -> 2026-07-21
#
#          Everything else gets `date: null, date_unknown: true`. photos[].date
#          is deliberately NOT used as a proxy: the date a photo was filed is not
#          evidence that the prose description was written from that photo, and
#          inferring it would re-introduce exactly the fabricated-confidence
#          problem the plan forbids. ~7 of 35 birds get a real date; the plan
#          anticipates this ("mark it date_unknown rather than inventing one").
#
#          Idempotent: a bird that already has color_observations is skipped, so
#          a re-run is a no-op. --dry-run prints the plan without writing.
#
# SRP/DRY check: Pass — sole responsibility is the historical backfill. The
#                age-weeks maths and the sort order are imported from
#                validate_flock_profiles rather than re-implemented, so the
#                backfill, the validator and bird_photo_ingest cannot drift.
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:
    sys.path.insert(0, str(_HERE.parents[2]))

from tools.pipeline.validate_flock_profiles import (  # noqa: E402
    _sort_key,
    age_weeks,
    validate,
)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# A date in either spelling the roster actually uses: 23-Jun-2026 / 08-April-2026
# / 2026-07-29. Kept as one alternation so the anchor patterns below stay legible.
_DATE = r"(\d{4}-\d{2}-\d{2}|\d{1,2}[-\s](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-\s]\d{4})"

# Phrases that genuinely anchor a date to a PLUMAGE observation. Order is not
# significant — every match is collected and the LATEST wins, because a later
# plumage note is by definition a fresher look at the bird.
_PLUMAGE_DATE_PATTERNS = (
    re.compile(r"plumage\s+(?:from|as\s+of|dated)\s+" + _DATE, re.I),
    re.compile(r"plumage\s+corrected\s+" + _DATE, re.I),
    re.compile(r"silver\s+resolved.{0,80}?confirmed\s+" + _DATE, re.I | re.S),
)

# Only trusted inside color_description itself, where the subject of the
# confirmation is unambiguously the plumage being described. In `notes` the same
# words routinely confirm a band, a dam or a death instead.
_CD_ONLY_PATTERNS = (
    re.compile(r"boss[-\s]confirmed\s+" + _DATE, re.I),
    re.compile(r"plumage[^.]{0,40}?confirmed\s+" + _DATE, re.I),
)


def _norm_date(raw: str) -> Optional[str]:
    """Normalise a matched date string to ISO yyyy-mm-dd, or None if nonsense."""
    raw = raw.strip().lower()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    m = re.fullmatch(r"(\d{1,2})[-\s]([a-z]+)[-\s](\d{4})", raw)
    if not m:
        return None
    day, mon_word, year = m.group(1), m.group(2)[:3], m.group(3)
    month = _MONTHS.get(mon_word)
    if not month:
        return None
    return f"{int(year):04d}-{month:02d}-{int(day):02d}"


def extract_observation_date(bird: dict) -> tuple[Optional[str], Optional[str]]:
    """Best recoverable PLUMAGE-observation date for `bird`.

    Returns (iso_date, provenance_phrase) or (None, None) when no plumage-anchored
    date exists. Never falls back to hatch/death/banding dates or photos[].date.
    """
    color_description = bird.get("color_description") or ""
    notes = bird.get("notes") or ""

    found: list[tuple[str, str]] = []
    for pattern in _PLUMAGE_DATE_PATTERNS:
        for haystack in (color_description, notes):
            for m in pattern.finditer(haystack):
                iso = _norm_date(m.group(1))
                if iso:
                    found.append((iso, m.group(0).strip()))
    for pattern in _CD_ONLY_PATTERNS:
        for m in pattern.finditer(color_description):
            iso = _norm_date(m.group(1))
            if iso:
                found.append((iso, m.group(0).strip()))

    if not found:
        return None, None
    iso, phrase = max(found, key=lambda pair: pair[0])
    return iso, re.sub(r"\s+", " ", phrase)[:160]


def build_observation(bird: dict) -> Optional[dict]:
    """The single backfilled color_observations entry for `bird`, or None when
    the bird has no color_description to convert."""
    description = (bird.get("color_description") or "").strip()
    if not description:
        return None

    iso, phrase = extract_observation_date(bird)
    obs: dict = {
        "date": iso,
        "date_unknown": iso is None,
        "description": description,
        "source": "roster-backfill",
    }

    # age_weeks only where the hatch date is both known AND not itself a rough
    # estimate — most of the older birds carry hatch_date_estimated: true, and
    # deriving an age from a guessed hatch would dress a guess up as data.
    if iso and bird.get("hatch_date") and not bird.get("hatch_date_estimated"):
        weeks = age_weeks(bird.get("hatch_date"), iso)
        if weeks is not None:
            obs["age_weeks"] = weeks

    obs["note"] = (
        f"Backfilled 11-Aug-2026 from the undated color_description. "
        + (
            f"Date recovered from the record's own wording: \"{phrase}\"."
            if iso
            else "No plumage-anchored date was recoverable from the record; "
            "the true observation date is unknown and was NOT inferred."
        )
    )
    return obs


def backfill(data: dict) -> tuple[int, int, int]:
    """Mutate `data` in place. Returns (converted, dated, skipped)."""
    converted = dated = skipped = 0
    for bird in data.get("flock_birds", []):
        if bird.get("color_observations"):
            skipped += 1
            continue
        obs = build_observation(bird)
        if obs is None:
            skipped += 1
            continue

        observations = [obs]
        observations.sort(key=_sort_key)
        newest = observations[-1]

        # Rebuild the entry so color_observations / color_description /
        # color_description_as_of sit together in key order instead of the new
        # keys landing at the end of the object, away from what they mirror.
        rebuilt: dict = {}
        for key, value in bird.items():
            if key == "color_description":
                rebuilt["color_observations"] = observations
                rebuilt["color_description"] = newest["description"]
                rebuilt["color_description_as_of"] = newest["date"]
            else:
                rebuilt[key] = value
        bird.clear()
        bird.update(rebuilt)

        converted += 1
        if newest["date"]:
            dated += 1
    return converted, dated, skipped


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="path to flock-profiles.json")
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = parser.parse_args(argv[1:])

    if args.path:
        path = Path(args.path).expanduser()
    else:
        from tools.pipeline.git_helper import farm_2026_root

        path = farm_2026_root() / "content" / "flock-profiles.json"

    src = path.read_text()
    data = json.loads(src)

    converted, dated, skipped = backfill(data)

    for bird in data.get("flock_birds", []):
        obs = (bird.get("color_observations") or [])
        if obs:
            newest = obs[-1]
            mark = newest["date"] or "date-unknown"
            print(f"  {bird.get('name', '?'):34} {mark:12} age_weeks={newest.get('age_weeks', '-')}")

    problems = validate(data)
    if problems:
        print(f"\nABORTED — backfilled data is invalid ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(
        f"\n{converted} bird(s) converted "
        f"({dated} with a recovered date, {converted - dated} date_unknown), "
        f"{skipped} skipped."
    )

    if args.dry_run:
        print("--dry-run: nothing written.")
        return 0

    # Byte-fidelity with the rest of the pipeline: indent=2 and the DEFAULT
    # ensure_ascii=True. Writing literal UTF-8 would un-escape every \uXXXX in
    # the file and churn every unicode line into the diff — see the same note in
    # bird_photo_ingest._set_photo_and_commit.
    path.write_text(json.dumps(data, indent=2) + ("\n" if src.endswith("\n") else ""))
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
