# Audio-Triggered Capture — Tomorrow's Plan

**Date:** 14-April-2026 (plan for)
**Author:** Claude Opus 4.6 (plan drafted 13-Apr-2026 evening)
**Status:** Drafted — awaiting Boss review / approval before implementation
**Related:** `docs/13-Apr-2026-multi-cam-image-pipeline-plan.md` (the VLM pipeline this feature leans on)

---

## Boss's Ask (13-Apr-2026 evening, paraphrased)

> "We've definitely got audio capabilities because the USB cam has a microphone. Maybe you could figure out something to capture a picture when there's a lot of noise?"

The `usb-cam` (pointed at the brooder) is the obvious first home for this. `ffmpeg -f avfoundation -list_devices true -i ""` confirms the device exposes a microphone at AVFoundation audio index `[1]` (same hardware as video index `[0]`). The Reolink, the S7, the MacBook Air, and the Gateway laptop all also have mics — they're out of scope for v1 so we stay simple; generalizing comes later.

## What This Gives Us That Pure Visual Doesn't

- **Off-frame predators.** An owl hoot or a coyote yip outside the camera's field of view won't trigger YOLO on the current frame. A mic catches it and pulls a snapshot in the same instant so we can at least *correlate* a visual with the sound (chickens freezing, looking up, etc.).
- **Distress calls.** Hens and chicks make distinct "alarm" vocalizations. A loud spike at 2 AM in the brooder is much more interesting than the ambient heat-lamp hum we currently get no signal on at all.
- **Events we already have but can't correlate.** Boss opens the brooder door (creak + footsteps), a chick tips the waterer (splash), the heat lamp fan speeds up (compressor-like whirring) — all are useful labels for the archive, but YOLO doesn't distinguish them. Audio does.

## Scope

**In:**

- A new standalone process `tools/audio-trigger/watcher.py` on the Mac Mini that continuously reads the `usb-cam` mic via AVFoundation (`ffmpeg -f avfoundation -i ":1"` or `sounddevice` on CoreAudio — both are viable; pick at impl time based on whichever needs fewer deps).
- RMS / peak-dB threshold trigger with a rolling baseline so "loud" is defined *relative to current ambient*, not an absolute dB.
- When the trigger fires:
  1. Capture a still via Guardian's existing `GET /api/v1/cameras/usb-cam/snapshot` endpoint (reuses the USB capture pipeline, AF, WB, warmup — no new capture code).
  2. Save the short audio clip (~5s pre-roll + 2s post-roll from a rolling buffer) as WAV next to the snapshot.
  3. Write a row to a new `audio_events` SQLite table in `data/guardian.db`.
  4. Optionally hand the snapshot + textual audio context ("captured because of an 18 dB-above-ambient spike at 02:14 ET") to the existing VLM pipeline for narrative enrichment.
- **Dedup / cooldown:** one event per N seconds (start with 30s) to avoid a single barn-door-slam spawning 40 captures.
- **Hard-capped storage:** cap `audio_events` at 30 days of WAVs before auto-deletion (the row stays, the audio file gets pruned). Same 90-day horizon as the VLM pipeline's `decent` tier.

**Out of scope (v1):**

- The other four cameras' microphones. (Reolink's audio API, the S7 IP Webcam audio, the Air / GWTC built-in mics — each is its own investigation. Don't try to design for all five on day 1.)
- Acoustic classification (YAMNet, BirdNET, etc.). Get a threshold trigger working first, see what the false-positive rate looks like, *then* decide if a classifier buys enough to justify the dependency weight.
- Continuous recording / streaming audio off-site. We're event-triggered only, with short clips. Continuous would be a totally different storage / privacy conversation.
- Live audio playback in the dashboard. If Boss wants to click an event row and hear the clip, that's a v2 feature.

## Architecture

```
┌────────────────────────────────┐
│  usb-cam mic (AVFoundation)    │
│  index :1, same device as video│
└──────────────┬─────────────────┘
               │ continuous PCM @ 16kHz mono
               ▼
┌────────────────────────────────┐
│  tools/audio-trigger/watcher.py│
│  - 5s rolling pre-roll buffer  │
│  - 500ms RMS window            │
│  - rolling-baseline dB ref     │
│  - cooldown timer (30s)        │
└──────────────┬─────────────────┘
               │ on trigger:
               ▼
┌────────────────────────────────┐
│  Guardian API                  │
│  GET /api/v1/cameras/usb-cam/  │
│      snapshot                  │  ← reuses existing AF + warmup + WB path
└──────────────┬─────────────────┘
               │
               ▼
┌────────────────────────────────┐
│  data/archive/2026-04/         │
│    audio-events/               │
│      2026-04-14T02-14-07.jpg   │
│      2026-04-14T02-14-07.wav   │  ← ~7s clip, 16kHz mono = ~220 KB
│      2026-04-14T02-14-07.json  │  ← sidecar: peak_db, baseline_db, duration
│                                 │
│  + new SQLite row in            │
│    audio_events table           │
└────────────────────────────────┘
               │
               ▼ (optional, v1.5)
┌────────────────────────────────┐
│  tools/pipeline/orchestrator   │
│  Enqueues the snapshot with    │
│  extra prompt context          │
│  ("audio trigger: 22 dB above  │
│  1-min rolling baseline")      │
└────────────────────────────────┘
```

## Files to Add

- `tools/audio-trigger/__init__.py` — package marker.
- `tools/audio-trigger/watcher.py` — the listener + trigger loop + archive writer. Single-file, ~200 lines. `ffmpeg -f avfoundation -i ":1" -f s16le -ar 16000 -ac 1 -` into a subprocess pipe; parse PCM; maintain baseline + rolling buffer; on trigger, `requests.get(http://localhost:6530/api/v1/cameras/usb-cam/snapshot)`, `wave.write(clip)`, INSERT into `audio_events`.
- `tools/audio-trigger/calibrate.py` — one-shot utility that records 60s of ambient audio, prints dB distribution, suggests a threshold. Run once at deploy time + any time the mic physically moves.
- `tools/audio-trigger/config.json` — tunables: device selector, threshold-above-baseline-dB, baseline-window-seconds, pre-roll/post-roll seconds, cooldown-seconds, sample rate, archive directory, `enabled` flag. Ship with conservative defaults; expect Boss to tune after the first night of data.
- `tools/audio-trigger/README.md` — install / run / stop / calibrate / tuning notes.

## Files to Modify

- `database.py` — new `audio_events` table migration (additive, idempotent `CREATE TABLE IF NOT EXISTS`, columns: `id`, `ts`, `camera_id`, `peak_db`, `baseline_db`, `peak_minus_baseline`, `clip_path`, `snapshot_path`, `sidecar_path`, `yolo_classes_json`, `vlm_caption`, `has_concerns`, `retained_until`). Index on `(ts)` and `(camera_id, ts)`. **No schema change to any existing table.**
- `dashboard.py` — add a new `/api/audio-events` endpoint returning recent events (paginated). Thin wrapper over the new table. Don't add streaming audio — just metadata + image URLs for now.
- `HARDWARE_INVENTORY.md` — add a note in the `usb-cam` row: "also exposes a microphone at AVFoundation audio index `[1]`; used by `tools/audio-trigger/` for event-driven capture."

## Files NOT to Touch

- `guardian.py` — the listener runs as its own process. Don't fold audio logic into the main Guardian loop; it has a completely different cadence and failure mode (continuous I/O vs. per-frame tick), and coupling them makes both harder to debug.
- The live video path. `capture.py`, `detect.py`, `tracker.py`, `deterrent.py` — all unchanged.
- The existing VLM pipeline. Feed audio events *into* it via its existing camera-snapshot-handling path; don't fork a second pipeline.

## Calibration — How to Set the Threshold

Absolute dB thresholds are useless (they depend on mic gain, physical placement, room noise). Use a **rolling baseline + fixed offset**:

1. Every second, compute the median dB of the previous 60 seconds of audio windows. This is the "ambient" floor.
2. Trigger when the current 500ms peak exceeds `ambient + delta_db`. Start with `delta_db = 15`. Calibrate:
3. `calibrate.py` records 5 minutes with the mic in its real position (brooder, heat lamp running, fan noise, water drip). It prints the dB distribution. Pick `delta_db` such that ambient variation stays below trigger but a moderately loud event (clap test, brief voice) reliably fires.
4. After the first full day of data, look at `audio_events` and see whether the trigger rate is reasonable (a few events per day, not per minute). Tune `delta_db` up or down.

## Privacy / Sensitivity

The `usb-cam` is pointed at the brooder, which is inside the house. The mic will pick up:

- Ambient brooder sounds (chicks, heat lamp).
- Boss's voice when he's in the room.
- Visitors, family, phone calls.
- The neighbor's leaf blower through the wall.

Policy:

- **Audio clips are private by default.** Never exposed via the Cloudflare tunnel. Never published to `farm.markbarney.net`. Stored under `data/archive/.../audio-events/` with the same backup-and-retention rules as the existing archive.
- **Event metadata is fine to surface** on the local dashboard (dB level, timestamp, triggered snapshot). Don't ship it to the public site either — audio events feel intimate in a way bird photos don't.
- **The snapshot itself** follows the existing share-worth rules: if the VLM pipeline tiers it as `strong`, it can go to Instagram / the site like any other archive image; it just won't carry the audio with it.
- **If `has_concerns` is set** on the sidecar (e.g., VLM flags something private the audio revealed), the row is exempt from auto-deletion and is *never* promoted out of the archive. Same rule the VLM pipeline uses for photos.

## Implementation Order (14-Apr-2026)

1. **Verify the mic works end-to-end** (15 min). `ffmpeg -f avfoundation -i ":1" -t 10 -ar 16000 -ac 1 /tmp/test.wav`, play it back, confirm it's picking up actual audio and not garbage. If TCC prompts for microphone access, grant it (same ffmpeg binary, so if it already has Camera permission the Mic prompt is a one-time grant — and the prompt *will* surface since this runs from the shell / Guardian service, not launchd).
2. **Write `calibrate.py`** (30 min). Record 5 minutes, emit a histogram and a recommendation.
3. **Write `watcher.py`** (90 min). Subprocess pipe from ffmpeg, rolling-baseline + trigger logic, cooldown, archive writes, DB inserts. Use `threading` for the ffmpeg-read loop so the main loop can handle HTTP timeouts gracefully.
4. **Migrate the DB** (10 min). `audio_events` table via `CREATE TABLE IF NOT EXISTS`. Verify schema in the Guardian DB.
5. **Smoke test** (30 min). Run with `delta_db = 8` (very sensitive) for 5 minutes while making known noises (clap, voice). Confirm events land in the DB, snapshots are being saved, clips are playable.
6. **Wire the dashboard endpoint** (30 min). `/api/audio-events` returning the last N rows.
7. **Let it run overnight on the real setting** (no work — just leave it).
8. **Review the morning after** (interactive). Open the DB, look at the 14-Apr → 15-Apr events. Tune `delta_db`. If false-positive rate is brutal, consider adding a cheap frequency filter (e.g., high-pass the heat-lamp fan's fundamental) before calling it v1.

**Budget:** ~4 hours of focused work + 1 overnight soak test. If any step stretches significantly beyond its budget, stop and reassess — the goal is to have *something* running before end of day, not a perfect thing.

## Open Questions for Boss (answer when you wake up; don't block on this)

1. **Dedup cooldown** — 30s feels right for a brooder (a single "event" is typically one sound). If the audio is on a predator-approach camera later, 30s might lose the chase. OK to keep as a per-camera config so we don't have to rewrite when it generalizes?
2. **Audio retention** — 30 days of WAV files at ~220 KB/event × ~50 events/day = ~330 MB. Trivial. But you might want *longer* retention on flagged events (distress calls you'd want to audit months later). Say "yes" and we'll tie it to `has_concerns` like the photos are.
3. **Mic gain in the AVFoundation device** — macOS sometimes applies automatic gain that compresses loud events back down. If the trigger keeps missing obvious loud sounds, we may need to disable AGC via Audio MIDI Setup. One-time GUI fix, noted in the README.
4. **Generalizing to other mics.** Not now, but the design anticipates it: `config.json` takes a camera name + AVFoundation audio index (local) or an RTSP URL (remote). The `mba-cam` Air mic and the Reolink audio stream are the two most interesting v2 targets.

## Done-When

- `tools/audio-trigger/watcher.py` is running as a `nohup`'d process on the Mac Mini. (Future session: convert to launchd plist alongside guardian.py and the pipeline daemon — see the v2.23.0 open items.)
- `data/guardian.db` has an `audio_events` table with at least one real row in it.
- `GET http://localhost:6530/api/audio-events` returns a non-empty list.
- `HARDWARE_INVENTORY.md` is updated.
- `CHANGELOG.md` gets a v2.25.0 entry.
- The threshold has been tuned once based on real overnight data, so we're not shipping something that triggers on every heat-lamp cycle.

## Cross-references

- `HARDWARE_INVENTORY.md` — device table; `usb-cam` row will get the mic note.
- `docs/13-Apr-2026-multi-cam-image-pipeline-plan.md` — the existing VLM pipeline this hooks into.
- `docs/13-Apr-2026-lm-studio-reference.md` — safety rules for calling LM Studio (unchanged; the audio trigger only uses LM Studio by enqueuing snapshots into the existing pipeline).
- `CLAUDE.md` "Multi-Machine Claude Orchestration" — when audio generalizes to the other hosts' mics, we use the same pattern.
