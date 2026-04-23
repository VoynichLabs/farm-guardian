# Nextdoor outbound cross-post — plan

**Author:** Claude Opus 4.7 (1M context)
**Date:** 23-April-2026
**Status:** approved by Boss in-session 2026-04-23; building immediately.
**Branch:** main.

## Scope

**In:**
- Nextdoor outbound cross-posting to Boss's Hampton CT neighborhood via the existing `farm-nextdoor` Playwright profile.
- Two posts per day, two lanes:
  - **Today lane** fires 18:30 local — pulls one reacted live-camera gem from today's `image_archive`.
  - **Throwback lane** fires 08:00 local — pulls one reacted `camera_id='discord-drop'` (archive-throwback) row that made it through Boss's Discord reaction gate.
- One photo per post (no carousels — Nextdoor's single-photo feed treatment is stronger).
- Captions drafted fresh each post by the currently-loaded VLM on LM Studio (no hardcoded opener rotation).
- Hard audience floor = `visibility-menu-option-2` ("Your neighborhood · Hampton only"). Refuse to submit if that option can't be picked.
- Per-post share-URL capture to `data/nextdoor/posts.json`.
- Shared dedup via `image_archive.nextdoor_posted_at`.

**Out:**
- No multi-photo posts.
- No neighbor-request / friend primitive (unchanged hard safety).
- No DM primitive (unchanged hard safety).
- No audience widening to "Nearby" or "Anyone" (even manually overridable — if the narrowest selector fails, the tick aborts).
- No LM-Studio model-load — only uses what's already loaded. Falls back to a short static caption if LM Studio is unreachable. Never crashes a post over a caption failure.

## Architecture

### Lane split via existing `image_archive` column `camera_id`

Both lanes pull from the same reaction-gated `image_archive` table — just filter by `camera_id`:

| Lane | SQL filter |
|---|---|
| Today | `camera_id IN ('s7-cam','gwtc','mba-cam','usb-cam','house-yard','iphone-cam') AND discord_reactions > 0 AND nextdoor_posted_at IS NULL AND ts >= (today 00:00 local)` |
| Throwback | `camera_id = 'discord-drop' AND discord_reactions > 0 AND nextdoor_posted_at IS NULL` (no date filter — any reacted archive drop is eligible) |

Order: `ORDER BY discord_reactions DESC, ts DESC LIMIT 1`.

If the today-lane query is empty, that tick skips silently. Throwback rarely empties because archive-throwback seeds Discord daily.

### New/changed code

- **NEW** `tools/nextdoor/caption_writer.py` — VLM call to LM Studio (`/v1/chat/completions` with the already-loaded model — never auto-loads). System prompt differs by lane. Reads the image bytes from disk, base64-encodes into a VLM message, asks for a 1–3-sentence neighbor-voice caption. Falls back to a one-line static caption on any failure.
- **REWRITE** `tools/nextdoor/crosspost.py` — orchestration: gate checks → pick gem → caption → post via primitives → capture share URL → mark posted.
- **NEW** `scripts/nextdoor-crosspost.py` — launcher, `--lane {today|throwback}` + `--dry-run` + `--headed` flags.
- **NEW** `deploy/launchagents/com.farmguardian.nextdoor-crosspost.plist` — one plist, two `StartCalendarInterval` entries (08:00 throwback, 18:30 today), dispatched via `scripts/nextdoor-crosspost.py` with `--lane` from a wrapper or env var. *Detail below.*
- **NEW SCHEMA** `image_archive.nextdoor_posted_at TEXT` (nullable timestamp) + `image_archive.nextdoor_share_url TEXT` — added idempotently at crosspost-module import.
- **EDIT** `tools/nextdoor/budget.py` — replace the 7-day cross-post cooldown with a daily per-lane cap (`post_today: 1`, `post_throwback: 1`).
- **EDIT** `~/bubba-workspace/skills/farm-nextdoor-engage/SKILL.md` — replace the "weekly Sunday" cross-post section with this two-lane design.

### LaunchAgent dispatch detail

One plist can't express "run A at 08:00 and B at 18:30" with different args directly — `StartCalendarInterval` fires the same `ProgramArguments`. Cleanest solution: the script reads the current local hour and picks the lane. If `hour < 12`, `--lane throwback`; else `--lane today`. One plist, two cal intervals, zero wrapper scripts. Manual override still works: `python scripts/nextdoor-crosspost.py --lane today` bypasses the clock.

### Caption writer — LM Studio safety

Per `farm-guardian/CLAUDE.md` LM Studio rules:

1. Call `GET /api/v0/models` — take the first model where `state == "loaded"`. If none, fall back.
2. Call `POST /v1/chat/completions` with `model=<that exact id>`, `messages=[{system}, {user+image}]`, `max_tokens=200`, `temperature=0.8`.
3. Never call `/models/load`, never pass a model name that wasn't in the loaded list.
4. 15s timeout. Any failure → static fallback caption.

The VLM sees the actual photo, so captions describe what's in the frame. Two different system prompts:

**Today-lane system prompt (summary):**
> You're writing a Nextdoor post for a neighborhood feed in Hampton, CT. Voice: warm neighbor, not promotional. 1–3 sentences. Mixed case, normal punctuation. Optional at most one tame emoji (🐣 ☀️ ❤️). Never mention this is cross-posted. Never reveal an exact address. Look at the photo and describe what's happening on the backyard chicken farm today. End with gentle curiosity (a question or open invitation) if it fits.

**Throwback-lane system prompt (summary):**
> Same voice, but this is a throwback. Open with a phrase like "Throwback —" or "Flashback to —" and describe what's in the photo. Warm, a little nostalgic.

Both prompts explicitly: no hashtags, no links, no Boss's name.

### Share-URL capture

After `submit_post`, the success modal contains `<a data-testid="share_app_button_TWITTER">` with href `https://twitter.com/intent/tweet/?text=...&url=https%3A%2F%2Fnextdoor.com%2Fp%2F{share_id}%3F...`. Decode the `url=` param, strip query string, land `https://nextdoor.com/p/{share_id}/`. Store in posts.json + `image_archive.nextdoor_share_url`.

Fallback if the share-button scan fails: record null URL, post is still committed.

### `posts.json` shape

```json
[
  {
    "ts": "2026-04-23T18:30:14-04:00",
    "lane": "today",
    "image_archive_id": 12345,
    "camera_id": "mba-cam",
    "image_path": "data/gems/2026-04/mba-cam/...-gem.jpg",
    "caption": "...",
    "share_url": "https://nextdoor.com/p/xyz/",
    "audience_confirmed": "Your neighborhood"
  }
]
```

## Ordered TODOs

1. Write `tools/nextdoor/caption_writer.py` with LM Studio call + fallback.
2. Rewrite `tools/nextdoor/crosspost.py`.
3. Edit `tools/nextdoor/budget.py` — per-lane daily caps, drop the 7-day cooldown.
4. Add idempotent `ALTER TABLE image_archive ADD COLUMN nextdoor_posted_at TEXT` + `nextdoor_share_url TEXT` at crosspost-module import (no separate migration script; the guardian db is single-host).
5. Write `scripts/nextdoor-crosspost.py` — dispatches to `tools.nextdoor.crosspost.run_tick(lane)`.
6. Dry-run once per lane: `venv/bin/python scripts/nextdoor-crosspost.py --lane throwback --dry-run --headed`, same for today.
7. Live fire once per lane while Boss is elsewhere — headed, eyeball submit; confirm share URL captured.
8. Write `deploy/launchagents/com.farmguardian.nextdoor-crosspost.plist` with two cal-intervals.
9. `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.farmguardian.nextdoor-crosspost.plist`.
10. Rewrite the Nextdoor skill doc's cross-post section.
11. CHANGELOG v2.37.4 entry.
12. Commit + push.

## Testing plan

- `--dry-run` opens composer, types caption, attaches photo, selects audience, takes a screenshot, **closes without submitting**. Verifies the whole flow minus submit.
- Live first-fire: `--lane throwback --headed` (throwback is safer for first live test — older content, lower stakes if it's misframed).
- Monitor: `data/nextdoor/crosspost.log`, `data/nextdoor/posts.json`, screenshots at `data/nextdoor/shots/`.
- Boss watches Nextdoor feed for the first 2–3 days; if neighbor reactions are negative or silent, dial back.

## Docs + Changelog touchpoints

- `CHANGELOG.md` — v2.37.4 entry describing two lanes, caption source, cadence, safety, LaunchAgent schedule.
- `~/bubba-workspace/skills/farm-nextdoor-engage/SKILL.md` — replace weekly-Sunday section with two-lane description.
- `farm-guardian/CLAUDE.md` — the Nextdoor bullet in the "Operational skills" list gets a one-line update about 2x/day cadence.

## Risks (logged once, then moving on)

- Nextdoor temp-restricts posting after sustained 2x/day — soft fail, challenge detector catches it and the cooldown flag kicks in globally for both lanes. If this happens we drop to 1/day (delete one cal interval).
- Neighbors mute — no API signal; detectable only via declining post reach, not actionable from automation.
- VLM drift producing bad captions — visual safety net is Boss seeing the first few live posts. Worst case the caption is bland; never illegal or embarrassing because the system prompt bans links, hashtags, names, and addresses.
