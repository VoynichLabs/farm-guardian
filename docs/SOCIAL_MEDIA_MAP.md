# SOCIAL_MEDIA_MAP.md — Farm social-media pipeline map

This is the current, live map of how cute photos of the flock get from camera frames and the iPhone archive out to Instagram, Facebook, Discord, and Nextdoor — and how engagement on those platforms feeds back into the system.

**This file is the source of truth for how the social pipeline runs *today*.** The dated docs in this directory (e.g. `19-Apr-2026-instagram-posting-plan.md`, `23-Apr-2026-nextdoor-plan.md`) are frozen planning artifacts — they're useful for *why* a thing exists, not *what's running now*. If those docs and this file disagree, this file wins; update it instead of writing another dated doc.

Verified against `launchctl list | grep farmguardian` and `~/Library/LaunchAgents/com.farmguardian.*` on 2026-04-26.

---

## The one-paragraph version

Cameras and the iPhone catalog are the two raw sources. Camera frames flow through the VLM enricher (LM Studio, every cycle) and only the ones the VLM rates `share_worth=strong` get dropped into Discord `#farm-2026` for Boss to react to. **A Boss reaction on Discord is the quality gate.** Every outbound lane (IG photo, IG carousel, IG story, IG reel, FB Page, Nextdoor) reads `image_archive.discord_reactions > 0` as its filter. The iPhone catalog runs a parallel "on-this-day" lane with no reaction gate — it's pre-curated archival content. Every successful IG post auto-mirrors to the FB Page. Engagement automation (likes/comments on IG and Nextdoor) runs as separate session-capped tools, not on a schedule.

---

## Outbound — farm → social

| Surface | Code | LaunchAgent | Cadence | Source |
|---|---|---|---|---|
| **IG photo** (single) | `tools/pipeline/ig_poster.py` | none — emergency CLI only | manual | reaction-gated gem |
| **IG carousel** | `tools/pipeline/ig_poster.py::post_carousel_to_ig` | `com.farmguardian.ig-daily-carousel` | daily 18:00 | today's reacted strong+sharp gems |
| **IG story** | `tools/pipeline/ig_poster.py::post_gem_to_story` | (rolled into `social-publisher`) | hourly | every unposted reacted gem, FIFO, 25/tick cap |
| **IG reel** | `tools/pipeline/ig_poster.py::post_reel_to_ig` | `com.farmguardian.ig-daily-reel` | daily 18:00 | past 24h reacted gems, ffmpeg-stitched; **Discord approval gate**: reel MP4 posted to `#farm-2026` first — Boss must react before it publishes to IG (checked on the next day's 18:00 run). Unreacted reels expire after 48h. State: `data/reels/pending/`, `posted/`, `expired/`. Script: `scripts/ig-daily-reel.py`. |
| **FB Page** ("Yorkies App") | `tools/pipeline/fb_poster.py` | none — tail-called from each `ig_poster` success | mirrors IG | every successful IG post auto-dual-posts |
| **On-this-day → IG/FB stories** | `tools/on_this_day/post_daily.py` (via `scripts/on-this-day-stories.py`) | (rolled into `social-publisher`) | hourly | iPhone catalog at `~/bubba-workspace/projects/photos-curation/photo-catalog/master-catalog.csv` |
| **Unified social publisher** | `scripts/social-publisher.py` | `com.farmguardian.social-publisher` | hourly (`StartInterval=3600`) | runs gem-lane + on-this-day in one tick |
| **Nextdoor** (Hampton CT) | `tools/nextdoor/crosspost.py` (via `scripts/nextdoor-crosspost.py`) | `com.farmguardian.nextdoor-crosspost` | 08:00 throwback + 18:30 today | 1 reacted live-cam gem + 1 archive photo per day |

---

## Inbound — social → farm

| Surface | Code | LaunchAgent | Cadence | What it does |
|---|---|---|---|---|
| **iPhone live ingest** | `tools/iphone_lane/ingest.py` (via `scripts/iphone-ingest.py`) | `com.farmguardian.iphone-ingest` | hourly (`StartInterval=3600`) | walks Photos.sqlite for the last 6h of new iPhone photos, runs each through the standard VLM enricher, posts strong-tier results into Discord `#farm-2026` with `camera_id="iphone"`. From there the reaction-gated lanes pick them up like any camera gem. Dedupe ledger: `data/iphone-lane/ingested.json`. |
| **Discord reaction sync** | `scripts/discord-reaction-sync.py` | `com.farmguardian.discord-reaction-sync` | every 30 min (`StartInterval=1800`) | scrapes reaction counts onto `image_archive.discord_reactions` — the quality gate every outbound lane reads |
| **Archive throwback → Discord** | `scripts/archive-throwback.py` | `com.farmguardian.archive-throwback` | daily 08:00 | posts catalog photos to `#farm-2026` so Boss can react; reactions feed back into the gem queue |
| **IG engagement** (likes/comments/story reactions) | `tools/ig-engage/engage.py` | none — manual / planned | session-capped | plays @pawel_and_pawleen's outbound presence — 30 likes / 10 comments / 20 story reactions per day |
| **Nextdoor engagement** | `tools/nextdoor/engage.py` | none — manual, not scheduled | session-capped | 10 likes / 3 comments per day |
| **FB Page reciprocate harvester** | `tools/on_this_day/reciprocate.py` | `com.farmguardian.reciprocate.plist.disabled` | currently OFF | pulls who's reacting/commenting on FB Page, posts top-15 to Discord channel `1476787165638951026` for manual click-through |

---

## Shared infrastructure

- **Reaction-gate trust signal:** `image_archive.discord_reactions` — single source of truth. Every outbound lane filters `WHERE discord_reactions > 0`. Cross-reference from a Discord message back to its `image_archive` row is by `(camera_id, ts ±60s)`, NOT sha256 (Discord CDN re-encodes). Reactions from Larry / Bubba / Egon (other Claude instances) don't count.
- **Cookie-lift session bootstrap** (no logins, no 2FA): `tools/chrome_session/decrypt.py`, shared by IG-engage and Nextdoor. Per-track Playwright Chromium persistent profiles at `~/Library/Application Support/farm-{ig-engage,nextdoor}/profile/`.
- **Browser automation stack** (when standing up a new social surface): Playwright + persistent profile, `tools/chrome_session/codegen.py` codegen wrapper, `chrome-devtools` MCP, Claude-for-Chrome extension. Index doc: `~/bubba-workspace/skills/browser-automation/SKILL.md`.
- **Tokens:** `~/bubba-workspace/secrets/farm-guardian-meta.env` (`0600`, gitignored) — non-expiring IG + FB long-lived tokens. Discord bot token in `~/.openclaw/openclaw.json`.
- **IG publish quota — HARD LIMIT:** Instagram Graph API caps Business accounts at **25 `media` publishes per rolling 24h**, shared across BOTH the gem lane (`ig-2hr-story` + `ig-daily-carousel` + `ig-weekly-reel`) AND the on-this-day archive lane. Both lanes detect the 403 and stop the batch cleanly so the next tick resumes when a slot frees. Don't "fix" the 403 by cranking timeouts or regenerating tokens — it's a hard quota, not auth.
- **FB cross-post is a SETTLED capability** (live since 2026-04-21, CHANGELOG v2.35.1). All four lanes — photo, carousel, story, reel — verified. Tokens non-expiring. Don't re-research Meta scopes; the "Manage everything on your Page" Use Case is already attached. Toggle: `FB_CROSSPOST_ENABLED=0` to disable without code change.

## Kill switches

- `touch /tmp/ig-engage-off` — disables IG engagement automation
- `touch /tmp/nextdoor-off` — disables Nextdoor lanes
- (no global one — each surface is independent on purpose)

---

## Per-track deep dives

These are the canonical runbooks for each track. They live in `~/bubba-workspace/skills/` (outside this repo) because they're invocation guidance for cross-agent use, not pipeline architecture. Read the relevant one before touching that surface.

- **IG posting** — `~/bubba-workspace/skills/farm-instagram-post/SKILL.md`
- **IG engagement** — `~/bubba-workspace/skills/farm-instagram-engage/SKILL.md`
- **FB cross-post** — `~/bubba-workspace/skills/farm-facebook-crosspost/SKILL.md`
- **Nextdoor (both lanes)** — `~/bubba-workspace/skills/farm-nextdoor-engage/SKILL.md`
- **On-this-day archive lane** — `../tools/on_this_day/README.md` (in this repo)
- **IG architecture (canonical)** — `20-Apr-2026-ig-scheduled-posting-architecture.md` (sibling file; slightly out of date on cadence — the social-publisher unification 23-Apr supersedes the four-LaunchAgent diagram, but the rest is current)

---

## Where this fits with other docs in this repo

- **`../CLAUDE.md`** at the repo root has inline social-pipeline detail mixed in with camera detail. That coverage is more verbose; this file is the surface-by-surface map.
- **`HOW_IT_ALL_FITS.md`** (sibling file) is the broader 10,000-ft view including the camera→pipeline→VLM→archive flow, not just the social side. Read it for context on *where the gems come from*; read this file for *where they go*.
- **Dated docs in this directory** are planning archives. They're frozen at the moment they were written. Don't trust them for current state.

## When to update this file

When any of these change, edit this file in the same commit:

- A LaunchAgent is added, retired, or has its cadence changed
- A new outbound surface is wired up (e.g. Threads, Bluesky, TikTok)
- The reaction-gate logic changes (e.g. trust signal moves off Discord reactions)
- A token / secrets path moves
- A kill switch is added or removed

If this file is older than 60 days at the time you read it, re-verify against `launchctl list | grep farmguardian` and `ls ~/Library/LaunchAgents/com.farmguardian.*` before trusting any row.
