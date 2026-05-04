# 04-May-2026 — S7 Backlog Reel Plan

## Context

The story queue has 291 reacted `s7-cam` gems spanning 2026-04-17 → 2026-05-04 that are draining
as individual Instagram stories at 5/hour. That's 58+ hours of queue for the s7 shots alone.

Rather than post them one-by-one as stories, we stitch each calendar day's worth into a short
time-lapse Reel — same pattern as the existing `ig-s7-daily-reel` lane, just aimed at past dates
instead of the last 24h. One Reel per day of backlog = 1 IG publish slot instead of 10–65.
Gems used in a backlog Reel get marked so they leave the story queue.

---

## Per-day breakdown (s7-cam reacted gems, unposted as of 2026-05-04)

| Date | Gems | Action |
|---|---|---|
| 2026-04-17 | 2 | skip (below floor) |
| 2026-04-20 | 8 | skip (below floor) |
| 2026-04-21 | 4 | skip (below floor) |
| 2026-04-22 | 24 | reel |
| 2026-04-23 | 14 | reel |
| 2026-04-24 | 9 | reel |
| 2026-04-25 | 5 | skip (below floor) |
| 2026-04-26 | 13 | reel |
| 2026-04-27 | 12 | reel |
| 2026-04-28 | 27 | reel |
| 2026-04-29 | 11 | reel |
| 2026-04-30 | 20 | reel |
| 2026-05-01 | 23 | reel |
| 2026-05-02 | 32 | reel |
| 2026-05-03 | 65 | reel (cap at 50 frames) |
| 2026-05-04 | 22 | reel |

**Floor: 10 gems minimum.** Days below the floor (Apr 17, 20, 21, 25) are skipped — not enough
frames for a real timelapse. Those gems stay in the story queue and drain normally.

**Frame cap: 50 per reel.** For days with more than 50 gems (May 3 has 65), pick
the 50 highest-`overall_score` frames so the best shots make the cut.

Expected output: **12 backlog Reels** over 12 days.

---

## Scope

**In:**
- New `select_s7_backlog_reel_gems(db_path, date_str, cfg)` selector in `ig_selection.py`
- New `S7_BACKLOG_REEL_LANE` config in `daily_reel_runner.py`
- `--date YYYY-MM-DD` arg threaded through `main()` and `_select_gems()` so a lane can target
  a specific past date instead of "the last N hours"
- Post-publish DB marking: `ig_story_skip_reason = 'used-in-backlog-reel:YYYY-MM-DD'`
- Thin script `scripts/ig-s7-backlog-reel.py` that finds the next unprocessed target date and
  calls `main(S7_BACKLOG_REEL_LANE, date=target_date)` — self-terminates when backlog is empty
- LaunchAgent `com.farmguardian.ig-s7-backlog-reel` firing daily at 12:00 local
- Deploy plist at `deploy/ig-scheduled/com.farmguardian.ig-s7-backlog-reel.plist`

**Out:**
- No changes to the mixed daily Reel or the live S7 daily Reel
- No approval gate (same pattern as `ig-s7-daily-reel`: auto-post + Discord notice)
- No changes to the story publisher — it continues draining non-s7 and thin-day s7 gems normally
- No UI changes

---

## Architecture

### New selector — `ig_selection.py::select_s7_backlog_reel_gems`

```
select_s7_backlog_reel_gems(db_path, date_str, cfg) -> list[int]
```

- `date_str`: `"YYYY-MM-DD"` (UTC calendar date of the target gems)
- Filter: `camera_id='s7-cam'`, `DATE(ts)=date_str`, `discord_reactions>=1`,
  `has_concerns=0`, `image_path IS NOT NULL`,
  `ig_story_skip_reason NOT LIKE 'story-permanent-skip:%'`,
  `ig_story_skip_reason NOT LIKE 'used-in-backlog-reel:%'`
- Order: `overall_score DESC, ts ASC` — best frames first, then oldest for tiebreak
- Cap at `s7_backlog_reel_max_frames` (default 50)
- Returns `[]` if fewer than `s7_backlog_reel_min_frames` (default 10)

### New lane config — `daily_reel_runner.py::S7_BACKLOG_REEL_LANE`

```python
S7_BACKLOG_REEL_LANE = DailyReelLane(
    lane_id="s7-backlog",
    log_name="ig-s7-backlog-reel",
    description="Auto-post one backlog S7 Reel per day, oldest date first.",
    selector_name="select_s7_backlog_reel_gems",
    state_subdir="s7-backlog",
    output_filename_prefix="reel-s7-backlog",
    discord_username="farm-reel-s7",
    discord_title="S7 backlog time-lapse",
    approval_required=False,
    ledger_lane="s7-backlog-reel",
    caption_fallback="A look back at the nesting box.",
    mention_user_id=MARK_DISCORD_USER_ID,
)
```

### `--date` arg threading

`daily_reel_runner.main()` gains an optional `target_date: str | None` parameter.
`_select_gems()` passes it as a keyword arg if present — selectors that don't need it
ignore it (existing lanes unchanged). `_build_publish_and_notify()` uses `target_date`
instead of today as the posted-file key so each backlog date gets its own audit file
(`data/reels/s7-backlog/posted/2026-04-22.json`).

### Post-publish marker — new helper in `ig_selection.py`

```python
def mark_gems_used_in_backlog_reel(db_path, gem_ids, date_str) -> None
```

Called from `_build_publish_and_notify()` after a successful IG publish for the backlog
lane. Writes `ig_story_skip_reason = 'used-in-backlog-reel:YYYY-MM-DD'` to the used rows
so `select_all_unposted_story_gems` stops seeing them.

### Thin script — `scripts/ig-s7-backlog-reel.py`

1. Queries the DB for the oldest calendar date that has ≥ `s7_backlog_reel_min_frames`
   unprocessed reacted s7-cam gems (same filter as the selector, no cap).
2. Checks whether `data/reels/s7-backlog/posted/{date}.json` already exists (idempotency).
3. If a target date exists → calls `main(S7_BACKLOG_REEL_LANE, date=target_date)`.
4. If no target date → logs "backlog empty, nothing to do" and exits 0.
   (The LaunchAgent keeps firing daily but does nothing — cheap and safe.)

Caption gets the target date baked in: *"In the nesting box — Saturday, April 26"* rather
than today's date.

### LaunchAgent

- Label: `com.farmguardian.ig-s7-backlog-reel`
- Fires: daily 12:00 local (between the story publisher ticks, clear of the 18:00 mixed
  reel and the 21:00 live S7 reel)
- Quota cost: 1 IG publish/day while the backlog exists, then zero

---

## Ordered TODOs

1. Add `select_s7_backlog_reel_gems()` and `mark_gems_used_in_backlog_reel()` to
   `tools/pipeline/ig_selection.py`
2. Add `S7_BACKLOG_REEL_LANE` to `tools/pipeline/daily_reel_runner.py`
3. Thread `target_date` through `main()`, `_select_gems()`, and
   `_build_publish_and_notify()` — no change to MIXED or S7_DAILY paths
4. Call `mark_gems_used_in_backlog_reel()` after successful publish inside
   `_build_publish_and_notify()` for the backlog lane only
5. Write `scripts/ig-s7-backlog-reel.py`
6. Write `deploy/ig-scheduled/com.farmguardian.ig-s7-backlog-reel.plist`
7. Install plist to `~/Library/LaunchAgents/`, bootstrap it
8. Dry-run test: `python scripts/ig-s7-backlog-reel.py --dry-run`
9. Manual first-run test on oldest eligible date
10. Verify DB marking and that the story queue count drops
11. Update `CHANGELOG.md` and `docs/SOCIAL_MEDIA_MAP.md`

---

## Quota math

| Time | Lane | IG publishes/day |
|---|---|---|
| 12:00 | backlog reel (this plan) | 1 |
| 18:00 | mixed daily reel (approval-gated) | 0–1 |
| 21:00 | live S7 daily reel | 1 |
| hourly | story publisher (5/tick × 24) | up to 22 remaining |

Total stays well under the 25-per-rolling-24h cap.
