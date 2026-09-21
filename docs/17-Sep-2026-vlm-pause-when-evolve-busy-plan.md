# 17-Sep-2026 — Pause VLM enrichment while the evolve jobs are busy

**Author:** Claude Opus 5
**Status:** SHIPPED 17-Sep-2026 (v2.72.4)

## Why

Boss runs `~/kg-lab-runtime/code/kaggriculture/lab/evolve.py` on this Mini. When it spawns its
multiprocessing workers it takes ~5.2 of 14 cores (measured 17-Sep-2026 10:41: six workers at
~87% each, load average 12.99). He wants that job to have the CPU, and asked that the VLM stop
competing — "only when evolve is actually busy", with **everything else staying on**.

Proportionality, stated plainly so nobody over-builds this later: the pipeline's own Python
process measured **18–30% of one core**, and the model sits `IDLE` between cycles. This buys back
roughly **a third of a core**. It is a small win; the design must therefore be small, and must
not cost anything else.

## Scope

**In:**
- A per-cycle skip of VLM enrichment in the pipeline, driven by a sentinel file.
- A watcher that creates/removes the sentinel based on whether evolve is actually burning CPU.

**Out:**
- Stopping the pipeline LaunchAgent. Rejected: `bootout` also stops **frame archiving**, which
  (a) contradicts "everything on", (b) thins the reels, and (c) removes the
  `image_archive`-freshness leg that `birdcatraz-watchdog` uses to corroborate a circuit-trip
  verdict — the exact weakening v2.71.8 existed to fix.
- Loading/unloading/swapping LM Studio models. The model stays resident. This keeps the night
  alert verifier (`llm_verify.py`, read-only) working, keeps `lmstudio-watchdog` quiet, and
  respects the standing ⛔ rule that agents never touch LM Studio model state. An idle resident
  model costs no CPU.
- Any new config key plumbed through both config files. Not worth it for this payoff.

## Architecture

**Sentinel file `/tmp/farm-vlm-off`** — the repo's existing kill-switch idiom
(`/tmp/ig-engage-off`, `/tmp/nextdoor-off`). Chosen over a config flag because it needs no
service restart, so nothing flaps and `ensure_model_loaded` is not re-run on every transition.
It doubles as a manual lever for Boss or a future agent.

1. **`tools/pipeline/orchestrator.py`** — single call site at the `enrich(...)` call (line ~897).
   When the sentinel exists, skip enrichment and record the frame with a `gated`-style status of
   `vlm_paused`. Capture, exposure gating, archiving, pruning and retention all continue
   untouched. Logged at most once per transition, not per frame.

2. **`tools/vlm-pause-watchdog/watchdog.py`** (new) + `com.farmguardian.vlm-pause-watchdog`
   LaunchAgent, 3-minute interval. Each tick:
   - Find evolve processes by **command pattern**, never a cached PID (the PID changed 43622 →
     68270 across one day; it is a recurring job).
   - **Sum CPU across the whole process tree.** The parent measured **0.0%** while alive for
     5h20m; only the workers burn CPU, so a parent-only threshold would read permanently busy.
   - **Hysteresis:** 2 consecutive busy ticks to pause, 3 consecutive idle ticks to resume.
   - **Fail safe:** if the probe errors, leave the current state alone and log it. A malformed
     probe must never read as "idle" — a bad `pgrep -c` did exactly that on 17-Sep and produced
     a wrong "the jobs have finished" report.

## TODOs

1. Plan approval.
2. Sentinel check at the `enrich` call site; update the file header.
3. Watcher script + plist; `bootstrap` it.
4. Verify: with sentinel present, archive rows keep arriving and no `/v1/chat/completions` call
   is made; remove it and enrichment resumes. Confirm a night alert still verifies while paused
   (model resident).
5. CHANGELOG top entry (SemVer minor).
6. CLAUDE.md note — a future agent finding enrichment paused **will** otherwise "fix" it.

## Docs/Changelog touchpoints

`CHANGELOG.md` (new top entry), `CLAUDE.md` (short subsection near the LM Studio rules pointing
here), this plan doc.

## Outcome (17-Sep-2026)

**Built smaller than planned.** The orchestrator needed **no change at all**: a pause gate
already existed at `tools/pipeline/orchestrator.py:885` reading `/tmp/farm-pipeline.pause`,
shipped from the 29-Apr-2026 control-plane plan. The watchdog drives that flag. Only two new
artifacts: `tools/vlm-pause-watchdog/watchdog.py` and its LaunchAgent.

**Verified:**
- Idle baseline: 3 ticks, no pause set.
- Two synthetic workers at ~199% CPU: paused on the 2nd busy tick, flag written with the owner
  marker.
- Workers killed: resumed on the 3rd idle tick.
- Foreign pause (`"paused by Mark"`): survived 3 idle ticks untouched.
- Probe failure (`ps` unavailable): logged an error and held state; did not resume.
- Heartbeat: one line per tick in `/tmp/vlm-pause-watchdog.err.log`. Added after the first load
  ran twice and logged **nothing**, because `pause()`/`resume()` both return early in steady
  state — a healthy watchdog looked dead.

**Not verified live, and why:** no `status: "paused"` cycle was observed, because `s7-cam` is the
only camera on the VLM path and at 19:00 every one of its frames was already rejected by the
darkness gate (`too_dark p50=1.0`), which runs *before* the pause gate. The gate itself is
long-shipped production code, but its interaction with this watchdog will first be exercised in
daylight. Worth a glance at the pipeline log the next time evolve runs during the day.

**Finding that contradicts the original request.** Boss asked for the VLM off "especially at
night". Overnight is when it is already doing the least: the only VLM camera is dark-gated, so
there is close to nothing to pause. The lever only bites in daylight. Reported to him rather than
quietly implementing a night schedule that would have saved nothing.
