#!/usr/bin/env python3
# Author: Claude Opus 4.7 (1M context)
# Date: 19-April-2026
# PURPOSE: One-shot CLI to add/remove/list a camera across BOTH Guardian's
#          config.json and tools/pipeline/config.json atomically. Kills the
#          recurring two-config drift bug that CLAUDE.md warns about
#          ("TWO SEPARATE CONFIG FILES — DO NOT FORGET THE SECOND ONE"):
#          every previous agent who added or moved a camera forgot one of
#          the two files at least once. Supports HTTP-snapshot cameras
#          (the dominant pattern: phones via IP Webcam, USB-cam-host
#          instances, iPhones via Continuity) and RTSP cameras (gwtc-style
#          MediaMTX paths). Pure stdlib so it works without the project
#          venv — `python3 scripts/add-camera.py ...` from anywhere.
# SRP/DRY check: Pass — one responsibility, "atomically maintain the two
#          camera configs." Reuses no Guardian internals; no new deps.
#          Plan + walkthrough: docs/19-Apr-2026-add-camera-cli.md.

"""
add-camera.py — atomic camera add/remove/list across both Guardian configs.

Usage:
  scripts/add-camera.py add NAME --url URL [--interval N] [--detect] [--context "..."] [--no-probe]
  scripts/add-camera.py add NAME --rtsp URL [--interval N] [--detect] [--context "..."]
  scripts/add-camera.py remove NAME
  scripts/add-camera.py list

Examples:
  # Old Android phone running IP Webcam (the cheapest high-quality camera path)
  scripts/add-camera.py add brooder-phone --url http://192.168.0.55:8080/photo.jpg \\
      --interval 5 --context "Pixel 4a in the brooder, USB-tethered for power"

  # Second iPhone over Continuity Camera (must already have a *-cam-host LaunchAgent
  # serving on the chosen port; --no-probe because the device may not be plugged in yet)
  scripts/add-camera.py add boss-ipad --url http://127.0.0.1:8092/photo.jpg \\
      --interval 10 --no-probe --context "Boss's iPad over Continuity, opportunistic"

  # New RTSP camera (Reolink, MediaMTX path, etc.)
  scripts/add-camera.py add coop-roof-cam --rtsp rtsp://192.168.0.99:8554/roof \\
      --interval 5 --context "Reolink E1 mounted on the coop roof, sky-watching"

  # Decommission a camera
  scripts/add-camera.py remove old-mba-cam

  # Audit: which cameras are in which config? Detects drift.
  scripts/add-camera.py list

After add/remove, restart the two services so they re-read the configs:
  launchctl kickstart -k gui/$(id -u)/com.farmguardian.guardian
  launchctl kickstart -k gui/$(id -u)/com.farmguardian.pipeline
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARDIAN_CONFIG = REPO_ROOT / "config.json"
PIPELINE_CONFIG = REPO_ROOT / "tools" / "pipeline" / "config.json"


def _load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write to a sibling .tmp then rename, so a half-written file is never
    visible to a concurrently-reading Guardian/pipeline process."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    tmp.replace(path)


def _probe(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Probe an HTTP-snapshot URL. 200 = live frame; 503 = service is up
    but the underlying device is currently absent (normal for opportunistic
    cameras like an iPhone on Continuity that's not plugged in yet) — both
    count as 'reachable, schema is correct'. Anything else is a fail."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status} ({resp.headers.get('content-type', '?')})"
    except urllib.error.HTTPError as e:
        if e.code == 503:
            return True, "HTTP 503 (service up, device currently absent — OK for opportunistic cameras)"
        return False, f"HTTP {e.code} {e.reason}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _split_url(url: str) -> tuple[str, str]:
    """('http://host:port/photo.jpg') -> ('http://host:port', '/photo.jpg').
    Guardian wants base + path separately; the pipeline wants the same split
    under different key names. One helper, two consumers."""
    parsed = urllib.parse.urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"URL {url!r} is missing a scheme or host")
    base = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path or "/photo.jpg"
    return base, path


def cmd_add(args: argparse.Namespace) -> int:
    guardian = _load_json(GUARDIAN_CONFIG)
    pipeline = _load_json(PIPELINE_CONFIG)

    existing_g = {c["name"] for c in guardian["cameras"]}
    existing_p = set(pipeline["cameras"].keys())
    if args.name in existing_g or args.name in existing_p:
        print(
            f"camera {args.name!r} already exists "
            f"(guardian={'yes' if args.name in existing_g else 'no'}, "
            f"pipeline={'yes' if args.name in existing_p else 'no'}). "
            f"Run `remove {args.name}` first, or pick a different name.",
            file=sys.stderr,
        )
        return 2

    if args.url:
        try:
            base, path = _split_url(args.url)
        except ValueError as e:
            print(f"bad --url: {e}", file=sys.stderr)
            return 2

        if not args.no_probe:
            ok, detail = _probe(args.url)
            if not ok:
                print(f"probe failed for {args.url}: {detail}", file=sys.stderr)
                print(
                    "re-run with --no-probe if the device isn't ready yet "
                    "(e.g., adding an opportunistic Continuity Camera ahead "
                    "of time, or the host service hasn't been installed yet).",
                    file=sys.stderr,
                )
                return 3
            print(f"probe ok: {detail}")

        guardian_entry = {
            "name": args.name,
            "type": "fixed",
            "source": "snapshot",
            "snapshot_method": "http_url",
            "http_base_url": base,
            "http_photo_path": path,
            "http_trigger_focus": False,
            "snapshot_interval": float(args.interval),
            "detection_enabled": bool(args.detect),
        }
        # Pipeline cadence floors at 5s — burning the VLM budget on a 1s
        # cadence is wasteful even for the fastest source.
        pipeline_entry = {
            "cycle_seconds": max(float(args.interval), 5.0),
            "capture_method": "ip_webcam",
            "ip_webcam_base": base,
            "photo_path": path,
            "trigger_focus": False,
            "context": args.context or f"Added via scripts/add-camera.py — fill this in with what {args.name} sees and any host quirks.",
            "burst_size": 1,
            "enabled": True,
        }
    elif args.rtsp:
        guardian_entry = {
            "name": args.name,
            "type": "fixed",
            "rtsp_transport": "tcp",
            "rtsp_url_override": args.rtsp,
            "snapshot_interval": float(args.interval),
            "detection_enabled": bool(args.detect),
        }
        pipeline_entry = {
            "cycle_seconds": max(float(args.interval), 10.0),
            # Pipeline pulls Guardian's snapshot API for RTSP cameras —
            # Guardian's ring buffer rides through transient publisher hiccups
            # in a way a direct RTSP pull from the pipeline cannot. See the
            # gwtc context note in tools/pipeline/config.json for background.
            "capture_method": "reolink_snapshot",
            "context": args.context or f"RTSP camera {args.name} — pipeline pulls via Guardian's snapshot API, not RTSP direct. Fill this in with what it sees.",
            "burst_size": 1,
            "motion_gate": False,
            "enabled": True,
        }
    else:  # argparse enforces this; defensive belt-and-braces
        print("specify --url or --rtsp", file=sys.stderr)
        return 2

    guardian["cameras"].append(guardian_entry)
    pipeline["cameras"][args.name] = pipeline_entry

    _atomic_write_json(GUARDIAN_CONFIG, guardian)
    _atomic_write_json(PIPELINE_CONFIG, pipeline)

    print(f"added {args.name!r} to:")
    print(f"  {GUARDIAN_CONFIG.relative_to(REPO_ROOT)}")
    print(f"  {PIPELINE_CONFIG.relative_to(REPO_ROOT)}")
    print()
    print("next steps:")
    print("  launchctl kickstart -k gui/$(id -u)/com.farmguardian.guardian")
    print("  launchctl kickstart -k gui/$(id -u)/com.farmguardian.pipeline")
    print(f"  curl -s http://localhost:6530/api/cameras/{args.name}/frame -o /tmp/{args.name}.jpg")
    print()
    print("Then update HARDWARE_INVENTORY.md with a row for this camera —")
    print("the configs alone don't tell the next assistant what hardware it is.")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    guardian = _load_json(GUARDIAN_CONFIG)
    pipeline = _load_json(PIPELINE_CONFIG)

    before_g = len(guardian["cameras"])
    guardian["cameras"] = [c for c in guardian["cameras"] if c["name"] != args.name]
    removed_g = before_g - len(guardian["cameras"]) > 0
    removed_p = pipeline["cameras"].pop(args.name, None) is not None

    if not removed_g and not removed_p:
        print(f"camera {args.name!r} was not present in either config", file=sys.stderr)
        return 1

    _atomic_write_json(GUARDIAN_CONFIG, guardian)
    _atomic_write_json(PIPELINE_CONFIG, pipeline)
    print(f"removed {args.name!r}: guardian={'yes' if removed_g else 'no'} pipeline={'yes' if removed_p else 'no'}")
    print()
    print("next:")
    print("  launchctl kickstart -k gui/$(id -u)/com.farmguardian.guardian")
    print("  launchctl kickstart -k gui/$(id -u)/com.farmguardian.pipeline")
    print()
    print("If the camera had a dedicated *-cam-host LaunchAgent, decommission")
    print("it too: launchctl bootout gui/$(id -u)/<label> AND rename the .plist")
    print("out of ~/Library/LaunchAgents/ (auto-load trap — see MEMORY.md).")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    guardian = _load_json(GUARDIAN_CONFIG)
    pipeline = _load_json(PIPELINE_CONFIG)
    g_names = [c["name"] for c in guardian["cameras"]]
    p_names = list(pipeline["cameras"].keys())
    all_names = sorted(set(g_names) | set(p_names))

    print(f"{'name':20s} {'guardian':10s} {'pipeline':10s}")
    print("-" * 42)
    for n in all_names:
        in_g = "yes" if n in g_names else "MISSING"
        in_p = "yes" if n in p_names else "MISSING"
        print(f"{n:20s} {in_g:10s} {in_p:10s}")

    drifted = [n for n in all_names if (n in g_names) != (n in p_names)]
    if drifted:
        print()
        print(f"WARNING: {len(drifted)} camera(s) are in only one config: {drifted}")
        print("Drift like this is the bug this script exists to prevent —")
        print("Guardian and the pipeline will see different fleets until you fix it.")
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add", help="add a new camera to both configs atomically")
    add.add_argument("name", help="camera name (device, never location — see HARDWARE_INVENTORY.md)")
    src = add.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="HTTP snapshot URL, e.g. http://192.168.0.55:8080/photo.jpg")
    src.add_argument("--rtsp", help="RTSP URL, e.g. rtsp://192.168.0.99:8554/stream")
    add.add_argument("--interval", type=float, default=10.0,
                     help="Guardian snapshot cadence in seconds (default 10). "
                          "Pipeline cadence floors at 5s for HTTP / 10s for RTSP.")
    add.add_argument("--detect", action="store_true",
                     help="enable YOLO predator detection on this camera (default off)")
    add.add_argument("--context", help="freeform context string for the pipeline (what this camera sees, host quirks, etc.)")
    add.add_argument("--no-probe", action="store_true",
                     help="skip URL reachability check — use when adding an opportunistic camera "
                          "whose host service may not be running yet")
    add.set_defaults(func=cmd_add)

    rm = sub.add_parser("remove", help="remove a camera from both configs atomically")
    rm.add_argument("name")
    rm.set_defaults(func=cmd_remove)

    ls = sub.add_parser("list", help="show all cameras with drift detection")
    ls.set_defaults(func=cmd_list)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
