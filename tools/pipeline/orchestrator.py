# Author: Claude Opus 4.7 (1M context); Claude Sonnet 4.6 (edits 27-April-2026 — vlm_bypass mode: run_raw_cycle, dedicated raw threads, raw retention sweep, v2.37.13; 28-April-2026 — sharpness gate wired in, v2.37.14; 04-May-2026 — Birds preset as prompt/schema source, v2.40.0); GPT-5.5 Codex (edits 08-May-2026 — static floor-pecking score calibration); Claude Opus 4.8 (1M context) (edits 03-June-2026 — VLM input downscale via _downscale_for_vlm + vlm_input_long_edge_px config, to cut per-frame latency, v2.40.17); Claude Opus 4.8 (Bubba sub-agent) (edits 14-June-2026 — golden-window raw capture: per-iteration thick/sparse cadence for usb-cam/dominator-cam via offpeak_cycle_seconds + timelapse_golden_windows); Claude Sonnet 4.6 (edits 27-June-2026 — run_raw_cycle quality gates + laplacian storage, v2.44.1); Claude Fable 5 (edits 02-July-2026 — Discord caption trim via gem_poster.trim_caption, v2.44.5); Claude Opus 4.8 (Bubba) (edits 12-July-2026 — _compute_overall_score 0-100 weighted-component scoring, floor-pecking cap + caption rescaled, v2.45.0; 13-July-2026 — dominance recalibrated (full at ~50% coverage) so real gems clear the 80 gate + BIRD SELFIE ping 95->90, v2.45.1); Claude Fable 5 (edits 16-July-2026 — IG-hook hashtag rotation fed from posted-caption ledger, v2.47.0); Claude Sonnet 5 Extra (edits 03-Aug-2026 — keyframe-promotion hook in run_raw_cycle for the permanent weekly/monthly time-lapse archive, v2.60.0); Claude Opus 5 (edits 09-Aug-2026 — keyframe capture switched from 3 fixed daily slots to a daylight-gated interval via _keyframe_interval_due, v2.69.0)
# Date: 17-April-2026 (last touched 03-Aug-2026)
# PURPOSE: Main entry point for the multi-cam image pipeline. Schedules per-
#          camera capture cycles at their configured cadences, runs each
#          frame through a four-stage pre-VLM filter (trivial std-dev gate,
#          exposure gate, per-camera motion gate), enriches passing frames
#          via the VLM, persists to SQLite + disk. Single in-flight VLM call
#          (enforced in vlm_enricher via a module-level lock). Per-cycle LM
#          Studio coordination is read-only: if the wrong model is loaded (or
#          nothing is loaded), the cycle is logged and skipped — we do not
#          auto-load mid-loop, to avoid contention with G0DM0D3 sweeps, per
#          docs/13-Apr-2026-lm-studio-reference.md. The ONE exception is a
#          single controlled ensure_model_loaded() at daemon startup (checks
#          first, never stacks) so a reboot can't leave the model JIT-loaded
#          at a too-small context.
#
#          Motion gate is opt-in per camera via `motion_gate: true` in the
#          camera's config block. Outdoor/coop cameras (house-yard, gwtc)
#          enable it because 90%+ of their frames are unchanged yard/coop
#          and returned `skip` from the VLM. Brooder cameras leave it off
#          because chicks move continuously and we want the VLM on every
#          frame.
#
#          03-Aug-2026: run_raw_cycle() now also promotes a frame into the
#          PERMANENT keyframe tier (store.store_keyframe) when the cycle
#          lands near one of a camera's configured keyframe_capture
#          local_times. This is the capture side of the house-yard/duo2
#          weekly+monthly time-lapse Reels — see
#          docs/03-Aug-2026-multi-day-timelapse-reels-plan.md. It reuses
#          the frame this cycle already captured (house-yard and duo2 are
#          both captured continuously regardless), so it adds zero new
#          camera traffic — it's a promotion, not a new capture path.
#          Opt-in per camera via keyframe_capture.cameras in config.json;
#          cameras not listed are entirely unaffected.
#
#          Modes:
#            --once                : run every enabled camera once, exit
#            --once --camera NAME  : run one camera once, exit
#            --daemon              : run forever on per-camera cadences
#            --retention-only      : run the retention sweep and exit
# SRP/DRY check: Pass — single responsibility is scheduling + gluing the
#                other pipeline modules together. The keyframe-promotion
#                hook reuses store.store_keyframe (Task 1) rather than
#                inventing a second insert path, and reuses the frame
#                run_raw_cycle already captured rather than opening a
#                second connection to the camera.

from __future__ import annotations

import argparse
import json
import logging
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2
import numpy as np

# Support both `python -m tools.pipeline.orchestrator` and
# `python tools/pipeline/orchestrator.py` invocations.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.pipeline.capture import capture_camera, capture_ip_webcam_burst, CaptureError
    from tools.pipeline.quality_gate import passes_trivial_gate, passes_exposure_gate, passes_sharpness_gate, MotionGate
    from tools.pipeline.presence import shared_detector
    from tools.pipeline.frame_selector import Candidate, select_best, subject_laplacian
    from tools.pipeline.vlm_enricher import enrich, ensure_model_loaded, ModelNotLoaded, EnricherError, ValidationFailed
    from tools.pipeline.store import ensure_schema, store, store_raw, store_keyframe
    from tools.pipeline.retention import sweep as retention_sweep, sweep_raw as retention_sweep_raw
    from tools.pipeline.golden_windows import camera_uses_golden_windows, camera_golden_cfg, is_dt_in_golden_windows, is_daylight
    from tools.pipeline.gem_poster import post_gem, should_post, load_dotenv, trim_caption
    from tools.pipeline.ig_poster import (
        build_caption,
        pick_hashtags,
        post_gem_to_ig,
        post_gem_to_story,
        query_last_ig_post_ts,
        query_last_story_ts,
        should_post_ig,
        should_post_story,
        _load_hashtag_library,
        _write_permalink,
        _write_story_metadata,
        IGPosterError,
    )
else:
    from .capture import capture_camera, capture_ip_webcam_burst, CaptureError
    from .quality_gate import passes_trivial_gate, passes_exposure_gate, passes_sharpness_gate, MotionGate
    from .presence import shared_detector
    from .frame_selector import Candidate, select_best, subject_laplacian
    from .vlm_enricher import enrich, ensure_model_loaded, ModelNotLoaded, EnricherError, ValidationFailed
    from .store import ensure_schema, store, store_raw, store_keyframe
    from .retention import sweep as retention_sweep, sweep_raw as retention_sweep_raw
    from .golden_windows import camera_uses_golden_windows, camera_golden_cfg, is_dt_in_golden_windows, is_daylight
    from .gem_poster import post_gem, should_post, load_dotenv, trim_caption
    from .ig_poster import (
        build_caption,
        pick_hashtags,
        post_gem_to_ig,
        post_gem_to_story,
        query_last_ig_post_ts,
        query_last_story_ts,
        should_post_ig,
        should_post_story,
        _load_hashtag_library,
        _write_permalink,
        _write_story_metadata,
        IGPosterError,
    )


log = logging.getLogger("pipeline.orchestrator")

_STOP = threading.Event()
_PAUSE_FLAG = Path("/tmp/farm-pipeline.pause")

# Module-level motion gate — holds one 64x64 thumbnail per camera that
# opts in via `motion_gate: true` in its config block. Lives at module
# scope so it survives across cycles for the daemon. The --once modes
# build their own per-invocation instance inside run_once (no point
# keeping baselines between one-shot invocations).
_MOTION_GATE: MotionGate | None = None


def _decode_jpeg(jpeg_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("jpeg decode failed")
    return img


def _downscale_for_vlm(jpeg_bytes: bytes, long_edge_px: int) -> bytes:
    """Shrink a JPEG so its longest side is at most long_edge_px, for the
    VLM call ONLY. The full-resolution frame is still what gets archived and
    posted — the model only needs enough detail to judge composition and
    whether a bird is cleanly in frame, so feeding it a smaller image cuts
    vision-token count and encode time sharply (the single biggest lever on
    per-frame VLM latency). long_edge_px <= 0 disables the resize. Any decode
    or encode failure falls back to the original bytes so a bad frame never
    blocks a cycle.
    """
    if long_edge_px <= 0:
        return jpeg_bytes
    try:
        img = _decode_jpeg(jpeg_bytes)
    except ValueError:
        return jpeg_bytes
    height, width = img.shape[:2]
    longest = max(height, width)
    if longest <= long_edge_px:
        return jpeg_bytes
    scale = long_edge_px / longest
    resized = cv2.resize(
        img,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    ok, encoded = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return encoded.tobytes() if ok else jpeg_bytes


def _compute_overall_score(metadata: dict) -> None:
    """Compute the 0-100 farm-gem score. Mutates metadata in place.

    THREE axes since v2.68.0 (08-Aug-2026, per Boss — "the requirement that the
    bird fill a certain amount of the frame, I think that's just adding extra
    noise, that's something we want to get rid of"):

      - expression        (0-30) — how absurd/expressive the bird is (VLM)
      - notable detail    (0-25) — claws/wings/feet/features in focus (VLM)
      - technical quality (0-15) — focus + light, derived from image_quality
                                   + lighting so it can't contradict them

    Only the first two are asked of the VLM — small concrete ranges a 4b model
    can actually rate. Code owns the weighting, and the VLM's own
    `overall_score` guess is discarded: a small VLM cannot calibrate a single
    0-100 number, but summing small axes lands on far more distinct totals.

    WHY DOMINANCE WENT, and the honest caveat. It was 0-30 derived from
    `largest_subject_pct`. Two things are true at once and worth recording:

      * Boss's own Discord reactions say frame-fill IS his single best-measured
        preference — reacted frames have a median largest-box of 31.4% of frame
        against 19.5% for strong-but-unreacted (measured 08-Aug-2026 by running
        YOLO over the archive). So the signal is real.
      * But it was being spent as a GATE, and that is the wrong job for it. At
        30 of 100 points it dragged whole clusters of good frames under the
        posting floor — the 07-Aug pile-up of frames scoring exactly 68 against
        a 70 floor was dominance 18 doing precisely this. Suppressing volume to
        express a preference is redundant when a human is already curating
        every frame downstream in Discord.

    So dominance moved rather than died: it is now a *selection* weight in
    frame_selector, where it picks the best of an already-captured burst and
    costs no volume at all, instead of a *gate* here. Boss's taste still steers
    which frame gets sent; it no longer decides whether one gets sent.

    RESCALING — the number that matters. Dropping dominance leaves a 0-70 raw
    range, so it is rescaled back to ~0-100. The factor is derived from the
    OBSERVED ceiling, not the theoretical one: across 495 strong-tier s7 frames
    the raw sum's max was 65 and its p95 62, because the 4b model never emits
    the top of its own ranges (it returns expression 15 / detail 20 over and
    over). Scaling by the theoretical 70 would have quietly preserved the
    status quo — the exact trap v2.45.1 fell into by calibrating dominance
    against synthetic scores instead of live output. Re-derive these two
    constants from real data if the VLM, the prompt, or the camera aim changes.
    """
    def _clamp(v, lo, hi):
        return max(lo, min(hi, v)) if isinstance(v, int) and not isinstance(v, bool) else lo

    expression = _clamp(metadata.get("expression_score"), 0, 30)
    detail = _clamp(metadata.get("detail_score"), 0, 25)

    technical = {"sharp": 15, "soft": 8, "blurred": 0}.get(metadata.get("image_quality"), 0)
    if metadata.get("lighting") in {"blown-out", "dim", "backlit"}:
        technical = max(0, technical - 4)

    raw = expression + detail + technical
    metadata["overall_score"] = min(100, round(raw * _SCORE_SCALE_TO / _SCORE_RAW_CEILING))
    log.debug(
        "score: expression=%d detail=%d technical=%d raw=%d -> overall=%d",
        expression, detail, technical, raw, metadata["overall_score"],
    )


def _calibrate_static_floor_pecking_score(camera_name: str, metadata: dict) -> bool:
    """Demote routine brooder/coop floor pecking below the gem bar.

    The VLM sometimes sees sharp chickens and assigns a 6 even when the
    actual photo is a flat floor snapshot: generic eating/foraging, partial
    birds, no clean subject, and no story. This deterministic guard keeps
    those cases aligned with the prompt rubric before storage/posting.
    Returns True when metadata was changed.
    """
    if camera_name not in {"usb-webcam-1080p", "gwtc"}:
        return False
    if metadata.get("scene") not in {"brooder", "coop"}:
        return False
    caption_text = " ".join(
        str(metadata.get(field, ""))
        for field in ("caption_draft", "share_reason")
    ).lower()
    routine_floor_terms = (
        "peck",
        "forag",
        "feed",
        "feeder",
        "water bowl",
        "waterer",
        "ground",
        "floor",
    )
    routine_floor_scene = (
        metadata.get("activity") in {"eating", "drinking", "foraging"}
        or any(term in caption_text for term in routine_floor_terms)
    )
    if not routine_floor_scene:
        return False

    composition = metadata.get("composition")
    face_visible = bool(metadata.get("bird_face_visible"))
    image_quality = metadata.get("image_quality")
    largest = metadata.get("largest_subject_pct")
    coverage = metadata.get("subject_coverage_pct")
    bird_count = metadata.get("bird_count")
    if not isinstance(bird_count, int):
        bird_count = 0

    scattered_equipment_terms = (
        "background",
        "nearby",
        "shadow",
        "fence line",
        "under the fence",
        "water bowl",
        "waterer",
        "feeder",
    )
    scattered_multi_bird_floor = (
        bird_count >= 3
        and any(term in caption_text for term in scattered_equipment_terms)
        and any(
            term in caption_text
            for term in (
                "peck",
                "forag",
                "ground",
                "floor",
                "feed",
                "water bowl",
                "waterer",
            )
        )
    )

    clean_portrait = (
        composition == "portrait"
        and face_visible
        and image_quality == "sharp"
        and isinstance(largest, int)
        and largest >= 35
    )
    if clean_portrait and not scattered_multi_bird_floor:
        return False

    floor_snapshot = (
        composition in {"group", "wide", "cluttered"}
        or not face_visible
        or scattered_multi_bird_floor
    )
    small_or_sparse = (
        (isinstance(largest, int) and largest < 35)
        or (isinstance(coverage, int) and coverage < 45)
    )
    if not (floor_snapshot or small_or_sparse):
        return False

    # v2.45.0: caps rescaled from the old 0-10 scale to 0-100 (3->30, 4->40).
    # This runs AFTER _compute_overall_score, so overall_score is the computed
    # 0-100 total; capping it keeps floor-pecking frames well under the 80 gate.
    old_score = metadata.get("overall_score")
    score_cap = 30
    if image_quality == "sharp" and face_visible and not scattered_multi_bird_floor:
        score_cap = 40
    if isinstance(old_score, int):
        metadata["overall_score"] = min(old_score, score_cap)
    else:
        metadata["overall_score"] = score_cap

    if metadata["overall_score"] <= 40 and metadata.get("share_worth") != "skip":
        metadata["share_worth"] = "skip"

    metadata["share_reason"] = (
        "Static brooder/coop-floor pecking scene lacks a clean subject, "
        "standout behavior, or share-worthy story."
    )
    return True


def run_raw_cycle(camera_name: str, camera_cfg: dict, cfg: dict,
                  db_path: Path, archive_root: Path) -> dict:
    """Capture → gate → save-to-disk cycle for cameras marked vlm_bypass=true.

    Applies the same trivial + exposure gate chain used by run_cycle so
    blank, washed-out, or corrupted frames are rejected before hitting disk.
    gate_metrics (std_dev, laplacian_var, exposure_p50) are stored in the DB
    row so select_timelapse_gems can rank frames by sharpness rather than
    picking randomly from each time bucket.

    No motion gate, no VLM inference, no Discord/IG posting.
    """
    result = {"camera": camera_name,
              "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "path": "raw"}
    retry_max = cfg.get("capture_retry_max", 3)
    jpeg_bytes = None
    for attempt in range(1, retry_max + 1):
        try:
            jpeg_bytes = capture_camera(camera_name, camera_cfg, cfg)
            break
        except CaptureError as e:
            log.warning("%s: raw capture attempt %d/%d failed: %s",
                        camera_name, attempt, retry_max, e)
            if attempt == retry_max:
                result.update(status="error", stage="capture", reason=str(e))
                return result
            time.sleep(1.0)
        except Exception as e:
            log.exception("%s: raw capture attempt %d/%d exception",
                          camera_name, attempt, retry_max)
            if attempt == retry_max:
                result.update(status="error", stage="capture",
                              reason=f"{type(e).__name__}: {e}")
                return result
            time.sleep(1.0)

    # Compute quality metrics and apply cheap gates. Mirrors run_cycle's
    # trivial + exposure chain. Catches: blank frames (std < std_dev_floor),
    # corrupted/washed-out frames (std < exposure_std_floor or p50 out of
    # range), and per-camera blurry frames (laplacian_floor > 0 in config).
    gate_metrics: dict = {}
    try:
        img = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            ok, gate_metrics = passes_trivial_gate(
                img, std_dev_floor=cfg.get("std_dev_floor", 5.0)
            )
            if not ok:
                result.update(status="gated", stage="trivial_gate", metrics=gate_metrics)
                return result
            exp_ok, exp_reason = passes_exposure_gate(
                gate_metrics,
                p50_floor=cfg.get("exposure_p50_floor", 25.0),
                p50_ceiling=cfg.get("exposure_p50_ceiling", 230.0),
                std_floor=cfg.get("exposure_std_floor", 15.0),
            )
            if not exp_ok:
                log.info("%s: raw frame rejected (%s) metrics=%s",
                         camera_name, exp_reason, gate_metrics)
                result.update(status="gated", stage="exposure",
                              reason=exp_reason, metrics=gate_metrics)
                return result
            lap_floor = float(camera_cfg.get("laplacian_floor", 0.0))
            if lap_floor > 0.0:
                sharp_ok, sharp_reason = passes_sharpness_gate(
                    gate_metrics, laplacian_floor=lap_floor
                )
                if not sharp_ok:
                    log.info("%s: raw frame rejected (%s) metrics=%s",
                             camera_name, sharp_reason, gate_metrics)
                    result.update(status="gated", stage="sharpness",
                                  reason=sharp_reason, metrics=gate_metrics)
                    return result
    except Exception as exc:
        # Metric computation is best-effort; a decode failure here should not
        # stop the frame from being stored (the file is still valid on disk).
        log.warning("%s: raw metric computation failed: %s — storing without metrics",
                    camera_name, exc)
        gate_metrics = {}

    # Optional per-camera archive downscale (raw_max_long_edge_px). High-res
    # cameras like duo2 (4608x1728, ~3.8MB/frame) fill the disk at ~33GB/day
    # on a 10s cadence; shrinking the archived copy keeps the rolling raw
    # window affordable. Gems/reels rendered from raw inherit the smaller
    # size, which is still above the 1080p publish target. 0/absent = full res.
    raw_edge = int(camera_cfg.get("raw_max_long_edge_px", 0) or 0)
    if raw_edge > 0:
        jpeg_bytes = _downscale_for_vlm(jpeg_bytes, raw_edge)

    try:
        sr = store_raw(db_path=db_path, archive_root=archive_root,
                       camera_id=camera_name, jpeg_bytes=jpeg_bytes,
                       gate_metrics=gate_metrics)
    except Exception as e:
        log.exception("%s: raw store failed", camera_name)
        result.update(status="error", stage="store",
                      reason=f"{type(e).__name__}: {e}")
        return result
    result.update(status="ok", tier=sr["tier"], image_path=sr["image_path"],
                  stored_bytes=sr["stored_bytes"],
                  width=sr["width"], height=sr["height"])

    _promote_keyframe_if_due(camera_name, cfg, db_path, archive_root,
                             jpeg_bytes, gate_metrics)
    return result


def _keyframe_interval_due(
    camera_name: str, cfg: dict, db_path: Path, now_utc: datetime,
) -> bool:
    """Interval mode (v2.69.0): true when this camera is in daylight AND its
    newest keyframe is older than keyframe_capture.interval_minutes.

    Replaces the fixed local_times slot list for the weekly/monthly time-lapse
    cameras. Three slots a day produced 17 frames for a whole week, so the
    reels held each shot ~1.8s and cut between captures five real hours apart
    — Boss's "choppy, lingers, then jumps" complaint on 09-Aug-2026. A reel
    that flows needs consecutive frames minutes apart, which means capture has
    to be interval-driven rather than slot-driven.

    Daylight-gated by golden_windows.is_daylight (plain sunrise->sunset), the
    same predicate select_multiday_timelapse_gems filters on — capturing dark
    frames the selector will discard would just burn permanent disk.

    Idempotency is the "newest keyframe age" query itself, so this is safe
    across daemon restarts with no state file, exactly like the slot path.
    """
    kf_cfg = cfg.get("keyframe_capture") or {}
    interval_min = float(kf_cfg.get("interval_minutes") or 0)
    if interval_min <= 0:
        return False

    mt_cfg = kf_cfg.get("daylight") or {}
    tz_name = kf_cfg.get("timezone", "America/New_York")
    if not is_daylight(
        now_utc,
        float(mt_cfg.get("latitude", 41.7558)),
        float(mt_cfg.get("longitude", -71.9789)),
        tz_name,
    ):
        return False

    cutoff = (now_utc - timedelta(minutes=interval_min)).isoformat()
    with sqlite3.connect(str(db_path), timeout=30) as c:
        recent = c.execute(
            """SELECT 1 FROM image_archive
                WHERE camera_id = ? AND image_tier = 'keyframe' AND ts >= ?
                LIMIT 1""",
            (camera_name, cutoff),
        ).fetchone()
    return recent is None


def _due_keyframe_slot(
    camera_name: str, cfg: dict, now_utc: datetime,
) -> tuple[str, datetime] | None:
    """Return (slot_label, slot_dt_utc) if `now_utc` falls within
    tolerance of one of this camera's configured keyframe_capture
    local_times, else None.

    Opt-in per camera via keyframe_capture.cameras — a camera absent from
    that list (i.e. every camera except house-yard/duo2 today) always
    returns None here, so this function is a no-op for the other five
    vlm_bypass cameras without needing its own enabled flag.

    Legacy slot mode. When keyframe_capture.interval_minutes is set (the
    default since v2.69.0) _promote_keyframe_if_due uses the interval path
    above instead and never calls this. Kept so a camera can still be pinned
    to specific times of day by clearing interval_minutes.
    """
    kf_cfg = cfg.get("keyframe_capture") or {}
    if camera_name not in (kf_cfg.get("cameras") or []):
        return None
    tz_name = kf_cfg.get("timezone", "America/New_York")
    tolerance_min = float(kf_cfg.get("tolerance_minutes", 5))
    local_times = kf_cfg.get("local_times") or ["07:00", "12:00", "16:00"]

    local_now = now_utc.astimezone(ZoneInfo(tz_name))
    for slot in local_times:
        try:
            hh, mm = (int(p) for p in str(slot).split(":", 1))
        except (ValueError, AttributeError):
            log.warning("keyframe_capture: unparseable local_time %r; skipping", slot)
            continue
        slot_local = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if abs((local_now - slot_local).total_seconds()) <= tolerance_min * 60:
            return str(slot), slot_local.astimezone(timezone.utc)
    return None


def _promote_keyframe_if_due(
    camera_name: str, cfg: dict, db_path: Path, archive_root: Path,
    jpeg_bytes: bytes, gate_metrics: dict,
) -> None:
    """Best-effort: if now is within tolerance of one of this camera's
    configured keyframe slots AND no keyframe row exists yet for that
    slot today, promote the frame this cycle already captured into the
    permanent keyframe tier (store.store_keyframe). Never raises — a
    failure here must never affect the raw-tier result the caller
    (run_raw_cycle) already committed to returning.

    Idempotency is a DB query (a keyframe row already inside this slot's
    tolerance window), not a state file — consistent with the rest of
    this pipeline, and correct across a daemon restart with no extra
    bookkeeping to reload.

    See docs/03-Aug-2026-multi-day-timelapse-reels-plan.md.
    """
    try:
        now_utc = datetime.now(timezone.utc)
        kf_cfg = cfg.get("keyframe_capture") or {}

        # Interval mode (default since v2.69.0) short-circuits the slot path:
        # capture every interval_minutes of daylight instead of at 3 fixed
        # times, so the weekly/monthly reels have hundreds of closely-spaced
        # frames to play instead of 17 hours-apart stills.
        if float(kf_cfg.get("interval_minutes") or 0) > 0:
            if camera_name not in (kf_cfg.get("cameras") or []):
                return
            if not _keyframe_interval_due(camera_name, cfg, db_path, now_utc):
                return
            store_keyframe(db_path=db_path, archive_root=archive_root,
                           camera_id=camera_name, jpeg_bytes=jpeg_bytes,
                           gate_metrics=gate_metrics)
            log.info("%s: promoted raw frame to permanent keyframe (interval)",
                     camera_name)
            return

        due = _due_keyframe_slot(camera_name, cfg, now_utc)
        if due is None:
            return
        slot_label, slot_utc = due
        tolerance_min = float(kf_cfg.get("tolerance_minutes", 5))
        window_start = (slot_utc - timedelta(minutes=tolerance_min)).isoformat(timespec="seconds")
        window_end = (slot_utc + timedelta(minutes=tolerance_min)).isoformat(timespec="seconds")

        with sqlite3.connect(str(db_path), timeout=30) as c:
            existing = c.execute(
                """SELECT 1 FROM image_archive
                   WHERE camera_id = ? AND image_tier = 'keyframe'
                     AND ts BETWEEN ? AND ? LIMIT 1""",
                (camera_name, window_start, window_end),
            ).fetchone()
        if existing:
            return

        store_keyframe(db_path=db_path, archive_root=archive_root,
                       camera_id=camera_name, jpeg_bytes=jpeg_bytes,
                       gate_metrics=gate_metrics)
        log.info("%s: promoted raw frame to permanent keyframe (slot=%s)",
                 camera_name, slot_label)
    except Exception:
        log.exception(
            "%s: keyframe promotion failed — raw store already succeeded "
            "this cycle, continuing", camera_name,
        )


# Per-camera gem-hunting state, keyed by camera name. Only cameras with a
# `hunt` config block ever appear here. Lives at module scope so it survives
# across daemon cycles (the --once paths simply never populate it).
#   hot_until      — monotonic deadline; while in the future, cadence is HOT
#   miss_streak    — consecutive cycles the presence gate found nothing
_HUNT_STATE: dict[str, dict] = {}

# Gem-score rescaling after dominance was dropped (v2.68.0). Both derived from
# LIVE output, not from the schema's theoretical maxima — see
# _compute_overall_score. Measured 08-Aug-2026 over 495 strong-tier s7 frames:
# raw (expression + detail + technical) had max 65, p95 62, median 50.
_SCORE_RAW_CEILING = 65   # observed max of the raw 3-axis sum
_SCORE_SCALE_TO = 95      # what that ceiling should map to on the 0-100 scale


def _hunt_capture(camera_name: str, camera_cfg: dict, cfg: dict,
                  hunt_cfg: dict) -> dict:
    """Burst-capture → per-frame gates → YOLO presence → pick the best frame.

    This is the cheap loop. It replaces run_cycle's single-capture + gate chain
    for cameras that opt in via a `hunt` config block, and its whole purpose is
    to decide whether the expensive loop (one ~5.2 s VLM call) is worth running
    at all, and if so on which frame.

    Returns either:
      {"status": "gated"/"error", ...}                  — caller returns as-is
      {"jpeg_bytes","img","gate_metrics","presence",...} — caller proceeds to VLM

    Ordering is cheapest-first, same principle as the original gate chain:
    decode → trivial → exposure → sharpness (all free, reusing one metrics
    dict) → YOLO (~16 ms) → VLM (~5.2 s).
    """
    burst_size = int(hunt_cfg.get("burst_size", 3))
    presence_cfg = hunt_cfg.get("presence") or {}
    presence_enabled = bool(presence_cfg.get("enabled", True))
    # Shadow mode logs what the gate WOULD have skipped without skipping it.
    # Flip to false to actually save the VLM call.
    shadow = bool(presence_cfg.get("shadow_mode", False))

    try:
        frames = capture_ip_webcam_burst(
            base_url=camera_cfg["ip_webcam_base"],
            burst_size=burst_size,
            shot_path=hunt_cfg.get("shot_path", "/shot.jpg"),
            trigger_focus=camera_cfg.get("trigger_focus", True),
            focus_wait=float(camera_cfg.get("focus_wait", 1.5)),
            inter_frame_delay=float(hunt_cfg.get("inter_frame_delay", 0.0)),
            force_portrait=camera_cfg.get("force_portrait", False),
        )
    except CaptureError as e:
        return {"status": "error", "stage": "capture", "reason": str(e)}
    except Exception as e:
        log.exception("%s: hunt burst raised", camera_name)
        return {"status": "error", "stage": "capture",
                "reason": f"{type(e).__name__}: {e}"}

    detector = shared_detector(presence_cfg) if presence_enabled else None
    candidates: list[Candidate] = []
    rejected: list[str] = []

    for jpeg_bytes in frames:
        try:
            img = _decode_jpeg(jpeg_bytes)
        except Exception:
            rejected.append("decode")
            continue

        ok, metrics = passes_trivial_gate(
            img, std_dev_floor=cfg.get("std_dev_floor", 5.0)
        )
        if not ok:
            rejected.append("trivial")
            continue
        exp_ok, exp_reason = passes_exposure_gate(
            metrics,
            p50_floor=cfg.get("exposure_p50_floor", 25.0),
            p50_ceiling=cfg.get("exposure_p50_ceiling", 230.0),
            std_floor=cfg.get("exposure_std_floor", 15.0),
        )
        if not exp_ok:
            rejected.append(exp_reason or "exposure")
            continue
        sharp_ok, sharp_reason = passes_sharpness_gate(
            metrics, laplacian_floor=float(camera_cfg.get("laplacian_floor", 0.0))
        )
        if not sharp_ok:
            rejected.append(sharp_reason or "sharpness")
            continue

        if detector is not None:
            presence = detector.detect(
                img, exposure_p50=metrics.get("exposure_p50"),
                daylight_only=bool(presence_cfg.get("daylight_only", True)),
            )
        else:
            from .presence import PresenceResult  # local: gate disabled entirely
            presence = PresenceResult(present=True, abstained=True,
                                      reason="presence_disabled")

        candidates.append(Candidate(
            jpeg_bytes=jpeg_bytes,
            laplacian_var=float(metrics.get("laplacian_var", 0.0)),
            presence=presence,
            gate_metrics=metrics,
            image_bgr=img,
            # Sharpness measured on the bird rather than the grass — the
            # whole-frame number ranks soft frames higher on this camera.
            subject_laplacian=subject_laplacian(img, presence),
        ))

    if not candidates:
        return {"status": "gated", "stage": "burst_gates",
                "reason": f"all {len(frames)} burst frames rejected",
                "rejections": rejected[:8]}

    # Presence verdict across the WHOLE burst, not per frame: a bird visible in
    # any frame of the burst means the scene is worth a VLM call. Abstentions
    # (too dark, model unavailable) count as present by construction, so the
    # gate can only ever save work — never lose a frame to its own failure.
    any_present = any(c.presence.present for c in candidates)
    abstained = any(c.presence.abstained for c in candidates)

    selection = select_best(candidates)
    winner = selection.candidate

    if presence_enabled and not any_present and not abstained:
        if shadow:
            log.info("%s: presence gate WOULD skip (shadow mode) — %d frames, no animal",
                     camera_name, len(candidates))
        else:
            return {"status": "gated", "stage": "presence",
                    "reason": "no_animal_in_burst",
                    "burst_size": len(frames), "considered": len(candidates),
                    "metrics": winner.gate_metrics}

    return {
        "jpeg_bytes": winner.jpeg_bytes,
        "img": winner.image_bgr,
        "gate_metrics": winner.gate_metrics,
        "presence": winner.presence,
        "hunt": {
            "burst_size": len(frames),
            "considered": selection.considered,
            "picked": selection.index,
            "score": round(selection.score, 3),
            "present": any_present,
            "abstained": abstained,
            **selection.breakdown,
        },
    }


def run_cycle(camera_name: str, camera_cfg: dict, cfg: dict, schema: dict,
              prompt_template: str, db_path: Path, archive_root: Path,
              motion_gate: MotionGate | None = None) -> dict:
    """One capture → gate → enrich → store cycle for one camera.
    Returns a summary dict. Never raises — failures are returned as
    {status: 'error', reason: '...'}.

    Gate order: trivial std-dev → exposure → motion (if opted in) → VLM.
    Any gate failure short-circuits with status='gated' — no VLM call,
    no archive row. The cheapest checks run first so rejections stay
    cheap."""
    result = {"camera": camera_name, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    retry_max = cfg.get("capture_retry_max", 3)

    # Capture with retry on trivial-gate failure. Only the trivial gate
    # triggers a recapture — exposure/motion rejections end the cycle
    # cleanly (the frame itself is fine, we just don't want to analyse it).
    last_gate_metrics = None
    jpeg_bytes = None
    img = None

    # Gem-hunting path (opt-in per camera via a `hunt` config block): a burst
    # of frames, YOLO presence gate, and best-frame selection replace the
    # single capture below. When it returns a frame, that frame has ALREADY
    # cleared the trivial/exposure/sharpness chain inside _hunt_capture, so
    # the re-checks further down are idempotent no-ops on the same metrics
    # dict. When it returns a status, the cycle is over — most importantly
    # stage='presence', which is the ~24% of cycles that no longer burn a
    # 5.2 s VLM call on an empty enclosure.
    hunt_cfg = camera_cfg.get("hunt") or {}
    hunt_enabled = bool(hunt_cfg.get("enabled", False))
    if hunt_enabled:
        hunt_out = _hunt_capture(camera_name, camera_cfg, cfg, hunt_cfg)
        if hunt_out.get("status"):
            result.update(hunt_out)
            return result
        jpeg_bytes = hunt_out["jpeg_bytes"]
        img = hunt_out["img"]
        last_gate_metrics = hunt_out["gate_metrics"]
        result["hunt"] = hunt_out["hunt"]

    # Legacy single-capture path — skipped entirely in hunt mode, which has
    # already chosen its winner above.
    capture_attempts = 0 if hunt_enabled else retry_max
    for attempt in range(1, capture_attempts + 1):
        try:
            jpeg_bytes = capture_camera(camera_name, camera_cfg, cfg)
        except CaptureError as e:
            log.warning("%s: capture attempt %d/%d failed: %s", camera_name, attempt, retry_max, e)
            if attempt == retry_max:
                result.update(status="error", stage="capture", reason=str(e))
                return result
            time.sleep(1.0)
            continue
        except Exception as e:
            log.exception("%s: capture attempt %d/%d exception", camera_name, attempt, retry_max)
            if attempt == retry_max:
                result.update(status="error", stage="capture", reason=f"{type(e).__name__}: {e}")
                return result
            time.sleep(1.0)
            continue

        try:
            img = _decode_jpeg(jpeg_bytes)
        except Exception as e:
            log.warning("%s: decode attempt %d/%d failed: %s", camera_name, attempt, retry_max, e)
            if attempt == retry_max:
                result.update(status="error", stage="decode", reason=str(e))
                return result
            time.sleep(1.0)
            continue

        ok, last_gate_metrics = passes_trivial_gate(img, std_dev_floor=cfg.get("std_dev_floor", 5.0))
        if ok:
            break
        log.info("%s: trivial gate failed attempt %d/%d metrics=%s", camera_name, attempt, retry_max, last_gate_metrics)
        if attempt == retry_max:
            result.update(status="gated", stage="trivial_gate", metrics=last_gate_metrics)
            return result
        time.sleep(1.0)

    # Exposure gate: cheap, reuses metrics from the trivial gate. Rejects
    # near-black, blown-out, washed-out frames before they burn VLM time.
    exp_ok, exp_reason = passes_exposure_gate(
        last_gate_metrics,
        p50_floor=cfg.get("exposure_p50_floor", 25.0),
        p50_ceiling=cfg.get("exposure_p50_ceiling", 230.0),
        std_floor=cfg.get("exposure_std_floor", 15.0),
    )
    if not exp_ok:
        log.info("%s: exposure gate rejected: %s metrics=%s",
                 camera_name, exp_reason, last_gate_metrics)
        result.update(status="gated", stage="exposure", reason=exp_reason,
                      metrics=last_gate_metrics)
        return result

    # Sharpness gate: per-camera opt-in via `laplacian_floor` config. Rejects
    # blurry frames (bird too close to lens, motion blur). Zero extra cost —
    # Laplacian variance is already in last_gate_metrics from trivial gate.
    sharp_ok, sharp_reason = passes_sharpness_gate(
        last_gate_metrics,
        laplacian_floor=float(camera_cfg.get("laplacian_floor", 0.0)),
    )
    if not sharp_ok:
        log.info("%s: sharpness gate rejected: %s metrics=%s",
                 camera_name, sharp_reason, last_gate_metrics)
        result.update(status="gated", stage="sharpness", reason=sharp_reason,
                      metrics=last_gate_metrics)
        return result

    # Motion gate: per-camera opt-in. Skip the VLM when the scene hasn't
    # changed since the last accepted frame for this camera. First frame
    # after startup always accepts (no baseline yet).
    if motion_gate is not None and camera_cfg.get("motion_gate", False):
        accepted, motion_metrics = motion_gate.accept(camera_name, img)
        if not accepted:
            log.info("%s: motion gate rejected metrics=%s", camera_name, motion_metrics)
            result.update(status="gated", stage="motion", metrics=motion_metrics)
            return result
        last_gate_metrics = {**last_gate_metrics, **motion_metrics}

    # Pause gate: flag-file control plane — touch /tmp/farm-pipeline.pause
    # to skip VLM inference without stopping capture. Resume = remove file.
    if _PAUSE_FLAG.exists():
        result.update(status="paused", reason="pipeline paused via flag file")
        return result

    # Enrich via VLM. Send a downscaled copy of the frame — the model only
    # judges composition/clarity, not pixel-level detail, so a smaller image
    # is the biggest single cut to per-frame latency. The full-res jpeg_bytes
    # is still what gets archived/posted below.
    vlm_image = _downscale_for_vlm(
        jpeg_bytes, cfg.get("vlm_input_long_edge_px", 1024)
    )
    try:
        vlm_result = enrich(
            image_bytes=vlm_image,
            camera_name=camera_name,
            camera_context=camera_cfg.get("context", ""),
            lm_base=cfg["lm_studio_base"],
            model_id=cfg["vlm_model_id"],
            prompt_template=prompt_template,
            schema=schema,
            max_tokens=cfg.get("vlm_max_tokens", 600),
            temperature=cfg.get("vlm_temperature", 0.2),
            timeout=cfg.get("vlm_timeout_seconds", 120),
        )
    except ModelNotLoaded as e:
        log.warning("%s: VLM skip — %s", camera_name, e)
        result.update(status="skipped", stage="vlm", reason=f"model_not_loaded: {e}")
        return result
    except ValidationFailed as e:
        log.warning("%s: VLM validation failed: %s", camera_name, e)
        result.update(status="error", stage="validation", reason=str(e))
        return result
    except EnricherError as e:
        log.warning("%s: VLM error: %s", camera_name, e)
        result.update(status="error", stage="vlm", reason=str(e))
        return result
    except Exception as e:
        # LM Studio restart / network blip / socket timeout at the requests
        # layer can surface as ConnectionError, ReadTimeout, OSError etc.
        # Treat all of these as a transient skip so the daemon keeps running.
        log.warning("%s: VLM transient failure (%s: %s), skipping cycle",
                    camera_name, type(e).__name__, e)
        result.update(status="skipped", stage="vlm",
                      reason=f"transient: {type(e).__name__}: {e}")
        return result

    # Compute the 0-100 weighted score from components BEFORE any capping.
    _compute_overall_score(vlm_result["metadata"])

    if _calibrate_static_floor_pecking_score(camera_name, vlm_result["metadata"]):
        log.info(
            "%s: calibrated static floor-pecking frame score=%s share_worth=%s",
            camera_name,
            vlm_result["metadata"].get("overall_score"),
            vlm_result["metadata"].get("share_worth"),
        )

    # Store
    try:
        store_result = store(
            db_path=db_path,
            archive_root=archive_root,
            camera_id=camera_name,
            jpeg_bytes=jpeg_bytes,
            gate_metrics=last_gate_metrics,
            vlm_result=vlm_result,
            vlm_model=cfg["vlm_model_id"],
            retention_days_strong=cfg.get("retention_days_strong", 90),
            retention_days_decent=cfg.get("retention_days_decent", 90),
            retention_days_concerns=cfg.get("retention_days_concerns"),
            downscale_decent_long_edge_px=cfg.get("downscale_decent_long_edge_px", 1920),
            downscale_decent_jpeg_quality=cfg.get("downscale_decent_jpeg_quality", 85),
        )
    except Exception as e:
        log.exception("%s: store failed", camera_name)
        result.update(status="error", stage="store", reason=f"{type(e).__name__}: {e}")
        return result

    result.update(
        status="ok",
        inference_ms=vlm_result["inference_ms"],
        tier=store_result["tier"],
        image_path=store_result["image_path"],
        scene=vlm_result["metadata"]["scene"],
        bird_count=vlm_result["metadata"]["bird_count"],
        activity=vlm_result["metadata"]["activity"],
        image_quality=vlm_result["metadata"]["image_quality"],
        share_worth=vlm_result["metadata"]["share_worth"],
        has_concerns=store_result["has_concerns"],
    )

    # Auto-post gems to Discord. Never break the cycle on a failed post.
    try:
        if should_post(vlm_result["metadata"], store_result["tier"], camera_id=camera_name):
            import os as _os
            webhook = _os.environ.get("DISCORD_WEBHOOK_URL", "")
            # v2.44.5: Discord-lane caption trim (~300 chars, sentence-aware).
            # IG lane below uses the untrimmed caption_draft by design.
            _caption = trim_caption(vlm_result["metadata"].get("caption_draft", "") or "")
            _score = vlm_result["metadata"].get("overall_score")
            if _score is not None:
                _caption = f"{_caption}\n⭐ {_score}/100"
            # 99%-er: a frame-filling, ridiculous, claw-out bird (Boss's bar).
            # v2.45.1: >=90 (was >=95). The real component ceiling is ~92
            # (dominance 30 + expr ~25 + detail ~22 + technical 15 — the 4b VLM
            # never emits the full expr 30 / detail 25), so >=95 could never
            # fire; >=90 restores the @-mention on genuine bird selfies.
            if isinstance(_score, int) and _score >= 90:
                _caption = f"<@293569238386606080> BIRD SELFIE 💯\n{_caption}"
            # v2.67.1: honour the return value. This used to set
            # posted_to_discord=True unconditionally, so a gem that Discord
            # rejected still logged as posted — which is exactly how four
            # 503-dropped gems on 07-Aug-2026 looked completely healthy in
            # the log. A silent delivery failure at this stage is the most
            # expensive kind: the frame already passed every quality gate.
            # Latency is bounded deliberately. post_gem runs INLINE in the
            # daemon's tick loop, and it fires immediately after a strong
            # verdict — i.e. at the start of the 90 s HOT window, the single
            # highest-value sampling period we have (6.16x lift). At the
            # default 20 s timeout / 2 s backoff, three attempts could freeze
            # capture for 66 s and undo the throughput work outright. 8 s /
            # 1 s caps the worst case at ~27 s. The observed 503s returned in
            # well under a second, so this costs nothing in the normal case.
            result["posted_to_discord"] = post_gem(
                image_bytes=jpeg_bytes,
                caption=_caption,
                camera_name=camera_name,
                webhook_url=webhook,
                timeout=8,
                backoff_seconds=1.0,
            )
    except Exception as e:
        log.warning("%s: gem post wrapper failed: %s", camera_name, e)

    # Auto-post gems to Instagram. Gated on config["instagram"]["enabled"]
    # (default false). Separate from Discord so the two posting lanes fail
    # independently. Never break the cycle — IG API hiccups, Graph rate
    # limits, git-push issues, etc. all get logged and the pipeline rolls on.
    try:
        _maybe_post_to_ig(
            cfg=cfg,
            db_path=db_path,
            camera_name=camera_name,
            gem_id=store_result.get("gem_id"),
            vlm_metadata=vlm_result["metadata"],
            store_result=store_result,
            result=result,
        )
    except Exception as e:
        log.warning("%s: IG post wrapper failed: %s", camera_name, e)

    # Auto-post to Instagram Stories. Independent of the feed-post lane:
    # looser predicate (decent+soft allowed), independent cadence
    # (min_hours_between_stories), no per-camera dedup. A single gem can
    # in theory trigger both a feed post and a story, but in practice the
    # tier/quality thresholds differ so they land on different gems.
    # Gated on config["instagram"]["stories"]["enabled"] (default false).
    try:
        _maybe_post_to_story(
            cfg=cfg,
            db_path=db_path,
            camera_name=camera_name,
            gem_id=store_result.get("gem_id"),
            vlm_metadata=vlm_result["metadata"],
            store_result=store_result,
            result=result,
        )
    except Exception as e:
        log.warning("%s: IG story wrapper failed: %s", camera_name, e)
    return result


def _maybe_post_to_ig(
    cfg: dict,
    db_path: Path,
    camera_name: str,
    gem_id: int | None,
    vlm_metadata: dict,
    store_result: dict,
    result: dict,
) -> None:
    """Decide + act on IG auto-posting for the current cycle's gem.

    Gated in layers, outermost first:
      1. cfg["instagram"]["enabled"] — master switch. Default false; has
         to be explicitly flipped in config.json. Never turn this on
         without Boss's sign-off.
      2. gem_id is present (defensive — store_result should always have
         it post-Phase-7-prereq, but a KeyError here would bubble up to
         the outer except).
      3. should_post_ig predicate — same gate as the CLI, stricter than
         the Discord gate (see ig_poster.should_post_ig docstring).
      4. cfg["instagram"]["auto_dry_run"] — if true, call post_gem_to_ig
         with dry_run=True so the hook exercises the full path without
         publishing. Production gate: flip to false only after a few
         auto-dry-run cycles confirm the predicate is picking the right
         gems.

    Skip reasons (from should_post_ig) are persisted to
    image_archive.ig_skip_reason so we can audit what the predicate
    rejects over time. A write is skipped if gem_id is None (shouldn't
    happen, logged if it does).
    """
    ig_cfg = (cfg.get("instagram") or {})
    if not ig_cfg.get("enabled", False):
        return

    if gem_id is None:
        log.warning("%s: IG hook: store_result missing gem_id; skipping", camera_name)
        return

    last_any = query_last_ig_post_ts(db_path, camera_id=None)
    last_same = query_last_ig_post_ts(db_path, camera_id=camera_name)

    gem_row = {
        "camera_id": camera_name,
        "has_concerns": store_result.get("has_concerns", False),
    }
    ok, reason = should_post_ig(
        vlm_metadata=vlm_metadata,
        gem_row=gem_row,
        last_ig_post_ts=last_any,
        last_same_camera_ts=last_same,
        min_hours_between_posts=int(ig_cfg.get("min_hours_between_posts", 6)),
        min_hours_per_camera=int(ig_cfg.get("min_hours_per_camera", 12)),
    )
    if not ok:
        log.info("%s: IG predicate skip (gem_id=%s): %s", camera_name, gem_id, reason)
        # Persist the skip reason so we can audit later. Best-effort — if
        # the write fails (e.g. DB locked), log and continue.
        try:
            _write_permalink(
                db_path=db_path,
                gem_id=gem_id,
                permalink=None,
                posted_at_iso=None,
                skip_reason=reason,
            )
        except Exception as e:
            log.warning("%s: failed to write ig_skip_reason: %s", camera_name, e)
        result["ig_skipped"] = reason
        return

    # Build caption from VLM caption_draft + picked hashtags. Rotation-set
    # state (last_n_tags_used) is punted to [] — per advisor, shadow-ban
    # avoidance can be added later once we have enough auto-posts to see
    # repetition patterns. First N auto-posts will pull from the library's
    # natural ordering.
    journal = (vlm_metadata.get("caption_draft") or "").strip()
    if not journal:
        # Defensive: if the VLM didn't emit a caption, bail rather than
        # posting a bare hashtag line.
        log.info("%s: IG hook: empty caption_draft; skipping gem_id=%s", camera_name, gem_id)
        try:
            _write_permalink(
                db_path=db_path,
                gem_id=gem_id,
                permalink=None,
                posted_at_iso=None,
                skip_reason="empty_caption_draft",
            )
        except Exception as e:
            log.warning("%s: failed to write ig_skip_reason: %s", camera_name, e)
        result["ig_skipped"] = "empty_caption_draft"
        return

    try:
        from tools.pipeline.ig_poster import recent_tags_used
        library = _load_hashtag_library(Path(__file__).parent / "hashtags.yml")
        tags = pick_hashtags(
            vlm_metadata=vlm_metadata,
            library=library,
            # v2.47.0: rotation fed from the posted-caption ledger (was []).
            last_n_tags_used=recent_tags_used(db_path),
        )
        caption = build_caption(journal_body=journal, hashtags=tags)
    except Exception as e:
        log.warning("%s: IG hook: caption build failed: %s", camera_name, e)
        result["ig_skipped"] = f"caption_build_error: {type(e).__name__}"
        return

    # Resolve farm-2026 path from config.
    farm_2026 = Path(ig_cfg.get("farm_2026_repo_path", "")).expanduser()
    if not farm_2026.exists():
        log.warning(
            "%s: IG hook: farm_2026_repo_path not found: %s", camera_name, farm_2026
        )
        result["ig_skipped"] = "farm_2026_repo_missing"
        return

    auto_dry_run = bool(ig_cfg.get("auto_dry_run", True))
    try:
        ig_result = post_gem_to_ig(
            gem_id=gem_id,
            full_caption=caption,
            db_path=db_path,
            farm_2026_repo_path=farm_2026,
            dry_run=auto_dry_run,
        )
    except IGPosterError as e:
        log.warning("%s: IG post credential/config error: %s", camera_name, e)
        result["ig_skipped"] = f"credentials: {e}"
        return

    if ig_result.get("error"):
        log.warning("%s: IG post failed: %s", camera_name, ig_result["error"])
        result["ig_error"] = ig_result["error"]
        return

    if auto_dry_run:
        log.info("%s: IG auto_dry_run — would have posted gem_id=%s", camera_name, gem_id)
        result["ig_dry_run"] = True
        return

    result["ig_permalink"] = ig_result.get("permalink")
    result["ig_media_id"] = ig_result.get("media_id")
    log.info("%s: IG posted gem_id=%s permalink=%s",
             camera_name, gem_id, ig_result.get("permalink"))


def _maybe_post_to_story(
    cfg: dict,
    db_path: Path,
    camera_name: str,
    gem_id: int | None,
    vlm_metadata: dict,
    store_result: dict,
    result: dict,
) -> None:
    """Decide + act on IG Story auto-posting for the current cycle's gem.

    Layered gate (outermost first):
      1. cfg["instagram"]["stories"]["enabled"] — master switch; default
         false. Stories ship gated off even though the feed-post lane is
         live, so the rollout can be staged independently.
      2. gem_id is present (defensive — should always be set after the
         store step succeeded).
      3. should_post_story predicate — looser than the feed predicate
         (tier in {strong, decent}, image_quality in {sharp, soft},
         no per-camera dedup, story-specific cadence).
      4. cfg["instagram"]["stories"]["auto_dry_run"] — if true, call
         post_gem_to_story with dry_run=True so the hook exercises
         the full path (9:16 prep + URL prediction) without committing
         or publishing. Operator flips to false once a day of dry-run
         audit confirms the predicate is picking reasonable gems.

    Skip reasons from should_post_story go to ig_story_skip_reason for
    audit. Writes are best-effort — a failure to persist a skip reason
    is logged and swallowed.
    """
    ig_cfg = cfg.get("instagram") or {}
    stories_cfg = ig_cfg.get("stories") or {}
    if not stories_cfg.get("enabled", False):
        return

    if gem_id is None:
        log.warning("%s: IG story hook: store_result missing gem_id; skipping", camera_name)
        return

    last_story = query_last_story_ts(db_path)

    gem_row = {
        "camera_id": camera_name,
        "has_concerns": store_result.get("has_concerns", False),
    }
    ok, reason = should_post_story(
        vlm_metadata=vlm_metadata,
        gem_row=gem_row,
        last_story_ts=last_story,
        min_hours_between_stories=int(stories_cfg.get("min_hours_between_stories", 2)),
    )
    if not ok:
        log.info("%s: IG story predicate skip (gem_id=%s): %s", camera_name, gem_id, reason)
        try:
            _write_story_metadata(
                db_path=db_path,
                gem_id=gem_id,
                story_id=None,
                posted_at_iso=None,
                skip_reason=reason,
            )
        except Exception as e:
            log.warning("%s: failed to write ig_story_skip_reason: %s", camera_name, e)
        result["ig_story_skipped"] = reason
        return

    farm_2026 = Path(ig_cfg.get("farm_2026_repo_path", "")).expanduser()
    if not farm_2026.exists():
        log.warning(
            "%s: IG story hook: farm_2026_repo_path not found: %s",
            camera_name, farm_2026,
        )
        result["ig_story_skipped"] = "farm_2026_repo_missing"
        return

    auto_dry_run = bool(stories_cfg.get("auto_dry_run", True))
    try:
        story_result = post_gem_to_story(
            gem_id=gem_id,
            db_path=db_path,
            farm_2026_repo_path=farm_2026,
            dry_run=auto_dry_run,
        )
    except IGPosterError as e:
        log.warning("%s: IG story credential/config error: %s", camera_name, e)
        result["ig_story_skipped"] = f"credentials: {e}"
        return

    if story_result.get("error"):
        log.warning("%s: IG story post failed: %s", camera_name, story_result["error"])
        result["ig_story_error"] = story_result["error"]
        return

    if auto_dry_run:
        log.info("%s: IG story auto_dry_run — would have posted gem_id=%s", camera_name, gem_id)
        result["ig_story_dry_run"] = True
        return

    result["ig_story_id"] = story_result.get("story_id")
    result["ig_story_permalink"] = story_result.get("permalink")
    log.info(
        "%s: IG story posted gem_id=%s story_id=%s permalink=%s",
        camera_name, gem_id, story_result.get("story_id"), story_result.get("permalink"),
    )


def _next_cadence(camera_name: str, camera_cfg: dict, result: dict) -> float:
    """How long to wait before this camera's next cycle — the expensive loop's
    feedback into scheduling.

    Rationale, measured over 21 days / 35,732 s7-cam frames: gems arrive in
    minute-scale bursts, not at particular times of day. Given a strong frame,
    P(another strong frame within 60 s) = 38.2% against a 6.2% base rate — a
    6.16x lift, decaying to 3.4x at 180 s and 2.0x at 600 s. Hour-of-day, by
    contrast, spans only 1.6%-5.3% across the whole daylight window, which is
    why this is a reactive state machine and NOT a reuse of the
    timelapse_golden_windows scheduler built for usb-cam/dominator-cam.

    Note the asymmetry that makes this safe: HOT can only be entered by an
    actual VLM verdict, so the pipeline speeds up only when it has already
    proven something is happening. COLD is entered from repeated presence
    misses, which are cheap to observe.

      HOT   — a strong frame just landed; sample hard for hot_hold_seconds
      WARM  — birds around but nothing special; the normal working cadence
      COLD  — presence gate has found nothing cold_after_misses times running

    Cameras without a `hunt` block are unaffected and keep cycle_seconds.
    """
    hunt_cfg = camera_cfg.get("hunt") or {}
    base = float(camera_cfg.get("cycle_seconds", 45))
    if not hunt_cfg.get("enabled", False):
        return base

    cadence_cfg = hunt_cfg.get("cadence") or {}
    hot = float(cadence_cfg.get("hot_seconds", 0.5))
    warm = float(cadence_cfg.get("warm_seconds", base))
    cold = float(cadence_cfg.get("cold_seconds", 20.0))
    hold = float(cadence_cfg.get("hot_hold_seconds", 90.0))
    cold_after = int(cadence_cfg.get("cold_after_misses", 3))

    state = _HUNT_STATE.setdefault(camera_name, {"hot_until": 0.0, "miss_streak": 0})
    now = time.monotonic()

    if result.get("share_worth") == "strong":
        state["hot_until"] = now + hold
        state["miss_streak"] = 0
        log.info("%s: HOT for %.0fs (strong frame)", camera_name, hold)
        return hot

    # Only a presence-gate skip counts as a miss. A VLM 'skip' verdict still
    # means birds were there, which is a warm scene, not a cold one.
    if result.get("stage") == "presence":
        state["miss_streak"] += 1
    else:
        state["miss_streak"] = 0

    if now < state["hot_until"]:
        return hot
    if state["miss_streak"] >= cold_after:
        return cold
    return warm


def _load_configs():
    here = Path(__file__).parent
    cfg = json.loads((here / "config.json").read_text())

    # ⚠️ THE PRESET PATH IS RETIRED (28-Jul-2026, v2.55.0). `birds_preset_path`
    # is now "" in config.json, so `tools/pipeline/prompt.md` and
    # `tools/pipeline/schema.json` — the files in git — are the SOLE source of
    # truth for the prompt and the response schema.
    #
    # DO NOT re-point this at a preset. The dual-file trap bit twice:
    #   16-Jul-2026 — prompt.md/schema.json edited for the Birdcatraz move, the
    #     daemon restarted, and the live preset (last touched 12-Jul) silently
    #     kept serving the stale prompt for hours.
    #   28-Jul-2026 — an entire band-identification rework (new schema fields,
    #     rewritten prompt) was shipped, committed, and the daemon restarted.
    #     Every change was INERT: the preset dated 22-Jul kept being served, and
    #     the only tell was that archived rows carried the old `vlm_prompt_hash`.
    #     The 16-Jul comment predicted this exact failure and recommended
    #     retiring the preset; that recommendation is now applied.
    #
    # The preset file is left on disk for manual experimentation in the LM
    # Studio GUI. It is NOT read by the pipeline and is expected to drift.
    #
    # The branch below is kept only so a non-empty `birds_preset_path` in an old
    # config still works. If you find yourself setting it, read the two
    # incidents above first — you are about to make your edits invisible.
    preset_path_raw = cfg.get("birds_preset_path")
    if preset_path_raw:
        preset_path = Path(preset_path_raw).expanduser()
        if preset_path.exists():
            preset = json.loads(preset_path.read_text())
            pfields = {f["key"]: f["value"] for f in preset.get("operation", {}).get("fields", [])}
            prompt_template = pfields.get("llm.prediction.systemPrompt", "")
            structured = pfields.get("llm.prediction.structured", {})
            schema = {
                "name": "farm_image_metadata",
                "strict": True,
                "schema": structured.get("jsonSchema", {}),
            }
            log.info("loaded prompt+schema from Birds preset: %s", preset_path)
        else:
            log.warning("birds_preset_path configured but missing: %s — falling back to schema.json+prompt.md", preset_path)
            schema = json.loads((here / "schema.json").read_text())
            prompt_template = (here / "prompt.md").read_text()
    else:
        schema = json.loads((here / "schema.json").read_text())
        prompt_template = (here / "prompt.md").read_text()

    repo_root = Path(__file__).resolve().parents[2]
    db_path = repo_root / cfg["guardian_db_path"]
    archive_root = repo_root / cfg["archive_root"]
    # Load .env so DISCORD_WEBHOOK_URL is available for gem auto-posting.
    # Idempotent — does not overwrite launchd-injected env vars.
    load_dotenv(repo_root / ".env")
    # Also load Meta/IG creds env file if configured. Same load_dotenv
    # (does not overwrite existing vars) so keychain-sourced values in a
    # launchd plist would still win.
    ig_cfg = cfg.get("instagram", {}) or {}
    meta_env = ig_cfg.get("meta_env_file")
    if meta_env:
        meta_env_path = Path(meta_env).expanduser()
        if meta_env_path.exists():
            load_dotenv(meta_env_path)
        else:
            log.warning("instagram.meta_env_file configured but missing: %s", meta_env_path)
    return cfg, schema, prompt_template, db_path, archive_root


def _install_signal_handlers():
    def handler(signum, _frame):
        log.info("signal %d received, shutting down after current cycle", signum)
        _STOP.set()
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


def run_once(only_camera: str | None = None) -> int:
    cfg, schema, prompt_template, db_path, archive_root = _load_configs()
    ensure_schema(db_path)
    # Motion gate in --once mode is effectively a no-op (every camera's
    # first frame always accepts), but we construct one so the code path
    # matches the daemon's.
    motion_gate = MotionGate(threshold=cfg.get("motion_delta_threshold", 3.0))
    any_error = False
    for name, ccfg in cfg["cameras"].items():
        if only_camera and name != only_camera:
            continue
        if not ccfg.get("enabled", False):
            log.info("%s: disabled, skipping", name)
            continue
        log.info("%s: cycle start", name)
        if ccfg.get("vlm_bypass", False):
            r = run_raw_cycle(name, ccfg, cfg, db_path, archive_root)
        else:
            r = run_cycle(name, ccfg, cfg, schema, prompt_template, db_path,
                          archive_root, motion_gate=motion_gate)
        log.info("%s: %s", name, json.dumps(r, default=str))
        if r.get("status") == "error":
            any_error = True
    return 0 if not any_error else 1


def _run_raw_camera_thread(camera_name: str, ccfg: dict, cfg: dict,
                           db_path: Path, archive_root: Path) -> None:
    """Dedicated loop for a vlm_bypass camera. Runs on its own thread so
    capture cadence isn't gated by the main VLM-serialized tick loop, and
    runs a rolling raw-tier pruner inline so we don't grow unboundedly.

    Golden-window capture (usb-cam / dominator-cam): when this camera opts in
    via instagram.scheduled.timelapse_golden_windows AND has an
    offpeak_cycle_seconds set, the cadence is recomputed every iteration —
    THICK (cycle_seconds) inside the two daily activity windows, SPARSE
    (offpeak_cycle_seconds) outside them. The off-peak frames are a slow
    heartbeat (camera provably alive, incidental midday bird not 100% lost),
    not a full stop. Mirrors the house-yard night_snapshot_interval precedent:
    per-camera interval values, shared time-of-day predicate. Cameras without
    offpeak_cycle_seconds keep the original fixed cadence (unchanged behavior).
    """
    thick_cadence = float(ccfg.get("cycle_seconds", 45))
    offpeak_raw = ccfg.get("offpeak_cycle_seconds")
    gw_root = ((cfg.get("instagram") or {}).get("scheduled") or {}).get(
        "timelapse_golden_windows"
    ) or {}
    golden_active = (
        offpeak_raw is not None
        and camera_uses_golden_windows(camera_name, gw_root)
    )
    gwc = camera_golden_cfg(camera_name, gw_root) if golden_active else {}
    offpeak_cadence = float(offpeak_raw) if offpeak_raw is not None else thick_cadence
    if golden_active:
        log.info(
            "%s: golden-window capture ON — thick %.0fs in-window / sparse %.0fs "
            "off-peak (windows=%s)",
            camera_name, thick_cadence, offpeak_cadence, gwc.get("windows"),
        )

    def _current_cadence() -> float:
        if not golden_active:
            return thick_cadence
        try:
            in_window = is_dt_in_golden_windows(datetime.now(timezone.utc), gwc)
        except Exception:
            # Never let a window-calc error stop capture — fall back to thick.
            log.exception("%s: golden-window check failed; using thick cadence",
                          camera_name)
            return thick_cadence
        return thick_cadence if in_window else offpeak_cadence

    # Per-camera override takes precedence; global default is 24h.
    retention_hours = int(ccfg.get("raw_retention_hours", cfg.get("raw_retention_hours", 24)))
    last_prune = 0.0
    prune_every = 300.0  # sweep once every 5 minutes
    # Initial stagger so thread doesn't wake in lockstep with main loop.
    _STOP.wait(timeout=min(5.0, thick_cadence))
    while not _STOP.is_set():
        t0 = time.monotonic()
        cadence = _current_cadence()
        try:
            r = run_raw_cycle(camera_name, ccfg, cfg, db_path, archive_root)
            log.info("%s: %s (raw thread)", camera_name, json.dumps(r, default=str))
        except Exception:
            log.exception("%s: raw thread cycle raised", camera_name)
        if time.monotonic() - last_prune >= prune_every:
            try:
                pr = retention_sweep_raw(db_path, archive_root, camera_name,
                                         retention_hours=retention_hours)
                if pr.get("deleted"):
                    log.info("%s: raw prune %s", camera_name, json.dumps(pr))
            except Exception:
                log.exception("%s: raw prune raised", camera_name)
            last_prune = time.monotonic()
        elapsed = time.monotonic() - t0
        _STOP.wait(timeout=max(0.5, cadence - elapsed))


def run_daemon() -> int:
    global _MOTION_GATE
    cfg, schema, prompt_template, db_path, archive_root = _load_configs()
    ensure_schema(db_path)
    _install_signal_handlers()
    _MOTION_GATE = MotionGate(threshold=cfg.get("motion_delta_threshold", 3.0))

    # Ensure the VLM is loaded at the configured context BEFORE the loop, so a
    # reboot or LM Studio restart can't leave it JIT-loaded at a too-small
    # context (which 400s portrait frames with "Context size has been
    # exceeded"). Non-fatal: if LM Studio is down or slow at startup, log and
    # carry on — the per-cycle read-only skip already handles an unloaded model.
    want_ctx = cfg.get("vlm_load_context_length", 16384)
    try:
        outcome = ensure_model_loaded(cfg["lm_studio_base"], cfg["vlm_model_id"], want_ctx)
        log.info("VLM ensure-loaded %s at context>=%d: %s",
                 cfg["vlm_model_id"], want_ctx, outcome)
    except Exception as e:
        log.warning("ensure_model_loaded failed (%s: %s) — continuing; "
                    "per-cycle skip handles an unloaded model",
                    type(e).__name__, e)

    # Launch dedicated threads for vlm_bypass cameras so they don't contend
    # with the main VLM-serialized scheduler. These threads own their own
    # cadence, capture, storage, and rolling raw retention.
    raw_threads: list[threading.Thread] = []
    for name, ccfg in cfg["cameras"].items():
        if not ccfg.get("enabled", False):
            continue
        if not ccfg.get("vlm_bypass", False):
            continue
        t = threading.Thread(
            target=_run_raw_camera_thread,
            args=(name, ccfg, cfg, db_path, archive_root),
            name=f"raw-{name}", daemon=True,
        )
        t.start()
        raw_threads.append(t)
        log.info("%s: raw-capture thread started (cadence %ds, vlm_bypass=true)",
                 name, ccfg.get("cycle_seconds", 45))

    # Per-camera next-due tracking (VLM-gated cameras only)
    now = time.monotonic()
    next_due: dict[str, float] = {}
    for name, ccfg in cfg["cameras"].items():
        if not ccfg.get("enabled", False):
            continue
        if ccfg.get("vlm_bypass", False):
            continue  # handled by dedicated thread above
        # Stagger start so all cameras don't fire at the same instant — spread
        # across the first minute.
        offset = (hash(name) % 60)
        next_due[name] = now + offset
        log.info("%s: scheduled first cycle in %ds (cadence %ds)", name, offset, ccfg["cycle_seconds"])

    last_retention_day = None
    cycle_count = 0

    while not _STOP.is_set():
        now = time.monotonic()
        # Find cameras whose next_due has passed. Sort by (priority asc,
        # how-overdue desc) so higher-priority cameras (lower numeric value)
        # get the VLM slot when multiple fire in the same 1-second tick.
        # Default priority is 5; s7-cam carries priority=1 because it's the
        # sharpest source in the fleet and we want to bias gems toward it.
        # Ties broken by how long the camera has been waiting past its
        # next_due so no camera starves.
        def _sort_key(n: str) -> tuple[int, float]:
            prio = int(cfg["cameras"][n].get("priority", 5))
            overdue = now - next_due[n]
            return (prio, -overdue)
        ready = sorted(
            (n for n, due in next_due.items() if due <= now),
            key=_sort_key,
        )
        for name in ready:
            if _STOP.is_set():
                break
            ccfg = cfg["cameras"][name]
            t0 = time.monotonic()
            try:
                if ccfg.get("vlm_bypass", False):
                    r = run_raw_cycle(name, ccfg, cfg, db_path, archive_root)
                else:
                    r = run_cycle(name, ccfg, cfg, schema, prompt_template, db_path,
                                  archive_root, motion_gate=_MOTION_GATE)
            except Exception as e:
                # Last-resort guard: run_cycle is supposed to never raise, but
                # if it does, don't let one bad cycle take the daemon down.
                log.exception("%s: run_cycle raised unexpectedly", name)
                r = {"camera": name, "status": "error", "stage": "orchestrator",
                     "reason": f"{type(e).__name__}: {e}"}
            elapsed = time.monotonic() - t0
            cadence = _next_cadence(name, ccfg, r)
            next_due[name] = time.monotonic() + cadence
            cycle_count += 1
            log.info("%s: %s elapsed=%.1fs next_in=%.1fs (cycle #%d)",
                     name, json.dumps(r, default=str), elapsed, cadence, cycle_count)

        # Daily retention sweep at roughly the same time each day
        today = datetime.now().date()
        if last_retention_day != today:
            r = retention_sweep(db_path, archive_root)
            log.info("retention: %s", json.dumps(r))

            # Keyframe tier (v2.69.0). Capture went from 3/day to ~168/day to
            # feed the dense time-lapse reels, so this tier can no longer be
            # unbounded — at that rate it would accrue ~117 GB/year across the
            # two cameras. The window only has to outlast the longest consumer,
            # which is the 30-day monthly reel; the default 768h (32 days)
            # leaves two days of slack for a late monthly run.
            kf_cfg = cfg.get("keyframe_capture") or {}
            kf_hours = int(kf_cfg.get("retention_hours", 768))
            if kf_hours > 0:
                for kf_camera in (kf_cfg.get("cameras") or []):
                    kr = retention_sweep_raw(
                        db_path, archive_root, kf_camera,
                        retention_hours=kf_hours, image_tier="keyframe",
                    )
                    if kr.get("deleted"):
                        log.info("retention(keyframe): %s", json.dumps(kr))

            last_retention_day = today

        # Sleep 1s between ticks; signals still wake us via _STOP
        _STOP.wait(timeout=1.0)

    log.info("daemon shutdown, ran %d cycles", cycle_count)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-cam image pipeline")
    parser.add_argument("--once", action="store_true", help="Run one cycle per enabled camera and exit")
    parser.add_argument("--daemon", action="store_true", help="Run forever on per-camera cadences")
    parser.add_argument("--camera", help="Limit --once to a single camera")
    parser.add_argument("--retention-only", action="store_true", help="Only run retention sweep")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.retention_only:
        cfg, _, _, db_path, archive_root = _load_configs()
        r = retention_sweep(db_path, archive_root)
        print(json.dumps(r, indent=2))
        return 0

    if args.once:
        return run_once(only_camera=args.camera)
    if args.daemon:
        return run_daemon()
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
