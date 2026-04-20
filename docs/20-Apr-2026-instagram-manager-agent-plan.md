# Always-on Instagram Manager Agent for @pawel_and_pawleen — Plan (20-Apr-2026)

**Author:** Claude Opus 4.7 (Bubba, Mac Mini resident agent)
**Status:** Plan — nothing built yet. Companion scope to the 19-Apr posting plan.
**Related:**
- `19-Apr-2026-instagram-posting-plan.md` — V2 auto-posting (the outbound-content pipeline)
- `~/bubba-workspace/skills/farm-instagram-post/SKILL.md` — the manual runbook
- `~/.claude/projects/-Users-macmini-bubba-workspace/memory/farm-instagram.md` — creds + account context

---

## What Boss actually asked for (20-Apr-2026, verbatim intent)

> "This is about updating my friends and family about all the cute animals and happy stuff here on the farm. I am not monetizing any of this. But I do want to expand the reach. I need an agent kind of managing my Instagram and reacting to comments and also most importantly, following and liking other people's posts. I'd like something that runs on the Mac mini all the time basically that is my Instagram manager."

**North star:** an always-on Mac Mini service that behaves like a thoughtful human social-media manager for a small family-farm account — reading comments, writing warm human replies, finding the right other accounts to engage with, posting on cadence — all while protecting the account's health and keeping Boss in the loop on anything he'd want to touch himself.

**Explicit non-goals:** monetization funnels, follower-count gaming, growth-hacking, spam engagement, anything that would make the account feel less human. Reach expansion is a side-effect of being a genuine participant in the small-farm / yorkie / backyard-chicken communities, not the goal.

---

## The constraint that shapes the whole architecture

**Meta's official Graph API does NOT permit:**
- Liking other users' posts programmatically
- Following / unfollowing other users
- Reading other users' feeds (beyond limited hashtag-search + business-discovery endpoints)

This has been the case since Meta tightened the API in ~2018 post-Cambridge Analytica. There is **no official path** to automate the "like/follow other accounts" part of Boss's ask.

**Two real options exist, and Boss has to pick:**

### Option A — Official Graph API only, human-in-loop for like/follow
- **What's automated (TOS-clean, no account risk):** posting, reading our own comments, replying to our own comments, reading DMs, sending DMs to people who DM us first, reading insights, hashtag-search discovery.
- **What's human:** every like and follow Boss wants to do, he does himself. The agent builds a *queue* — "here are 12 posts from small-farm accounts worth engaging with today" — and Boss taps through in the IG mobile app while making coffee. ~1–3 minutes/day.
- **Risk:** zero. 100% TOS-compliant.
- **Cost:** Boss has to do the tapping. Reach growth via likes/follows is constrained by Boss's tapping cadence.

### Option B — Unofficial automation for the like/follow piece
- Tools like `instagrapi` (reverse-engineered IG private API) or a headless Chrome driving the real IG web app can automate likes and follows.
- **Risk:** real. Meta actively detects automation — common outcomes: temporary action blocks (12–48h), permanent account suspension, shadowban (posts stop appearing in hashtag feeds). The 381-follower `@pawel_and_pawleen` account is also tied to your real FB identity + your consulting portfolio. Losing it is not just losing followers — it's losing the portfolio artifact.
- **Mitigations if Boss chooses this path:** very low rate caps (< 30 likes/day, < 10 follows/day, human-like timing jitter), never run headless on a fresh IP, keep action targets high-quality so the engagement looks human. This is *ordinary* risk-reduction but does not eliminate risk.
- **My recommendation:** start with Option A, prove the rest of the agent works, then if Boss wants more reach, revisit Option B with clear eyes.

**This plan assumes Option A as the primary build, with hooks designed so Option B (or a future Meta API expansion) can drop in later without a rewrite.**

---

## System architecture

Single long-running Python service, runs under `launchd`, restarts on crash, logs to a rotating file + Discord alerts on anomalies. Call it `ig_manager/` under `~/Documents/GitHub/farm-guardian/tools/` (it shares infra with Farm Guardian — DB, Discord alerts, keychain loader).

```
farm-guardian/tools/ig_manager/
├── service.py              # entrypoint, event loop, LaunchAgent target
├── poller.py               # polls IG endpoints on a schedule
├── comment_responder.py    # drafts + queues comment replies
├── dm_responder.py         # drafts + queues DM replies
├── discovery.py            # hashtag + business-discovery search → engagement queue
├── engagement_queue.py     # posts queue items to Boss as Discord/Telegram cards
├── poster.py               # wraps the V2 ig_poster.py (already scoped)
├── insights.py             # periodic metrics pull → weekly digest
├── safety.py               # rate-limit guardrails + action-block detection
├── state.py                # SQLAlchemy models for the new tables
└── config.py
```

### Event loop (rough cadence)

| Interval | Action |
|---|---|
| Every 2 min | Poll for new comments on our last 20 posts |
| Every 5 min | Poll for new DMs |
| Every 10 min | Check `iphone-today/` drop folder for new photos |
| Every 30 min | Refresh hashtag-search discovery queue (if depth < threshold) |
| 06:00 / 12:00 / 18:00 / 00:00 | Scheduled post slot (per V2 plan) |
| 09:00 daily | Publish daily engagement queue to Discord for Boss to tap through |
| 09:00 Mon | Weekly insights digest |
| Continuous | `safety.py` watches for 4xx rate-limit responses + backs off |

### The 6 agent capabilities, scoped

---

## Capability 1 — Comment replies (official API, automatable)

**Scope already granted:** `instagram_manage_comments`.

Flow:
1. `poller.py` calls `GET /{ig-user-id}/media?fields=id,comments_count` every 2 min for the last 20 posts. If `comments_count` changed, fetch the delta via `GET /{media-id}/comments`.
2. New comment rows go into `ig_comments` table (id, media_id, from_username, text, created_at, status).
3. `comment_responder.py` classifies each comment:
   - **Compliment / emoji-only** ("😍", "cute!!") → auto-like via API + no reply (or short warm reply, see rule below)
   - **Question** ("what breed?", "when did they hatch?") → Claude drafts a reply, queues for Boss approval
   - **Spam / bot / slur** → mark `status=hidden`, optionally call the `hide` endpoint (`instagram_manage_comments` supports hiding)
   - **Friends / family** (recognized usernames from a seed list) → warm personal draft, queue for Boss
4. Boss approves/edits via Discord card posted to `#farm-2026`. Reactions: 🟢 post as-drafted, ✏️ edit (he replies in thread with edit), 🔴 skip, 🔇 hide.
5. On green, call `POST /{media-id}/comments` with the reply, log to `ig_comment_replies`.

**Approval gate** stays on forever for comment replies — unlike posting, we never want fully-autonomous replies going to real humans. Boss's face is on this account.

**The "warmth principle" applies in the drafter prompt.** This is an account that represents a real farm and a real family; replies should sound like a farmer thanking a neighbor, not an assistant fulfilling a support ticket.

### TODO for the Claude Code session picking this up:
1. Create `ig_comments` and `ig_comment_replies` SQLAlchemy models.
2. Implement `poller.py` with comment-delta detection.
3. Classification prompt + draft prompt in `comment_responder.py` (the draft prompt should be authored carefully; draft a first version and run it past Boss).
4. Discord card posting (reuse `alerts.py` pattern from Farm Guardian).
5. Reply submission with error handling (rate-limit backoff into `safety.py`).
6. Seed file `friends_family.yml` — usernames Boss wants recognized-on-sight. Boss supplies this at setup.

---

## Capability 2 — DM triage + replies (official API, automatable-with-caveats)

**Scope granted:** `instagram_manage_messages`.

**Caveat:** IG's messaging API has strict rules — you can only send messages to users who have messaged you *first*, and only within a 24-hour window after their last message (outside that window, you can only send a limited set of message templates). This matches the philosophy of "respond to friends, don't cold-DM."

Flow:
1. Poll `GET /me/conversations` every 5 min (needs the `yorkies-page-token`, not the user token — DM endpoints go through the Page).
2. New messages → `ig_messages` table.
3. `dm_responder.py` classifies:
   - **Friends checking in** → warm draft, queue for Boss
   - **Genuine questions** (egg sales, visits, flock questions) → Claude draft, **always** queued for Boss — DMs are more intimate than comments
   - **Spam / NSFW / bot onboarding offers** → mark + ignore, do not reply
   - **Business inquiries** (brand asking to "collab") → flag distinctively, queue for Boss with no auto-draft — Boss handles these himself
4. Same Discord approval flow as comments.

**Extra care:** DMs can contain PII (addresses, phone numbers). Do not log full message bodies to Discord alerts — summarize. Never forward full DM text to any external service. Store locally in SQLite.

### TODO:
1. Page-token-based conversation polling (different auth than comment flow — document this).
2. Classification + draft prompts.
3. PII-aware Discord card formatting.
4. 24-hour-window tracker so we never try to send an expired reply.

---

## Capability 3 — Discovery + engagement queue (the "follow/like others" piece, human-in-loop)

This is the capability Boss called out as "most importantly." Option A implementation:

**What the agent does:**
1. **Sources.**
   - **Hashtag search** via `GET /ig_hashtag_search?user_id={ig-id}&q=yorkiesofinstagram` then `GET /{hashtag-id}/recent_media` and `/top_media`. Scopes already granted. Pull the same small-account community tags from our own hashtag library (`#yorkiesofinstagram`, `#backyardchickens`, `#homesteading`, etc.) — accounts posting in *our* community pool are who we should be engaging with.
   - **Business discovery** via `GET /{ig-id}?fields=business_discovery.username({username}){...}` — lets us look up specific accounts by username and see their recent posts. Scope `instagram_basic` covers this. Useful for "accounts Boss already likes but forgets to engage with regularly."
   - **Seed accounts file** `accounts_to_engage.yml` — Boss-curated list of small-farm, yorkie, backyard-chicken accounts he actively wants to stay engaged with. The agent pulls their recent posts and slots them at higher priority than cold hashtag search.
2. **Scoring.** For each candidate post, score on:
   - Niche-fit (is the account small-farm / yorkie / chicken / homestead?)
   - Content-warmth (does the post look like a real person's real animal / real farm moment?)
   - Engagement ceiling (a massive account won't notice our like; a 500-follower homesteader will)
   - Recency (old posts = weird to like)
   - Our own history (have we engaged with this account before? Recently? We want to build genuine relationships, not one-off sprays)
3. **Queue generation.** Every morning at 09:00, generate a Discord (and Telegram, per Boss preference) card:
   > *Today's engagement picks — 12 posts. Reply with 🟢 to queue for tap, 🔴 to skip, and I'll send you the tap-list to your phone.*
   Each card = small thumbnail + username + one-line "why this matches" + a deep-link to the post in the IG app (`https://www.instagram.com/p/{shortcode}/`).
4. **The tap-list.** Boss opens Telegram, taps the 12 links, each opens the IG app, he double-taps. 1–3 minutes. Agent can't do the taps, but it can remove 99% of the cognitive load (curation).
5. **Optional: engagement tracking.** Log which posts Boss tapped through (he confirms back in Telegram with a 🟢 after tapping) so the agent learns his preferences and rotates candidates to new accounts after 3–4 engagements with the same one, to avoid over-focusing.

**Option B extension (deferred):** Add `engagement_executor.py` that, if Boss opts in with full risk awareness, performs taps automatically via `instagrapi` with aggressive rate caps. Designed as a drop-in module so the primary architecture doesn't need changing. **Not built in this plan.** Explicit gate: Boss has to set `engagement.auto_execute=true` in config AND enter a confirmation phrase to arm it.

### TODO:
1. Implement hashtag-search + business-discovery wrappers.
2. Scoring heuristic (start simple — niche-fit as text-match on bio + recent-caption keywords; account-size tier; recency).
3. `accounts_to_engage.yml` seed file + the ingestion logic.
4. Daily queue generation + Discord/Telegram card posting.
5. Engagement-history table so we don't recommend the same post twice.
6. Stub `engagement_executor.py` that refuses to run unless explicitly armed — reserved for if-and-when Boss wants Option B.

---

## Capability 4 — Scheduled posting (V2 plan, already scoped)

Already fully scoped in `19-Apr-2026-instagram-posting-plan.md`. This plan just says: the always-on service *hosts* the V2 posting code — it's not a separate cron job, it's a coroutine inside `service.py` that fires at the 4 cadence slots. The V2 plan's module `ig_poster.py` becomes `ig_manager/poster.py` and imports stay aligned.

**Only new thing here:** wire `poster.py` into the same `safety.py` rate-limiter used by the rest of the agent. One Meta rate-limit budget, shared.

### TODO:
1. Move (or just thin-wrap) `ig_poster.py` into the manager namespace.
2. Ensure posting slots go through the same LaunchAgent'd `service.py` loop, not a separate cron.

---

## Capability 5 — Insights / weekly digest (official API)

**Scope granted:** `instagram_manage_insights`.

Every Monday 09:00, pull and Discord-post:
- Follower count + delta vs prior week (absolute + % growth)
- Per-post reach, impressions, likes, comments, saves for the week's posts
- Best-performing hashtags (which tags correlated with highest reach — this data directly feeds the V2 hashtag rotation)
- Top-engagement commenters (who's replying the most — these are friends being made)
- Action-block / shadowban watch: if reach drops >50% week-over-week, flag for Boss.

This is low-stakes code but high-value information — it tells Boss whether the whole thing is working.

### TODO:
1. Endpoints: `/{media-id}/insights?metric=reach,impressions,engagement,saved`
2. Weekly aggregator + Markdown digest generator.
3. Discord post + email fallback if Discord is down.

---

## Capability 6 — Safety + guardrails (cross-cutting)

`safety.py` is the module every API-calling capability goes through. Responsibilities:

1. **Rate limiter** — global budget across all capabilities. Meta's IG Graph API uses a sliding-window rate limit (200 calls / hour / user by default, more for business accounts). Centralized token-bucket; capabilities sleep if they'd bust it.
2. **Backoff on 4xx** — specifically `429` (rate limited) and `400` with error codes like `32` (page rate limit) or `613` (call rate exceeded). Exponential backoff, alert Boss on sustained back-off (>1hr).
3. **Action-block detection** — if reach or engagement craters suddenly, or if multiple calls return 403s, assume Meta has flagged something and **stop all outbound activity** pending Boss review. Alert immediately.
4. **Circuit breaker** — if the account gets a `challenge_required` response (Meta asking for login verification), halt all operations and alert Boss. This is the canary for account health.
5. **Audit log** — every API call logged to `ig_api_log` table with status code + response summary. If anything goes wrong, we can trace it.

### TODO:
1. Token-bucket implementation (one bucket, shared).
2. Response-code handlers for every capability's API layer.
3. Circuit-breaker + alerting.

---

## Operational concerns

### LaunchAgent

`~/Library/LaunchAgents/com.farmguardian.ig-manager.plist`:
- `RunAtLoad=true`, `KeepAlive=true`
- `StandardOutPath` and `StandardErrorPath` to `~/Library/Logs/ig-manager.{out,err}.log`
- Logs rotate via `newsyslog` config.
- On crash → `launchd` auto-restarts; `service.py` emits a Discord alert on startup after unexpected restart.

### Secrets

All via macOS keychain (same services as the existing IG work). No env files under source control. `config.py` handles keychain reads + fallback to the `farm-guardian-meta.env` file if keychain is unavailable (CI, etc.).

### Database

Reuse Farm Guardian's SQLite database. Add new tables:
- `ig_comments`, `ig_comment_replies`
- `ig_messages`
- `ig_engagement_candidates`, `ig_engagement_taps`
- `ig_insights_weekly`
- `ig_api_log`

Migration script lives in `tools/ig_manager/migrations/`.

### Discord + Telegram routing

- **Discord `#farm-2026`** for approval cards (UI affordances: reactions, threads for edits)
- **Telegram DM to Boss** for urgent alerts (action blocks, DM from friends-and-family)
- Both via existing Bubba infrastructure — no new bots to create.

### Observability

- `service.py` emits a heartbeat to a `ig_manager_heartbeat` file every 30s; a companion launchd job alerts if heartbeat is stale >5 min.
- `/status` endpoint on localhost:9876 returning JSON — queue depths, last-successful-call-per-capability, rate-bucket remaining.

---

## Suggested session breakdown (each a fresh Claude Code session)

Boss said he'd spawn new CC sessions per piece. Suggested ordering — dependency-correct, smallest-to-largest:

1. **Session 1 — Scaffolding + safety + launchd.** Build `ig_manager/` skeleton, `safety.py`, LaunchAgent plist, logging, heartbeat, `/status` endpoint. Ship a service that runs forever and does nothing but log "alive" every 30s and expose its status. Foundation for everything.

2. **Session 2 — Porting V2 auto-posting into the manager.** Move/wrap `ig_poster.py`, wire into the event loop at the 4 cadence slots. Keep the manual approval gate from the V2 plan. After this session, the always-on service replaces the scheduled posting cron.

3. **Session 3 — Comment capability.** Polling + classification + draft + Discord approval + submit. End-to-end on real comments from our two existing posts. Easiest way to prove the agent loop works with real user content.

4. **Session 4 — Insights / weekly digest.** Lowest-risk endpoint surface, highest-value feedback to Boss. Gives us data to tune hashtag rotation.

5. **Session 5 — Discovery + engagement queue (Option A).** Hashtag-search, scoring, daily queue, Telegram/Discord delivery. Don't attempt Option B in this session.

6. **Session 6 — DM capability.** Similar to comments but with PII and page-token-specific auth. Worth isolating so PII-handling gets full attention.

7. **Session 7 (optional, gated) — Option B engagement execution.** Only if Boss explicitly requests it after 2–4 weeks on Option A. Fully isolated module, behind an explicit config flag, with rate caps designed to look human.

Each session picks up from this plan + the running state of the service, writes its own CHANGELOG entry, and updates SKILL.md when the capability goes live.

---

## Open questions for Boss

1. **Risk tolerance on Option B** — hold off, or authorize Option B with tight caps after Option A proves out? Default assumption in this plan: hold off.
2. **`friends_family.yml` + `accounts_to_engage.yml`** — Boss supplies these. Who's the starter set for each?
3. **DM auto-reply boldness** — should the agent ever send a DM reply without explicit approval (e.g., a canned "Thanks, I'll get back to you!" on first contact outside business hours)? Default assumption: never.
4. **Discord vs Telegram routing** — approval cards in Discord, urgent alerts to Telegram, engagement queue in Telegram. Right split?
5. **Account-health red lines** — what's the threshold at which Boss wants the agent to halt all activity? Default assumption: any 403, any `challenge_required`, any >50% week-over-week reach drop.
6. **Cadence of the daily engagement queue** — 09:00 coffee-time right? Or multiple small queues throughout the day?
7. **"Manager persona" distinct from "post author"?** — should comment-replies be signed differently from post captions, or share the same voice? Current plan: same voice, the account speaks with one voice.

---

## The why (don't lose sight of)

The repeated framing from Boss: this is about friends and family and the warmth of a real farm with real animals. Monetization is not a goal. The automation exists so he doesn't have to choose between *being present on the farm* and *staying connected with the people who care about it*. The agent is a back-office social-media manager for a family journal, not a growth-hacking funnel. Every design choice — the manual-approval gates, the human-in-loop for likes/follows, the "warmth principle" in reply drafting, the conservative safety guardrails — flows from that.

If the agent ever starts to feel like a corporate content bot instead of a thoughtful neighbor helping out with the farm's phone, something has gone wrong and Boss should push back hard.
