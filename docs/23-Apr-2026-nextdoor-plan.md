# 23-Apr-2026 — Nextdoor automation plan

## Scope

Extend the "zero-login cookie-lift from Chrome → dedicated Playwright Chromium persistent profile" pattern (built 2026-04-23 for Instagram) to Nextdoor. Boss logs into nextdoor.com with his Apple ID in Chrome on this Mac Mini; we read those session cookies, decrypt with the Chrome Safe Storage key, seed into a new Playwright profile at `~/Library/Application Support/farm-nextdoor/profile/`.

Nextdoor is a different shape from Instagram — it's a local-neighborhood platform, posts are seen by the user's geographic area, and there is no public posting API. So this is a web-UI automation for BOTH directions:

**Outbound (cross-post farm content TO Nextdoor):** when a strong+sharp gem is posted to IG/FB, also post it to the Hampton CT neighborhood feed with the same caption, on a slower cadence (Nextdoor audiences are more noise-sensitive than IG — weekly rhythm, not daily).

**Inbound (engage with neighbors ON Nextdoor):** browse the neighborhood feed, like / react to neighbor posts (especially pet / yard / garden / weather content), leave warm contextual comments on ones that match Boss's actual interests, build reciprocity with the local audience who'd actually care about the flock. Same safety rails as IG — no follow-churn, no DMs, small daily caps.

**Not in scope:** buying recommendations, the marketplace, real-estate feature, event-hosting, the "classifieds" sub-surface. Farm-adjacent content only.

## Status

**NOT BUILT YET.** This doc is the plan. Verification steps done 2026-04-23:

- Cookie inventory: 21 nextdoor cookies in Chrome, including `ndbr_at`, `ndbr_idt` (an 820-char JWT — the real session token), `csrftoken`, `DAID`. Decrypted cleanly with the IG bootstrap's exact decrypt path (same Chrome Safe Storage key, same PBKDF2 constants, same 32-byte SHA256 host-hash prefix strip). **No new crypto work needed — the bootstrap is literally copy-paste with a different host filter and profile dir.**
- Chrome already logged in (confirmed with Boss).

## Architecture

**Reuse the IG bootstrap verbatim.** The only change is:

```python
# tools/ig-engage/bootstrap.py:
WHERE host_key LIKE '%instagram%'
PROFILE_DIR = ~/Library/Application Support/farm-ig-engage/profile
landing = instagram.com/

# tools/nextdoor/bootstrap.py (NEW — near-clone):
WHERE host_key LIKE '%nextdoor%'
PROFILE_DIR = ~/Library/Application Support/farm-nextdoor/profile
landing = nextdoor.com/news_feed  (or whatever the feed URL is post-login)
```

Everything else — keychain password fetch, key derivation, v10/v11 decryption, host-hash prefix strip, Playwright persistent_context launch, stealth patches — is identical. **Plan is to generalize `decrypt_cookie` + `get_chrome_safe_storage_password` into a shared module (`tools/chrome_session/` or similar)** rather than copying code across projects. That shared module becomes the foundation for every future browser-automation track Boss wants.

**Modules under `tools/nextdoor/`:**

- `bootstrap.py` — near-clone of IG bootstrap.
- `budget.py` — per-UTC-day caps (smaller than IG because Nextdoor cadence is slower). Proposed: 10 likes + 3 comments + 1 post-per-week.
- `challenge.py` — scan for Nextdoor's version of challenge / action-block dialogs. Strings TBD; inspect the first session and populate.
- `comment_writer.py` — can reuse IG's Qwen3.6 VLM path, but with a NEIGHBORHOOD voice (less lowercase-casual-emoji, more "hi neighbor!" warmth). Different system prompt.
- `primitives.py` — scroll_feed, find_posts, like, comment, create_post (new — the post lane).
- `engage.py` — engagement session runner.
- `crosspost.py` — new lane: called from orchestrator after an IG post lands strong+sharp, posts same photo + caption to Nextdoor on a weekly gate.

**Shared module (coming in a follow-up refactor):**

- `tools/chrome_session/decrypt.py` — `get_chrome_safe_storage_password()`, `derive_key()`, `decrypt_cookie()`, `read_cookies_for_hosts(patterns: list[str])`. Both IG and Nextdoor import this; no duplicate crypto code.

## Voice differences (Nextdoor vs Instagram)

The comment VLM prompt for Nextdoor needs different voice rules:

- Nextdoor comments are typically longer (1-3 sentences, not 1-8 words).
- Mixed-case, proper punctuation, no lowercase aesthetic.
- Zero emoji, or at most one very tame one (❤️, ☀️, 🐣 ok; 😍 😂 🔥 no).
- Warmer / "good neighbor" register: "What a lovely little spring morning" > "those feet!"
- Post-forward questions ("Do you have a favorite coop design?") still work — same reciprocity mechanic as IG.
- NEVER commercial, NEVER link to external sites, NEVER mention this is cross-posted.
- Farm content captions on Nextdoor should add a grounding line: "Hampton CT backyard flock" or similar — neighbors care about local.

## Posting lane (NEW — not in the IG engager)

Nextdoor has no Graph-API equivalent. Posts are created through the web UI. The automation:

1. Detect that we have a new, strong+sharp, adult-appropriate gem from the last 7 days that hasn't been cross-posted to Nextdoor.
2. Launch Playwright at the Nextdoor profile.
3. Click "Create Post" (selector TBD, inspect during first attended session).
4. Attach the photo.
5. Fill in a short caption — first line a neighborhood-friendly framing, then a sentence or two describing the specific bird/dog/scene.
6. Select audience: "Just my neighborhood" (default — not city-wide, not farther).
7. Submit.
8. Log the post URL + timestamp to `data/nextdoor/posts.json` so we don't re-post.

**Cadence:** once per week, weekend mornings (Sunday 09:00-ish). The Nextdoor audience is saturation-sensitive; too-frequent posting kills reciprocity.

## Cross-post integration (from the pipeline)

The existing `tools/pipeline/orchestrator.py` already has `_maybe_post_to_ig()` + `_maybe_post_to_fb()`. Add `_maybe_post_to_nextdoor()` that gates on:
- has been 7+ days since the last Nextdoor post, AND
- current gem is tier=strong, image_quality=sharp, AND
- current hour is within a "plausible Sunday morning" window (08:00-11:00 local on Sunday).

Or simpler: skip the orchestrator hook entirely and make Nextdoor cross-post a standalone LaunchAgent that fires Sunday morning, picks the best reacted gem from the past week via the existing `image_archive.discord_reactions` signal (same as the IG weekly reel), and posts it.

**Leaning toward option 2** (standalone LaunchAgent) because:
- Decouples Nextdoor from the per-gem pipeline timing.
- Weekly-cadence flow is clearer when it lives in its own runner.
- Reuses the Discord-reaction quality gate that we already trust for IG weekly reels.

## Safety rails (same pattern as IG, tuned for Nextdoor)

- **No follow/unfollow** (Nextdoor has no exact follow, but has neighbor requests — don't send them automatically).
- **No DMs.**
- **Daily caps:** 10 likes + 3 comments per UTC day. Weekly: 1 post.
- **Session length:** <3 min (shorter than IG — Nextdoor pages are slower-loading and more text-heavy, we don't need long sessions).
- **Kill switch:** `touch /tmp/nextdoor-off`.
- **Challenge cooldown:** 24h, same as IG. Strings TBD after first attended session.
- **Audience floor:** all posts default to "just my neighborhood" — never broader, never "nearby neighborhoods."
- **No identifying details in automated content:** the `feedback_no_editorializing.md` memory applies — never put Boss's name in a Nextdoor post, never reveal exact address.

## Ordered TODOs

1. [ ] Extract shared Chrome-cookie decrypt into `tools/chrome_session/decrypt.py`; update IG bootstrap to import from there.
2. [ ] `tools/nextdoor/bootstrap.py` — clone IG bootstrap, verify lands on feed.
3. [ ] First attended session with Boss — navigate the Nextdoor UI by hand, record selectors for post-create / like / comment / audience-picker.
4. [ ] `primitives.py` — wire the selectors.
5. [ ] `challenge.py` — scan the first session's real page text for any Nextdoor-specific "action blocked" / "verify" strings, seed the detector list.
6. [ ] `comment_writer.py` — VLM prompt tuned for neighborhood voice.
7. [ ] `engage.py` — same structure as IG's engage.py, different caps.
8. [ ] `crosspost.py` — posting lane, weekly, reaction-gated gem selection.
9. [ ] LaunchAgents: one for engagement (daily-ish), one for cross-post (Sunday mornings).
10. [ ] Attended headed + likes-only dry run.
11. [ ] Enable comments, still attended.
12. [ ] Enable posting with a manual first post Boss watches end-to-end.
13. [ ] Go unattended.

## Docs touchpoints (when this ships)

- `farm-guardian/CHANGELOG.md` — new minor version (likely v2.37.0 since this is a whole new surface).
- `~/bubba-workspace/skills/farm-nextdoor-engage/SKILL.md` — canonical cross-agent reference, same structure as `farm-instagram-engage/SKILL.md`.
- `farm-2026/CLAUDE.md` — heads-up that Nextdoor cross-posting exists, same no-frontend-impact pattern as the IG engagement note.
- `farm-2026/docs/23-Apr-2026-nextdoor-announce.md` — the actual heads-up doc.
- `swarm-coordination/events/2026/apr/` — cross-agent announcement so Larry / Egon / any other swarm Claude knows.
- `farm-guardian/CLAUDE.md` — pointer under social-ops.

## Known open questions

- Does Nextdoor's "neighborhood feed" URL have a stable path once logged in? (`/news_feed`? `/feed`? Need first attended session to confirm.)
- Is there a "posts from my neighborhood only" filter we can pin in the URL? (Reduces the scroll surface the engager has to reason about.)
- What's the post-attachment flow? (Drag-drop? File picker? Paste image?)
- Does Nextdoor show an "action temporarily blocked" dialog like IG does, or does it silently rate-limit without telling us? (Attended session will surface this.)

## What I need from Boss before building

Nothing blocking — the bootstrap will just work. But when it's time for the first attended session (TODO step 3), Boss should be at the Mini so we can step through the post-create UI together and record the right selectors. That's a 10-minute thing.
