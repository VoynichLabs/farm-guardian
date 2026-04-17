# 16-Apr-2026 — GWTC as a coop-side stimulus / sensor / display node: capabilities brainstorm

**Author:** Claude Opus 4.7 (1M context) — Bubba
**Status:** BRAINSTORM. Not a plan; not approved. Intended to surface the design space so Boss can choose what's worth building. Anything "experimental" here implies a separate plan doc and welfare review before code.
**Companion docs:** `16-Apr-2026-flock-acoustic-response-study-plan.md` (audio arm — committed). `16-Apr-2026-gwtc-visual-stimuli-plan.md` (visual arm — committed alongside this).

---

## What Boss asked

> "Let's think about all the cool stuff we could be doing with GWTC to interact with our flock out there… Could we be turning on a little night light? Could we display something on the screen for them?… You've got full access to that GWTC; it does nothing other than sit in the chicken coop. Be creative. Pretend like you have an IQ of 160."

The audience right now (2026-04-16, ~21:00 ET): ~3-week-old chicks and ~1.5-week-old turkey poults, all in the GWTC coop run. Boss directly asked about *tonight*. The first answer in this doc is therefore "what we will *not* do tonight, and why" — without that, every other idea here is irresponsible.

## Welfare floor (non-negotiable, applies to every idea below)

1. **No on-flock illumination or sound stimulation tonight, period.** 3-week chicks and 1.5-week poults are roosting. Their sleep architecture matters at this age — disrupted dark-phase rest is associated with measurable fearfulness and fitness costs in young poultry. The "red light is invisible to birds" heuristic is partially true for adult-bird *retinal* photoreception but young poultry have a deep-brain photoreceptor + pineal axis that responds to red light differently than the eye does. Default to dark.
2. **Build and calibrate during the day.** Even night-light infrastructure gets prototyped at noon, when "screen turned warm" is within natural variance and a startled chick has a parent and sunlight to recover with.
3. **No strobe / flicker / fast colour transitions, ever.** Some breeds have known seizure susceptibility; even where the breed-specific literature is thin, flicker is a low-upside, real-downside lever.
4. **Stimuli at most as loud as a person speaking nearby.** ≤ 65 dB at the nearest perching point. Same rule as the acoustic-response plan.
5. **High-arousal stimuli (alarm calls, predator silhouettes, hawk-screams) are research instruments, not toys.** One per cohort per day cap, and *never* aimed at the farm's own flock as a "deterrent" — repeated unconditioned predator stimuli on the bird you're trying to protect teaches them to ignore the real thing.
6. **Stop-on-distress.** If the bird-side response (visible motion, vocalisation, postural alarm in the camera) crosses a threshold, the experiment aborts the rest of the day's trials — not just the current one.
7. **Boss has the override.** Every script defaults safe; "actually do the thing" is an explicit flag. The default of `playback.py` being `tada.wav` is the template for everything that follows.

If anything below conflicts with these seven rules, the rule wins.

---

## What GWTC physically is, and what that buys us

GWTC = Gateway laptop, Windows 11 (`653Pudding`), Intel UHD 600, 1366×768 native. Sits in the coop. Plugged in (battery 100%, charging). Currently the entire flock-side instrumentation node:

- **Webcam** — already wired into the Guardian fleet as the `gwtc` camera, 720p H.264 over RTSP via MediaMTX :8554. Looking *into* the coop run.
- **Display** — 1366×768. Active right now (verified 2026-04-16 20:46: GPU reports current resolution at native). Where it's pointed *physically* is open — see Open Technical Questions.
- **Built-in speakers** — proven end-to-end via SSH+PowerShell PlaySync (audio plan Appendix B; reverified in this branch's `playback.py` smoke test, ~2.8 s round-trip).
- **GPU** — Intel UHD 600. No CUDA. Plenty for a fullscreen browser, a dim solid-colour fill, an HTML5 Canvas animation, or a 720p video. Not enough to share with a YOLO inference workload — that's a feature, not a bug; the inference lives on the Mini, GWTC just has to render and play.
- **Microphone** — built into the webcam. Out of scope for v1 audio-trigger (the Mac Mini's USB cam mic was chosen instead per the 14-Apr audio-trigger plan), but available later.
- **Network role** — already serves MediaMTX RTSP + has Windows OpenSSH server. Boss has key-auth as `markb`. Headless control works.
- **Power** — plugged in. Display sleep behaviour matters (see open questions). No sleep-on-lid-close concern because we should not be relying on the lid state to drive the camera (separate hardware question for Boss).
- **Shared-resource discipline (HARD CONSTRAINT):** GWTC also runs `mediamtx`, `farmcam` (the ffmpeg dshow → MediaMTX pipeline), and `farmcam-watchdog` Shawl services. Anything we add MUST: (a) be a separate process, (b) yield CPU/GPU promptly, (c) be killable without touching the camera services, (d) NOT interact with the dshow video device or anything ffmpeg holds. A fullscreen kiosk browser at 1 FPS animation is fine; a WebGL particle storm is not.

---

## The design space, ranked by value × tractability

### Tier 1 — high value, plausibly tractable in days

**1.1 Visual-arm of the acoustic-response study (multimodal pairing).** The single biggest scientific unlock here. Tinbergen / Lorenz hawk-vs-goose silhouette is the canonical result that started ethology; nobody has redone it on a backyard mixed-species flock with a VLM-in-the-loop response measurement. Pair it with the existing audio plan's hawk-scream category and you get cross-modal trial cells (silhouette-only, scream-only, both, neither) the audio plan can't test alone. **Companion plan doc: `16-Apr-2026-gwtc-visual-stimuli-plan.md`.** Build target: weeks. Welfare class: trial-mode only, Tier-1 stimulus rules apply.

**1.2 Daytime "ambient screen" baseline.** GWTC's screen is on most of the time anyway (there's a logged-in `markb` interactive session). Display the farm-2026 dashboard, the gwtc camera mirror, or a simple muted slideshow as the **always-on baseline state**. Two payoffs: (a) Boss can glance at the screen during coop visits for live telemetry, (b) it makes "something novel was displayed" a much cleaner contrast in the visual experiments — the birds get habituated to "screen has stuff on it, no big deal" so a real stimulus doesn't carry "the screen lit up" as a spurious cue. Build target: 1-2 days. Welfare: net positive (gentle low-contrast content, no novelty).

**1.3 Closed-loop operant conditioning sandbox.** This is the IQ-160 idea. GWTC camera → optical-flow / YOLO gates a region-of-interest in front of the screen → if a bird pecks within the ROI of a displayed coloured target, GWTC plays a "good" sound and shows a brief food-image. Touch-screen pecking discrimination is a real, cited literature in poultry research (Rugani on number, Smith on visual). The farm has every piece (camera, display, classifier, audio); nobody has assembled them in a backyard setting. **A "hello world" version is a 1-pixel discrimination task; a publishable version is a 4-shape forced-choice with hundreds of trials per week, fully automated.** Welfare: positive when reward-based (this is enrichment, not stress). Build target: 2-4 weeks for hello-world; pre-reg for the publishable version is its own plan.

**1.4 Coordinated multi-camera predator response (research, not deterrent).** When `house-yard` YOLO detects a `bird` of suspicious size/trajectory in the predator-arrival zone, GWTC plays the matching alarm-call audio + shows the matching silhouette. This is *itself* a research instrument — does the flock's response generalise across stimuli, does it differ from naive playback, does cross-cohort variance change after one real predator visit followed by one matching playback. Importantly: NEVER as a deterrent. Repeatedly broadcasting unconditioned predator stimuli on your own flock conditions them to *ignore* the real thing. Welfare: research-only, capped per the audio plan.

### Tier 2 — useful infrastructure, lower scientific glamour

**2.1 Night-light for camera-illuminated low-light monitoring.** A dim warm/red glow on the screen acts as an emergency "what's happening at 2 AM" lamp. Low welfare cost (gentle, gradual, far from the perch) once the geometry is confirmed safe. **Strict prerequisites: (a) screen orientation confirmed not to point at the perch, OR (b) intensity below a measured no-startle threshold derived from daytime calibration, AND (c) only triggered by a real event (audio-spike, motion-spike) — never by a clock.**

**2.2 Sunrise simulation for dawn-shift studies.** Gradual blue→white→warm ramp over 30 min before natural sunrise. Real published effect on egg-laying chronotype + foraging onset. Welfare-positive when the gradient is gentle. Requires confirmed screen orientation, daytime calibration, and a measured baseline lux at the perch before / after.

**2.3 Telemetry mirror.** GWTC's screen showing the Guardian dashboard or `farm-2026` site so visitors to the coop can see what the system sees. Nice for visitors / future field-day demos. Engineering only.

**2.4 Low-frequency audio test rig.** GWTC's PowerShell can generate pure tones via `[Console]::Beep(freq, ms)` (limited to 37–32767 Hz, but covers the chicken-relevant 100 Hz – 10 kHz range cleanly). Useful for SPL calibration without burning a real exemplar, and for future psychoacoustic discrimination experiments (do they distinguish a 2 kHz tone from a 4 kHz tone — relevant given chick-distress calls live in 2-5 kHz). Welfare: low at sensible amplitude.

### Tier 3 — speculative, document for the future

**3.1 Real-time audio classification (BirdNET-on-coop-audio).** GWTC's mic feeds BirdNET-Lite or an ONNX YAMNet model running on its CPU; emits structured events ("rooster crow", "chick distress", "ambient") to the Guardian event log. Pairs with the visual stream for proper bioacoustics annotation. Ambitious but a real published paper if it works. Out of v1 scope for both audio + visual plans.

**3.2 Bird-driven generative content.** Use the VLM's frame description + bird count to drive a slow ambient generative-art screen. A "coop says hi" visualization. Pure art-tech, no science. Could be charming for the farm-2026 site.

**3.3 Vocal mimicry feedback.** Detect the rooster's crow (audio classifier) and play it back delayed + pitch-shifted, see if it triggers an audience effect. There is published literature on chickens having "audience effects" on their alarm calling (Marler/Evans 1988); a coop with a synthetic audience is novel.

**3.4 Multi-coop networking.** When the farm grows to multiple coops, every coop runs a GWTC-like node and they coordinate. Out of scope until coop count > 1.

### Explicitly excluded, with reasons

- **Strobe / flicker stimuli.** Welfare risk too high; scientific upside too low.
- **Mirror tests on chickens.** Chickens fail mirror self-recognition; the "other bird" usually triggers aggression. Welfare-negative.
- **Visual predator stimuli used as a deterrent on the farm's own flock.** Conditions habituation against the real threat. Use only as a research stimulus, never as a tool.
- **Continuous-loop alarm playback.** Cumulative stress with no upside.
- **Anything that requires modifying the camera-publishing pipeline on GWTC.** That pipeline is the farm's eyes; a display change should not be able to take it down.

---

## Concrete tonight (16-Apr-2026, post-21:00 ET)

Per the welfare floor, **no flock-side stimulus tonight**. The night-light idea Boss raised is good but is a *daytime engineering target* that gets deployed at night only after daytime calibration. So the deliverables on the brainstorm branch are:

- **This document.**
- **`docs/16-Apr-2026-gwtc-visual-stimuli-plan.md`** — companion plan doc to the audio plan, scoping the visual-arm experiment (Tier 1.1 above) into something pre-registrable.
- **NO code.** No `display.py`, no scheduled-task probe, no anything that touches the GWTC user-session tonight. The session-0 vs interactive-desktop technical question is real but is best probed in daylight when "an unexpected window flashed onto the screen" is within natural variance and Boss can be looking at the laptop while we run the probe.

This is intentionally a small physical-action footprint — the science deliverable is the design space being on paper, not new tech debt.

---

## Open technical questions (assigned to the next agent / Boss)

1. **Where does GWTC's screen physically face?** The webcam looks into the coop run. Most laptop webcams sit above the screen, so the screen *probably* also faces into the run — but "probably" is not "verified." Boss can confirm next coop visit. If the screen faces the perch where birds roost overnight, the night-light idea is permanently downgraded to "needs an angle change first."
2. **Does SSH-launched PowerShell reach the interactive desktop session, or only session 0?** Standard Windows OpenSSH behaviour is session-0 — GUI windows from SSH-launched processes don't appear on the user's screen unless we use a workaround. The candidates are: (a) `schtasks /Create /SC ONCE /ST +0:01 /RU markb /IT /TR ...` — schedule a one-shot task to run interactively (requires the user to be logged in, which they currently are), (b) PsExec `-i 1 -d` if PsExec is installed, (c) drop a `.cmd` into the user's Startup folder + ask them to log out / back in (heaviest, last resort). **Probe in daylight, log results in this doc.**
3. **Brightness control.** Intel UHD 600 + Windows 11 doesn't expose `WmiMonitorBrightness` (verified 2026-04-16: query returned empty). For a screen-as-light-source use case, that means *colour* and *area-fill* are our only real brightness levers (a black background with a small dim-coloured patch is "low brightness"; a full-screen white pixel is always max). Software solutions exist (`Set-DisplayBrightness`, `nircmd setbrightness`) but most rely on the same WMI provider that's missing. If we need real brightness control, the path is probably an external USB-attached light fixture + smart plug, not the laptop screen.
4. **Speaker SPL at perch distance.** The audio plan calls for a phone-app SPL measurement at a typical perching point. Same number serves the visual plan (multimodal trials use audio).
5. **GPU / kiosk-browser CPU footprint.** If a fullscreen kiosk browser running an HTML5 Canvas animation pushes Intel UHD 600 over ~30% utilisation, ffmpeg's dshow capture may stutter and the watchdog may flap. Test in daylight with the Guardian frame rate logged on both sides during a 5-min display loop, before any nighttime use.
6. **Lid open vs closed.** If GWTC is currently in clamshell with the lid closed and an external display is doing the bird-facing work, the entire "screen as stimulus" branch needs that external display's native resolution and orientation.

---

## Cross-references

- `docs/16-Apr-2026-flock-acoustic-response-study-plan.md` — the audio-arm study plan this doc complements.
- `docs/16-Apr-2026-gwtc-visual-stimuli-plan.md` — the visual-arm sibling plan (committed alongside this doc).
- `tools/flock-response/` — the audio scaffold (branch `bubba/flock-response-scaffold-16-Apr`) that the visual primitives will live next to once written.
- `HARDWARE_INVENTORY.md` — GWTC hardware row; the source-of-truth for the camera + machine that hosts the experiments.
- `docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md` — what to do when GWTC drops off the LAN (PIN at the coop keyboard, watchdog auto-recovery).

---

**done. brainstorm. zero flock-side action taken; zero device state changed beyond fetching one camera frame for geometry assessment. The visual plan and the audio plan together set up two pre-registrable studies that can run on overlapping cohorts — that's the value being staged here, not any single tonight-tactic.**
