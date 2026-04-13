# 12-Apr-2026 — House-Yard Night Detection Window

**Author:** OpenAI Codex GPT-5.4
**Status:** Implemented
**Version target:** v2.15.1

## Scope

Add a small, config-driven time gate so YOLO detection only runs for enabled cameras during the approved night window: 20:00 → 09:00 in America/New_York.

## Objectives

- Keep the snapshot/dashboard path live all day.
- Keep the change inside the existing `guardian.py` detection flow.
- Re-enable `house-yard` in live config so it participates in the night window.
- Document the behavior in `CHANGELOG.md`.

## Implementation Notes

- The gate is checked in `GuardianService._on_frame()` before `AnimalDetector.detect()` runs.
- The window is configured in `config.json` / `config.example.json` under `detection.night_window_*`.
- The comparison treats `20:00 → 09:00` as an overnight window that crosses midnight.
- The default timezone is `America/New_York` so the gate matches the boss-approved local schedule.

## TODO

- [x] Inspect the existing detection flow and live config.
- [x] Add the detection window gate in code.
- [x] Update live config so `house-yard` is enabled.
- [x] Update the example config.
- [x] Update the top `CHANGELOG.md` entry.
- [x] Validate the code path locally.
- [x] Restart Guardian and confirm the updated service is running live.
