# Author: GPT-5.5
# Date: 03-May-2026
# PURPOSE: UTC-day budget tracker for the Nextdoor automation. Mirrors
#          tools/ig-engage/budget.py but with Nextdoor-tuned caps (much
#          lower — Nextdoor audiences punish oversharing harder than IG)
#          and an extra "post" bucket for the weekly cross-post lane.
#
#          Caps (per UTC day unless noted):
#            like          — 10
#            comment       — 3
#            react         — 5   (post-reactions, not story reactions)
#            post_today    — 1   (outbound live-cam reacted gem; 18:30 local)
#            post_throwback — 1  (reserved; throwback lane disabled unless
#                                  FARM_NEXTDOOR_THROWBACK_ENABLED=1)
#
#          Kill switch file: /tmp/nextdoor-off
#          Challenge cooldown file: /tmp/nextdoor-cooldown-until (epoch)
#
# SRP/DRY check: Pass — mirrors the IG budget shape but for Nextdoor. Kept
#                separate (rather than parameterized off a shared module) so
#                cap changes on one platform never accidentally change the
#                other.

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

DATA_DIR = Path("/Users/macmini/Documents/GitHub/farm-guardian/data/nextdoor")
KILL_SWITCH = Path("/tmp/nextdoor-off")
COOLDOWN_FLAG = Path("/tmp/nextdoor-cooldown-until")

ActionKind = Literal["like", "comment", "react", "post_today", "post_throwback"]

DEFAULT_CAPS = {
    "like": 10,
    "comment": 3,
    "react": 5,
    "post_today": 1,
    "post_throwback": 1,
}


@dataclass
class BudgetState:
    day: str
    counts: dict[str, int] = field(default_factory=dict)
    last_post_ts: int = 0  # epoch of most recent cross-post, 0 if never

    def as_dict(self) -> dict:
        return {
            "day": self.day,
            "counts": self.counts,
            "last_post_ts": self.last_post_ts,
        }


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
        last_post_ts = int(raw.get("last_post_ts", 0))
        if raw.get("day") != today:
            # New UTC day: reset all daily counters including post_today /
            # post_throwback (each lane gets one fire per day).
            return BudgetState(day=today, last_post_ts=last_post_ts)
        return BudgetState(
            day=raw["day"],
            counts=dict(raw.get("counts", {})),
            last_post_ts=last_post_ts,
        )
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


def record_post(state: BudgetState, now_ts: int | None = None) -> None:
    """Bump last_post_ts for audit (lane caps are tracked via counts)."""
    state.last_post_ts = now_ts or int(time.time())
    save_state(state)


def kill_switch_on() -> bool:
    return KILL_SWITCH.exists()


def in_cooldown() -> tuple[bool, int]:
    if not COOLDOWN_FLAG.exists():
        return (False, 0)
    try:
        end = int(COOLDOWN_FLAG.read_text().strip())
    except Exception:
        return (False, 0)
    return (int(time.time()) < end, end)


def set_cooldown(seconds_from_now: int) -> int:
    end = int(time.time()) + seconds_from_now
    COOLDOWN_FLAG.write_text(str(end))
    return end


def clear_cooldown() -> None:
    if COOLDOWN_FLAG.exists():
        COOLDOWN_FLAG.unlink()
