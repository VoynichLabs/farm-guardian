# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Append-only rolling-24h publish ledger. Single source of
#          truth for "how much of the 25-per-24h Instagram Graph
#          quota has the Mac Mini burned." Both the gem lane and the
#          archive lane append to this ledger after every successful
#          publish so the publisher can make quota-aware decisions.
#
#          Format: newline-delimited JSON (NDJSON). Each line is a
#          self-contained record; the file is append-only from
#          multiple processes' perspective, though in practice the
#          LaunchAgent serialises the writer.
#
#          48h retention: entries older than 48h are pruned on every
#          write so the scan for "publishes in the last 24h" stays
#          linear over a bounded file.
#
# SRP/DRY check: Pass — single responsibility is "read/write the
#                publish ledger." Does not make publish decisions,
#                does not hit Graph API, does not know about gems or
#                archive UUIDs.

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger("tools.social.ledger")


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_iso(ts: str) -> Optional[dt.datetime]:
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def append(
    ledger_path: Path,
    lane: str,
    identifier: str,
    ig_media_id: Optional[str] = None,
    fb_post_id: Optional[str] = None,
) -> None:
    """Record one successful publish. `lane` is "gem" or "archive".
    `identifier` is the gem_id (int, stringified) or the Photos UUID.
    At least one of ig_media_id/fb_post_id should be non-None — a call
    with both None is tolerated (logs a warning) since a partial-
    lane failure still consumes quota at the platform end.
    """
    if not ig_media_id and not fb_post_id:
        log.warning(
            "ledger.append: neither ig_media_id nor fb_post_id set — "
            "recording anyway so the quota counter stays honest",
        )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _now_utc().isoformat(),
        "lane": lane,
        "id": identifier,
        "ig_media_id": ig_media_id,
        "fb_post_id": fb_post_id,
    }
    # Append with O_APPEND so concurrent writes (extremely unlikely —
    # the LaunchAgent runs one-at-a-time — but defensive) are atomic
    # at the POSIX level for small writes.
    line = json.dumps(entry) + "\n"
    fd = os.open(str(ledger_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def count_last_24h(ledger_path: Path, platform: str = "ig") -> int:
    """Return the number of publishes in the last 24 hours to the
    given platform. platform ∈ {"ig","fb","any"}.

    Missing ledger → 0. Any line we can't parse → skipped (counted
    as zero rather than raising, so a corrupt line doesn't wedge the
    publisher)."""
    if not ledger_path.exists():
        return 0
    cutoff = _now_utc() - dt.timedelta(hours=24)
    count = 0
    with ledger_path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ts = _parse_iso(entry.get("ts", ""))
            if ts is None or ts < cutoff:
                continue
            if platform == "ig" and not entry.get("ig_media_id"):
                continue
            if platform == "fb" and not entry.get("fb_post_id"):
                continue
            count += 1
    return count


def prune_older_than(ledger_path: Path, hours: int = 48) -> int:
    """Rewrite the ledger without entries older than `hours`. Returns
    the number of entries dropped. Safe to call on a missing ledger.
    Atomic: writes to temp + rename."""
    if not ledger_path.exists():
        return 0
    cutoff = _now_utc() - dt.timedelta(hours=hours)
    kept: list[str] = []
    dropped = 0
    with ledger_path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                # Don't drop unparseable lines — a future bugfix might
                # recover them. Keep them, they're cheap.
                kept.append(line)
                continue
            ts = _parse_iso(entry.get("ts", ""))
            if ts is None or ts >= cutoff:
                kept.append(line)
            else:
                dropped += 1
    if dropped == 0:
        return 0
    fd, tmp_path = tempfile.mkstemp(
        prefix=".publish_ledger.", suffix=".tmp", dir=str(ledger_path.parent),
    )
    os.close(fd)
    tmp = Path(tmp_path)
    tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    tmp.replace(ledger_path)
    log.info("ledger.prune: dropped %d entries older than %dh", dropped, hours)
    return dropped


__all__ = ["append", "count_last_24h", "prune_older_than"]
