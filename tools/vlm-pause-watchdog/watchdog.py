#!/usr/bin/env python3
"""
Author: Claude Opus 5
Date: 17-September-2026
PURPOSE: Pauses VLM enrichment in the image pipeline while Boss's evolve.py
         research job is actually burning CPU, and resumes it when that job
         goes idle. Control plane is the orchestrator's EXISTING flag file
         /tmp/farm-pipeline.pause (tools/pipeline/orchestrator.py:125) — when
         present, run_cycle() returns status="paused" before calling the VLM,
         while _run_raw_camera_thread keeps archiving frames on its own thread.
         Nothing here stops a LaunchAgent and nothing here touches LM Studio
         model state: the model stays resident so Guardian's read-only night
         alert verifier (llm_verify.py) keeps working while enrichment is
         paused. Invoked every 3 minutes by com.farmguardian.vlm-pause-watchdog.
         Dependencies: stdlib only (subprocess/ps). No Discord posting, so the
         urllib User-Agent trap documented in CLAUDE.md does not apply.
SRP/DRY check: Pass — reuses the existing pause flag rather than adding a
         second control path or a new config key; verified no other watchdog
         owns this flag (grep for _PAUSE_FLAG / farm-pipeline.pause).
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

# The orchestrator's own pause flag. Do NOT invent a second one.
PAUSE_FLAG = Path("/tmp/farm-pipeline.pause")

# State for hysteresis. Survives ticks; a reboot resets it, which is safe
# because an empty streak simply means "wait for confirmation before acting".
STATE_FILE = Path("/tmp/farm-vlm-pause-watchdog.state")

# Matches the research job's command line. The PID changes between runs
# (43622 -> 68270 within one day), so this is matched fresh every tick.
EVOLVE_PATTERN = "kaggriculture/lab/evolve.py"

# Summed CPU% across the whole evolve process tree. Its six workers measured
# ~87% each (~520% total) while the parent sat at 0.0%, so this threshold is
# far above idle noise and far below a real run.
BUSY_CPU_PERCENT = 150.0

# Consecutive agreeing ticks before flipping. Asymmetric on purpose: pause
# quickly when the job ramps up, resume conservatively so the pipeline does
# not flap across evolve's generation boundaries.
TICKS_TO_PAUSE = 2
TICKS_TO_RESUME = 3

# Written by this watchdog only. Distinguishes "we paused for evolve" from a
# pause a human or the dashboard set, so we never resume someone else's pause.
OWNER_MARKER = "paused-by-vlm-pause-watchdog"

log = logging.getLogger("vlm-pause-watchdog")


def evolve_cpu_percent() -> float:
    """Total CPU% across every evolve process (parent + multiprocessing
    workers). Raises on probe failure — the caller treats that as "unknown"
    and holds state, because a malformed probe that reads as 0.0 would look
    exactly like an idle job and wrongly resume the VLM. A bad `pgrep -c`
    did precisely that on 17-Sep-2026.
    """
    # `ps -Ao pcpu,command` then match in Python: no pgrep flag portability
    # traps, and we see the full command line to filter on.
    proc = subprocess.run(
        ["ps", "-Ao", "pcpu,command"],
        capture_output=True, text=True, timeout=30, check=True,
    )
    total = 0.0
    matched = 0
    for line in proc.stdout.splitlines():
        if EVOLVE_PATTERN not in line:
            continue
        # Skip our own ps/grep-ish artifacts and this watchdog itself.
        if "vlm-pause-watchdog" in line:
            continue
        head, _, _rest = line.strip().partition(" ")
        try:
            total += float(head)
        except ValueError:
            # A command line wrapping onto a second row has no leading pcpu
            # field; ignore it rather than aborting the whole probe.
            continue
        matched += 1
    log.debug("evolve processes=%d cpu=%.1f%%", matched, total)
    return total


def read_state() -> tuple[str, int]:
    """Returns (last_observation, consecutive_count). Unreadable or malformed
    state resets to a neutral streak rather than raising — worst case we wait
    one extra tick before acting.
    """
    try:
        raw = STATE_FILE.read_text().strip().split()
        return raw[0], int(raw[1])
    except (OSError, IndexError, ValueError):
        return "unknown", 0


def write_state(observation: str, count: int) -> None:
    try:
        STATE_FILE.write_text(f"{observation} {count}\n")
    except OSError as exc:
        # Losing the streak only costs an extra confirmation tick, so this is
        # a warning rather than a failure.
        log.warning("could not persist state: %s", exc)


def we_own_the_pause() -> bool:
    """True when the current pause was set by this watchdog. A pause set by a
    human or the dashboard has different (or no) contents, and we leave it
    alone — resuming someone else's deliberate pause would be the rudest
    possible failure mode.
    """
    try:
        return OWNER_MARKER in PAUSE_FLAG.read_text()
    except OSError:
        return False


def pause() -> None:
    if PAUSE_FLAG.exists():
        return
    try:
        PAUSE_FLAG.write_text(
            f"{OWNER_MARKER}\nevolve.py is busy; VLM enrichment paused.\n"
            "Frame archiving and Guardian are unaffected. Remove this file to "
            "resume immediately.\n"
        )
        log.info("evolve busy — VLM enrichment PAUSED (archiving continues)")
    except OSError as exc:
        log.error("could not create pause flag: %s", exc)


def resume() -> None:
    if not PAUSE_FLAG.exists():
        return
    if not we_own_the_pause():
        log.info("pause flag was set by someone else — leaving it in place")
        return
    try:
        PAUSE_FLAG.unlink()
        log.info("evolve idle — VLM enrichment RESUMED")
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.error("could not remove pause flag: %s", exc)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        cpu = evolve_cpu_percent()
    except (subprocess.SubprocessError, OSError) as exc:
        # Unknown state: hold whatever we have. Never guess "idle".
        log.error("CPU probe failed (%s) — holding current state", exc)
        return 1

    observation = "busy" if cpu >= BUSY_CPU_PERCENT else "idle"
    last, count = read_state()
    count = count + 1 if observation == last else 1
    write_state(observation, count)

    # Heartbeat on EVERY tick. Without it the steady state is silent (pause()
    # and resume() both return early when there is nothing to do), and a
    # healthy watchdog is indistinguishable from a dead one in the log —
    # which is the failure mode the s7-settings-watchdog got wrong for weeks.
    log.info("evolve cpu=%.1f%% observation=%s streak=%d paused=%s",
             cpu, observation, count, PAUSE_FLAG.exists())

    if observation == "busy" and count >= TICKS_TO_PAUSE:
        pause()
    elif observation == "idle" and count >= TICKS_TO_RESUME:
        resume()
    return 0


if __name__ == "__main__":
    sys.exit(main())
