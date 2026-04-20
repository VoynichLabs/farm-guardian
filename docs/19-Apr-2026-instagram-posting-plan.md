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

The GPU that runs the brooder-gem curation pipeline also produces the waste heat that kept these eggs at ~37.5°C through the 21-day hatch. The image-curator and the egg-warmer are the same compute. When this V2 code gets written, the predicate and the caption-picking logic should honor that — the account isn't a content-firehose, it's a journal of life-in-the-loop with AI participating as a subject, not a mechanism. Narrative richness over feed-throughput. **Do not claim the software does things it doesn't.** Farm Guardian does image curation via VLM scoring reliably; it does NOT reliably detect/deter predators. Don't frame it as a security system in public captions — Boss explicitly flagged this 2026-04-20.

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

**Rewritten 2026-04-20 (second pass) after Boss flagged the first-draft library as trash and advisor caught my second-draft padding.** History to remember:

- First draft (2026-04-19) used creator-branded tags (`#markbarney`, `#markbarneyai`, `#builtbymarkbarney`, `#builtwithai`, `#aiassistedfarm`) — all DEAD. Personal-name tags only work for people already famous. Never use these.
- Second draft (earlier 2026-04-20) included tags I hadn't verified (invented-sounding like `#yorkieterrier`, `#yorkiesmile`, `#hatchday`, `#heritagechickens`, `#coopgoals`, `#farmingforbeginners`). Advisor caught it. **Only use tags that appear in at least 2 hashtag-analytics sources (best-hashtags.com, top-hashtags.com, displaypurposes.com).**

**Strategy for a small account (@pawel_and_pawleen has 381 followers):** giant tags (>10M posts, e.g., `#dogsofinstagram`, `#farmlife` as a whole) are algorithmic dead zones — your post drowns in seconds. Real reach on this account comes from long-tail niche tags where you can actually surface on the "Recent" feed. Formula per post: **1–2 top-tier** (signal to IG's algorithm, not reach) + **3–4 mid-tier** (your actual discoverability lane) + **4–5 long-tail** (rank potential). Cap at 10 total.

**No AI/tech hashtag bucket.** Removed entirely. Tech tags don't reach the farm audience and don't reach the ML audience from a 381-follower farm account — it's dead weight either way. If a specific post warrants AI-audience reach, Boss specifies exact tags for that post himself. The tech-showcase CONTENT (chick-on-laptop etc.) stays; the tag strategy is pure community-latch.

#### Yorkies bucket — source-verified community tags

Primary community-pool tags, confirmed across 2+ analytics sources:

- #yorkiesofinstagram (top of the community, 3M+ posts)
- #yorkshireterrier
- #yorkie
- #yorkies
- #yorkielove
- #yorkielife
- #yorkiegram
- #yorkielovers
- #yorkiepuppy
- #terriersofinstagram

Dog-adjacent support tags (use sparingly — 1, not more, because they're giant):

- #puppy, #puppylove, #doglover, #dogstagram, #dogoftheday, #puppiesofinstagram, #petsofinstagram

Avoid as primary driver on this account: #dogsofinstagram (100M+, dead zone for small accounts — include only if all other slots are filled and you want a signal tag).

#### Chickens / adult flock bucket

- #chickensofinstagram
- #backyardchickens
- #backyardpoultry
- #chickens
- #hens
- #hensofinstagram
- #chickenlife
- #petchickens
- #chooks
- #freerangechickens
- #happychickens
- #chickensofig
- #chickencoop
- #crazychickenlady (community identity tag, strong engagement)

#### Baby chicks / brooder bucket

- #babychicks
- #babychick
- #babychickens
- #chicks
- #chickmath (real backyard-chicken community slang, verified)
- #raisingchickens
- #hatchingeggs

Note: do NOT add silkie-specific tags (`#silkies`, `#silkiechicken`) unless the flock actually has silkies — they don't, based on what I can see in the photos. Tag accuracy matters; community will notice.

#### Homestead / farm life bucket (add on top of content-specific tags)

- #homesteading (verified as larger than `#homestead` in search results)
- #homestead
- #farmlife
- #farming
- #homesteadlife
- #farm

#### Coop / enclosure / build bucket (sparse — only well-verified)

- #chickencoop
- #backyardchickens (overlap with flock bucket, fine)
- #homesteading

Do NOT use: `#coopgoals`, `#chickencooplife`, `#backyardbuild`, `#farmbuild`, `#homesteaddiy` (all unverified from my research).

#### Yard-diary / seasonal bucket

- #farmlife
- #homesteading
- #countrylife (verified in farm-life tag searches)

OPSEC note: don't over-localize with `#hamptonct` type tags. State-level (`#newengland`) is OK if the content is clearly regional; town-level is a predator-disclosure risk and farm identity risk.

Do NOT use: `#springonthefarm`, `#autumnonthefarm`, `#winteronthefarm`, `#homesteadseasons`, `#farmstagram`, `#seasonsonthefarm` (all unverified).

#### Orchard / garden bucket

- #gardening
- #garden
- #homegarden
- #orchard (verify this is active before use — smaller community)

Do NOT invent: `#orchardlife`, `#cherryblossom` for bloom-specific posts is OK (large established tag from cherry-blossom photography community) but don't combine with farm tags in a way that feels grafted.

### Per-post selection rule of thumb (revised for this account's size)

For a brooder post (the account's current primary content):
- 3 Chick/Brooder (mid-tier)
- 3 Chicken/Flock (mid + long-tail)
- 2 Homestead
- 1–2 content-specific (e.g., `#raisingchickens` for a feeding shot)
= 9–10 tags.

For a yorkie post:
- 5 Yorkies bucket (top + mid-tier)
- 1 Dog-adjacent support (rotate which one — puppy/doglover/etc)
- 2 Homestead/Farm (ties to the account's farm identity so the yorkie content doesn't feel disconnected from the rest)
- 1–2 content-specific
= 9–10 tags.

For a flock/coop/yard post:
- 3 Chicken/Flock
- 3 Homestead/Farm
- 2 content-specific (coop or yard)
- 2 community tags
= 10 tags.

### Rotation rule

Track last-N tags used per bucket in a small state file (`ig_tag_history.json` alongside the pipeline). Never post the same 10 tags in a row — IG's spam detection flags repeated identical tag sets. Pick new tags from the bucket's pool complement each post. Pool depth per bucket is intentionally 7–14 tags so rotation has room.

### The one thing I keep getting wrong, flagged for future me

I keep wanting to include hashtags that "sound right" but aren't actually in any community tag pool. Invented-sounding tags are AI slop in hashtag form. The rule: **if it's not in best-hashtags.com OR top-hashtags.com OR displaypurposes.com as an existing tag with a verified post count, don't use it.** Don't compose tags. Don't merge words to make a new tag. Only use what already exists.

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

## Every-6-hour posting cadence (Boss target, 2026-04-20)

Boss's cadence target is "post pretty much every 6 hours" = 4 posts/day = ~28/week. The account's historical cadence was roughly monthly; this is a 20–30× ramp. Making it land requires all of:

1. **Sufficient source material.** Three channels:
   - Farm Guardian gems (tier=strong, quality=sharp) — steady ~5–15/day from live cameras.
   - Boss's iPhone drops (`~/Desktop/iphone-today/`) — ad-hoc, highest narrative quality.
   - Scheduled tech-showcase posts — hand-prepped assets (dashboard screenshots, architecture diagrams, gear shots) stored separately.
2. **Content router** that picks the right channel per slot:
   - 06:00 — morning: yard-diary seasonal stockpile frame OR brooder early-light gem.
   - 12:00 — midday: flock/yorkies/garden iPhone content, community-hashtag-heavy.
   - 18:00 — evening: flock-at-dusk gem or coop-build iPhone drop; only occasionally tech-showcase (when the visual actually earns it).
   - 00:00 — late: quieter content (sleeping chicks, nightfall), lighter hashtags.
3. **LaunchAgent** `com.farmguardian.ig-poster.plist` — fires 4x/day at the slots above; runs `tools/pipeline/ig_poster.py --slot <name>`.
4. **Dedup window:** never post the same scene_type within 24h; never same camera_id within 12h. Tracked in `image_archive.ig_posted_at` + new `ig_slot_log` table.
5. **Buffer-low alert:** if the router can't find a gem that meets the slot's criteria (e.g., no sharp brooder gems for 6h), post a Discord alert to #farm-2026 instead of auto-posting sub-par content. Boss can then either drop iPhone photos to the pickup folder or approve skipping the slot.
6. **Manual-approval gate stays on** for the first 10 scheduled auto-posts (per earlier plan section). Boss greens/reds each via Discord reaction, 15-min timeout = skip. Flip to auto-trust after the gate proves out.

### iPhone input channels (Boss → farm)

Boss has two iPhone channels available. They're complementary, not alternatives:

**Channel A — drop folder (already in use):** Boss airdrops iPhone shots into `~/Desktop/iphone-today/` (HEIC + JPG pairs, JPG preferred). Already used for posts #2 (chick-on-laptop) and where post #3's unused candidates currently sit. Low-friction, high-signal — Boss pre-curates what lands here.

**Channel B — direct iPhone AFC via libimobiledevice + pymobiledevice3 (not wired yet, 2026-04-20):** When the iPhone is USB-connected and trusted, we can pull from DCIM directly without Boss moving anything. `libimobiledevice` is installed on this Mac Mini (2026-04-20 via brew). `ifuse` (the FUSE-mount path) is Linux-only — the macOS-compatible approach is `pymobiledevice3` (pure-Python, pip-installable, supports the `afc` multimedia-file protocol). CLI probe:

```bash
# tools available after brew install libimobiledevice:
idevice_id -l                    # list connected+trusted devices (UDID)
idevicepair pair                 # first-time pairing (Boss taps "Trust" on phone)

# Python AFC browsing (pip install pymobiledevice3 in farm-guardian venv):
pymobiledevice3 afc ls /DCIM/100APPLE/
pymobiledevice3 afc pull /DCIM/100APPLE/IMG_2156.HEIC /tmp/
```

**V2.5 work** (not in this plan's V2.0 scope):
- Install `pymobiledevice3` in the farm-guardian venv
- Add `tools/pipeline/iphone_pull.py` with:
  - `list_recent_photos(since_ts: str) -> list[dict]` — queries AFC for DCIM entries newer than last sync
  - `pull_photo(device_path: str, local_dest: Path) -> Path` — copies one photo to local disk, converts HEIC→JPG via `sips` if needed
  - `sync_since(since_ts: str, dest_dir: Path) -> list[Path]` — pull everything new into a staging area
- LaunchAgent polling every 15 min when iPhone is plugged in (detect via `idevice_id -l`)
- Pulled photos land in `~/Documents/GitHub/farm-2026/public/photos/iphone-drops/{YYYY-MM-DD}/{IMG_XXXX}.jpg`, auto-committed + pushed (reuses the same `git_helper.py` Phase 3 ships)
- Discord preview posted to #farm-2026 for each new photo asking Boss for a caption + approval

**Prerequisites for Channel B to work:**
- iPhone plugged into Mini via USB
- Phone unlocked once for initial pairing (Boss taps "Trust This Computer" when prompted)
- Pairing cert persists in `~/Library/Lockdown/` — one-time setup
- Phone doesn't need to be unlocked for subsequent photo reads once trusted

**Both channels stay alive.** The drop folder is still useful for: (a) when the phone isn't plugged in, (b) when Boss wants to curate which specific photos go to the farm-2026 pipeline rather than auto-pulling everything from DCIM. V2.5 adds Channel B; it doesn't replace Channel A.

## V3 (future — not scoped in this plan)

**Reels.** 9:16 MP4 stitched from N sharp gems in a time window. This is probably the single highest-reach format on IG in 2026 — reels get significantly more distribution than carousels. If we're posting 4x/day per Boss's cadence, at least 1–2 of those slots should be reels.

Pipeline sketch:
1. **Source selection.** Query N gems with similar scene/camera over a time window (e.g., 30-min bursts at sunrise; or every strong brooder gem from the last 24h; or yard-diary frames spanning a week). Reel quality depends heavily on source selection — bias toward scenes with *motion* between frames (chicks moving around the brooder, clouds across the sky, the PTZ camera sweeping).
2. **Frame pacing.** 15–30s reels. At 30fps that's 450–900 frames. Source 15–30 gems at 1fps stride (each gem shown for ~1s with 5–10 frame crossfade). Total compute is small — ffmpeg handles this in seconds on M4 Pro.
3. **ffmpeg compose** (new helper `tools/reel_compose.py` wrapping subprocess calls). Reuse the pattern from `capture.py:_ffmpeg_single_frame()` for the subprocess style. Key ffmpeg options: `xfade` for transitions, `scale` + `pad` for the 9:16 crop (center-crop from 16:9 source or pad with blurred background), audio track optional (silent reels work fine).
4. **Music/sfx track.** Optional V3 extension — Boss has audio samples at `~/bubba-workspace/tracks/` and can curate a library of ambient clips that fit different scenes (brooder = soft peeping; yard-diary = wind/birds; coop-build = hammer/saw). Not needed for V3.0 — ship silent first.
5. **Publish to `farm-2026/public/videos/<name>.mp4`** (new subdir), commit + push. Use `https://raw.githubusercontent.com/VoynichLabs/farm-2026/main/public/videos/<name>.mp4` for the `video_url` param.
6. **Graph API:** `POST /media` with `media_type=REELS`, `video_url=<raw URL>`, `caption=...` → returns container id. Poll `/{container}?fields=status_code` until `FINISHED` (reels take longer than photos, 10–60s). Then `POST /media_publish`.
7. **Candidate early reels:**
   - "24 hours in the brooder" — stitch 12 sharp brooder gems spanning morning → midday → evening → lights-out, 2s each, captioned with hatch day + current day number.
   - "A week in the yard" — 7 yard-diary 4K frames in sequence, 3s each. Showcases the seasonal progression (the whole point of the yard-diary stockpile per Boss's 17-Apr note).
   - "Coop build in timelapse" / "Cherry blossom progression" — passive environment reels from whatever seasonal transition is active.

### Hard rule: do not dramatize predation for content

Hawks, foxes, raccoons on camera = the flock is under attack or has been killed. These are losses, not narrative moments. Guardian exists to *deter* predators; its camera log is an operational record, not a highlight reel. **Do not frame predator detections as "Guardian catches a hawk" type content** — the reality is a dead bird, and turning that into a tension-build reel is tone-deaf to what the farm actually is. If a real loss happens and Boss wants to post about it, that's a decision he makes with his own words, in a caption he writes himself. Don't pre-bake "predator drama" templates into the pipeline.

**Stories.** Lower bar, 24h ephemeral. Good for slice-of-day shots that don't warrant a grid post — a brief brooder moment, a sunrise yard frame, a coop-build progress photo. Just `media_type=STORIES` on the existing container flow. Could enable on `share_worth=decent` instead of strong.

**@pawel_and_pawleen embed on farm.markbarney.net.** `content/instagram-posts.json` currently lists @markbarney121 only. After this pipeline lands, add @pawel_and_pawleen posts to the embed feed.
