# 09-Aug-2026 — Dense weekly/monthly Reolink time-lapse reels

**Status:** implemented (v2.69.0). Approved by Boss as a 100-word plan in chat.

## The complaint

> "you got 17 frames in it for the entire fucking week? That's fucking insane.
> It should be hundreds of pictures."

> "the videos you show me are choppy... They linger on one shot far too long,
> and then it is an abrupt transition to another shot at a totally different
> time of day. The same thing is the case on the other reolink camera."

## Diagnosis (exact, not approximate)

`keyframe_capture` promoted a frame at 3 fixed local times a day. 7 days × 3 =
21 possible; the tier began 03-Aug-2026, so duo2 held **17 keyframes total**.
The xfade stitch path then held each for 1.8s:

```
17 × 1.8 − 16 × 0.15 = 28.2s   ← matches the posted MP4 exactly
```

Consecutive frames were **five real hours apart**, which is the "linger, then
jump to a different time of day" Boss described. The selector was innocent —
there was nothing else in the pool to choose.

## Scope

**In (7 lanes):**

- `house-yard` + `duo2` weekly + monthly (4 lanes) — the reported fault.
- `house-yard-cam-timelapse` (09:00), `duo2-timelapse` (15:00),
  `jieli-dashcam-timelapse` (21:30) — the **daily** lanes, found during a
  follow-up sweep. Each was pinned at exactly 90 frames
  (`timelapse_reel_max_frames`) with 5-minute buckets, i.e. one shot per 16
  minutes of a 24h day held 0.4s. Same linger-then-jump, and these post daily,
  so they are the ones actually seen. duo2 captures ~8,500 raw frames/day, so
  the cap was discarding >99% of the material.

**Out:** the S7, mixed, and the four `.disabled` per-camera time-lapse lanes
(mba-cam, gwtc, usb-cam, dominator-cam). They keep the xfade path and their
existing pacing, untouched.

## Architecture

| Layer | File | Change |
|---|---|---|
| Capture | `orchestrator.py`, `config.json` | `_keyframe_interval_due` — one keyframe per `interval_minutes` (5) of daylight, ~168/day/camera. Slot path kept as fallback. |
| Stitch | `reel_stitcher.py` | `stitch_frames_to_timelapse()` — 18fps, no crossfade, `image2` demuxer. Separate `_MAX_TIMELAPSE_FRAMES = 900`. |
| Selection | `ig_selection.py` | Even-stride subsample across the window, replacing keep-most-recent. |
| Retention | `retention.py`, `orchestrator.py` | `sweep_raw(image_tier=…)`, 768h rolling window on the keyframe tier. |
| Lanes | `daily_reel_runner.py` | `timelapse_fps=18.0`, `timelapse_min_frames=200`, `selector_overrides` on the 7 lanes. |

### Why `selector_overrides` rather than editing the config keys

`timelapse_reel_max_frames` and `timelapse_reel_bucket_minutes` are **global**
keys read by every time-lapse lane. Raising them in `config.json` would hand
900 frames to the mba-cam / gwtc / usb-cam / dominator lanes, which are still
on the xfade path (one ffmpeg input + one filter per frame) and cannot take it.
They are `.disabled` today; this keeps them safe if restored.

### Why the xfade path could not be reused

It spends **one ffmpeg `-i` and one chained xfade filter per frame**. Fine at
17, untenable at 900. And a 0.15s crossfade on a 0.056s frame would occupy most
of every frame's screen time. The dense path is a genuinely different shape, so
it is a second function rather than a parameter on the first.

### Why `_MAX_FRAMES` was not simply raised

`ig_selection` imports `_MAX_FRAMES`, and the S7 daily lane budgets its
per-frame gem holds against it. Raising it would silently restretch lanes this
change has no business touching. `_MAX_TIMELAPSE_FRAMES` is separate for that
reason — do not merge them.

### Why even-stride subsampling was mandatory, not a nicety

The old trim kept the most recent N. At 3 frames/day the cap was never reached
so it never mattered. At 168/day the monthly reel would have silently become
"the last day and a half" under a caption saying *"A month across the farm."*

## Rejected options

- **The duo2's SD card.** It is real and it does record, but the card is full
  (811 MB of 122 GB free) and a `cmd=Search` status sweep shows only Aug 7/8/9
  — ~3 days. It cannot serve a 7-day lane, never mind 30. Substream-only would
  reach ~27 days at 1536×576, worse than the 2304 px frames the pipeline
  already captures every 10s. Good backstop for finding a past event; wrong
  source for a reel.
- **Longer `raw_retention_hours`.** 30 days of duo2 raw ≈ 128 GB, and it still
  cannot serve the monthly lane. Doubles a rolling window CLAUDE.md already
  flags at ~50 GB.
- **YOLO-selected "interesting" frames.** duo2 logged **24 detections in a
  week, all `person`**. The birds are too far from this camera for YOLO to see.
  No signal to select on.

## Verification

- Both cameras confirmed promoting on the `(interval)` path at 11:47 EDT
  09-Aug-2026 in `/tmp/pipeline.err.log`.
- Selector, lane config, and both stitch paths import and resolve clean.
- First weekly reel with a full dense week: **Sunday 16-Aug-2026**. The 10-Aug
  run will have ~1 day of dense capture and is expected to skip on the
  200-frame floor — that is the guard working, not a regression.

## Known-unaddressed

The duo2 frame is 2304×864 (near 3:1). In a 9:16 Reel it renders as a strip
with black filling most of a phone screen, and the birds are specks at that
distance. That is a **framing** problem and no frame count fixes it. Raised
with Boss; not in this change's scope.

## Docs touched

- `CHANGELOG.md` — v2.69.0 entry.
- `retention.py` header — the "keyframes are permanent by construction" note
  from v2.60.0 is now wrong and is marked superseded in place.

## Addendum — the daily window (same day, after the first dense build)

Density alone was not enough. Boss on the first dense daily reel:

> "It starts at like 7 p.m., which is not good. It's just a long period of just
> nothing because it's nighttime... It needs to start at 6 a.m."

> "I want it to go from 6 a.m. until 8 p.m. or 9 p.m. That's it. From 9 p.m.
> until 6 a.m. the next morning ... that can be the gap."

**Cause:** `select_timelapse_gems` selected `ts >= now - window_hours` with **no
upper bound**. The window opened wherever the clock was when the lane ran and
ran through the night. (The 7 p.m. start he saw was a test build at 18:39; the
live 15:00 lane opened at 15:00. Same defect, different hour.)

**Fix:** `timelapse_reel_single_day` anchors the window to ONE local calendar
day — the most recent COMPLETE one (today if run after dusk, else yesterday).
That makes the reel independent of the lane's scheduled hour, so no plist moved
and the packed 20:00–21:30 block and 25/day IG quota were left alone. The SQL
gained a genuine upper bound; moving only the cutoff would still have run to
`now`.

**Bounded by sunrise/sunset, not clock hours.** A fixed 06:00–21:00 was tried
first and measured ending 20:59 with the camera already in infrared — ~45 min
of grey. Fixed hours are season-blind too: too long in August, four hours of
dark in December. Solar bounds reuse `golden_windows.sunrise_minute` /
`sunset_minute` and the farm coordinates, so all six time-lapse lanes now share
one definition of daylight.

**Retention is the constraint to watch.** The previous day's sunrise is ~27h old
at the 09:00 house-yard run. All three cameras are at
`raw_retention_hours: 48`, leaving ~14h of margin. Drop any of them below ~36h
and that lane goes silent — the selector logs a warning naming the camera and
the key rather than reporting an empty day.

**Measured after the change:**

| lane | frames | window (local) | duration |
|---|---|---|---|
| house-yard-cam-timelapse | 849 | 05:50 → 19:58 | 47s |
| duo2-timelapse | 849 | 05:50 → 19:58 | 47s |
| jieli-dashcam-timelapse | 772 | 05:50 → 19:30 | 43s |

Last frame of the duo2 build: 19:41, full evening colour, no IR.
