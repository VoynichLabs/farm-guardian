# Author: Claude Opus 4.6 (updated), Cascade (Claude Sonnet 4) (original)
# Date: 03-April-2026
# PURPOSE: Local web dashboard for Farm Guardian. Serves a FastAPI app on the Mac Mini
#          that provides real-time monitoring and full control of the guardian service.
#          Features: live MJPEG camera feeds, detection timeline, alert history, PTZ
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

        # Count today's detections from buffer
        today_str = date.today().isoformat()
        today_detections = sum(
            1 for d in _service.recent_detections if d.get("timestamp", "").startswith(today_str)
        )
        today_alerts = sum(
            1 for a in _service.recent_alerts if a.get("timestamp", "").startswith(today_str)
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
        if not _service:
            return []
        cameras = _service._discovery.cameras
        active = set(_service._capture_manager.active_cameras)
        result = []
        for name, cam in cameras.items():
            result.append({
                "name": cam.name,
                "ip": cam.ip,
                "type": cam.camera_type,
                "online": cam.online,
                "capturing": name in active,
                "rtsp_url": cam.rtsp_url or "",
                "supports_motion": cam.supports_motion_events,
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
                    _, jpeg = cv2.imencode(
                        ".jpg", frame_result.frame, [cv2.IMWRITE_JPEG_QUALITY, 70]
                    )
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n"
                        + jpeg.tobytes()
                        + b"\r\n"
                    )
                await asyncio.sleep(0.3)

        return StreamingResponse(
            generate(), media_type="multipart/x-mixed-replace; boundary=frame"
        )

    @app.get("/api/cameras/{name}/frame")
    async def camera_frame(name: str):
        """Single latest frame as JPEG."""
        if not _service:
            raise HTTPException(503, "Service not running")
        frame_result = _service._capture_manager.get_latest_frame(name)
        if not frame_result:
            raise HTTPException(404, f"No frame available for '{name}'")
        _, jpeg = cv2.imencode(".jpg", frame_result.frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return StreamingResponse(
            iter([jpeg.tobytes()]), media_type="image/jpeg"
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
                _service._capture_manager.add_camera(cam.name, cam.rtsp_url)
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
        _service._capture_manager.add_camera(name, cam.rtsp_url)
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

    # Register the v1 REST API for LLM tool access (Phase 4)
    if db and reports:
        try:
            from api import register_api
            register_api(app, service, db, reports)
        except Exception as exc:
            log.error("Failed to register API v1: %s", exc)

    def run():
        uvicorn.run(app, host=host, port=port, log_level="warning")

    thread = threading.Thread(target=run, name="dashboard", daemon=True)
    thread.start()
    log.info("Dashboard running at http://%s:%d", host, port)
    return thread
