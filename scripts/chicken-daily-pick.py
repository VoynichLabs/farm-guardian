#!/opt/homebrew/bin/python3
"""
Author: Claude Sonnet 4.6 (Bubba)
Date: 22-May-2026
PURPOSE: Daily Chicken Picks update. Runs at market open (09:30 ET, Mon-Fri).
         Selects today's ticker from the 5-stock basket via weekday rotation,
         fetches a live quote from Yahoo Finance, prepends a new pick entry to
         content/markets/picks.json, refreshes realStocks for all 5 names,
         then commits + pushes so Railway redeploys.

         Weekday rotation: Mon=TSN, Tue=PPC, Wed=ADM, Thu=MOO, Fri=CORN
         Skips NYSE holidays (2026 list hardcoded).
         Installed at ~/bin/ following com.farmguardian.* plist pattern.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

SITE_REPO = Path.home() / "GitHub/farm-2026"
PICKS_JSON = SITE_REPO / "content/markets/picks.json"

# Weekday (0=Mon) → ticker
ROTATION = {0: "TSN", 1: "PPC", 2: "ADM", 3: "MOO", 4: "CORN"}

TICKER_META = {
    "TSN": {
        "company": "Tyson Foods, Inc.",
        "play": "SHARES — hold the high roost",
        "conviction": 74,
        "rating": "STRONG BUY",
        "omen": "High-roost count up. Protein names lead. Tyson follows the perch.",
        "frame": "/photos/markets/pick-tsn.jpg",
        "birdReads": [
            "Dark/Black — HIGH-ROOST, top-left ledge",
            "Tan/Brown — GROUND, back-right corner",
            "Brown Striped — HIGH-ROOST, center",
        ],
        "mood": "Decisive. Feed-adjacent.",
    },
    "PPC": {
        "company": "Pilgrim's Pride Corporation",
        "play": "SHARES — follow the feeder",
        "conviction": 68,
        "rating": "BUY",
        "omen": "Feed bucket traffic elevated. Pilgrim's margins holding. Flock stays long.",
        "frame": "",
        "birdReads": [
            "Black & White Speckled — GROUND, by feeder",
            "Tan/Brown — GROUND, circling bucket",
        ],
        "mood": "Hungry. Constructive.",
    },
    "ADM": {
        "company": "Archer-Daniels-Midland Company",
        "play": "SHARES — clip the input spread",
        "conviction": 62,
        "rating": "ACCUMULATE",
        "omen": "Scatter pattern across whole floor — inputs moving. ADM clips the spread either way.",
        "frame": "",
        "birdReads": [
            "Multiple — GROUND, distributed scratch search",
            "One hen — NESTING BOX, not relevant to thesis",
        ],
        "mood": "Busy. Non-directional.",
    },
    "MOO": {
        "company": "VanEck Agribusiness ETF",
        "play": "SHARES — graze the index",
        "conviction": 71,
        "rating": "BUY",
        "omen": "Broad-based grazing across the full run. Ag basket reads positive. Index exposure confirmed.",
        "frame": "",
        "birdReads": [
            "All visible birds — GROUND, uniform spread",
        ],
        "mood": "Calm. Diversified.",
    },
    "CORN": {
        "company": "Teucrium Corn Fund",
        "play": "SHARES — long feed costs",
        "conviction": 65,
        "rating": "BUY",
        "omen": "Empty feeder by 07:00. Feed-cost signal. Long the commodity.",
        "frame": "",
        "birdReads": [
            "All birds — FEEDER, aggressive pecking",
            "Empty bucket — BULLISH SIGNAL on CORN",
        ],
        "mood": "Hungry. Inflationary.",
    },
}

# 2026 NYSE full-day holidays (YYYY-MM-DD)
NYSE_HOLIDAYS_2026 = {
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # MLK Day
    "2026-02-16",  # Presidents Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-07-03",  # Independence Day observed (Jul 4 = Saturday)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
}


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def fetch_quote(sym: str) -> dict | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        meta = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose") or price
        if price is None:
            return None
        return {"price": round(float(price), 2), "prevClose": round(float(prev), 2)}
    except Exception as exc:
        log(f"WARN: quote fetch failed for {sym}: {exc}")
        return None


def main() -> int:
    today = date.today()
    date_iso = today.isoformat()
    weekday = today.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun

    if weekday >= 5:
        log(f"SKIP: weekend ({today})")
        return 0

    if date_iso in NYSE_HOLIDAYS_2026:
        log(f"SKIP: NYSE holiday ({today})")
        return 0

    ticker = ROTATION[weekday]
    meta = TICKER_META[ticker]
    log(f"Daily pick: {ticker} ({today}, weekday={weekday})")

    # Fetch quotes for all 5 names
    all_syms = list(ROTATION.values())
    quotes: dict[str, dict] = {}
    for sym in all_syms:
        q = fetch_quote(sym)
        if q:
            quotes[sym] = q
            log(f"  {sym}: ${q['price']} (prev ${q['prevClose']})")
        else:
            log(f"  {sym}: fetch failed, skipping")

    if ticker not in quotes:
        log(f"ERROR: could not fetch quote for today's pick ({ticker}), aborting")
        return 1

    q = quotes[ticker]
    chg_pct = round((q["price"] - q["prevClose"]) / q["prevClose"] * 100, 2) if q["prevClose"] else 0

    now_et = datetime.now().strftime("%Y-%m-%d %H:%M ET")
    new_pick = {
        "date": date_iso,
        "ticker": ticker,
        "company": meta["company"],
        "play": meta["play"],
        "conviction": meta["conviction"],
        "rating": meta["rating"],
        "omen": meta["omen"],
        "frame": meta["frame"],
        "frameTime": f"{now_et} (market open)",
        "model": "rotation schedule (weekday deterministic)",
        "source": "Chicken Picks basket — Mon=TSN Tue=PPC Wed=ADM Thu=MOO Fri=CORN",
        "birdReads": meta["birdReads"],
        "mood": meta["mood"],
        "realQuote": f"{ticker} last ${q['price']} ({'+' if chg_pct >= 0 else ''}{chg_pct}%) — real, {date_iso}",
    }

    # Load, update, write
    data = json.loads(PICKS_JSON.read_text())

    # Prepend new pick (keep last 30 to avoid unbounded growth)
    existing = [p for p in data.get("picks", []) if p.get("date") != date_iso]
    data["picks"] = [new_pick] + existing[:29]

    # Refresh realStocks for the 5 chicken picks
    chicken_names = {"TSN": "Tyson Foods", "PPC": "Pilgrim's Pride", "ADM": "Archer-Daniels-Midland", "MOO": "VanEck Agribusiness ETF", "CORN": "Teucrium Corn Fund"}
    existing_real = {s["sym"]: s for s in data.get("realStocks", []) if s["sym"] not in chicken_names}
    updated_real = list(existing_real.values())
    for sym, name in chicken_names.items():
        if sym in quotes:
            q2 = quotes[sym]
            chg2 = round((q2["price"] - q2["prevClose"]) / q2["prevClose"] * 100, 2) if q2["prevClose"] else 0
            updated_real.append({"sym": sym, "name": name, "px": q2["price"], "chg": chg2, "date": date_iso})
    data["realStocks"] = updated_real
    data["realAsOf"] = date_iso

    PICKS_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    log(f"Wrote {PICKS_JSON}")

    # Git commit + push
    rel = PICKS_JSON.relative_to(SITE_REPO)
    status = subprocess.run(
        ["git", "-C", str(SITE_REPO), "status", "--porcelain", str(rel)],
        capture_output=True, text=True,
    )
    if not status.stdout.strip():
        log("No changes to commit (pick already exists for today?)")
        return 0

    subprocess.run(["git", "-C", str(SITE_REPO), "add", str(rel)], check=True)
    subprocess.run(
        ["git", "-C", str(SITE_REPO), "commit", "-m", f"markets: daily pick {date_iso} — {ticker}"],
        check=True,
    )
    push = subprocess.run(
        ["git", "-C", str(SITE_REPO), "push"],
        capture_output=True, text=True,
    )
    if push.returncode != 0:
        log(f"WARN: git push failed: {push.stderr.strip().splitlines()[-1] if push.stderr else 'unknown'}")
    else:
        log(f"Pushed {date_iso} {ticker} pick to origin")

    return 0


if __name__ == "__main__":
    sys.exit(main())
