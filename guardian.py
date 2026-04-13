# Author: Claude Opus 4.6 (updated), OpenAI Codex GPT-5.4 Mini (prior)
# Date: 13-April-2026 (v2.19.0 — Phase C1: usb-cam switches to high-quality snapshot polling)
# PURPOSE: Main service entry point for Farm Guardian v2. Orchestrates camera discovery,
#          frame capture, YOLO animal detection, animal visit tracking (for alert dedup),
#          automated deterrence (spotlight/siren/audio), PTZ patrol with pause-on-predator,
#          eBird raptor early warning, Discord alerting, event logging (DB + JSONL), daily
#          intelligence reports, REST API for LLM tools, and the local web dashboard. Runs
#          as a foreground process on the Mac Mini. Handles graceful shutdown via
#          SIGINT/SIGTERM. Supports sky-watch mode: parks camera at a fixed preset on
#          startup for optimal hawk detection coverage. Detection is gated to a configurable
#          night window (default 20:00–09:00 America/New_York). v2.17.0: removed the GLM
#          vision species refinement entirely — YOLO's class label is what flows through to
#          the alert. Boss directive: "just show me the picture, no classification."
# SRP/DRY check: Pass — reviewed the detection flow when removing the vision refinement.

import os
# MUST be set before any cv2 import anywhere in the process — sets a 5-second
# stream timeout (prevents 30-second hangs on stream loss). Per-camera RTSP
# transport (TCP/UDP) is set in capture.py before each VideoCapture() call,
# because the Reolink needs TCP and the S7 needs UDP.
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "stimeout;5000000"

import argparse
import json
import logging
import signal
import sys
import threading
import time

from dotenv import load_dotenv
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from discovery import CameraDiscovery
from capture import FrameCaptureManager, FrameResult, ReolinkSnapshotSource, UsbSnapshotSource
from detect import AnimalDetector, DetectionResult
from alerts import AlertManager
from logger import EventLogger
from database import GuardianDB
from tracker import AnimalTracker
from camera_control import CameraController
from deterrent import DeterrentEngine
from patrol import SweepPatrol
from ebird import EBirdWatcher
from reports import ReportGenerator
from dashboard import start_dashboard

log = logging.getLogger("guardian")

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
        self._detector = None  # Deferred to start() — YOLO import is slow (~60s PyTorch load)
        # CameraController created early — AlertManager needs it for 4K HTTP snapshots
        self._camera_ctrl = CameraController(config)
        self._alert_manager = AlertManager(config, camera_controller=self._camera_ctrl)
        self._event_logger = EventLogger(config, db=self._db)

        # Phase 2: Tracking (vision refinement removed v2.17.0 — Boss decision: just
        # YOLO detection at night; if it sees something interesting, send the picture.
        # No species classification needed for this farm's volume.)
        self._tracker = AnimalTracker(config, db=self._db)

        # Phase 3: Deterrence (camera_ctrl already created above)
        self._patrol_pause_event = threading.Event()  # set = patrol paused
        self._deterrent = DeterrentEngine(
            config, self._camera_ctrl, self._db,
            patrol_pause_event=self._patrol_pause_event,
        )

        # Phase 3: eBird early warning
        self._ebird = EBirdWatcher(config, self._db, self._alert_manager)

        # Phase 4: Reports + API
        self._reports = ReportGenerator(config, self._db)

        # Capture manager (last — depends on other modules via callback).
        # Handles ALL cameras: detection cameras at ~1fps, non-detection cameras at
        # configurable snapshot_interval (default 10s) for lightweight polling.
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

        detection_cfg = self._config.get("detection", {})
        if detection_cfg.get("night_window_enabled", True):
            log.info(
                "Detection window for enabled cameras: %s-%s %s",
                detection_cfg.get("night_window_start", "20:00"),
                detection_cfg.get("night_window_end", "09:00"),
                detection_cfg.get("night_window_timezone", "America/New_York"),
            )

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        # Start web dashboard + API FIRST — so the API is available during
        # YOLO model loading and camera discovery (both can take 30-60s)
        dashboard_cfg = self._config.get("dashboard", {})
        if dashboard_cfg.get("enabled", True):
            self._dashboard_thread = start_dashboard(
                self, self._config, config_path=self._config_path,
                db=self._db, reports=self._reports,
            )
            port = dashboard_cfg.get("port", 6530)
            log.info("Dashboard + API available at http://localhost:%d", port)

        # Load YOLO detector in background thread — the ultralytics import pulls
        # in PyTorch (~60s on cold start). Camera feeds and dashboard work immediately;
        # detection kicks in once the model finishes loading. The null guard in
        # _on_frame() silently skips frames until self._detector is set.
        def _load_detector():
            try:
                log.info("Loading YOLO detector in background (this may take a minute)...")
                self._detector = AnimalDetector(self._config)
                log.info("YOLO detector ready — detection active")
            except Exception as exc:
                log.error("Failed to load YOLO detector: %s — detection disabled", exc)

        threading.Thread(target=_load_detector, name="yolo-load", daemon=True).start()

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
            # Register PTZ cameras with the hardware controller FIRST so the
            # snapshot poller (Phase A+) can call take_snapshot on the first tick
            # without an authentication race. RTSP/USB cameras don't need this
            # but it's harmless to do for everyone in this loop.
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

            # Register cameras in DB and start capturing
            for cam in online_cameras:
                cam_cfg = self._get_camera_config(cam.name)
                self._db.get_or_create_camera(
                    camera_id=cam.name,
                    name=cam.name,
                    model=cam_cfg.get("model", "unknown") if cam_cfg else "unknown",
                    ip=cam.ip if hasattr(cam, "ip") else None,
                    rtsp_url=cam.rtsp_url,
                    cam_type=cam_cfg.get("type", "ptz") if cam_cfg else "ptz",
                )
                self._register_camera_capture(cam, cam_cfg)

        # Sky-watch mode: park camera at a fixed preset on startup.
        # Used instead of patrol — camera stays in one position optimized for
        # yard + sky coverage to catch hawks before they dive.
        sky_watch_cfg = self._config.get("sky_watch", {})
        if sky_watch_cfg.get("enabled", False):
            sw_camera = sky_watch_cfg.get("camera", "")
            sw_preset = sky_watch_cfg.get("preset_id", 0)
            if sw_camera and self._camera_ctrl._get_host(sw_camera):
                log.info(
                    "Sky-watch mode: moving '%s' to preset %d and holding position",
                    sw_camera, sw_preset,
                )
                self._camera_ctrl.ptz_goto_preset(sw_camera, sw_preset)
                # Wait for camera to reach position + autofocus settle
                time.sleep(3)
                self._camera_ctrl.trigger_autofocus(sw_camera)
            else:
                log.warning(
                    "Sky-watch enabled but camera '%s' not connected — skipping",
                    sw_camera,
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
                patrol_mode = ptz_cfg.get("patrol_mode", "sweep")

                if patrol_mode == "sweep":
                    # Continuous sweep patrol — camera scans everything it can see
                    self._sweep_patrol = SweepPatrol(
                        self._camera_ctrl, first_ptz.name, self._config
                    )
                    self._patrol_thread = threading.Thread(
                        target=self._sweep_patrol.run,
                        args=(self._shutdown_event,),
                        kwargs={"pause_event": self._patrol_pause_event},
                        name="patrol-sweep", daemon=True,
                    )
                    self._patrol_thread.start()
                    log.info("Sweep patrol started for '%s'", first_ptz.name)

                elif patrol_mode == "preset":
                    # Legacy preset-hopping patrol
                    presets = ptz_cfg.get("presets", [])
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
                            name="patrol-preset", daemon=True,
                        )
                        self._patrol_thread.start()
                        log.info("Preset patrol started for '%s' — %d presets",
                                 first_ptz.name, len(patrol_presets))
                else:
                    log.warning("Unknown patrol_mode '%s' — patrol disabled", patrol_mode)

        # Start eBird polling (Phase 3)
        self._ebird_thread = threading.Thread(
            target=self._ebird.run_poll_loop,
            args=(self._shutdown_event,),
            name="ebird", daemon=True,
        )
        self._ebird_thread.start()

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

    def _register_camera_capture(self, cam, cam_cfg: Optional[dict]) -> bool:
        """Route a discovered camera to the right capture/poller mode and add it
        to the capture manager. Returns True if a capture was started.

        Acquisition modes (in priority order):
          1. snapshot mode — `cam_cfg["source"] == "snapshot"`. Builds a
             SnapshotSource based on `snapshot_method` and dispatches to
             CameraSnapshotPoller. Currently only `snapshot_method: "reolink"`
             is implemented (Phase A); Phase B adds "http_url", Phase C adds
             "usb".
          2. USB mode — discovery says `cam.source == "usb"`.
          3. RTSP mode — `cam.rtsp_url` was discovered/overridden.

        Centralized so the initial setup loop and the periodic re-scan loop
        agree on the dispatch logic. Otherwise both sites tend to drift.
        """
        if cam_cfg is None:
            cam_cfg = {}

        source_kind = cam_cfg.get("source", "")
        if source_kind == "snapshot":
            method = cam_cfg.get("snapshot_method", "reolink")
            if method == "reolink":
                snap_src = ReolinkSnapshotSource(self._camera_ctrl, cam.name)
            elif method == "usb":
                device_index = cam_cfg.get("device_index", 0)
                target_res = cam_cfg.get("snapshot_resolution")
                target_tuple = tuple(target_res) if target_res else None
                jpeg_quality = cam_cfg.get("snapshot_jpeg_quality", 95)
                snap_src = UsbSnapshotSource(
                    device_index=device_index,
                    target_resolution=target_tuple,
                    jpeg_quality=jpeg_quality,
                    label=f"usb:{cam.name}",
                )
            else:
                log.error(
                    "Camera '%s' has snapshot_method=%r — not implemented (Phase B adds http_url); skipping",
                    cam.name, method,
                )
                return False
            self._capture_manager.add_camera(
                cam.name,
                snapshot_source=snap_src,
                snapshot_interval=cam_cfg.get("snapshot_interval", 5.0),
                night_snapshot_interval=cam_cfg.get("night_snapshot_interval"),
                is_night_window=self._detection_window_open,
            )
            log.info(
                "Camera '%s' registered in snapshot mode (method=%s)", cam.name, method,
            )
            return True

        # Legacy RTSP / USB paths — unchanged from v2.17.0
        detection_on = cam_cfg.get("detection_enabled", True)
        snapshot_interval = cam_cfg.get("snapshot_interval", 10.0)
        transport = cam_cfg.get("rtsp_transport")
        # Non-detection RTSP/USB cameras poll at snapshot_interval (default 10s);
        # detection cameras use the global frame_interval_seconds.
        interval_override = None if detection_on else snapshot_interval

        if cam.source == "usb" and cam.device_index is not None:
            self._capture_manager.add_camera(
                cam.name, device_index=cam.device_index,
                frame_interval=interval_override,
            )
            return True
        if cam.rtsp_url:
            self._capture_manager.add_camera(
                cam.name, cam.rtsp_url, rtsp_transport=transport,
                frame_interval=interval_override,
            )
            return True

        log.warning(
            "Camera '%s' online but has no snapshot/USB/RTSP source — skipping", cam.name,
        )
        return False

    def _on_frame(self, frame_result: FrameResult) -> None:
        """
        Callback invoked by capture threads for each new frame. Runs YOLO detection,
        tracks animal visits (for alert dedup), logs events, and sends alerts if
        predators are found. This runs on the capture thread — keep it fast.
        """
        if self._detector is None:
            return  # Detector still loading — skip frame

        # Skip detection for cameras with detection disabled in config.
        cam_cfg = self._get_camera_config(frame_result.camera_name)
        if cam_cfg and not cam_cfg.get("detection_enabled", True):
            return

        # Enabled cameras still respect the global night window.
        if not self._detection_window_open():
            return

        try:
            result = self._detector.detect(frame_result.frame, frame_result.camera_name)
            self._frames_processed += 1

            if not result.detections:
                return

            for det in result.detections:
                # Track this detection as part of an animal visit (used by alert
                # dedup — one Discord post per visit, not one per frame).
                track = self._tracker.process_detection(
                    camera_id=result.camera_name,
                    detection=det,
                )
                track_id = track.track_id if track else None

                # Log to JSONL + DB
                event = self._event_logger.log_event(
                    camera_name=result.camera_name,
                    detection_class=det.class_name,
                    confidence=det.confidence,
                    bbox=det.bbox,
                    frame=result.frame,
                    bbox_area_pct=det.bbox_area_pct,
                    is_predator=det.is_predator,
                    track_id=track_id,
                    model_name="yolov8n",
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

    @staticmethod
    def _clock_to_minutes(clock: str) -> int:
        """Convert HH:MM clock strings to minutes since midnight."""
        hour, minute = (int(part) for part in clock.split(":"))
        return hour * 60 + minute

    @classmethod
    def _window_allows_minutes(cls, current_minutes: int, start_clock: str, end_clock: str) -> bool:
        """Return True when current_minutes falls inside the configured window."""
        start_minutes = cls._clock_to_minutes(start_clock)
        end_minutes = cls._clock_to_minutes(end_clock)

        # Same start/end means the gate is effectively open all day.
        if start_minutes == end_minutes:
            return True

        # Normal daytime window.
        if start_minutes < end_minutes:
            return start_minutes <= current_minutes < end_minutes

        # Overnight window crossing midnight.
        return current_minutes >= start_minutes or current_minutes < end_minutes

    def _detection_window_open(self) -> bool:
        """Check whether the current time is inside the configured night window."""
        detection_cfg = self._config.get("detection", {})
        if not detection_cfg.get("night_window_enabled", True):
            return True

        timezone_name = detection_cfg.get("night_window_timezone", "America/New_York")
        try:
            now = datetime.now(ZoneInfo(timezone_name))
        except Exception:
            log.warning(
                "Invalid night window timezone '%s' — falling back to system local time",
                timezone_name,
            )
            now = datetime.now()

        current_minutes = now.hour * 60 + now.minute
        return self._window_allows_minutes(
            current_minutes=current_minutes,
            start_clock=detection_cfg.get("night_window_start", "20:00"),
            end_clock=detection_cfg.get("night_window_end", "09:00"),
        )

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
                    if cam.name not in active:
                        cam_cfg = self._get_camera_config(cam.name)

                        # Connect PTZ hardware FIRST — snapshot mode needs the
                        # authenticated controller before its first take_snapshot.
                        if cam_cfg and cam_cfg.get("type") == "ptz":
                            self._camera_ctrl.connect_camera(
                                camera_id=cam.name,
                                ip=cam_cfg.get("ip", ""),
                                username=cam_cfg.get("username", "admin"),
                                password=cam_cfg.get("password", ""),
                                port=cam_cfg.get("port", 80),
                            )

                        log.info("New/reconnected camera '%s' — starting capture", cam.name)
                        self._register_camera_capture(cam, cam_cfg)

                # Start patrol if a PTZ camera is online but patrol isn't running
                patrol_alive = (
                    hasattr(self, '_patrol_thread')
                    and self._patrol_thread is not None
                    and self._patrol_thread.is_alive()
                )
                if not patrol_alive:
                    ptz_cfg = self._config.get("ptz", {})
                    if ptz_cfg.get("patrol_enabled", False):
                        first_ptz = next(
                            (c for c in online
                             if self._get_camera_config(c.name)
                             and self._get_camera_config(c.name).get("type") == "ptz"),
                            None
                        )
                        if first_ptz:
                            patrol_mode = ptz_cfg.get("patrol_mode", "sweep")
                            if patrol_mode == "sweep":
                                self._sweep_patrol = SweepPatrol(
                                    self._camera_ctrl, first_ptz.name, self._config
                                )
                                self._patrol_thread = threading.Thread(
                                    target=self._sweep_patrol.run,
                                    args=(self._shutdown_event,),
                                    kwargs={"pause_event": self._patrol_pause_event},
                                    name="patrol-sweep", daemon=True,
                                )
                                self._patrol_thread.start()
                                log.info("Sweep patrol started for '%s' (via rescan)", first_ptz.name)

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

    # Overlay secrets from environment variables (.env or system env).
    # Env vars take precedence over config.json values, and they replace
    # placeholder strings so config.json can stay sanitized in git.
    env_camera_pw = os.environ.get("CAMERA_PASSWORD")
    if env_camera_pw:
        for cam in config.get("cameras", []):
            if not cam.get("password") or "YOUR_" in cam.get("password", ""):
                cam["password"] = env_camera_pw

    env_webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if env_webhook:
        config.setdefault("alerts", {})["discord_webhook_url"] = env_webhook

    env_ebird_key = os.environ.get("EBIRD_API_KEY")
    if env_ebird_key:
        config.setdefault("ebird", {})["api_key"] = env_ebird_key

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
        force=True,  # Replace bootstrap handler from main() — without this,
                     # basicConfig is a no-op and DEBUG/file logging never activates
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

    # Load .env file (secrets), then config
    load_dotenv()
    # Temporary bootstrap logger for config loading messages only.
    # setup_logging() replaces this with the real config-driven setup using
    # force=True so basicConfig doesn't silently no-op.
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    config = load_config(args.config)

    # Replace the bootstrap logger with full config-driven logging (file + console).
    # force=True is required because basicConfig above already set a handler.
    setup_logging(config, debug=args.debug)

    log.info("Config loaded from %s", args.config)

    # Start the service
    service = GuardianService(config, config_path=args.config)
    service.start()


if __name__ == "__main__":
    main()
