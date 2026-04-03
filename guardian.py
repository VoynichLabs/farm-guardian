# Author: Claude Opus 4.6 (updated), Cascade (Claude Sonnet 4) (original)
# Date: 03-April-2026
# PURPOSE: Main service entry point for Farm Guardian v2 (Phases 1-4). Orchestrates camera
#          discovery, frame capture, YOLO animal detection, GLM vision refinement, animal
#          visit tracking, automated deterrence (spotlight/siren/audio), PTZ patrol with
#          pause-on-predator, eBird raptor early warning, Discord alerting, event logging
#          (DB + JSONL), daily intelligence reports, REST API for LLM tools, and the local
#          web dashboard. Runs as a foreground process on the Mac Mini. Handles graceful
#          shutdown via SIGINT/SIGTERM.
# SRP/DRY check: Pass — single responsibility is service lifecycle and module coordination.

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from discovery import CameraDiscovery
from capture import FrameCaptureManager, FrameResult
from detect import AnimalDetector, DetectionResult
from alerts import AlertManager
from logger import EventLogger
from database import GuardianDB
from vision import VisionRefiner
from tracker import AnimalTracker
from camera_control import CameraController
from deterrent import DeterrentEngine
from ebird import EBirdWatcher
from reports import ReportGenerator
from dashboard import start_dashboard

log = logging.getLogger("guardian")

# Vision-refined class → predator mapping. YOLO detects generic "bird"/"cat"/"dog",
# but the vision model refines to specific species. These sets determine whether
# the refined class is a threat or safe.
_REFINED_PREDATORS = {"hawk", "bobcat", "coyote", "fox", "wild_cat", "other_canine", "other_bird"}
_REFINED_SAFE = {"chicken", "small_bird", "small_dog", "house_cat"}

# Default config file path
_CONFIG_PATH = "config.json"

# How often to re-scan for cameras (seconds)
_RESCAN_INTERVAL = 300

# How often to run event cleanup (seconds) — once per hour
_CLEANUP_INTERVAL = 3600


class GuardianService:
    """Main service that wires together all Farm Guardian modules."""

    def __init__(self, config: dict, config_path: str = "config.json"):
        self._config = config
        self._config_path = config_path
        self._shutdown_event = threading.Event()

        # Initialize modules
        log.info("Initializing Farm Guardian v2 modules (Phases 1-4)...")

        # Database — foundation for v2 (all other modules may write to it)
        self._db = GuardianDB(config)

        # Phase 1: Core pipeline
        self._discovery = CameraDiscovery(config)
        self._detector = AnimalDetector(config)
        self._alert_manager = AlertManager(config)
        self._event_logger = EventLogger(config, db=self._db)

        # Phase 2: Vision + Tracking
        self._vision = VisionRefiner(config)
        self._tracker = AnimalTracker(config, db=self._db)

        # Phase 3: Camera control + Deterrence
        self._camera_ctrl = CameraController(config)
        self._patrol_pause_event = threading.Event()  # set = patrol paused
        self._deterrent = DeterrentEngine(
            config, self._camera_ctrl, self._db,
            patrol_pause_event=self._patrol_pause_event,
        )

        # Phase 3: eBird early warning
        self._ebird = EBirdWatcher(config, self._db, self._alert_manager)

        # Phase 4: Reports + API
        self._reports = ReportGenerator(config, self._db)

        # Capture manager (last — depends on other modules via callback)
        self._capture_manager = FrameCaptureManager(config, on_frame=self._on_frame)

        # Background task threads
        self._rescan_thread: threading.Thread | None = None
        self._cleanup_thread: threading.Thread | None = None
        self._patrol_thread: threading.Thread | None = None
        self._ebird_thread: threading.Thread | None = None

        # Stats tracking
        self._frames_processed = 0
        self._alerts_sent = 0
        self._deterrents_fired = 0
        self._start_time: float | None = None

        # Buffers for dashboard access — thread-safe deques
        self.recent_detections: deque[dict] = deque(maxlen=200)
        self.recent_alerts: deque[dict] = deque(maxlen=100)

    def start(self) -> None:
        """Start the guardian service: discover cameras, begin capture, run detection."""
        self._start_time = time.time()
        log.info("=" * 60)
        log.info("Farm Guardian starting up")
        log.info("=" * 60)

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Initial camera scan
        cameras = self._discovery.scan()
        online_cameras = self._discovery.get_online_cameras()

        if not online_cameras:
            log.warning(
                "No cameras online. Guardian will keep running and re-scan every %ds. "
                "Check config.json camera settings and network connectivity.",
                _RESCAN_INTERVAL,
            )
        else:
            # Register cameras in DB and start capturing
            for cam in online_cameras:
                # Register in database for v2 tracking
                cam_cfg = self._get_camera_config(cam.name)
                self._db.get_or_create_camera(
                    camera_id=cam.name,
                    name=cam.name,
                    model=cam_cfg.get("model", "unknown") if cam_cfg else "unknown",
                    ip=cam.ip if hasattr(cam, "ip") else None,
                    rtsp_url=cam.rtsp_url,
                    cam_type=cam_cfg.get("type", "ptz") if cam_cfg else "ptz",
                )

                if cam.rtsp_url:
                    self._capture_manager.add_camera(cam.name, cam.rtsp_url)
                else:
                    log.warning(
                        "Camera '%s' online but no RTSP URL resolved — skipping capture", cam.name
                    )

        # Connect camera hardware control (Phase 3)
        for cam in online_cameras:
            cam_cfg = self._get_camera_config(cam.name)
            if cam_cfg and cam_cfg.get("type") == "ptz":
                self._camera_ctrl.connect_camera(
                    camera_id=cam.name,
                    ip=cam_cfg.get("ip", ""),
                    username=cam_cfg.get("username", "admin"),
                    password=cam_cfg.get("password", ""),
                    port=cam_cfg.get("port", 80),
                )

        # Start background tasks
        self._rescan_thread = threading.Thread(
            target=self._rescan_loop, name="rescan", daemon=True
        )
        self._rescan_thread.start()

        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, name="cleanup", daemon=True
        )
        self._cleanup_thread.start()

        # Start PTZ patrol (Phase 3) — only for first PTZ camera
        ptz_cfg = self._config.get("ptz", {})
        if ptz_cfg.get("patrol_enabled", False) and online_cameras:
            first_ptz = next(
                (c for c in online_cameras
                 if self._get_camera_config(c.name) and
                    self._get_camera_config(c.name).get("type") == "ptz"),
                None
            )
            if first_ptz:
                presets = ptz_cfg.get("presets", [])
                # Convert config presets to patrol format
                patrol_presets = [
                    {"index": i, "name": p["name"], "dwell": p.get("dwell", 30)}
                    for i, p in enumerate(presets) if p.get("patrol", True)
                ]
                if patrol_presets:
                    self._patrol_thread = threading.Thread(
                        target=self._camera_ctrl.start_patrol,
                        args=(first_ptz.name, patrol_presets),
                        kwargs={
                            "shutdown_event": self._shutdown_event,
                            "pause_event": self._patrol_pause_event,
                        },
                        name="patrol", daemon=True,
                    )
                    self._patrol_thread.start()
                    log.info("PTZ patrol started for '%s' — %d presets", first_ptz.name, len(patrol_presets))

        # Start eBird polling (Phase 3)
        self._ebird_thread = threading.Thread(
            target=self._ebird.run_poll_loop,
            args=(self._shutdown_event,),
            name="ebird", daemon=True,
        )
        self._ebird_thread.start()

        # Start web dashboard + API
        dashboard_cfg = self._config.get("dashboard", {})
        if dashboard_cfg.get("enabled", True):
            self._dashboard_thread = start_dashboard(
                self, self._config, config_path=self._config_path,
                db=self._db, reports=self._reports,
            )
            port = dashboard_cfg.get("port", 6530)
            log.info("Dashboard + API available at http://localhost:%d", port)

        active = self._capture_manager.active_cameras
        log.info(
            "Guardian running — %d camera(s) active: %s",
            len(active),
            ", ".join(active) if active else "(none)",
        )
        log.info("Press Ctrl+C to stop.")

        # Block main thread until shutdown
        try:
            while not self._shutdown_event.is_set():
                self._shutdown_event.wait(timeout=1.0)
        except KeyboardInterrupt:
            pass

        self.stop()

    def stop(self) -> None:
        """Gracefully shut down all modules."""
        log.info("Shutting down Farm Guardian...")
        self._shutdown_event.set()

        # Unpause patrol so patrol thread can exit
        self._patrol_pause_event.clear()

        self._capture_manager.stop_all()
        self._tracker.close_all()
        self._camera_ctrl.close()

        # Generate end-of-day report before closing DB
        try:
            self._reports.generate_daily_report()
            log.info("End-of-day report generated")
        except Exception as exc:
            log.error("Failed to generate shutdown report: %s", exc)

        self._db.close()

        uptime = time.time() - self._start_time if self._start_time else 0
        log.info(
            "Guardian stopped — uptime: %.0fs, frames: %d, alerts: %d, deterrents: %d",
            uptime, self._frames_processed, self._alerts_sent, self._deterrents_fired,
        )

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle SIGINT/SIGTERM for graceful shutdown."""
        sig_name = signal.Signals(signum).name
        log.info("Received %s — initiating graceful shutdown", sig_name)
        self._shutdown_event.set()

    def _on_frame(self, frame_result: FrameResult) -> None:
        """
        Callback invoked by capture threads for each new frame. Runs detection,
        optionally refines species via vision model, tracks animal visits,
        logs events, and sends alerts if predators are found. This runs on the
        capture thread — keep it fast.
        """
        try:
            result = self._detector.detect(frame_result.frame, frame_result.camera_name)
            self._frames_processed += 1

            if not result.detections:
                return

            for det in result.detections:
                # -- v2: Vision refinement for ambiguous classes --
                model_name = "yolov8n"
                if self._vision.should_refine(det.class_name):
                    # Get existing track to check cache, or pass None for new
                    existing_track = self._tracker.get_track_for_detection(
                        result.camera_name, det.class_name
                    )
                    existing_track_id = existing_track.track_id if existing_track else None

                    refined_class, refined_conf, model_name = self._vision.refine(
                        class_name=det.class_name,
                        frame=result.frame,
                        bbox=det.bbox,
                        track_id=existing_track_id,
                    )
                    # Update detection with refined class
                    if refined_class != det.class_name:
                        det.class_name = refined_class
                        if refined_class in _REFINED_PREDATORS:
                            det.is_predator = True
                        elif refined_class in _REFINED_SAFE:
                            det.is_predator = False
                        # else: keep original YOLO predator status

                # -- v2: Track this detection as part of an animal visit --
                track = self._tracker.process_detection(
                    camera_id=result.camera_name,
                    detection=det,
                )
                track_id = track.track_id if track else None

                # -- Log to JSONL + DB --
                event = self._event_logger.log_event(
                    camera_name=result.camera_name,
                    detection_class=det.class_name,
                    confidence=det.confidence,
                    bbox=det.bbox,
                    frame=result.frame,
                    bbox_area_pct=det.bbox_area_pct,
                    is_predator=det.is_predator,
                    track_id=track_id,
                    model_name=model_name,
                )
                self.recent_detections.append(event)

            # Send alert and fire deterrents if predator detections passed all filters
            if result.has_predators:
                sent = self._alert_manager.send_alert(
                    camera_name=result.camera_name,
                    detections=result.predator_detections,
                    frame=result.frame,
                )
                if sent:
                    self._alerts_sent += 1
                    self.recent_alerts.append({
                        "timestamp": datetime.now().isoformat(),
                        "camera": result.camera_name,
                        "classes": [d.class_name for d in result.predator_detections],
                        "sent": True,
                    })

                # Phase 3: Fire deterrents for predator tracks
                for det in result.predator_detections:
                    track = self._tracker.get_track_for_detection(
                        result.camera_name, det.class_name
                    )
                    if track and track.is_predator:
                        actions = self._deterrent.evaluate(track, result.camera_name)
                        if actions:
                            self._deterrents_fired += 1
                            # Update track outcome via tracker
                            self._tracker.set_track_outcome(
                                track.track_id,
                                outcome="deterred",
                                deterrent_used=actions,
                            )

        except Exception as exc:
            log.error(
                "Error processing frame from '%s': %s",
                frame_result.camera_name,
                exc,
                exc_info=True,
            )

    def _get_camera_config(self, camera_name: str) -> Optional[dict]:
        """Find the config dict for a camera by name."""
        for cam_cfg in self._config.get("cameras", []):
            if cam_cfg.get("name") == camera_name:
                return cam_cfg
        return None

    def _rescan_loop(self) -> None:
        """Periodically re-scan for cameras that may have reconnected."""
        rescan_interval = self._config.get("discovery", {}).get(
            "rescan_interval_seconds", _RESCAN_INTERVAL
        )

        while not self._shutdown_event.is_set():
            self._shutdown_event.wait(rescan_interval)
            if self._shutdown_event.is_set():
                break

            log.info("Running periodic camera re-scan...")
            try:
                self._discovery.scan()
                online = self._discovery.get_online_cameras()
                active = set(self._capture_manager.active_cameras)

                # Start capture for newly-online cameras
                for cam in online:
                    if cam.name not in active and cam.rtsp_url:
                        log.info("New/reconnected camera '%s' — starting capture", cam.name)
                        self._capture_manager.add_camera(cam.name, cam.rtsp_url)

            except Exception as exc:
                log.error("Camera re-scan failed: %s", exc)

    def _cleanup_loop(self) -> None:
        """Periodically clean up old event directories and back up the database."""
        while not self._shutdown_event.is_set():
            self._shutdown_event.wait(_CLEANUP_INTERVAL)
            if self._shutdown_event.is_set():
                break

            try:
                removed = self._event_logger.cleanup_old_events()
                if removed:
                    log.info("Cleaned up %d old event directories", removed)
            except Exception as exc:
                log.error("Event cleanup failed: %s", exc)

            # Daily database backup
            db_cfg = self._config.get("database", {})
            if db_cfg.get("backup_daily", True):
                try:
                    self._db.backup()
                    self._db.cleanup_old_backups()
                except Exception as exc:
                    log.error("Database backup failed: %s", exc)

            # Phase 4: Generate daily report (run once per day at end of day)
            now = datetime.now()
            report_time = self._config.get("reports", {}).get("daily_summary_time", "23:59")
            try:
                rh, rm = int(report_time.split(":")[0]), int(report_time.split(":")[1])
                if now.hour == rh and now.minute >= rm:
                    self._reports.generate_daily_report()
            except (ValueError, IndexError):
                pass


def load_config(config_path: str) -> dict:
    """Load and validate the JSON config file."""
    path = Path(config_path)
    if not path.exists():
        log.error(
            "Config file not found: %s — copy config.example.json to config.json and edit it.",
            config_path,
        )
        sys.exit(1)

    try:
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as exc:
        log.error("Invalid JSON in config file %s: %s", config_path, exc)
        sys.exit(1)

    # Basic validation
    if not config.get("cameras"):
        log.warning("No cameras configured in %s — guardian will wait for cameras", config_path)

    webhook_url = config.get("alerts", {}).get("discord_webhook_url", "")
    if not webhook_url or "YOUR_WEBHOOK" in webhook_url:
        log.warning("Discord webhook not configured — alerts will be logged but not sent")

    return config


def setup_logging(config: dict, debug: bool = False) -> None:
    """Configure logging from config settings."""
    log_cfg = config.get("logging", {})
    level_str = "DEBUG" if debug else log_cfg.get("level", "INFO")
    level = getattr(logging, level_str.upper(), logging.INFO)

    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    # Optional file logging
    log_file = log_cfg.get("file")
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format=log_format,
        datefmt=date_format,
        handlers=handlers,
    )

    # Suppress noisy third-party loggers
    logging.getLogger("ultralytics").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Farm Guardian — intelligent farm security camera monitoring"
    )
    parser.add_argument(
        "--config",
        default=_CONFIG_PATH,
        help="Path to config.json (default: config.json)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug-level logging",
    )
    args = parser.parse_args()

    # Load config first (basic logging for config errors)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config(args.config)

    # Now set up full logging from config
    setup_logging(config, debug=args.debug)

    log.info("Config loaded from %s", args.config)

    # Start the service
    service = GuardianService(config, config_path=args.config)
    service.start()


if __name__ == "__main__":
    main()
