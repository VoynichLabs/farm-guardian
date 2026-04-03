# Author: Claude Opus 4.6 (implementation), Bubba/Claude Sonnet 4.6 (design)
# Date: 03-April-2026
# PURPOSE: eBird API polling for regional raptor activity near Hampton CT. Provides
#          early warning when hawks are reported within 15km of the farm. Polls Cornell
#          Lab's eBird Recent Observations API every 30 minutes during hawk hours (8am-4pm).
#          Sends Discord alerts for HIGH/MEDIUM threat raptors with cooldown. Logs all
#          sightings to the ebird_sightings DB table. Designed to run as a background
#          thread managed by guardian.py.
# SRP/DRY check: Pass — single responsibility is external raptor intelligence.

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

import requests

from alerts import AlertManager
from database import GuardianDB

log = logging.getLogger("guardian.ebird")

# Hampton CT coordinates
HAMPTON_CT_LAT = 41.7943
HAMPTON_CT_LNG = -72.0591

# Raptor species codes → (common name, threat level)
RAPTOR_SPECIES = {
    "rethaw": ("Red-tailed Hawk", "HIGH"),
    "coohaw": ("Cooper's Hawk", "HIGH"),
    "shshaw": ("Sharp-shinned Hawk", "MEDIUM"),
    "reshaw": ("Red-shouldered Hawk", "MEDIUM"),
    "pefafa": ("Peregrine Falcon", "MEDIUM"),
    "norbob": ("Northern Harrier", "MEDIUM"),
    "osfreh": ("Osprey", "LOW"),
    "amerke": ("American Kestrel", "LOW"),
    "merlin": ("Merlin", "LOW"),
    "baleag": ("Bald Eagle", "LOW"),
}

HIGH_THREAT_RAPTORS = {"rethaw", "coohaw", "shshaw"}


class EBirdWatcher:
    """Polls eBird for regional raptor activity and sends early warnings."""

    def __init__(self, config: dict, db: GuardianDB, alert_manager: AlertManager):
        ebird_cfg = config.get("ebird", {})
        self._enabled = ebird_cfg.get("enabled", False)
        self._api_key = ebird_cfg.get("api_key", "")
        self._poll_interval = ebird_cfg.get("poll_interval_seconds", 1800)
        self._poll_start_hour = ebird_cfg.get("poll_hours_start", 8)
        self._poll_end_hour = ebird_cfg.get("poll_hours_end", 16)
        self._alert_levels = set(ebird_cfg.get("alert_on_threat_levels", ["HIGH", "MEDIUM"]))
        self._alert_cooldown = ebird_cfg.get("alert_cooldown_seconds", 7200)
        self._radius_km = ebird_cfg.get("radius_km", 15)
        self._lookback_hours = ebird_cfg.get("lookback_hours", 2)

        self._db = db
        self._alert_manager = alert_manager
        self._last_alert_time: float = 0
        self._lock = threading.Lock()

        if self._enabled and not self._api_key:
            log.warning("eBird enabled but no API key configured — disabling")
            self._enabled = False

        log.info(
            "EBirdWatcher initialized — enabled=%s, poll=%ds, hours=%d-%d",
            self._enabled, self._poll_interval, self._poll_start_hour, self._poll_end_hour,
        )

    def poll_raptors(self) -> list[dict]:
        """
        Poll eBird API for recent raptor sightings near Hampton CT.
        Returns list of raptor sighting dicts.
        """
        if not self._api_key:
            return []

        url = "https://api.ebird.org/v2/data/obs/geo/recent"
        params = {
            "lat": HAMPTON_CT_LAT,
            "lng": HAMPTON_CT_LNG,
            "dist": self._radius_km,
            "back": self._lookback_hours,
            "cat": "species",
        }
        headers = {"X-eBirdApiToken": self._api_key}

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            observations = resp.json()
        except requests.RequestException as exc:
            log.error("eBird API request failed: %s", exc)
            return []

        raptors = []
        now_iso = datetime.now().isoformat()

        for obs in observations:
            species_code = obs.get("speciesCode", "")
            if species_code not in RAPTOR_SPECIES:
                continue

            name, threat = RAPTOR_SPECIES[species_code]
            sighting = {
                "species_code": species_code,
                "common_name": name,
                "threat_level": threat,
                "location_name": obs.get("locName", "Unknown location"),
                "lat": obs.get("lat"),
                "lng": obs.get("lng"),
                "observed_at": obs.get("obsDt"),
                "count": obs.get("howMany", 1),
            }
            raptors.append(sighting)

            # Log every sighting to DB
            try:
                self._db.insert_ebird_sighting(
                    species_code=species_code,
                    common_name=name,
                    threat_level=threat,
                    polled_at=now_iso,
                    location_name=sighting["location_name"],
                    lat=sighting["lat"],
                    lng=sighting["lng"],
                    observed_at=sighting["observed_at"],
                    count=sighting["count"],
                )
            except Exception as exc:
                log.error("Failed to log eBird sighting: %s", exc)

        if raptors:
            log.info("eBird poll: %d raptor sighting(s) found", len(raptors))
        else:
            log.debug("eBird poll: no raptors found")

        return raptors

    def process_and_alert(self, raptors: list[dict]) -> bool:
        """
        Check if alertable raptors were found and send Discord alert.
        Returns True if alert was sent.
        """
        if not raptors:
            return False

        # Filter to alertable threat levels
        alertable = [r for r in raptors if r["threat_level"] in self._alert_levels]
        if not alertable:
            return False

        # Check cooldown
        with self._lock:
            elapsed = time.time() - self._last_alert_time
            if elapsed < self._alert_cooldown:
                log.debug("eBird alert cooldown active (%.0fs remaining)", self._alert_cooldown - elapsed)
                return False

        # Build and send alert message
        message = self._format_alert(alertable)
        if not message:
            return False

        sent = self._alert_manager._post_webhook({
            "title": "Regional Raptor Alert",
            "description": message,
            "color": 0xFFA500,  # Orange
            "timestamp": datetime.now().isoformat(),
            "footer": {"text": "Farm Guardian | eBird Early Warning"},
        })

        if sent:
            with self._lock:
                self._last_alert_time = time.time()

            # Mark sightings as alerted in DB
            now_iso = datetime.now().isoformat()
            for r in alertable:
                try:
                    self._db.mark_ebird_alert_sent(r["species_code"], now_iso)
                except Exception:
                    pass  # non-critical

            log.info("eBird raptor alert sent — %d species", len(alertable))

        return sent

    def _format_alert(self, raptors: list[dict]) -> Optional[str]:
        """Build a Discord alert message for regional raptor activity."""
        if not raptors:
            return None

        high = [r for r in raptors if r["threat_level"] == "HIGH"]
        medium = [r for r in raptors if r["threat_level"] == "MEDIUM"]
        low = [r for r in raptors if r["threat_level"] == "LOW"]

        lines = [f"eBird sightings within {self._radius_km}km of farm:"]
        if high:
            lines.append("\n**HIGH THREAT:**")
            for r in high:
                lines.append(f"  - {r['common_name']} — {r['location_name']} ({r['observed_at']})")
        if medium:
            lines.append("\n**MEDIUM THREAT:**")
            for r in medium:
                lines.append(f"  - {r['common_name']} — {r['location_name']}")
        if low:
            lines.append(f"\n*Low-threat: {', '.join(r['common_name'] for r in low)}*")

        lines.append("\nConsider bringing flock inside or increasing yard supervision.")
        return "\n".join(lines)

    def _is_hawk_hours(self) -> bool:
        """Check if current time is within hawk-active polling hours."""
        hour = datetime.now().hour
        return self._poll_start_hour <= hour < self._poll_end_hour

    def run_poll_loop(self, shutdown_event: threading.Event) -> None:
        """
        Background loop that polls eBird at configured intervals during hawk hours.
        Call from a dedicated daemon thread.
        """
        if not self._enabled:
            log.info("eBird polling disabled — thread exiting")
            return

        log.info(
            "eBird poll loop started — every %ds during %d:00-%d:00",
            self._poll_interval, self._poll_start_hour, self._poll_end_hour,
        )

        while not shutdown_event.is_set():
            if self._is_hawk_hours():
                try:
                    raptors = self.poll_raptors()
                    self.process_and_alert(raptors)
                except Exception as exc:
                    log.error("eBird poll cycle failed: %s", exc)
            else:
                log.debug("Outside hawk hours — skipping eBird poll")

            # Wait for next poll interval
            shutdown_event.wait(self._poll_interval)

        log.info("eBird poll loop stopped")
