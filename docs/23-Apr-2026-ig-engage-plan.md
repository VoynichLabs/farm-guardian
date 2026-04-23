# 23-Apr-2026 — IG engagement automation plan

## Scope

Build an automation that logs into Instagram as `@pawel_and_pawleen` (the farm's only social handle — there is no separate "farm" vs "dogs" account) and engages with other people's content on Boss's behalf: scroll home feed + targeted hashtag feeds, like a selective subset, react to friends' stories with emoji, occasionally leave a short contextual comment on a post. Purpose: build audience reciprocity so posts from `@pawel_and_pawleen` reach more eyes, without Boss spending time scrolling.

**In scope:** session bootstrap, engagement primitives (scroll, like, story-react, comment), comment content via local VLM, challenge detection, kill switch, daily/session caps, LaunchAgent scheduling, cross-repo documentation.

**Out of scope:** follow/unfollow churn (forbidden — #1 bot signal). DMs (forbidden — #2 bot signal). Buying followers. Any form of paid growth. Engagement on OTHER accounts owned by Boss (only `@pawel_and_pawleen`). The POSTING pipeline is unrelated — it's already live via `tools/pipeline/ig_poster.py` and the Graph API.

## Architecture

**Zero-login session bootstrap.** Boss is already logged into IG in Chrome on this Mac. Rather than having him re-enter credentials + 2FA into a fresh browser, we read his IG session cookies directly from Chrome's SQLite cookie DB, decrypt them with the "Chrome Safe Storage" macOS keychain key, and seed them into a dedicated Playwright Chromium persistent profile on the same machine. Same IP, same device fingerprint (roughly), no cross-device session-hijack signal to Meta. Then the Playwright profile persists that session indefinitely — headed or headless — with no further Boss interaction unless Meta challenges us.

**Why not other paths:**
- `document.cookie` in Chrome DevTools → Meta's self-XSS block rejects it visually; Boss can't see the cookie values. Verified failed 2026-04-23.
- Fresh login into Playwright Chromium → Boss has no memorized password (LastPass, no CLI), and 2FA makes it annoying.
- Cross-device cookie lift → higher fingerprint divergence, triggers Meta's "new login" security-alert flow.
- Chrome channel with Boss's real profile dir → fights Chrome's profile lock while Chrome is running.
- CDP-attach to Boss's running Chrome → requires Chrome to be relaunched with `--remote-debugging-port=9222` every time. Fine as a fallback, bad as the default.

The cookie-lift path worked on first attempt (verified 2026-04-23): 12 IG cookies decrypted cleanly, Playwright landed on `instagram.com/` feed (not `/accounts/login`).

**Modules (live under `tools/ig-engage/`):**

- `bootstrap.py` (DONE, 2026-04-23) — reads Chrome cookies, decrypts, seeds into Playwright profile dir at `~/Library/Application Support/farm-ig-engage/profile/`, verifies feed loads, writes `bootstrap-ok.json` marker.
- `engage.py` (in progress) — main session runner. Launches Playwright at the persistent profile, runs engagement primitives within budget, checks kill switch + challenge flag before every action, writes activity log.
- `primitives.py` (in progress) — `scroll_home_feed()`, `like_post()`, `react_to_stories()`, `visit_hashtag()`, `post_comment()`. Each primitive is idempotent-ish and defensive (every interaction wrapped in try/except + challenge check).
- `comment_writer.py` (in progress) — sends post image + caption to local Qwen3.6 VLM on LM Studio `localhost:1234`, requests a short warm context-aware comment. Falls back to curated template library if the VLM refuses.
- `budget.py` — tracks likes/comments/stories used in the current UTC day; enforces caps (30 likes + 10 comments + 20 story reactions per day). Persists counters to JSON under `data/ig-engage/`.
- `challenge.py` — scans the open page for known Meta challenge strings; on detection, screenshots + posts to Discord + writes `/tmp/ig-engage-cooldown-until` + exits.

**Deploy:** `deploy/ig-engage/com.farmguardian.ig-engage.plist` — LaunchAgent firing 3x/day (09:00, 13:00, 19:00 local) with `RunAtLoad=false`, `ThrottleInterval=60`. Log redirect to `/tmp/ig-engage.out.log` + `/tmp/ig-engage.err.log`.

## TODOs (ordered)

1. [x] Bootstrap — persistent Playwright profile seeded from Chrome cookies (done 2026-04-23).
2. [ ] VLM probe — confirm Qwen3.6 will write IG-style comments; if refused, build template library.
3. [ ] Engagement primitives, attended (headed, like-only, no comments) — run once in front of Boss to tune selectors and timing.
4. [ ] Challenge detector — integrate, test against a contrived "fake dialog" page.
5. [ ] Budget + kill switch — integrate.
6. [ ] Attended dry run — 10 minutes, headed, likes-only, Boss watches.
7. [ ] Enable comments (still attended, headed).
8. [ ] LaunchAgent — headless, 3x/day, for 2 days unattended.
9. [ ] Review — check Discord for challenge alerts, eyeball the activity log.

## Safety choices

- **Never follow/unfollow, never DM.** Hard constraint in the code — no primitive exists.
- **Volume cap:** 30 likes + 10 comments + 20 story reactions per UTC day, total across all sessions.
- **Session length:** cap at 5 min wall-clock with a 15–25 action cap per session.
- **Timing:** non-uniform randomization (6–18s per action, with 1-in-8 actions having a 30–90s pause to mimic phone distraction).
- **Cadence:** 2–3 sessions/day at plausible human times (morning coffee, lunch, evening).
- **Kill switch:** `touch /tmp/ig-engage-off` halts everything.
- **Challenge cooldown:** 24h minimum after any Meta challenge-dialog detection.
- **Stealth patches:** `navigator.webdriver`, `navigator.plugins`, `navigator.languages`, `chrome.runtime`, `permissions.query`. User-agent matches real desktop Chrome on Mac. Locale `en-US`, timezone `America/New_York`.
- **Comment content:** VLM-written per-post contextual comments, never a static pool. Generic "nice post" strings are forbidden — they are the exact bot signature Meta hunts.

## Docs/CHANGELOG touchpoints

- `CHANGELOG.md` — v2.36.8 entry describing the bootstrap + plan, pointing at this file.
- `~/bubba-workspace/skills/farm-instagram-engage/SKILL.md` — cross-agent skill doc covering the bootstrap path, credential inventory, safety choices, runbook.
- `farm-guardian/CLAUDE.md` — add a line pointing to this plan + the skill doc under the social-ops section.
- `farm-2026/docs/` — mirror note so the website agent knows engagement is happening (no frontend impact, but the agents that work on both repos should know this exists).

## Out of scope / explicitly NOT doing

- Growing follower count through outbound follow.
- Any paid promotion integration.
- Engagement from OTHER social accounts (Facebook page engagement is a separate, decommissioned project — see `tools/on_this_day/reciprocate.py`, currently disabled at `~/Library/LaunchAgents/com.farmguardian.reciprocate.plist.disabled`).
- Replying to comments on `@pawel_and_pawleen`'s OWN posts — that's a separate tool (`reciprocate`) that can use the Graph API officially; engagement on OTHER people's content is what this engager covers.
