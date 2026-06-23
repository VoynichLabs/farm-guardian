# Author: Claude Opus 4.7 — Bubba coding sub-agent (motion-alert separate debounce),
#         Claude Opus 4.8 (1M context) — Bubba coding sub-agent (motion-alert add),
#         Claude Opus 4.6 (updated), Cascade (Claude Sonnet 4) (original)
# Date: 22-June-2026 (v2.43.0 — separate motion_alert.cooldown_seconds debounce);
#       12-June-2026 (v2.41.0 — send_motion_alert for camera-hardware motion)
# PURPOSE: Discord alert manager for Farm Guardian. Posts webhook messages to the
#          #farm-2026 Discord channel when predator-class animals are detected. Each alert
#          includes an embedded snapshot image, detection class, confidence score, timestamp,
#          and camera name. Implements rate limiting (cooldown per animal class, default 5 min)
#          to avoid spamming. Buffers failed alerts and retries them on subsequent calls.
#          Alert images prefer the camera's HTTP snapshot API (4K, sharp) over RTSP buffer
#          frames (1080p, often blurry due to autofocus lag). Bounding box coordinates are
#          scaled from detection resolution to snapshot resolution. Falls back to RTSP frame
#          if the HTTP snapshot is unavailable.
#          v2.41.0: added send_motion_alert() — posts a "Motion" embed when a camera's own
#          hardware motion sensor fires (no YOLO involved). It reuses the same webhook +
#          snapshot path as send_alert but has its OWN per-camera cooldown dict and a distinct
#          embed color, so motion alerts and predator alerts never throttle each other. This is
#          pure-upside: it only ADDS alerts and can never suppress a predator detection. Gating
#          (enable flag / night-only / per-camera opt-in) lives in guardian.py, not here.
# SRP/DRY check: Pass — single responsibility is alert delivery via Discord webhook. The new
#          motion path reuses _capture_http_snapshot / _encode_snapshot / _post_webhook (DRY).

import io
import logging
import time
import threading
from collections import defaultdict
from datetime import datetime
from typing import Optional

import cv2
import numpy as np
import requests

from detect import Detection

log = logging.getLogger("guardian.alerts")

# Discord embed color for predator alerts (red-orange)
_ALERT_COLOR = 0xFF4500

# Discord embed color for camera-hardware motion alerts (amber/gold) — visually
# distinct from the red-orange predator alerts so the two are obvious at a glance.
_MOTION_ALERT_COLOR = 0xFFC107

# Maximum retries for buffered alerts before dropping them
_MAX_RETRIES = 3

# HTTP timeout for webhook posts
_WEBHOOK_TIMEOUT = 15


class AlertManager:
    """Sends Discord alerts when predator detections meet alert criteria."""

    def __init__(self, config: dict, camera_controller=None):
        alerts_cfg = config.get("alerts", {})
        detection_cfg = config.get("detection", {})

        # Optional camera controller for sharp HTTP snapshots (4K) instead of
        # blurry RTSP buffer frames. Set via constructor or set_camera_controller().
        self._camera_ctrl = camera_controller

        self._webhook_url = alerts_cfg.get("discord_webhook_url", "")
        self._include_snapshot = alerts_cfg.get("include_snapshot", True)
        self._mention_on_alert = alerts_cfg.get("mention_on_alert", False)

        # Cooldown: seconds between alerts for the same animal class
        self._cooldown_seconds = detection_cfg.get("alert_cooldown_seconds", 300)

        # Separate, typically-larger debounce for camera-hardware MOTION alerts
        # (send_motion_alert). These are pure-motion ("sensor triggered") posts with
        # NO YOLO gate, so they are the spammiest layer; throttling them hard here
        # cannot suppress predator alerts, which use _cooldown_seconds above.
        # Falls back to _cooldown_seconds when motion_alert.cooldown_seconds is unset.
        motion_alert_cfg = config.get("motion_alert", {})
        self._motion_cooldown_seconds = motion_alert_cfg.get(
            "cooldown_seconds", self._cooldown_seconds
        )

        # Track last alert time per class to enforce cooldown
        # Key: class_name -> last alert unix timestamp
        self._last_alert_time: dict[str, float] = defaultdict(float)

        # Separate cooldown for camera-hardware motion alerts (send_motion_alert).
        # Key: camera_name -> last motion-alert unix timestamp. Kept distinct from
        # _last_alert_time so motion and predator alerts never throttle one another;
        # both reuse the same _cooldown_seconds value (detection.alert_cooldown_seconds).
        self._last_motion_alert_time: dict[str, float] = defaultdict(float)

        self._lock = threading.Lock()

        # Buffer for failed alerts that need retry
        self._retry_buffer: list[dict] = []

        if not self._webhook_url or "YOUR_WEBHOOK" in self._webhook_url:
            log.warning(
                "Discord webhook URL not configured — alerts will be logged but not sent. "
                "Set alerts.discord_webhook_url in config.json."
            )

        log.info(
            "AlertManager initialized — cooldown=%ds, snapshots=%s",
            self._cooldown_seconds,
            self._include_snapshot,
        )

    def should_alert(self, class_name: str) -> bool:
        """Check if an alert for this class is allowed (cooldown not active)."""
        with self._lock:
            last = self._last_alert_time.get(class_name, 0)
            elapsed = time.time() - last
            return elapsed >= self._cooldown_seconds

    def send_alert(
        self,
        camera_name: str,
        detections: list[Detection],
        frame: Optional[np.ndarray] = None,
    ) -> bool:
        """
        Send a Discord alert for one or more predator detections. Respects cooldown
        per class. Returns True if an alert was actually sent.

        Only predator detections that pass cooldown are included. If all detections
        are on cooldown, no alert is sent.
        """
        # Filter to alertable detections (predator + cooldown passed)
        alertable = [d for d in detections if d.is_predator and self.should_alert(d.class_name)]
        if not alertable:
            return False

        now = datetime.now()
        now_ts = time.time()

        # Build the embed
        title = self._build_title(alertable)
        description = self._build_description(alertable, camera_name, now)
        embed = {
            "title": title,
            "description": description,
            "color": _ALERT_COLOR,
            "timestamp": now.isoformat(),
            "footer": {"text": f"Farm Guardian | {camera_name}"},
        }

        # Encode snapshot as JPEG bytes for upload.
        # Prefer the camera's HTTP snapshot API (sharp 4K) over the RTSP buffer
        # frame (1080p, often blurry from autofocus lag or HEVC decode artifacts).
        snapshot_bytes: Optional[bytes] = None
        if self._include_snapshot:
            if self._camera_ctrl is not None:
                snapshot_bytes = self._capture_http_snapshot(
                    camera_name, frame, alertable
                )
            if snapshot_bytes is None and frame is not None:
                snapshot_bytes = self._encode_snapshot(frame, alertable)

        # Set the embed image to reference the attached file
        if snapshot_bytes:
            embed["image"] = {"url": "attachment://snapshot.jpg"}

        # Attempt to send
        sent = self._post_webhook(embed, snapshot_bytes)

        if sent:
            # Update cooldown timestamps for all alerted classes
            with self._lock:
                for d in alertable:
                    self._last_alert_time[d.class_name] = now_ts

            class_list = ", ".join(f"{d.class_name} ({d.confidence:.0%})" for d in alertable)
            log.info("Alert sent — %s on '%s'", class_list, camera_name)
        else:
            # Buffer for retry
            self._retry_buffer.append({
                "embed": embed,
                "snapshot_bytes": snapshot_bytes,
                "retries": 0,
            })
            log.warning("Alert failed — buffered for retry (%d in queue)", len(self._retry_buffer))

        # Process retry buffer while we're here
        self._process_retries()

        return sent

    def _motion_cooldown_passed(self, camera_name: str) -> bool:
        """Check if a motion alert for this camera is allowed (cooldown not active).

        Uses the dedicated _motion_cooldown_seconds debounce (motion_alert.cooldown_seconds)
        so pure-motion alerts can be throttled far harder than predator alerts without
        affecting them.
        """
        with self._lock:
            last = self._last_motion_alert_time.get(camera_name, 0)
            elapsed = time.time() - last
            return elapsed >= self._motion_cooldown_seconds

    def send_motion_alert(
        self,
        camera_name: str,
        frame: Optional[np.ndarray] = None,
    ) -> bool:
        """
        Send a Discord alert when a camera's own hardware motion sensor fires.

        This is independent of YOLO detection — it is triggered by the camera's
        built-in motion event (see guardian.py::_motion_watch_loop). It exists to
        surface activity that detection might miss (e.g. detection disabled, or an
        object too small/fast for YOLO). It is pure-upside: it only ADDS alerts and
        can never suppress a predator detection.

        Respects an OWN per-camera cooldown (self._last_motion_alert_time) using the
        dedicated motion_alert.cooldown_seconds debounce (self._motion_cooldown_seconds),
        tracked separately so the two alert kinds never throttle each other. Returns
        True only if an alert was actually sent.

        Gating (enabled flag, night-only, per-camera opt-in) is the caller's job —
        this method only enforces cooldown and posts.
        """
        # Cooldown check BEFORE posting — mirrors send_alert. The timestamp is stamped
        # only on a successful send, so a failed post does not start a bogus cooldown.
        if not self._motion_cooldown_passed(camera_name):
            return False

        now = datetime.now()
        now_ts = time.time()

        embed = {
            "title": f"⚠️ Motion — {camera_name}",
            "description": (
                f"**Camera:** {camera_name}\n"
                f"**Time:** {now.strftime('%I:%M:%S %p')}\n\n"
                "Camera motion sensor triggered."
            ),
            "color": _MOTION_ALERT_COLOR,
            "timestamp": now.isoformat(),
            "footer": {"text": f"Farm Guardian | {camera_name} | motion"},
        }

        # Snapshot: prefer the sharp HTTP snapshot (same path send_alert uses), fall
        # back to the supplied frame. Pass an empty detections list — both helpers
        # iterate it to draw boxes, and there are no boxes for a motion-only event.
        snapshot_bytes: Optional[bytes] = None
        if self._include_snapshot:
            if self._camera_ctrl is not None:
                snapshot_bytes = self._capture_http_snapshot(camera_name, frame, [])
            if snapshot_bytes is None and frame is not None:
                snapshot_bytes = self._encode_snapshot(frame, [])

        if snapshot_bytes:
            embed["image"] = {"url": "attachment://snapshot.jpg"}

        sent = self._post_webhook(embed, snapshot_bytes)

        if sent:
            with self._lock:
                self._last_motion_alert_time[camera_name] = now_ts
            log.info("Motion alert sent for '%s'", camera_name)
        else:
            # Unlike predator alerts, motion alerts are not retry-buffered: a missed
            # motion alert simply re-fires on the camera's next False→True transition,
            # so buffering would only risk double-posting stale motion.
            log.warning("Motion alert failed for '%s' — not buffered", camera_name)

        return sent

    def _build_title(self, detections: list[Detection]) -> str:
        """Build a concise alert title from the detection list."""
        classes = sorted(set(d.class_name for d in detections))
        if len(classes) == 1:
            return f"Predator Alert: {classes[0].title()}"
        return f"Predator Alert: {', '.join(c.title() for c in classes)}"

    def _build_description(
        self, detections: list[Detection], camera_name: str, dt: datetime
    ) -> str:
        """Build the embed description with detection details."""
        lines = [f"**Camera:** {camera_name}", f"**Time:** {dt.strftime('%I:%M:%S %p')}"]
        lines.append("")
        for d in detections:
            lines.append(
                f"- **{d.class_name.title()}** — {d.confidence:.0%} confidence, "
                f"{d.bbox_area_pct:.1f}% of frame, seen {d.frame_count} frames"
            )
        return "\n".join(lines)

    def _encode_snapshot(
        self, frame: np.ndarray, detections: list[Detection]
    ) -> Optional[bytes]:
        """Draw bounding boxes on the frame and encode as JPEG bytes."""
        try:
            annotated = frame.copy()
            for d in detections:
                x1, y1, x2, y2 = [int(v) for v in d.bbox]
                color = (0, 0, 255)  # Red in BGR
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
                label = f"{d.class_name} {d.confidence:.0%}"
                # Draw label background
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
                cv2.rectangle(annotated, (x1, y1 - th - 8), (x1 + tw + 4, y1), color, -1)
                cv2.putText(
                    annotated, label, (x1 + 2, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA,
                )

            _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return buf.tobytes()
        except Exception as exc:
            log.error("Failed to encode snapshot: %s", exc)
            return None

    def _capture_http_snapshot(
        self,
        camera_name: str,
        det_frame: Optional[np.ndarray],
        detections: list[Detection],
    ) -> Optional[bytes]:
        """Fetch a sharp snapshot via the camera's HTTP API and annotate with bboxes.

        The HTTP snapshot API (/cgi-bin/api.cgi?cmd=Snap) returns a focused 4K JPEG
        regardless of RTSP stream state. Detection bounding boxes are in the detection
        frame's coordinate space (typically 1080p) and must be scaled to the snapshot
        resolution (typically 4K = 2x).

        Returns annotated JPEG bytes, or None on any failure (caller falls back to
        the RTSP frame).
        """
        try:
            jpeg_bytes = self._camera_ctrl.take_snapshot(camera_name)
            if jpeg_bytes is None:
                log.debug("HTTP snapshot returned None for '%s'", camera_name)
                return None

            # Decode the camera's JPEG to a numpy array for annotation
            snapshot = cv2.imdecode(
                np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR
            )
            if snapshot is None:
                log.warning("Failed to decode HTTP snapshot for '%s'", camera_name)
                return None

            snap_h, snap_w = snapshot.shape[:2]

            # Compute scale factors from detection frame to snapshot resolution.
            # Detection typically runs on 1080p (1920x1080), snapshot is 4K (3840x2160).
            scale_x, scale_y = 1.0, 1.0
            if det_frame is not None:
                det_h, det_w = det_frame.shape[:2]
                scale_x = snap_w / det_w
                scale_y = snap_h / det_h

            # Draw bounding boxes scaled to 4K — thicker lines and larger text
            # than the 1080p path since the image has 4x the pixels.
            annotated = snapshot.copy()
            for d in detections:
                x1 = int(d.bbox[0] * scale_x)
                y1 = int(d.bbox[1] * scale_y)
                x2 = int(d.bbox[2] * scale_x)
                y2 = int(d.bbox[3] * scale_y)
                color = (0, 0, 255)  # Red in BGR
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 3)
                label = f"{d.class_name} {d.confidence:.0%}"
                (tw, th), _ = cv2.getTextSize(
                    label, cv2.FONT_HERSHEY_SIMPLEX, 1.0, 2
                )
                cv2.rectangle(
                    annotated, (x1, y1 - th - 12), (x1 + tw + 6, y1), color, -1
                )
                cv2.putText(
                    annotated, label, (x1 + 3, y1 - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA,
                )

            _, buf = cv2.imencode(
                ".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 90]
            )
            log.info(
                "Alert using 4K HTTP snapshot for '%s' (%dx%d, scale=%.1fx)",
                camera_name, snap_w, snap_h, scale_x,
            )
            return buf.tobytes()

        except Exception as exc:
            log.warning(
                "HTTP snapshot failed for alert on '%s': %s — will use RTSP frame",
                camera_name, exc,
            )
            return None

    def _post_webhook(self, embed: dict, snapshot_bytes: Optional[bytes] = None) -> bool:
        """Post to the Discord webhook. Returns True on success."""
        if not self._webhook_url or "YOUR_WEBHOOK" in self._webhook_url:
            log.info("Webhook not configured — alert logged only: %s", embed.get("title"))
            return True  # Treat as success so it doesn't buffer forever

        payload = {"embeds": [embed]}

        try:
            if snapshot_bytes:
                # Multipart upload: JSON payload + file
                files = {
                    "file": ("snapshot.jpg", io.BytesIO(snapshot_bytes), "image/jpeg"),
                }
                import json
                response = requests.post(
                    self._webhook_url,
                    data={"payload_json": json.dumps(payload)},
                    files=files,
                    timeout=_WEBHOOK_TIMEOUT,
                )
            else:
                response = requests.post(
                    self._webhook_url,
                    json=payload,
                    timeout=_WEBHOOK_TIMEOUT,
                )

            if response.status_code in (200, 204):
                return True

            # Discord rate limiting
            if response.status_code == 429:
                retry_after = response.json().get("retry_after", 5)
                log.warning("Discord rate-limited — retry after %.1fs", retry_after)
                time.sleep(min(retry_after, 10))
                return False

            log.error(
                "Discord webhook returned %d: %s",
                response.status_code,
                response.text[:200],
            )
            return False

        except requests.Timeout:
            log.error("Discord webhook timed out after %ds", _WEBHOOK_TIMEOUT)
            return False
        except requests.RequestException as exc:
            log.error("Discord webhook request failed: %s", exc)
            return False

    def _process_retries(self) -> None:
        """Attempt to re-send buffered alerts. Drop after max retries."""
        if not self._retry_buffer:
            return

        remaining = []
        for item in self._retry_buffer:
            if item["retries"] >= _MAX_RETRIES:
                log.warning("Dropping alert after %d retries: %s", _MAX_RETRIES, item["embed"].get("title"))
                continue

            sent = self._post_webhook(item["embed"], item.get("snapshot_bytes"))
            if not sent:
                item["retries"] += 1
                remaining.append(item)

        self._retry_buffer = remaining
