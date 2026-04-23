# Smart Publishing Queue — plan

**Date:** 23-April-2026
**Author:** Claude Opus 4.7 (1M context)
**Status:** DRAFT — awaiting Boss approval before implementation.
**Supersedes (partially):** the separate `ig-2hr-story` + `on-this-day-stories` + `ig-daily-carousel` + `ig-weekly-reel` LaunchAgents that today all compete first-come-first-served for IG's 25-per-24h publish quota.

## Why this exists

Instagram Graph API hard-caps `@pawel_and_pawleen` at **25 media publishes per rolling 24 hours** (stories + feed posts + carousels + reels all counted together). Today (2026-04-23) two independent lanes hit that wall:

- **Gem lane** (cameras → Discord reactions → stories) — Boss's high-signal curation; today's live content.
- **Archive lane** (Qwen-catalogued iPhone photos → stories) — fallback, historical.

Current behaviour is first-come-first-served. Archive lane fires every 90 min (16×/day), gem lane every 2 h (12×/day) + daily carousel + weekly reel. The archive lane, firing more often, tends to burn Boss's quota before the gem lane gets to post. The result this afternoon: gem pipeline had 315 reacted gems queued, only 7 published before IG 403'd.

**Boss directive 2026-04-23:** "The gem lane is priority — those are today's photos. The archive is only reached into if we don't have photos from today. Build a smart system that queues and uses our quota intelligently."

## Scope — in

- A single new module `tools/social/publisher.py` that owns every IG/FB publish decision across both lanes.
- A shared quota ledger at `data/social/publish_ledger.ndjson` that tracks every publish in the last 48 h for accurate rolling-24h counting.
- Strict priority rule: gem queue first, archive only when gem queue is empty.
- One new LaunchAgent `com.farmguardian.social-publisher` replacing `com.farmguardian.ig-2hr-story` + `com.farmguardian.on-this-day` as the tick-driven publisher.
- `com.farmguardian.ig-daily-carousel` and `com.farmguardian.ig-weekly-reel` stay independent (different cadence, different media type), but they check the ledger and skip if quota is tight.
- Retirement path for the two old LaunchAgents (rename plist files, bootout; keep the scripts as CLI for manual force-posts).

## Scope — out

- No changes to the v2.37.2 gem-gate semantic filters (other dev's lane).
- No changes to `fb_poster.py` / `ig_poster.py` / `git_helper.py` — the publisher composes these unchanged.
- No changes to `archive-throwback.py`, `discord-reaction-sync.py`, or the live capture pipeline.
- No changes to content policy (hashtags, content filters).
- No attempt to raise the 25/24h IG quota (it's a hard Meta limit for Business accounts without specific review).

## Architecture

### One publisher, one decider

```
Every 60 min, com.farmguardian.social-publisher fires:

  1. Read publish_ledger.ndjson → count publishes in the last 24h.
  2. slots_free = 25 - recent_24h_count
     if slots_free <= 0: log "quota full", exit 0.

  3. GEM QUEUE (priority):
       select_all_unposted_story_gems(no time window)
       → FIFO oldest-first, skip stale entries older than
         STALE_DAYS (decide: 30 default? see open questions).
       publish up to min(slots_free, MAX_PER_TICK) gems.
       append each to ledger.
       update slots_free after each publish.

  4. ARCHIVE FALLBACK — only if gem queue returned ZERO items:
       if slots_free >= ARCHIVE_RESERVE_FLOOR:
           run_auto_story_cycle(dry_commit=False)
           append to ledger if posted.
       else:
           log "reserving remaining slots for live gems"; skip.

  5. On 403 / rate-limit mid-batch: stop, log "quota hit earlier
     than ledger expected (external post drift)", update ledger to
     reflect reality, exit.

  6. On any non-rate-limit error: log, continue with next item.
```

The publisher is a thin orchestrator — all actual photo handling (9:16 prep, farm-2026 commit, Graph API publish) stays in `ig_poster.post_gem_to_story` and `tools.on_this_day.post_daily._publish_one_story`. The publisher just decides WHICH item goes next.

### Why one process, not two talking

Two separate LaunchAgents sharing a ledger file would race on reads/writes and each would need its own fairness heuristic — recipe for "gem lane posted 3 but archive posted 4 and now we're over." One process makes the decision atomically per tick.

### Ledger schema (append-only, newline-delimited JSON)

```json
{"ts": "2026-04-23T20:40:34Z", "lane": "gem", "gem_id": 7832, "ig_media_id": "17861583243626498", "fb_post_id": "..."}
{"ts": "2026-04-23T21:15:00Z", "lane": "archive", "uuid": "5E893169-...", "ig_media_id": "18010040138853424", "fb_post_id": "..."}
```

Append is the only mutation. Nightly sweep (or on each read, if file grows past N lines) truncates entries older than 48 h. Counting the last 24 h is a linear scan of that capped file — cheap.

### Cadence — DECIDE

**Recommendation: every 60 min.** 24 decision points/day for 25 slots means at most 1 gem/tick in steady state, and the gem queue drains evenly through the day instead of bunching into one 2 h burst that might exceed the per-hour soft limit (Meta imposes an undocumented per-hour rate limit ~6-8 media too).

Alternative: every 90 min (16 ticks/day, avg 1.5 gems/tick) — fewer decision points but each tick posts more. Higher chance of 403 mid-batch if per-hour limit kicks in.

**DECIDE:** 60 min vs 90 min. I'd go 60; willing to hear Boss prefers 90.

### Priority rule numbers — DECIDE

The exact thresholds are config (`tools/social/config.json`), not code:

| Knob | Default I'd propose | Why |
|---|---|---|
| `IG_ROLLING_24H_QUOTA` | 25 | Meta's hard cap; don't change unless Meta changes it |
| `ARCHIVE_RESERVE_FLOOR` | 5 | Never auto-post archive when fewer than 5 slots remain → leaves headroom for urgent gems later in the window |
| `MAX_PER_TICK` | 5 | Cap per tick to avoid burning the whole window in one shot when a big gem backlog is pending |
| `STALE_DAYS` | 30 | Don't republish reacted gems older than this; they've already been seen by the people who'd care and content-wise feel less "now". Can be raised. |

### Carousel + reel lanes — DECIDE

`ig-daily-carousel` (18:00) and `ig-weekly-reel` (Sun 19:00) also burn quota. Options:

- **(a) Bypass the publisher** — they just run, publisher picks up whatever's left. Simpler but risks starving gem lane on Sunday evenings.
- **(b) Reserve slots** — carousel pre-books 1 slot at 17:55, reel pre-books 1 slot at 18:55. Publisher skips that slot. More complex.
- **(c) Treat carousel/reel as the highest priority** — if they're scheduled to fire, they go first, publisher drains whatever's left to gems.

**I'd pick (a)** — carousel fires once a day (1 slot), reel once a week (1 slot). That's negligible quota drain compared to the 24 hourly decisions. The publisher just reads the ledger at tick time and accepts whatever has already landed.

## TODOs (ordered, each verified before the next)

1. [ ] Boss approves this plan (respond with "go" or specific changes).
2. [ ] Create `tools/social/` package + `publisher.py` + `config.json` + file header per CLAUDE.md.
3. [ ] Write `append_ledger()` + `count_last_24h()` + `prune_older_than_48h()` — cover the atomic-append case (concurrent ticks are impossible because LaunchAgent serialises, but atomicity guards against killed-midwrite).
4. [ ] Write `pick_next()` that returns `("gem", gem_id) | ("archive",) | None` per the priority rule.
5. [ ] Unit-test `pick_next()` with synthetic ledgers (no gems, gems available, quota tight, quota full).
6. [ ] Smoke-test the publisher end-to-end in `--dry-run` mode against the live DB.
7. [ ] Add LaunchAgent `com.farmguardian.social-publisher.plist` (60-min `StartInterval`, `HOME` set per the TCC memory).
8. [ ] Shadow-run phase: new publisher runs with `--dry-run` on its plist for 24 h alongside the old agents. Compare what it would have posted vs what the old agents posted. Sanity-check the priority rule.
9. [ ] Cutover: bootout + rename plists for `com.farmguardian.ig-2hr-story` + `com.farmguardian.on-this-day` (rename, don't delete — keeps the TCC label family stable per the rename-not-delete memory).
10. [ ] Flip the new publisher's plist to non-dry-run.
11. [ ] Keep `scripts/ig-2hr-story.py` and `scripts/on-this-day-stories.py` as CLI entry points for manual force-posts; the new publisher becomes the only scheduled caller.
12. [ ] Update `CLAUDE.md` (farm-guardian + farm-2026), `tools/on_this_day/README.md`, `CHANGELOG.md` with the new topology.
13. [ ] Verify over 48 h that gem backlog is draining and archive is only firing on truly idle windows.

## Docs / changelog touchpoints

- `CHANGELOG.md` → v2.38.0 or v2.39.0 entry: "Smart publishing queue: gem-priority, archive-fallback, shared-quota."
- `CLAUDE.md` (farm-guardian) → rewrite the "Current architecture" line under Instagram Posting: four LaunchAgents → one publisher + carousel + reel + reaction-sync.
- `CLAUDE.md` (farm-2026) → update the pipelines table: two lanes converge into one scheduled publisher.
- `tools/on_this_day/README.md` → note the archive lane is now fallback-only; the `--publish` CLI still works for manual force-posts.
- New: `tools/social/README.md` — the publisher's canonical doc.

## Open questions — ASK BOSS

1. **Stale cutoff.** Publish reacted gems older than 30 days? 60? Forever? Boss's words — "anything reacted is worthy" — suggests forever, but the feed experience of a March photo in late April is stale. Default = forever (no cutoff) until Boss says otherwise; I think 30 days is right but won't decide it.
2. **FB-only overflow.** When IG is maxed for the day, should we keep posting to FB alone (which has no 25/day cap)? My instinct: yes — Boss's reactions are commitments across both platforms, and FB gets more reach for his audience anyway. But it creates a content-mismatch (some gems on both, some FB-only). Needs his call.
3. **Gem-queue freshness tie-break.** When gem queue has 5 items queued and we can only post 1 this tick, do we FIFO (oldest first, matches today's behaviour) or LIFO (freshest first, better for "today's brooder")? I lean LIFO for recency, but FIFO ensures the backlog eventually drains. Maybe FIFO within the last 6 h, LIFO beyond? Boss's call.
4. **Carousel content overlap.** The daily 18:00 carousel picks reacted gems from today. If those same gems already went out as stories this morning, do we post the carousel anyway (it's a different format, people see it differently) or exclude-already-storied? Default today is "post anyway"; I'd keep that but flag it.

## What the next Claude should NOT do

- Do not implement this before Boss says "go." The whole point of the plan doc is to surface the decide/ask-Boss items before coding.
- Do not introduce a second shared-state mechanism. Everything goes through `publish_ledger.ndjson`. A second DB table for queue state would double the sync surface.
- Do not attempt to raise IG's 25/24h quota via API review. Meta requires a specific business-case justification; "we want to post more" doesn't meet the bar. The plan assumes the cap stays.
- Do not retire `archive-throwback.py` — that lives on a different content surface (`#farm-2026` Discord for Boss reaction-gating). It's not an IG publisher.
