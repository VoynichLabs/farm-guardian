# How It All Fits — Photos, Metadata, and the Instagram Pipeline

**Last updated:** 2026-05-04
**Audience:** a human or an AI agent who is new to this project and needs the 10,000-ft view of where the farm's photographs live, how they get tagged, and how they end up on Instagram.
**Companion docs:** [`19-Apr-2026-instagram-posting-plan.md`](./19-Apr-2026-instagram-posting-plan.md) · [`20-Apr-2026-ig-poster-implementation-plan.md`](./20-Apr-2026-ig-poster-implementation-plan.md) · [`20-Apr-2026-instagram-manager-agent-plan.md`](./20-Apr-2026-instagram-manager-agent-plan.md)

---

## The one-paragraph version

A handful of cameras — a PTZ in the yard, two phones over the brooder, a laptop cam in the coop run, and two more laptop-hosted USB cams — shoot frames every 2–600 seconds. Each frame passes a three-stage quality gate (std-dev, exposure, motion), then the frames that survive get run through a local VLM (LM Studio, Gemma-4-31b) which returns rich JSON metadata: scene type, bird count, activity, image quality, share-worthiness, concerns flag, and a caption draft. Everything lands in `data/guardian.db` → `image_archive` with the JPEG in `data/archive/YYYY-MM/<camera>/*.jpg`. Feed/carousel/reel publishers use the farm-2026 public media path; reacted Story publishing writes prepared 9:16 JPEGs to `data/story-assets/` and serves them through Guardian at extension URLs Meta accepts. All tokens live in macOS keychain and never expire.

---

## Where every photo on this Mac Mini lives

### 1. Live pipeline — THE primary pool (this is what auto-posting draws from)

**Database:** `~/Documents/GitHub/farm-guardian/data/guardian.db`
**Table:** `image_archive`
**Files on disk:** `~/Documents/GitHub/farm-guardian/data/archive/YYYY-MM/<camera>/YYYY-MM-DDTHH-MM-SS-<tier>.jpg`

Every surviving frame has a row with ~30 columns of VLM metadata. Schema lives in [`tools/pipeline/store.py`](../tools/pipeline/store.py) (`_SCHEMA_SQL`). Key columns:

| column | meaning |
|---|---|
| `camera_id` | which camera (`s7-cam`, `usb-cam`, `iphone-cam`, `house-yard`, `gwtc`, `mba-cam`) |
| `ts` | capture timestamp (UTC ISO-8601) |
| `image_path` | JPEG path relative to repo root |
| `image_tier` | `strong` / `decent` / `skip` |
| `scene` | `brooder` / `yard` / `coop` / etc. |
| `bird_count` | integer |
| `activity` | e.g. "three chicks huddled at heat lamp" |
| `image_quality` | `sharp` / `decent` / `soft` |
| `share_worth` | `strong` / `decent` / `weak` |
| `has_concerns` | privacy flag (house placards, faces, etc.) |
| `individuals_visible_csv` | named birds (when identifiable) |
| `vlm_json` | full raw VLM response including `caption_draft` |
| `ig_permalink`, `ig_posted_at`, `ig_skip_reason` | Instagram posting state (added 2026-04-20) |

**Query the archive directly:**

```bash
sqlite3 ~/Documents/GitHub/farm-guardian/data/guardian.db "
  SELECT image_tier, COUNT(*) FROM image_archive GROUP BY image_tier;
"
```

As of 2026-04-20 this table had 6,952 rows across 7 days, with 205 `strong`-tier gems. The count grows by roughly 1,000 rows per day depending on how many cameras are active.

### 2. Curated photo trees on GitHub (already public)

**Repo:** [`farm-2026`](https://github.com/VoynichLabs/farm-2026) — the public-facing farm.markbarney.net website.

| Path | What's there |
|---|---|
| `public/photos/brooder/` | Feed photos the IG pipeline has posted through the farm-2026 public media path. |
| `public/photos/yard-diary/` | Thrice-daily yard wide-shots (`YYYY-MM-DD-{morning,noon,evening}.jpg`), captured by `scripts/yard-diary-capture.py` via its own launchd plist. Independent of the VLM pipeline — it's a time-lapse record. |
| `public/photos/coop/` | Build-documentation photos (enclosure install, hardware drops). Hand-curated. |
| `public/photos/iphone-drops/YYYY-MM-DD/` | (Planned, not yet auto-populated) AirDropped iPhone shots that feed captions-on-tap posting. See `19-Apr-2026-instagram-posting-plan.md` §iPhone channel. |

Everything under `public/` is served by Next.js on Railway at `https://farm.markbarney.net/photos/...` and by GitHub raw URLs. Feed/carousel/reel lanes still use this path where needed. Reacted Story assets do not: they stay on the Mac Mini under `data/story-assets/` and are served by Guardian.

### 3. Historical / analysis archives (NOT yet wired into auto-posting)

These pools contain tagged photos from prior manual work. They're candidates for a future "archive replay" mode that would let the IG pipeline post retrospectives. None are currently read by `tools/pipeline/ig_poster.py`.

| Location | Count | Tagged? | Notes |
|---|---|---|---|
| `~/Documents/GitHub/swarm-coordination/chick-photos/` | 6 | Markdown only | 2026-03-25 sex-calling portraits. Analyses at `chicks_larry_complete/bird_N_analysis.md`, written by a Claude sub-agent ("Larry") against the Cream Legbar / Silver Laced Wyandotte / heritage-mixed flock. Breed + sex calls with confidence. Not JSON-structured — a future replay mode would need to normalize these into the same shape as `vlm_json`. |
| `~/bubba-workspace/projects/farm-vision/farm-vision-dev-test.json` | 1 (+ room to grow) | JSON | 2026-03-17 LM Studio run (qwen3.5-35b) against `backyard.png` — full metadata (scene, colors, subjects, aesthetic tags, design usability). The schema is richer than `image_archive.vlm_json` because it was designed for website-background selection, not IG selection. Proof-of-concept for the entire VLM-tagging idea that later became Farm Guardian's pipeline. |
| `~/Desktop/iphone-today/` | variable | no | AirDropped recent iPhone shots — HEIC + JPG pairs. Boss drops photos here when he wants one posted; the "Channel B" pickup in `19-Apr-2026-instagram-posting-plan.md` will eventually watch this folder. |
| `~/Pictures/` (Photos Library) | 4,225 items, 23 GB | no | macOS Photos library. **Not** a pipeline source — personal photos, no metadata normalization. Left alone deliberately. |

---

## The one gotcha that shapes image-hosting paths

Instagram's media fetcher rejects any `image_url` that does not end in `.jpg`, `.jpeg`, `.png`, or `.mp4`. This is not documented; it was discovered the hard way on 2026-04-19. Consequences:

- `https://guardian.markbarney.net/api/v1/images/gems/{id}/image?size=1920` → rejected (9004 / 2207052 "Media download has failed"), even though the response body is valid `image/jpeg`.
- `https://guardian.markbarney.net/api/v1/images/story-assets/2026-05-04-gem123-story.jpg` → works because the URL ends in `.jpg` and is served directly from the Mac Mini through Cloudflare Tunnel.
- `https://farm.markbarney.net/photos/brooder/pic.jpg` → works, but requires a 2–5 min wait for Railway to rebuild after the git push.
- `https://raw.githubusercontent.com/VoynichLabs/farm-2026/main/public/photos/brooder/pic.jpg` → works immediately, live the instant the push lands.

So reacted Stories now use `data/story-assets/` plus the Guardian route `/api/v1/images/story-assets/<name>.jpg`. Feed/carousel/reel media that still needs farm-2026 hosting uses [`tools/pipeline/git_helper.py`](../tools/pipeline/git_helper.py) with `GIT_TERMINAL_PROMPT=0` + `GIT_ASKPASS=/bin/echo` so the osxkeychain helper handles auth non-interactively inside a Python subprocess.

IG caches the image on Meta's CDN after ingestion. The source URL is only read during container creation, so deleting or renaming the source file later doesn't break an already-created post, but new Story container creation needs the local asset to return HTTP 200 at that moment.

---

## The pipeline, cycle by cycle

```
┌──────────────┐
│  6 cameras   │   HTTP snapshot / RTSP every N seconds
└──────┬───────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  tools/pipeline/orchestrator.py         │
│  run_cycle()                            │
│  ├── capture_camera (jpeg bytes)        │
│  ├── trivial gate  (std-dev)            │
│  ├── exposure gate (p50 bounds)         │
│  ├── motion gate   (per-camera opt-in)  │
│  ├── enrich (LM Studio, Gemma-4-31b)    │
│  ├── store (SQLite + disk)              │
│  ├── post_gem to Discord (opt-in)       │
│  └── _maybe_post_to_ig    ←── NEW       │
└──────┬──────────────────────────────────┘
       │  cfg["instagram"]["enabled"]=true
       ▼
┌──────────────────────────────────────────┐
│  should_post_ig() predicate              │
│  - tier == "strong"                      │
│  - image_quality == "sharp"              │
│  - bird_count >= 1                       │
│  - has_concerns == false                 │
│  - min_hours_between_posts cooldown OK   │
│  - min_hours_per_camera cooldown OK      │
└──────┬───────────────────────────────────┘
       │  ok
       ▼
┌────────────────────────────────────────┐
│  pick_hashtags() + build_caption()     │
│  - scene → buckets (brooder → chicks+  │
│            chickens+homestead)         │
│  - hashtags.yml:forbidden rejected     │
│  - 5 long-tail + 4 mid + 2 top         │
│  - layout: journal + 📸@mark + tags    │
└──────┬─────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────┐
│  post_gem_to_ig()                    │
│  1. commit_image_to_farm_2026 (push) │
│  2. POST /media (create container)   │
│  3. poll until FINISHED              │
│  4. POST /media_publish              │
│  5. GET /{media_id}?fields=permalink │
│  6. UPDATE image_archive.ig_permalink│
└──────┬───────────────────────────────┘
       │
       ▼
  @pawel_and_pawleen
```

Both the Discord post and the IG post are wrapped in try/except so any failure (Graph API rate-limit, git push retry, VLM hiccup) just logs and the cycle rolls on — one bad post never takes the daemon down.

---

## Where the secrets actually live (reference only — no secrets committed)

**macOS keychain** on this Mac Mini. Each entry is read with `security find-generic-password -s <service> -w`. Never committed to any repo.

| Service | Purpose |
|---|---|
| `meta-app-id` | FB App ID (`613565154985119`) — the Yorkies dev-portal app |
| `meta-app-secret` | App secret (used only for token refresh, never in requests) |
| `yorkies-long-lived-user-token` | Never-expires user token (`expires_at: 0`) |
| `yorkies-page-token` | Never-expires Page token |
| `yorkies-page-id` | `614607655061302` (Yorkies App page) |
| `yorkies-ig-business-account-id` | `17841460199126266` |
| `yorkies-ig-username` | `pawel_and_pawleen` |

**Env-file mirror** at `/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env` (0600, not in any repo). Pipeline sources it via `_load_configs()` so `os.environ` has Graph API creds without launchd plist changes.

**Discord webhook** for gem posts — lives in the farm-guardian repo's `.env` (gitignored).

**Discord bot token** for `tools/discord_harvester.py` — read from OpenClaw's config at runtime.

None of these credentials appear in any committed file. The gitignore at `~/Documents/GitHub/farm-guardian/.gitignore` includes `.env`, `tools/pipeline/config.json`, and everything under `data/` for exactly this reason.

---

## Enabling / disabling auto-posting

It's controlled by two flags in `tools/pipeline/config.json`:

```json
"instagram": {
  "enabled": true,          // master switch
  "auto_dry_run": false,    // preview-only mode
  ...
}
```

- `enabled: false` → hook no-ops silently. Daemon doesn't know IG exists.
- `enabled: true, auto_dry_run: true` → predicate runs, caption builds, "would have posted gem_id=N" log lines fire, but no Graph API call. Skip reasons get persisted to `ig_skip_reason` for audit.
- `enabled: true, auto_dry_run: false` → real posts.

Flipping either flag requires a daemon restart to take effect:

```bash
launchctl kickstart -k gui/$(id -u)/com.farmguardian.pipeline
```

**Current state as of 2026-04-20:** `enabled: true, auto_dry_run: false`. Live auto-posting.

---

## The cadence knobs

```json
"min_hours_between_posts": 6,   // IG-wide rate limit
"min_hours_per_camera": 12      // per-scene dedup
```

`6` hours between posts gives roughly 4 posts per 24h when content is available — matches the cadence Boss asked for on 2026-04-20. `12` hours per camera means the brooder doesn't take over the whole feed; the yard and coop get a shot at the pool.

These can be tuned in `config.json` without a code change — restart picks them up.

---

## Manually posting (the CLI escape hatch)

```bash
cd ~/Documents/GitHub/farm-guardian
python3 scripts/ig-post.py --gem-id 6849 --caption "$(cat caption.txt)" --dry-run   # preview
python3 scripts/ig-post.py --gem-id 6849 --caption "$(cat caption.txt)"              # ship
```

This is useful for:
- Posting a gem the auto-predicate rejected (Boss has a particular shot in mind).
- Posting hand-written captions with no VLM draft involvement.
- Posting anything from the iPhone drop folder once that pickup lands.
- Debugging a failing auto-post without waiting for the next capture cycle.

Works whether the flags are on or off.

---

## Things that are intentionally NOT in scope yet

- **Reels.** Plan exists in `19-Apr-2026-instagram-posting-plan.md` §V3 — stitch 5–10 brooder shots with ffmpeg into a 9:16 MP4, add a slow pan/zoom, post as `media_type=REELS`. No code.
- **Stories.** Same API, different endpoint (`media_type=STORIES`). Trivial to add once someone decides the cadence.
- **Historical archive replay.** The swarm-coordination chick-photos + farm-vision dev-test pools could feed a "this week two years ago" retrospective mode. Would need a schema-normalizer and a curation flag.
- **IG manager agent.** Reading + replying to comments, DMs, curated like/follow queues. See `20-Apr-2026-instagram-manager-agent-plan.md` for the full scoping — Meta's official API restricts this heavily; there's a trade-off call to make.
- **Hashtag rotation state.** `pick_hashtags(last_n_tags_used=[])` currently has no memory. Once auto-posting has been live for a week, the audit trail will show repetition patterns worth avoiding. Cheap to add; waiting on data.

---

## One-liners for human inspection

```bash
# How many strong gems are waiting to be picked?
sqlite3 ~/Documents/GitHub/farm-guardian/data/guardian.db \
  "SELECT COUNT(*) FROM image_archive WHERE image_tier='strong' AND image_quality='sharp' AND ig_permalink IS NULL AND ig_skip_reason IS NULL;"

# What did the auto-poster skip and why?
sqlite3 ~/Documents/GitHub/farm-guardian/data/guardian.db \
  "SELECT ig_skip_reason, COUNT(*) FROM image_archive WHERE ig_skip_reason IS NOT NULL GROUP BY ig_skip_reason ORDER BY 2 DESC;"

# What's live on Instagram from the pipeline?
sqlite3 ~/Documents/GitHub/farm-guardian/data/guardian.db \
  "SELECT ig_posted_at, camera_id, ig_permalink FROM image_archive WHERE ig_permalink IS NOT NULL ORDER BY ig_posted_at DESC;"

# Is the daemon alive?
launchctl list | grep farmguardian.pipeline

# Tail the pipeline log
tail -f /tmp/pipeline.err.log
```
