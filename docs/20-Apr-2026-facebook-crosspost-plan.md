# Facebook cross-post plan — dual-post every IG post to the Yorkies FB Page

**Date:** 20-April-2026
**Author:** Claude Opus 4.7 (1M context)
**Status:** **LIVE as of 21-Apr-2026 19:06 UTC.** First real FB post at https://www.facebook.com/122176308710784044/posts/122176308566784044 (mirrors IG `DXXpbw7k31l`). See CHANGELOG v2.35.1.

## 21-Apr-2026 update — LIVE, full access

Pipeline is live. First real FB post: https://www.facebook.com/122176308710784044/posts/122176308566784044.

**For the next assistant:** tokens are in `/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env`, non-expiring, full publish scopes granted (`pages_manage_posts` + dependencies + full IG suite). Nothing to enable. Call `fb_poster.crosspost_photo(url, caption)` and it posts. The "one blocker" section below is historical — keep reading only if something actually breaks.

---

## Goal

Every time the IG poster (`tools/pipeline/ig_poster.py`) successfully publishes to `@pawel_and_pawleen`, also publish the same content to the linked Facebook Page *Yorkies App* (`page_id=614607655061302`) via Graph API v25.0 using the page access token in keychain.

Scope is narrow on purpose — Boss: *"I don't fucking care if it dual posts."* Not doing native IG→FB "Share to Facebook" linkage (Meta's own docs call it eventually-consistent), not doing caption edits to hide that it's two posts, not building any UI gating. Dual-post, graceful degradation when the page token is missing the scope.

## The one blocker discovered during planning

`security find-generic-password -s yorkies-page-token -w` currently resolves a token with:

```
scopes: pages_show_list, pages_read_engagement,
        instagram_basic, instagram_content_publish,
        instagram_manage_{comments,insights,messages,contents}, public_profile
```

**Not granted:** `pages_manage_posts`. The memory file at `~/.claude/projects/-Users-macmini-bubba-workspace/memory/farm-instagram.md` already records this ("Not granted: pages_manage_posts … regenerate the token with that scope added"). `POST /{page_id}/photos` returns `(#200) The permission(s) pages_manage_posts are not available.` today.

Regen recipe (lives in the skill doc, cross-referenced here):

1. Graph Explorer → app = "Yorkies" → scope picker: add `pages_manage_posts` to the existing set → Generate User Access Token → consent.
2. Exchange short-lived → long-lived via `oauth/access_token?grant_type=fb_exchange_token&client_id=${APP_ID}&client_secret=${APP_SECRET}&fb_exchange_token=${SHORT_TOK}`.
3. Fetch the refreshed page token from `/me/accounts?access_token=${LONG_USER_TOK}` — it inherits the new user scopes.
4. Write back to keychain: `security add-generic-password -s yorkies-long-lived-user-token -a markb -w "${NEW_USER_TOK}" -U`, and the same for `yorkies-page-token`.
5. Re-mirror the env file: `echo "LONG_LIVED_USER_TOKEN=…" > /Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env` (same 0600 perms).

Done in ~2 minutes. No App Review; Boss is app admin + page admin + IG account owner — advanced-access path applies to self-admin content in Dev-mode apps.

## Architecture

New module `tools/pipeline/fb_poster.py` — single responsibility: publish a finished piece of content (already-hosted image URL / already-hosted MP4 URL, already-decided caption) to the FB Page. Knows nothing about gems, git, farm-2026, the IG API, or the orchestrator. Pure Graph-API-v25 wrapper.

Four public entry points, mirroring IG's lanes:

| FB function | IG counterpart | Graph API path |
|---|---|---|
| `crosspost_photo(url, caption)` | `post_gem_to_ig` | `POST /{page-id}/photos` |
| `crosspost_carousel(urls, caption)` | `post_carousel_to_ig` | 2-step: unpublished `/photos` × N then `/feed` with `attached_media` |
| `crosspost_photo_story(url)` | `post_gem_to_story` | unpublished `/photos` then `/photo_stories` |
| `crosspost_reel(video_url, caption)` | `post_reel_to_ig` | `POST /{page-id}/video_reels` (resumable-upload not needed; `file_url` variant on the reels endpoint accepts the GitHub raw URL) |

Each entry point returns a dict: `{"ok": bool, "fb_post_id": str | None, "error": str | None}`. **Never raises** unless credentials are structurally malformed — the ig_poster success path must not be poisoned by FB failures.

### Hook points in `ig_poster.py`

At the end of the successful IG publish branch in each of the four functions, after `_write_permalink` (or its story/reel equivalent), call the matching `fb_poster.crosspost_*`. Wrap in try/except to guarantee FB errors never reach the caller.

Result dict gains one field per function: `fb_post_id: str | None`. Callers ignore it today; operators can grep the pipeline log for `fb_poster:` to audit.

### Config

`tools/pipeline/config.json` → new key:

```json
"facebook": {
  "crosspost_enabled": true
}
```

Default true — Boss's stated preference. A failure-loud mode is available by setting it to false and watching logs; no UI flag needed.

Credentials: reuse the existing `_load_credentials()` path in `ig_poster.py` — page token is already exported as `PAGE_ACCESS_TOKEN` and page id as `FB_PAGE_ID` into `os.environ` via the same env file. `fb_poster.py` only needs to read those two.

## SRP/DRY check

- SRP: `fb_poster.py` is strictly the FB Graph publish layer. It doesn't know about git, hashtags, gems, farm-2026, or IG.
- DRY: reuses the same `image_url` / `video_url` / `caption` that `ig_poster.py` already built. No duplicated image prep, no duplicated git commits. Reuses `_graph_request`-style urllib wrapper but lives in its own module (a 20-line helper duplication beats importing-across-modules-with-implicit-coupling; these two publishers are peers, not parent/child).

## TODOs (in order)

1. Write `tools/pipeline/fb_poster.py` — four public functions + a private `_graph_request` + a `_load_fb_credentials` that reads from `os.environ` with a keychain fallback consistent with ig_poster.
2. Patch `ig_poster.py` — add `_maybe_crosspost_to_fb_*` helpers (one per lane) that read the config flag, call fb_poster, swallow all exceptions, write the id onto the result dict.
3. Add `facebook` block to `tools/pipeline/config.example.json` + document in `tools/pipeline/README.md`.
4. Write `~/bubba-workspace/skills/farm-facebook-crosspost/SKILL.md` — mirrors the farm-instagram-post skill but for FB. Includes the token-regen recipe in full.
5. Update `~/bubba-workspace/skills/farm-instagram-post/SKILL.md` with a pointer to the new skill.
6. Update the farm-instagram memory file with the current regen state ("gap known, code shipped, awaiting token regen").
7. CHANGELOG entry (v2.35.0 — new feature) + `CLAUDE.md` operational-skills section update.
8. Commit + push. No live test until Boss regenerates the token; the config-default keeps the dual-post attempts firing in prod, failures logged and swallowed. First post after regen IS the live test.

## Out of scope

- Editing historical IG posts to add FB counterparts.
- Cross-posting from FB → IG (reverse direction).
- Using the Accounts Center UI toggle (we dual-post explicitly; no need).
- FB-side comments, insights, or audience targeting.
- Moving captions between surfaces (captions stay identical — IG hashtags carried over to FB even though they're less useful there; accepted tradeoff for code simplicity).

## Verification plan

Post-regen, Boss triggers one carousel via the existing ig-daily-carousel LaunchAgent (or manual `scripts/ig-post.py`). Success = IG permalink + FB `post_id` in the pipeline log + the FB page shows the post at `https://www.facebook.com/profile.php?id=614607655061302`.

If FB publish fails with a non-permission error (e.g. image too large, URL rejected), the error string lands in the pipeline log verbatim — `grep 'fb_poster:' data/pipeline-logs/pipeline.log`.
