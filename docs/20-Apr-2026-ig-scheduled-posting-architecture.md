# Instagram Posting — Scheduled Architecture (Current State)

**Audience:** the next Claude Code session picking up Instagram work on `@pawel_and_pawleen`.
**Last updated:** 2026-04-20 (afternoon — supersedes the 2026-04-20 morning plan docs).
**Status:** live in production.

---

## TL;DR

Four LaunchAgents run the whole thing. Humans react on Discord `#farm-2026`; those reactions are the only quality gate; nothing gets posted to Instagram without a reaction.

```
LaunchAgent                                      Script                              Cadence
────────────────────────────────────────────────────────────────────────────────────────────────
com.farmguardian.discord-reaction-sync           scripts/discord-reaction-sync.py    every 30 min
com.farmguardian.ig-2hr-story                    scripts/ig-2hr-story.py             every 2 hours
com.farmguardian.ig-daily-carousel               scripts/ig-daily-carousel.py        daily 18:00 local
com.farmguardian.ig-weekly-reel                  scripts/ig-weekly-reel.py           Sundays 19:00 local
```

Per-cycle auto-posting from the pipeline orchestrator is **dead** (config flag `instagram.enabled=false`). Do not re-enable it without Boss approval — it spams one-frame-per-strong+sharp-gem which is exactly what the scheduled architecture replaces.

---

## Hard rules (inherited; violations are regressions)

1. **Baby birds.** Brooder chicks, flock, yorkies, coop, yard-diary. Not security, not AI-showcase, not flex content.
2. **Never frame Guardian as a security/predator system.** Predator on camera = dead bird, not content.
3. **Hashtags only from [`tools/pipeline/hashtags.yml`](../tools/pipeline/hashtags.yml).** The `forbidden` list is a runtime safety net.
4. **No creator-branded hashtags** (`#markbarney*`, `#builtwithai`, etc.). Sign-off `📸 @markbarney121` goes in the caption body only.
5. **No emoji in code or commits.** The `📸` is only in runtime-built caption strings.
6. **Tokens live in keychain + [`/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env`](file:///Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env) (0600).** Never commit either.
7. **Call `advisor` before substantive edits.** Boss's standing directive.
8. **Zero CLI for Boss.** Boss never types into a terminal. If a new lane needs manual triggering, that's the wrong design — put it on a LaunchAgent.

---

## How the quality gate works

```
[capture cycle] ──► VLM tags it strong+sharp+birds≥1
                              │
                              ▼
                 [gem_poster.py] posts it to Discord #farm-2026
                              │
                              ▼
              humans (NOT Larry/Bubba/Egon) react with emoji
                              │
                              ▼
   [discord-reaction-sync.py] every 30 min:
     - fetches messages from #farm-2026
     - counts unique non-bot reactors per message
     - matches each message back to an image_archive row
       by (camera_id, ts ±60s) — no sha256 match because
       Discord's CDN re-encodes the JPEG
     - writes image_archive.discord_reactions = count
                              │
                              ▼
   [ig_selection.py] all selection queries require
       discord_reactions >= 1
                              │
                              ▼
              [post to Instagram as story/carousel/reel]
```

**Larry, Bubba, Egon** are Claude instances on other machines. Their Discord user IDs are in [`tools/discord_harvester.py`](../tools/discord_harvester.py) `BOT_USER_IDS`. Their reactions do NOT count. Only actual humans.

**VLM tags (share_worth, image_quality, bird_count) are inputs to the Discord-post gate**, not the Instagram-post gate. They filter out obvious junk before humans see it, but a strong+sharp VLM tag does NOT mean Instagram-worthy. Boss has seen the VLM tag heat-lamp-orange-cast clipped frames as `strong+sharp`. The reaction gate is the backstop.

---

## The selection helpers ([`tools/pipeline/ig_selection.py`](../tools/pipeline/ig_selection.py))

All three helpers require `discord_reactions >= 1` AND `has_concerns=0` AND `image_path IS NOT NULL`.

| Helper | Window | VLM tier | Quality | Diversity bucket | Notes |
|---|---|---|---|---|---|
| `select_daily_carousel_gems` | today (UTC) | `strong` | `sharp` | (camera, 15 min) | Excludes gems with `ig_permalink` populated |
| `select_best_story_gem` | last N min (default 120) | `strong` or `decent` | `sharp` or `soft` | — (picks single best) | Excludes gems with `ig_story_id` populated |
| `select_weekly_reel_gems` | last N days (default 7) | `strong` | `sharp` | (camera, 6 hr) | Stitches an MP4 |

Ranking is by the `_score_gem` tuple: `(discord_reactions, tier_rank, quality_rank, bird_count, ts)`. Reaction count always beats VLM tags — a gem with 2 reactions wins over a gem with 1 regardless of VLM.

Diversity filter: group candidates by (camera_id, time-bucket); pick highest-scoring gem per group; return chronologically ordered. Stops two near-identical shots ending up in the same carousel — Boss flagged this on the very first hand-curated post.

---

## Config ([`tools/pipeline/config.json`](../tools/pipeline/config.json) — gitignored, per-host)

Canonical block:

```json
"instagram": {
  "enabled": false,          // per-cycle hook — stays false
  "auto_dry_run": false,
  "farm_2026_repo_path": "/Users/macmini/Documents/GitHub/farm-2026",
  "meta_env_file": "/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env",
  "stories": {
    "enabled": false,         // orchestrator story hook — stays false
    "auto_dry_run": false,
    "min_hours_between_stories": 2
  },
  "reels": {
    "enabled": false,         // orchestrator reel hook — never wired
    "auto_dry_run": false,
    "output_root": "data/reels",
    "seconds_per_frame": 1.0,
    "crossfade_seconds": 0.15,
    "frames_per_reel_default": 6
  },
  "scheduled": {
    "daily_carousel_max_items": 10,
    "daily_carousel_min_items": 2,
    "daily_carousel_bucket_minutes": 15,
    "story_window_minutes": 120,
    "weekly_reel_window_days": 7,
    "weekly_reel_max_frames": 8,
    "weekly_reel_bucket_hours": 6
  }
}
```

`enabled`, `stories.enabled`, `reels.enabled` are vestigial from earlier work — they gate the orchestrator's per-cycle hooks. Those hooks still exist in [`tools/pipeline/orchestrator.py`](../tools/pipeline/orchestrator.py) but are never called because the flags are false. **Leave them false.** The scheduled agents don't consult these flags.

The `scheduled` sub-block is what the three LaunchAgent scripts read.

---

## Schema ([`tools/pipeline/store.py`](../tools/pipeline/store.py) — image_archive table)

Added this session (v2.32.0):

| Column | Type | Written by |
|---|---|---|
| `discord_message_id` | TEXT | `discord-reaction-sync.py` |
| `discord_reactions` | INT DEFAULT 0 | `discord-reaction-sync.py` |
| `discord_reactions_checked_at` | TEXT | `discord-reaction-sync.py` |

Plus the pre-existing IG columns: `ig_permalink`, `ig_posted_at`, `ig_skip_reason`, `ig_story_id`, `ig_story_posted_at`, `ig_story_skip_reason`.

Indexes: `idx_archive_discord_reactions`, `idx_archive_discord_message`, `idx_archive_ig_posted`, `idx_archive_ig_story_posted`.

Migration is idempotent via `_add_column_if_missing` — call `ensure_schema(db_path)` and the columns arrive on whatever DB is in front of you.

---

## Plists ([`deploy/ig-scheduled/`](../deploy/ig-scheduled/))

All four plists use `Label = com.farmguardian.*` (known-working TCC label family). Installed at `~/Library/LaunchAgents/`. Bootstrapped via `launchctl bootstrap gui/$(id -u) <plist>`.

```
com.farmguardian.discord-reaction-sync   StartInterval=1800  RunAtLoad=true
com.farmguardian.ig-2hr-story            StartInterval=7200  RunAtLoad=false
com.farmguardian.ig-daily-carousel       StartCalendar Hour=18 Minute=0
com.farmguardian.ig-weekly-reel          StartCalendar Weekday=0 Hour=19 Minute=0
```

Check status: `launchctl list | grep farmguardian.ig`. Kickstart on demand: `launchctl kickstart gui/$(id -u)/com.farmguardian.ig-2hr-story`.

Logs:
- `/tmp/discord-reaction-sync.{out,err}.log`
- `/tmp/ig-2hr-story.{out,err}.log`
- `/tmp/ig-daily-carousel.{out,err}.log`
- `/tmp/ig-weekly-reel.{out,err}.log`

---

## The `gem_poster → Discord → sync → IG` round-trip in detail

**gem_poster posts** ([`tools/pipeline/gem_poster.py`](../tools/pipeline/gem_poster.py)):

- Gate: `image_quality=sharp AND bird_count>=1` (NOT share_worth-gated; see the gate_poster.should_post docstring for the v2.28.7 history of why tier was dropped from this filter).
- Webhook username is mapped via `_USERNAME_BY_CAMERA`:
  - `s7-cam` → `"S7 Brooder"`
  - `house-yard` → `"Yard"`
  - `mba-cam` → `"Brooder Overhead"`
  - `usb-cam` → `"Brooder Floor"`
  - `gwtc` → `"Coop"`
  - `iphone-cam` → `"iphone-cam"` (no friendly alias; falls through to raw name)
- Image filename: `{camera_name}-gem.jpg` (NOT the gem_id). This means Discord doesn't carry a direct pointer back to image_archive.

**discord-reaction-sync finds matches** ([`scripts/discord-reaction-sync.py`](../scripts/discord-reaction-sync.py)):

- Reverses `_USERNAME_BY_CAMERA` to turn `msg.author.username` back into a camera_id.
- SQL: find the image_archive row with `camera_id = <mapped>` and `ts` closest to `msg.timestamp`, require delta ≤ 60s.
- Writes reaction count back.

**Cross-reference caveat:** sha256 matching does NOT work because Discord's CDN re-encodes uploaded JPEGs. The timestamp+camera match is the only reliable link. If you ever change `gem_poster` to post with different timing (e.g., batched or delayed), update the sync's tolerance window.

---

## Useful one-liners

```bash
# How many eligible unposted gems are there right now?
venv/bin/python -c "
import sqlite3; c = sqlite3.connect('data/guardian.db')
print(c.execute('''SELECT COUNT(*) FROM image_archive
  WHERE discord_reactions >= 1 AND ig_story_id IS NULL
  AND ig_permalink IS NULL AND image_path IS NOT NULL
  AND (has_concerns=0 OR has_concerns IS NULL)''').fetchone()[0])
"

# Force a sync right now (instead of waiting for the 30-min cadence)
launchctl kickstart gui/$(id -u)/com.farmguardian.discord-reaction-sync

# See what a sync actually wrote
tail -20 /tmp/discord-reaction-sync.out.log

# Dry-run a carousel selection without posting
venv/bin/python scripts/ig-daily-carousel.py --dry-run

# Scrape the whole Discord channel (use when you think older reactions were missed)
venv/bin/python scripts/discord-reaction-sync.py --backfill
```

---

## Known unresolved items (as of 2026-04-20)

1. **51 reacted gems sitting unposted as stories.** Today's batch posted the top 20 by reactions. The other 51 (single-reaction, older dates) are still eligible. The 2h story LaunchAgent will pick from the 2h window, so these older gems will not auto-post unless their ts is recent. If Boss wants a bigger catch-up sweep, run the inline script from today's transcript.
2. **No auto-reel orchestration history yet.** The first weekly reel fires Sunday 19:00 local. Watch `/tmp/ig-weekly-reel.*.log` for the first real run. ffmpeg stitch tested offline; Graph API publish tested via the manual CLI but not via LaunchAgent.
3. **The stale CLI — [`scripts/ig-post.py`](../scripts/ig-post.py)** — still works and still takes `--mode {photo,story,reel}`. It's not used by any scheduled lane. Keep it around for emergencies (e.g., Boss wants to force-post a specific gem); don't use it in automation.
4. **Orchestrator-internal hooks — `_maybe_post_to_ig`, `_maybe_post_to_story`** — still exist in [`orchestrator.py`](../tools/pipeline/orchestrator.py) but are `cfg["instagram"]["enabled"]=false`-gated. Left in place so the code doesn't bitrot; if a future design reverts to per-cycle posting, the plumbing is there.
5. **CLAUDE.md's Instagram section was last synced to the pre-scheduled design.** Out of date — it claims photo auto-posting is live. Fix: update the bullet that describes the IG architecture to point here. Do NOT rewrite the hard rules (1–7 above), those are still authoritative.

---

## First-read list for a fresh agent

1. **This doc** (you're reading it).
2. [`tools/pipeline/ig_selection.py`](../tools/pipeline/ig_selection.py) — the three selection functions and the `_score_gem` ordering.
3. [`scripts/discord-reaction-sync.py`](../scripts/discord-reaction-sync.py) — how reactions land in the DB.
4. [`tools/pipeline/ig_poster.py`](../tools/pipeline/ig_poster.py) — `post_gem_to_story`, `post_gem_to_ig`, `post_carousel_to_ig`, `post_reel_to_ig`, and the Graph API primitives they share.
5. [`19-Apr-2026-instagram-posting-plan.md`](19-Apr-2026-instagram-posting-plan.md) — account voice / hashtag / framing rules. Still the canonical "what does this account sound like" doc.
6. [`tools/discord_harvester.py`](../tools/discord_harvester.py) — pre-existing script that harvests Discord-reacted images to the farm-2026 website gallery. Separate flow from the IG pipeline; don't merge them. The IG sync borrows its Discord API helpers.

---

## What NOT to do

- Do NOT re-enable `instagram.enabled=true` in the pipeline config. The scheduled agents are the single path.
- Do NOT post to Instagram directly from the orchestrator. Orchestrator captures + scores; scheduled agents post.
- Do NOT lower the reaction gate below `>= 1`. A reaction is the only signal that's empirically correlated with "Boss won't delete this post."
- Do NOT add AI/tech hashtags, creator-branded hashtags, or anything outside [`hashtags.yml`](../tools/pipeline/hashtags.yml).
- Do NOT post more than ~20 stories in one batch without Boss confirming. Feed stacking is a thing.
- Do NOT use `scripts/ig-post.py` from the orchestrator or a LaunchAgent. It's an emergency tool.
- Do NOT rebuild the Discord webhook plumbing. `gem_poster.py` is known-working.
- Do NOT attempt sha256 matching between Discord attachments and `image_archive` — CDN re-encodes guarantee zero matches. Use (camera, ts ±60s) and accept the 60-second tolerance.
