# Author: Claude Opus 5
# Date: 11-August-2026
# PURPOSE: Validate farm-2026's content/flock-profiles.json against the roster
#          schema at farm-2026/content/flock-profiles.schema.json — specifically
#          the dated color_observations[] trail added 11-Aug-2026 (plan:
#          farm-2026/docs/plans/2026-08-11-bird-observation-timestamps.md).
#
#          Deliberately DEPENDENCY-FREE. `jsonschema` is not installed in the
#          guardian venv and is not in requirements.txt, and this validator has
#          to be runnable from a bare `python3` on the Mac Mini (system Python is
#          3.9 with PEP 668 blocking pip). So rather than pull in a dependency
#          for one file, this asserts the invariants that actually matter by
#          hand. The JSON Schema file remains the machine-readable spec of record
#          for any external tooling that does have jsonschema available.
#
#          Invariants enforced (the ones a bad write would plausibly break):
#            - color_observations[] entries have the required keys and no others
#            - date is ISO yyyy-mm-dd, and `date is None` iff date_unknown is True
#              (this is the anti-fabrication rule from the plan)
#            - age_weeks, when present, is a non-negative int AND consistent with
#              hatch_date -> date, AND absent whenever hatch_date is estimated
#            - observations are sorted oldest-first with undated entries leading
#            - color_description / color_description_as_of mirror the LAST entry
#
#          Exit code 0 = valid, 1 = invalid (prints every problem, not just the
#          first, so one run tells you everything to fix).
#
# SRP/DRY check: Pass — searched first; flock-profiles.json validated against
#                nothing before this. tools/pipeline/schema.json is the LM Studio
#                VLM structured-output schema (strict/additionalProperties:false,
#                consumed by vlm_enricher/orchestrator/iphone_lane/replay script)
#                and is NOT a roster schema; extending it would have corrupted
#                the vision contract. Path resolution reuses git_helper
#                .farm_2026_root() rather than re-deriving the checkout location.
from __future__ import annotations

import json
import re
import sys
from datetime import date as _date
from pathlib import Path
from typing import Optional

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_OBS_REQUIRED = {"date", "description", "source", "date_unknown"}
_OBS_OPTIONAL = {"age_weeks", "note"}


def _parse_iso(value: str) -> Optional[_date]:
    try:
        return _date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def age_weeks(hatch_date: Optional[str], observed: Optional[str]) -> Optional[int]:
    """Whole weeks between hatch and observation, or None if not computable.

    Shared with the backfill script and bird_photo_ingest so all three agree on
    what "age_weeks" means. Returns None (caller omits the field) rather than a
    guess whenever either date is missing/unparseable or the observation
    predates the hatch.
    """
    if not hatch_date or not observed:
        return None
    h, o = _parse_iso(hatch_date), _parse_iso(observed)
    if not h or not o or o < h:
        return None
    return (o - h).days // 7


def _sort_key(obs: dict):
    """Chronological, undated FIRST.

    Undated observations lead rather than trail: an entry with no recoverable
    date is, in every case in this roster, an old hand-written description that
    predates the dated ones. Putting it last would make it the "newest" entry
    and mirror stale prose into color_description — the exact bug being fixed.
    """
    return (obs.get("date") is not None, obs.get("date") or "")


def validate(data: dict) -> list[str]:
    """Return a list of human-readable problems; empty list means valid."""
    problems: list[str] = []
    birds = data.get("flock_birds")
    if not isinstance(birds, list):
        return ["flock_birds is missing or not a list"]

    for i, bird in enumerate(birds):
        name = bird.get("name") or f"<unnamed #{i}>"
        if not bird.get("name"):
            problems.append(f"[{i}] bird has no name")

        obs_list = bird.get("color_observations")
        if obs_list is None:
            continue  # color_observations is optional at the schema level
        if not isinstance(obs_list, list) or not obs_list:
            problems.append(f"{name}: color_observations must be a non-empty array")
            continue

        hatch = bird.get("hatch_date")
        hatch_estimated = bool(bird.get("hatch_date_estimated"))

        for j, obs in enumerate(obs_list):
            tag = f"{name}.color_observations[{j}]"
            if not isinstance(obs, dict):
                problems.append(f"{tag}: not an object")
                continue

            keys = set(obs)
            missing = _OBS_REQUIRED - keys
            if missing:
                problems.append(f"{tag}: missing required key(s) {sorted(missing)}")
            extra = keys - _OBS_REQUIRED - _OBS_OPTIONAL
            if extra:
                problems.append(f"{tag}: unexpected key(s) {sorted(extra)}")

            d, unknown = obs.get("date"), obs.get("date_unknown")
            if not isinstance(unknown, bool):
                problems.append(f"{tag}: date_unknown must be a boolean")
            if d is None:
                # The anti-fabrication rule: no date means it must SAY so.
                if unknown is not True:
                    problems.append(f"{tag}: date is null but date_unknown is not true")
            else:
                if not isinstance(d, str) or not _ISO.match(d) or not _parse_iso(d):
                    problems.append(f"{tag}: date {d!r} is not a valid yyyy-mm-dd date")
                elif unknown is True:
                    problems.append(f"{tag}: date_unknown is true but a date {d!r} is set")

            if not (obs.get("description") or "").strip():
                problems.append(f"{tag}: description is empty")
            if not (obs.get("source") or "").strip():
                problems.append(f"{tag}: source is empty")

            if "age_weeks" in obs:
                aw = obs["age_weeks"]
                if not isinstance(aw, int) or isinstance(aw, bool) or aw < 0:
                    problems.append(f"{tag}: age_weeks {aw!r} is not a non-negative int")
                elif hatch_estimated:
                    problems.append(
                        f"{tag}: age_weeks is set but hatch_date is flagged estimated "
                        f"— it must be omitted rather than computed from a guess"
                    )
                else:
                    expected = age_weeks(hatch, d)
                    if expected is None:
                        problems.append(
                            f"{tag}: age_weeks is set but is not computable from "
                            f"hatch_date={hatch!r} and date={d!r}"
                        )
                    elif expected != aw:
                        problems.append(
                            f"{tag}: age_weeks {aw} != {expected} computed from "
                            f"hatch_date={hatch} -> {d}"
                        )

        if obs_list != sorted(obs_list, key=_sort_key):
            problems.append(f"{name}: color_observations is not chronologically sorted")

        # The derived mirror must actually mirror, or consumers (the website's
        # /flock cards, roster.py's VLM prompt block) silently read stale prose.
        latest = obs_list[-1]
        if bird.get("color_description") != latest.get("description"):
            problems.append(
                f"{name}: color_description does not mirror the newest observation"
            )
        expected_as_of = latest.get("date")
        if bird.get("color_description_as_of") != expected_as_of:
            problems.append(
                f"{name}: color_description_as_of ({bird.get('color_description_as_of')!r}) "
                f"does not mirror the newest observation date ({expected_as_of!r})"
            )

    return problems


def _default_path() -> Path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.pipeline.git_helper import farm_2026_root

    return farm_2026_root() / "content" / "flock-profiles.json"


def main(argv: list[str]) -> int:
    path = Path(argv[1]).expanduser() if len(argv) > 1 else _default_path()
    try:
        data = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001 — any read/parse failure is invalid
        print(f"INVALID: cannot read/parse {path}: {exc}")
        return 1

    problems = validate(data)
    n_birds = len(data.get("flock_birds", []))
    n_obs = sum(len(b.get("color_observations") or []) for b in data.get("flock_birds", []))
    if problems:
        print(f"INVALID: {path} — {len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"VALID: {path}")
    print(f"  {n_birds} birds, {n_obs} color observation(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
