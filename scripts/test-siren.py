#!/usr/bin/env python3
# Author: Claude Opus 4.7 — Bubba coding sub-agent (live siren hardware-test tool)
# Date: 22-June-2026 (v2.44.0 — standalone manual siren test for the dormant motion-siren feature)
# PURPOSE: Standalone, one-shot hardware test for a Reolink camera's onboard siren. Fires the
#          siren on EXACTLY ONE camera ONCE via the SAME production code path the dormant
#          motion-siren deterrent uses — CameraController.siren_timed() (reolink-aio set_siren,
#          non-blocking auto-off) — and prints success/failure. This is the live test the Boss
#          runs by hand to confirm the hardware works BEFORE flipping motion_siren.enabled true;
#          it makes REAL NOISE, so it is never invoked automatically by Guardian.
#          Loads config.json + the CAMERA_PASSWORD secret the same way guardian.load_config does
#          (dotenv overlay), connects ONLY the requested camera (so no second connection pool is
#          opened against the whole fleet), fires once, waits for the auto-off, then exits.
#          Usage:  python scripts/test-siren.py <camera_id> [duration_seconds]
#          Example: python scripts/test-siren.py house-yard 10
# SRP/DRY check: Pass — reuses CameraController.siren_timed (no raw AudioAlarmPlay / requests
#          call) and mirrors guardian.load_config's CAMERA_PASSWORD env overlay rather than
#          inventing a new siren path. Single responsibility: fire one siren once for a manual test.

import json
import sys
import time
from pathlib import Path

# Repo root is the parent of scripts/. Add it to sys.path so the production
# camera_control module (and its reolink-aio plumbing) is importable unchanged.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402
import os  # noqa: E402

from camera_control import CameraController  # noqa: E402

CONFIG_PATH = REPO_ROOT / "config.json"


def _load_config() -> dict:
    """Load config.json and overlay the CAMERA_PASSWORD secret from .env / env.

    Mirrors guardian.load_config's secret overlay (it replaces placeholder/blank camera
    passwords with $CAMERA_PASSWORD) so this tool authenticates exactly as the daemon does.
    """
    load_dotenv(REPO_ROOT / ".env")
    with open(CONFIG_PATH, "r") as handle:
        config = json.load(handle)

    env_camera_pw = os.environ.get("CAMERA_PASSWORD")
    if env_camera_pw:
        for cam in config.get("cameras", []):
            if not cam.get("password") or "YOUR_" in cam.get("password", ""):
                cam["password"] = env_camera_pw
    return config


def _find_camera(config: dict, camera_id: str) -> dict:
    for cam in config.get("cameras", []):
        if cam.get("name") == camera_id:
            return cam
    return {}


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python scripts/test-siren.py <camera_id> [duration_seconds]")
        return 2

    camera_id = sys.argv[1]
    try:
        duration = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    except ValueError:
        print(f"ERROR: duration must be a number, got '{sys.argv[2]}'")
        return 2

    config = _load_config()
    cam_cfg = _find_camera(config, camera_id)
    if not cam_cfg:
        names = [c.get("name") for c in config.get("cameras", [])]
        print(f"ERROR: camera '{camera_id}' not found in config.json. Known: {names}")
        return 1

    ip = cam_cfg.get("ip", "")
    username = cam_cfg.get("username", "admin")
    password = cam_cfg.get("password", "")
    port = cam_cfg.get("port", 80)

    if not ip:
        print(
            f"ERROR: camera '{camera_id}' has no 'ip' in config (type="
            f"{cam_cfg.get('type')!r}). A Reolink IP+credentials are required to drive the "
            f"siren; this camera is not wired for siren control."
        )
        return 1

    controller = CameraController(config)
    try:
        print(f"Connecting to '{camera_id}' at {ip}:{port} ...")
        if not controller.connect_camera(
            camera_id=camera_id, ip=ip, username=username, password=password, port=port
        ):
            print(f"FAILURE: could not connect to '{camera_id}' — check IP/credentials/network.")
            return 1

        print(f"Firing siren on '{camera_id}' for {duration:.0f}s (auto-off) ...")
        fired = controller.siren_timed(camera_id, duration)
        if not fired:
            print(f"FAILURE: siren_timed returned False for '{camera_id}'.")
            return 1

        # Hold the process open until the non-blocking auto-off has run, so the siren
        # actually stops before the controller's event loop is torn down on exit.
        time.sleep(duration + 2)
        print(f"SUCCESS: siren fired and auto-off completed on '{camera_id}'.")
        return 0
    finally:
        try:
            controller.disconnect_camera(camera_id)
        except Exception:
            pass
        controller.close()


if __name__ == "__main__":
    raise SystemExit(main())
