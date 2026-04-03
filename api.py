# Author: Claude Opus 4.6
# Date: 03-April-2026
# PURPOSE: REST API for Farm Guardian v2 (Phase 4). Provides structured JSON endpoints
#          that local LLM assistants can query for detection history, animal patterns,
#          deterrent effectiveness, and camera control. Mounted on the same FastAPI app
#          as the dashboard under /api/v1/. All endpoints return structured JSON.
#          Authentication via optional API key header for future hosted mode.
# SRP/DRY check: Pass — single responsibility is structured API for LLM tool access.

import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from database import GuardianDB
from reports import ReportGenerator

log = logging.getLogger("guardian.api")

# Module-level references — set by register_api()
_db: Optional[GuardianDB] = None
_reports: Optional[ReportGenerator] = None
_service = None  # GuardianService reference


def create_api_router() -> APIRouter:
    """Create the v1 API router with all endpoints."""
    router = APIRouter(prefix="/api/v1", tags=["v1"])

    # ------------------------------------------------------------------
    # Service status
    # ------------------------------------------------------------------

    @router.get("/status")
    async def api_status():
        """Service health + camera status."""
        if not _service:
            return {"online": False}
        import time
        uptime = time.time() - _service._start_time if _service._start_time else 0
        cameras = _service._discovery.cameras
        online_count = sum(1 for c in cameras.values() if c.online)
        return {
            "online": True,
            "uptime_seconds": round(uptime),
            "frames_processed": _service._frames_processed,
            "alerts_sent": _service._alerts_sent,
            "cameras_online": online_count,
            "cameras_total": len(cameras),
        }

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    @router.get("/summary/today")
    async def summary_today():
        """Today's summary — generates on-demand if not cached."""
        if not _reports or not _db:
            raise HTTPException(503, "Service not ready")
        today = date.today().isoformat()
        # Try loading existing report first
        report = _reports.get_report(today)
        if not report:
            report = _reports.generate_daily_report(today)
        return report

    @router.get("/summary/{target_date}")
    async def summary_for_date(target_date: str):
        """Summary for a specific date (YYYY-MM-DD)."""
        if not _reports or not _db:
            raise HTTPException(503, "Service not ready")
        try:
            date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(400, "Invalid date format — use YYYY-MM-DD")
        # Try existing report, then generate
        report = _reports.get_report(target_date)
        if not report:
            report = _reports.generate_daily_report(target_date)
        return report

    @router.get("/summary/dates")
    async def summary_dates():
        """List available report dates."""
        if not _reports:
            raise HTTPException(503, "Service not ready")
        return {"dates": _reports.get_available_dates()}

    # ------------------------------------------------------------------
    # Detections
    # ------------------------------------------------------------------

    @router.get("/detections")
    async def query_detections(
        class_name: Optional[str] = Query(None, alias="class"),
        days: int = Query(7, ge=1, le=365),
        camera_id: Optional[str] = None,
        limit: int = Query(100, ge=1, le=1000),
    ):
        """Query detections with optional filters."""
        if not _db:
            raise HTTPException(503, "Service not ready")
        detections = _db.get_recent_detections(
            camera_id=camera_id, minutes=days * 24 * 60, limit=limit,
        )
        if class_name:
            detections = [d for d in detections if d.get("class_name") == class_name]
        return {"count": len(detections), "detections": detections}

    # ------------------------------------------------------------------
    # Tracks (animal visits)
    # ------------------------------------------------------------------

    @router.get("/tracks")
    async def query_tracks(
        predator: Optional[bool] = None,
        days: int = Query(7, ge=1, le=365),
        camera_id: Optional[str] = None,
        limit: int = Query(100, ge=1, le=1000),
    ):
        """Query animal visit tracks with optional filters."""
        if not _db:
            raise HTTPException(503, "Service not ready")
        tracks = _db.get_tracks(
            camera_id=camera_id,
            predator_only=predator is True,
            days=days,
            limit=limit,
        )
        return {"count": len(tracks), "tracks": tracks}

    # ------------------------------------------------------------------
    # Species patterns
    # ------------------------------------------------------------------

    @router.get("/patterns/{class_name}")
    async def species_pattern(
        class_name: str,
        days: int = Query(30, ge=1, le=365),
    ):
        """Get activity patterns for a specific species."""
        if not _db:
            raise HTTPException(503, "Service not ready")
        pattern = _db.get_species_pattern(class_name, days=days)
        return pattern

    # ------------------------------------------------------------------
    # Deterrent effectiveness
    # ------------------------------------------------------------------

    @router.get("/deterrents/effectiveness")
    async def deterrent_effectiveness(
        days: int = Query(30, ge=1, le=365),
    ):
        """Get deterrent success rates over the given period."""
        if not _db:
            raise HTTPException(503, "Service not ready")
        stats = _db.get_deterrent_effectiveness(days=days)
        return stats

    @router.get("/deterrents/actions")
    async def deterrent_actions(
        days: int = Query(7, ge=1, le=365),
        limit: int = Query(50, ge=1, le=500),
    ):
        """List recent deterrent actions."""
        if not _db:
            raise HTTPException(503, "Service not ready")
        actions = _db.get_deterrent_actions(days=days, limit=limit)
        return {"count": len(actions), "actions": actions}

    # ------------------------------------------------------------------
    # eBird sightings
    # ------------------------------------------------------------------

    @router.get("/ebird/recent")
    async def ebird_recent(
        days: int = Query(7, ge=1, le=30),
        limit: int = Query(50, ge=1, le=200),
    ):
        """Get recent eBird raptor sightings."""
        if not _db:
            raise HTTPException(503, "Service not ready")
        sightings = _db.get_recent_ebird_sightings(days=days, limit=limit)
        return {"count": len(sightings), "sightings": sightings}

    # ------------------------------------------------------------------
    # Camera control
    # ------------------------------------------------------------------

    @router.post("/cameras/{camera_id}/ptz")
    async def camera_ptz(camera_id: str, request: Request):
        """Control PTZ position. Body: {action, preset_index, pan, tilt, zoom, speed}."""
        if not _service or not hasattr(_service, '_camera_ctrl'):
            raise HTTPException(503, "Camera control not available")
        body = await request.json()
        ctrl = _service._camera_ctrl
        action = body.get("action", "goto_preset")

        if action == "goto_preset":
            idx = body.get("preset_index", 0)
            ok = ctrl.ptz_goto_preset(camera_id, idx)
        elif action == "move":
            ok = ctrl.ptz_move(
                camera_id,
                pan=body.get("pan", 0), tilt=body.get("tilt", 0),
                zoom=body.get("zoom", 0), speed=body.get("speed", 25),
            )
        elif action == "stop":
            ok = ctrl.ptz_stop(camera_id)
        elif action == "save_preset":
            idx = body.get("preset_index", 0)
            name = body.get("name", "")
            ok = ctrl.ptz_save_preset(camera_id, idx, name)
        else:
            raise HTTPException(400, f"Unknown PTZ action: {action}")

        return {"ok": ok, "action": action}

    @router.post("/cameras/{camera_id}/spotlight")
    async def camera_spotlight(camera_id: str, request: Request):
        """Toggle spotlight. Body: {on: true/false, brightness: 0-100}."""
        if not _service or not hasattr(_service, '_camera_ctrl'):
            raise HTTPException(503, "Camera control not available")
        body = await request.json()
        ctrl = _service._camera_ctrl

        if body.get("on", True):
            brightness = body.get("brightness", 100)
            ok = ctrl.spotlight_on(camera_id, brightness)
        else:
            ok = ctrl.spotlight_off(camera_id)

        return {"ok": ok}

    @router.post("/cameras/{camera_id}/siren")
    async def camera_siren(camera_id: str, request: Request):
        """Trigger siren. Body: {duration: seconds}."""
        if not _service or not hasattr(_service, '_camera_ctrl'):
            raise HTTPException(503, "Camera control not available")
        body = await request.json()
        ctrl = _service._camera_ctrl
        duration = body.get("duration", 10)
        ok = ctrl.siren_timed(camera_id, duration)
        return {"ok": ok, "duration": duration}

    # ------------------------------------------------------------------
    # Reports export
    # ------------------------------------------------------------------

    @router.get("/export/{target_date}")
    async def export_report(target_date: str):
        """Full daily export (same as /summary/{date})."""
        if not _reports:
            raise HTTPException(503, "Service not ready")
        try:
            date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(400, "Invalid date format — use YYYY-MM-DD")
        report = _reports.get_report(target_date)
        if not report:
            report = _reports.generate_daily_report(target_date)
        return report

    return router


def register_api(app, service, db: GuardianDB, reports: ReportGenerator):
    """Register the v1 API router on the FastAPI app and set module references."""
    global _db, _reports, _service
    _db = db
    _reports = reports
    _service = service
    router = create_api_router()
    app.include_router(router)
    log.info("API v1 registered — %d endpoints", len(router.routes))
