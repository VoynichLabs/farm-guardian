# 02-Oct-2026 — s7-cam local focus check (plan + result)

**Author:** Claude Opus 5.5. **Status:** implemented and live as v2.75.0.

## The ask

Boss, 02-Oct-2026, with a frame attached: *"The VLM pipeline is spamming me with photos that
are often blurry and out of focus. Can you fix this? That's poor quality. That's not 92.
That's like 50."* The frame (`s7-cam`, 17:16:42Z, row 3024011) is a rooster's head defocused
to a red smear across the top-left with a sharp hen behind it. It scored 92 and posted.

## What was actually wrong

Not the score weights and not the posting floor. The VLM is answering by rote:

- That frame came back `image_quality: sharp`, `bird_face_visible: true`, `detail_score: 22`,
  `share_reason: "Close rooster, sharp eye, facing camera"`.
- That exact `share_reason` string was written on **522 of 1,958** s7 frames on 02-Oct
  (367 of 2,101 on 30-Sep, 311 of 1,929 on 01-Oct). It is the example sentence in `prompt.md`.
- Every focus-related input to the score and to `should_post` (`image_quality`,
  `bird_face_visible`, `detail_score`) is read from that same answer. Re-weighting or raising
  the floor only moves the volume of a signal that carries no focus information. The floor has
  already been moved 7 -> 80 -> 70 -> 65 -> 80 -> 82.

The physical cause: a bird closer than the phone's minimum focus distance, or moving. The
camera focuses on the scene behind it.

## Scope

**In:** a local, pixel-based focus verdict for `s7-cam` that overrules the VLM's
`image_quality` when it finds a large out-of-focus patch, caps the score, and so blocks the
Discord post and the reel lanes that require `image_quality='sharp'`.

**Out:** prompt/schema changes, score-weight changes, the posting floor, burst-frame selection,
other cameras (all `vlm_bypass`), retro-fixing rows already in the archive.

## What was measured before building

1. **Laplacian variance inside the YOLO box** (the existing `subject_laplacian` idea): rejected
   frames 134-3131, crisp frames 156-1789. No separation. Boxes merge, background shows through,
   and it measures contrast as much as sharpness. Dead end, for the third time.
2. **Contrast-normalised edge sharpness per tile** (`p99 |gradient| / (p95-p5 luminance)`):
   defocused birds light up cleanly as blobs of soft tiles; crisp frames are clean.
3. Counting the largest soft blob **without** handling the feed bowl: no separation, because the
   permanently blurry bowl merges with everything near it.
4. With a slow per-tile running average that learns and ignores the bowl: bimodal. Of 266
   post-eligible frames on 02-Oct, 153 scored under 12 tiles and 98 scored 20 or more.
   Eight frames Boss would reject scored 20-127; ten crisp frames scored 0-12 with one
   exception (56) which at full size is a hen visibly soft from being too close.
5. Hourly median across 01-Oct dawn to dusk: 1-5 tiles. Not a lighting artefact.

Replayed in order through the shipped class, two days of post-eligible frames split
**264 sharp / 61 soft / 82 blurred** (6 abstained during warm-up): roughly a third of what was
eligible to post was out of focus.

## Architecture

- `tools/pipeline/focus_check.py` (new) — `tile_states`, `largest_soft_region`, `FocusJudge`
  (holds the static map, persists it to `data/cache/focus-static-<camera>.npz`), `judge_for`.
  Fails open: abstains on any error, during a 30-frame warm-up, and after a frame-shape change.
- `tools/pipeline/orchestrator.py` — `run_cycle` calls the judge before the VLM;
  `_apply_focus_verdict` overrules `image_quality` before `_compute_overall_score` (so the
  technical axis follows), `_cap_score_for_focus` then caps at 60 (soft) / 45 (blurred), moves
  `strong` to `decent`, and rewrites `share_reason`. One-directional: it never promotes.
- `tools/pipeline/config.json` — `s7-cam.focus_check {enabled, soft_area_pct 3.0,
  blurred_area_pct 8.0}`. Opt-in per camera.
- No schema or DB change. The verdict is stored inside `vlm_json` as `focus_check`
  (`verdict`, `soft_area_pct`, and `vlm_image_quality` when it overruled).
- `should_post` is untouched: s7 already requires `image_quality == "sharp"`.

## Verification

- `tools/pipeline/test_focus_check.py` — 9 synthetic tests (crisp passes, defocused bird
  caught, bowl learned, patch touching the bowl still caught, flat white hen not blur, warm-up
  abstains, restart keeps the map, overrule + cap, never promotes).
- Existing `test_floor_pecking_calibration.py` and `test_gem_poster_gate.py` still pass.
- Pipeline restarted 17:34Z with the static map pre-seeded from the day's frames; rows from
  17:34:59Z onward carry `focus_check` in `vlm_json`.

- Boss's frame (row 3024011) replayed through the live seeded static map and the real helpers:
  `sharp` / 92 / would post -> `blurred` (11.8% of frame) / 45 / `decent` / would not post.
- Counterfactual on the 49 frames that actually posted to Discord on 02-Oct before the fix and
  are in the replay: 29 sharp, 8 soft, 12 blurred. 20 of 49 would not have posted.
- At the time of writing no live frame had been overruled yet (the birds went quiet after the
  restart). Check with:
  `SELECT count(*) FROM image_archive WHERE json_extract(vlm_json,'$.focus_check.vlm_image_quality') IS NOT NULL;`

## Side effects (intended)

- An overruled frame is stored in the `decent` tier: downscaled, decent retention, not
  full-size for 90 days.
- It also leaves the 21:00 S7 reel's pool, which selects on `image_quality='sharp'`.

## Known limits

- It catches a **large** out-of-focus region. A frame that is uniformly slightly soft, or a
  small distant bird that is soft, is not what it measures.
- A bird that parks at the lens for ~25 minutes of hot-cadence frames starts being learned as
  furniture. It unlearns at the same rate.
- Thresholds were set on two days of one camera aim. If the aim or the phone changes, re-run
  the distribution before trusting 3% / 8%.
- The rote `share_reason` and `bird_face_visible` are still rote. This fixes focus, not the
  model's stereotyped answers in general.
