# Daily Reel Pipeline — Plan

**Date:** 29-Apr-2026  
**Author:** Claude Sonnet 4.6  
**Goal:** Replace the Sunday-only reel with a daily reel that goes through Discord approval before posting to IG.

---

## Scope

**In:**
- New `select_daily_reel_gems()` in `tools/pipeline/ig_selection.py` — 24h window, 4h buckets, min 3 gems, max 6 frames
- New `scripts/ig-daily-reel.py` — check pending approval, build today's reel, post to Discord for review, save pending state
- New `~/Library/LaunchAgents/com.farmguardian.ig-daily-reel.plist` — daily at 18:00
- Config additions in `tools/pipeline/config.json` — daily reel keys, reels.enabled true
- Retire `com.farmguardian.ig-weekly-reel` (bootout + rename plist)
- Update `docs/SOCIAL_MEDIA_MAP.md` and `CHANGELOG.md`

**Out:**
- No changes to `reel_stitcher.py` or `ig_poster.py` — they're already correct
- No new DB tables — pending state lives in flat JSON files under `data/reels/pending/`
- No changes to `discord-reaction-sync.py` — reel approval is a separate check in the daily script

---

## Flow

```
Daily at 18:00
│
├─ 1. Check data/reels/pending/*.json
│      For each pending reel:
│        • GET /channels/{channel_id}/messages/{message_id} via bot token
│        • Count human reactions (exclude BOT_USER_IDS)
│        • reactions > 0  → post to IG, move file to data/reels/posted/
│        • age > 48h, no reactions → log warning, move to data/reels/expired/
│
├─ 2. Check if today's pending file already exists → skip build if so
│
├─ 3. select_daily_reel_gems() — past 24h, discord_reactions >= 1
│      Fewer than daily_reel_min_frames → exit 0 (quiet day, no reel)
│
├─ 4. stitch_gems_to_reel() → local MP4
│
├─ 5. Build caption from best gem's VLM caption_draft
│
├─ 6. POST MP4 to Discord webhook ?wait=true → get message_id
│
└─ 7. Save data/reels/pending/YYYY-MM-DD.json
       {discord_message_id, mp4_path, gem_ids, caption, created_at}
```

---

## Key decisions

- **Approval window is 24h** — the next day's 18:00 run checks yesterday's pending reel. No action needed from Boss other than reacting in Discord.
- **Quiet-day skip** — if fewer than 3 reacted gems exist in the 24h window, no reel is built or posted to Discord. Keeps the Discord channel clean.
- **Discord post is a raw file upload** (multipart webhook), not a URL. The MP4 is local at post time. The IG post later commits the same MP4 to farm-2026.
- **Weekly reel is retired** — daily + Discord approval makes the weekly redundant.
- **No bird_count filter** (unlike weekly reel) — any reaction-gated gem qualifies. The daily window is tight enough that bird-free frames will be rare.

---

## TODOs (in order)

- [x] Plan doc written
- [ ] Add `select_daily_reel_gems()` to `ig_selection.py`
- [ ] Write `scripts/ig-daily-reel.py`
- [ ] Add daily reel config keys to `tools/pipeline/config.json`, set `reels.enabled = true`
- [ ] Create `com.farmguardian.ig-daily-reel.plist`
- [ ] `launchctl bootout` weekly reel agent, rename its plist to `.disabled`
- [ ] `launchctl bootstrap` the new daily reel agent
- [ ] Update `SOCIAL_MEDIA_MAP.md`
- [ ] Update `CHANGELOG.md`
- [ ] Smoke test: `scripts/ig-daily-reel.py --dry-run`

---

## Files touched

| File | Change |
|---|---|
| `tools/pipeline/ig_selection.py` | Add `select_daily_reel_gems()` |
| `scripts/ig-daily-reel.py` | New — daily reel entry point |
| `tools/pipeline/config.json` | Add daily reel config, enable reels |
| `~/Library/LaunchAgents/com.farmguardian.ig-daily-reel.plist` | New |
| `~/Library/LaunchAgents/com.farmguardian.ig-weekly-reel.plist` | Rename to `.disabled` |
| `docs/SOCIAL_MEDIA_MAP.md` | Update reel row cadence + approval step |
| `CHANGELOG.md` | v2.40.0 entry |
