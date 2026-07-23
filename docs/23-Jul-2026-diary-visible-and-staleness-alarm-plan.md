<!--
Author: Claude Opus 4.8 (1M)
Date: 23-July-2026
PURPOSE: Plan-of-record for the two pieces the daily-diary system was still
         missing after the 20-Jul writer shipped (v2.51.9/.10): (1) a reaction
         that produces VISIBLE public content, and (2) a staleness alarm so the
         diary can't silently rot again — the exact failure that started this.
SRP/DRY check: Pass — reuses tools/discord_harvester (token/fetch/git), the
         daily_reel_runner caption-eligibility logic (imported, not copied), and
         the existing Boss-reaction gate. Adds no new storage or scheduler concept.
-->

# Diary: reaction→visible + staleness alarm (23-Jul-2026)

## Why
The 20-Jul plan diagnosed the diary and the 23-Jul build (`farm-diary-from-discord.py`,
v2.51.9/.10) added the nightly writer + fixed the `unresolved`→`resolved` filter bug. Two
gaps remained after that build:

1. **The reaction does nothing.** The nightly post to `#farm-2026` says "react … a reaction
   promotes it toward the public field notes," but nothing consumed the reaction and nothing
   wrote a field-note. So the daily story only improved reel captions (invisible) and sat in
   Discord — it never became visible content on the site. That was the headline of the
   original ask.
2. **Nothing watches for staleness.** The whole outage happened because the diary silently
   went stale for weeks and no one noticed until captions were visibly repeating. The writer
   makes that less likely but doesn't close the loop: if it dies quietly (the `claude` CLI
   wedging, an auth hiccup, or a stretch of no farm chat), captions starve again with no
   warning.

## Scope
**In:** the reaction→field-note promoter; the staleness canary; their two LaunchAgents;
docs + changelog. **Out:** posting the diary to IG/FB as a standalone social post (IG is
image-first; the caption path already carries the narrative into reels), reviving the
`/diary` route (field-notes is the sanctioned public surface per farm-2026 CLAUDE.md), and
any change to the writer or the caption consumer.

## Architecture (reuse-first)

### Component 1 — `scripts/diary-promote-on-reaction.py` (LaunchAgent, hourly)
- Fetch recent `#farm-2026` messages (bounded lookback), select the bot's diary posts by
  author id + the `**Farm diary — {stem}**` title line.
- Gate on **the Boss's** reaction specifically (`293569238386606080`) — the authoritative
  signal per SOCIAL_MEDIA_MAP; other reactors don't publish.
- Promote: read `farm-2026/content/diary/{stem}.md` → write `content/field-notes/{stem}.mdx`
  with frontmatter (`title`, `date`, `tags`, and a `cover` auto-picked from that day's
  `public/photos/carousel/{date}/` gems when one exists — the page guards `{cover && …}`, so
  a missing cover is safe). Strip the duplicate leading H1 (title lives in frontmatter) and
  MDX-escape `<>{}` in the prose (MDXRemote is strict).
- Commit **only that file** (not `git add -A`, which would sweep the other async
  committers' work) and push farm-2026 → Railway deploys → visible at `/field-notes`. One
  retry with `git pull --rebase` on a rejected push (the repo has many auto-pushers).
- Idempotent: a `data/diary-promote-state.json` ledger + an existence check prevent
  re-publishing. Reuses `tools/discord_harvester`: `load_bot_token`, `discord_headers`,
  `DISCORD_API`, `CHANNEL_ID` (#farm-2026), `BOT_USER_IDS`.
- **Why this satisfies the design:** farm-2026 CLAUDE.md states the intent outright —
  `content/diary` is raw source material (not published); field-notes is the published
  surface. Promotion is the sanctioned bridge, gated by the Boss's reaction.

### Component 2 — `scripts/diary-staleness-check.py` (LaunchAgent, daily 09:00)
- Imports `tools.pipeline.daily_reel_runner` (verified light: 0.12s, no side effects) and
  reuses `_load_farm_context`, `_diary_date`, `_RESOLVED_RE`, `FARM_CONTEXT_MAX_AGE_DAYS`,
  `FARM_DIARY_DIR` — so the alarm measures the **exact** context captions receive, and can't
  drift from it.
- Severity: **RED** if the live `_load_farm_context()` is empty (captions starved now) or no
  entry is in-window; **YELLOW** if the newest usable entry ages out within 3 days, or no
  diary file has been written in >2 days (writer likely down). Silent when healthy.
- Posts one alarm to `#farm-2026` via the bot, mentioning the Boss, with the concrete remedy
  (talk farm in `#meet-the-lobsters`, or run the writer with `--force`). A plain-text alarm
  with no image attachment can't pollute the gem reaction signal (reaction-sync only matches
  image posts), so `#farm-2026` is safe and is where the Boss already watches diary activity.

## Files
- NEW `scripts/diary-promote-on-reaction.py`
- NEW `scripts/diary-staleness-check.py`
- NEW `deploy/launchagents/com.farmguardian.diary-promote.plist` (hourly)
- NEW `deploy/launchagents/com.farmguardian.diary-staleness.plist` (daily 09:00)
- EDIT `CHANGELOG.md` (v2.52.0)
- Installed copies → `~/Library/LaunchAgents/`, loaded via `launchctl bootstrap`.

## Verification (before commit)
1. `diary-promote-on-reaction.py --dry-run` against live Discord — prints which diary posts it
   sees, whether the Boss reacted, and the field-note it *would* write. No file/push.
2. `diary-staleness-check.py --dry-run` — prints the computed severity + would-be alarm text
   against the real diary folder.
3. Confirm both import cleanly under the venv and exit 0.
4. Load both LaunchAgents; confirm `launchctl list | grep diary`.

## Guardrails
- Nothing reaches the public site without the Boss's reaction (Component 1 gate).
- The promoter escapes MDX-hostile characters and strips the duplicate title, so a promoted
  entry can't break the farm-2026 build.
- The alarm is measurement-only and read-only on the diary; it never writes entries.
- Targeted `git add` (single file) avoids clobbering concurrent async committers.

## Changelog touchpoint
v2.52.0 — what/why/how, author. The 20-Jul plan's "to build" items 1 (reaction→publish) and 2
(staleness) are delivered here; item 3 (provenance side-write) remains optional/out of scope.
