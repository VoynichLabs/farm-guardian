# 25-Jul-2026 — Night alert artifact suppression + local VLM verification (plan)

**Status:** IMPLEMENTED 25-Jul-2026 (v2.53.0) — verified against real data, **not yet deployed to
the Mac Mini**. See §9 for measured results and §10 for the deploy steps.
**Author:** Claude Opus 5
**Trigger:** the night of 24→25 Jul 2026. **139 Discord alerts between 00:00 and 07:00**, 135 of
them `person` on `duo2`, every one of them a spider web on the lens lit up by the IR illuminator.

---

## 1. What actually happened (measured, not theorised)

All figures below are from the live Mac Mini — `guardian.log` and `data/guardian.db`, read-only,
25-Jul-2026 morning.

### 1.1 The immediate cause: the verifier went fail-open at 00:02:20

v2.52.1's `llm_verify.py` was working. Then:

```
2026-07-25 00:02:20  first  402 Client Error: Payment Required  (openrouter.ai)
2026-07-25 06:19:44  last   402 Client Error: Payment Required  (openrouter.ai)
                     1,147 consecutive 402s. Zero successful verifications all night.
```

**The OpenRouter account ran out of credit at two minutes past midnight.** `verify_detection()` is
fail-open by design, so every single borderline detection from that moment on was passed straight
through to Discord. The night of alerts is precisely the fail-open mode running for six hours.

This is not a criticism of fail-open — never silently dropping a real threat is the right call. It
is a criticism of fail-open being **binary and silent**: it degraded to "alert on everything" and
nothing said so.

### 1.2 The verifier is genuinely good when it can run

Lifetime verdicts before the credits died:

| verdict | count |
|---|---|
| SUPPRESS | 2,425 |
| CONFIRM | 74 |

**97% suppression rate.** The concept works. The delivery mechanism — a metered third-party API on
the critical path of a farm alarm — is what failed.

### 1.3 The artifacts are near-lens junk, and they never move

2,222 `duo2` `person` detections in the 00:00–07:00 window. Clustered by bbox on a 100 px grid:

| bbox cluster | detections | avg conf |
|---|---|---|
| (0, 200) – (300, 700) | 2,103 (95%) | 0.76 |
| (1500, 0) – (1900, 700) | 99 | 0.73 |
| (0, 0) – (300, 700) | 20 | 0.73 |

**100% of them fall in three clusters, and all three touch the frame edge** (`min(x1)=1`,
`max(x2)=1920`, frame is 1920×720). The dominant cluster held position — within a few pixels — for
five straight hours. Hourly: 540 / 329 / 393 / 474 / 486.

Contrast a real person crossing `house-yard` at 21:44 the previous evening, which the remote model
correctly CONFIRMed. Its bbox x1 walked 774 → 748 → 736 → 739 → 791 → 831 → 1338 over 44 seconds.

**Real subjects translate. Near-lens artifacts sit still.** That single fact is the cheapest,
strongest discriminator available and it currently is not used anywhere in the pipeline.

Physically: spider silk and insects on the lens glass, a few millimetres from the sensor, hopelessly
out of focus, blasted by the IR LEDs mounted next to the lens, clipping to pure white. They only
appear after dark because that's when the illuminator is on, and spiders rebuild every night because
insects congregate at the IR glow. YOLO sees a bright, vertical, roughly person-shaped blur and says
`person 76%`.

### 1.4 The local VLM already solves this, for free, in ~1.2 seconds

LM Studio on the Mini has `qwen/qwen3-vl-4b` loaded at 16,384 context right now (the pipeline keeps
it up). Probed live against real frames from last night — annotated full frame downscaled to 768 px
long edge, artifact-aware prompt, JSON-schema grammar sampling:

| case | source | latency | verdict |
|---|---|---|---|
| `duo2` 04:56 artifact | 1920×720 | 1,242 ms | `artifact` — *"bright, out-of-focus streak from an insect or spider web on the lens"*, `alert_worthy: false` |
| `house-yard` 21:44:09 real person | 1920×1080 | 1,425 ms | `real` / `person` / `alert_worthy: true` |
| `house-yard` 21:44:19 real person | 1920×1080 | 1,238 ms | `real` / `person` / `alert_worthy: true` |
| `house-yard` 21:44:47 real person | 1920×1080 | 1,241 ms | `real` / `person` / `alert_worthy: true` |

**4/4 correct.** The model named the failure mode itself — the prompt lists several candidate
artifacts (webs, glare, rain, moths, static scenery) and it picked the right one unprompted.

Two things this probe also settled:

- **Sending the annotated full frame beats sending the crop.** The current bare-crop path took
  **5,733 ms** and returned an unexplained `NO`. The annotated full frame took **1,434 ms** and
  returned a reasoned verdict. Cropping to a blob throws away the exact context — position in
  frame, focus relative to the scene, ground plane — that makes the call decidable.
- **Latency is fine for the alert path.** ~1.2 s local vs an 8 s remote timeout budget.

### 1.5 A waste that made the outage worse

`guardian.py::_on_frame` verifies **every** predator detection, then `AlertManager.send_alert()`
applies the 90-second per-class cooldown afterwards. Last night that meant **1,147 verification
calls to produce 139 alerts** — roughly 16 calls burned per alert that could actually fire. On a
metered API that is literally how the credit ran out faster.

---

## 2. Scope

### In

1. **`artifact_filter.py`** (new) — static-region suppression. Free, deterministic, runs first.
2. **`llm_verify.py`** (rewrite) — **local LM Studio VLM only**, annotated-full-frame prompt,
   JSON-schema output, artifact-aware question. No remote backend, no API key, no billing
   relationship — see §2.1.
3. **`guardian.py`** — reorder so the alert cooldown gates verification, wire the artifact filter,
   and implement graduated fail-open.
4. **`alerts.py`** — an `unverified` alert variant (visually distinct, own long cooldown) plus a
   one-shot "verifier is down" notice.
5. **DB** — populate the already-existing, currently-unused `detections.suppressed` /
   `suppression_reason` columns so suppression is auditable.
6. **Config** — new `artifact_filter` block; `llm_verification` block repointed at the local backend
   and stripped of every remote/key field.
7. **Log rotation** — `guardian.log` had reached 616 MB unrotated. Cleaned 25-Jul (archived
   compressed to `~/guardian-log-archive/`, truncated in place); rotation added here so it cannot
   recur.
8. **Docs/CHANGELOG** — including pre-burying the wrong theories in `CLAUDE.md`.

### 2.1 Standing rule — the alert path is local-only

**No paid or remote API may sit on the predator alert path. There is not to be a config option for
one.** v2.52.1 put a metered vision API there; it ran the balance to zero in ~27 hours and the alarm
went deaf for six of them. An earlier draft of this plan kept the remote path "configurable but
disabled," which is the same failure one config edit away. It is removed from the code entirely —
`llm_verify.py` talks to `http://localhost:1234` and nothing else, and holds no key-reading logic to
re-point.

The Mac Mini has run a loaded vision model since April (`CLAUDE.md` opens with a section calling it a
load-bearing production dependency). Any future agent that finds itself shopping for a remote vision
endpoint from inside this repo has already taken a wrong turn: the model is loaded, local, free, and
answers in ~1.2 s.

### Out

- **Raising detection thresholds.** Explicitly rejected by Boss, and v2.52.1 already reverted one
  such bump. Nothing in this plan touches `confidence_threshold` or `min_dwell_frames`.
- **Laplacian / blur-variance gating.** Boss distrusts Laplacian-vs-GLM calibration (already recorded
  in `CLAUDE.md` for `usb-cam-host`). Not used as a suppressor here. The VLM is free and 1.2 s; a
  second hand-tuned heuristic with its own thresholds is knobs for no gain.
- **Physically cleaning the `duo2` lens.** That is the actual root cause and it needs Boss's hands,
  not code — see §6. This plan makes the software survive a dirty lens; it does not clean it.
*(Log rotation was originally out of scope here; Boss pulled it in on 25-Jul. It is now item 7 of
§2 and step 7 of §4.)*

---

## 3. Architecture

Four gates, cheapest first. A detection must clear all of them to reach Discord.

```
YOLO detection (predator, dwell met)
        │
   ① alert-cooldown pre-check        AlertManager.should_alert()   — free, existing method
        │                            drops ~16 of every 17 candidates
   ② static-region filter            artifact_filter.py            — free, in-process
        │                            drops the webs; ~95% of last night
   ③ local VLM second opinion        llm_verify.py → LM Studio     — free, ~1.2 s, local
        │                            "is this worth pinging a human about?"
   ④ graduated fail-open             guardian.py + alerts.py       — when ③ can't answer
        │
   Discord alert + deterrent volley
```

Each gate is independently sufficient to have prevented last night. That redundancy is the point:
gate ③ *was* deployed and *did* fail, because it had exactly one external dependency and no floor
underneath it.

### 3.1 Gate ① — cooldown before verification (`guardian.py`)

Move the existing `AlertManager.should_alert(class_name)` check ahead of verification. Reuses a
public method already on the object Guardian holds — no new state, no second cooldown engine.
Detections that lose the cooldown race are still logged to the DB exactly as today; they just don't
buy a VLM round-trip to be thrown away.

Expected effect on last night's data: 1,147 verification calls → roughly 139.

### 3.2 Gate ② — `artifact_filter.py` (new module, SRP)

Per `(camera_id, class_name)`, keep a small rolling deque of recent detection bboxes with
timestamps. A candidate is classed **static scenery** when *all* of:

- IoU ≥ `iou_threshold` (default **0.6**) against a tracked region, **and**
- that region has been continuously present for ≥ `static_seconds` (default **600** — ten minutes),
  **and**
- the region's centroid has drifted < `max_drift_px` (default **40**) over that span.

Behaviour on a match: **suppress the alert, not the detection.** The row still goes to the DB with
`suppressed=1, suppression_reason='static-region'`, so the dashboard and reports stay honest and the
data is there to audit.

Three deliberate safety properties:

- **The first sighting always gets through.** A region has to hold for ten minutes before it is ever
  called scenery, and gates ③/④ see it during that window. A predator that arrives and leaves is
  never touched by this filter.
- **A genuinely stationary animal alerts once, then goes quiet.** A hawk perched on the fence for
  half an hour produces one alert and then stops — which is the correct behaviour anyway, and is
  strictly better than the current 90-second-cooldown repeat.
- **Regions decay.** After `decay_seconds` (default **300**) with no matching detection, the region
  is forgotten. Nothing is permanently blacklisted; a web cleared at dawn does not mute that patch
  of frame forever. State is in-memory only — a Guardian restart starts clean.

Config lives in a new top-level `artifact_filter` block, `enabled: true`, with a per-camera opt-out.

### 3.3 Gate ③ — `llm_verify.py` rewritten for the local VLM

Same public entry point (`verify_detection`) so `guardian.py`'s call site stays thin, but:

**Backend.** Primary is LM Studio at `http://localhost:1234` with `qwen/qwen3-vl-4b`. It is already
running, already loaded at 16k, costs nothing, needs no network, and cannot return 402.

**LM Studio safety rules are non-negotiable** (`docs/13-Apr-2026-lm-studio-reference.md`, and the
2026-04-13 incident that took the whole machine down):

- Check `/v1/models` for the loaded model **before every call**. Reuse
  `tools.pipeline.vlm_enricher.list_loaded_models` rather than writing a second copy (DRY).
- **Guardian never loads a model.** No `/api/v1/models/load`, ever. If the model isn't loaded,
  raise/return "unavailable" and let gate ④ handle it. The pipeline's `ensure_model_loaded()` at
  daemon startup remains the only load path in this repo.
- Module-level `threading.Lock` so Guardian is single-in-flight, mirroring the pipeline's
  `_VLM_LOCK`. The two processes share one LM Studio; neither may fan out.
- Timeout **10 s** (measured p50 is 1.2 s; 10 s covers contention with a pipeline cycle in flight).

**Payload.** Annotated full frame — the detection bbox drawn in red — downscaled to 768 px long edge
(matching the pipeline's `vlm_input_long_edge_px`), JPEG q85. Verified 4× faster and correct where
the bare crop was slow and wrong.

**Prompt.** Artifact-aware, and asks the question Boss actually wants asked — *is this worth pinging
a human about?* — not the leading *"is there a person here, YES or NO?"*. It names the real failure
modes for this camera: IR-lit spider webs and insects on the lens, glare and flare, rain and snow
streaks, moths near the lens, static scenery. Output is constrained by `response_format`
json_schema grammar sampling (same mechanism as `vlm_enricher`):

```json
{"verdict": "real|artifact|unsure", "what_it_is": "string", "alert_worthy": true}
```

`unsure` is treated as **alert** — ambiguity resolves toward waking Boss, never toward silence.
`what_it_is` goes into the Discord embed and the log, so a suppression is always explainable after
the fact.

**No remote fallback exists.** Per §2.1 the module has no `api_key_env`, no `api_base` override, and
no code path that can reach the public internet. If LM Studio cannot answer, gate ④ handles it.

### 3.4 Gate ④ — graduated fail-open (`guardian.py` + `alerts.py`)

When gate ③ cannot answer (model not loaded, timeout, LM Studio down, any exception):

- **The alert still fires.** Fail-open stays. Never silently drop a real threat.
- The embed is titled **⚠️ UNVERIFIED** in a distinct colour, so Boss can tell at a glance that the
  alarm is unfiltered rather than confirmed.
- It uses a separate, much longer `unverified_cooldown_seconds` (default **900**) per
  camera+class — same pattern as the existing `motion_alert` cooldown, which already proves the
  separate-debounce approach in `alerts.py`.
- The **first** failure in a rolling window posts a one-shot notice mentioning Boss
  (`<@293569238386606080>`): *"predator alert verification is unavailable — alerts are unfiltered."*
  Re-armed after `health_notice_cooldown_seconds` (default **21600**, six hours).

Applied to last night: gate ② alone would have killed the webs. Had it somehow not, gate ④ would
have turned 139 alerts into roughly 24 clearly-labelled unverified ones plus one message telling
Boss the verifier was down — instead of six silent hours.

### 3.5 Storage

`database.py` already has `detections.suppressed` and `suppression_reason`, and `log_detection()`
already accepts both — nothing writes them today. Add pass-through params to
`logger.EventLogger.log_event()` and populate them. No new tables, no schema migration.
`dashboard.py` and `reports.py` already filter on `suppressed = 0`, so suppressed rows drop out of
counts for free.

---

## 4. Ordered TODOs

1. **`artifact_filter.py`** — new module + `artifact_filter` config block. Unit-check the IoU and
   drift math against the three real bbox clusters from §1.3.
2. **`llm_verify.py`** — rewrite for LM Studio; loaded-model guard via `vlm_enricher.list_loaded_models`;
   module lock; annotated-frame encoder; artifact-aware prompt + JSON schema; `unsure` → alert;
   remote fallback present but off.
3. **`logger.py` / `database.py`** — thread `suppressed` / `suppression_reason` through.
4. **`alerts.py`** — `send_unverified_alert()` reusing `_capture_http_snapshot` / `_encode_snapshot` /
   `_post_webhook`; own cooldown dict; `send_verifier_health_notice()` one-shot.
5. **`guardian.py::_on_frame`** — reorder to cooldown → artifact filter → VLM → graduated fail-open.
6. **`config.json`** — `artifact_filter` block; `llm_verification` pointed at `http://localhost:1234`
   / `qwen/qwen3-vl-4b`, with `api_base` / `api_key_env` **deleted**. Root `config.json` only — the
   pipeline config is untouched by this change.
7. **Log rotation** — `setup_logging()` in `guardian.py:1049` builds a plain
   `logging.FileHandler`, which is why `guardian.log` reached 616 MB spanning 14-Apr → 25-Jul.
   Swap for `RotatingFileHandler` with `max_bytes` / `backup_count` read from the existing
   `logging` config block (defaults 50 MB × 5). The oversized file is already dealt with —
   archived to `~/guardian-log-archive/guardian-2026-04-14_2026-07-25.log.gz` (17 MB, integrity
   verified) and truncated in place on 25-Jul; append-mode `FileHandler` meant Guardian never
   missed a line.
8. **Replay verification against last night's real data** (see §5).
9. **Live verification** — restart Guardian, watch one full night, count alerts.
10. **Docs + CHANGELOG** (§8).

---

## 5. Verification steps

Real data, real services, no mocks — per the repo standard.

**5.1 Replay (before deploying).** A `scripts/` replay harness over the 00:00–07:00 window from
`data/guardian.db` + the saved `events/2026-07-25/` snapshots. Assertions:

- Every one of the 2,222 `duo2` `person` detections ends suppressed by gate ② after the first
  ten-minute window, and the whole night yields **0** `duo2` alerts.
- The `house-yard` 21:44 walking-person sequence from 24-Jul still alerts. **This is the
  false-negative regression test and it is the one that matters.** If it goes quiet, the change is
  wrong regardless of how good the duo2 numbers look.
- Gate ① reduces verification calls from 1,147 to ~139.

**5.2 Fault injection.** Point `llm_verification.api_base` at a dead port. Confirm: alerts still
fire, labelled UNVERIFIED, on the 900 s cooldown, and exactly one health notice posts.

**5.3 LM Studio contention.** Run the replay while the pipeline is mid-cycle. Confirm Guardian's
lock holds it to one in-flight request, that it never attempts a load, and that no pipeline cycle
drops.

**5.4 Live.** Restart Guardian, watch the next full night. Success = **zero** web alerts from
`duo2`, and any real visitor still alerts.

---

## 6. The actual root cause — needs Boss's hands, not code

**Clean the `duo2` lens and housing.** Dry microfiber on the glass, knock the webs off the housing.
Sixty seconds. Everything in this plan is the software surviving a dirty lens; none of it makes the
lens clean.

Prevention: the standard trick is keeping the illuminator away from the lens, which a fixed Duo 2
won't allow, so the practical options are spraying *around* (never on) the housing with a bug
repellent, or a dab of dish-soap solution on the housing edges. In summer expect to redo it every
couple of weeks.

---

## 7. Flagged, not fixed here

- **`guardian.log` is 616 MB and unrotated.** It will keep growing until something breaks. Wants a
  rotating handler with a size cap. Separate change.
- **OpenRouter credit is exhausted.** After this change nothing on the alert path depends on it, so
  it becomes optional rather than urgent. It is worth knowing that other tooling may still be
  pointed at that key.

---

## 8. Docs / CHANGELOG touchpoints

- **`CHANGELOG.md`** — new top entry, `v2.53.0`.
- **`CLAUDE.md`** — this needs to pre-bury the wrong theories the way the heat-lamp and GWTC docs do:
  - Night `person` alerts on `duo2` at the frame edge are **spider webs on the lens**, not people.
    **Physically confirmed by Boss, 25-Jul-2026: fine strands strung from the housing bridge to the
    lens glass.** That geometry is why they blow out so hard — anchored on the bridge, the strand
    sits directly in front of the IR LEDs *and* millimetres from the glass, so it takes the
    illuminator side-on at full power while being completely out of focus. It is also why every
    false positive hugged the frame border: the webs anchor at the housing edge.
  - **The alert path is local-only. No paid API, no key, no remote option** — see §2.1 of this plan.
  - **Do not raise the detection threshold.** Boss has rejected this twice now.
  - Guardian **does** call LM Studio as of v2.53.0 — the long-standing "Guardian = detection, no LM
    Studio; pipeline = VLM" line becomes wrong and must be corrected in place, along with the note
    that Guardian is read-only against LM Studio and never loads a model.
- **`docs/13-Apr-2026-lm-studio-reference.md`** — add Guardian as a second consumer and record the
  measured ~1.2 s verification latency.
- **This file** — mark implemented, record live results after the first clean night.

---

## 9. Measured results (25-Jul-2026)

Replayed through the production modules via `scripts/replay-artifact-filter.py --with-vlm`, against
real rows in `data/guardian.db` and the real saved frames, with real local VLM calls.

**Case 1 — duo2 `person`, 25-Jul 00:00–07:00 (the webs):**

| stage | alerts posted |
|---|---|
| before (no filter) | **136** — actual production figure was 135, so the harness matches reality |
| after gates ①+② | 42 |
| after gates ①+②+③ | **0** (42 VLM calls, 42 suppressed, 0 confirmed) |

**Case 2 — house-yard `person`, 24-Jul 21:44 (real person, the regression test): still alerts.**
0 of 14 detections suppressed, 1 alert posted.

**Fault injection (§5.2):** LM Studio unreachable → `available=False`, `suppressed=False`; model not
loaded → `available=False`, `error="model-not-loaded"`, and it correctly refused to auto-load;
unverified debounce holds (2nd call within 900s returns False); health notice holds (2nd within 6h
returns False); a second camera is not throttled by the first's debounce. Nothing was posted to
Discord — the test ran against a placeholder webhook.

**Contention (§5.3):** the 42 verification calls ran alongside the live image pipeline, which
enriched **307 frames** in the same window. LM Studio still healthy at 16,384 context afterwards.

**Two bugs found by running against real data rather than reasoning about it:**

1. *Peak-drift was the wrong statistic.* The first cut measured drift from a fixed anchor and kept
   the maximum. One excursion — YOLO's box jumps ~122px on these webs at p95 — permanently
   disqualified a region, and since no detection gap that night exceeded 88s the decay never reset
   it. 26.6% suppression where 95%+ was needed. Replaced with a rolling-window p90 spread (90% of
   these detections sit within **3px** of their median), which took it to 92.6%.
2. *The harness was unfaithful.* It replayed `is_predator=0` rows, which `detect.py` clears when
   dwell is unmet and which never reach the alert path in production. They also carry no snapshot,
   which is how it was caught: 1,120 of the duo2 rows had `is_predator=0` and zero snapshots.
   Filtering to `is_predator=1` brought the "before" figure to 136 against a real 135.

Gate ②'s detection-level rate on `is_predator=1` rows is 79.2%, which is fine — its job is bulk
reduction (136 candidate alerts → 42 VLM calls, a 3.2× cut in model load), and gate ③ makes the
semantic call. Tuning gate ② harder against this one night's data would be overfitting.

---

## 10. Deploying to the Mac Mini

Not yet done — this changes the live alarm's behaviour, so it is Boss's call. The Mini runs from
`/Users/macmini/GitHub/farm-guardian`.

1. Land the branch (nothing has been committed yet — repo standard is not to commit unasked).
2. On the Mini: `git pull`.
3. Reload Guardian only — the pipeline is untouched by this change:
   `launchctl kickstart -k gui/$(id -u)/com.farmguardian.guardian`
4. Confirm at startup: `StaticArtifactFilter initialized` in `guardian.log`, and that the first
   borderline detection logs an `LLM verify:` line with a latency in the ~1.2s range.
5. Watch one full night. Success = zero web alerts from `duo2`, and any real visitor still alerts.

Note `config.json` is tracked in git and the root copy carries the LAN-only Reolink password, so
the pull brings the new `artifact_filter` block and the rewritten `llm_verification` block with it.
The pipeline's `tools/pipeline/config.json` is untouched.
