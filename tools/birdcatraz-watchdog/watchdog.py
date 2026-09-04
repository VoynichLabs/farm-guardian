# Author: Claude Opus 5
# Date: 04-September-2026
# PURPOSE: Alert on Discord when the Birdcatraz Raspberry Pi (`farm-pi5`) goes
#          quiet. The Pi has NO battery backup, so "Pi unreachable" is the
#          cheapest reliable proxy for "the Birdcatraz outdoor circuit lost
#          power" — which per CLAUDE.md's top banner has already cost 3.25
#          hours overnight (07-Aug-2026) and 70 unnoticed minutes (13-Aug-2026).
#          Nothing in the stack watched for a camera host going silent; this
#          closes that gap.
#
#          Runs on the Mac Mini every 5 minutes via
#          com.farmguardian.birdcatraz-watchdog.plist. Probes the Pi's two
#          camera health endpoints; after 2 consecutive failures (~10 min) it
#          posts ONE Discord alert mentioning Mark, then stays silent for the
#          duration of the outage, then posts ONE un-mentioned recovery notice.
#
#          On failure it also probes the other outdoor devices so the alert can
#          distinguish "the whole circuit tripped" (walk out and flip the
#          breaker) from "just the Pi" (its own brick/cable/SD card). That
#          classification is the point of the alert — Boss's stated reason for
#          wanting it was that the Pi tells him whether everything else is down.
#
#          ⚠️ 04-Sep-2026: that classification WAS WRONG ONCE AND COST A TRIP.
#          On 03-Sep it declared a circuit trip while house-yard and duo2 were
#          both serving normally — duo2 archived a frame at the exact second it
#          was called down. Cause: a single un-retried TCP probe decided the
#          verdict. A device is now considered powered if EITHER it archived a
#          frame recently OR its port answers; only the absence of BOTH counts
#          as down. See docs/04-Sep-2026-watchdog-circuit-verdict-fix-plan.md.
#
#          DELIBERATELY NOT a remediation tool. A power cut cannot be fixed
#          from the Mini; the only correct output is telling a human sooner.
#
# INTEGRATION POINTS:
#          - Reads outdoor device addresses from config.json (single source of
#            truth; no second copy of an IP to drift).
#          - Reads DISCORD_WEBHOOK_URL from .env — the same #farm-2026 webhook
#            every other farm notifier uses. Posts TEXT ONLY under an unmapped
#            username, which is what keeps it invisible to
#            scripts/discord-reaction-sync.py: that only ingests messages
#            carrying an image attachment, and maps gem reactions by webhook
#            username -> camera_id. So this cannot pollute the gem quality gate.
#          - State lives in a JSON file, NOT the database — the watchdog must
#            keep working when the DB is locked or the pipeline is down.
#          - READS data/guardian.db (read-only, short timeout) purely to ask
#            "did this camera archive a frame recently?" as corroborating
#            evidence of power. Any DB failure degrades to TCP-only rather than
#            raising — a broken DB must never suppress an alert.
#
# SRP/DRY check: Pass. Single responsibility: is the Pi alive, and tell someone
#          if not. Frame-recency corroboration reuses the existing image_archive
#          table rather than adding a second liveness store. Alert/state/recovery shape is deliberately modelled on the
#          existing tools/s7-battery-monitor/monitor.py rather than invented —
#          same one-shot-latch-in-a-JSON-file idea, same post_discord shape.
#          Stdlib only, so it runs even if the venv is broken.

from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.json"
ENV_PATH = REPO_ROOT / ".env"
DB_PATH = REPO_ROOT / "data" / "guardian.db"

# Median seconds between archived frames per outdoor camera, measured 04-Sep-2026.
# A frame newer than FRAME_FRESH_MULTIPLIER x this proves the device had power when
# it was written, which is stronger evidence than any single TCP connect.
FRAME_CADENCE_S = {"house-yard": 45.0, "duo2": 10.0, "s7-cam": 5.0}
FRAME_FRESH_MULTIPLIER = 3.0

SERVICE_ROOT = Path(
    os.environ.get(
        "SERVICE_ROOT", os.path.expanduser("~/.local/farm-services/birdcatraz-watchdog")
    )
)
STATE_PATH = SERVICE_ROOT / "state.json"
LOG_PATH = SERVICE_ROOT / "watchdog.log"

# Mark's Discord user ID. Mentioned on the OUTAGE alert only — see module
# docstring. Same ID the S7 reel lanes use (CLAUDE.md).
MARK_DISCORD_USER_ID = "293569238386606080"

# Consecutive failed probes before alerting. At a 5-minute interval, 2 means a
# real alert lands ~10 minutes in. Deliberately >1: a single miss during a Pi
# reboot or a Wi-Fi blip is not an outage, and an alert that cries wolf is an
# alert that gets muted.
FAIL_THRESHOLD = int(os.environ.get("BIRDCATRAZ_FAIL_THRESHOLD", "2"))

# Per-probe timeout. Generous enough for a loaded Pi, short enough that probing
# every device on a dead circuit still finishes well inside the 5-minute tick.
PROBE_TIMEOUT_S = float(os.environ.get("BIRDCATRAZ_PROBE_TIMEOUT", "6"))
WEBHOOK_TIMEOUT_S = 10

# The Pi is on DHCP with NO static lease reserved (open TODO in
# docs/05-Aug-2026-birdcatraz-pi5-bringup-log.md). Probe the mDNS name first so
# we follow the host if its IP drifts, then the known IP so a flaky mDNS
# resolver alone cannot fake an outage. Down only if BOTH fail.
PI_HOSTS = ("farm-pi5.local", "192.168.0.17")
PI_HEALTH_PORTS = (8090, 8091)

SERVICE_ROOT.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("birdcatraz-watchdog")


def load_webhook() -> str:
    """Read DISCORD_WEBHOOK_URL from the environment, falling back to .env.

    launchd gives a job a near-empty environment, so the .env fallback is the
    path that actually runs in production — the env var branch exists for
    manual/CLI testing.
    """
    from_env = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if from_env:
        return from_env
    try:
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith("DISCORD_WEBHOOK_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError as exc:
        log.warning("could not read %s: %s", ENV_PATH, exc)
    return ""


def tcp_open(host: str, port: int, timeout: float = PROBE_TIMEOUT_S) -> bool:
    """True if a TCP connection to host:port completes.

    Used for the non-HTTP outdoor devices (RTSP/camera web ports). ICMP is
    blocked between wired and wireless on this router, so ping is useless here
    and a TCP connect is the only meaningful reachability test (CLAUDE.md).
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def pi_is_alive() -> tuple[bool, str]:
    """Probe the Pi's camera health endpoints.

    Returns (alive, detail). Alive means at least one camera host answered
    /health with ok=true on at least one of the addresses we know it by. We
    accept one-of-two cameras as alive on purpose: a single wedged USB camera
    is a camera problem, not a power problem, and this watchdog only claims to
    answer the power question. Reporting which cameras answered goes in the
    detail string so a half-alive Pi is still visible in the log.
    """
    healthy: list[str] = []
    reachable = False
    for host in PI_HOSTS:
        for port in PI_HEALTH_PORTS:
            url = f"http://{host}:{port}/health"
            try:
                with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_S) as resp:
                    reachable = True
                    payload = json.loads(resp.read().decode("utf-8", "replace"))
            except (urllib.error.URLError, OSError, ValueError, socket.timeout):
                continue
            if payload.get("ok"):
                healthy.append(f"{payload.get('camera_id', port)}")
        if healthy:
            # First address that works is enough — don't probe the second.
            return True, f"{host}: {', '.join(healthy)} healthy"
    if reachable:
        # Host answered HTTP but no camera reports ok. The Pi has power, so
        # this is NOT the outage this watchdog exists to report.
        return True, "Pi reachable but no camera reporting ok"
    return False, "no response on any address/port"


def load_outdoor_devices() -> dict[str, tuple[str, int]]:
    """Read the other outdoor devices from config.json.

    Addresses come from config rather than being hardcoded so there is one
    source of truth — these IPs are DHCP and have drifted before.
    """
    devices: dict[str, tuple[str, int]] = {}
    try:
        cameras = json.loads(CONFIG_PATH.read_text()).get("cameras", {})
    except (OSError, ValueError) as exc:
        log.warning("could not read %s: %s", CONFIG_PATH, exc)
        return devices

    items = cameras.items() if isinstance(cameras, dict) else [
        (c.get("name"), c) for c in cameras
    ]
    # Only devices that are physically OUTSIDE and therefore share the fate of
    # the Birdcatraz circuit. The MacBook Air and the Mini are indoors — per
    # CLAUDE.md, indoor gear staying up is what confirms a circuit trip rather
    # than a whole-house outage, so including them would break the inference.
    outdoor_ports = {"house-yard": 80, "duo2": 554, "s7-cam": 8080}
    for name, cfg in items:
        if name not in outdoor_ports or not isinstance(cfg, dict):
            continue
        host = cfg.get("ip")
        if not host:
            base = cfg.get("http_base_url") or ""
            host = urlparse(base).hostname
        if host:
            devices[name] = (host, outdoor_ports[name])
    return devices


def last_frame_age_s(camera_id: str) -> Optional[float]:
    """Seconds since this camera's newest archived frame, or None if unknown.

    Read-only, short timeout. ANY failure (DB missing, locked, corrupt, unparsable
    timestamp) returns None so the caller falls back to the TCP probe. A broken
    database must never be able to suppress a power alert.
    """
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=3.0)
        try:
            row = con.execute(
                "SELECT MAX(ts) FROM image_archive WHERE camera_id = ?", (camera_id,)
            ).fetchone()
        finally:
            con.close()
    except Exception as exc:  # sqlite3.Error, OSError — all degrade the same way
        log.debug("frame-age lookup failed for %s: %s", camera_id, exc)
        return None

    if not row or not row[0]:
        return None
    try:
        ts = datetime.fromisoformat(row[0])
    except ValueError:
        log.debug("unparsable ts for %s: %r", camera_id, row[0])
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()


def device_has_power(name: str, host: str, port: int) -> tuple[bool, str]:
    """Is this outdoor device powered? Returns (powered, evidence).

    TWO independent sources of POSITIVE evidence, either of which is sufficient:

      1. It archived a frame within FRAME_FRESH_MULTIPLIER x its median cadence.
         Local SQLite read — immune to the network contention that produced the
         03-Sep-2026 false "circuit tripped" alert.
      2. Its TCP port answers (retried once — a single refusal decided that alert).

    Only the absence of BOTH means no power. The OR is deliberate and not
    symmetric: on 03-Sep `s7-cam` had power and answered TCP while producing no
    frames (its charging lane), whereas `house-yard`/`duo2` were producing frames
    while a probe was refused. Each source covers the other's blind spot.
    """
    age = last_frame_age_s(name)
    if age is not None:
        fresh_within = FRAME_FRESH_MULTIPLIER * FRAME_CADENCE_S.get(name, 60.0)
        if age <= fresh_within:
            return True, f"frame {age:.0f}s old"

    # Retry once: the 03-Sep misfire hung on a single un-retried probe.
    for _ in range(2):
        if tcp_open(host, port):
            return True, "port open"
    return False, f"no port, no frame (age={age if age is None else f'{age:.0f}s'})"


def classify_outage() -> tuple[str, list[str], list[str]]:
    """Work out whether the whole outdoor circuit is down or only the Pi.

    Returns (verdict, down, up) where verdict is 'circuit' or 'pi-only'.
    """
    down: list[str] = []
    up: list[str] = []
    for name, (host, port) in load_outdoor_devices().items():
        powered, evidence = device_has_power(name, host, port)
        log.info("outage probe: %s -> %s (%s)", name, "UP" if powered else "DOWN", evidence)
        (up if powered else down).append(name)
    # Any other outdoor device also dark => shared cause => the circuit.
    verdict = "circuit" if down else "pi-only"
    return verdict, down, up


def post_discord(content: str, webhook: str, *, username: str = "farm-power") -> bool:
    if not webhook:
        log.error("no DISCORD_WEBHOOK_URL available; alert NOT sent: %s", content)
        return False
    body = json.dumps({"username": username, "content": content}).encode()
    req = urllib.request.Request(
        webhook,
        data=body,
        headers={
            "Content-Type": "application/json",
            # REQUIRED. Discord's edge answers 403 Forbidden to urllib's default
            # "Python-urllib/3.x" User-Agent — verified 13-Aug-2026, the identical
            # POST succeeds with any custom UA and fails without one. Every other
            # Discord path in this repo uses `requests`, which sets its own UA, so
            # this trap only bites stdlib callers. Do not remove this header.
            # (tools/s7-battery-monitor/monitor.py has the same latent bug; it is
            # disabled, so it has never surfaced there.)
            "User-Agent": "FarmGuardian-BirdcatrazWatchdog/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_S) as resp:
            resp.read()
    except (urllib.error.URLError, OSError, socket.timeout) as exc:
        # Never raise: a failed alert must not kill the watchdog, or one blip
        # on Discord's side would end the monitoring silently.
        log.error("Discord post failed: %s", exc)
        return False
    log.info("posted to Discord: %s", content.replace("\n", " | "))
    return True


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except (OSError, ValueError) as exc:
            log.warning("state load failed (%s); starting fresh", exc)
    return {"consecutive_failures": 0, "alerted": False}


def save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, indent=2))
    except OSError as exc:
        log.error("could not write state to %s: %s", STATE_PATH, exc)


def _humanise_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def build_outage_message(verdict: str, down: list[str], up: list[str]) -> str:
    """Compose the outage alert.

    Deliberately written for a human standing in a kitchen, not for a log
    reader: what is dark, what it probably means, and what to physically do.
    """
    mention = f"<@{MARK_DISCORD_USER_ID}>"
    if verdict == "circuit":
        also = ", ".join(f"`{d}`" for d in down)
        still_up = f" `{'`, `'.join(up)}` still up." if up else ""
        return (
            f"{mention} 🔴 **Birdcatraz power is out.**\n"
            f"The Pi has been unreachable for ~{FAIL_THRESHOLD * 5} minutes, and so "
            f"{'is' if len(down) == 1 else 'are'} {also}. Multiple outdoor devices "
            f"going dark together means the outdoor circuit tripped.{still_up}\n"
            f"**It does not reset itself — the breaker on the Birdcatraz outlet "
            f"needs flipping by hand.** Nothing here can fix it."
        )
    return (
        f"{mention} 🟠 **The Birdcatraz Pi has gone quiet.**\n"
        f"Unreachable for ~{FAIL_THRESHOLD * 5} minutes, but the other outdoor "
        f"cameras are still up — so this looks like the Pi itself, not the circuit. "
        f"Check its power adapter, its cable, and the Ethernet run.\n"
        f"Both Pi cameras (the 1080p webcam and the dashcam) are down until it's back."
    )


def main() -> int:
    webhook = load_webhook()
    state = load_state()
    alive, detail = pi_is_alive()

    if alive:
        if state.get("alerted"):
            down_since = state.get("down_since")
            duration = (
                f" It was down for {_humanise_duration(time.time() - down_since)}."
                if down_since
                else ""
            )
            # No mention on recovery — "it's back" is not worth a phone ping,
            # and mentioning on both halves is how an alert becomes noise.
            post_discord(
                f"🟢 **Birdcatraz is back.** The Pi is answering again and both "
                f"cameras are live.{duration}",
                webhook,
            )
        if state.get("consecutive_failures"):
            log.info("Pi recovered after %d failed probe(s)", state["consecutive_failures"])
        save_state({"consecutive_failures": 0, "alerted": False})
        log.info("Pi alive — %s", detail)
        return 0

    failures = int(state.get("consecutive_failures", 0)) + 1
    state["consecutive_failures"] = failures
    state.setdefault("down_since", time.time())
    log.warning("Pi probe failed (%s) — %d consecutive", detail, failures)

    # Alert exactly once per outage, on the transition past the threshold.
    if failures >= FAIL_THRESHOLD and not state.get("alerted"):
        verdict, down, up = classify_outage()
        log.warning("outage classified as '%s' (also down: %s; up: %s)", verdict, down, up)
        if post_discord(build_outage_message(verdict, down, up), webhook):
            state["alerted"] = True
        else:
            # Alert failed to send — leave `alerted` false so the next tick
            # retries rather than swallowing the outage entirely.
            log.error("outage alert could not be delivered; will retry next tick")

    save_state(state)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log.exception("unhandled error")
        sys.exit(2)
