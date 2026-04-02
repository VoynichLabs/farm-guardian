# Author: Cascade (Claude Sonnet 4)
# Date: 01-April-2026
# PURPOSE: Main service entry point for Farm Guardian. Orchestrates camera discovery,
#          frame capture, YOLO animal detection, Discord alerting, and event logging.
#          Runs as a foreground process on the Mac Mini. Handles graceful shutdown via
#          SIGINT/SIGTERM. Supports periodic camera re-scanning, daily event cleanup,
#          and configurable logging. All configuration loaded from config.json.
# SRP/DRY check: Pass — single responsibility is service lifecycle and module coordination.

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from discovery import CameraDiscovery
from capture import FrameCaptureManager, FrameResult
from detect import AnimalDetector, DetectionResult
from alerts import AlertManager
from logger import EventLogger

log = logging.getLogger("guardian")

# Default config file path
_CONFIG_PATH = "config.json"

# How often to re-scan for cameras (seconds)
_RESCAN_INTERVAL = 300

# How often to run event cleanup (seconds) — once per hour
_CLEANUP_INTERVAL = 3600


class GuardianService:
    """Main service that wires together all Farm Guardian modules."""

    def __init__(self, config: dict):
        self._config = config
        self._shutdown_event = threading.Event()

        # Initialize modules
        log.info("Initializing Farm Guardian modules...")
        self._discovery = CameraDiscovery(config)
        self._detector = AnimalDetector(config)
        self._alert_manager = AlertManager(config)
        self._event_logger = EventLogger(config)
        self._capture_manager = FrameCaptureManager(config, on_frame=self._on_frame)

        # Background task threads
        self._rescan_thread: threading.Thread | None = None
        self._cleanup_thread: threading.Thread | None = None

        # Stats tracking
        self._frames_processed = 0
        self._alerts_sent = 0
        self._start_time: float | None = None

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
            # Start capturing from all online cameras with RTSP URLs
            for cam in online_cameras:
                if cam.rtsp_url:
                    self._capture_manager.add_camera(cam.name, cam.rtsp_url)
                else:
                    log.warning(
                        "Camera '%s' online but no RTSP URL resolved — skipping capture", cam.name
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

        self._capture_manager.stop_all()

        uptime = time.time() - self._start_time if self._start_time else 0
        log.info(
            "Guardian stopped — uptime: %.0fs, frames processed: %d, alerts sent: %d",
            uptime,
            self._frames_processed,
            self._alerts_sent,
        )

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle SIGINT/SIGTERM for graceful shutdown."""
        sig_name = signal.Signals(signum).name
        log.info("Received %s — initiating graceful shutdown", sig_name)
        self._shutdown_event.set()

    def _on_frame(self, frame_result: FrameResult) -> None:
        """
        Callback invoked by capture threads for each new frame. Runs detection,
        logs events, and sends alerts if predators are found. This runs on the
        capture thread — keep it fast.
        """
        try:
            result = self._detector.detect(frame_result.frame, frame_result.camera_name)
            self._frames_processed += 1

            if not result.detections:
                return

            # Log all non-ignored detections
            for det in result.detections:
                self._event_logger.log_event(
                    camera_name=result.camera_name,
                    detection_class=det.class_name,
                    confidence=det.confidence,
                    bbox=det.bbox,
                    frame=result.frame,
                )

            # Send alert if any predator detections passed all filters (including dwell)
            if result.has_predators:
                sent = self._alert_manager.send_alert(
                    camera_name=result.camera_name,
                    detections=result.predator_detections,
                    frame=result.frame,
                )
                if sent:
                    self._alerts_sent += 1

        except Exception as exc:
            log.error(
                "Error processing frame from '%s': %s",
                frame_result.camera_name,
                exc,
                exc_info=True,
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
                    if cam.name not in active and cam.rtsp_url:
                        log.info("New/reconnected camera '%s' — starting capture", cam.name)
                        self._capture_manager.add_camera(cam.name, cam.rtsp_url)

            except Exception as exc:
                log.error("Camera re-scan failed: %s", exc)

    def _cleanup_loop(self) -> None:
        """Periodically clean up old event directories."""
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
    service = GuardianService(config)
    service.start()


if __name__ == "__main__":
    main()
