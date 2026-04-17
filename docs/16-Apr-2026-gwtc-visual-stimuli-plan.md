# 16-Apr-2026 — Flock visual-stimuli study (visual arm of the acoustic-response programme)

**Author:** Claude Opus 4.7 (1M context) — Bubba
**Status:** FIRST-PASS PLAN. Companion to `16-Apr-2026-flock-acoustic-response-study-plan.md`. Not approved; awaits Boss review and the technical unblocks listed in §Apparatus.
**Reading order:** read the audio plan first. This document repeats only the design pieces that *change* for the visual arm; everything else (cohort design, randomisation, analysis, ethics framing) inherits from the audio plan unchanged.

---

## Why visual on top of audio

The audio plan can answer "do they respond differentially to acoustic stimulus categories." It cannot answer the more interesting cross-modal questions:

- **Q5 (cross-modal congruence).** Does an aerial-predator *visual* stimulus presented alone produce the same response magnitude as the matching acoustic alarm — and does presenting both together produce a super-additive response (consistent with Tinbergen-style innate releaser + acoustic confirmation), an additive response, or a sub-additive one (saturation)?
- **Q6 (modality dominance).** When the visual and acoustic categories conflict — hawk silhouette + hen-cluck audio, or rooster crow + fox image — which modality drives the response, and does the answer differ between the chickens and the turkey poults?
- **Q7 (cross-modal habituation transfer).** Does habituation to the auditory hawk-scream transfer to the visual hawk silhouette and vice versa? If yes, the underlying response is "predator concept", not "modality-specific reflex" — a more interesting cognitive claim.

These questions are *the* methods-novelty case for this whole programme. There is published literature on each piece in isolation (Heinroth/Lorenz/Tinbergen on silhouettes, Marler/Evans on alarm calls, Davies/Maynard-Smith on multi-modal signal evolution), but not on a low-cost backyard automated multi-camera setup with a VLM in the loop adjudicating responses.

## Hypotheses (additional to the audio plan's H1–H5)

- **H6 (cross-modal congruence).** Mean response magnitude for `silhouette + scream` trials > `silhouette only` ≈ `scream only` > `silent control`. The combined-vs-each-alone contrast is the published-effect-size lever.
- **H7 (modality dominance — visual ≥ audio at this distance).** When categories are crossed (`silhouette + cluck` vs `silhouette + scream`), the silhouette dominates the response in the chickens. Predicted directionally because the screen is closer (~1-2 m at the perch) than any natural acoustic predator cue tends to be.
- **H8 (turkey ≠ chicken cross-modal).** Turkey poults show a different cross-modal weighting than chickens — predicted because turkey alarm-response ontogeny relies more heavily on acoustic-conspecific cueing than on innate visual templates (literature is thin; this would be the novel data point).
- **H9 (cross-modal habituation transfer).** Within-cohort habituation slope on the auditory hawk-scream during the audio-arm pilot generalises to the visual hawk-silhouette in the visual-arm pilot — i.e., naive birds respond more strongly to the silhouette than birds that have already heard 10 hawk-scream playbacks.

## Stimuli — visual library

Eight categories mirroring the audio plan, paired so multimodal trials are well-defined:

| # | Category | Audio analogue | Notes |
|---|---|---|---|
| V1 | Aerial predator silhouette (gliding diagonal) | A6 hawk_scream | Tinbergen-style — solid black silhouette of a buteo/accipiter shape on a sky-blue field, slow horizontal traversal across the screen over 3-5 s. Real species shape (red-tail / Cooper's), not generic raptor. |
| V2 | Falling-leaf control (same trajectory) | (no audio analogue) | Same diagonal traversal, same timing, neutral leaf-shape. Controls for "moving thing on screen" without predator content. |
| V3 | Conspecific image — adult rooster head-on | A2 rooster_crow | Static high-contrast image, 3-5 s display. |
| V4 | Conspecific image — hen with chicks (broody) | A3 hen_cluck | Static, 3-5 s. |
| V5 | Heterospecific predator image — red fox or raccoon | (no direct audio analogue; pair with A6 hawk_scream for cross-modal mismatch trials) | Static, 3-5 s. |
| V6 | Food cue — corn pile / scattered grain (top-down) | A7 insect_rustle | Static, 3-5 s. |
| V7 | Solid colour test — daylight-blue full screen | A8 silent_control | The "screen activates with neutral content" baseline. Equivalent to the audio plan's silent-but-speaker-fires trial. |
| V8 | Idle dashboard (the always-on ambient state) | A8b ambient | The state the screen is in *between* trials. Played as a "stimulus" trial periodically to confirm birds aren't responding to "dashboard appeared" instead of "stimulus appeared". |

**Sourcing rules** (mirroring the audio plan's MANIFEST.csv discipline):

- Public-domain or CC-BY only (we want to redistribute the dataset alongside any preprint).
- Image library lives at `tools/flock-response/visuals/` — same parent dir as `sounds/`, with the same per-category subdir + MANIFEST.csv pattern.
- Each image normalised to the screen native res (1366×768) at fixed luminance (target mean luminance per category recorded in MANIFEST so "the bird responded" can't be confounded with "this image was just brighter").
- Silhouettes drawn vector (SVG → PNG) so the silhouette shape itself is reproducible; species + wingspan-to-body-length ratio recorded in MANIFEST.

## Apparatus — the unblocks before any pixel hits a screen

The visual arm is *blocked* on these technical questions, in order. None should be answered tonight; all are daytime work.

1. **Screen physical orientation.** Boss confirms which way the GWTC screen faces. If toward the perch where birds roost, the visual arm is paused until the laptop is repositioned (or an external display is added for the bird-facing role). If toward an empty wall, the arm is moved to a location where birds will see the screen during *waking* hours — possibly that means waiting for a coop-furniture change.
2. **SSH → interactive desktop session.** Probe in daylight via `schtasks /Create /SC ONCE /ST <now+1min> /RU markb /IT /TR 'powershell -file <test.ps1>'`. The test script writes a known timestamped log file and opens a 2-second-displayed PowerShell `MessageBox`. Success criterion: the log file appears AND a Boss-side glance at the screen confirms the box appeared. Failure → fall back to PsExec or Startup-folder approach.
3. **Brightness / colour-as-light calibration.** With the screen confirmed pointing into the run, measure (via `gwtc` camera frames) the per-pixel response of the camera to a series of solid-colour fills (#000, #110000, #220000, #330000, …). Identify the brightness floor below which the fill is camera-invisible (= safe nighttime night-light range) and the floor above which the fill produces a startle response in awake daytime birds (= upper safety bound for any daytime stimulus).
4. **GPU / browser footprint.** With a Chromium kiosk fullscreen-displaying a static image for 5 minutes, log Guardian's `gwtc` capture FPS and frame-loss rate. If FPS drops or watchdog flaps, the visual arm must use a non-browser renderer (raw Win32 fullscreen window via .NET Forms, much lighter).
5. **Stimulus-onset timing.** Same as audio plan: round-trip from "Mac Mini sends trigger" to "frame is on the screen and visible" must be measured once, recorded as the per-trial `display_latency_ms`, used as a fixed offset in analysis.
6. **Speaker-and-screen co-location effect.** GWTC has both. Multimodal trials therefore have *zero* spatial separation between visual and acoustic source. This is a confound vs. natural conditions where a hawk's silhouette (sky) and scream (above) are roughly co-located but a hen-cluck and a chick image (ground) are co-located on a different axis. Document this as a known limitation; consider a future second display + speaker mounted at a different height as the v2 design.

## Trial protocol

Identical structure to the audio plan: T-30 s baseline → T0 stimulus onset → T+30 s response → T+120 s tail. Trial cadence ≥ 10 min apart (≥ 30 min same-category), no trial during a real predator detection.

The cross-modal trial set adds these cells per day:

| Audio | Visual | Trial cell name |
|---|---|---|
| (none) | V1 silhouette | visual-only-V1 |
| (none) | V3 rooster | visual-only-V3 |
| A6 scream | V1 silhouette | congruent-aerial |
| A6 scream | V5 fox | incongruent-mismatch |
| A2 crow | V3 rooster | congruent-rooster |
| A3 cluck | V4 hen-with-chicks | congruent-broody |
| A8 silent | V7 blue | both-baselines |

Counterbalance the cross-modal cells via a Latin square nested inside the day's randomisation, exactly as the audio plan does its category counterbalance.

## Response metrics

Inherits the audio plan's metrics (bird_count_delta, face_toward_rate, motion_density, alarm_posture_count, response_duration). Adds:

- `screen_directed_attention`: VLM judgment per frame on whether visible birds' bodies / heads are oriented toward the GWTC screen specifically (vs the rest of the run). Requires a one-time prompt addition to `tools/pipeline/prompt.md` and a corresponding schema field.
- `peck_at_screen_count`: bursts of motion within an ROI defined as the bottom-2/3 of the camera's view of the screen surface (where a chicken would peck). Captured by optical-flow analysis on the gwtc frame stack, not by the VLM.

The first metric requires touching the pipeline schema (an additive, non-breaking change). The second is computed entirely in `analyze.py` from already-archived frames. **Neither change is part of the scaffold tranche.** Schema additions + frontend coordination get their own plan + commit when the pilot is ready to start.

## Storage + DB schema

Either:

- (a) extend the audio plan's `flock_response_trials` table with `visual_exemplar` (nullable text), `audio_exemplar` (nullable text — already implied by the audio plan's `exemplar` field; rename to `audio_exemplar` for cross-modal clarity), and store cross-modal trials as single rows; OR
- (b) keep the table as audio-only and add a sibling `visual_response_trials` + a `multimodal_response_trials` join table.

(a) is simpler and lets the analysis treat audio-only / visual-only / multimodal as a single mixed-effects design. Recommend (a) for v1.

## Welfare deltas vs the audio plan

Tighter than the audio plan in a few places:

- **Visual stimuli are direction-specific.** A roosting bird at the perch can hear an alarm call regardless of head position; a roosting bird with eyes closed cannot be visually startled by a silhouette. This means the visual arm does *not* run during dark-phase / roost periods at all, even with the welfare floor in place. The audio plan's "no nighttime stimuli" rule applies more strongly here — there is no scientific case for a nighttime visual trial.
- **Daytime visual flash bound.** Maximum frame-to-frame luminance change ≤ 25% of full-white per second. No black→white flashes. Smooth fades only.
- **Stop on aggression toward the screen.** If a bird directly attacks the screen (peck force visible in optical flow + repeat within seconds), the trial aborts and that exemplar is retired pending review. Birds attacking laptop screens has equipment-damage downside on top of welfare downside.

## Open items for the next assistant

1. **Resolve the unblocks in §Apparatus 1-6.** Each is a 30-minute daytime task; do them before any code.
2. **Source the V1-V8 image library** — same MANIFEST + license discipline as the audio sounds.
3. **Pick storage option (a) vs (b)** — recommend (a); confirm with whoever's writing `experiment.py` for the audio arm so we converge on one schema.
4. **Schema addition for `screen_directed_attention`** — one-PR change to `tools/pipeline/schema.json` + `prompt.md` + `vlm_enricher.py`, mirrors the v2.28.6 `bird_face_visible` precedent.
5. **Fold visual + audio into one runner.** Best end-state is a single `experiment.py` that owns both modalities and the multimodal cross-product. Don't fork the runner.
6. **Pre-register both arms together** on OSF.io after the audio pilot, before either full run.
7. **Build display primitive (`display.py`)** — sibling of `playback.py`. Defaults to the safe-equivalent of `tada.wav`: a known-neutral image (e.g., the Windows default desktop wallpaper) for smoke-testing. CLI: `--remote-image PATH --duration-s SECS --host …`. Implementation depends on the §Apparatus 2 unblock.
8. **Build display latency measurement** — sibling of `measure_latency.py`. Same pattern: N timed displays of a neutral image, report median / p95 of the trigger-to-pixel-rendered latency. The "pixel rendered" detection requires either (a) a single-frame `gwtc` capture with timestamp comparison or (b) a screen-pixel-readback via PowerShell. (a) is simpler and uses tools we already have.

---

**done. plan only. zero code; zero device state changed. The visual arm is conditional on the §Apparatus unblocks; document them in this branch's PR description so Boss has the exact list of physical / configuration questions to answer when next at the coop.**
