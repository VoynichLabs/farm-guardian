# Author: GPT-5.5
# Date: 03-May-2026
# PURPOSE: DISABLED publish orchestrator for the on-this-day Facebook
#          pipeline. Dry-run candidate selection still works, but every
#          publish/auto-story path exits unless
#          FARM_ON_THIS_DAY_STORIES_ENABLED=1 is explicitly set.
#          Boss rejected the current selector/back-catalog fallback on
#          03-May-2026 after irrelevant old photos polluted daily Reel
#          material. Future redesign must be exact-date-only.
#
#          Historical behavior:
#          CLI orchestrator for the on-this-day Facebook pipeline.
#          Given a calendar date (default: today), select ranked
#          candidates from 2022/2024/2025, and either:
#            - --dry-run (default): write candidates to
#              data/on-this-day/{YYYY-MM-DD}-candidates.json so a
#              human (or a future reaction-gate implementation) can
#              review.
#            - --publish: export the top candidate's Photos master via
#              osxphotos, convert HEIC→JPEG if needed, commit to
#              farm-2026/public/photos/on-this-day/{YYYY-MM-DD}/ via
#              git_helper, and call fb_poster.crosspost_photo with
#              the resulting raw.githubusercontent URL + caption.
#
#          The split between --dry-run and --publish is the
#          quality gate for this pipeline. The camera-gem pipeline
#          uses Discord reactions; this one assumes a human (Boss or
#          another Claude) eyeballs the candidate JSON before
#          promoting it to a live post. A reaction-gate integration
#          is feasible but would require schema work on
#          image_archive; see the plan doc for why we deferred.
#
# SRP/DRY check: Pass — orchestration only. Reuses tools/pipeline/
#                git_helper.py (unchanged), tools/pipeline/fb_poster.py
#                (unchanged), tools/on_this_day/selector.py, and
#                tools/on_this_day/caption.py. The osxphotos export
#                step is a subprocess call — there is no Python
#                binding we prefer over the CLI.

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# Make tools.pipeline imports resolve when this module is run as
# `python3 -m tools.on_this_day.post_daily` from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.pipeline import fb_poster, git_helper  # noqa: E402

from .caption import CaptionSafetyError, compose as compose_caption  # noqa: E402
from .selector import Candidate, select_candidates  # noqa: E402

log = logging.getLogger("on_this_day.post_daily")

# --- Paths ---

FARM_GUARDIAN_ROOT = Path(__file__).resolve().parents[2]
FARM_2026_REPO = Path("/Users/macmini/Documents/GitHub/farm-2026")
CANDIDATES_DIR = FARM_GUARDIAN_ROOT / "data" / "on-this-day"
_ENABLE_ENV = "FARM_ON_THIS_DAY_STORIES_ENABLED"

# Posted-state ledger. Each UUID we've ever posted as a story is
# recorded here with the timestamp and lanes hit, so the --auto-story
# cadence loop doesn't cycle the same photo twice. Never auto-pruned;
# a human can hand-delete entries to force a repost.
POSTED_LEDGER = CANDIDATES_DIR / "posted.json"

# Transient export-failure blacklist. iCloud-only photos occasionally
# miss the osxphotos 10-minute download window; without this, the
# 90-min LaunchAgent would re-pick the same slow UUID on every fire
# and make zero progress. Entries live until midnight local — after
# that iCloud has usually warmed up.
EXPORT_FAILURE_LEDGER = CANDIDATES_DIR / "export-failures.json"

# Max candidates to try within one auto-story cycle. Direct-read
# exports (see _copy_photos_original) either succeed immediately or
# FileNotFoundError in <1s, so attempts are cheap. 8 attempts lets
# us skip past a handful of cloud-only photos without overlapping
# the next 90-min tick.
AUTO_STORY_MAX_ATTEMPTS = 8

# Boss strategy (2026-04-22): publish day-to-day as FB *stories*, not
# feed posts. Stories are cheap (24-hour lifespan, no feed dilution,
# 0–N per day is fine) and give us a performance signal — later we
# promote the winning stories to a curated feed post or carousel.
# So:
#   - `--publish` default → post every top candidate as its own Story
#   - `--carousel` → compose one feed carousel (the "best-of" promotion)
#   - `--single`   → one-feed-post-per-candidate (legacy)
#   - `--uuid`     → implies `--single`
DEFAULT_TOP_N = 15
DEFAULT_PUBLISH_N = 8  # stories are cheap; default to a wider set

# FB Page /feed + attached_media cap at 10 photos per post.
MAX_CAROUSEL_SIZE = 10


# ---------------------------------------------------------------------------
# Photos master export
# ---------------------------------------------------------------------------


def _copy_photos_original(source_path: Path, dest_dir: Path) -> Path:
    """Direct read of a Photos Library original and copy into dest_dir.
    Returns the path to the copied file.

    Replaces the earlier osxphotos-subprocess path (see commit
    history). The osxphotos CLI invokes `uv tools`' own Python
    interpreter, which does NOT hold a kTCCServicePhotos grant, and
    launchd would hang it indefinitely waiting on a TCC prompt that
    never surfaces (verified 2026-04-22 with zero-byte stderr tails).
    The venv python DOES hold the grant — that's why the selector
    reads Photos.sqlite successfully — so a direct file copy sidesteps
    the whole TCC cross-process mess.

    The catalog CSV already carries the canonical
    `source_path` for every indexed photo, pointing at
    `/Users/macmini/Pictures/Photos Library.photoslibrary/originals/
    {folder}/{UUID}.{heic|jpeg|png}`. We read from there directly.
    If the file isn't locally materialised (cloud-only), open() fails
    fast and the retry loop moves on.

    Raises:
      FileNotFoundError — source isn't on disk (cloud-only or deleted).
      OSError — any other read failure (treat as a non-retriable
                export issue; caller blacklists).
    """
    if not source_path:
        raise FileNotFoundError("catalog row has empty source_path")
    if not source_path.exists():
        raise FileNotFoundError(f"photo not local: {source_path}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / source_path.name
    shutil.copy2(source_path, dest)
    return dest


def _to_jpeg_if_needed(src: Path) -> Path:
    """HEIC → JPEG via sips (built-in macOS). PNG stays PNG — both are
    in git_helper's allow-list. Returns the path of the file that
    should be committed."""
    ext = src.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png"}:
        return src
    if ext != ".heic":
        raise ValueError(f"unsupported source extension {ext!r}: {src}")

    dst = src.with_suffix(".jpg")
    proc = subprocess.run(
        ["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0 or not dst.exists():
        raise RuntimeError(f"sips HEIC→JPEG failed: {proc.stderr.strip()}")
    return dst


# ---------------------------------------------------------------------------
# Dry-run output
# ---------------------------------------------------------------------------


def write_candidates_json(
    target_date: dt.date,
    candidates: list[Candidate],
    out_dir: Path = CANDIDATES_DIR,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{target_date.isoformat()}-candidates.json"

    payload = {
        "target_date": target_date.isoformat(),
        "eligible_years": [2022, 2024, 2025],
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "candidates": [],
    }
    for c in candidates:
        row = c.as_dict()
        # Also dry-compose the caption so the reviewer sees what would
        # be posted. Caption-safety failures show up in the output
        # rather than getting hidden.
        try:
            row["proposed_caption"] = compose_caption(c)
            row["caption_safe"] = True
        except CaptionSafetyError as e:
            row["proposed_caption"] = None
            row["caption_safe"] = False
            row["caption_reason"] = str(e)
        payload["candidates"].append(row)

    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    log.info("wrote %d candidate(s) → %s", len(candidates), out_path)
    return out_path


# ---------------------------------------------------------------------------
# Publish path
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Posted-state ledger
# ---------------------------------------------------------------------------


def _load_posted_ledger() -> dict:
    if not POSTED_LEDGER.exists():
        return {}
    try:
        return json.loads(POSTED_LEDGER.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("posted ledger at %s unreadable; treating as empty", POSTED_LEDGER)
        return {}


def _mark_posted(uuid: str, lanes: list[str], fb_post_id: Optional[str],
                 ig_post_id: Optional[str], raw_url: Optional[str]) -> None:
    POSTED_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    ledger = _load_posted_ledger()
    ledger[uuid] = {
        "posted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "lanes": lanes,
        "fb_post_id": fb_post_id,
        "ig_post_id": ig_post_id,
        "raw_url": raw_url,
    }
    # Atomic write so a concurrent LaunchAgent kickstart can't
    # truncate the ledger mid-rewrite.
    tmp = POSTED_LEDGER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ledger, indent=2, default=str), encoding="utf-8")
    tmp.replace(POSTED_LEDGER)


def already_posted(uuid: str) -> bool:
    return uuid in _load_posted_ledger()


def _load_export_failures() -> dict:
    if not EXPORT_FAILURE_LEDGER.exists():
        return {}
    try:
        return json.loads(EXPORT_FAILURE_LEDGER.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _record_export_failure(uuid: str, reason: str) -> None:
    EXPORT_FAILURE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    failures = _load_export_failures()
    failures[uuid] = {
        "failed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "local_date": dt.date.today().isoformat(),
        "reason": reason[:500],
    }
    tmp = EXPORT_FAILURE_LEDGER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(failures, indent=2, default=str), encoding="utf-8")
    tmp.replace(EXPORT_FAILURE_LEDGER)


def _export_failed_today(uuid: str) -> bool:
    """Treat a failure as blocking only within the same local day.
    By the next sunrise iCloud has usually caught up."""
    entry = _load_export_failures().get(uuid)
    if not entry:
        return False
    return entry.get("local_date") == dt.date.today().isoformat()


# ---------------------------------------------------------------------------
# IG story publishing — reuses tools.pipeline.ig_poster helpers
# ---------------------------------------------------------------------------


def _publish_ig_story(image_url: str) -> dict:
    """Post a 9:16 image to Instagram as a 24-hour Story.

    Delegates to the existing ig_poster helpers rather than
    reimplementing the Graph API dance. Never raises: returns
    {ok, ig_post_id, permalink, error}. Image must already be 9:16
    and publicly reachable at image_url (same contract as
    fb_poster.crosspost_photo_story).
    """
    result = {"ok": False, "ig_post_id": None, "permalink": None, "error": None}
    try:
        from tools.pipeline import ig_poster
        creds = ig_poster._load_credentials()
        container_id = ig_poster._create_story_container(
            ig_id=creds["ig_id"],
            image_url=image_url,
            user_token=creds["user_token"],
        )
        ig_poster._wait_for_container(container_id, creds["user_token"])
        publish_resp = ig_poster._publish(
            ig_id=creds["ig_id"],
            container_id=container_id,
            user_token=creds["user_token"],
        )
        # ig_poster._publish returns {"media_id": ..., "permalink": ...}.
        # Fallback to "id" defensively in case the wrapper shape ever drifts.
        result["ig_post_id"] = publish_resp.get("media_id") or publish_resp.get("id")
        result["permalink"] = publish_resp.get("permalink")
        result["ok"] = bool(result["ig_post_id"])
        if not result["ok"]:
            result["error"] = f"ig_poster._publish returned no media_id: {publish_resp}"
    except Exception as e:  # noqa: BLE001 — surface any failure as error field
        result["error"] = f"{type(e).__name__}: {e}"
        log.warning("ig story publish failed: %s", result["error"])
    return result


def _prepare_9x16(src: Path) -> Path:
    """Return a path to a 9:16 center-cropped JPEG derived from src.
    Reuses ig_poster._prepare_story_image so FB + IG consume the same
    aspect ratio (IG requires it; FB accepts anything; unifying
    avoids divergence bugs)."""
    from tools.pipeline import ig_poster
    return ig_poster._prepare_story_image(src)


def _export_and_stage(
    candidate: Candidate,
    target_date: dt.date,
    workdir: Path,
) -> Path:
    """Copy the Photos Library original for candidate into workdir,
    convert HEIC→JPEG if needed, rename to a stable public-friendly
    filename, return the staged path. Pure side-effect-on-disk; no
    git, no FB."""
    export_subdir = workdir / candidate.uuid
    raw_master = _copy_photos_original(candidate.source_path, export_subdir)
    jpeg = _to_jpeg_if_needed(raw_master)
    stable_name = (
        f"{target_date.isoformat()}-{candidate.year}-"
        f"{candidate.uuid}{jpeg.suffix.lower()}"
    )
    staged = workdir / stable_name
    shutil.copy2(jpeg, staged)
    return staged


def _compose_carousel_caption(
    candidates: list[Candidate], target_date: dt.date
) -> str:
    """Build a single carousel-level caption summarising the set. The
    per-image scene descriptions aren't shown once you have a grid post
    — FB only renders the /feed message. We lead with the date, then
    list the years present so Boss's friends see 'From 2024 & 2025'
    type framing."""
    years = sorted({c.year for c in candidates})
    year_phrase = " & ".join(str(y) for y in years) if years else "the archive"
    month_day = target_date.strftime("%B %-d")
    return f"On this day — {month_day}, from {year_phrase}."


def publish_carousel(
    candidates: list[Candidate],
    target_date: dt.date,
    dry_commit: bool = False,
) -> dict:
    """Export + commit every candidate, then publish one FB carousel
    post with all of them. Returns a dict describing the result.

    This is the default publish path (2026-04-22 onward). The
    single-photo lane is still available via publish_candidate() for
    callers that want granular control — but day-to-day, carousels
    are what Boss wants to see on the Page.
    """
    if not candidates:
        raise ValueError("publish_carousel: no candidates to publish")
    if len(candidates) > MAX_CAROUSEL_SIZE:
        log.warning(
            "carousel clipped to %d (FB /feed attached_media cap)", MAX_CAROUSEL_SIZE
        )
        candidates = candidates[:MAX_CAROUSEL_SIZE]

    caption = _compose_carousel_caption(candidates, target_date)

    with tempfile.TemporaryDirectory(prefix="on-this-day-carousel-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        staged_paths: list[Path] = []
        for cand in candidates:
            staged = _export_and_stage(cand, target_date, tmpdir_path)
            staged_paths.append(staged)

        if dry_commit:
            log.info(
                "dry_commit: %d photos staged at %s, skipping farm-2026 + FB",
                len(staged_paths), tmpdir_path,
            )
            return {
                "uuids": [c.uuid for c in candidates],
                "caption": caption,
                "image_urls": [],
                "fb_post_id": None,
                "error": None,
                "dry_commit": True,
            }

        subdir = f"on-this-day/{target_date.isoformat()}"
        raw_urls: list[str] = []
        for cand, staged in zip(candidates, staged_paths):
            commit_msg = (
                f"on-this-day: {target_date.isoformat()} carousel — "
                f"{cand.year} {cand.uuid[:8]} [score={cand.score}]"
            )
            _, raw_url = git_helper.commit_image_to_farm_2026(
                local_image=staged,
                subdir=subdir,
                repo_path=FARM_2026_REPO,
                commit_message=commit_msg,
            )
            raw_urls.append(raw_url)
        log.info("committed %d photos to farm-2026", len(raw_urls))

        fb_result = fb_poster.crosspost_carousel(image_urls=raw_urls, caption=caption)
        log.info("fb_poster carousel result: %s", fb_result)

        return {
            "uuids": [c.uuid for c in candidates],
            "caption": caption,
            "image_urls": raw_urls,
            "fb_post_id": fb_result.get("fb_post_id"),
            "error": fb_result.get("error"),
            "dry_commit": False,
        }


def _publish_one_story(
    cand: Candidate,
    target_date: dt.date,
    workdir: Path,
    dry_commit: bool,
    fb: bool = True,
    ig: bool = True,
) -> dict:
    """Export a single candidate, prep 9:16, commit to farm-2026, and
    publish as Story on FB and/or IG. Returns a result dict — always,
    never raises. Records to the posted ledger on any successful lane.
    """
    result: dict = {
        "uuid": cand.uuid, "year": cand.year, "score": cand.score,
        "caption": None, "image_url": None,
        "fb_post_id": None, "ig_post_id": None, "ig_permalink": None,
        "lanes": [], "error": None, "lane": "story",
    }

    try:
        result["caption"] = compose_caption(cand)
    except CaptionSafetyError as e:
        result["error"] = f"CaptionSafetyError: {e}"
        log.warning("story: skipping %s (unsafe caption): %s", cand.uuid, e)
        return result

    try:
        raw_master = _copy_photos_original(cand.source_path, workdir / cand.uuid)
        source_jpeg = _to_jpeg_if_needed(raw_master)
    except FileNotFoundError as e:
        # Cloud-only / missing-from-disk. Blacklist for today; tomorrow
        # Photos.app's background sync may have pulled it down.
        result["error"] = f"photo not local: {e}"
        log.warning("photo not local for %s — blacklisting until tomorrow", cand.uuid)
        _record_export_failure(cand.uuid, str(e))
        return result
    except Exception as e:
        result["error"] = f"export: {e!r}"
        log.exception("export failed for %s", cand.uuid)
        _record_export_failure(cand.uuid, repr(e))
        return result

    # Prepare 9:16. Both FB Page Stories and IG Stories render as
    # 9:16; using a single prepared image keeps the published URL
    # identical for both lanes.
    try:
        staged_9x16 = _prepare_9x16(source_jpeg)
    except Exception as e:
        result["error"] = f"9:16 prep: {e!r}"
        log.exception("9:16 prep failed for %s", cand.uuid)
        return result

    stable_name = (
        f"{target_date.isoformat()}-{cand.year}-{cand.uuid}-9x16.jpg"
    )
    staged = workdir / stable_name
    shutil.copy2(staged_9x16, staged)
    try:
        staged_9x16.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass

    if dry_commit:
        log.info("dry_commit: 9:16 staged at %s; skipping farm-2026 + FB + IG", staged)
        return result

    subdir = f"on-this-day/{target_date.isoformat()}/stories"
    commit_msg = (
        f"on-this-day story: {target_date.isoformat()} — "
        f"{cand.year} {cand.uuid[:8]} [score={cand.score}]"
    )
    try:
        _, raw_url = git_helper.commit_image_to_farm_2026(
            local_image=staged,
            subdir=subdir,
            repo_path=FARM_2026_REPO,
            commit_message=commit_msg,
        )
    except Exception as e:
        result["error"] = f"git_helper: {e!r}"
        log.exception("farm-2026 commit failed for %s", cand.uuid)
        return result
    result["image_url"] = raw_url

    # Lane A: FB Page Story.
    if fb:
        fb_result = fb_poster.crosspost_photo_story(image_url=raw_url)
        log.info(
            "FB story %s → ok=%s fb_post_id=%s",
            cand.uuid[:8], fb_result.get("ok"), fb_result.get("fb_post_id"),
        )
        if fb_result.get("fb_post_id"):
            result["fb_post_id"] = fb_result["fb_post_id"]
            result["lanes"].append("fb_story")
        elif fb_result.get("error"):
            # Capture FB error but keep attempting IG — one lane's
            # failure should not gate the other.
            result["error"] = f"fb: {fb_result['error']}"

    # Lane B: IG Story.
    if ig:
        ig_result = _publish_ig_story(image_url=raw_url)
        log.info(
            "IG story %s → ok=%s ig_post_id=%s",
            cand.uuid[:8], ig_result.get("ok"), ig_result.get("ig_post_id"),
        )
        if ig_result.get("ig_post_id"):
            result["ig_post_id"] = ig_result["ig_post_id"]
            result["ig_permalink"] = ig_result.get("permalink")
            result["lanes"].append("ig_story")
        elif ig_result.get("error"):
            prior = result["error"]
            result["error"] = (
                f"{prior} | ig: {ig_result['error']}" if prior
                else f"ig: {ig_result['error']}"
            )

    if result["lanes"]:
        _mark_posted(
            uuid=cand.uuid,
            lanes=result["lanes"],
            fb_post_id=result["fb_post_id"],
            ig_post_id=result["ig_post_id"],
            raw_url=raw_url,
        )

    return result


def publish_stories(
    candidates: list[Candidate],
    target_date: dt.date,
    dry_commit: bool = False,
) -> list[dict]:
    """Publish each candidate as its own 24-hour FB Page Story.

    This is the default publish path as of 2026-04-22. Stories are
    cheap — they don't dilute the feed and they give us a per-photo
    performance signal (impressions / reactions / taps) that a carousel
    doesn't. The promotion loop is: post many stories → read insights
    → pick winners → re-publish those as a curated feed carousel via
    `--carousel` on the chosen date.

    Stories don't take captions (FB Graph API limitation, same as IG).
    The per-photo Qwen caption still flows into the audit JSON so Boss
    has the semantic context when reviewing what won.
    """
    results: list[dict] = []
    if not candidates:
        return results

    with tempfile.TemporaryDirectory(prefix="on-this-day-stories-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        for cand in candidates:
            results.append(
                _publish_one_story(cand, target_date, tmpdir_path, dry_commit=dry_commit)
            )

    return results


def publish_candidate(
    candidate: Candidate,
    target_date: dt.date,
    dry_commit: bool = False,
) -> dict:
    """Single-photo publish (legacy path — kept for --uuid overrides).
    Export the candidate, commit to farm-2026, call fb_poster.
    Returns a dict with uuid, caption, image_url, fb_post_id, error."""
    caption = compose_caption(candidate)

    with tempfile.TemporaryDirectory(prefix="on-this-day-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        staged = _export_and_stage(candidate, target_date, tmpdir_path)

        if dry_commit:
            log.info("dry_commit: skipping farm-2026 push. exported=%s", staged)
            return {
                "uuid": candidate.uuid,
                "caption": caption,
                "image_url": None,
                "fb_post_id": None,
                "error": None,
                "dry_commit": True,
            }

        subdir = f"on-this-day/{target_date.isoformat()}"
        commit_msg = (
            f"on-this-day: {target_date.isoformat()} — {candidate.year} photo "
            f"{candidate.uuid[:8]} [score={candidate.score}]"
        )
        committed_path, raw_url = git_helper.commit_image_to_farm_2026(
            local_image=staged,
            subdir=subdir,
            repo_path=FARM_2026_REPO,
            commit_message=commit_msg,
        )
        log.info("committed to farm-2026: %s → %s", committed_path, raw_url)

        fb_result = fb_poster.crosspost_photo(image_url=raw_url, caption=caption)
        log.info("fb_poster result: %s", fb_result)

        return {
            "uuid": candidate.uuid,
            "caption": caption,
            "image_url": raw_url,
            "fb_post_id": fb_result.get("fb_post_id"),
            "error": fb_result.get("error"),
            "dry_commit": False,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _pick_next_unposted_for_today(
    target_date: dt.date, top_n: int, exclude: set[str] | None = None,
) -> Optional[Candidate]:
    """Return the highest-scoring candidate for target_date that is
    NOT in the posted ledger, the today-failed export blacklist, or
    the in-cycle exclude set (UUIDs we've already tried this fire).
    """
    exclude = exclude or set()
    candidates = select_candidates(target_date=target_date, top_n=top_n, include_rejected=False)
    for cand in candidates:
        if cand.uuid in exclude or already_posted(cand.uuid) or _export_failed_today(cand.uuid):
            continue
        return cand
    return None


def _pick_fallback_from_back_catalog(exclude: set[str] | None = None) -> Optional[Candidate]:
    """When today's on-this-day pool is exhausted, pick the
    highest-scoring unposted catalog row from any date in 2022/2024/2025.

    Scans ALL Photos.sqlite assets with a date in the eligible years
    (no month-day filter), joins the catalog, filters-and-ranks via
    the same selector scoring path, and returns the top unposted.

    Heavier than the normal on-this-day query (full table scan over
    ~78k rows) but we only pay it when the daily pool is dry — and the
    posted ledger shrinks the usable set over time, so "exhausted" is
    the eventual steady state.
    """
    from .selector import (
        _open_photos_db_readonly,
        _cocoa_to_datetime,
        _score_row,
        load_catalog_index,
        ELIGIBLE_YEARS,
    )
    exclude = exclude or set()
    catalog = load_catalog_index()
    ledger = _load_posted_ledger()
    failed_today = {
        u for u, v in _load_export_failures().items()
        if v.get("local_date") == dt.date.today().isoformat()
    }
    skip = exclude | set(ledger.keys()) | failed_today
    eligible_years = set(ELIGIBLE_YEARS)

    scored: list[tuple[int, str, dt.datetime, dict]] = []  # (score, uuid, taken, row)
    with _open_photos_db_readonly() as conn:
        rows = conn.execute(
            "SELECT ZUUID, ZDATECREATED FROM ZASSET "
            "WHERE ZTRASHEDDATE IS NULL AND ZHIDDEN = 0 AND ZKIND = 0 "
            "  AND ZDATECREATED IS NOT NULL"
        )
        for row in rows:
            uuid = row["ZUUID"]
            if not uuid or uuid in skip:
                continue
            cat_row = catalog.get(uuid)
            if cat_row is None:
                continue
            try:
                taken_local = _cocoa_to_datetime(row["ZDATECREATED"]).astimezone()
            except (TypeError, ValueError, OSError):
                continue
            if taken_local.year not in eligible_years:
                continue
            score, reason = _score_row(cat_row)
            if reason is not None or score <= 0:
                continue
            scored.append((score, uuid, taken_local, cat_row))

    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[2].year), reverse=True)
    top_score, uuid, taken_local, cat_row = scored[0]
    source_path = Path(cat_row.get("source_path", ""))
    return Candidate(
        uuid=uuid,
        date_taken=taken_local,
        year=taken_local.year,
        source_path=source_path,
        catalog_row=cat_row,
        score=top_score,
    )


def run_auto_story_cycle(dry_commit: bool = False) -> dict:
    """Disabled unless FARM_ON_THIS_DAY_STORIES_ENABLED=1.

    Historical behavior: one LaunchAgent tick. Walks the candidate queue top-down
    (today's on-this-day pool first, back-catalog fallback next),
    skipping UUIDs already posted or failed-today. Publishes the first
    candidate that exports successfully as a FB + IG Story and records
    it to the posted ledger. Retries up to AUTO_STORY_MAX_ATTEMPTS
    times within a single cycle so one slow iCloud download doesn't
    block the whole run.

    Always returns a dict describing what happened; never raises. An
    empty return (no_candidate=True) is a normal steady-state result
    when the catalog is genuinely exhausted.
    """
    if os.environ.get(_ENABLE_ENV) != "1":
        log.warning(
            "auto-story disabled; set %s=1 only after exact-date selection is redesigned",
            _ENABLE_ENV,
        )
        return {
            "posted": False,
            "disabled": True,
            "target_date": dt.date.today().isoformat(),
            "reason": "on-this-day publishing disabled",
        }

    target_date = dt.date.today()
    tried: set[str] = set()
    attempts: list[dict] = []
    final_result: Optional[dict] = None

    for attempt in range(AUTO_STORY_MAX_ATTEMPTS):
        cand = _pick_next_unposted_for_today(target_date, top_n=15, exclude=tried)
        source = "on-this-day"
        if cand is None:
            cand = _pick_fallback_from_back_catalog(exclude=tried)
            source = "back-catalog"
        if cand is None:
            log.info("auto-story: no more unposted candidates — nothing to do")
            if attempt == 0:
                final_result = {
                    "posted": False,
                    "no_candidate": True,
                    "target_date": target_date.isoformat(),
                    "attempts": attempts,
                }
            break

        tried.add(cand.uuid)
        log.info(
            "auto-story attempt %d: %s source=%s year=%s score=%s",
            attempt + 1, cand.uuid[:8], source, cand.year, cand.score,
        )
        with tempfile.TemporaryDirectory(prefix="on-this-day-auto-") as tmpdir:
            result = _publish_one_story(
                cand, target_date, Path(tmpdir), dry_commit=dry_commit,
            )
        result["source"] = source
        attempts.append({
            "uuid": cand.uuid, "source": source, "year": cand.year, "score": cand.score,
            "lanes": result.get("lanes"), "error": result.get("error"),
        })

        if result.get("lanes"):
            result["posted"] = True
            result["no_candidate"] = False
            result["target_date"] = target_date.isoformat()
            result["attempts"] = attempts
            final_result = result
            break

        # IG's 25-publish-per-rolling-24h quota is shared with the
        # gem-pipeline story lane. When it's exhausted Graph 403s;
        # further retries in this tick would be wasted compute. Bail
        # cleanly so the next 90-min tick can retry once the rolling
        # window has freed a slot.
        err_str = str(result.get("error") or "")
        if "403" in err_str or "rate" in err_str.lower() or "limit" in err_str.lower():
            log.warning(
                "auto-story: IG/FB publish quota exhausted; stopping batch"
            )
            result["posted"] = False
            result["no_candidate"] = False
            result["quota_exhausted"] = True
            result["target_date"] = target_date.isoformat()
            result["attempts"] = attempts
            final_result = result
            break

        # Dry-commit deliberately stops before git/FB/IG, but the
        # export succeeded — treat it as "done for this cycle" so the
        # smoke-test path doesn't hammer 5 candidates.
        if dry_commit and not result.get("error"):
            result["posted"] = False
            result["no_candidate"] = False
            result["target_date"] = target_date.isoformat()
            result["attempts"] = attempts
            final_result = result
            break

        log.warning(
            "auto-story attempt %d failed (%s); trying next candidate",
            attempt + 1, result.get("error"),
        )

    if final_result is None:
        final_result = {
            "posted": False,
            "no_candidate": False,
            "exhausted_attempts": True,
            "target_date": target_date.isoformat(),
            "attempts": attempts,
        }

    audit_path = CANDIDATES_DIR / f"auto-story-{target_date.isoformat()}.ndjson"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "fired_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            **final_result,
        }, default=str) + "\n")
    log.info("auto-story: appended audit row to %s", audit_path)
    return final_result


def _parse_date(s: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Disabled on-this-day publisher. Dry-run selection is allowed; "
            "publish paths require FARM_ON_THIS_DAY_STORIES_ENABLED=1."
        ),
    )
    p.add_argument("--date", type=_parse_date, default=None,
                   help="Target calendar date YYYY-MM-DD. Default: today (local).")
    p.add_argument("--top-n", type=int, default=DEFAULT_TOP_N,
                   help=f"Candidates to rank. Default: {DEFAULT_TOP_N}.")
    p.add_argument("--publish", action="store_true",
                   help="Disabled unless FARM_ON_THIS_DAY_STORIES_ENABLED=1.")
    p.add_argument("--publish-n", type=int, default=DEFAULT_PUBLISH_N,
                   help=f"How many of the top candidates to publish when --publish. "
                        f"Default: {DEFAULT_PUBLISH_N}. Carousel lane caps at "
                        f"{MAX_CAROUSEL_SIZE} by FB's attached_media limit; story "
                        f"lane has no hard cap but be reasonable.")
    lane = p.add_mutually_exclusive_group()
    lane.add_argument("--carousel", action="store_true",
                      help="Publish the top candidates as one FB feed carousel "
                           "(the 'best-of' promotion lane). Use this after "
                           "reviewing story insights to curate a keeper post.")
    lane.add_argument("--single", action="store_true",
                      help="Publish as separate feed posts (one per candidate). "
                           "Rarely wanted; prefer stories or carousel.")
    p.add_argument("--uuid", type=str, default=None,
                   help="Publish a specific UUID (must be in today's candidate pool). "
                        "Implies --single. Requires --publish.")
    p.add_argument("--include-rejected", action="store_true",
                   help="Dry-run only: include filtered rows with rejection_reason.")
    p.add_argument("--dry-commit", action="store_true",
                   help="Publish path: export + caption but skip farm-2026 push + FB call.")
    p.add_argument("--auto-story", action="store_true",
                   help="Disabled unless FARM_ON_THIS_DAY_STORIES_ENABLED=1.")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _parse_args()

    if (args.auto_story or args.publish) and os.environ.get(_ENABLE_ENV) != "1":
        log.warning(
            "on-this-day publish path disabled; set %s=1 only after exact-date "
            "selection is redesigned",
            _ENABLE_ENV,
        )
        return 0

    # Auto-story short-circuits before the candidate write-JSON path
    # because it's driven by the posted ledger, not by top-N selection.
    if args.auto_story:
        cycle = run_auto_story_cycle(dry_commit=args.dry_commit)
        return 0 if (cycle.get("posted") or cycle.get("no_candidate")) else 1

    target_date = args.date or dt.date.today()

    candidates = select_candidates(
        target_date=target_date,
        top_n=args.top_n,
        include_rejected=args.include_rejected and not args.publish,
    )

    if not candidates:
        log.warning("no candidates for %s across years 2022/2024/2025", target_date.isoformat())
        if not args.publish:
            # Still write an (empty) candidate JSON so ops can tell
            # the script ran and there was genuinely nothing to post.
            write_candidates_json(target_date, [])
        return 0

    # Dry-run path.
    if not args.publish:
        out = write_candidates_json(target_date, candidates)
        print(f"Wrote {len(candidates)} candidate(s) → {out}")
        print("Review the JSON, then re-run with --publish (optionally --uuid <Z>)")
        return 0

    # Publish path.
    to_publish: list[Candidate]
    force_single = args.single or bool(args.uuid)
    use_carousel = args.carousel and not force_single
    use_stories = not force_single and not use_carousel  # default lane

    if args.uuid:
        matched = [c for c in candidates if c.uuid == args.uuid]
        if not matched:
            log.error("requested --uuid %s not in today's candidate pool", args.uuid)
            return 4
        to_publish = matched
    else:
        to_publish = [c for c in candidates if not c.rejected][: args.publish_n]

    if not to_publish:
        log.warning("publish: no eligible candidates after rejection filter")
        return 0

    results: list[dict] = []
    any_error = False
    lane_name: str

    if use_stories:
        lane_name = "story"
        story_results = publish_stories(to_publish, target_date, dry_commit=args.dry_commit)
        results.extend(story_results)
        any_error = any(r.get("error") for r in story_results)
    elif use_carousel:
        lane_name = "carousel"
        try:
            results.append(publish_carousel(to_publish, target_date, dry_commit=args.dry_commit))
            if results[0].get("error"):
                any_error = True
        except Exception as e:
            log.exception("carousel publish failed")
            results.append({"uuids": [c.uuid for c in to_publish], "error": repr(e)})
            any_error = True
    else:
        lane_name = "single"
        for cand in to_publish:
            try:
                results.append(publish_candidate(cand, target_date, dry_commit=args.dry_commit))
            except CaptionSafetyError as e:
                log.warning("skipping %s: caption unsafe (%s)", cand.uuid, e)
                results.append({"uuid": cand.uuid, "error": str(e), "fb_post_id": None})
                any_error = True
            except Exception as e:
                log.exception("publish failed for %s", cand.uuid)
                results.append({"uuid": cand.uuid, "error": repr(e), "fb_post_id": None})
                any_error = True

    # Persist the publish result alongside the dry-run artifact so
    # there's an audit trail.
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    result_path = CANDIDATES_DIR / f"{target_date.isoformat()}-publish-result.json"
    result_path.write_text(json.dumps({
        "target_date": target_date.isoformat(),
        "published_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "lane": lane_name,
        "dry_commit": args.dry_commit,
        "results": results,
    }, indent=2, default=str), encoding="utf-8")
    log.info("wrote publish result → %s", result_path)

    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())
