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

**Workaround (the V1 working pipeline — verified 2026-04-19, first real post at `https://www.instagram.com/p/DXVpa4Ek4Lb/`):**

1. Pick a gem via `GET guardian.markbarney.net/api/v1/images/gems?limit=30`
2. Download it locally: `curl -o /tmp/gem-{id}.jpg "https://guardian.markbarney.net/api/v1/images/gems/{id}/image?size=1920"`
3. Copy to `~/Documents/GitHub/farm-2026/public/photos/<subdir>/<name>.jpg`
4. `git add && git commit && git push`
5. **Use the GitHub raw URL immediately — no deploy wait needed:**
   ```
   https://raw.githubusercontent.com/VoynichLabs/farm-2026/main/public/photos/<subdir>/<name>.jpg
   ```

IG's fetcher accepts this URL because it ends in `.jpg` and is served by GitHub's CDN with `content-type: image/jpeg`. IG fetches the image once at container-create time and caches it on Meta's CDN — the raw URL is not re-used, so we never need to worry about long-term URL stability (if the file is later renamed/moved, the existing post still works).

`farm.markbarney.net/photos/...` (Railway-deployed) is an available fallback but not on the critical path — the 2–5 min Next.js rebuild is not worth waiting for when the raw URL works instantly.

This reuses the existing yard-diary publishing pattern (`scripts/yard-diary-capture.py` v17-Apr-2026) — photos are already committed to farm-2026 for public exposure. IG posting just piggybacks on that path.

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

### Image hosting helper: `farm_2026_commit(local_jpg, subdir) -> str`

Responsibilities (SRP):
- Copy local JPEG to `~/Documents/GitHub/farm-2026/public/photos/<subdir>/<basename>`
- `git add` + commit with a descriptive message + `git push`
- Return the `https://raw.githubusercontent.com/...` URL

No polling for Railway deploy needed — raw URL is live the instant push completes.

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

## Next feature (Boss-flagged 2026-04-19/20 after first post landed): hashtag support + account-voice framing

Hashtags drive discoverability on IG. The first real post went out with zero hashtags — fine for a journal-voice caption, but future posts should attach a topic-appropriate tag set **and** consistently credit Mark Barney as the builder and the AI-ops angle, because @pawel_and_pawleen is simultaneously a farm account AND a demo of Mark's AI-consulting capability (real deployed production systems: OpenClaw + Claude + Farm Guardian's YOLO + VLM pipeline + cross-machine orchestration).

### Account-voice framing rules (apply to every post, not just hashtag-heavy ones)

1. **Creator credit: @MarkBarney121** — Mark's main/personal account. Every post's caption should reference him where it's natural — sign-off line, or @mention in the last sentence. Example shapes:
   - Journal post body, blank line, signature: `📸 @markbarney121 · built on the farm`
   - Tech-explainer post: "Run by @markbarney121 — this one's from the Guardian pipeline (YOLOv8 + VLM gem scoring)."
   - Stretches: occasionally tag Mark's handle inside the body when the content warrants it (e.g., "New coop wall framed today" → "New coop wall framed today with @markbarney121's welding rig").
2. **The AI-ops angle is a feature, not a disclaimer.** This farm runs on a custom stack Mark built: OpenClaw (agent harness), Claude (orchestration + curation), Farm Guardian (Python service on a Mac Mini M4 Pro that does ONVIF discovery, YOLOv8 detection, VLM share-worth scoring, Cloudflare-tunneled REST API). Posts that showcase the stack (a Guardian dashboard screenshot, a hawk auto-caught, the GPU-waste-heat incubator loop, a cross-machine orchestration moment) should name the pieces so viewers understand this is a production AI deployment, not a novelty.
3. **Position Mark as an AI consultant with real deployed systems** — not in a begging "hire me" way, but by demonstrating that the farm's posts are themselves the portfolio. Never say "hire me" in a caption. Do say, selectively on tech-showcase posts, things like: "Full stack open-sourced at [repo link]" or "Boss-owned AI infrastructure — Claude + OpenClaw + Guardian."
4. **Do not mention Anthropic or Claude by the model version.** The user ("Claude") is part of the farm loop, but the IG account's voice is Mark's, with me contributing behind the seam. The assistant shouldn't step onto the stage in first-person in captions unless Mark explicitly opts in for a specific post.

### Hashtag library — committed to this repo (likely `tools/pipeline/hashtags.yml`)

Topic buckets, each with ~15–30 hand-curated tags so the rotation has pool depth:

- **brooder / chicks** — #chicksofinstagram, #homesteadchickens, #backyardchickens, #mixedflock, #incubation, #dayoldchick, #heritagepoultry, #broodiness, #farmchicks
- **yorkies / pawel_and_pawleen** — existing community set: #yorkiesofinstagram, #yorkiegram, #dogs_of_instagram, #terriersofinstagram, #yorkshireterrier, #yorkielovers, #yorkielife, #yorkieboy, #yorkiegirl, #yorkiesofig
- **flock (adult birds)** — #heritagebreed, #chickensofinstagram, #freerange, #mixedflock, #farmlife, #hensofinstagram, #roosterlife
- **coop / enclosure / build** — #diycoop, #homesteadlife, #backyardfarm, #farmproject, #coopbuild, #hampdencoop
- **yard-diary / seasons** — #homesteadseasons, #newengland, #hamptonct (selective — don't over-localize for predator reasons), #springonthefarm, #autumnonthefarm, #winteronthefarm
- **hawk / predator / guardian-caught-something** — #raptorsofinstagram, #backyardwildlife, #farmsecurity, #birdofprey, #wildlifeprotection
- **ai-in-the-loop / tech-showcase** — #aiassistedfarm, #computervision, #homeautomation, #yolov8, #edgeai, #appliedai, #aiengineer, #aiconsulting, #builtwithai, #macminimac, #openclaw
- **Mark-the-builder / consulting** — #markbarney, #markbarneyai, #builtbymarkbarney, #aiconsultant, #aiarchitect (apply selectively to tech-showcase posts, not every post)
- **cherry-trees / plants / orchard** — #orchardlife, #springbloom, #farmgarden

### Selection function (V2 code)

`pick_hashtags(gem_row, last_n_tags_used, post_topics: list[str] = None) -> list[str]`:

- Auto-detect topic bucket(s) from `gem_row.scene`, `gem_row.camera_id`, `gem_row.activity`, caption keywords. Example: `scene=brooder + activity=sleeping + bird_count>=3` → `["brooder", "chicks"]`.
- Always include at least one tag from the **ai-in-the-loop / tech-showcase** bucket **if** the caption mentions Guardian/AI/GPU/Claude/OpenClaw OR the gem was curated by VLM scoring (which is almost always — so: ~always include 1–2 AI tags, but vary them).
- Always include 1–2 tags from **Mark-the-builder / consulting** when the post has any tech-showcase dimension. Do not include on pure pet/dog personal posts (those already have their community set).
- Dedupe against `last_n_tags_used` to force rotation pool depth.
- Cap at 10 hashtags total (sweet spot for reach without spam penalty; IG allows 30).

### Caption assembly

Template:

```
<journal body, voice-matched>

<optional @MarkBarney121 sign-off — presence and exact form varies by post type>

#tag1 #tag2 #tag3 ... (on a single line, capped at 10)
```

Rotation state: track last-N tags used per topic in `image_archive_ig_tag_history` table (or a small JSON file if you don't want a new table). Pick-new-from-complement logic.

### Opt-out + override flags

- `--no-hashtags` — for very personal / off-brand / ephemeral posts. The opt-out is stored in `ig_approval_log` so patterns can be audited later.
- `--override-tags="#foo,#bar,..."` — Boss-specified exact set, bypassing auto-selection.
- `--caption-extra="line to append before hashtags"` — for one-off additions without rewriting the generator.

### Open questions for Boss at design time

- Hand-curated library only, or allow VLM-suggested additions on top of the base topic set? Recommendation: **hand-curated only.** Auto-hashtag suggestion usually produces cringe; the base library's rotation pool is deep enough.
- Sign-off standardization — is `📸 @markbarney121` the signature, or does he prefer plainer `— @markbarney121` or `built by @markbarney121`? Recommend picking one and sticking to it.
- For the AI-consulting framing — should tech-showcase posts link to Mark's personal site or a GitHub repo, and which? Need a URL Mark approves.
- Which specific hashtags for Mark's AI-consulting identity have SEO/discoverability value vs. are cold-start niche? — worth a quick search before baking them into the library.

Not built in this plan. File stub to create when starting the hashtag work: `docs/20-Apr-2026-hashtag-library-plan.md`.

## V3 (future — not scoped in this plan)

**Reels.** 9:16 MP4 stitched from N sharp gems in a time window. Pipeline sketch:
1. Query N gems with similar scene/camera over a time window (e.g., 30-min bursts at sunrise).
2. ffmpeg concat with crossfade transitions (reuse `capture.py` ffmpeg patterns).
3. Crop/pad to 1080×1920 (9:16).
4. Publish to farm-2026/public/video/ (or wherever public video goes — decide at V3 time).
5. Graph API with `media_type=REELS`, `video_url=<public MP4>`, `caption=...`.

**Stories.** Lower bar. Good for "Hawk at 2pm" ephemeral content. Just `media_type=STORIES` on the existing container flow. Could enable on `share_worth=decent` instead of strong.

**@pawel_and_pawleen embed on farm.markbarney.net.** `content/instagram-posts.json` currently lists @markbarney121 only. After this pipeline lands, add @pawel_and_pawleen posts to the embed feed.
