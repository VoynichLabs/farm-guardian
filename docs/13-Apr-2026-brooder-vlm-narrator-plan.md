<!--
Author: Claude Opus 4.6
Date: 13-April-2026
PURPOSE: Plan for a standalone brooder narrator that periodically samples
         a camera snapshot, sends it to LM Studio's GLM 4.6v Flash with
         an open-ended prompt, and logs the model's textual interpretation.
         Per Boss directive: "we're going to see lots of pictures of baby
         chickens." Goal is interesting interpretive output, not
         classification.
SRP/DRY check: Pass — new tool, no overlap with the removed vision.py
               (which did per-detection species refinement and was removed
               in v2.17.0). This tool runs on a slow cadence, against a
               specific camera, with open-ended prompts — a different
               problem.
-->

# Brooder VLM Narrator — Plan (Draft for Approval)

## What it is, in one paragraph

A small standalone Python script that wakes up every N minutes, asks
Guardian for a snapshot of the brooder camera, sends the JPEG to GLM
4.6v Flash via LM Studio with an open-ended prompt, and appends the
text response to a JSONL log. The original image is discarded after
the call. Output is a growing chronological log of "what GLM thinks is
happening in the brooder right now," readable as a narrative.

Boss's wording was: "we're going to see lots of pictures of baby
chickens." This tool is for finding interesting needles in the
haystack of those pictures without us having to scrub footage.

## Scope

**In scope (v0.1):**
- Standalone script `farm-guardian/tools/brooder_narrator.py`
- One camera at a time (default: `usb-cam` = brooder)
- Periodic sampling (default 5 min between calls, configurable)
- One open-ended prompt mode at v0.1 ("describe what you see")
- JSONL log at `data/narrator/{camera}_{YYYYMMDD}.jsonl`
- Image discarded after VLM call (privacy + storage hygiene per Boss
  directive: "I don't want to save them forever")
- Optional debug mode keeps the most recent N images for spot-checking
- Uses Guardian's existing snapshot API — no reach into Guardian
  internals

**Out of scope (v0.1, future versions):**
- Multi-camera narration (trivial extension once v0.1 lands)
- Structured/categorical prompts (count chicks, activity coding)
- Anomaly detection / alerting on unusual responses
- Daily HTML summary
- Integration into Guardian dashboard
- Comparative-pair prompts ("what changed in the last hour?")
- Persistence of images for audit beyond the debug-mode rolling window

## Architecture

```
+-------------------+         +-------------------+         +------------------+
| brooder_narrator  |  HTTP   | Guardian dashboard|  capture| usb-cam (brooder)|
| (this tool)       +--------->  /api/v1/cameras/ <---------+                  |
|                   |  GET    |  usb-cam/snapshot |         +------------------+
|                   |         +-------------------+
|                   |
|                   |  HTTP POST                +------------------+
|                   +-------------------------->| LM Studio        |
|                   |  /v1/chat/completions    |  glm-4.6v-flash  |
|                   |  (image_url + text prompt)+------------------+
|                   |
|                   |  append
|                   v
|        data/narrator/usb-cam_20260413.jsonl
+-------------------+
```

**Module responsibilities:**
- `brooder_narrator.py` — sole responsibility: sample → ask → log
- Reuses Guardian's snapshot endpoint (no new code in `capture.py`)
- Reuses LM Studio's standard OpenAI-compatible chat endpoint
- Writes to `data/narrator/` (new subdir; no DB schema changes)

**Why standalone, not a Guardian module:**
1. Different cadence (slow). Guardian's pipeline runs at 5s intervals;
   this runs at 5-min intervals.
2. Different failure mode. If this tool dies, Guardian must keep
   running. If Guardian dies, this tool must back off cleanly.
3. Different concurrency profile. Guardian is multi-threaded
   real-time; this is a single sleep-then-call loop.
4. The removed `vision.py` taught us the lesson: VLM calls do not
   belong in the hot path. This is the slow-path version of the same
   capability.

**LM Studio coordination — explicit:**
- The narrator targets `zai-org/glm-4.6v-flash` specifically (vision-
  capable). Before each call, GET `/v1/models` to check what's loaded.
- If something else is loaded, the narrator **logs and skips** that
  cycle rather than auto-loading. Two reasons: (a) avoid the watchdog
  reset crash from earlier today (memory of which is in
  `~/.claude/projects/-Users-macmini/memory/project_lm_studio_guardian_vram_race.md`);
  (b) Boss may be running other LM Studio work and shouldn't be
  preempted by a chicken narrator.
- If `glm-4.6v-flash` is loaded, proceed.
- If nothing is loaded, attempt to load with `context_length=8192` (no
  long context needed for one-shot image+question), wait for verify,
  proceed.
- Single in-flight VLM call at a time (no parallelism). The 5-min
  cadence > GLM 4.6v inference time (~5–30s), so this is naturally
  serialized.

## The v0.1 prompt

```
You are looking at a snapshot from a chicken brooder.
This is a wooden box with heat lamp, chick crumble feeder, and a
shallow water station. There are about 22 chicks roughly 1-3 weeks
old. Describe what you see — what are the chicks doing right now,
where are they relative to the heat source, anything that looks
unusual or concerning, any individuals you can pick out by behavior
or position. Two or three sentences. Plain prose. No bullet points.
```

Two-three sentences keeps each response narrative-shaped and short
enough to skim a day's worth at a glance.

## Config

A small JSON file at `tools/brooder_narrator.config.json`:

```json
{
  "guardian_base": "http://localhost:6530",
  "lm_studio_base": "http://localhost:1234",
  "camera_id": "usb-cam",
  "model_id": "zai-org/glm-4.6v-flash",
  "interval_seconds": 300,
  "max_tokens": 200,
  "log_dir": "data/narrator",
  "debug_keep_last_n_images": 0,
  "prompt_file": "tools/brooder_narrator.prompt.md"
}
```

`debug_keep_last_n_images = 0` is the default — no images persisted.
Set to e.g. 20 during development to spot-check a rolling window.

## JSONL record schema

```json
{
  "ts": "2026-04-13T20:00:00-04:00",
  "camera_id": "usb-cam",
  "model_id": "zai-org/glm-4.6v-flash",
  "prompt_hash": "sha256:abc123...",
  "image_bytes": 487213,
  "image_sha256": "f00ba9...",
  "inference_ms": 12450,
  "response": "Most of the chicks are clustered under the heat lamp...",
  "skipped": null
}
```

`skipped` is a non-null reason string when the cycle was skipped
(e.g. `"different_model_loaded"`, `"snapshot_404"`,
`"lm_studio_unreachable"`).

## TODOs (ordered)

1. **Approve this plan** (Boss).
2. Create `tools/` directory if it doesn't exist; add
   `tools/brooder_narrator.py`, `tools/brooder_narrator.config.json`,
   `tools/brooder_narrator.prompt.md`.
3. Implement the script (~150-200 lines):
   - Config loading
   - Snapshot fetch with timeout + retry-with-backoff
   - LM Studio model-loaded check (read-only, no auto-load if wrong
     model present)
   - Optional model load with context_length cap
   - Image base64 encode + chat completion request (OpenAI
     compatible image content type)
   - JSONL append with daily-rotated filename
   - Debug-mode rolling image directory (only if N > 0)
   - SIGINT handler for clean shutdown
   - Verbose stdout logging for the operator
4. Add a launchd plist for boot-time start (OPTIONAL — Boss may want
   to run it manually first).
5. Run for 1-3 days, sample the JSONL log, decide if v0.2 (multi-
   camera, structured prompts, daily HTML summary) is warranted.

**Verification steps:**
- `curl -s http://localhost:6530/api/v1/cameras/usb-cam/snapshot
  --output /tmp/test.jpg && file /tmp/test.jpg` — confirm Guardian
  serves a JPEG
- Manually load glm-4.6v-flash in LM Studio
- Run `python3 tools/brooder_narrator.py --once` (one cycle, exit)
- Inspect the JSONL line + the response
- If response looks reasonable, run `--daemon` on the 5-min cadence

## Docs / Changelog touchpoints

- New file: `docs/13-Apr-2026-brooder-vlm-narrator-plan.md` (this doc)
- New file (on implementation): `tools/brooder_narrator.py` + config
- `CHANGELOG.md` top entry on implementation: v2.19.0 — "Brooder VLM
  narrator: standalone tool, sampled snapshots → glm-4.6v-flash →
  JSONL narrative log. Out-of-band from main Guardian pipeline."
- `CLAUDE.md` "Modules" section gets a new bullet under "Tools (not
  part of the main pipeline)" naming the script and pointing to this
  plan.

## Risks & open questions

1. **VLM quality on a 4-bit quant.** GLM 4.6v at 4-bit may hallucinate
   chick counts or invent details. The two-three sentence cap limits
   damage. We can switch to a sharper model if Boss adds one to LM
   Studio later.
2. **Brooder lighting.** Heat-lamp cast may make the image hard to
   read. v0.1 just sends what Guardian returns; if early outputs look
   bad, v0.2 can add a simple brightness/contrast preprocessing step.
3. **Cadence.** 5 min × 24 h = 288 calls/day. At ~15s/call that's
   ~72 minutes of GLM time per day, ~7GB VRAM held continuously.
   Cheap on this box. If Boss wants 1-min cadence, the math still
   works (about 6 hours of total VLM time spread across the day).
4. **What counts as "interesting"?** v0.1 produces a flat log of
   open-ended descriptions. v0.2 could add: anomaly scoring (LLM
   reads the day's log and surfaces the unusual entries), or a
   "diary mode" that asks GLM to write a daily summary referencing
   earlier entries.
5. **Should we tag entries with the brood's age?** Boss has 22 new
   chicks ~1-3 weeks old per the auto-memory. v0.1's prompt
   embeds that. As they grow, the prompt should evolve. v0.2 could
   read brood age from a small datafile and template it in.

## Why this is interesting (not just "more classification")

The removed `vision.py` (v2.17.0) was *closed-set classification*:
"is this chicken a hen or a rooster?" Boss correctly killed it as
over-engineered for our farm. This tool is *open-set narration*:
"what's happening in the brooder right now?" — a different question
that produces text suitable for human reading rather than a label
suitable for machine routing. The cost of the failure modes is also
different: a wrong species label silently corrupts visit-tracking
data; a hallucinated narrative line is just a weird sentence in a
log Boss will skim, which is recoverable.

---

**Approval requested before any code changes.** Boss said he'll kick
this off in a new session. This doc is the ready-to-implement plan;
no edits to existing Guardian code are required to ship v0.1 — only
new files in `tools/` + a CHANGELOG entry.
