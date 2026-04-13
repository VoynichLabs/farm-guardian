# Author: Claude Opus 4.6 (1M context)
# Date: 13-April-2026
# PURPOSE: Standalone smoke-test for the S7's IP Webcam HTTP endpoint. Run this
#          BEFORE flipping the config.json s7-cam block in v2.24.0 to verify the
#          phone is actually serving /photo.jpg as expected. Fails loudly with
#          a human-readable diagnosis instead of the generic "snapshot returned
#          None" warning you'd get at Guardian startup. Usage:
#              venv/bin/python tools/s7_http_smoke.py
#          or with overrides:
#              venv/bin/python tools/s7_http_smoke.py --host 192.168.0.249 --port 8080
# SRP/DRY check: Pass — thin diagnostic wrapper around HttpUrlSnapshotSource;
#          does not duplicate its logic, it EXERCISES it end-to-end.

import argparse
import sys
import time
from pathlib import Path

# Make the repo root importable without a package install (tools/ is not on
# sys.path by default when run as a script).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from capture import HttpUrlSnapshotSource  # noqa: E402


def _pp(msg: str, ok: bool = True) -> None:
    prefix = "[OK]  " if ok else "[FAIL]"
    print(f"{prefix} {msg}")


def main() -> int:
    ap = argparse.ArgumentParser(description="S7 IP Webcam HTTP snapshot smoke test")
    ap.add_argument("--host", default="192.168.0.249", help="phone IP (default: 192.168.0.249)")
    ap.add_argument("--port", type=int, default=8080, help="IP Webcam HTTP port (default: 8080)")
    ap.add_argument("--username", default="", help="IP Webcam 'Login' (empty if no auth set)")
    ap.add_argument("--password", default="", help="IP Webcam 'Password' (empty if no auth set)")
    ap.add_argument("--photo-path", default="/photo.jpg")
    ap.add_argument("--focus-path", default="/focus")
    ap.add_argument("--save", default="/tmp/s7-smoke.jpg", help="where to save the test JPEG")
    ap.add_argument("--iterations", type=int, default=3,
                    help="how many consecutive pulls to try (catches stale-preview flaps)")
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"
    auth = (args.username, args.password) if args.username else None
    print(f"Smoke-testing {base}  (auth={'on' if auth else 'off'})\n")

    # 1. Plain fetch, no AF.
    src = HttpUrlSnapshotSource(
        base_url=base, photo_path=args.photo_path,
        trigger_focus=False, timeout=10.0, auth=auth,
    )
    data = src.fetch()
    if not data:
        _pp(f"GET {base}{args.photo_path} failed — no JPEG returned. "
            "Check the phone: IP Webcam's 'Start server' button pressed? "
            f"Browser loads {base}/ ? Correct port ({args.port}) and path "
            f"({args.photo_path}) ? If auth is set on the phone, did you "
            "pass --username and --password to this script?", ok=False)
        return 1
    _pp(f"Basic /photo.jpg fetch ({len(data)} bytes JPEG)")

    # 2. Save the JPEG for visual inspection.
    try:
        Path(args.save).write_bytes(data)
        _pp(f"Wrote test JPEG to {args.save}  (open it to eyeball framing/focus)")
    except OSError as exc:
        _pp(f"Could not write {args.save}: {exc}", ok=False)

    # 3. AF-trigger path.
    src_af = HttpUrlSnapshotSource(
        base_url=base, photo_path=args.photo_path, focus_path=args.focus_path,
        trigger_focus=True, focus_wait=1.5, timeout=15.0, auth=auth,
    )
    t0 = time.monotonic()
    data_af = src_af.fetch()
    dt = time.monotonic() - t0
    if not data_af:
        _pp(f"AF-trigger + photo failed. {args.focus_path} may not exist on "
            "this IP Webcam build — set http_trigger_focus=false in the "
            "config if so.", ok=False)
    else:
        _pp(f"AF trigger + /photo.jpg OK ({len(data_af)} bytes, {dt:.2f}s round-trip)")

    # 4. Consecutive-pull stability.
    failures = 0
    sizes = []
    for i in range(args.iterations):
        d = src.fetch()
        if d:
            sizes.append(len(d))
        else:
            failures += 1
        time.sleep(0.5)
    if failures:
        _pp(f"{failures}/{args.iterations} consecutive pulls failed — the phone "
            "may be flapping (preview not always ready). Consider raising "
            "snapshot_interval to 8-10s.", ok=False)
    else:
        smin, smax = (min(sizes), max(sizes)) if sizes else (0, 0)
        _pp(f"Consecutive {args.iterations} pulls all returned JPEGs "
            f"({smin}–{smax} bytes)")

    print("\nIf everything above is [OK], flip the s7-cam block in config.json "
          "per docs/13-Apr-2026-s7-phone-setup.md Step 4 and restart Guardian.")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
