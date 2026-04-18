# 17-Apr-2026 — Yard-diary capture (thrice-daily, dated)

Author: Claude Opus 4.7 (1M context)

## Purpose re-clarification (18-Apr-2026)

Boss clarified after the initial build: **the yard-diary is raw material for a year-end timelapse reel, not daily curated site content.** Cherry bloom → summer green → autumn burn → snow, assembled at year-end via `ffmpeg -framerate 30 -pattern_type glob -i 'data/yard-diary/*.jpg' ...`. The captures and the 4K masters on the Mini are the primary artifact; `/yard` on farm-2026 is a secondary browse surface so the stockpile is visible while it accrues.

**Do not retire this pipeline as "boring content."** Individual frames are boring on purpose; the sequence is what matters. If a future agent proposes stopping the LaunchAgent, deleting masters, or removing `/yard` without explicit instruction from Boss — they have misread the purpose. Push back and re-read this section.

Related:
- Auto-memory: `~/.claude/projects/-Users-macmini/memory/project_yard_diary_pipeline.md`
- farm-guardian `CLAUDE.md` — "Recent Changes (17-Apr-2026)" section
- farm-2026 `docs/FRONTEND-ARCHITECTURE.md` — yard-diary row in the SSoT table
- farm-2026 `app/yard/page.tsx` — file header restates this purpose
- farm-guardian `scripts/yard-diary-capture.py` — file header restates this purpose

## The problem

Boss wants a visual record of the yard through the year — the cherry tree blooming right now, the summer green, the autumn burn, the snow — for a year-end retrospective. The existing gems pipeline doesn't solve this: it only promotes `share_worth='strong'` frames from the VLM pass and the house-yard camera currently produces zero strong gems. Relying on stochastic curation for a seasonal-record story is the wrong failure mode — miss a day, miss the bloom.

Requirements tightened in conversation:
1. **Three captures a day** — around 07:00 (sunup), 12:00 (noon), 16:00 (before sundown). Boss wants morning / noon / evening light on every day.
2. **Date burned into the image** — not an HTML caption, not alt text, not EXIF. The `DD-Mon-YYYY` label is part of the JPEG itself so the retrospective artifact is self-describing regardless of how the image ends up being viewed (slideshow, print, re-share).

Written post-hoc — Boss was clear the work needed to happen today, not after a planning round trip.

## Scope

**In:**
- Three captures per day (morning / noon / evening) from the Reolink (`house-yard`) via launchd.
- 4K master stored on the Mini (`data/yard-diary/{YYYY-MM-DD}-{slot}.jpg`).
- 1920px published copy with a burned-in `DD-Mon-YYYY` label in the lower-right, committed to farm-2026's `public/photos/yard-diary/` so Railway serves it from its own CDN.
- Automatic git commit + push on each capture; site picks up the new frame without manual action.
- Idempotent — re-running in the same slot on the same day overwrites (safe for manual re-triggers).
- Site surface: `app/yard/page.tsx` renders today's latest slot as hero + per-day triptych rows below, newest day first.

**Out:**
- PTZ re-aim. Whatever the Reolink is pointed at is what we get. The cherry tree is currently in frame (confirmed by Boss 2026-04-17); if it leaves the frame later, that's a coop-side re-aim, not a code change. Per `docs/08-Apr-2026-absolute-ptz-investigation.md`, absolute PTZ doesn't work reliably — don't script aim changes.
- VLM enrichment. The diary frames are not run through `glm-4.6v-flash`; they're raw snapshots. Captions, if ever wanted, go in a sibling MDX file per date.
- Retention policy. At 1,095 frames/year × ~900 KB published ≈ 1 GB/year in the farm-2026 repo, plus ~1.5 GB/year in 4K masters on the Mini. Manageable for now; revisit at year-2.

## Architecture

```
07:00 / 12:00 / 16:00 daily  (launchd — com.farmguardian.yard-diary-capture)
      │
      ▼
/opt/homebrew/bin/python3  /Users/macmini/bin/yard-diary-capture.py
      │
      ├─► slot = morning|noon|evening  (derived from current hour)
      ├─► curl localhost:6530/api/v1/cameras/house-yard/snapshot
      │         (reuses existing Guardian Reolink snapshot endpoint)
      │
      ├─► data/yard-diary/{YYYY-MM-DD}-{slot}.jpg   (4K master, ~1.3 MB)
      │
      ├─► Pillow: resize (long edge → 1920px)
      │          → draw rounded-rect pill at bottom-right
      │          → overlay "DD-Mon-YYYY" in HelveticaNeue white-on-translucent-dark
      │          → JPEG quality 88
      │
      ├─► farm-2026/public/photos/yard-diary/{YYYY-MM-DD}-{slot}.jpg
      │
      └─► git add + commit "yard-diary: {YYYY-MM-DD} {slot}" + push
              │
              ▼
         Railway redeploys → site shows new frame
```

### Key invariants

1. **Reuses the existing Guardian snapshot endpoint** — no new backend code. If that endpoint breaks, the dashboard and the gems pipeline also break, so we'll know.
2. **Publish path is farm-2026's `public/`, not a Guardian API endpoint.** The diary is served from Railway's CDN with zero Cloudflare-tunnel dependency at view time. A redeploy-per-capture cost is acceptable (three deploys/day).
3. **Master and published are separate.** 4K masters live indefinitely on the Mini for future re-use (a print, a zoom-in crop, a re-render at a different size or without the overlay). The published copy is downscaled + overlaid + committed.
4. **Date is burned into the pixels.** The retrospective artifact is self-describing.
5. **Script lives at `~/bin/`, not `~/Documents/`.** Launchd denies execution of scripts under TCC-protected `~/Documents/`. `~/bin/` is clear. The label prefix `com.farmguardian.*` piggybacks on the known-working Guardian plist's TCC grant so the Python process can still write into `~/Documents/` — the farm-guardian CLAUDE.md covers this under "LaunchAgent posix_spawn EPERM."

### Slot derivation

Slot comes from the system hour at runtime, not a plist argument, so one script handles all three firings:

| Hour (local) | Slot       |
|--------------|------------|
| `< 10`       | `morning`  |
| `10–13`      | `noon`     |
| `>= 14`      | `evening`  |

A launchd fire at 07:00 lands in morning, 12:00 in noon, 16:00 in evening. Ad-hoc kickstarts pick up whichever slot matches the current hour, so manual re-fires don't require reasoning about which label to pass.

### Date label rendering

`Pillow.ImageDraw.text` with `HelveticaNeue.ttc`. Font size scales with image width (~2.2%) so the label is readable but not dominant. A rounded semi-transparent dark pill sits behind the text so it stays legible against any seasonal backdrop (blown-out sky, snow, dark tree line). White text, ~95% opacity. Positioned bottom-right with ~1.8% margin.

Format: `DD-Mon-YYYY` (e.g. `17-Apr-2026`) — Boss's standard date format, matching his repo filenames and docs.

## Files

| Path | Purpose |
|------|---------|
| `scripts/yard-diary-capture.py` | **Source of truth, git-tracked.** Copy installed at `~/bin/yard-diary-capture.py`. |
| `~/bin/yard-diary-capture.py` | Installed copy that launchd actually fires. Out of `~/Documents/` to dodge TCC. |
| `~/Library/LaunchAgents/com.farmguardian.yard-diary-capture.plist` | Fires at 07:00 / 12:00 / 16:00 local. Not in git. |
| `data/yard-diary/{YYYY-MM-DD}-{slot}.jpg` | 4K masters. Gitignored via `data/`. |
| `data/pipeline-logs/yard-diary.log` | Per-run log. |
| `/tmp/yard-diary-stderr.log` | Launchd stderr redirect (mirrors the Guardian plist convention). |
| `../farm-2026/public/photos/yard-diary/{YYYY-MM-DD}-{slot}.jpg` | 1920px published copies with date overlay. In git. |
| `../farm-2026/app/yard/page.tsx` | `/yard` route — latest as hero, per-day triptych below. |

## Failure modes

| Mode | Behavior |
|------|----------|
| Guardian snapshot endpoint down | Python script logs `ERROR: snapshot fetch failed`; exits 1; no git commit; next slot retries. |
| Snapshot tiny (camera off, error page) | Size check rejects `< 50 KB`, deletes the master, exits 1. |
| Pillow font load fails | Falls back to `ImageFont.load_default()`. Label still burns in but smaller and less pretty. |
| `git push` fails (no network) | Commit is made locally; next run's push will include it. Log shows WARN. |
| Reolink pointed at sky / wrong angle | Captured anyway. Boss re-aims at the coop. |
| Two runs same slot same day | Second overwrites first. Intentional — allows a manual retake if the scheduled frame was bad. |
| TCC blocks script exec | Shouldn't happen now (script out of `~/Documents/`, label prefix in the known-working family). If it does, see farm-guardian CLAUDE.md on renaming labels and the `com.farmguardian.guardian` post-mortem. |

## Verification

- `launchctl print gui/501/com.farmguardian.yard-diary-capture` — shows the job registered with three calendar entries and `last exit code = 0` after each fire.
- `tail -f data/pipeline-logs/yard-diary.log` — watch each slot land.
- `https://farm.markbarney.net/yard` — after Railway redeploys, shows today's hero + triptych.
- First dated frame: **`2026-04-17-noon.jpg`**, label `17-Apr-2026` — captured during setup and already live in `farm-2026` `main`. Subsequent slots flow through automatically.

## Follow-ups (not today)

- Dedicated Reolink preset "yard-diary" once the camera-control module supports preset save. Script would recall the preset before each snapshot so the framing is identical day-to-day. Until then, leaving the camera pointed is sufficient.
- Per-date MDX notes. If Boss wants "Day 4 of bloom — first pink petals" text alongside a frame, drop `content/yard-diary-notes/{YYYY-MM-DD}.mdx` and extend the page to read them.
- Timelapse generator. At ~1,000 frames/year, `ffmpeg -framerate 30 -pattern_type glob -i '*.jpg'` produces a 36-second year in review. Boss can ask for this at Christmas.

done.
