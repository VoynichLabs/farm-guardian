# tools/flock-response/

Seed directory for the flock acoustic-response study. The design and scope live in **`../../docs/16-Apr-2026-flock-acoustic-response-study-plan.md`** — read that first.

Current state: plan only. The experiment runner, analyzer, and sound library are still to be built by the next assistant. Only artifact in this directory right now:

- `sounds/turkey-gobble-soundbible-1737.wav` — 239 KB, 44.1 kHz stereo, public domain. Sourced from SoundBible (id 1737). First seed stimulus for the turkey-gobble category.

Verified working end-to-end: playback via `ssh markb@192.168.0.68 'powershell -Command "(New-Object System.Media.SoundPlayer C:\Windows\Media\tada.wav).PlaySync()"'` plays on the GWTC laptop's speakers and returns control cleanly. The plan doc's Appendix B has the full verification.

For the sound-library conventions (naming, MANIFEST.csv, normalization), the directory layout, and the DB schema sketch, see the plan doc.
