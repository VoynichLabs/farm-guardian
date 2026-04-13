# Author: Claude Opus 4.6 (1M context)
# Date: 13-April-2026
# PURPOSE: Main entry point for the multi-cam image pipeline. Schedules per-
#          camera capture cycles at their configured cadences, runs each
#          frame through the trivial garbage gate, enriches passing frames
#          via glm-4.6v-flash, persists to SQLite + disk. Single in-flight
#          VLM call (enforced in vlm_enricher via a module-level lock). LM
#          Studio coordination is read-only: if the wrong model is loaded
#          (or nothing is loaded), the cycle is logged and skipped — we do
#          not auto-load to avoid contention with G0DM0D3 sweeps, per
#          docs/13-Apr-2026-lm-studio-reference.md.
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
    from tools.pipeline.quality_gate import passes_trivial_gate
    from tools.pipeline.vlm_enricher import enrich, ModelNotLoaded, EnricherError, ValidationFailed
    from tools.pipeline.store import ensure_schema, store
    from tools.pipeline.retention import sweep as retention_sweep
else:
    from .capture import capture_camera, CaptureError
    from .quality_gate import passes_trivial_gate
    from .vlm_enricher import enrich, ModelNotLoaded, EnricherError, ValidationFailed
    from .store import ensure_schema, store
    from .retention import sweep as retention_sweep


log = logging.getLogger("pipeline.orchestrator")

_STOP = threading.Event()


def _decode_jpeg(jpeg_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("jpeg decode failed")
    return img


def run_cycle(camera_name: str, camera_cfg: dict, cfg: dict, schema: dict,
              prompt_template: str, db_path: Path, archive_root: Path) -> dict:
    """One capture → gate → enrich → store cycle for one camera.
    Returns a summary dict. Never raises — failures are returned as
    {status: 'error', reason: '...'}."""
    result = {"camera": camera_name, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    retry_max = cfg.get("capture_retry_max", 3)

    # Capture with retry on trivial-gate failure
    last_gate_metrics = None
    jpeg_bytes = None
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
    return result


def _load_configs():
    here = Path(__file__).parent
    cfg = json.loads((here / "config.json").read_text())
    schema = json.loads((here / "schema.json").read_text())
    prompt_template = (here / "prompt.md").read_text()
    repo_root = Path(__file__).resolve().parents[2]
    db_path = repo_root / cfg["guardian_db_path"]
    archive_root = repo_root / cfg["archive_root"]
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
    any_error = False
    for name, ccfg in cfg["cameras"].items():
        if only_camera and name != only_camera:
            continue
        if not ccfg.get("enabled", False):
            log.info("%s: disabled, skipping", name)
            continue
        log.info("%s: cycle start", name)
        r = run_cycle(name, ccfg, cfg, schema, prompt_template, db_path, archive_root)
        log.info("%s: %s", name, json.dumps(r, default=str))
        if r.get("status") == "error":
            any_error = True
    return 0 if not any_error else 1


def run_daemon() -> int:
    cfg, schema, prompt_template, db_path, archive_root = _load_configs()
    ensure_schema(db_path)
    _install_signal_handlers()

    # Per-camera next-due tracking
    now = time.monotonic()
    next_due: dict[str, float] = {}
    for name, ccfg in cfg["cameras"].items():
        if not ccfg.get("enabled", False):
            continue
        # Stagger start so all cameras don't fire at the same instant — spread
        # across the first minute.
        offset = (hash(name) % 60)
        next_due[name] = now + offset
        log.info("%s: scheduled first cycle in %ds (cadence %ds)", name, offset, ccfg["cycle_seconds"])

    last_retention_day = None
    cycle_count = 0

    while not _STOP.is_set():
        now = time.monotonic()
        # Find cameras whose next_due has passed
        ready = [n for n, due in next_due.items() if due <= now]
        for name in ready:
            if _STOP.is_set():
                break
            ccfg = cfg["cameras"][name]
            t0 = time.monotonic()
            r = run_cycle(name, ccfg, cfg, schema, prompt_template, db_path, archive_root)
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
