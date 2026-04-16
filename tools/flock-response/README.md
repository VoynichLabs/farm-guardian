# tools/flock-response/

Scaffold for the flock acoustic-response study. The full design lives in
**`../../docs/16-Apr-2026-flock-acoustic-response-study-plan.md`** — read
that first; this README only documents what's *built* in this directory.

## What's here

```
tools/flock-response/
├── README.md                       — you are here
├── playback.py                     — minimal SSH→GWTC→PowerShell playback primitive
├── measure_latency.py              — runs playback.py N times, reports median/p95
├── deploy/
│   └── push-sounds-to-gwtc.sh      — scp the sounds/ tree to C:/farm-sounds on GWTC
└── sounds/
    ├── MANIFEST.csv                — one row per WAV, source/license/normalization
    ├── 01-turkey-gobble/           — seed exemplar already here
    ├── 02-rooster-crow/            — empty, awaiting curation
    ├── 03-hen-cluck/               — empty, awaiting curation
    ├── 04-hen-alarm/               — empty, awaiting curation
    ├── 05-chick-distress/          — empty, awaiting curation
    ├── 06-hawk-scream/             — empty, awaiting curation
    ├── 07-insect-rustle/           — empty, awaiting curation
    ├── 08-silent-control/          — control trial; no audio required
    └── 08b-ambient/                — record on-farm, do not source
```

Still to come (per the plan's "Open items for the next assistant"):
`experiment.py` (the daemon owning the schedule), `analyze.py` (mixed-effects
+ effect-size report), schema additions, the `flock_response_trials` DB
table, the LaunchAgent plist, weather integration, and OSF pre-registration.

## Critical: do not pre-play real stimuli

Every playback of a real exemplar from `sounds/` to a live cohort before the
pilot officially begins contaminates the H5 habituation measurement on that
cohort. **Use `C:\Windows\Media\tada.wav` for any development or
calibration playback** — it's GWTC-bundled, it's not in the stimulus set,
and the birds get to encounter the real stimuli on day 1 of the pilot
genuinely naive.

`playback.py` defaults to `tada.wav`. Anyone reaching for a real WAV path
should be doing it from inside the eventual `experiment.py`, not the CLI.

## Smoke test the playback path

```bash
# from the repo root, with the venv active or the system python
python tools/flock-response/playback.py
# expected: JSON with "ok": true, wall_clock_s ~2-3s, returncode 0
```

The default plays GWTC's `tada.wav` once. If GWTC is at the lock screen
(pre-PIN-entry) it will not be reachable — see
`docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md` for the
30-second recovery recipe (PIN `5196` at the coop keyboard).

## Calibrate the round-trip latency

```bash
python tools/flock-response/measure_latency.py --n 10
```

Reports median, p95, min, max wall-clock seconds across N=10 plays of
`tada.wav`. The median number gets written into the experiment runner's
per-trial `meta.json:playback_latency_ms` so the analysis can subtract it
from T0 timing. Re-run after any change to GWTC's WiFi link, the SSH path,
or the playback toolchain.

Note: wall-clock includes the audio duration of the clip itself
(`tada.wav` is ~1.5 s). To estimate pure overhead, subtract the clip
duration. For the experiment metadata, the wall-clock-as-measured is the
right thing to record — it's what the analysis pipeline expects.

## Push the sounds library to GWTC

After sourcing new exemplars and updating `MANIFEST.csv`:

```bash
./tools/flock-response/deploy/push-sounds-to-gwtc.sh --dry-run
./tools/flock-response/deploy/push-sounds-to-gwtc.sh
```

Drops the entire `sounds/` tree at `C:/farm-sounds/` on GWTC. Idempotent.
After this runs, `playback.py --remote-path 'C:/farm-sounds/01-turkey-gobble/turkey-gobble-soundbible-1737.wav'`
addresses the seed file directly. (Don't actually run that until the pilot
starts. See above.)

## Cross-references

- `docs/16-Apr-2026-flock-acoustic-response-study-plan.md` — the design.
- `HARDWARE_INVENTORY.md` — GWTC row (`gwtc` camera; same laptop hosts the
  speaker for this study).
- `docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md` — what to do
  when GWTC stops responding (almost always pre-login WiFi).
- `~/bubba-workspace/memory/reference/network.md` — the LAN inventory.
