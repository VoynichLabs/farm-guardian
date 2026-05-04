# Author: Claude Opus 4.7 (1M context); Claude Sonnet 4.6 (edits 27-April-2026 — vlm_bypass mode: run_raw_cycle, dedicated raw threads, raw retention sweep, v2.37.13; 28-April-2026 — sharpness gate wired in, v2.37.14; 04-May-2026 — Birds preset as prompt/schema source, v2.40.0)
# Date: 17-April-2026
# PURPOSE: Main entry point for the multi-cam image pipeline. Schedules per-
#          camera capture cycles at their configured cadences, runs each
#          frame through a four-stage pre-VLM filter (trivial std-dev gate,
#          exposure gate, per-camera motion gate), enriches passing frames
#          via the VLM, persists to SQLite + disk. Single in-flight VLM call
#          (enforced in vlm_enricher via a module-level lock). LM Studio
#          coordination is read-only: if the wrong model is loaded (or
#          nothing is loaded), the cycle is logged and skipped — we do not
#          auto-load to avoid contention with G0DM0D3 sweeps, per
#          docs/13-Apr-2026-lm-studio-reference.md.
#
#          Motion gate is opt-in per camera via `motion_gate: true` in the
#          camera's config block. Outdoor/coop cameras (house-yard, gwtc)
#          enable it because 90%+ of their frames are unchanged yard/coop
#          and returned `skip` from the VLM. Brooder cameras leave it off
#          because chicks move continuously and we want the VLM on every
#          frame.
#
#          Modes:
#            --once                : run every enabled camera once, exit
#            --once --camera NAME  : run one camera once, exit
#            --daemon              : run forever on per-camera cadences
#            --retention-only      : run the retention sweep and exit
# SRP/DRY check: Pass — single responsibility is scheduling + gluing the
#                other pipeline modules together.

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

# Support both `python -m tools.pipeline.orchestrator` and
# `python tools/pipeline/orchestrator.py` invocations.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.pipeline.capture import capture_camera, CaptureError
    from tools.pipeline.quality_gate import passes_trivial_gate, passes_exposure_gate, passes_sharpness_gate, MotionGate
    from tools.pipeline.vlm_enricher import enrich, ModelNotLoaded, EnricherError, ValidationFailed
    from tools.pipeline.store import ensure_schema, store, store_raw
    from tools.pipeline.retention import sweep as retention_sweep, sweep_raw as retention_sweep_raw
    from tools.pipeline.gem_poster import post_gem, should_post, load_dotenv
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
    from .capture import capture_camera, CaptureError
    from .quality_gate import passes_trivial_gate, passes_exposure_gate, passes_sharpness_gate, MotionGate
    from .vlm_enricher import enrich, ModelNotLoaded, EnricherError, ValidationFailed
    from .store import ensure_schema, store, store_raw
    from .retention import sweep as retention_sweep, sweep_raw as retention_sweep_raw
    from .gem_poster import post_gem, should_post, load_dotenv
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


def run_raw_cycle(camera_name: str, camera_cfg: dict, cfg: dict,
                  db_path: Path, archive_root: Path) -> dict:
    """Capture → save-to-disk cycle for cameras marked vlm_bypass=true.

    Bypasses the VLM queue entirely: no quality gate, no exposure gate,
    no motion gate, no VLM inference, no Discord/IG posting. Raw JPEG
    lands on disk and the image_archive row carries tier='raw' with
    vlm_* columns NULL. Intended for house-yard, where ~95% of frames
    would be rated 'skip' anyway and VLM contention was starving the
    effective cadence to 60-85s against a 45s target.
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
    try:
        sr = store_raw(db_path=db_path, archive_root=archive_root,
                       camera_id=camera_name, jpeg_bytes=jpeg_bytes)
    except Exception as e:
        log.exception("%s: raw store failed", camera_name)
        result.update(status="error", stage="store",
                      reason=f"{type(e).__name__}: {e}")
        return result
    result.update(status="ok", tier=sr["tier"], image_path=sr["image_path"],
                  stored_bytes=sr["stored_bytes"],
                  width=sr["width"], height=sr["height"])
    return result


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
    for attempt in range(1, retry_max + 1):
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

    # Enrich via VLM
    try:
        vlm_result = enrich(
            image_bytes=jpeg_bytes,
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
            _caption = vlm_result["metadata"].get("caption_draft", "") or ""
            _score = vlm_result["metadata"].get("overall_score")
            if _score is not None:
                _caption = f"{_caption}\n⭐ {_score}/10"
            post_gem(
                image_bytes=jpeg_bytes,
                caption=_caption,
                camera_name=camera_name,
                webhook_url=webhook,
            )
            result["posted_to_discord"] = True
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
        library = _load_hashtag_library(Path(__file__).parent / "hashtags.yml")
        tags = pick_hashtags(
            vlm_metadata=vlm_metadata,
            library=library,
            last_n_tags_used=[],
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


def _load_configs():
    here = Path(__file__).parent
    cfg = json.loads((here / "config.json").read_text())

    # If birds_preset_path is configured, use the LM Studio Birds preset as the
    # single source of truth for both the prompt template and the response schema.
    # This keeps the pipeline and the LM Studio UI preset in sync — editing the
    # preset file in ~/.lmstudio/config-presets/ updates both at once.
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
    runs a rolling raw-tier pruner inline so we don't grow unboundedly."""
    cadence = int(ccfg.get("cycle_seconds", 45))
    retention_hours = int(cfg.get("raw_retention_hours", 24))
    last_prune = 0.0
    prune_every = 300.0  # sweep once every 5 minutes
    # Initial stagger so thread doesn't wake in lockstep with main loop.
    _STOP.wait(timeout=min(5.0, cadence))
    while not _STOP.is_set():
        t0 = time.monotonic()
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
            next_due[name] = time.monotonic() + ccfg["cycle_seconds"]
            cycle_count += 1
            log.info("%s: %s elapsed=%.1fs next_in=%ds (cycle #%d)",
                     name, json.dumps(r, default=str), elapsed, ccfg["cycle_seconds"], cycle_count)

        # Daily retention sweep at roughly the same time each day
        today = datetime.now().date()
        if last_retention_day != today:
            r = retention_sweep(db_path, archive_root)
            log.info("retention: %s", json.dumps(r))
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
