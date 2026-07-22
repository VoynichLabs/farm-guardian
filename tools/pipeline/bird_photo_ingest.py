# Author: Claude Opus 4.8
# Date: 2026-07-21
# PURPOSE: Ingest a Discord-dropped bird photo + its caption and, when the
#          caption names a single roster bird, wire that photo in as the
#          bird's portrait in farm-2026's content/flock-profiles.json —
#          renamed, committed into public/photos/birds/, and pushed. This is
#          the roster-write half that the existing gem/IG pipeline never did
#          (that pipeline only writes the IG-hosting dirs
#          stories/carousel/birdcatraz and never touches the roster JSON).
#
#          Control flow: parse bird name(s) from the caption (substring match
#          against flock_birds[].name, built ON TOP of roster.match_name —
#          match_name stays exact-only by design) -> run vlm_enricher.enrich()
#          for a descriptor slug + bird_count + composition -> apply the
#          ambiguity gate (group shots / count mismatches are flagged, never
#          guessed) -> rename to IMG_<orig>-<slug>-<descriptor>-<DDmonYYYY>.jpg
#          -> commit the image via git_helper.commit_image_to_farm_2026 -> for
#          a single named bird, set its "photo" field and commit the JSON in a
#          second, path-scoped commit -> verify both landed with git ls-tree ->
#          return a structured result dict.
#
#          Boss's caption IS the identity signal (fine-grained auto-ID was
#          hard-disabled in v2.38.2 for confident false positives). The VLM
#          never names the bird; it only supplies count/composition for the
#          ambiguity gate and a free-text descriptor for the filename slug.
#
#          Deliberate scope limits (v1): never writes stories/carousel/
#          birdcatraz; never edits content/hatches/2026/*.md (ornitharch birds
#          get a hatch_file_needs_update flag instead); never sends Discord
#          messages itself (returns `message` for a caller/hook to relay).
#
# SRP/DRY check: Pass — reuses git_helper (image commit + git plumbing),
#                roster (canonical name list), and vlm_enricher (vision). The
#                net-new responsibility here is exactly caption->roster-portrait
#                wiring. The "private" git_helper helpers (_git,
#                _current_branch, _push_with_rebase_retry) are imported on
#                purpose rather than reimplemented — see the import comment.

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

# --- sys.path bootstrap -----------------------------------------------------
# vlm_enricher.enrich() -> prompt_for() does `from tools.pipeline.roster import
# ...`, and our own imports below use the same `tools.pipeline` package root.
# This MUST run before those imports so a direct `python3 bird_photo_ingest.py`
# CLI run resolves them even without PYTHONPATH set. The hook also sets
# PYTHONPATH (belt); this in-module insert is the suspenders. (E402 on the
# imports below is accepted — vlm_enricher's own __main__ does the same.)
_FARM_GUARDIAN_ROOT = Path(__file__).resolve().parents[2]
if str(_FARM_GUARDIAN_ROOT) not in sys.path:
    sys.path.insert(0, str(_FARM_GUARDIAN_ROOT))

# Reuse git_helper's proven git plumbing. commit_image_to_farm_2026 +
# GitHelperError are the documented public surface; _git / _current_branch /
# _push_with_rebase_retry are imported deliberately (not reimplemented) so the
# JSON commit shares the exact same non-interactive env, detached-HEAD guard,
# and concurrent-push rebase-retry as the image commit. DRY over duplication.
from tools.pipeline.git_helper import (  # noqa: E402
    GitHelperError,
    _current_branch,
    _git,
    _push_with_rebase_retry,
    commit_image_to_farm_2026,
)
from tools.pipeline.roster import get_all_names  # noqa: E402
from tools.pipeline.vlm_enricher import (  # noqa: E402
    EnricherError,
    ModelNotLoaded,
    enrich,
)

log = logging.getLogger("pipeline.bird_photo_ingest")

# --- paths ------------------------------------------------------------------
FARM_2026_ROOT = Path.home() / "Documents" / "GitHub" / "farm-2026"
FLOCK_PROFILES_PATH = FARM_2026_ROOT / "content" / "flock-profiles.json"
_PIPE_DIR = Path(__file__).resolve().parent

# Media types git_helper will host; we mirror the check so we fail with a clear
# message before touching the VLM rather than deep inside the git step.
_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}

# Cross-actor de-dup. The whole point of this module is to be the SINGLE code
# path that files a bird photo — the OpenClaw hook calls it, and any interactive
# agent (Bubba) should call it too instead of hand-committing. To make two
# near-simultaneous filings of the SAME bird safe, we take a short-lived
# per-subject claim before committing. First writer wins; the second no-ops with
# status "deduped". A claim older than the TTL is treated as stale and taken
# over (so a crashed run can't wedge a bird forever).
_CLAIM_DIR = Path.home() / ".openclaw" / "state" / "bird-ingest-claims"
_CLAIM_TTL_S = 600  # 10 minutes

# The literal word "suspected" must NEVER appear in a committed filename (it
# leaked into an earlier hand-named file — IMG_6227-ingebird-suspected-... —
# and reads as an unresolved guess). Plus generic hedges we strip from the
# VLM-derived descriptor so the slug stays a real visual descriptor.
_FORBIDDEN_DESCRIPTOR_WORDS = {
    "suspected", "likely", "maybe", "possibly", "probable", "probably",
    "unconfirmed", "tbd", "uncertain", "unknown", "possible",
}

# Common words that carry no descriptive value in a filename slug.
_DESCRIPTOR_STOPWORDS = {
    "the", "this", "that", "these", "those", "and", "with", "for", "her",
    "his", "its", "their", "one", "two", "three", "single", "solo", "shot",
    "photo", "image", "picture", "close", "closeup", "close-up", "standing",
    "sitting", "looking", "here", "there", "being", "held", "hand", "arm",
    "bird", "birds", "chick", "chicks", "pullet", "cockerel", "hen", "rooster",
    "young", "little", "small", "big", "farm",
}


# --- name matching ----------------------------------------------------------
def _core_name(name: str) -> str:
    """Strip a trailing parenthetical count/qualifier from a roster name so it
    can be matched against free caption text — e.g. "White turkeys (3)" ->
    "white turkeys", "Henridotta" -> "henridotta"."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", name or "").strip()


def match_caption_names(caption: str) -> list[str]:
    """Return the canonical roster names whose (parenthetical-stripped) name
    appears as a whole word/phrase in the caption, case-insensitively.

    This is the substring variant the roster module explicitly says to build
    ON TOP of match_name() — roster.match_name() stays exact-match-only by
    design (a false substring match would write a wrong identity into the
    archive). Word boundaries keep near-identical siblings distinct
    ("Henriella"/"Henriessa"/"Henridotta" never cross-match). Includes
    deceased birds — an old-photo caption may legitimately name one.
    """
    caption_l = (caption or "").lower()
    if not caption_l.strip():
        return []
    matched: list[str] = []
    for name in get_all_names(include_deceased=True):
        core = _core_name(name).lower()
        if not core:
            continue
        pattern = r"\b" + re.escape(core) + r"\b"
        if re.search(pattern, caption_l) and name not in matched:
            matched.append(name)
    return matched


# --- slug / filename helpers ------------------------------------------------
def _slug(text: str) -> str:
    """Lowercase, non-alnum -> single dash, trimmed."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _descriptor_from_vlm(meta: dict) -> str:
    """Build a short (<= 2 token) visual descriptor slug for the filename from
    the VLM output. There is no dedicated color field in the schema, so we mine
    caption_draft (free text) for the first couple of meaningful words, dropping
    stopwords and any hedge word (never emits "suspected"). Falls back to the
    structured `composition`/`scene` enums when the caption yields nothing.
    """
    caption = meta.get("caption_draft") or ""
    tokens = re.findall(r"[a-z0-9]+", caption.lower())
    picked: list[str] = []
    for tok in tokens:
        if len(tok) < 3:
            continue
        if tok in _DESCRIPTOR_STOPWORDS or tok in _FORBIDDEN_DESCRIPTOR_WORDS:
            continue
        picked.append(tok)
        if len(picked) >= 2:
            break
    if not picked:
        comp = meta.get("composition") or ""
        if comp and comp not in ("empty", "cluttered"):
            picked = [comp]
        else:
            scene = meta.get("scene") or ""
            picked = [scene] if scene and scene != "other" else ["portrait"]
    slug = _slug("-".join(picked))
    # Defense in depth: strip the forbidden word even if it ever slips through.
    slug = re.sub(r"suspected-?", "", slug).strip("-")
    return slug or "portrait"


def _parse_orig(image_path: str) -> str:
    """Parse the original iPhone stem from the inbound basename.

    Canonical inbound is "IMG_7655---<uuid>.jpg" -> "IMG_7655". Falls back to
    the chunk before a "---" separator, then to the slugified stem.
    """
    stem = Path(image_path).stem
    m = re.match(r"(IMG_\d+)", stem, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    head = re.split(r"-{2,}", stem)[0].strip("-")
    if head:
        return head
    return _slug(stem) or "IMG"


def _build_filename(orig: str, name_slug: str, descriptor: str, ext: str) -> str:
    """IMG_<orig>-<slug>-<descriptor>-<DDmonYYYY><ext>, empty parts dropped."""
    date_tag = datetime.now().strftime("%d%b%Y").lower()  # e.g. 21jul2026
    parts = [p for p in (orig, name_slug, descriptor, date_tag) if p]
    base = "-".join(parts)
    # Final guard: the committed filename must never contain "suspected".
    base = re.sub(r"-?suspected-?", "-", base, flags=re.IGNORECASE).strip("-")
    base = re.sub(r"-{2,}", "-", base)
    return f"{base}{ext}"


# --- VLM --------------------------------------------------------------------
def _run_vlm(image_bytes: bytes) -> Optional[dict]:
    """Run vlm_enricher.enrich() and return its `metadata` dict, or None if the
    model is unavailable / the call fails. We deliberately fail SAFE: without a
    bird_count we cannot tell a solo shot from a group, so a VLM miss must block
    the roster write rather than guess (plan section 4: "flag, never guess")."""
    cfg = json.loads((_PIPE_DIR / "config.json").read_text())
    schema = json.loads((_PIPE_DIR / "schema.json").read_text())  # whole file
    prompt = (_PIPE_DIR / "prompt.md").read_text()

    # A handheld iPhone bird portrait has no Guardian camera; use usb-cam's
    # context as soft prompt guidance (we only consume count/composition/
    # caption from the result, so the exact context is not load-bearing).
    cameras = cfg.get("cameras", {})
    camera_name = "usb-cam" if "usb-cam" in cameras else next(iter(cameras), "usb-cam")
    cam_ctx = cameras.get(camera_name, {}).get("context", "")

    lm_base = cfg.get("lm_studio_base", "http://localhost:1234")
    model_id = cfg.get("vlm_model_id", "qwen/qwen3-vl-4b")
    try:
        # enrich() does NOT auto-load the model; ensure it once first.
        from tools.pipeline.vlm_enricher import ensure_model_loaded
        ensure_model_loaded(lm_base, model_id, cfg.get("vlm_load_context_length", 16384))
        result = enrich(
            image_bytes=image_bytes,
            camera_name=camera_name,
            camera_context=cam_ctx,
            lm_base=lm_base,
            model_id=model_id,
            prompt_template=prompt,
            schema=schema,
            max_tokens=cfg.get("vlm_max_tokens", 600),
            temperature=cfg.get("vlm_temperature", 0.2),
            timeout=cfg.get("vlm_timeout_seconds", 300),
        )
        return result.get("metadata")
    except (ModelNotLoaded, EnricherError) as exc:
        log.warning("bird_photo_ingest: VLM unavailable: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        # Any other vision failure (e.g. a raw requests.ConnectionError when LM
        # Studio is unreachable) must ALSO fail safe: without bird_count we
        # cannot tell a solo shot from a group, so block the write rather than
        # let it surface as a generic "error" upstream.
        log.warning(
            "bird_photo_ingest: VLM call failed (%s) — treating as unavailable", exc
        )
        return None


# --- roster JSON write ------------------------------------------------------
def _find_record(bird_name: str) -> Optional[dict]:
    try:
        data = json.loads(FLOCK_PROFILES_PATH.read_text())
    except Exception as exc:  # noqa: BLE001
        log.warning("bird_photo_ingest: cannot read roster: %s", exc)
        return None
    for bird in data.get("flock_birds", []):
        if bird.get("name") == bird_name:
            return bird
    return None


def _set_photo_and_commit(
    bird_name: str,
    photo_value: str,
    commit_message: str,
    photo_date: Optional[str] = None,
    caption: Optional[str] = None,
) -> bool:
    """Set flock_birds[name].photo = photo_value (the current hero portrait) AND
    append the shot to that bird's append-only photos[] history (the aging
    timeline — every picture we've ever had of the bird). Then commit ONLY
    content/flock-profiles.json (path-scoped) and push with rebase-retry.

    The path-scoped `git commit -- <file>` ignores anything else a concurrent
    pipeline may have left staged and keeps the working tree clean, so
    _push_with_rebase_retry's `pull --rebase` stays clean. Returns True if a
    change was written+committed, False if it was already up to date.
    """
    src = FLOCK_PROFILES_PATH.read_text()
    data = json.loads(src)
    changed = False
    for bird in data.get("flock_birds", []):
        if bird.get("name") == bird_name:
            if bird.get("photo") != photo_value:
                bird["photo"] = photo_value
                changed = True
            # Append to the photo history (dedup by filename basename) so it
            # accumulates instead of overwriting. `photo` above is the current
            # portrait; photos[] is the full timeline.
            history = bird.setdefault("photos", [])
            base = photo_value.split("/")[-1]
            if not any(p.get("file", "").split("/")[-1] == base for p in history):
                entry: dict = {"file": photo_value}
                if photo_date:
                    entry["date"] = photo_date
                if caption:
                    entry["caption"] = caption
                history.append(entry)
                # Chronological, undated last — matches the /flock render order.
                history.sort(
                    key=lambda p: (p.get("date") is None, p.get("date") or "", p.get("file", ""))
                )
                changed = True
            break
    if not changed:
        return False

    # ensure_ascii default (True) matches the file's existing \uXXXX escapes, so
    # the diff is just this bird's change — writing literal UTF-8 (ensure_ascii=
    # False) would un-escape the WHOLE file and churn every unicode line.
    FLOCK_PROFILES_PATH.write_text(
        json.dumps(data, indent=2) + ("\n" if src.endswith("\n") else "")
    )

    rel = "content/flock-profiles.json"
    status = _git(FARM_2026_ROOT, "status", "--porcelain", "--", rel).stdout.strip()
    if not status:
        return False
    # Path-scoped commit: commits the working-tree version of ONLY this file.
    _git(FARM_2026_ROOT, "commit", "-m", commit_message, "--", rel)
    _push_with_rebase_retry(FARM_2026_ROOT, _current_branch(FARM_2026_ROOT))
    return True


def _verify_tracked(rel_path: str) -> bool:
    """True if rel_path is present in the pushed HEAD tree."""
    try:
        out = _git(FARM_2026_ROOT, "ls-tree", "-r", "HEAD", "--", rel_path).stdout.strip()
        return bool(out)
    except GitHelperError:
        return False


def _verify_roster_contains(needle: str) -> bool:
    """True if the committed HEAD version of the roster JSON contains needle."""
    try:
        blob = _git(FARM_2026_ROOT, "show", "HEAD:content/flock-profiles.json").stdout
        return needle in blob
    except GitHelperError:
        return False


# --- de-dup claim -----------------------------------------------------------
def _claim_subject(subject_slug: str) -> bool:
    """Atomically claim `subject_slug`. Returns True if we won the claim (safe to
    commit), False if another processor holds a FRESH claim (skip to avoid a
    duplicate). Stale claims (> _CLAIM_TTL_S) are taken over."""
    try:
        _CLAIM_DIR.mkdir(parents=True, exist_ok=True)
        claim = _CLAIM_DIR / f"{subject_slug or 'unknown'}.claim"
        now = datetime.now().timestamp()
        try:
            fd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, str(now).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                age = now - float((claim.read_text() or "0").strip())
            except Exception:  # noqa: BLE001 — unreadable/garbage claim = stale
                age = _CLAIM_TTL_S + 1
            if age > _CLAIM_TTL_S:
                claim.write_text(str(now))
                return True
            return False
    except Exception as exc:  # noqa: BLE001 — never let de-dup bookkeeping
        # block a real filing; degrade to "no claim system" rather than fail.
        log.warning("bird_photo_ingest: claim check failed (%s) — proceeding", exc)
        return True


def _release_subject(subject_slug: str) -> None:
    """Drop the claim so a retry can proceed (used after a FAILED filing; a
    successful filing keeps the claim until the TTL expires to block dups)."""
    try:
        (_CLAIM_DIR / f"{subject_slug or 'unknown'}.claim").unlink()
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("bird_photo_ingest: claim release failed: %s", exc)


# --- entry point ------------------------------------------------------------
def ingest(image_path: str, caption: str) -> dict:
    """Ingest one Discord-dropped bird photo + caption into the farm-2026
    roster. Callable from the OpenClaw hook or the CLI. Always returns a
    structured dict (never raises); `status` is one of:

      - "posted"          single named bird, solo shot -> portrait set
      - "multi_committed" N named birds, count matches -> file committed, no
                          single portrait set (a group shot isn't a portrait)
      - "ambiguous"       1 named but VLM sees a group/other count -> no writes
      - "no_match"        caption named no roster bird -> no writes
      - "vlm_unavailable" VLM down -> fail safe, no writes
      - "partial"         image committed but the roster JSON commit failed
      - "error"           bad input / unexpected failure
    """
    result: dict = {
        "status": "error",
        "birds": [],
        "filename": None,
        "raw_url": None,
        "roster_updated": False,
        "hatch_file_needs_update": False,
        "image_verified": False,
        "roster_verified": False,
        "message": "",
    }
    try:
        src = Path(image_path).expanduser()
        if not image_path or not src.exists():
            result["message"] = f"No readable image at {image_path!r}."
            return result
        ext = src.suffix.lower()
        if ext not in _ALLOWED_EXTENSIONS:
            result["status"] = "error"
            result["message"] = f"Unsupported image type {ext!r} (need jpg/jpeg/png)."
            return result

        # 1. Caption -> roster name(s). Do this FIRST: if the caption names no
        #    roster bird, stop here BEFORE the vision model runs. This is what
        #    makes the hook "only birds you name" — a gourd, a screenshot, or
        #    plain chatter matches nothing and costs zero VLM inference.
        matched = match_caption_names(caption)
        result["birds"] = matched
        if not matched:
            result["status"] = "no_match"
            result["message"] = (
                "I didn't recognize a roster bird's name in that caption, so I "
                "left the flock file untouched. Name the bird and I'll file it."
            )
            return result

        # 2. Vision (count + composition drive the gate; caption -> descriptor).
        meta = _run_vlm(src.read_bytes())
        if meta is None:
            result["status"] = "vlm_unavailable"
            result["message"] = (
                "Vision model is unavailable, so I can't safely tell a solo "
                "shot from a group — skipping this one. Try again shortly."
            )
            return result
        bird_count = int(meta.get("bird_count", 0))
        composition = meta.get("composition") or ""
        descriptor = _descriptor_from_vlm(meta)
        is_group_comp = composition in ("group", "wide")

        n = len(matched)

        # 4. Ambiguity gate — flag, never guess.
        if n == 1:
            # Solo path requires exactly one bird and a non-group composition.
            # Rejecting bird_count==0 is intentional (tighter than "> 1"): if
            # the VLM can't see a single clear bird we don't assert a portrait.
            if bird_count != 1 or is_group_comp:
                result["status"] = "ambiguous"
                result["message"] = (
                    f"You said {matched[0]}, but I see {bird_count} bird(s)"
                    + (f" / a {composition} shot" if is_group_comp else "")
                    + f" — which one is {matched[0]}? I didn't change the roster."
                )
                return result
        else:
            # N named: only commit the file (as a group record) if the VLM's
            # count agrees. A mismatch is flagged, not guessed.
            if bird_count != n:
                result["status"] = "ambiguous"
                result["message"] = (
                    f"You named {n} birds ({', '.join(matched)}) but I count "
                    f"{bird_count} in the photo — leaving the roster untouched."
                )
                return result

        # 5. Build the final filename and commit the image into birds/.
        orig = _parse_orig(image_path)
        name_slug = "-".join(_slug(m) for m in matched)
        filename = _build_filename(orig, name_slug, descriptor, ext)
        result["filename"] = filename

        subject = matched[0] if n == 1 else ", ".join(matched)

        # De-dup: claim this subject before ANY commit, so a near-simultaneous
        # filing of the same bird (the hook AND an interactive agent) can't
        # double-commit. First writer wins; the loser no-ops as "deduped".
        if not _claim_subject(name_slug):
            result["status"] = "deduped"
            result["message"] = (
                f"Another filing of {subject} is already in flight — skipped to "
                f"avoid a duplicate. (If that one failed, retry in a few minutes.)"
            )
            return result

        img_commit_msg = f"public/photos/birds: {subject} portrait (discord drop auto)"
        try:
            # Temp file carries the FINAL basename so git_helper commits it
            # under our intended name (it uses local_image.name as the dest).
            with tempfile.TemporaryDirectory() as td:
                staging = Path(td) / filename
                shutil.copy2(src, staging)
                _committed_path, raw_url = commit_image_to_farm_2026(
                    local_image=staging,
                    subdir="birds",
                    repo_path=FARM_2026_ROOT,
                    commit_message=img_commit_msg,
                )
        except (GitHelperError, FileNotFoundError, ValueError) as exc:
            _release_subject(name_slug)  # failed cleanly — let a retry through
            result["status"] = "error"
            result["message"] = f"Image commit failed, no roster change made: {exc}"
            return result

        result["raw_url"] = raw_url
        rel_image = f"public/photos/birds/{filename}"
        result["image_verified"] = _verify_tracked(rel_image)

        # 6. Multi-bird group shot: file is committed, but a group is not a
        #    single bird's portrait — do NOT set any one bird's photo.
        if n != 1:
            result["status"] = "multi_committed"
            result["message"] = (
                f"Filed the group shot of {subject} at {rel_image} "
                f"(no single portrait set — it's a group)."
            )
            return result

        # 7. Single named bird -> set its portrait in the roster JSON.
        bird_name = matched[0]
        record = _find_record(bird_name)
        photo_value = f"birds/{filename}"  # base is relative to public/photos/
        try:
            roster_updated = _set_photo_and_commit(
                bird_name,
                photo_value,
                commit_message=(
                    f"content/flock-profiles.json: set {bird_name} photo to "
                    f"{photo_value} + append to history (discord drop auto)"
                ),
                photo_date=datetime.now().strftime("%Y-%m-%d"),
                caption=((meta.get("caption_draft") or "").strip()[:450] or None),
            )
        except GitHelperError as exc:
            # Image already landed; the JSON commit didn't. Report honestly —
            # this is NOT a clean success.
            _release_subject(name_slug)  # roster not done — allow a retry
            result["status"] = "partial"
            result["message"] = (
                f"Image landed at {rel_image}, but the roster JSON commit "
                f"failed — {bird_name}'s portrait was NOT updated: {exc}"
            )
            return result

        result["roster_updated"] = roster_updated
        result["roster_verified"] = _verify_roster_contains(photo_value)

        if roster_updated and not result["roster_verified"]:
            # The write + path-scoped commit returned OK, but the value isn't
            # visible in the pushed HEAD. Roster/photo edits have been lost to
            # git races before (feedback_photo_management) — never claim a clean
            # win we can't see in HEAD. Downgrade to partial so the reply tells
            # Boss to eyeball /flock.
            _release_subject(name_slug)  # not confirmed — allow a retry
            result["status"] = "partial"
            result["message"] = (
                f"Image landed at {rel_image} and I wrote {bird_name}'s photo, "
                f"but I couldn't confirm it in the pushed roster — please check "
                f"/flock and re-drop if it didn't take."
            )
            return result

        # Optional, non-blocking coarse sanity note on the silver discriminator
        # (the only unhedged roster field cheap to cross-check). Boss's caption
        # is authoritative, so this never blocks the commit — it just surfaces
        # an obvious contradiction for a human to eyeball.
        sanity = _silver_sanity_note(record, meta)
        if sanity:
            result["sanity_note"] = sanity

        # 8. Ornitharch named individuals also have a per-chick hatch .md whose
        #    YAML we deliberately do NOT auto-edit (v1) — flag it for a human.
        result["hatch_file_needs_update"] = bool(record and record.get("ornitharch"))

        result["status"] = "posted"
        msg = (
            f"Set {bird_name}'s portrait to {rel_image}"
            + (" (roster already had it)" if not roster_updated else "")
            + "."
        )
        if result["hatch_file_needs_update"]:
            msg += f" Heads up: {bird_name} is an ornitharch — the hatch .md may want the new photo too."
        if sanity:
            msg += f" ({sanity})"
        result["message"] = msg
        return result

    except Exception as exc:  # noqa: BLE001 — entry point must never raise.
        log.exception("bird_photo_ingest: unexpected failure")
        result["status"] = "error"
        result["message"] = f"Unexpected failure: {exc}"
        return result


def _silver_sanity_note(record: Optional[dict], meta: dict) -> str:
    """Coarse, non-blocking cross-check on the one unhedged roster
    discriminator (`silver_marking`). Returns a short advisory string when the
    VLM caption clearly contradicts a definite silver_marking value, else "".
    Only fires on the obvious case — it never blocks or overrides the caption ID.
    """
    if not record:
        return ""
    marking = (record.get("silver_marking") or "").strip().lower()
    if marking not in ("confirmed", "none"):
        return ""
    caption = (meta.get("caption_draft") or "").lower()
    says_silver = bool(re.search(r"\bsilver\b|\bblue-?gray\b|\bblue-?grey\b", caption))
    if marking == "none" and says_silver:
        return (
            f"note: {record.get('name')} is logged as NOT silver, but the "
            f"photo reads silver/blue-gray — worth a human glance."
        )
    if marking == "confirmed" and not says_silver and re.search(r"\b(dark|brown|black)\b", caption):
        return (
            f"note: {record.get('name')} is logged as the silver one, but the "
            f"photo reads dark/brown — worth a human glance."
        )
    return ""


def _reply(reply_channel: Optional[str], text: str) -> None:
    """Best-effort Discord reply via the openclaw CLI. Never raises — by the
    time we reply the commit has already happened, so a messaging failure must
    not turn a successful ingest into a failure."""
    if not reply_channel or not text:
        return
    import subprocess
    try:
        subprocess.run(
            [
                "openclaw", "message", "send",
                "--channel", "discord",
                "--target", f"channel:{reply_channel}",
                "--message", text,
            ],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("bird_photo_ingest: Discord reply failed: %s", exc)


def ingest_and_reply(
    image_path: str, caption: str, reply_channel: Optional[str] = None
) -> dict:
    """ingest() + relay the human-readable outcome back to Discord.

    The OpenClaw hook is fire-and-forget, so the MODULE (not the hook) owns the
    detailed reply: the ambiguity prompt ("which one is Henridotta?"), the
    success line with the raw URL, or the honest partial/failure note. Every
    non-happy status ends in a message here rather than in silence. With no
    reply_channel (CLI / tests) it stays silent so local runs never post.
    """
    result = ingest(image_path, caption)
    text = result.get("message", "")
    if result.get("raw_url") and result.get("status") in ("posted", "multi_committed"):
        text = f"{text}\n{result['raw_url']}"
    _reply(reply_channel, text)
    return result


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": (
                        "usage: bird_photo_ingest.py <image_path> <caption> "
                        "[reply_channel_id]"
                    ),
                }
            )
        )
        sys.exit(1)
    _image_path = sys.argv[1]
    _caption = sys.argv[2]
    # Optional 3rd arg = Discord channel id; when present the outcome is relayed
    # back to that channel. Omit it (local tests) to stay silent.
    _reply_channel = sys.argv[3] if len(sys.argv) > 3 else None
    print(
        json.dumps(
            ingest_and_reply(_image_path, _caption, _reply_channel),
            indent=2,
            ensure_ascii=False,
        )
    )
