# S7 adaptive sampling + frame selection — analysis and plan

**Date:** 07-Aug-2026
**Author:** Claude Opus 5
**Status:** SHIPPED 07-Aug-2026 (v2.67.0) and live on the pipeline daemon.
Boss's steer mid-analysis reframed the objective: *"I know how the Discord
reaction works. I'm just concerned with more of them getting there."* So the
goal is **throughput of good gems into #farm-2026**, not better measurement —
which reordered the proposal below and dropped the negative-signal work to a
noted follow-up (§6).
**Trigger:** Boss: *"there's a smarter way to pull images from the S7, judge them,
and send them to the LLM. There are large clusters of activity and then large
periods of nothing. Why not judge when the clusters are and poll more
frequently? Even during clusters, not all images are great — we've got to get an
image that's clear, doesn't have bird photobombers."*

---

## 1. How it works today

`s7-cam` is the **only** camera that reaches the VLM. Every other enabled camera
(`house-yard`, `usb-webcam-1080p`, `macbook-air-facetime`, `jieli-dashcam`,
`dominator-cam`, `duo2`) is `vlm_bypass: true` — raw capture straight to disk.
So this whole question is entirely about one camera.

Per cycle, from `orchestrator.run_cycle`:

| Step | Cost | Notes |
|---|---|---|
| `GET /focus`, sleep `focus_wait` | **2.0 s** | every cycle, `trigger_focus: true` |
| `GET /photo.jpg` + EXIF/portrait bake | ~1–2 s | `capture.capture_ip_webcam` |
| trivial gate (`std_dev ≥ 5`) | ~10 ms | |
| exposure gate (p50 25–230, std ≥ 15) | free | reuses trivial metrics |
| sharpness gate (`laplacian_floor: 60`) | free | reuses trivial metrics |
| downscale to 768 px long edge | ~20 ms | |
| **Qwen3-VL-4B via LM Studio** | **~5.2 s** | single in-flight lock |
| `_compute_overall_score` → store → `should_post` → Discord | ~100 ms | |

`cycle_seconds: 7` is measured from the *end* of the previous cycle, so the real
period is **~17 s median** (p10 16 s, p90 19 s).

Notably: **`motion_gate: false`** on s7-cam, and Guardian's YOLO detector does
**not** run on s7-cam (only `house-yard` and `duo2` have rows in `detections`).
So there is no cheap "is anything happening" signal anywhere in the s7 path — the
pipeline spends a full 5.2 s VLM call to discover an empty enclosure.

## 2. The funnel, measured (s7-cam, 21 days, n=35,732 VLM calls)

```
35,732  VLM calls                      (~one per 17 s, effectively daylight-only)
 ├─  8,444  (23.6%)  bird_count = 0  →  0.0% ever produce a gem
 └─ 27,288           birds present
      └─ 1,083 (3.03% of all)  share_worth = strong    ≈ 52/day
           └─   203  posted to Discord                 ≈ 10/day
                └─   203  "reacted"   ← ARTIFACT, see §3.5
```

## 3. Five findings

### 3.1 Hour-of-day is a dead end for this camera

Strong-frame yield by local hour, 21 days:

| Hour | 05 | 06 | 07 | 08 | 09 | 10 | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| strong % | 2.9 | 3.0 | 3.4 | 3.3 | **4.5** | 2.6 | 3.0 | 3.6 | 1.6 | 2.8 | 1.6 | 2.3 | 3.2 | **5.3** | 3.5 | 0.4 |

Range 1.6 %–5.3 %. There is a mild dinner-hour bump at 18:00 and a mild 09:00
bump, but nothing that justifies a schedule. **The `timelapse_golden_windows`
machinery already built for `usb-webcam-1080p` / `dominator-cam` would buy
almost nothing on s7** — don't reach for it here.

Also worth noting for §3.3: hours 21:00–04:00 contain **0 gems out of 18 frames
total**. s7 is a daylight camera in practice.

### 3.2 But minute-scale clustering is real, and strong

P(at least one strong frame arrives within the next W seconds):

| W | given this frame is strong | given this frame is skip | lift |
|---|---|---|---|
| 60 s | **38.2 %** | 6.2 % | **6.16×** |
| 120 s | 49.5 % | 12.0 % | 4.14× |
| 300 s | 65.7 % | 24.7 % | 2.66× |
| 600 s | 78.2 % | 38.4 % | 2.03× |

**Boss's intuition is correct, and the useful timescale is ~1–2 minutes, not
"morning vs afternoon."**

Important constraint: this lift is only *observable* by making a VLM call. So it
**cannot gate VLM calls** — it can only **accelerate sampling after a hit**. It
is a burst-after-success rule, not a scheduler. Keep it architecturally distinct
from the cheap presence gate in §3.3.

Weaker but VLM-free-ish predictors, for reference:

| Predictor (at W=60 s) | P(hit\|yes) | P(hit\|no) | lift |
|---|---|---|---|
| `overall_score ≥ 40` | 20.8 % | 5.3 % | 3.95× |
| `largest_subject_pct ≥ 15` | 11.0 % | 3.4 % | 3.24× |
| `bird_count ≥ 1` | 8.5 % | 2.8 % | 3.08× |

### 3.3 A quarter of every VLM call is spent on an empty enclosure

`bird_count = 0` → **8,444 frames (23.6 %), of which 0.0 % ever became a gem.**
That is ~35 min/day of GPU time establishing that no bird is present.

Boss's note — *"the reason we weren't doing YOLO before was that it tended to be
overactive for nighttime detection, but it might be the right thing for
daytime"* — lines up exactly with §3.1: **s7 produces zero gems outside
05:00–20:00.** A daylight-gated YOLO never runs during the window where it
misbehaved. The failure mode Boss remembers is structurally avoidable here, not
merely tuned around.

Why YOLO rather than the existing `MotionGate`: see §5, open question 1.

### 3.4 The photobomber problem is visible in the data — and deliberately ignored for s7

Strong-frame yield by `bird_count`:

| bird_count | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 10 | 12+ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| n | 8,444 | 4,245 | 3,646 | 4,262 | 3,602 | 5,015 | 2,499 | 1,552 | 1,549 | 766 | 74 |
| strong % | 0.0 | 4.9 | 4.5 | 4.6 | 5.1 | 4.0 | 2.5 | 2.2 | 2.2 | 0.5 | 0.0 |

Crowds are worse. **But `should_post` explicitly exempts `s7-cam`** from every
semantic gate (activity / composition / caption / `largest_subject_pct`) — by
Boss's own instruction, recorded in the docstring: *"s7-cam is already strict and
Boss trusts its output."* The same docstring gives a good reason against a
`bird_count` cap: a bird posing close to the lens with the flock behind it is one
of Boss's *favourite* framings, and it carries a high count.

So this should be expressed as a **dominance floor, not a count cap** —
`largest_subject_pct` separates those two cases cleanly:

| Predictor | strong-frame yield |
|---|---|
| `largest_subject_pct ≥ 25` | 14.5 % |
| `largest_subject_pct < 25` | 4.9 % |

That is the shape of the "clear subject, no photobombers" test Boss asked for.
Any change here touches Boss's trusted lane and must be surfaced, not slipped in.

### 3.5 We are throwing away every negative human signal

`scripts/discord-reaction-sync.py:685`:

```python
human_count = _count_human_reactions(msg, token, dh)
if human_count == 0:
    continue          # ← nothing is ever written for an ignored gem
```

`_update_gem_reactions` is only reached when a reaction already exists. Its
docstring claims *"checked_at always advances"*, but the caller's guard means it
never advances for a gem Boss scrolled past. Hence 203 posted / 203 "reacted" —
**not a 100 % hit rate, an absence of recorded negatives.**

Consequence: Boss's real accept rate on Discord is unmeasurable, and **no change
to the gate or the selection logic can be validated against his taste.** This is
the cheapest fix on the list and it unblocks measuring everything else.

## 4. What was built

A `hunt` block, opt-in per camera, currently enabled only on `s7-cam`. Three
new pieces plus a cadence change; every other camera is untouched.

**New — `tools/pipeline/presence.py`.** Lazily-loaded YOLO presence gate.
`yolov8s.pt` at `conf 0.05`, ~16 ms on MPS. Threshold chosen by measuring recall
against 200 randomly-sampled archived s7 frames the pipeline had already tiered
`strong` — i.e. 200 frames known to contain a bird:

| | conf 0.25 | 0.15 | 0.10 | 0.05 |
|---|---|---|---|---|
| yolov8n | 88.0 % | 93.0 % | 96.0 % | 96.5 % |
| **yolov8s** | 93.5 % | 95.0 % | 98.0 % | **99.5 %** |

Tuned hard toward recall because the costs are asymmetric: a false negative
silently loses a gem, a false positive costs one VLM call — exactly the status
quo. Deliberately **not** reused from `detect.py`'s `AnimalDetector`, whose
dwell filter, no-alert zones and `bird_min_bbox_width_pct: 8.0` floor all exist
for security alerting and would each destroy recall here.

Boss's constraint — *"YOLO tended to be overactive for nighttime detection, but
it might be the right thing for daytime"* — is handled structurally, not by
tuning: the gate abstains below `exposure_p50 30`, and per §3.1 s7 produced zero
gems outside 05:00–20:00, so there is nothing to win at night. **Every failure
path abstains** (model missing, inference error, too dark), so the gate can only
ever save work — it can never be the reason a frame is lost.

**New — `tools/pipeline/frame_selector.py`.** Ranks a burst and returns one
winner: sharpness 0.45, dominance 0.30, focus 0.15, centring 0.10, all ranked
*within* the burst.

The photobomber component is `largest_area / total_animal_area`, **not** a bird
count cap — deliberately, because `should_post`'s docstring records Boss's
instruction that a bird at the lens with the flock behind it is a favourite
framing and that capping count would kill it. One bird up close with six specks
behind scores near 1.0; eight evenly-scattered birds score near 0.125. Same
count, opposite verdicts — a distinction a count cap cannot express.

**New — `capture.capture_ip_webcam_burst`.** N frames on ONE AF lock via
`/shot.jpg` rather than `/photo.jpg`: same pixels, same wall-clock, but it
serves the video-stream frame instead of triggering N full camera captures on a
phone running off a ~5 W Qi pad with a documented brown-out history.

**Changed — `orchestrator`.** `_hunt_capture` runs the cheap loop
(decode → trivial → exposure → sharpness → YOLO) and returns either a gated
status or a winning frame; `_next_cadence` runs the expensive loop's feedback
(HOT 0.5 s for 90 s after a strong verdict → WARM 3 s → COLD 20 s after three
presence misses). The two are kept strictly separate: the 6.16× lift is only
observable *via* a VLM call, so it can never gate VLM calls — only accelerate
after a hit.

## 5. Measured results, live

Open question 2 from the original draft is **answered**: AF-once-then-burst
holds focus fine. Bursts routinely come back with all three frames well above
`laplacian_floor: 60` (typical 1200–1450), and the variance that matters is
between frames, not decay across them.

First live hunt cycle after the restart, which is the whole design in one line:

```
all_laplacian: [674.5, 743.2, 1205.1]  ->  picked 2  ->  tier: strong  ->  next_in=0.5s
```

Under the old code that cycle had a 2-in-3 chance of handing the VLM a 674 or
743 frame, then sleeping 7 s.

| | before | after |
|---|---|---|
| median cycle period | 17.0 s | **12.0 s** |
| VLM calls/hour | 212 | **300** (1.42×) |
| frame sent to VLM | whichever landed on the tick | best of 3 |
| empty-frame VLM calls | 23.6 % | gated out |

Selection picked an above-median-sharpness frame in 12/20 observed cycles
(chance would be ~1/3), for a mean **+8.6 %** sharpness over taking a random
frame of the burst. That average is deliberately modest — the `MIN_SHARPNESS_
REL_SPREAD` guard suppresses the component entirely when a burst is uniformly
sharp, so the gain concentrates in the bursts that actually have spread (the
example above is +42 % against its burst mean).

**A bug this caught, worth recording.** The first implementation min-max
normalised sharpness unconditionally. A live burst came back at
`1518.3 / 1517.9 / 1505.8` — three equally sharp frames, a 0.8 % spread — and
plain normalisation mapped that to `1.0 / 0.97 / 0.0`, handing the last frame a
0.45 penalty for nothing. Hence the relative-spread guard: below 15 % spread the
component goes flat and subject geometry decides.

The presence gate has not yet had much to do, because at the new aim birds are
in frame almost continuously. It earns its keep when they wander off.

**It therefore ships in `shadow_mode: true`** — logging what it would skip
without skipping. The 99.5 % recall was measured at the camera's *old* aim, and
Boss re-placed the camera the same evening; more importantly, with the gate live
its false-negative rate is unmeasurable by construction, because a skipped frame
never gets a VLM label to check it against. Shadow mode costs nothing while the
gate has nothing to skip, and turns the error rate into something readable off
the log (skip-decisions where the VLM still reported `bird_count >= 1`). Flip to
`false` after a day of numbers at this aim.

### 5.1 The bigger find: gems were dying at the Discord POST (v2.67.1)

Verifying the above surfaced something that matters more to Boss's actual goal
than any of it. Four consecutive gems passed `should_post` (tier strong, scores
73 / 85 / 71 / 82, sharp, face visible), were sent to Discord, and **all four
were lost** to `http=503 'upstream connect error or disconnect/reset before
headers'` — while a GET on the same webhook returned 200 in 0.25 s.

Two bugs, both now fixed:

1. `post_gem` made **one attempt**, so any transient failure permanently
   destroyed a frame that had already survived capture, four gates, a VLM call
   and the score gate. Now 3 attempts with backoff; 5xx/429 retry (429 honours
   `Retry-After`), other 4xx don't.
2. `orchestrator` **ignored the return value** and set `posted_to_discord = True`
   regardless — which is why four total failures logged as successes and the
   pipeline looked perfectly healthy while dropping every gem.

Worth internalising for future audits: `image_archive.discord_message_id` is
written by the reaction sync, not at post time, and only for messages that
already carry a reaction. It cannot tell you whether a gem was delivered. The
log was the only record, which is precisely why this stayed invisible.

### 5.2 Where the funnel actually narrows now

With throughput up 1.42× and delivery no longer silently failing, the binding
constraint is the **score gate**. Observed at the new camera position, the VLM
returns a near-stereotyped `largest_subject_pct = 30`, giving dominance
`30 x 30/50 = 18`, and with expression 15 + detail 20 + technical 15 that lands
on **68** — two points under `_MIN_OVERALL_SCORE = 70` — over and over.

This is not obviously a scoring bug; it is partly *where the camera now sits*.
Frames where a bird comes close still score 71–85 and pass. But it does mean
the new aim is costing roughly 5 points against the old one (strong frames
historically had `largest_subject_pct` median 39, now 30), and at 68 that is the
difference between a gem posting and never existing. Two levers, both Boss's
call, neither applied:

- **Move the camera closer / lower.** Cheapest fix, no code, and the frames at
  22:19 show birds already crowding the lens — a small nudge would push a lot of
  68s over 70.
- **Lower `_MIN_OVERALL_SCORE` 70 → 65.** One constant in `gem_poster.py`.
  Deliberately not done on ~20 minutes of dusk data from a camera that had been
  in its new position for under an hour; the 14-day history shows a healthy
  spread of 79–95, so this may simply be the light and the settling-in.

## 6. Follow-ups, not done

1. **Record the Discord negatives.** `scripts/discord-reaction-sync.py:685` does
   `if human_count == 0: continue`, so a gem Boss ignored is never written — the
   203/203 in §2 is an absence of negatives, not a 100 % hit rate. Two lines to
   fix, and it would give a real labelled dataset. Deliberately left alone:
   Boss's steer was throughput, not measurement, and this changes nothing about
   how many gems reach the channel.
2. **JPEG quality 99 → 95 on the phone.** Measured 07-Aug-2026, restored to 99
   afterwards: q99 1.15 s / 1834 KB per frame, **q95 0.68 s / 923 KB**, q85
   0.55 s / 512 KB. Quality 95 halves the file and cuts ~1.4 s from every
   burst-of-3 for a difference no one can see. Not applied — it permanently
   affects the archived and published gems, which is Boss's aesthetic call, not
   mine. One command: `curl "http://192.168.0.249:8080/settings/quality?set=95"`
   (the s7-settings-watchdog does not enforce quality, so it will stick).
3. **A dominance floor for s7 in `should_post`.** Per §3.4, `largest_subject_pct
   ≥ 20–25` is the one signal separating a bird at the lens from a distant
   scatter. Not applied: it would *reduce* what reaches Discord, which is the
   opposite of the stated objective, and it changes the lane Boss says he
   trusts. Worth revisiting only alongside (1).
4. **`MotionGate` on s7 remains unassessed.** Threshold 2.3 was tuned for
   house-yard/gwtc; s7 is outdoors where wind and cloud move a 64×64 thumbnail
   with no bird present. It cannot be checked retroactively — skip-tier rows
   carry `image_path = NULL`, so those 8,444 empty frames were never written to
   disk. YOLO was chosen over frame-differencing precisely to sidestep this.

## 7. Out of scope / aside

Daily s7 volume is erratic — 297 to 3,078 rows/day over the last 14 days, with
**2026-07-30 missing entirely and 2026-07-31 holding a single row.** That looks
like daemon or phone uptime gaps, not sampling strategy, and is worth its own
look. Not part of this proposal.

## 8. Docs / changelog touchpoints

`CHANGELOG.md` updated (v2.67.0). No farm-2026 changes — the website is
unaffected by everything here. Re-run the recall measurement in §4 after any
change to the model, the threshold, or the camera's aim: those numbers are
scene-specific and a re-aim can invalidate them.
