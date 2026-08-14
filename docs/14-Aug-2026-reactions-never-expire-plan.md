# 14-Aug-2026 — Reactions never expire

**Author:** Claude Opus 5
**Status:** ✅ SHIPPED 14-Aug-2026 (CHANGELOG v2.71.1). Both fixes live and verified — see
"Verification" at the bottom. Approved by Boss ("fix both windows so the reactions never
expire. We want to save the good ones").

## The bug class

Two lanes decide what to publish by asking *"how old is the thing?"* when they should be asking
*"has Boss reacted, and has it been used yet?"* A reaction arriving after the window closes is
discarded, and it is discarded **silently** — no log line, no retry, nothing to notice.

Boss's reaction is a commitment to publish. It must never expire.

## What is actually broken

### 1. `scripts/diary-promote-on-reaction.py` — a hard 72-hour Discord scan

The promoter finds diary posts by scanning `#farm-2026` back a fixed 72h (`--hours`, default
72, hard-stopped at 15 pages). React on day 4 and the post is outside the scan **forever** —
nothing else ever looks at it.

Measured impact: **44 diary entries written, 3 ever promoted** (25-Jul, 28-Jul, 13-Aug). That
ratio is structural, not Boss under-reacting.

### 2. `select_daily_reel_gems` in `tools/pipeline/ig_selection.py` — windows on frame time

`WHERE ts >= now - 24h`, where `ts` is when the **photo was taken**, not when Boss reacted.
`discord-reaction-sync` only runs every 30 minutes, so even a same-evening reaction on a
morning frame can miss. A reaction on anything older than 24h could never be seen at all.

## Scope

**In:** the two fixes above.

**Explicitly OUT — `select_s7_weekly_gems_reel_gems`'s 7-day window is CORRECT and stays.**
It looks like the same bug and is not. Its docstring carries the measurement: eligible gems
arrive at **~112/week** while the reel takes at most 60, so an oldest-first pool drains slower
than it fills and would silently re-grow the 1,746-gem backlog that lane just finished
clearing. Surplus ageing out of that window is the design, not a leak. **Do not "fix" it.**

**Nothing is lost by leaving it alone, and this is the load-bearing fact:**
`select_all_unposted_story_gems` (verified live, 14-Aug-2026) has **no time window at all** —
any reacted gem not yet posted as a Story is picked up on the next hourly `social-publisher`
tick, however old. Late reactions on photos are already saved by that backstop. It is the
model both fixes below copy.

## Architecture

### Fix 1 — remember the message, don't re-scan the channel

Replacing the 72h window with an ever-widening scan would work but degrades: entries Boss never
reacts to would keep the scan wide forever, re-reading thousands of messages every 30 minutes.

Instead the promoter **remembers each diary post's Discord message id** in its state file, then
re-checks those specific messages by id. Cost is one cheap request per *unpromoted* entry —
about 40 today, shrinking as entries get promoted — and it never expires, because a known
message id is checkable forever.

- `data/diary-promote-state.json` gains `known: {iso_day: message_id}` alongside `promoted`.
- Each run: short recent scan (72h) discovers *new* posts and records their ids.
- Then every `known` day not in `promoted` is re-checked directly by id.
- **One-time seeding:** if any diary file on disk is unpromoted and has no known message id,
  the scan widens once to cover it. After that first pass every id is known and scans stay
  short permanently — it converges instead of degrading.
- A message that 404s (deleted) is remembered as gone so it is not retried forever.

### Fix 2 — select on consumption, not age

`select_daily_reel_gems` drops the `ts` window and instead excludes frames already used:
`reel_posted_at IS NULL` (a real, populated column — 1,122 rows carry it, written by
`ig_poster.mark_reel_posted`). A reaction makes a frame eligible immediately and **stays**
eligible until the frame is actually published.

Ordering changes to **newest-first**. There are **1,803** reacted-but-never-reeled frames going
back to 21-Mar; oldest-first with no window would drain that as a backlog and produce reels of
March content — the exact failure the S7 lane's docstring warns about. Newest-first means a
late reaction is picked up on the very next run while the old pool stays available rather than
dominating. The result is still sorted chronologically for playback.

**This lane is retired** (v2.70.7, 18:00 slot freed), so this changes nothing that runs today.
It is fixed because the selector is live code, it is the natural base for whatever replaces the
18:00 slot, and shipping the bug forward would reintroduce silent reaction loss.

## TODOs

1. Fix `select_daily_reel_gems`.
2. Fix `diary-promote-on-reaction.py` (state schema, id recall, seeding scan).
3. Verify both against live data — dry-run the promoter, confirm it now sees entries older
   than 72h; confirm the selector returns late-reacted frames it previously dropped.
4. CHANGELOG + CLAUDE.md.

## Verification (14-Aug-2026 — run against live data and live Discord)

**Fix 2, `select_daily_reel_gems`:** returns **90** frames spanning 2026-07-12 → 2026-08-14.
Before the change the same call returned **8** (the last 24h). Every frame outside that 24h is
one Boss reacted to that the lane could never have seen.

**Fix 1, `diary-promote-on-reaction.py`:**

| | |
|---|---|
| Seeding scan | 19,655 messages, 22 diary posts found, 400-page cap not reached |
| Entries with no Discord post ever (pre-23-Jul, before the writer existed) | 10, recorded in `no_post` |
| Entries awaiting a reaction with a checkable post | 18 |
| **Reacted entries the 72h window had permanently lost** | **3 — 31-Jul, 09-Aug, 10-Aug** |
| Published live | all 3, committed and pushed to farm-2026 (`62df640`, `b3ab476`, `7a6da71`) |
| Field notes on the site | 10 → 13 |

**Convergence proven, not assumed** — the whole design rests on the seeding scan being
one-time, so it was measured on the very next run:

| Run | Scan | Wall clock |
|---|---|---|
| First (seeding) | 19,655 messages / 2,712h | ~3 min |
| Second | **473 messages / 72h** | **13 s** |

…while still re-checking all 15 remaining known posts by id. Short scan, non-expiring
eligibility — which was the point.

**⚠️ Trap hit during verification: Discord 429s a long scan.** The first live attempt died on
`RuntimeError: discord 429`. The original code slept a flat 0.4s between pages and had never
scanned far enough to be throttled; making the scan able to run long made the rate limit
reachable for the first time. `_discord_get()` now honours `retry_after` and retries the **same**
page — retrying a *later* page would silently skip a window of history, and entries inside it
would look like they had no post and be written off into `no_post` permanently. Nine 429s were
absorbed during the seeding run.

## Note for whoever revisits this

10 diary entries (25-Apr → 09-Jul) are recorded in `no_post` because they predate the diary
writer's Discord posting, which began 23-Jul-2026. They can never be promoted by reaction —
there is nothing to react to. If those are wanted on the site they need publishing by hand, or
a small backfill that posts them to `#farm-2026` first.
