# 17-Apr-2026 — Yard-diary daily capture

Author: Claude Opus 4.7 (1M context)

## The problem

Boss wants a daily photo of the yard to record the cherry tree blooming (right now, mid-April) and the seasonal progression through the year. The existing gems pipeline doesn't solve this — by design it only promotes frames that score `share_worth='strong'` in the VLM pass, and the house-yard camera is currently producing zero strong gems. A tree-in-bloom scene isn't what the curator's tuned for, and relying on stochastic curation for a seasonal-record story has the wrong failure mode: miss a day, miss the bloom.

Written post-hoc — Boss was clear the work needed to happen today, not tomorrow after a plan review. This doc records what was built so a future agent can find it.

## Scope

**In:**
- One-shot-per-day capture from the Reolink (`house-yard`) at 12:00 local via launchd.
- 4K master stored on the Mini; 1920px published copy committed to farm-2026 so Railway serves it from its own CDN.
- Automatic git commit + push on each capture so the site picks up the new frame without manual action.
- Idempotent — re-running on the same day overwrites (safe for manual re-triggers).
- Site surface: `app/yard/page.tsx` in farm-2026 renders today as hero + prior days in a grid.

**Out:**
- PTZ re-aim. Whatever the Reolink is pointed at today is what today's diary captures. If the cherry tree isn't in frame, that's a physical-aim concern for Boss to handle at the coop, not a code concern. Per `docs/08-Apr-2026-absolute-ptz-investigation.md`, absolute PTZ doesn't work reliably; don't script aim changes.
- VLM enrichment. The diary frames are not run through `glm-4.6v-flash`; they're raw snapshots. If we want captions later, add them manually or via a separate pass — keeping the daily capture dead-simple is the point.
- Retention policy. JPEGs accumulate indefinitely on both the Mini (4K masters, ~1.3 MB/day) and in farm-2026's `public/` (1920px, ~500 KB–1 MB/day). At 365 frames/year this is ≤500 MB/year in the repo; acceptable for now. Revisit at year-2 if repo size becomes an issue.

## Architecture

```
12:00 daily (launchd)
      │
      ▼
scripts/yard-diary-capture.sh
      │
      ├─► curl localhost:6530/api/v1/cameras/house-yard/snapshot
      │         (reuses existing Guardian Reolink snapshot endpoint)
      │
      ├─► data/yard-diary/{YYYY-MM-DD}.jpg   (4K master, ~1.3 MB)
      │
      ├─► sips -Z 1920
      │
      ├─► ../farm-2026/public/photos/yard-diary/{YYYY-MM-DD}.jpg (~500 KB)
      │
      └─► git add + commit + push (farm-2026)
              │
              ▼
         Railway redeploys → site shows new frame
```

Key invariants:

1. **Reuses the existing snapshot endpoint** — no new Guardian code. If that endpoint breaks, the gems pipeline and the dashboard also break, so we'll know.
2. **Publish path is farm-2026's `public/`, not a Guardian API endpoint.** This is the opposite of how gems work. Gems go through the tunnel; the yard diary goes through Railway's CDN. Rationale: the diary should stay visible during Mini outages or tunnel drops, and a redeploy-per-day cost is negligible.
3. **Master and published are separate.** 4K master stays on the Mini for future re-use (zoomable detail view, print, regenerate a different size). Published copy is 1920px because that's plenty for the site and halves bandwidth.

## Files

| Path | Purpose |
|------|---------|
| `scripts/yard-diary-capture.sh` | The capture + publish script. |
| `~/Library/LaunchAgents/com.voynichlabs.yard-diary-capture.plist` | Daily noon schedule. Not in git (lives under `~/Library/`). |
| `data/yard-diary/{YYYY-MM-DD}.jpg` | 4K masters. Not in git (gitignored via `data/`). |
| `data/pipeline-logs/yard-diary.log` | Per-run log. |
| `../farm-2026/public/photos/yard-diary/{YYYY-MM-DD}.jpg` | 1920px published copies. In git (farm-2026 repo). |
| `../farm-2026/app/yard/page.tsx` | The `/yard` route. |
| `docs/17-Apr-2026-yard-diary-capture-plan.md` | This doc. |

## Why 12:00 local

- Sun is usually close to overhead; tree-line lighting is neutral.
- No heavy back-light from low-angle morning or evening sun behind the tree line.
- Noon is a memorable, predictable time — no confusion about when the daily shot happens.
- If it's raining or overcast, the diary still captures the day; we want the weather record too.

If it turns out noon is wrong (shadow angle bad for the bloom, or Boss wants morning light), change the `Hour` key in the plist and `launchctl bootstrap` it again. One-line change.

## Failure modes

| Mode | Behavior |
|------|----------|
| Guardian snapshot endpoint down | Script exits with log `ERROR: snapshot fetch failed`; no git commit; next day retries. No partial/empty frame pushed. |
| Snapshot tiny (camera off, network hiccup returns error page) | Size check rejects < 50 KB, deletes the file, logs and exits. |
| `sips` resize fails | Logs and exits. Master is kept, can be re-published manually later. |
| `git push` fails (no network) | Commit is made locally; next run's push will include it. `pipeline-logs/yard-diary.log` shows WARN. |
| Reolink pointed at sky / wrong angle | Captured anyway. This is a physical-aim issue, not a code issue. Boss fixes at the coop. |
| Two runs same day (manual re-trigger) | Second overwrites first. Intentional — allows a manual "retake" if the noon frame was bad. |

## Verification

- `launchctl print gui/501/com.voynichlabs.yard-diary-capture` — shows the job registered, next-fire time.
- `tail -f data/pipeline-logs/yard-diary.log` — watch the next run land.
- Hit `https://farm.markbarney.net/yard` after Railway redeploys — should show today's frame as hero.
- First entry 2026-04-17 was captured during setup (manual run at ~10:44) and is already live in farm-2026 `main`.

## Follow-ups (not today)

- If Boss wants the cherry tree specifically centered rather than the whole yard, consider saving a "daily-diary" Reolink preset and having the script recall it before snapshot. That requires the preset-save feature which isn't yet implemented — see TODO in CLAUDE.md.
- A "yard timelapse" page (every-30-min frames for a week during bloom) could live alongside the daily diary. Different cadence, different aesthetic, different retention. If Boss wants it, spin a separate plan.
- Captioning: if we want "Day 4 of bloom: first pink petals" style copy, add a sibling MDX file per date with author-written notes, or run a separate VLM pass on the masters (not in the live pipeline).

done.
