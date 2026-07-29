# S7 dawn-to-dusk daily Reel + weekly gems Reel — plan

**Date:** 28-Jul-2026
**Author:** Claude Opus 5
**Status:** ✅ APPROVED AND IMPLEMENTED 28-Jul-2026 — shipped as CHANGELOG v2.54.0
**Requested by:** Boss, 28-Jul-2026

> **Implementation note — one deviation from §4.4.** The plan said an over-budget
> reel would drop filler frames first. In practice nothing is ever dropped: the
> selector's 90-frame cap already guarantees the 1.0s baseline fits inside 77s,
> so only the gem *bonus* can overrun, and tapering the gem hold always resolves
> it. Verified on the 25-Jul worst case (68 frames / 36 reacted): 86.8s → 1.52s
> hold → 76.7s, zero frames lost. The drop path is unreachable and was not built.

---

## 1. What Boss asked for

1. The S7 daily Reel must be a **true dawn-to-dusk reel of a single day**, not a
   rolling window that straddles two days.
2. It must post at **21:00 (9pm)**, not 12:00 noon.
3. It should keep using **good frames that never got a Discord reaction** to fill
   the reel out — reactions must NOT become a requirement for the daily lane.
4. Frames that **did** get a reaction should get **better treatment** inside the
   daily reel.
5. Reacted gems should **also** be stitched into a **weekly** reel, because there
   are not enough gems on any single day to carry a gems-only reel.

Boss also confirmed the `bird_count >= 1` filter should stay.

---

## 2. Findings that shape the plan

### 2.1 Why the current reel is not "a day"

The lane fires at **12:00** and looks back a **rolling 24 hours**. That window is
noon-yesterday → noon-today, so every reel is two half-days spliced together:
yesterday's afternoon and evening, then this morning. The chronological sort makes
that read as one continuous arc, which is why it looks wrong.

CHANGELOG 22-Jul-2026 moved this lane 21:00 → 12:00 with the stated rationale that
*"a 09:00 run's 24h window is mostly the prior calendar day, where the old 21:00
runs skewed toward the current day."* **That objection was about the rolling window,
not about the hour.** Moving to 21:00 *and* switching to a same-day window resolves
it properly instead of reverting it — a 21:00 run bounded to today's calendar date
covers exactly one day, start to finish, with nothing borrowed from yesterday.

### 2.2 The window does not need solar math

`tools/pipeline/golden_windows.py` already exposes `sunrise_minute()` /
`sunset_minute()`, so a literal solar window is available. **It should not be used
here.** Measured first/last usable frame per day over the last week:

| Local date | Usable frames | Distinct 15-min buckets | First | Last |
|---|---|---|---|---|
| 2026-07-23 | 186 | 47 | 05:10 | 19:45 |
| 2026-07-24 | 120 | 32 | 05:31 | 20:15 |
| 2026-07-25 | 278 | 54 | 05:18 | 20:07 |
| 2026-07-26 | 322 | 53 | 05:15 | 18:57 |
| 2026-07-27 | 110 | 12 | 16:48 | 19:34 |
| 2026-07-28 | 287 | 44 | 07:55 | 18:52 |

Geometric sunrise for Hampton CT in late July is ~05:25 — **later than the earliest
real frames (05:10)**, so a solar lower bound would clip genuine first-light frames.
There is also nothing to exclude at the other end: **zero** usable frames exist
between 21:00 and 05:00, because the retention path only writes a file for `sharp`
frames and night frames come back `soft`.

**Decision: bound the window to the local calendar day.** Dawn-to-dusk emerges from
the data for free, it self-adjusts with the seasons, it has no clipping edge, and it
adds no dependency.

### 2.3 The frame pool is bounded by retention, not by the filters

Of 8,646 S7 rows in the last 3 days:

| Bucket | Count |
|---|---|
| `soft`, no file written | 6,985 |
| `sharp`, file on disk — **the real usable pool** | 998 |
| `sharp`, file already pruned | 594 |
| `blurred`, no file | 69 |

So the usable pool is ~330/day. `bird_count = 0` only accounts for 809 rows —
correcting what I said in chat earlier, the bird filter is *not* what thins the
reel; retention is. Boss's call to keep the bird filter still stands.

Full-day bucket counts (§2.1 table) land at **44–54 buckets**, so a same-day window
yields *more* frames than today's two-half-days window, not fewer.

### 2.4 The real "gems get dropped" bug

The 90-frame cap never binds at ~50 buckets, so reacted gems are not being lost to
the cap. The actual defect is in bucketing: `_score_gem` picks **exactly one
representative per 15-minute bucket**. When two reacted gems land in the same
bucket, **one is silently discarded**. On a 36-reaction day (25-Jul) that is close
to certain. That is what "better treatment" has to fix first — guaranteed survival,
then longer screen time.

### 2.5 The s7-backlog lane is finished, and it is already the weekly gems reel

`select_s7_backlog_reel_gems` is *already* "S7 reacted portrait gems, oldest first."
That is the weekly gems reel, running 4×/day at 09/13/17/20.

| Measure | Value |
|---|---|
| Gems already consumed (`used-in-backlog-reel`) | 1,746 |
| Gems remaining undrained | **15** |
| Lane's `min_frames` | 20 |

The backlog is drained. With ~11 reacted gems/day arriving against 4×25 = 100/day
of drain capacity, the lane has degraded into "an irregular gems reel every 1–2
days" — its recent runs fired 18-Jul, 22-Jul, 24, 25, 26, 28, and it is below its
own minimum right now.

**Decision: do not add a fifth S7 posting slot. Convert the drained backlog lane
into the weekly gems reel.** Same pool, same `used-in-backlog-reel` marker, same
no-double-post guarantee — only the cadence and the framing change. ~77 reacted
gems/week comfortably clears a 20-frame minimum.

Note the existing side effect, which is retained deliberately: marking a gem
consumed also removes it from the hourly Story queue, so a gem goes into the weekly
reel *or* a Story, never both. The **daily** lane writes no marker, so a gem can
appear in the daily reel and still be picked up by the weekly one — which is
exactly the behaviour Boss asked for.

---

## 3. Scope

### In scope

- Retime `com.farmguardian.ig-s7-daily-reel` 12:00 → 21:00.
- Switch the daily selector from a rolling 24h window to the local calendar day.
- Guarantee every reacted gem survives bucketing in the daily reel.
- Add per-frame durations to the stitcher so reacted gems hold longer on screen.
- Convert the s7-backlog lane into a weekly S7 gems Reel (Sundays).

### Out of scope

- Any change to the house-yard, duo2, mba-cam, dominator, gwtc or mixed lanes.
  The stitcher change must be **additive and default-off** so those five lanes are
  unchanged by construction.
- Reviving the all-camera `select_weekly_reel_gems()` mixed lane. Boss's rule is one
  camera per reel, never combined — the weekly lane stays S7-only.
- Reviving throwback / on-this-day sourcing.
- Touching capture cadence, retention, or the VLM.

---

## 4. Architecture

### 4.1 Daily lane — window change

`tools/pipeline/ig_selection.py::select_s7_daily_reel_gems`

Replace the rolling cutoff with a single-day bound.

**Do not use SQLite's `date(ts,'localtime')`.** That modifier resolves to the
*host* timezone and offers no way to pass a named zone — it happens to agree with
`America/New_York` on this Mini today, which is luck, not design. Compute the day
boundaries in Python with `zoneinfo` and pass them as parameterized ISO bounds,
exactly like the existing `cutoff_iso` in this function:

```
day_start_iso <= ts < day_end_iso     # both computed via ZoneInfo, both bound params
```

**Anchor to an explicit target date, not to "now".** A same-day window has no
graceful degradation on a missed run: if the Mini is asleep at 21:00, launchd fires
late, and a run that lands after midnight would see the *new* day, find nothing, and
lose that day's reel silently — the rolling window it replaces did not have this
failure mode. So the target date is resolved once at startup:

- run at/after first light on date D → target D
- run before first light (i.e. it slipped past midnight) → target D−1

The lane already threads a `date_key` into `_append_ledger`
(`daily_reel_runner.py:1467`); check that first and reuse it as the anchor rather
than inventing a parallel notion of "which day is this reel."

New config keys under `instagram.scheduled`, replacing
`s7_daily_reel_window_hours`:

| Key | Default | Meaning |
|---|---|---|
| `s7_daily_reel_same_day_only` | `true` | Bound to one calendar day |
| `s7_daily_reel_timezone` | `"America/New_York"` | Day-boundary timezone (Python-side) |
| `s7_daily_reel_late_run_fallback_hour` | `5` | Before this local hour, treat the run as yesterday's |

`s7_daily_reel_bucket_minutes` (15), `s7_daily_reel_max_frames` (90),
`s7_daily_reel_require_source_reactions` (`false`) all stay as they are —
requirement 3 is already satisfied by that last flag and must not change.

`s7_daily_reel_min_frames` drops **12 → 10**. A full day now reliably produces
44–54 buckets, so 12 was never the binding constraint; lowering it slightly keeps a
rain-shortened day from silently skipping the slot.

### 4.2 Daily lane — reacted gems survive bucketing

Same function. Selection becomes a union rather than a straight bucket sweep:

1. Take **every** qualifying frame with `discord_reactions >= 1` — unconditionally,
   no bucket competition. These are the gems.
2. Bucket the remaining non-reacted frames as today and take one representative
   each.
3. Drop any representative whose bucket already contains a gem, so a gem does not
   sit next to a near-identical neighbour.
4. Union, sort chronologically, then apply the duration guard in §4.4.

`s7_daily_reel_max_frames` (90) still applies to the union, but **asymmetrically**:
non-reacted representatives are trimmed lowest-score-first until the count fits, and
gems are only touched if the gems alone exceed the cap — in which case the
lowest-scoring gems go and the count is logged. Gems are protected; filler is not.

Requirement 3 is preserved: non-reacted frames still carry the bulk of the reel.

### 4.3 Stitcher — per-frame durations

`tools/pipeline/reel_stitcher.py::stitch_gems_to_reel` and
`_build_filter_complex`.

Add an **optional** `per_frame_seconds: Optional[list[float]]` argument. When
`None` (every existing caller), behaviour is byte-for-byte what it is today.

When supplied, **three** things change together — changing only the first produces
a broken render:

1. `_build_filter_complex` walks **cumulative** offsets instead of multiplying a
   single value.
2. Each image input's own duration at `reel_stitcher.py:373` becomes
   `-loop 1 -t {per_frame_seconds[i]} -i {path}`. Today every input is loaded at a
   flat `seconds_per_frame`; if the offsets stretch but the inputs do not, the
   crossfade runs off the end of a 1.0s input that was supposed to hold 1.8s and
   ffmpeg yields black frames or errors out.
3. The silent audio track's `-t {total_duration}` becomes the **sum** of
   `per_frame_seconds` minus the crossfade overlaps, not `n × seconds_per_frame`.

This lets us delete the frame-0 duplication hack at `daily_reel_runner.py:1422`,
which the code comment already flags as a workaround for exactly this missing
feature.

Daily-lane timing:

| Frame type | Hold |
|---|---|
| Reacted gem | 1.8s |
| Everything else | 1.0s |

Both configurable (`s7_daily_reel_gem_seconds`, `seconds_per_frame`).

### 4.4 Stitcher — duration guard replaces the frame-count proxy

`_MAX_FRAMES = 90` exists as a *duration* proxy: the comment reads
"90 × 1s − 89 × 0.15s ≈ 77s, under Instagram's 90s reel limit." Variable durations
break that equivalence, so the cap must become a real duration check.

Add `_MAX_REEL_SECONDS = 77.0`. When `per_frame_seconds` is supplied, compute total
duration and, while over budget:

1. drop the lowest-scoring **non-reacted** frame;
2. if only gems remain and it is still over, taper the gem hold toward 1.0s;
3. if it is *still* over, drop the lowest-scoring gems and log exactly how many
   were dropped — no silent truncation.

Worst measured case (25-Jul: 54 frames, 36 reacted) computes to ~74.9s, inside
budget — but the guard is what makes that safe rather than lucky.

### 4.5 Weekly gems lane — conversion, not a new lane

`tools/pipeline/daily_reel_runner.py`

`S7_BACKLOG_REEL_LANE` is re-pointed and renamed rather than duplicated:

| Field | From | To |
|---|---|---|
| `lane_id` | `s7-backlog` | `s7-weekly-gems` |
| `discord_title` | "S7 backlog time-lapse" | "S7 gems of the week" |
| `caption_fallback` | "A look back at the nesting box." | "The week's best from the nesting box." |
| `ledger_lane` | `s7-backlog-reel` | `s7-weekly-gems-reel` |
| Cadence | 09/13/17/20 daily | **Sundays 10:30** |

The `lane_id == "s7-backlog"` gate at `daily_reel_runner.py:1470` moves to the new
id so the consumption marker keeps firing. The marker string
`used-in-backlog-reel` stays **unchanged** — 1,746 existing rows carry it and
renaming it would re-expose all of them.

Selector renames `select_s7_backlog_reel_gems` → `select_s7_weekly_gems_reel_gems`
with a compatibility alias.

**It must also stop being a queue.** Measured eligible arrival — reacted, portrait
1080×1920, file on disk — is **112/week averaged over 28 days, 119 in the last 7**.
(Every reacted S7 row currently qualifies: of 448 in 28 days, zero lack a file and
zero are landscape.) Any per-week cap below ~112 drains slower than gems arrive, so
an oldest-first queue would silently re-grow the very backlog this lane just
finished draining. Raising the cap to the 90-frame maximum would still lose ~22/week.

So the weekly lane changes shape: **a highlights reel bounded by a 7-day window, not
a drain over an unbounded pool.**

| Aspect | Backlog behaviour | Weekly gems behaviour |
|---|---|---|
| Pool | all unposted reacted gems, any age | reacted gems from the **last 7 days only** |
| Order | oldest first | top-scored, then sorted chronologically |
| Surplus | queues forever | ages out of the window — no queue exists |

| Key | From | To | Why |
|---|---|---|---|
| `s7_weekly_gems_window_days` | *(new)* | 7 | Window, not a queue |
| `s7_weekly_gems_max_frames` | 25 | 60 | ~51s at 0.85s effective/frame |
| `s7_weekly_gems_max_per_day` | *(new)* | 12 | Stops one 36-reaction day eating the week |
| `s7_weekly_gems_min_frames` | 20 | 20 | unchanged |

Gems not selected simply do not appear — they already ran in that day's daily reel,
and they keep their Story eligibility because only *posted* frames get marked.

**Consequence to accept explicitly:** the 15 undrained legacy gems (oldest
26-Apr-2026) fall outside a 7-day window and will never post. That is the correct
outcome — the backlog is declared finished, not carried forward.

Every frame in this reel is a gem, so it uses uniform timing — no per-frame
durations, no differential treatment needed.

**Sunday 10:30** is chosen to avoid stacking: 09:00 house-yard, 12:30 carousel,
18:00 mixed, 18:30 Nextdoor, Sun 20:00 weekly digest, 21:00 S7 daily. Retiring the
4×/day backlog cadence frees up to 1 publish/day against the shared 25-per-24h IG
quota; the weekly adds 1 on Sundays. Net quota pressure goes **down**.

---

## 5. TODOs (ordered)

1. `ig_selection.py` — local-day window + guaranteed-gem-survival union in
   `select_s7_daily_reel_gems`. Update the docstring's stated criteria.
2. `ig_selection.py` — rename the backlog selector to the weekly gems selector,
   keep an alias, and convert it from an unbounded oldest-first queue to a 7-day
   top-scored window with a per-day cap. Leave the marker string alone.
3. `reel_stitcher.py` — optional `per_frame_seconds` driving **all three** of
   cumulative offsets, per-input `-t`, and the audio track length;
   `_MAX_REEL_SECONDS` guard with logged drops.
4. `daily_reel_runner.py` — build the per-frame duration list for the S7 daily
   lane; delete the frame-0 duplication hack; re-point `S7_BACKLOG_REEL_LANE` to
   the weekly gems lane and move the marker gate to the new `lane_id`.
5. `tools/pipeline/config.json` — new/changed keys from §4.1, §4.3, §4.5.
6. Rename `scripts/ig-s7-backlog-reel.py` → `scripts/ig-s7-weekly-gems-reel.py`;
   update its header and the lane import.
7. Plists — **both copies each** (`~/Library/LaunchAgents/` live and
   `deploy/ig-scheduled/` in-repo):
   - `ig-s7-daily-reel` → Hour 21.
   - `ig-s7-backlog-reel` → renamed to `com.farmguardian.ig-s7-weekly-gems-reel`,
     `StartCalendarInterval` = Weekday 0, Hour 10, Minute 30.

### Verification (all before declaring done)

8. **Selector dry-run, no posting.** Print what today's 21:00 run *would* pick:
   frame count, first/last timestamp, how many are reacted, computed duration.
   Assert: all timestamps fall on one local date; every reacted frame present.
   Re-run with a faked 00:30 clock and confirm the late-run fallback targets the
   previous day rather than returning an empty set.
9. **Stitcher regression — the one that matters.** Render an existing lane
   (mba-cam or duo2) with `per_frame_seconds=None` and confirm the output is
   identical in duration and dimensions to a current reel. This is the guard
   against silently breaking five lanes at 09:00/15:00/18:00.
10. **Variable-duration correctness by measurement, not by reading code.** Render a
    test reel with deliberately unequal holds and `ffprobe` the result; measured
    duration must match the formula. The xfade offsets are cumulative and must not
    be trusted on inspection.
11. **Duration guard.** Force the 25-Jul worst case (54 frames / 36 reacted) through
    selection and confirm the output lands under 77s with drops logged.
12. **Weekly lane dry-run.** Confirm it selects only from the last 7 days, honours
    the 12/day cap, lands at ≤60 frames, and that a dry run writes **no** marker.
13. **Live, watched.** Load the retimed daily plist with `bootout` + `bootstrap`
    (**not** `kickstart -k`, which re-runs from launchd's cached plist and silently
    ignores the edit — documented in CLAUDE.md for the MBA env change and it applies
    equally to a schedule change). Watch the 21:00 run, then check the Discord
    notice and the posted Reel.

---

## 6. Docs / CHANGELOG touchpoints

- **`CLAUDE.md`** — the "S7 social exception" paragraph near the top hardcodes
  *"fires at 12:00 local (was 21:00)"*; it must say 21:00 dawn-to-dusk, same-day
  window. The live-schedule line listing "s7 reel 12:00" and "s7-backlog reel
  09/13/17/20" both change.
- **`docs/SOCIAL_MEDIA_MAP.md`** — declared single source of truth for lanes.
  Update the S7 daily row and replace the backlog row with the weekly gems row.
  Do not fork this table into another doc.
- **`CHANGELOG.md`** — new top entry, minor version bump. Must record that the
  22-Jul 12:00 retime is being superseded deliberately, with the same-day-window
  reasoning, so a future agent does not "restore" noon on the strength of the
  22-Jul entry.
- **This plan doc** — mark approved/implemented once shipped.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Stitcher change breaks the five other reel lanes | `per_frame_seconds` optional, default `None` = today's exact code path; TODO 9 verifies against a real existing lane |
| Variable durations overrun IG's 90s limit | `_MAX_REEL_SECONDS = 77.0` guard with logged, never-silent drops |
| Weekly lane double-posts frames | Reuses the existing `used-in-backlog-reel` marker unchanged; 1,746 historical rows stay excluded |
| A future agent reverts 21:00 → 12:00 citing the 22-Jul entry | CHANGELOG entry explicitly supersedes it and states why the hour was never the problem |
| Rain-shortened day skips the slot | `min_frames` 12 → 10; a genuine no-material day should still no-op rather than post a 4-frame reel |
| Missed/late run silently loses a day | Explicit target-date anchor + pre-first-light fallback to D−1 (§4.1) |
| Weekly cap below arrival re-grows the backlog | Lane converted from unbounded queue to a 7-day window; surplus ages out instead of accumulating (§4.5) |
| Variable durations render black frames | Per-input `-t` and audio length change together with the offsets, verified by `ffprobe`, not by inspection (§4.3, TODO 10) |
