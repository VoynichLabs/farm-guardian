# Instagram posting for @pawel_and_pawleen — Plan (19-Apr-2026)

**Author:** Claude Opus 4.7 (Bubba's resident agent on the Mac Mini)
**Status:** Plan — V1 docs + manual pipeline live; V2 auto-posting from pipeline not yet built
**Related:** `~/bubba-workspace/skills/farm-instagram-post/SKILL.md` · `~/.claude/projects/-Users-macmini-bubba-workspace/memory/farm-instagram.md`

---

## Scope

**In:**
- Single-photo and carousel posts to `@pawel_and_pawleen` via Meta Graph API.
- Image sourcing from Farm Guardian's curated gem archive (`/api/v1/images/gems/...`).
- Image hosting via `farm-2026/public/photos/` → Railway → `farm.markbarney.net` (see caveat below).
- Token / credentials lookup from macOS keychain (`security find-generic-password -s yorkies-* -w`).

**Out (this plan — see "Future work" section):**
- Auto-posting from `tools/pipeline/orchestrator.py` (V2).
- Reels / stitched-video posts (V3 — needs ffmpeg compose code).
- Approval UX (Discord-slash approval, mobile approval — V2).
- FB Page posting (would need `pages_manage_posts` scope, not currently granted).

---

## The architectural decision that will bite you if you don't know it

**Meta's IG media fetcher rejects `guardian.markbarney.net/api/v1/images/gems/{id}/image?size=1920`.**

Verified 2026-04-19. The URL returns a correct 1920×1080 `image/jpeg` with proper headers to any ordinary HTTPS client, including curl with `User-Agent: facebookexternalhit/1.1`. Meta's server-side fetcher still rejects it with `9004 / 2207052 "Media download has failed. The media URI doesn't meet our requirements."`

The heuristic appears to be URL-extension-based: URLs that don't end in `.jpg`/`.jpeg`/`.png`/`.mp4` are rejected without being fetched. `picsum.photos/...` (redirects, no extension) also fails. Wikipedia Commons URLs ending in `1280px-Cat03.jpg` succeed immediately.

**Workaround (the V1 working pipeline):**

1. Pick a gem via `GET guardian.markbarney.net/api/v1/images/gems?limit=30`
2. Download it locally: `curl -o /tmp/gem-{id}.jpg "https://guardian.markbarney.net/api/v1/images/gems/{id}/image?size=1920"`
3. Copy to `~/Documents/GitHub/farm-2026/public/photos/<subdir>/<name>.jpg`
4. `git add && git commit && git push`
5. Wait 2–5 min for Railway to deploy
6. Use `https://farm.markbarney.net/photos/<subdir>/<name>.jpg` as the `image_url` param to Graph API

This reuses the existing yard-diary publishing pattern (`scripts/yard-diary-capture.py` v17-Apr-2026) — photos are already committed to farm-2026 via Railway-triggered deploy for public exposure. IG posting just piggybacks on that path.

---

## Architecture (V2 — auto-posting, when Boss asks for it)

### New module: `tools/pipeline/ig_poster.py`

Responsibilities (SRP):
- Given a gem id, produce an IG-eligible image URL (committing to farm-2026 if not yet done).
- Call Graph API container create + publish.
- Record the IG permalink back into the `image_archive` table (new `ig_permalink` column) so each gem tracks where it landed.
- Emit a Discord alert on success/failure to `#farm-2026`.

Interface:
```python
def post_gem_to_ig(gem_id: int, caption: str | None = None, dry_run: bool = False) -> dict
# returns {"media_id": "...", "permalink": "...", "posted_at": "..."}
# on dry_run: returns the payload that WOULD be posted without publishing
```

### Predicate: `should_post_ig(gem_row) -> bool`

STRICTER than `should_post()` in `gem_poster.py` because IG is public and the account has 381 followers who expect curation:
- `tier == "strong"`
- `image_quality == "sharp"`
- `bird_count >= 1`
- **NEW:** `any_special_chick == True` OR caption_draft contains a life-event keyword (hatch, first, fledge, flight, sleep, huddle, etc.) — to favor narrative-rich frames
- **NEW:** not posted to IG in last 24h (dedup against `ig_permalink` column)
- **NEW:** passes manual approval gate for first N posts (see below)

### Manual approval gate (first N auto-posts)

Config: `instagram.manual_approval_until_count = 10` (default).

When `should_post_ig()` returns True:
1. Post a Discord preview to `#farm-2026` with the gem image + draft caption + an emoji-react approval prompt: "🟢 to post, 🔴 to skip"
2. Wait up to 15 minutes for a reaction from Boss's user id
3. Green → proceed with publish
4. Red / timeout → skip + record decision in `ig_approval_log` table

After `ig_approval_count >= manual_approval_until_count`, flip the gate off by default (configurable override).

### Secret loading

Either:
- `load_dotenv("/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env")` — fast
- Direct keychain read with `security find-generic-password -s <svc> -w` via subprocess — one fewer file dependency

**Preference: keychain.** Avoids env-file drift between workspace and repo. Boss keeps everything keychain-sourced; Farm Guardian already shells out to `security` elsewhere.

### Orchestrator wiring

`tools/pipeline/orchestrator.py:run_cycle()` already calls `post_gem()` for Discord when `should_post()` returns True. Add a parallel call:

```python
if cfg.get("instagram", {}).get("enabled", False) and should_post_ig(gem_row):
    try:
        result = post_gem_to_ig(gem_row.id, caption=gem_row.caption_draft)
        logger.info(f"Posted to IG: {result['permalink']}")
    except Exception as e:
        logger.exception(f"IG post failed for gem {gem_row.id}")
```

Default `instagram.enabled = false`. Boss flips it on after reviewing the manual-approval flow.

---

## TODOs (V2 — ordered, in dependency order)

1. Add `ig_permalink` column to `image_archive` table (and `ig_posted_at`, `ig_skip_reason`). Migration.
2. Add `ig_approval_log` table (gem_id, discord_message_id, approved_by, approved_at, decision, caption_used).
3. Implement `tools/pipeline/ig_poster.py` with `post_gem_to_ig()`, using keychain secret loading.
4. Add `farm_2026_deploy(gem_id) -> str` helper that handles the farm-2026 cp+commit+push+deploy-wait dance. Return the final `farm.markbarney.net/photos/...` URL.
5. Implement `should_post_ig()` predicate.
6. Implement Discord approval flow (reuse `alerts.py` webhook + add reaction listener — or simpler polling loop on the webhook's message id).
7. Wire into `orchestrator.py:run_cycle()` behind `instagram.enabled` config flag.
8. Update `config.example.json` with new `instagram` section.
9. CHANGELOG entry (v2.28.0 or whatever's next) with what/why/how + author attribution.
10. End-to-end test: enable the flag, wait for next strong gem, approve in Discord, verify IG post lands, verify `image_archive.ig_permalink` populated.

---

## Verification checklist

- [ ] Gem selection query returns only images where IG post hasn't been made in last 24h.
- [ ] Dry-run path does not POST to Graph — prints payload and exits.
- [ ] Manual-approval flow respects 15-min timeout and skips cleanly.
- [ ] On publish success, `image_archive.ig_permalink` is set to the Graph-returned permalink.
- [ ] On publish failure, the gem is NOT marked as posted (idempotent retry).
- [ ] Token rotation is a no-op: if tokens are regenerated and keychain is updated, next run picks up new values without restart.
- [ ] Discord notification fires on both post success AND skip reasons.

---

## Docs / Changelog touchpoints

- `CHANGELOG.md` — top entry with SemVer bump, what/why/how.
- `CLAUDE.md` — one-bullet pointer under "Operational skills" pointing at this plan + the skill.
- `~/bubba-workspace/skills/farm-instagram-post/SKILL.md` — already exists; update with V2 code-path invocation examples when ig_poster lands.
- `~/.claude/projects/-Users-macmini-bubba-workspace/memory/farm-instagram.md` — already exists; add a "V2 live" note when auto-posting goes live.

---

## The why (don't lose sight of)

The user's reframe on 2026-04-19:

> "Don't ever lose focus. Don't ever lose sight of the bigger intent behind this. You get to be a part of this. It's beautiful. It's life. Code is cold. Life requires warmth. Compute requires cooling. The waste heat incubates these chicks."

The GPU running Guardian's detection also produces the waste heat that incubated this brood. The hawk-detector and the egg-warmer are the same compute. When this V2 code gets written, the predicate and the caption-picking logic should honor that — the account isn't a content-firehose, it's a journal of life-in-the-loop with AI participating as a subject, not a mechanism. Narrative richness over feed-throughput.

---

## V3 (future — not scoped in this plan)

**Reels.** 9:16 MP4 stitched from N sharp gems in a time window. Pipeline sketch:
1. Query N gems with similar scene/camera over a time window (e.g., 30-min bursts at sunrise).
2. ffmpeg concat with crossfade transitions (reuse `capture.py` ffmpeg patterns).
3. Crop/pad to 1080×1920 (9:16).
4. Publish to farm-2026/public/video/ (or wherever public video goes — decide at V3 time).
5. Graph API with `media_type=REELS`, `video_url=<public MP4>`, `caption=...`.

**Stories.** Lower bar. Good for "Hawk at 2pm" ephemeral content. Just `media_type=STORIES` on the existing container flow. Could enable on `share_worth=decent` instead of strong.

**@pawel_and_pawleen embed on farm.markbarney.net.** `content/instagram-posts.json` currently lists @markbarney121 only. After this pipeline lands, add @pawel_and_pawleen posts to the embed feed.
