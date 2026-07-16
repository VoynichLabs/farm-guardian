# 16-Jul-2026 — S7 camera moved to Birdcatraz (relocation record)

## What happened

The whole flock now lives in **Birdcatraz**: the outdoor enclosed poultry area — a fenced compound containing the chicken coop and the turkey pen. The Samsung Galaxy S7 camera (`s7-cam`) moved there with them, mounted facing the **big water bowl**. The brooder era is over; no camera shows a brooder anymore.

Camera → location map after the move:

| Camera | Location |
|---|---|
| s7-cam | Birdcatraz, aimed at the big water bowl |
| usb-cam | Coop run (inside Birdcatraz) — currently disconnected, Boss handling |
| mba-cam | Turkey pen (inside Birdcatraz) — moved 2026-06-21 |
| house-yard | Yard PTZ (outside the enclosure) |
| duo2 | Fixed pano, zone TBD |
| dominator-cam | Opportunistic desk cam |

## What changed in code (v2.46.0)

- `tools/pipeline/config.json` — s7-cam `context` rewritten for Birdcatraz/water bowl (grown birds, drinking scenes are the expected subject); usb-cam and mba-cam contexts note they're inside Birdcatraz; brooder-cohort language removed.
- `tools/pipeline/prompt.md` — skip trigger 9 no longer condemns water-bowl shots wholesale: it now targets *distant scatter with no close lead subject*, and explicitly states a close bird at the bowl passes the close-and-looking rule. New positive calibration example ("water-bowl portrait" → strong). Scene guidance documents the new `birdcatraz` label. Named-individual section: **Birdadette renamed Birddor** (July 2026, turned out to be a cockerel); prompt now says never to use the old name.
- `tools/pipeline/schema.json` — `scene` enum gains `"birdcatraz"` (the outdoor enclosure).
- `tools/pipeline/gem_poster.py` — Discord usernames: `S7 Brooder`→`S7 Birdcatraz`, `Brooder Overhead`→`Turkey Pen`, `Brooder Floor`→`Coop Run`.
- `tools/pipeline/ig_poster.py` — IG photo commits no longer hardcode `public/photos/brooder/`: new `_SCENE_SUBDIR_MAP` + `_subdir_for_gem()` route by VLM scene (default `birdcatraz/`). Existing `brooder/` files are pinned by past IG media URLs and are untouched. `_SCENE_BUCKET_MAP` now maps birdcatraz/coop/nesting-box/yard scenes to adult-bird hashtag buckets (`chickens`/`coop`/`homestead`/`yard_diary`); #chicks tags only remain on the historical `brooder` scene.
- `tools/pipeline/daily_reel_runner.py` — mba-cam timelapse lane relabeled brooder→turkey pen ("A day in the turkey pen.").

## Not changed (deliberate)

- `test_floor_pecking_calibration.py` — its BAD case is distant scatter (largest_subject_pct=18), still a correct skip under the revised rule; the demotion helper is scoped to usb-cam/gwtc and never touched s7.
- s7-backlog reel lane caption ("A look back at the nesting box.") — that lane replays historical footage, so the label is accurate.
- Gem scoring math (v2.45.x) — untouched.
- The `farm-2026/public/photos/brooder/` archive — frozen, never modify.

## Operational note

The orchestrator daemon reads `config.json`/`prompt.md`/`schema.json` — a restart of `com.farmguardian.pipeline` is required for the VLM changes to take effect. **Not restarted as part of this change** (Boss to schedule; restart is `launchctl kickstart -k gui/501/com.farmguardian.pipeline`).

Follow-up work (Parts B, D, E) is planned in `farm-2026/docs/16-Jul-2026-birdcatraz-era-refresh-plan.md`.

Author: Claude Fable 5, 16-Jul-2026.
