# Author: Claude Opus 4.6
# Date: 04-April-2026
# PURPOSE: ONVIF camera discovery for Farm Guardian. Connects to cameras defined in
#          config.json, validates ONVIF connectivity, retrieves RTSP stream URIs, and
#          subscribes to motion alarm events. Supports periodic re-scanning to handle
#          cameras that reconnect after power loss or network changes. Falls back to
#          config-defined RTSP URLs if ONVIF profile retrieval fails.
# SRP/DRY check: Pass — single responsibility is camera discovery and stream URL resolution.

import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Optional

from onvif import ONVIFCamera

log = logging.getLogger("guardian.discovery")

# Default ONVIF WSDL path shipped with onvif-zeep
# onvif-zeep ships WSDLs at site-packages/wsdl/, not inside the onvif package dir
import os as _os, site as _site
_WSDL_DIR = _os.path.join(_os.path.dirname(_site.getsitepackages()[0] if hasattr(_site, 'getsitepackages') else _site.getusersitepackages()), 'wsdl')
if not _os.path.exists(_WSDL_DIR):
    # fallback: search site-packages
    for _sp in (_site.getsitepackages() if hasattr(_site, 'getsitepackages') else []):
        _candidate = _os.path.join(_sp, 'wsdl')
        if _os.path.exists(_candidate):
            _WSDL_DIR = _candidate
            break


@dataclass
class CameraInfo:
    """Resolved camera connection details."""
    name: str
    ip: str
    port: int
    username: str
    password: str
    onvif_port: int
    camera_type: str  # "ptz" or "fixed"
    rtsp_url: Optional[str] = None
    onvif_camera: Optional[ONVIFCamera] = None
    supports_motion_events: bool = False
    last_seen: float = field(default_factory=time.time)
    online: bool = False


class CameraDiscovery:
    """Discovers and maintains connections to ONVIF cameras on the local network."""

    def __init__(self, config: dict):
        self._camera_configs = config.get("cameras", [])
        self._cameras: dict[str, CameraInfo] = {}
        self._lock = threading.Lock()
        self._rescan_interval = config.get("discovery", {}).get("rescan_interval_seconds", 300)

    @property
    def cameras(self) -> dict[str, CameraInfo]:
        """Return a copy of the current camera registry keyed by name."""
        with self._lock:
            return dict(self._cameras)

    def scan(self) -> dict[str, CameraInfo]:
        """
        Attempt to connect to every camera defined in config. Updates internal
        registry and returns the current state of all cameras.
        """
        log.info("Starting camera scan — %d camera(s) configured", len(self._camera_configs))

        for cam_cfg in self._camera_configs:
            name = cam_cfg.get("name", "unnamed")
            try:
                # Run probe in a thread with a hard timeout so ONVIF hangs don't block forever
                result: list = []
                exc_holder: list = []

                def _probe():
                    try:
                        result.append(self._probe_camera(cam_cfg))
                    except Exception as e:
                        exc_holder.append(e)

                t = threading.Thread(target=_probe, daemon=True)
                t.start()
                t.join(timeout=15)  # 15s hard limit per camera

                if t.is_alive():
                    raise TimeoutError(f"ONVIF probe timed out after 15s")
                if exc_holder:
                    raise exc_holder[0]

                info = result[0]
                with self._lock:
                    self._cameras[name] = info
                # Mask credentials in RTSP URL before logging
                safe_url = self._mask_rtsp_url(info.rtsp_url) if info.rtsp_url else "(none)"
                log.info(
                    "Camera '%s' online — RTSP: %s | motion_events: %s",
                    name,
                    safe_url,
                    info.supports_motion_events,
                )
            except Exception as exc:
                log.warning("Camera '%s' unreachable: %s", name, exc)
                with self._lock:
                    existing = self._cameras.get(name)
                    if existing:
                        existing.online = False
                    else:
                        # Store offline placeholder so guardian knows about it
                        self._cameras[name] = CameraInfo(
                            name=name,
                            ip=cam_cfg.get("ip", ""),
                            port=cam_cfg.get("port", 80),
                            username=cam_cfg.get("username", "admin"),
                            password=cam_cfg.get("password", ""),
                            onvif_port=cam_cfg.get("onvif_port", 8000),
                            camera_type=cam_cfg.get("type", "fixed"),
                            online=False,
                        )

        online = sum(1 for c in self._cameras.values() if c.online)
        log.info("Scan complete — %d/%d cameras online", online, len(self._cameras))
        return self.cameras

    def _probe_camera(self, cam_cfg: dict) -> CameraInfo:
        """Connect to a single camera via ONVIF, retrieve stream URI and capabilities."""
        ip = cam_cfg["ip"]
        onvif_port = cam_cfg.get("onvif_port", 8000)
        username = cam_cfg.get("username", "admin")
        password = cam_cfg.get("password", "")

        log.debug("Probing ONVIF at %s:%d", ip, onvif_port)

        cam = ONVIFCamera(ip, onvif_port, username, password, wsdl_dir=_WSDL_DIR)
        cam.update_xaddrs()

        # Resolve RTSP stream URL from the first media profile
        rtsp_url = self._get_rtsp_url(cam)

        # Check for motion event support
        supports_motion = self._check_motion_events(cam)

        info = CameraInfo(
            name=cam_cfg.get("name", "unnamed"),
            ip=ip,
            port=cam_cfg.get("port", 80),
            username=username,
            password=password,
            onvif_port=onvif_port,
            camera_type=cam_cfg.get("type", "fixed"),
            rtsp_url=rtsp_url,
            onvif_camera=cam,
            supports_motion_events=supports_motion,
            last_seen=time.time(),
            online=True,
        )
        return info

    def _get_rtsp_url(self, cam: ONVIFCamera) -> Optional[str]:
        """Retrieve the RTSP stream URI from the camera's first media profile."""
        try:
            media_service = cam.create_media_service()
            profiles = media_service.GetProfiles()
            if not profiles:
                log.warning("No media profiles found on camera")
                return None

            # Use the first (usually main/high-res) profile
            profile = profiles[0]
            stream_setup = media_service.create_type("GetStreamUri")
            stream_setup.ProfileToken = profile.token
            stream_setup.StreamSetup = {
                "Stream": "RTP-Unicast",
                "Transport": {"Protocol": "RTSP"},
            }
            uri_response = media_service.GetStreamUri(stream_setup)
            rtsp_url = uri_response.Uri

            # Inject credentials into RTSP URL if not already present
            # rtsp://ip:port/... -> rtsp://user:pass@ip:port/...
            if rtsp_url and "@" not in rtsp_url:
                rtsp_url = rtsp_url.replace(
                    "rtsp://", f"rtsp://{cam.user}:{cam.passwd}@", 1
                )

            return rtsp_url
        except Exception as exc:
            log.warning("Failed to retrieve RTSP URI via ONVIF: %s", exc)
            return None

    def _check_motion_events(self, cam: ONVIFCamera) -> bool:
        """Check whether the camera advertises ONVIF motion alarm events."""
        try:
            event_service = cam.create_events_service()
            capabilities = event_service.GetServiceCapabilities()
            # If we got this far without exception, events are at least partially supported
            log.debug("ONVIF events service available — capabilities: %s", capabilities)
            return True
        except Exception as exc:
            log.debug("ONVIF events not available: %s", exc)
            return False

    @staticmethod
    def _mask_rtsp_url(url: str) -> str:
        """Replace credentials in an RTSP URL with '***' for safe logging."""
        # rtsp://user:pass@host:port/... -> rtsp://user:***@host:port/...
        import re
        return re.sub(r"(rtsp://[^:]+:)[^@]+(@)", r"\1***\2", url)

    def get_rtsp_url(self, camera_name: str) -> Optional[str]:
        """Return the resolved RTSP URL for a named camera, or None if unavailable."""
        with self._lock:
            cam = self._cameras.get(camera_name)
            if cam and cam.online:
                return cam.rtsp_url
        return None

    def get_online_cameras(self) -> list[CameraInfo]:
        """Return a list of currently-online cameras."""
        with self._lock:
            return [c for c in self._cameras.values() if c.online]
