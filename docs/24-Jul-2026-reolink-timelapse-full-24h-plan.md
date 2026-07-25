# 24-Jul-2026 — Reolink time-lapse reels: full 24h, no mid-reel cut

**Author:** Claude Opus 5
**Status:** awaiting approval
**Boss directive (24-Jul-2026):** "I want all of the footage for all the cameras, the duo2 and the house yard. I want them to just be a normal time-lapse." Plus: the hard cut in the middle of the duo2 reel is unacceptable.

---

## Problem

The `duo2` time-lapse reel is filtered to local hours 06:00–20:00 by
`instagram.scheduled.timelapse_reel_daylight_only_cameras` in
`tools/pipeline/config.json` (currently `["gwtc", "usb-cam", "duo2"]`).

Two consequences, one root cause:

1. **All night footage is discarded.** Verified against the live DB on
   2026-07-24: the duo2 selector returned 90 frames, **0** of them from
   21:00–04:00. The lane logged `daylight filter kept 4990/8534 raw frames`.
2. **The reel has a hard cut in the middle.** The lane runs at 15:00 with a
   rolling 24h window (15:00 yesterday → 15:00 today). The daylight filter
   removes 20:00→06:00 from the *middle* of that otherwise-contiguous span, so
   the reel plays yesterday afternoon → dusk, then jumps discontinuously to this
   morning's dawn → this afternoon.

The cut is a direct artifact of the filter, not a separate bug. Removing the
filter fixes both at once.

`house-yard` is **not** on the daylight list and already behaves correctly:
full 24h, continuous. Verified — its 90 selected frames spread evenly across all
24 local hours (30 of 90 from the fully-dark 21:00–04:00 band). **No change
needed for house-yard.**

## Scope

**In:**
- Remove `"duo2"` from `timelapse_reel_daylight_only_cameras` in
  `tools/pipeline/config.json`.
- CHANGELOG entry.
- Update `docs/SOCIAL_MEDIA_MAP.md` where it describes the two yard reel lanes,
  so the next agent knows these are deliberately all-hours.

**Out:**
- `house-yard` — already correct, not touched.
- Reel start-hour / calendar-day alignment. Boss chose "from run time"
  (24-Jul-2026): house-yard covers 09:00→09:00, duo2 covers 15:00→15:00. Both
  continuous. Posting times do not move. The midnight→midnight alternatives were
  rejected because raw frames are pruned at 24h (`raw_retention_hours: 24`), so a
  calendar-day reel would require either posting at ~00:10 (worst IG slot) or
  bumping raw retention to ~40h (≈ +35–40GB on a volume with 141GB free).
- The `gwtc` and `usb-cam` entries in the daylight list, and the
  `timelapse_golden_windows` block (`usb-cam`, `dominator-cam`). **All of those
  lanes are disabled** — their plists carry `.disabled` suffixes and
  `launchctl list` confirms only `house-yard`, `duo2`, `s7-daily`, `s7-backlog`
  and the mixed `ig-daily-reel` are loaded. Nothing is being produced from them,
  so there is nothing to fix today. See "Future" below.
- The 18:00 mixed `ig-daily-reel` — it gates on `discord_reactions >= 1`, and
  `vlm_bypass` raw frames are never individually posted to Discord, so it cannot
  pull current Reolink footage. Unaffected.
- The s7 lanes — different selectors (`select_s7_daily_reel_gems`), gated on
  `share_worth`/`image_quality`, not raw time-lapse selection. Unaffected.

## Architecture

No new code, no new module. The behavior is already fully config-driven:
`tools/pipeline/ig_selection.py::_timelapse_daylight_only_enabled` (line 183)
reads the camera list and `select_timelapse_gems` (line 736) applies the filter
only when the camera is a member. Dropping `duo2` from the list takes the
`elif daylight_only:` branch out of play for that camera and the selector falls
through to its default all-hours behavior — the same path `house-yard` already
takes.

This is deliberately a config change, not a code change. Adding a per-lane
boolean or a new selector would duplicate a decision the existing list already
expresses (DRY).

**Frame budget is unaffected.** duo2 captures every 10s round the clock (~8,500
frames/24h). Bucketing at 5 minutes yields 288 buckets over a full day, already
well above `timelapse_reel_max_frames: 90`, so the selector's even-subsample step
still returns exactly 90 frames and reel duration stays ~22.6s. The change alters
*which* 90, not how many.

**Quality gating is unaffected.** Night frames already clear the capture-time
gate (`exposure_p50_floor: 25`; duo2's night median is 66), and per-bucket
selection means a night frame only ever competes against other frames from the
same 5 minutes. Nothing needs relaxing.

## TODOs

1. Edit `tools/pipeline/config.json`:
   `timelapse_reel_daylight_only_cameras` → `["gwtc", "usb-cam"]`.
2. Reload the pipeline LaunchAgent:
   `launchctl kickstart -k gui/$(id -u)/com.farmguardian.pipeline`.
   (Guardian's root `config.json` is untouched — this key lives only in the
   pipeline config — so `com.farmguardian.guardian` does **not** need a reload.)
3. **Verify (read-only, before the next scheduled run):** re-run
   `select_duo2_timelapse_gems` against the live DB. The **discriminating**
   evidence is the selector's own logging:
   - the `daylight filter kept N/M raw frames for duo2` line must be **absent**
     (previously: `kept 4990/8534`), and
   - `picked 90/<M>` must report the **full** unfiltered denominator (~8,500),
     not ~4,990.

   Note what does *not* prove anything: because `raw_retention_hours: 24` prunes
   older frames, a same-day run always sees a populated 24-hour span, so
   "every hour has frames" would look like a pass either way. Treat the hour
   histogram as a sanity check only — the log lines above are the actual proof.
4. Run `tools/pipeline/test_ig_selection_timelapse.py` — it covers the gwtc
   daylight-only case and the generic all-hours case. Neither references duo2, so
   both should still pass; this confirms the shared selector wasn't disturbed.
5. Confirm the same two log conditions again at the next live 15:00 fire, against
   a real scheduled run rather than a manual invocation.
6. CHANGELOG entry — **v2.52.2**, patch. Matches how this repo versions behavior
   fixes (v2.51.11 diary filenames, v2.51.12 gwtc black-frame recovery); minor
   bumps here are reserved for new capabilities.
7. Update `docs/SOCIAL_MEDIA_MAP.md` lines describing the house-yard and duo2
   reel lanes to state that both are deliberately all-hours, including IR night
   footage, and that this is a Boss decision — so the next agent doesn't
   "helpfully" re-add a daylight filter.

## Docs / changelog touchpoints

- `CHANGELOG.md` — new top entry.
- `docs/SOCIAL_MEDIA_MAP.md` — lane descriptions for the two yard reels.
- This plan doc.
- `docs/14-Jun-2026-usb-dominator-golden-window-reels-plan.md` — left as-is;
  it is a historical plan for lanes that are now disabled, and its golden-window
  design is untouched by this change.

## Risk

Low, and reversible by re-adding one string. The realistic downside is aesthetic:
roughly a third of the duo2 reel becomes low-contrast IR night footage, exactly
as it already is on house-yard. Boss has seen house-yard's output and is asking
for duo2 to match it.

## Future (not now)

If `usb-cam` or `dominator-cam` are ever re-enabled, they will exhibit a *worse*
version of the same cut: `timelapse_golden_windows` keeps only
sunrise→09:00 and 19:30→20:30, discarding the entire middle of the day. That
would need the same treatment before those lanes go live. Flagging it here rather
than changing dormant config today.

**Landmine for whoever does that work:** `usb-cam` is listed in **both**
`timelapse_reel_daylight_only_cameras` *and* `timelapse_golden_windows.cameras`.
`select_timelapse_gems` tests `use_golden` first, so the golden block wins and the
daylight-list entry for `usb-cam` is currently dead config. Anyone who removes the
golden-window block to "unfilter" that lane would silently re-activate a
06:00–20:00 daylight filter they didn't know was there. Both entries must go
together. Left as-is today because the lane is disabled.

Separately, `duo2`'s old filter was fixed clock hours (06:00–20:00), not solar —
so it was only ever truly "daylight" near the equinoxes. Moot once removed, but
worth knowing if any daylight gating is reintroduced anywhere: the codebase
already has correct dynamic sunrise/sunset math in
`tools/pipeline/golden_windows.py`, and any future window should use it rather
than hardcoded hours.
