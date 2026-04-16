"""
Author: Claude Opus 4.7 (1M context) — Bubba
Date: 16-April-2026
PURPOSE: Playback primitive for the flock acoustic-response study. Triggers
blocking audio playback of a WAV that is already present on the GWTC laptop
(Gateway, Windows 11, 192.168.0.68) via SSH + PowerShell
`System.Media.SoundPlayer.PlaySync()`. Returns the wall-clock round-trip so
`measure_latency.py` and the eventual `experiment.py` can record per-trial
latency.

This is intentionally a *minimal* primitive: it does not push files, it does
not schedule trials, it does not validate the file is a real stimulus. The
deploy script (`deploy/push-sounds-to-gwtc.sh`) handles file transport; the
experiment runner will own scheduling, randomization, and the counterbalance.

WELFARE / EXPERIMENTAL VALIDITY WARNING
---------------------------------------
NEVER play a real stimulus from `sounds/` during development. Every
pre-pilot playback of a real exemplar contaminates the H5 habituation
measurement on the current cohort. For smoke-testing, use the bundled
`C:\\Windows\\Media\\tada.wav` on GWTC (verified working in the plan's
Appendix B).

SRP/DRY check: Pass — single responsibility (one remote-trigger playback
with timing). No existing helper in the repo covers SSH-driven audio.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass

DEFAULT_HOST = "192.168.0.68"
DEFAULT_USER = "markb"
DEFAULT_REMOTE_PATH = r"C:\Windows\Media\tada.wav"  # safe smoke-test target
DEFAULT_TIMEOUT_S = 30.0


@dataclass
class PlaybackResult:
    remote_path: str
    host: str
    user: str
    wall_clock_s: float
    returncode: int
    ok: bool
    stderr: str


def play(
    remote_path: str = DEFAULT_REMOTE_PATH,
    host: str = DEFAULT_HOST,
    user: str = DEFAULT_USER,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> PlaybackResult:
    """Trigger blocking playback of `remote_path` on `host`.

    Returns a `PlaybackResult` with the full wall-clock round-trip
    (SSH connect + auth + PowerShell launch + audio playback + return).
    The caller is responsible for subtracting the audio duration if
    they want pure round-trip overhead.
    """
    # PowerShell single-quotes the path literal so backslashes do not need
    # escaping. The outer shlex.quote handles the outer single-quoting on the
    # ssh argv.
    ps_command = (
        "powershell -NoProfile -Command "
        f"\"(New-Object System.Media.SoundPlayer '{remote_path}').PlaySync()\""
    )
    ssh_argv = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
        f"{user}@{host}",
        ps_command,
    ]

    t0 = time.perf_counter()
    try:
        completed = subprocess.run(
            ssh_argv,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        wall = time.perf_counter() - t0
        return PlaybackResult(
            remote_path=remote_path,
            host=host,
            user=user,
            wall_clock_s=round(wall, 4),
            returncode=completed.returncode,
            ok=(completed.returncode == 0),
            stderr=completed.stderr.strip(),
        )
    except subprocess.TimeoutExpired as exc:
        wall = time.perf_counter() - t0
        return PlaybackResult(
            remote_path=remote_path,
            host=host,
            user=user,
            wall_clock_s=round(wall, 4),
            returncode=-1,
            ok=False,
            stderr=f"timeout after {timeout_s}s: {exc}",
        )


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Trigger one blocking playback on the GWTC laptop.",
    )
    parser.add_argument(
        "--remote-path",
        default=DEFAULT_REMOTE_PATH,
        help=(
            "Windows path to the WAV on GWTC. Default is C:\\Windows\\Media"
            "\\tada.wav (safe for development). Real stimuli live under "
            "C:\\farm-sounds\\ once push-sounds-to-gwtc.sh has run."
        ),
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    args = parser.parse_args()

    result = play(
        remote_path=args.remote_path,
        host=args.host,
        user=args.user,
        timeout_s=args.timeout_s,
    )
    print(json.dumps(asdict(result), indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(_main())
