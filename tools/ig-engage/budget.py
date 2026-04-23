# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: UTC-day budget tracker for the IG engager. Persists per-day counts
#          of likes, comments, and story reactions to a JSON file so budgets
#          survive across multiple sessions per day. Also exposes the kill
#          switch (/tmp/ig-engage-off) and the challenge cooldown flag
#          (/tmp/ig-engage-cooldown-until) as first-class helpers — nothing
#          else in the engager should open those files directly.
#
# SRP/DRY check: Pass — one module, one responsibility: "is this action within
#                budget right now, and if not why not?"

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

DATA_DIR = Path("/Users/macmini/Documents/GitHub/farm-guardian/data/ig-engage")
KILL_SWITCH = Path("/tmp/ig-engage-off")
COOLDOWN_FLAG = Path("/tmp/ig-engage-cooldown-until")

ActionKind = Literal["like", "comment", "story_react"]

DEFAULT_CAPS = {
    "like": 30,
    "comment": 10,
    "story_react": 20,
}


@dataclass
class BudgetState:
    day: str  # YYYY-MM-DD in UTC
    counts: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"day": self.day, "counts": self.counts}


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _state_path() -> Path:
    return DATA_DIR / "budget.json"


def load_state() -> BudgetState:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = _state_path()
    today = today_utc()
    if not p.exists():
        return BudgetState(day=today)
    try:
        raw = json.loads(p.read_text())
        if raw.get("day") != today:
            # Stale day — reset to zeroes.
            return BudgetState(day=today)
        return BudgetState(day=raw["day"], counts=dict(raw.get("counts", {})))
    except Exception:
        return BudgetState(day=today)


def save_state(state: BudgetState) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(json.dumps(state.as_dict(), indent=2))


def remaining(state: BudgetState, kind: ActionKind, caps: dict[str, int] | None = None) -> int:
    caps = caps or DEFAULT_CAPS
    used = state.counts.get(kind, 0)
    return max(0, caps[kind] - used)


def record(state: BudgetState, kind: ActionKind) -> None:
    state.counts[kind] = state.counts.get(kind, 0) + 1
    save_state(state)


def kill_switch_on() -> bool:
    return KILL_SWITCH.exists()


def in_cooldown() -> tuple[bool, int]:
    """Returns (in_cooldown, epoch_end). If the flag file is missing or stale
    (past epoch), we are not in cooldown."""
    if not COOLDOWN_FLAG.exists():
        return (False, 0)
    try:
        end = int(COOLDOWN_FLAG.read_text().strip())
    except Exception:
        return (False, 0)
    now = int(time.time())
    return (now < end, end)


def set_cooldown(seconds_from_now: int) -> int:
    """Writes the cooldown flag; returns the epoch it expires at."""
    end = int(time.time()) + seconds_from_now
    COOLDOWN_FLAG.write_text(str(end))
    return end


def clear_cooldown() -> None:
    if COOLDOWN_FLAG.exists():
        COOLDOWN_FLAG.unlink()
