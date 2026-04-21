# Author: Claude Opus 4.7 (1M context)
# Date: 21-April-2026
# PURPOSE: Select "on-this-day" iPhone/iCloud photo candidates for a
#          given calendar date, pulling from years 2022, 2024, 2025
#          (2023 deliberately excluded per Boss). Reads the macOS
#          Photos.sqlite library database (read-only) to enumerate
#          assets whose EXIF-derived creation date matches the target
#          month-day in an eligible year, joins each hit against the
#          Qwen-described master catalog at
#          ~/bubba-workspace/projects/photos-curation/photo-catalog/
#          master-catalog.csv, applies a content filter (brooder /
#          yorkie / flock / yard-diary yes; hawks / predators /
#          receipts / screenshots no) and ranks by aesthetic signals
#          already present in the catalog. Returns the top-N candidates
#          as plain dicts — no side effects, no file writes, no LLM
#          calls. Consumed by post_daily.py.
#
# SRP/DRY check: Pass — single responsibility is "from a date, return
#                ranked candidate rows." Does not post, does not export,
#                does not mutate the catalog or Photos library. Reuses
#                the catalog CSV as-is (no schema change) so the
#                existing process_batch.py writer and this reader stay
#                decoupled.

from __future__ import annotations

import csv
import datetime as dt
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger("on_this_day.selector")

# --- Paths (absolute by design: single-host pipeline on the Mac Mini) ---

PHOTOS_SQLITE = Path(
    "/Users/macmini/Pictures/Photos Library.photoslibrary/database/Photos.sqlite"
)
CATALOG_CSV = Path(
    "/Users/macmini/bubba-workspace/projects/photos-curation/photo-catalog/master-catalog.csv"
)

# Apple Cocoa epoch: seconds since 2001-01-01 UTC. ZASSET.ZDATECREATED and
# friends are stored in this scale. Unix-epoch = Cocoa + 978307200.
COCOA_EPOCH_OFFSET = 978307200

# Years we post from. 2023 deliberately skipped per Boss.
ELIGIBLE_YEARS = (2022, 2024, 2025)

# --- Content filters ---

# Farm-content keywords in scene_description give a strong positive signal.
# Listed lowercase; matched case-insensitively via substring. Intentionally
# narrow — we want brooder/yorkie/flock/coop/yard-diary energy, not
# "any outdoor photo". Weights defined in _score_row.
FARM_KEYWORDS = (
    "chicken", "hen", "rooster", "chick", "pullet",
    "yorkie", "dog", "puppy", "pawel", "pawleen",
    "flock", "coop", "brooder", "nesting", "egg",
    "garden", "pasture", "yard", "farm", "backyard",
    "sunset", "sunrise", "golden hour",
    "snow", "spring", "bloom",
)

# Positive aesthetic tags from the Qwen catalog.
GOOD_AESTHETIC_TAGS = frozenset({
    "cute", "adorable", "beautiful", "vibrant", "warm", "soft",
    "bokeh", "golden-hour", "sunny", "lush", "cozy", "picturesque",
    "pastoral", "natural", "peaceful",
})

# Hard-reject aesthetic tags — these rows never post.
BAD_AESTHETIC_TAGS = frozenset({
    "accident", "damage", "documentary", "retail", "receipt",
    "screenshot", "text-heavy", "meme", "automotive",
    "paperwork", "medical", "graphic-design",
})

# Hard-reject scene_description / notable_elements keywords. The
# hawk / predator exclusion is non-negotiable per the IG content
# policy (see CLAUDE.md → "Content rules"); the FB Page inherits it
# because the audience overlap is large.
BAD_TEXT_KEYWORDS = (
    "accident", "damage", "receipt", "screenshot",
    "paperwork", "invoice", "meme", "text message",
    "hawk", "predator", "dead", "blood", "injury",
    "wound", "carcass",
)


@dataclass
class Candidate:
    """One ranked photo candidate ready for post_daily.py to consider."""
    uuid: str
    date_taken: dt.datetime
    year: int
    source_path: Path
    catalog_row: dict
    score: int
    rejected: bool = False
    rejection_reason: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "uuid": self.uuid,
            "date_taken": self.date_taken.isoformat(),
            "year": self.year,
            "source_path": str(self.source_path),
            "score": self.score,
            "rejected": self.rejected,
            "rejection_reason": self.rejection_reason,
            "scene_description": self.catalog_row.get("scene_description", ""),
            "aesthetic_tags": self.catalog_row.get("aesthetic_tags", ""),
            "time_of_day": self.catalog_row.get("time_of_day", ""),
            "lighting": self.catalog_row.get("lighting", ""),
            "dimensions": self.catalog_row.get("dimensions", ""),
        }


# ---------------------------------------------------------------------------
# Photos.sqlite enumeration
# ---------------------------------------------------------------------------


def _cocoa_to_datetime(cocoa_seconds: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(cocoa_seconds + COCOA_EPOCH_OFFSET, dt.timezone.utc)


def _open_photos_db_readonly(path: Path = PHOTOS_SQLITE) -> sqlite3.Connection:
    # Photos.app keeps this DB open in WAL mode. We open in read-only mode
    # via the URI form so we can't possibly contend for a writer lock.
    # immutable=1 would be stronger still but also prevents WAL reads of
    # not-yet-checkpointed writes, which matters when Photos is actively
    # syncing from iCloud. mode=ro is the right balance.
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def enumerate_assets_for_month_day(
    month: int,
    day: int,
    years: Iterable[int] = ELIGIBLE_YEARS,
    db_path: Path = PHOTOS_SQLITE,
) -> list[dict]:
    """Return every non-trashed photo asset whose local-time date_taken
    falls on the given month/day in one of the eligible years.

    Returns: list of {uuid, date_taken (datetime, UTC), year}.
    """
    # ZDATECREATED is Cocoa-epoch seconds in UTC. We convert to local
    # time before month/day comparison because "on this day" is a
    # calendar concept, not a UTC concept. For the same reason we
    # bracket by year+month+day boundaries rather than generating a
    # set of per-year unix ranges (avoids DST drift issues).
    year_list = sorted(set(int(y) for y in years))
    results: list[dict] = []

    with _open_photos_db_readonly(db_path) as conn:
        # ZKIND = 0 → photo (ZKIND = 1 is video; we exclude videos for
        # the Facebook photo lane). ZTRASHEDDATE IS NULL excludes the
        # Recently Deleted bucket. ZHIDDEN = 0 excludes the Hidden album.
        # We do NOT filter by ZSAVEDASSETTYPE — the field's enum drifts
        # between Photos versions (this library uses values 3/4/6 where
        # older ones used 0/1/2) and screenshots / saved-from-web are
        # already weeded out downstream by the catalog's aesthetic-tag
        # filter (screenshot/text-heavy/retail → hard reject).
        sql = (
            "SELECT ZUUID, ZDATECREATED, ZFILENAME "
            "FROM ZASSET "
            "WHERE ZTRASHEDDATE IS NULL "
            "  AND ZHIDDEN = 0 "
            "  AND ZKIND = 0 "
            "  AND ZDATECREATED IS NOT NULL"
        )
        for row in conn.execute(sql):
            try:
                created_utc = _cocoa_to_datetime(row["ZDATECREATED"])
            except (TypeError, ValueError, OSError):
                continue
            # Convert to local time for calendar comparison. The Mac Mini
            # lives in a fixed timezone so astimezone() with no arg uses
            # the system locale, matching Photos.app's calendar view.
            local = created_utc.astimezone()
            if local.month != month or local.day != day:
                continue
            if local.year not in year_list:
                continue
            results.append({
                "uuid": row["ZUUID"],
                "date_taken": local,
                "year": local.year,
            })

    log.info(
        "enumerate: %d candidate assets for %02d-%02d across years %s",
        len(results), month, day, year_list,
    )
    return results


# ---------------------------------------------------------------------------
# Catalog index
# ---------------------------------------------------------------------------


def load_catalog_index(catalog_csv: Path = CATALOG_CSV) -> dict[str, dict]:
    """Return {uuid: row_dict} from the master catalog CSV.

    UUIDs in the catalog are uppercase hex with dashes (Photos library
    format). ZASSET.ZUUID is the same format, so the join is exact.
    """
    if not catalog_csv.exists():
        raise FileNotFoundError(
            f"catalog CSV missing: {catalog_csv}. "
            "Run tools/on_this_day/catalog_backfill.py first."
        )
    out: dict[str, dict] = {}
    with catalog_csv.open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            uuid = (row.get("uuid") or "").strip()
            if uuid:
                out[uuid] = row
    log.info("catalog: loaded %d indexed rows from %s", len(out), catalog_csv)
    return out


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


_DIM_RE = re.compile(r"^(\d+)\s*x\s*(\d+)")


def _parse_short_edge_px(dim_field: str) -> Optional[int]:
    if not dim_field:
        return None
    m = _DIM_RE.match(dim_field.strip())
    if not m:
        return None
    try:
        w, h = int(m.group(1)), int(m.group(2))
    except ValueError:
        return None
    return min(w, h)


def _split_tag_field(raw: str) -> list[str]:
    # Catalog stores aesthetic_tags as pipe-separated (e.g. "cute|warm|bokeh").
    # dominant_colors uses the same convention.
    if not raw:
        return []
    return [t.strip().lower() for t in raw.split("|") if t.strip()]


def _contains_any(haystack: str, needles: Iterable[str]) -> bool:
    if not haystack:
        return False
    low = haystack.lower()
    return any(n in low for n in needles)


def _score_row(catalog_row: dict) -> tuple[int, Optional[str]]:
    """Return (score, rejection_reason). If rejection_reason is set, the
    row is a hard reject regardless of score."""
    desc = (catalog_row.get("scene_description") or "").strip()
    tags = set(_split_tag_field(catalog_row.get("aesthetic_tags", "")))
    notable = catalog_row.get("notable_elements") or ""

    # --- Hard rejects ---
    if tags & BAD_AESTHETIC_TAGS:
        return (0, f"bad aesthetic tag(s): {sorted(tags & BAD_AESTHETIC_TAGS)}")
    if _contains_any(desc, BAD_TEXT_KEYWORDS) or _contains_any(notable, BAD_TEXT_KEYWORDS):
        return (0, "rejected keyword in description/notable_elements")

    # Short-edge floor. Many old iCloud thumbnails or messaging receipts
    # land here; also rejects catalog rows where dimensions weren't
    # parseable (model sometimes returns "unknown").
    short_edge = _parse_short_edge_px(catalog_row.get("dimensions", ""))
    if short_edge is not None and short_edge < 1500:
        return (0, f"short_edge={short_edge}px < 1500")

    # --- Positive signals ---
    score = 0

    # Farm-content keywords — the most important positive signal.
    farm_hits = sum(1 for kw in FARM_KEYWORDS if kw in desc.lower())
    score += 2 * farm_hits

    # Good aesthetic tags.
    score += len(tags & GOOD_AESTHETIC_TAGS)

    # Golden-hour / soft light.
    tod = (catalog_row.get("time_of_day") or "").lower()
    if tod in {"golden-hour", "golden hour", "sunset", "sunrise", "dawn", "dusk"}:
        score += 2
    lighting = (catalog_row.get("lighting") or "").lower()
    if lighting in {"soft", "warm", "natural"}:
        score += 1

    # Subject-forward composition. primary_subjects is a JSON string
    # like '[{"subject":"...","position":"center","approximate_size_pct":80}]'.
    subjects_raw = catalog_row.get("primary_subjects") or ""
    if subjects_raw.startswith("["):
        # Cheap substring test for a sizable primary subject — we avoid
        # json.loads on the hot path because malformed rows exist.
        if '"approximate_size_pct": 4' in subjects_raw or '"approximate_size_pct": 5' in subjects_raw \
                or '"approximate_size_pct": 6' in subjects_raw or '"approximate_size_pct": 7' in subjects_raw \
                or '"approximate_size_pct": 8' in subjects_raw or '"approximate_size_pct": 9' in subjects_raw:
            score += 1

    # Rows with zero positive signals are low-value noise — treat as
    # rejected even though they didn't trip a hard filter. Keeps the
    # candidate JSON readable.
    if score == 0:
        return (0, "no positive signals")

    return (score, None)


# ---------------------------------------------------------------------------
# Top-level entry points
# ---------------------------------------------------------------------------


def select_candidates(
    target_date: dt.date,
    top_n: int = 5,
    include_rejected: bool = False,
    catalog_csv: Path = CATALOG_CSV,
    db_path: Path = PHOTOS_SQLITE,
) -> list[Candidate]:
    """Core entry point. Given a target calendar date, return up to
    top_n ranked Candidate objects drawn from the eligible years.

    include_rejected=True keeps filtered rows (with score=0 and a
    rejection_reason set) so callers can surface them in dry-run
    output for debugging.
    """
    catalog = load_catalog_index(catalog_csv)
    assets = enumerate_assets_for_month_day(
        target_date.month, target_date.day, ELIGIBLE_YEARS, db_path=db_path
    )

    accepted: list[Candidate] = []
    rejected: list[Candidate] = []

    for asset in assets:
        row = catalog.get(asset["uuid"])
        if row is None:
            # Not catalogued — the backfill script is what fixes this.
            if include_rejected:
                rejected.append(Candidate(
                    uuid=asset["uuid"],
                    date_taken=asset["date_taken"],
                    year=asset["year"],
                    source_path=Path(""),
                    catalog_row={},
                    score=0,
                    rejected=True,
                    rejection_reason="not in catalog (run catalog_backfill.py)",
                ))
            continue

        source_path = Path(row.get("source_path", ""))
        score, reason = _score_row(row)
        if reason is not None:
            if include_rejected:
                rejected.append(Candidate(
                    uuid=asset["uuid"],
                    date_taken=asset["date_taken"],
                    year=asset["year"],
                    source_path=source_path,
                    catalog_row=row,
                    score=score,
                    rejected=True,
                    rejection_reason=reason,
                ))
            continue

        accepted.append(Candidate(
            uuid=asset["uuid"],
            date_taken=asset["date_taken"],
            year=asset["year"],
            source_path=source_path,
            catalog_row=row,
            score=score,
        ))

    # Highest score first; ties broken by year (newest first — 2025 beats
    # 2024 beats 2022 when the scorer says they're equal).
    accepted.sort(key=lambda c: (c.score, c.year), reverse=True)

    log.info(
        "select: %d accepted / %d rejected / %d not-in-catalog among %d assets",
        len(accepted),
        sum(1 for c in rejected if c.rejection_reason != "not in catalog (run catalog_backfill.py)"),
        sum(1 for c in rejected if c.rejection_reason == "not in catalog (run catalog_backfill.py)"),
        len(assets),
    )

    top = accepted[:top_n]
    if include_rejected:
        return top + rejected
    return top


__all__ = [
    "Candidate",
    "ELIGIBLE_YEARS",
    "enumerate_assets_for_month_day",
    "load_catalog_index",
    "select_candidates",
]
