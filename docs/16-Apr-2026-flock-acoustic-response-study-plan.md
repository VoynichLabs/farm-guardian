# 16-Apr-2026 — Flock Acoustic-Response Study: first-pass scientific plan

Author: Claude Opus 4.7 (1M context) — Bubba
Status: FIRST-PASS PLAN for handoff. Another assistant will flesh this out into an executable protocol. **No code written for this yet** beyond the seed `tools/flock-response/sounds/` directory.

Boss brief: *"Let's do a whole research pass on this. Let's figure out what sounds the whole little flock responds to the most: bugs, other chickens, stuff like that … we got a whole new flock too that's going to be out there in a few weeks, we can run the same tests with them. Make it scientific. Make it publishable."*

---

## Why this is worth being scientific about

Chicken-and-turkey acoustic response is a real ethology subfield with a small literature (alarm calls, food calls, hen-to-chick communication), but: (a) most published work uses purpose-built arenas and audio tracks, not a fixed coop-cam setup, and (b) there are almost no published studies mixing a heritage chicken flock with backyard turkey poults at this life stage. The farm has three things that make a real contribution possible:

1. **Always-on multi-camera capture** already writing JSONL / SQLite via the image pipeline (v2.28 series).
2. **A VLM in the loop** — structured observations per frame (bird count, activity, composition, orientation via `bird_face_visible` as of v2.28.6), not just pixel stats.
3. **Two cohorts in one season:** the current spring flock (~4 winter-survivor adults, Birdadette, ~22 brooder chicks, several adolescent turkeys now in the GWTC coop run) and a **new flock arriving in a few weeks**. Two cohorts in rapid succession is a rare natural replication opportunity.

Publishable targets (in order of ambition): (a) a preprint / report on the farm's public site, (b) a methods note on running low-cost automated ethology with a VLM in the loop, (c) a journal submission (e.g. *Applied Animal Behaviour Science*, *PLOS ONE*, or a citizen-science venue).

## Research questions

### Primary
**Q1.** Do heritage mixed-breed chickens + backyard turkeys in an unrestrained coop run show differential behavioural responses to pre-recorded acoustic stimuli across ecologically-plausible sound categories (conspecific vocalizations, predator calls, putative food-associated sounds, neutral controls)?

### Secondary
**Q2.** Does response magnitude differ between cohorts (spring 2026 flock vs. summer 2026 flock), controlling for time-of-day and weather?

**Q3.** Does response attenuate within a cohort across the trial period (habituation), and does the attenuation slope differ by sound category?

**Q4.** Do individual birds (where identifiable — Birdadette is the only distinctively-marked one currently) show stable response biases across conditions?

### Exploratory
- Does the presence of the turkey poults modify the chickens' response to any category (inter-species vigilance transfer)?
- Correlation between acoustic response metrics and image_archive tier trajectory (does a flock that's "alert" produce more share_worth=strong gems that day)?

## Hypotheses (pre-specified, for pre-registration)

- **H1** (alarm): alarm / predator stimuli (hawk scream, chicken alarm call) will produce the largest attention-toward-camera delta (measured via `bird_face_visible` transitions + motion score) within 3 s of stimulus onset, relative to silent control.
- **H2** (food): food-associated stimuli (conspecific food call, cricket / insect rustle, corn-shake) will produce approach behaviour (reduction in mean bird-to-camera distance proxy via bird_count-in-frame delta) within 15 s.
- **H3** (conspecific neutral): rooster crow and hen cluck will produce orientation (head turn toward speaker) but not net approach or flight, consistent with "someone's there" rather than threat / food.
- **H4** (interspecific): turkey gobble will produce response in the adolescent turkeys but attenuated or null response in the chickens.
- **H5** (habituation): all stimulus categories show response attenuation across the trial period; attenuation slope is shallowest for alarm stimuli (evolutionarily costly to tune out).
- **H0** (null / control): silent trials and ambient-only trials produce no systematic orientation or approach beyond baseline variance.

## Experimental design

### Apparatus
- **Playback device:** GWTC laptop (Gateway, Windows 11, `192.168.0.68`), built-in speakers at the coop. Nominal max SPL ≈ 75 dB at 1 m. Known position — measured distance to nearest perching point becomes a covariate.
- **Observation:** existing multi-camera pipeline. Primary camera is `gwtc` (same laptop, looking into the coop run) — guarantees alignment with the sound source direction. Secondary: `mba-cam` (overhead brooder), `s7-cam` (coop area), `usb-cam` (brooder close-up), `house-yard` (yard / exit).
- **Data pipeline:** experiment runner writes to a new `flock_response_trials` table in `data/guardian.db` plus pre/post frame stacks under `data/flock-response/YYYY-MM-DD/<trial_id>/`. VLM metadata is obtained by running the existing pipeline enricher on each saved frame post-hoc (so we get structured labels without blocking the experiment loop).

### Stimuli

Target 8 categories, 2–3 exemplars per category (to avoid single-recording bias / pseudoreplication). One exemplar in hand; the handoff assistant sources the rest from public-domain libraries (SoundBible, Freesound CC0, Wikimedia Commons, USFWS).

| # | Category | Purpose | Exemplars |
|---|---|---|---|
| 1 | Wild turkey gobble | Conspecific for turkey poults | **turkey-gobble-soundbible-1737.wav** (already in repo) + 2 more needed |
| 2 | Rooster crow | Conspecific, neutral context | needed ×3 |
| 3 | Hen cluck / contentment | Conspecific, low-arousal | needed ×3 |
| 4 | Hen alarm call | Conspecific, high-arousal | needed ×3 |
| 5 | Chick distress peep | Cross-age-class alert | needed ×3 |
| 6 | Hawk scream (red-tail / Cooper's) | Aerial predator — native to the farm | needed ×3, must be real species (red-tail / Cooper's / Sharp-shinned) to match local hawks |
| 7 | Insect rustle (crickets, beetle flapping) | Putative food sound, low arousal | needed ×3 |
| 8 | Silent control | Noise-floor baseline | trivial — no audio, just mark T0 |
| 8b | Ambient-noise control | Habituation / handling control | record farm ambient with no birds present; play back as "signal" |

Normalization: all stimuli peak-normalized to -3 dBFS and duration-clamped to 3–6 s (short enough to not fatigue; long enough for a distinctive vocalization to be identifiable). The handoff assistant should document sample rate, duration, RMS, and peak in a `sounds/MANIFEST.csv`.

### Trial protocol

One trial =
1. **Baseline observation window** (T-30 s to T0): capture frames every 3 s for all cameras. No intervention.
2. **Stimulus onset** at T0: trigger playback on GWTC via ssh + `System.Media.SoundPlayer.PlaySync`; record actual onset timestamp with ms precision (round-trip latency is the observation bias; measure it once, include as a known lag).
3. **Response window** (T0 to T+30 s): capture frames every 3 s. Fixed cadence regardless of camera's normal pipeline rate.
4. **Tail** (T+30 s to T+120 s): capture every 15 s to observe return-to-baseline.

**Inter-trial interval:** minimum 10 minutes between any two trials, to reduce carryover. Minimum 30 minutes between two trials of the same category (stronger carryover control). No trial during a predator event (YOLO detection from Guardian's `/api/detections` within 60 s) — drop and redraw.

### Randomization

- Each daily run: Latin-square-style counterbalancing over the 8 categories, plus 2–3 silent control trials interleaved. Target 12–15 trials per day spread from one hour after sunrise to one hour before sunset.
- Exemplars within a category rotate in a balanced incomplete-block design across days so no category is ever represented by only one exemplar in the final analysis.
- The silent control is timing-matched to stimulus trials (same baseline + response window length, same ITI rules) so "time of day" and "time since last trial" can't confound the null effect.

### Duration

- **Pilot** (current flock): 7–10 days to calibrate response metrics, stimulus volume, and flag sensor gaps.
- **Full pre-registered run** (current flock, after pilot): 14 days.
- **Replication** (new flock, arriving 2026-04-end / 2026-05-early): 14 days, protocol frozen from pilot.
- **Cross-cohort comparison**: analysis only after both cohorts complete. Current flock gets a second 7-day run at the same time as the new flock's week 2, controlling for weather / season.

## Response metrics

Pulled from the pipeline's existing VLM output on trial frames. Schema additions needed in `tools/pipeline/schema.json` (to be scoped by the handoff assistant):

**Additions proposed:**
- `attention_direction`: enum `["toward-camera","away-from-camera","mixed","no-birds"]` — majority vote over visible birds.
- `motion_level`: enum `["still","slow","moderate","rapid"]` — VLM judgment of whether birds are moving relative to the previous frame (for within-trial comparisons, use optical flow in Python instead — more reliable).
- `alarm_posture_count`: integer — number of birds standing tall with necks extended (chicken alarm posture) or ptiloerection (feathers raised).

**Derived metrics (computed in analysis, not VLM-requested):**
- `bird_count_delta` = bird_count(T0+3s) − bird_count(T-3s). Captures approach (increase) or flight (decrease).
- `face_toward_rate` = `bird_face_visible==True` over the response window vs. baseline window. Captures orientation.
- `motion_density` = mean absolute frame-to-frame pixel difference across the response window, normalized to baseline.
- `time_to_first_alarm_posture` = first frame in the response window where `alarm_posture_count > 0`, seconds since T0. NaN if none.
- `response_duration` = time from T0 to first frame where `motion_density` returns to within 1 SD of baseline.

## Analysis plan

Pre-specified to avoid garden-of-forking-paths:

- **Primary test (per question):** linear mixed-effects model with each metric as outcome, stimulus category + trial number (for habituation) + cohort as fixed effects, trial nested within day as random. α = 0.05 after Bonferroni correction across the 5 primary metrics.
- **Secondary:** post-hoc contrasts where the omnibus ANOVA is significant. Effect-size reporting (Cohen's d or η²) alongside p-values — prefer effect size as the primary communication in the writeup.
- **Missing data:** trials aborted for weather or predator events are dropped, not imputed. Report N dropped per cohort.
- **Multiple-comparison:** all exploratory tests labelled exploratory; FDR correction across the exploratory family.
- **Robustness checks:** each primary result repeated with non-parametric equivalent (Mann-Whitney / Wilcoxon) since bird-count deltas are bounded non-normal integers.

## Publishable angle

- **If H1–H4 show the expected pattern with decent effect sizes across both cohorts:** a short report is publishable as a methods-first piece ("Automated acoustic-stimulus ethology with a multi-camera + VLM pipeline in a mixed-species backyard flock") in an applied-animal-behaviour or citizen-science journal.
- **If cohort effects are strong (new flock responds differently):** story becomes "individual / cohort-specific response variation" — more interesting, still publishable.
- **If null across the board:** honest null is worth a blog / preprint; design documented sufficiently that someone else can try with more statistical power.

Pre-register the protocol on OSF.io before the full run begins (after the pilot). Cost: $0, gains credibility enormously.

## Ethics / welfare

- Stimuli are recorded natural sounds the birds encounter in the wild or would, at peak SPL comparable to a person talking nearby. Not louder.
- No food deprivation, no capture, no physical contact. Minimally invasive.
- Trials end immediately (no retry that day) if a bird shows sustained alarm behaviour (flight, alarm calls persisting > 60 s) to avoid repeated stress.
- Predator-sound trials (hawk scream) are the highest-arousal category — cap at 1 per day per cohort to avoid cumulative stress.
- Document any observed negative welfare events in a dedicated incidents log; report in the writeup regardless of statistical result.

No IACUC equivalent applies to a privately-owned backyard flock in Connecticut, but the protocol should still pass a reasonable "would you be comfortable if this were on a university campus" test. I think this one does.

## Threats to validity & mitigations

| Threat | Mitigation |
|---|---|
| Speaker-direction cueing (birds learn the GWTC speaker, not the sound) | Include silent trials and ambient-noise trials from the same speaker; significant effects must exceed baseline-from-same-speaker variance. |
| Time-of-day confound | Randomize stimulus category within day; analyze with time-of-day as a covariate. |
| Weather confound | Record wind speed + precipitation (via a nearby weather API — handoff open item) as covariates; drop trials during heavy rain. |
| Observer effect (human present) | The coop is generally unattended during trials. Boss presence is rare but should be logged (Boss can mark on his phone or via a Discord reaction). |
| Single-speaker bias | Current design *is* single-speaker. Discuss in limitations. A future phase could add an MBA-cam-mounted speaker for a 2-source design. |
| Habituation contaminating primary effect | Trial order randomized; habituation itself is H5, modeled in the mixed-effects analysis, not treated as noise. |
| Pseudoreplication from a single exemplar per category | 2–3 exemplars per category, nested in the analysis. |
| VLM hallucination / measurement error | Manual spot-check of ≥ 5 % of frames per category per cohort (blinded labeler — doesn't know stimulus category). Report kappa / IRR. |

## Practical implementation sketch (for the handoff assistant)

```
tools/flock-response/
├── README.md                  — points at this plan
├── sounds/
│   ├── MANIFEST.csv            — filename, category, source, license, peak_dBFS, duration_s, rms
│   ├── 01-turkey-gobble/       — 3 exemplars
│   ├── 02-rooster-crow/        — 3 exemplars
│   ├── …                       — 8 categories
│   ├── 08b-ambient/            — 3 exemplars
│   └── 00-silent-control.wav   — 5 s silence
├── experiment.py               — CLI: --once / --daemon; picks next stimulus from a counterbalancing schedule; plays via ssh; captures frame bursts; logs to DB
├── analyze.py                  — pulls trials from DB, runs the pre-specified tests, writes a report
└── deploy/
    ├── com.farmguardian.flock-response.plist   — LaunchAgent for --daemon
    └── push-sounds-to-gwtc.sh                  — one-off scp + mkdir on GWTC

data/flock-response/
└── YYYY-MM-DD/<trial_id>/
    ├── meta.json                — category, exemplar, T0_ms, ITI_since_last_trial, weather snapshot
    ├── baseline_-30s.jpg, baseline_-27s.jpg … baseline_-3s.jpg
    ├── response_+0s.jpg, response_+3s.jpg, response_+6s.jpg … response_+30s.jpg
    └── tail_+45s.jpg, tail_+60s.jpg, tail_+75s.jpg … tail_+120s.jpg
```

New DB table (sketch):

```sql
CREATE TABLE flock_response_trials (
  id INTEGER PRIMARY KEY,
  trial_id TEXT UNIQUE NOT NULL,       -- YYYY-MM-DDTHH-MM-SSZ_<category>_<exemplar>
  cohort TEXT NOT NULL,                -- 'spring-2026' | 'summer-2026' | ...
  day_index INTEGER NOT NULL,          -- 1..N within cohort run
  category TEXT NOT NULL,              -- 'turkey_gobble' | 'rooster_crow' | ...
  exemplar TEXT NOT NULL,              -- sounds/01-turkey-gobble/<filename>
  silent_control INTEGER NOT NULL,     -- 0 / 1
  t0_utc TEXT NOT NULL,
  playback_latency_ms INTEGER,
  weather_json TEXT,
  iti_minutes_since_prior REAL,
  -- derived metrics (computed post-hoc)
  bird_count_baseline_mean REAL,
  bird_count_response_mean REAL,
  face_toward_rate_baseline REAL,
  face_toward_rate_response REAL,
  motion_density_delta REAL,
  time_to_first_alarm_posture_s REAL,
  notes TEXT
);
```

## Open items for the next assistant

1. **Source the remaining stimuli** — 2 more turkey, 3 rooster, 3 hen-cluck, 3 hen-alarm, 3 chick-distress, 3 hawk-scream, 3 insect-rustle, 3 ambient. Document each in `sounds/MANIFEST.csv` with license. Prefer CC0 / public domain so the data + the audio are redistributable.
2. **Measure playback round-trip latency** (Mini → ssh → GWTC → speaker) once and write it into the plan + the analysis as a known offset.
3. **Calibrate SPL** at a typical perching point. A phone dB-meter app is fine; record the reading in the MANIFEST next to each exemplar after normalization.
4. **Implement `experiment.py`** — single daemon, owns the schedule. Coordinates with the capture pipeline to avoid running a trial during an image_archive capture cycle for the same camera (would skew frames). Simplest is to pause the pipeline around trials — or just accept the parallel capture and oversample.
5. **Schema additions** (`attention_direction`, `motion_level`, `alarm_posture_count`) — scope the impact on farm-2026 (it won't break; new fields are additive).
6. **Pre-register on OSF** after the pilot.
7. **Weather integration** — add a small pull from OpenWeatherMap or equivalent to attach to each trial's `meta.json`.
8. **Build the analyze.py** — can be Jupyter + pandas + statsmodels; keep it runnable end-to-end from the trials DB.
9. **Writeup template** — skeleton of the methods + results sections in `docs/flock-response-writeup.md` to be filled in after data collection.

## Appendix A — recommended first sound-library sources

- **SoundBible** (public domain and CC tags; the turkey gobble already in the repo is PD) — https://soundbible.com
- **Freesound** (CC0 filter available) — https://freesound.org
- **Wikimedia Commons** (audio files section) — https://commons.wikimedia.org/wiki/Category:Audio_files
- **Macaulay Library** (Cornell — usage terms per recording; many are not redistributable)
- **USFWS sound library** (federal, public domain)

Always record the source URL, license, and contributor in `MANIFEST.csv`. If the study is submitted for publication, the reviewers will want this.

## Appendix B — what's already built

- `tools/flock-response/sounds/01-turkey-gobble/turkey-gobble-soundbible-1737.wav` — 239 KB, 44.1 kHz stereo, public domain, the first seed stimulus. (from http://soundbible.com/grab.php?id=1737&type=wav). Moved from `sounds/` into the per-category subdir during the 16-Apr scaffold tranche to match the layout in the practical-implementation sketch above.
- Verified: `ssh markb@192.168.0.68 'powershell -Command "(New-Object System.Media.SoundPlayer C:\Windows\Media\tada.wav).PlaySync()"'` → audio plays on GWTC, PowerShell returns READY. Playback path end-to-end works; just needs the study code on top.
- v2.28.6 pipeline schema already emits `bird_face_visible`, which will be a core response metric.

---

**done. handoff. No code written beyond the seed sound file in the sounds/ directory.**
