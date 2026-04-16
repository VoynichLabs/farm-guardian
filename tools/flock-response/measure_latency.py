"""
Author: Claude Opus 4.7 (1M context) — Bubba
Date: 16-April-2026
PURPOSE: Measure the Mac-Mini -> SSH -> GWTC -> PowerShell -> audio-out
round-trip latency for the flock acoustic-response study. Plays the bundled
Windows `tada.wav` N times and reports median / p95 / min / max wall-clock
durations as JSON.

The output median goes into the experiment runner's `meta.json:
playback_latency_ms` per trial as a known offset. Re-run after any change
to GWTC's WiFi link, the SSH path, or the playback toolchain.

Pure stdlib; runs from the repo venv or the system Python 3.11+.

SRP/DRY check: Pass — wraps `playback.play()`, adds only the loop and stats.
No existing latency tool in the repo.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import asdict, dataclass

from playback import (
    DEFAULT_HOST,
    DEFAULT_REMOTE_PATH,
    DEFAULT_TIMEOUT_S,
    DEFAULT_USER,
    play,
)


@dataclass
class LatencySummary:
    n: int
    n_ok: int
    remote_path: str
    host: str
    wall_clock_s: list[float]
    median_s: float
    p95_s: float
    min_s: float
    max_s: float
    failures: list[dict]


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolated percentile for 0 < pct < 100."""
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo])


def measure(
    n: int,
    remote_path: str = DEFAULT_REMOTE_PATH,
    host: str = DEFAULT_HOST,
    user: str = DEFAULT_USER,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> LatencySummary:
    walls: list[float] = []
    failures: list[dict] = []
    for i in range(n):
        result = play(remote_path=remote_path, host=host, user=user, timeout_s=timeout_s)
        if result.ok:
            walls.append(result.wall_clock_s)
        else:
            failures.append({
                "trial_index": i,
                "wall_clock_s": result.wall_clock_s,
                "returncode": result.returncode,
                "stderr": result.stderr,
            })

    walls_sorted = sorted(walls)
    return LatencySummary(
        n=n,
        n_ok=len(walls),
        remote_path=remote_path,
        host=host,
        wall_clock_s=walls,
        median_s=round(statistics.median(walls), 4) if walls else float("nan"),
        p95_s=round(_percentile(walls_sorted, 95.0), 4) if walls else float("nan"),
        min_s=round(min(walls), 4) if walls else float("nan"),
        max_s=round(max(walls), 4) if walls else float("nan"),
        failures=failures,
    )


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure Mini->GWTC playback round-trip latency over N trials.",
    )
    parser.add_argument("--n", type=int, default=10, help="number of playback trials (default 10)")
    parser.add_argument("--remote-path", default=DEFAULT_REMOTE_PATH)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    args = parser.parse_args()

    summary = measure(
        n=args.n,
        remote_path=args.remote_path,
        host=args.host,
        user=args.user,
        timeout_s=args.timeout_s,
    )
    print(json.dumps(asdict(summary), indent=2))
    # Non-zero exit if any failures so a CI / scripted caller notices.
    return 0 if not summary.failures else 2


if __name__ == "__main__":
    sys.exit(_main())
