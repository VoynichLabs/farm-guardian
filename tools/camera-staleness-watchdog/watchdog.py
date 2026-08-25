# Author: Claude Opus 5
# Date: 25-August-2026
# PURPOSE: Alerts when ANY Guardian camera stops producing frames. Fills the gap exposed by
#          the 25-Aug-2026 s7-cam guest-Wi-Fi incident, where a camera was dark for 16.5
#          hours and nothing told anyone: com.farmguardian.birdcatraz-watchdog watches
#          farm-pi5 ONLY and stayed green (135 clean ticks) throughout, because it was —
#          correctly — reporting on a different device.
#
#          LIVENESS SOURCE: Guardian's own /api/cameras `online` field, which as of v2.71.5
#          means "discovered AND producing frames" (CaptureManager.liveness()). This is
#          deliberate DRY: the staleness rule lives in exactly ONE place, so this watchdog
#          and the dashboard can never disagree about whether a camera is up. Do NOT
#          reimplement threshold maths here — fix capture.py::liveness() instead.
#
#          ALERT SHAPE (modelled directly on tools/birdcatraz-watchdog/watchdog.py, which is
#          the house pattern for this): ONE alert per outage, ONE recovery notice, never a
#          stream. State in state.json. A camera list that changes mid-outage (a second
#          camera drops) escalates once rather than re-alerting per tick.
#
#          Mentions Boss ONLY on a total blackout (every camera down, or Guardian itself
#          unreachable), because that means the farm has lost all eyes and is worth waking
#          someone for. A single dead camera logs + posts WITHOUT a mention.
#
# SRP/DRY check: Pass — reuses Guardian's liveness verdict rather than recomputing it, and
#          mirrors birdcatraz-watchdog's proven state/alert/recovery structure. Verified no
#          existing tool watches per-camera staleness: birdcatraz-watchdog probes farm-pi5
#          TCP ports only, s7-settings-watchdog is retired, pipeline-digest is a report.
import argparse
import json
import logging
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"

SERVICE_ROOT = Path(
    os.environ.get(
        "SERVICE_ROOT", os.path.expanduser("~/.local/farm-services/camera-staleness-watchdog")
    )
)
STATE_PATH = SERVICE_ROOT / "state.json"
LOG_PATH = SERVICE_ROOT / "watchdog.log"

MARK_DISCORD_USER_ID = "293569238386606080"

GUARDIAN_URL = os.environ.get("GUARDIAN_URL", "http://localhost:6530/api/cameras")

# Consecutive failed ticks before alerting. Same reasoning as birdcatraz-watchdog: at a
# 5-minute interval, 2 means a real alert lands ~10 minutes in, and a single miss during a
# camera restart is not an outage. Guardian's own liveness already absorbs sub-30s gaps, so
# this is a second, coarser layer of anti-flap on top of it.
FAIL_THRESHOLD = int(os.environ.get("CAMERA_STALENESS_FAIL_THRESHOLD", "2"))
HTTP_TIMEOUT_S = float(os.environ.get("CAMERA_STALENESS_TIMEOUT", "10"))
WEBHOOK_TIMEOUT_S = 10

SERVICE_ROOT.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("camera-staleness-watchdog")


def load_webhook() -> str:
    """Read DISCORD_WEBHOOK_URL from env, falling back to .env.

    launchd hands a job a near-empty environment, so the .env branch is the one that
    actually runs in production; the env var exists for CLI testing.
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


def fetch_camera_state() -> tuple[bool, list[str], list[str]]:
    """Ask Guardian which cameras are live.

    Returns (guardian_reachable, live_names, dead_names). When Guardian itself is
    unreachable we return (False, [], []) — the caller treats that as a total blackout,
    because a dead Guardian means no camera is being recorded regardless of the hardware.
    """
    try:
        req = urllib.request.Request(
            GUARDIAN_URL, headers={"User-Agent": "FarmGuardian-CameraStalenessWatchdog/1.0"}
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError, socket.timeout) as exc:
        log.error("Guardian unreachable at %s: %s", GUARDIAN_URL, exc)
        return False, [], []

    rows = payload if isinstance(payload, list) else payload.get("cameras", [])
    live, dead = [], []
    for row in rows:
        name = row.get("name")
        if not name:
            continue
        # `online` is v2.71.5 liveness (discovered AND producing frames). A camera that is
        # configured but was never discovered is NOT counted as an outage — that is a
        # deliberate config state, not a camera that died.
        if not row.get("discovered", True):
            continue
        (live if row.get("online") else dead).append(name)
    return True, sorted(live), sorted(dead)


# Set by --dry-run. Exists because load_webhook() deliberately falls back to .env, so
# clearing DISCORD_WEBHOOK_URL does NOT make a test run safe — it silently posts to the real
# #farm-2026 channel. That happened once during this tool's own verification (25-Aug-2026)
# and required a retraction. Always test with --dry-run.
DRY_RUN = False


def post_discord(content: str, webhook: str, *, username: str = "farm-cameras") -> bool:
    if DRY_RUN:
        log.info("[dry-run] WOULD post to Discord: %s", content.replace("\n", " | "))
        return True
    if not webhook:
        log.error("no DISCORD_WEBHOOK_URL available; alert NOT sent: %s", content)
        return False
    body = json.dumps({"username": username, "content": content}).encode()
    req = urllib.request.Request(
        webhook,
        data=body,
        headers={
            "Content-Type": "application/json",
            # REQUIRED. Discord's edge 403s urllib's default "Python-urllib/3.x" UA.
            # Verified 13-Aug-2026 on the birdcatraz watchdog; the identical POST succeeds
            # with any custom UA and fails without one. Do not remove this header.
            "User-Agent": "FarmGuardian-CameraStalenessWatchdog/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_S) as resp:
            resp.read()
    except (urllib.error.URLError, OSError, socket.timeout) as exc:
        # Never raise: a failed alert must not kill the watchdog, or one Discord blip
        # would end the monitoring silently.
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
    return {"consecutive_failures": 0, "alerted": False, "alerted_cameras": []}


def save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, indent=2))
    except OSError as exc:
        log.error("could not write state to %s: %s", STATE_PATH, exc)


def build_message(reachable: bool, dead: list[str], live: list[str]) -> str:
    """Compose the outage alert. Mentions Boss only when the farm has lost all eyes."""
    if not reachable:
        return (
            f"<@{MARK_DISCORD_USER_ID}> **Guardian is not responding.**\n"
            f"Nothing is being recorded from any camera. The service on the Mac Mini is "
            f"down or wedged — `launchctl kickstart -k gui/$(id -u)/com.farmguardian.guardian`."
        )
    if live:
        listed = ", ".join(f"`{c}`" for c in dead)
        return (
            f"**Camera down: {listed}**\n"
            f"No frames for several minutes. Still live: {', '.join(f'`{c}`' for c in live)}.\n"
            f"If it is `s7-cam`, check which Wi-Fi the phone is on before anything else — "
            f"the guest SSID looks fine from the phone but is invisible to Guardian "
            f"(see CLAUDE.md)."
        )
    return (
        f"<@{MARK_DISCORD_USER_ID}> **Every camera is down.**\n"
        f"No frames from any of: {', '.join(f'`{c}`' for c in dead)}.\n"
        f"Guardian itself is up, so this is a network or power fault, not the service."
    )


def main() -> int:
    global DRY_RUN
    parser = argparse.ArgumentParser(description="Alert when a Guardian camera stops producing frames.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Log what would be posted instead of posting. USE THIS FOR ALL TESTING — "
             "clearing DISCORD_WEBHOOK_URL is not enough, the .env fallback still fires.",
    )
    DRY_RUN = parser.parse_args().dry_run

    webhook = load_webhook()
    state = load_state()
    reachable, live, dead = fetch_camera_state()

    healthy = reachable and not dead
    if healthy:
        if state.get("alerted"):
            post_discord(
                f"✅ Cameras recovered — all {len(live)} live again "
                f"({', '.join(f'`{c}`' for c in live)}).",
                webhook,
            )
        save_state({"consecutive_failures": 0, "alerted": False, "alerted_cameras": []})
        log.info("all cameras live: %s", ", ".join(live))
        return 0

    failures = int(state.get("consecutive_failures", 0)) + 1
    # Escalate if the outage GREW (another camera dropped) — one extra alert, not a stream.
    grew = state.get("alerted") and sorted(state.get("alerted_cameras", [])) != dead
    already = state.get("alerted") and not grew

    log.warning(
        "unhealthy (consecutive=%d): guardian_reachable=%s dead=%s live=%s",
        failures, reachable, ",".join(dead) or "-", ",".join(live) or "-",
    )

    if failures >= FAIL_THRESHOLD and not already:
        if post_discord(build_message(reachable, dead, live), webhook):
            state["alerted"] = True
            state["alerted_cameras"] = dead
    state["consecutive_failures"] = failures
    save_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
