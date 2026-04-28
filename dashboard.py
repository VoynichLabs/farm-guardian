# Author: Claude Opus 4.6 (updated), Cascade (Claude Sonnet 4) (original); Claude Sonnet 4.6 (edits 27-April-2026 — allow_stale on frame endpoints, v2.37.13)
# Date: 13-April-2026 (v2.18.0 — frame/stream endpoints prefer original camera JPEG when present)
# PURPOSE: Local web dashboard for Farm Guardian. Serves a FastAPI app on the Mac Mini
#          that provides real-time monitoring and full control of the guardian service.
#          Features: camera snapshot feeds (polled), detection timeline, alert history, PTZ
#          controls, deterrent status, daily reports, camera start/stop/rescan controls,
#          detection threshold tuning, zone masking config, and Discord alert testing.
#          Also mounts the /api/v1/ REST API router for LLM tool access.
#          Accessed via browser on the local network.
# SRP/DRY check: Pass — single responsibility is HTTP API + dashboard serving.

import asyncio
import json
import logging
import time
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger("guardian.dashboard")

# Module-level reference to the guardian service — set by start_dashboard()
_service = None
_config = {}
_config_path = "config.json"

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Farm Guardian", docs_url=None, redoc_url=None)

    # Allow farm site to call Guardian API (PTZ controls, image archive, etc.).
    # v2.25.0 widened this: DELETE is needed for /api/v1/images/review/{id};
    # Authorization + If-None-Match are needed for bearer auth and ETag 304s.
    # `http://localhost:3000` stays in for farm-2026 local dev per the cross-repo plan.
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://farm.markbarney.net", "http://localhost:3000"],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "If-None-Match"],
    )

    # Serve static files (JS, CSS)
    STATIC_DIR.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ──────────────────────────────────────────────
    # Dashboard HTML
    # ──────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def index():
        html_path = STATIC_DIR / "index.html"
        if not html_path.exists():
            raise HTTPException(500, "Dashboard HTML not found")
        return HTMLResponse(html_path.read_text(encoding="utf-8"))

    # ──────────────────────────────────────────────
    # Service Status
    # ──────────────────────────────────────────────

    @app.get("/api/status")
    async def get_status():
        if not _service:
            return {"online": False}
        uptime = time.time() - _service._start_time if _service._start_time else 0
        cameras = _service._discovery.cameras
        online_count = sum(1 for c in cameras.values() if c.online)

        today_str = date.today().isoformat()

        # Prefer DB counts — accurate after restart. Fall back to in-memory buffer
        # if DB is unavailable.
        today_detections = 0
        today_alerts = 0
        if hasattr(_service, '_db') and _service._db:
            try:
                db = _service._db
                today_start = f"{today_str}T00:00:00"
                today_end = f"{today_str}T23:59:59"
                with db._lock:
                    row = db._conn.execute(
                        "SELECT COUNT(*) as cnt FROM detections WHERE detected_at BETWEEN ? AND ? AND suppressed = 0",
                        (today_start, today_end),
                    ).fetchone()
                    today_detections = row["cnt"] if row else 0
                    row2 = db._conn.execute(
                        "SELECT COUNT(*) as cnt FROM alerts WHERE alerted_at BETWEEN ? AND ?",
                        (today_start, today_end),
                    ).fetchone()
                    today_alerts = row2["cnt"] if row2 else 0
            except Exception as exc:
                log.warning("DB count query failed, falling back to buffer: %s", exc)
                today_detections = sum(
                    1 for d in _service.recent_detections
                    if d.get("timestamp", "").startswith(today_str)
                )
                today_alerts = sum(
                    1 for a in _service.recent_alerts
                    if a.get("timestamp", "").startswith(today_str)
                )
        else:
            today_detections = sum(
                1 for d in _service.recent_detections
                if d.get("timestamp", "").startswith(today_str)
            )
            today_alerts = sum(
                1 for a in _service.recent_alerts
                if a.get("timestamp", "").startswith(today_str)
            )

        return {
            "online": True,
            "uptime_seconds": round(uptime),
            "frames_processed": _service._frames_processed,
            "alerts_sent": _service._alerts_sent,
            "cameras_online": online_count,
            "cameras_total": len(cameras),
            "detections_today": today_detections,
            "alerts_today": today_alerts,
        }

    # ──────────────────────────────────────────────
    # Cameras
    # ──────────────────────────────────────────────

    @app.get("/api/cameras")
    async def list_cameras():
        """Camera list for the local dashboard.

        v2.37.5 (2026-04-23): added `last_frame_age_seconds` and `is_live` so
        the local dashboard can distinguish a camera that is actually
        delivering frames right now from one that was configured at startup
        but has since stopped responding (S7 unplugged to charge, USB host
        down, etc.). Farm Guardian's `online` flag was set at discovery and
        never refreshed, which is why the dashboard kept showing offline
        cameras as green. `online` is preserved unchanged for any consumer
        that already relies on it; the new fields are additive. The public
        farm-2026 site does not render these fields — it continues to show
        the last snapshot as before.

        Freshness rule: a camera is `is_live=true` iff we have a captured
        frame whose timestamp is within `max(30s, 3 * snapshot_interval)`
        of now. Rationale: allow one missed cycle of slack before we call
        it dead. `snapshot_interval` is read from `config.json` per camera;
        cameras without one use a 90s default."""
        if not _service:
            return []
        cameras = _service._discovery.cameras
        active = set(_service._capture_manager.active_cameras)

        # Pull per-camera snapshot intervals from the running config so the
        # staleness threshold adapts to cadence. 60s and 5s cameras should
        # not share one threshold.
        interval_by_name: dict[str, float] = {}
        for cam_cfg in (_config.get("cameras") or []):
            nm = cam_cfg.get("name")
            if not nm:
                continue
            interval_by_name[nm] = float(
                cam_cfg.get("snapshot_interval")
                or cam_cfg.get("poll_interval")
                or 30.0
            )

        now = time.time()
        result = []
        for name, cam in cameras.items():
            last_frame = _service._capture_manager.get_latest_frame(name, allow_stale=True)
            if last_frame is not None:
                age = max(0.0, now - float(last_frame.timestamp))
            else:
                age = None
            interval = interval_by_name.get(name, 30.0)
            # Allow one missed cycle of slack, floor at 30s so 3s cameras
            # don't flap on a single dropped frame.
            stale_after = max(30.0, 3.0 * interval)
            is_live = age is not None and age <= stale_after
            result.append({
                "name": cam.name,
                "ip": cam.ip,
                "type": cam.camera_type,
                "online": cam.online,
                "capturing": name in active,
                "rtsp_url": cam.rtsp_url or "",
                "supports_motion": cam.supports_motion_events,
                "last_frame_age_seconds": (round(age, 1) if age is not None else None),
                "stale_after_seconds": round(stale_after, 1),
                "is_live": is_live,
            })
        return result

    @app.get("/api/cameras/{name}/stream")
    async def camera_stream(name: str):
        """MJPEG live stream — point an <img> tag at this endpoint."""
        if not _service:
            raise HTTPException(503, "Service not running")

        async def generate():
            last_ts = 0.0
            while True:
                frame_result = _service._capture_manager.get_latest_frame(name)
                if frame_result and frame_result.timestamp != last_ts:
                    last_ts = frame_result.timestamp
                    # Snapshot-mode cameras carry their original camera-encoded
                    # JPEG — yield it as-is for zero re-encode loss. RTSP cameras
                    # have jpeg_bytes=None so we fall back to encoding the numpy
                    # frame at quality 100.
                    if frame_result.jpeg_bytes is not None:
                        jpeg_bytes = frame_result.jpeg_bytes
                    else:
                        _, encoded = cv2.imencode(
                            ".jpg", frame_result.frame, [cv2.IMWRITE_JPEG_QUALITY, 100]
                        )
                        jpeg_bytes = encoded.tobytes()
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n"
                        + jpeg_bytes
                        + b"\r\n"
                    )
                # Poll faster than the capture rate so each new frame is yielded
                # promptly.
                await asyncio.sleep(0.1)

        return StreamingResponse(
            generate(), media_type="multipart/x-mixed-replace; boundary=frame"
        )

    @app.get("/api/cameras/{name}/frame")
    async def camera_frame(name: str, max_width: int = 0, q: int = 0,
                           allow_stale: bool = True):
        """Single latest frame as JPEG from the capture manager.

        Query params (both optional):
          - max_width: clamp image width (preserving aspect). Triggers a re-encode.
                       Use this for tunnel/remote clients — the Reolink's native 4K
                       (~1.4MB) chokes the home upstream Cloudflare tunnel.
          - q: JPEG quality 1..100 (default 85 when re-encoding). Ignored without
               max_width unless the source was numpy.
          - allow_stale: when true (the default), return the most recent cached
                         good frame even if the live RTSP buffer is currently
                         empty during a reconnect window.

        With no params and a snapshot-mode camera, the camera's original JPEG is
        returned as-is — zero re-encode, full native resolution.
        """
        if not _service:
            raise HTTPException(503, "Service not running")

        frame_result = _service._capture_manager.get_latest_frame(name, allow_stale=allow_stale)
        if not frame_result:
            raise HTTPException(404, f"No frame available for '{name}'")

        needs_resize = max_width > 0 and frame_result.original_width > max_width
        quality = q if 1 <= q <= 100 else 85

        if frame_result.jpeg_bytes is not None and not needs_resize and q == 0:
            # Pass through the camera's original JPEG — the zero-loss fast path.
            jpeg_bytes = frame_result.jpeg_bytes
        else:
            # Need to (re)encode. The numpy `frame` is already downscaled to
            # _TARGET_WIDTH for YOLO; if the caller wants something smaller, do it.
            src = frame_result.frame
            if needs_resize and src.shape[1] > max_width:
                scale = max_width / src.shape[1]
                new_w = max_width
                new_h = int(src.shape[0] * scale)
                src = cv2.resize(src, (new_w, new_h), interpolation=cv2.INTER_AREA)
            _, encoded = cv2.imencode(".jpg", src, [cv2.IMWRITE_JPEG_QUALITY, quality])
            jpeg_bytes = encoded.tobytes()

        return StreamingResponse(
            iter([jpeg_bytes]),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-cache, no-store"},
        )

    @app.post("/api/cameras/rescan")
    async def rescan_cameras():
        if not _service:
            raise HTTPException(503, "Service not running")
        cameras = _service._discovery.scan()
        online = _service._discovery.get_online_cameras()
        active = set(_service._capture_manager.active_cameras)
        # Start capture for newly-online cameras
        started = []
        for cam in online:
            if cam.name not in active and cam.rtsp_url:
                cam_cfg = _service._get_camera_config(cam.name)
                transport = cam_cfg.get("rtsp_transport") if cam_cfg else None
                _service._capture_manager.add_camera(cam.name, cam.rtsp_url, rtsp_transport=transport)
                started.append(cam.name)
        return {
            "ok": True,
            "cameras_found": len(cameras),
            "cameras_online": len(online),
            "started_capture": started,
        }

    @app.post("/api/cameras/{name}/capture/start")
    async def start_capture(name: str):
        if not _service:
            raise HTTPException(503, "Service not running")
        cam = _service._discovery.cameras.get(name)
        if not cam:
            raise HTTPException(404, f"Camera '{name}' not found")
        if not cam.rtsp_url:
            raise HTTPException(400, f"No RTSP URL for '{name}'")
        cam_cfg = _service._get_camera_config(name)
        transport = cam_cfg.get("rtsp_transport") if cam_cfg else None
        _service._capture_manager.add_camera(name, cam.rtsp_url, rtsp_transport=transport)
        return {"ok": True, "message": f"Capture started for '{name}'"}

    @app.post("/api/cameras/{name}/capture/stop")
    async def stop_capture(name: str):
        if not _service:
            raise HTTPException(503, "Service not running")
        _service._capture_manager.remove_camera(name)
        return {"ok": True, "message": f"Capture stopped for '{name}'"}

    # ──────────────────────────────────────────────
    # Detections & Events
    # ──────────────────────────────────────────────

    @app.get("/api/detections/recent")
    async def recent_detections(limit: int = 50):
        if not _service:
            return []
        items = list(_service.recent_detections)
        # If in-memory buffer is empty (fresh restart), fall back to DB
        if not items and hasattr(_service, '_db') and _service._db:
            try:
                db_rows = _service._db.get_recent_detections(minutes=120, limit=limit)
                # Normalize DB rows to match the buffer dict format
                result = []
                for r in db_rows:
                    result.append({
                        "timestamp": r.get("detected_at", ""),
                        "camera": r.get("camera_id", ""),
                        "class": r.get("class_name", ""),
                        "confidence": r.get("confidence", 0.0),
                        "bbox": [r.get("bbox_x1", 0), r.get("bbox_y1", 0),
                                 r.get("bbox_x2", 0), r.get("bbox_y2", 0)],
                        "is_predator": bool(r.get("is_predator", False)),
                        "snapshot": r.get("snapshot_path"),
                    })
                return result[:limit]
            except Exception as exc:
                log.warning("DB fallback for recent detections failed: %s", exc)
                return []
        items.reverse()  # newest first
        return items[:limit]

    @app.get("/api/events/dates")
    async def event_dates():
        """List available event dates with counts."""
        events_dir = _service._event_logger._events_dir if _service else Path("events")
        if not events_dir.exists():
            return []
        dates = []
        for entry in sorted(events_dir.iterdir(), reverse=True):
            if entry.is_dir():
                try:
                    date.fromisoformat(entry.name)
                except ValueError:
                    continue
                log_file = entry / "events.jsonl"
                count = 0
                if log_file.exists():
                    count = sum(1 for _ in open(log_file, encoding="utf-8"))
                dates.append({"date": entry.name, "count": count})
        return dates

    @app.get("/api/events/{event_date}")
    async def events_for_date(event_date: str):
        events_dir = _service._event_logger._events_dir if _service else Path("events")
        log_file = events_dir / event_date / "events.jsonl"
        if not log_file.exists():
            return []
        events = []
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        events.reverse()  # newest first
        return events

    @app.get("/api/snapshots/{event_date}/{filename}")
    async def serve_snapshot(event_date: str, filename: str):
        events_dir = _service._event_logger._events_dir if _service else Path("events")
        filepath = events_dir / event_date / filename
        if not filepath.exists() or not filepath.suffix.lower() in (".jpg", ".jpeg", ".png"):
            raise HTTPException(404, "Snapshot not found")
        return FileResponse(str(filepath), media_type="image/jpeg")

    # ──────────────────────────────────────────────
    # Alerts
    # ──────────────────────────────────────────────

    @app.get("/api/alerts/recent")
    async def recent_alerts(limit: int = 20):
        if not _service:
            return []
        items = list(_service.recent_alerts)
        items.reverse()
        return items[:limit]

    @app.post("/api/alerts/test")
    async def test_alert():
        """Send a test alert to Discord to verify webhook configuration."""
        if not _service:
            raise HTTPException(503, "Service not running")
        from detect import Detection
        test_det = Detection(
            class_name="test",
            confidence=0.99,
            bbox=(0, 0, 100, 100),
            is_predator=True,
            bbox_area_pct=5.0,
            frame_count=99,
        )
        # Bypass cooldown for test alerts
        sent = _service._alert_manager._post_webhook(
            {
                "title": "Test Alert",
                "description": "This is a test alert from Farm Guardian dashboard.\nIf you see this, your webhook is working.",
                "color": 0x00CC66,
                "timestamp": datetime.now().isoformat(),
                "footer": {"text": "Farm Guardian | Test"},
            }
        )
        return {"ok": sent, "message": "Test alert sent" if sent else "Failed — check webhook URL"}

    # ──────────────────────────────────────────────
    # Config
    # ──────────────────────────────────────────────

    @app.get("/api/config")
    async def get_config():
        """Return config with sensitive fields redacted."""
        sanitized = json.loads(json.dumps(_config))
        # Redact secrets
        for cam in sanitized.get("cameras", []):
            if cam.get("password"):
                cam["password"] = "********"
        alerts = sanitized.get("alerts", {})
        webhook = alerts.get("discord_webhook_url", "")
        if webhook and "YOUR_WEBHOOK" not in webhook:
            # Show only last 8 chars
            alerts["discord_webhook_url"] = "..." + webhook[-8:]
        return sanitized

    @app.post("/api/config/detection")
    async def update_detection_config(request: Request):
        """Update detection settings and save to config.json."""
        body = await request.json()
        detection = _config.setdefault("detection", {})

        # Update allowed fields
        allowed = {
            "confidence_threshold", "bird_min_bbox_width_pct", "min_dwell_frames",
            "alert_cooldown_seconds", "predator_classes", "ignore_classes",
            "frame_interval_seconds", "no_alert_zone", "class_confidence_thresholds",
        }
        updated = []
        for key, value in body.items():
            if key in allowed:
                detection[key] = value
                updated.append(key)

        if updated:
            _save_config()
            # Apply live-updatable settings to detector
            if _service and _service._detector:
                det = _service._detector
                if "confidence_threshold" in body:
                    det._default_confidence = body["confidence_threshold"]
                if "bird_min_bbox_width_pct" in body:
                    det._bird_min_bbox_pct = body["bird_min_bbox_width_pct"]
                if "min_dwell_frames" in body:
                    det._min_dwell_frames = body["min_dwell_frames"]
                if "predator_classes" in body:
                    det._predator_classes = set(body["predator_classes"])
                if "ignore_classes" in body:
                    det._ignore_classes = set(body["ignore_classes"])
                if "no_alert_zone" in body:
                    zone = body["no_alert_zone"]
                    det._no_alert_zone = np.array(zone, dtype=np.float32) if zone else None
                if "class_confidence_thresholds" in body:
                    det._class_thresholds = body["class_confidence_thresholds"]

        return {"ok": True, "updated": updated}

    # ──────────────────────────────────────────────
    # PTZ Controls (Phase 3)
    # ──────────────────────────────────────────────

    @app.get("/api/ptz/status")
    async def ptz_status():
        if not _service or not hasattr(_service, '_camera_ctrl'):
            return {"patrol_active": False}
        patrol_paused = _service._patrol_pause_event.is_set() if hasattr(_service, '_patrol_pause_event') else False
        return {
            "patrol_active": _service._patrol_thread is not None and _service._patrol_thread.is_alive() if hasattr(_service, '_patrol_thread') and _service._patrol_thread else False,
            "patrol_paused": patrol_paused,
        }

    @app.post("/api/ptz/{camera_name}/move")
    async def ptz_move(camera_name: str, request: Request):
        if not _service or not hasattr(_service, '_camera_ctrl'):
            raise HTTPException(503, "Camera control not available")
        body = await request.json()
        ok = _service._camera_ctrl.ptz_move(
            camera_name, pan=body.get("pan", 0), tilt=body.get("tilt", 0),
            zoom=body.get("zoom", 0), speed=body.get("speed", 25),
        )
        return {"ok": ok}

    @app.post("/api/ptz/{camera_name}/stop")
    async def ptz_stop(camera_name: str):
        if not _service or not hasattr(_service, '_camera_ctrl'):
            raise HTTPException(503, "Camera control not available")
        ok = _service._camera_ctrl.ptz_stop(camera_name)
        return {"ok": ok}

    @app.post("/api/ptz/{camera_name}/preset/{index}")
    async def ptz_goto_preset(camera_name: str, index: int):
        if not _service or not hasattr(_service, '_camera_ctrl'):
            raise HTTPException(503, "Camera control not available")
        ok = _service._camera_ctrl.ptz_goto_preset(camera_name, index)
        return {"ok": ok}

    @app.post("/api/ptz/{camera_name}/spotlight")
    async def toggle_spotlight(camera_name: str, request: Request):
        if not _service or not hasattr(_service, '_camera_ctrl'):
            raise HTTPException(503, "Camera control not available")
        body = await request.json()
        if body.get("on", True):
            ok = _service._camera_ctrl.spotlight_on(camera_name, body.get("brightness", 100))
        else:
            ok = _service._camera_ctrl.spotlight_off(camera_name)
        return {"ok": ok}

    @app.post("/api/ptz/{camera_name}/siren")
    async def trigger_siren(camera_name: str, request: Request):
        if not _service or not hasattr(_service, '_camera_ctrl'):
            raise HTTPException(503, "Camera control not available")
        body = await request.json()
        duration = body.get("duration", 10)
        ok = _service._camera_ctrl.siren_timed(camera_name, duration)
        return {"ok": ok, "duration": duration}

    # ──────────────────────────────────────────────
    # Deterrent Status (Phase 3)
    # ──────────────────────────────────────────────

    @app.get("/api/deterrent/status")
    async def deterrent_status():
        if not _service or not hasattr(_service, '_deterrent'):
            return {"active": {}, "enabled": False}
        active = _service._deterrent.active_deterrents
        return {
            "enabled": _service._deterrent._enabled,
            "active_count": len(active),
            "active": {k: {"track_id": v[0], "since": v[1]} for k, v in active.items()},
        }

    # ──────────────────────────────────────────────
    # Reports (Phase 4)
    # ──────────────────────────────────────────────

    @app.get("/api/reports/dates")
    async def report_dates():
        if not _service or not hasattr(_service, '_reports'):
            return []
        return _service._reports.get_available_dates()

    @app.get("/api/reports/{target_date}")
    async def get_report(target_date: str):
        if not _service or not hasattr(_service, '_reports'):
            raise HTTPException(503, "Reports not available")
        report = _service._reports.get_report(target_date)
        if not report:
            report = _service._reports.generate_daily_report(target_date)
        return report

    @app.post("/api/reports/generate")
    async def generate_report(request: Request):
        """Generate report on-demand. Body: {date: "YYYY-MM-DD"} or empty for today."""
        if not _service or not hasattr(_service, '_reports'):
            raise HTTPException(503, "Reports not available")
        body = await request.json() if request.headers.get("content-type") == "application/json" else {}
        target_date = body.get("date")
        report = _service._reports.generate_daily_report(target_date)
        return report

    # ──────────────────────────────────────────────
    # Tracks (Phase 2+)
    # ──────────────────────────────────────────────

    @app.get("/api/tracks/active")
    async def active_tracks():
        if not _service:
            return []
        tracks = _service._tracker.get_active_tracks()
        return [
            {
                "track_id": t.track_id,
                "camera_id": t.camera_id,
                "class_name": t.class_name,
                "is_predator": t.is_predator,
                "detection_count": t.detection_count,
                "duration_sec": round(t.duration_sec, 1),
                "max_confidence": round(t.max_confidence, 2),
            }
            for t in tracks
        ]

    @app.post("/api/config/alerts")
    async def update_alert_config(request: Request):
        body = await request.json()
        alerts = _config.setdefault("alerts", {})
        detection = _config.setdefault("detection", {})

        updated = []
        if "discord_webhook_url" in body:
            alerts["discord_webhook_url"] = body["discord_webhook_url"]
            if _service:
                _service._alert_manager._webhook_url = body["discord_webhook_url"]
            updated.append("discord_webhook_url")
        if "include_snapshot" in body:
            alerts["include_snapshot"] = body["include_snapshot"]
            if _service:
                _service._alert_manager._include_snapshot = body["include_snapshot"]
            updated.append("include_snapshot")
        if "alert_cooldown_seconds" in body:
            detection["alert_cooldown_seconds"] = body["alert_cooldown_seconds"]
            if _service:
                _service._alert_manager._cooldown_seconds = body["alert_cooldown_seconds"]
            updated.append("alert_cooldown_seconds")

        if updated:
            _save_config()
        return {"ok": True, "updated": updated}

    return app


def _save_config():
    """Write the current in-memory config back to disk."""
    try:
        with open(_config_path, "w", encoding="utf-8") as f:
            json.dump(_config, f, indent=2)
        log.info("Config saved to %s", _config_path)
    except OSError as exc:
        log.error("Failed to save config: %s", exc)


def start_dashboard(service, config: dict, config_path: str = "config.json",
                    db=None, reports=None) -> threading.Thread:
    """Start the dashboard in a daemon thread. Called from guardian.py."""
    global _service, _config, _config_path
    _service = service
    _config = config
    _config_path = config_path

    dashboard_cfg = config.get("dashboard", {})
    host = dashboard_cfg.get("host", "0.0.0.0")
    port = dashboard_cfg.get("port", 6530)

    app = create_app()

    # Register the v1 REST API for LLM tool access (Phase 4).
    # v2.25.0: pass `config` through so register_api can also mount the
    # /api/v1/images/* router with GUARDIAN_REVIEW_TOKEN + data_root.
    if db and reports:
        try:
            from api import register_api
            register_api(app, service, db, reports, config=config)
        except Exception as exc:
            log.error("Failed to register API v1: %s", exc)

    def run():
        try:
            uvicorn.run(app, host=host, port=port, log_level="warning")
        except (Exception, SystemExit) as exc:
            # Port bind failure or other uvicorn error — log and exit thread cleanly.
            # Catch SystemExit too: uvicorn raises it on bind failure.
            # Do NOT propagate; dashboard is non-critical (capture/detection continues).
            log.error("Dashboard failed to start on port %d: %s", port, exc)

    thread = threading.Thread(target=run, name="dashboard", daemon=True)
    thread.start()
    log.info("Dashboard running at http://%s:%d", host, port)
    return thread
