<!--
Author: Claude Opus 4.8 (1M) — Bubba, Mac Mini
Date: 20-July-2026
PURPOSE: Findings + plan for a self-sustaining daily farm diary that feeds the social feed,
         sourced by distilling the day's #meet-the-lobsters Discord chatter. Written after a
         three-agent survey of farm-guardian, OpenClaw/Bubba, and the provenance memory system.
SRP/DRY check: Pass — this is the plan-of-record; it reuses the existing diary store,
         reel-caption consumer, reaction gate, and the dormant daily-email-summary cron pattern.
         Nothing here rebuilds machinery that already exists.
-->

# Daily Farm Diary from Discord — findings + plan (20-Jul-2026)

## The ask (Boss, verbatim intent)
A running daily farm diary that feeds the social feed with the *general* goings-on around the
farm — not just pretty camera frames. Bonus goal: Bubba has a provenance memory system but
barely writes to it; the Boss wants that fixed too. Boss's steer on the mechanism: **a daily
cron that collects what was said in the main Discord channel** — because he's already talking
there every day, so nothing new should be asked of him.

## TL;DR
We are **not building a diary system — we already have one, and it's already wired to the feed.**
It went dead because nothing writes it. The fix is one component: a once-a-day job that reads the
day's `#meet-the-lobsters` conversation, distills the farm-relevant bits into a short entry in
Bubba's voice, writes it to the existing diary store, and posts it back for the Boss's reaction.
His reaction is the publish button — same gate the whole farm social system already runs on.

---

## Findings (what's actually there)

**1. The diary already exists AND already feeds the social feed — it's just starved.**
- Store: `farm-2026/content/diary/*.md` — 23 real flock-narrative entries, signed "-Bubba".
- Consumer (already wired): `farm-guardian/tools/pipeline/daily_reel_runner.py::_load_farm_context()`
  (`:651`; dir at `:604`, gate at `:611`) globs `*.md` (`:666`), takes the 3 newest inside a
  **21-day freshness window**, skips resolved health incidents, caps 600 chars each, and injects
  them into both the Codex and LM-Studio reel-caption prompts (`codex_reel_curator.py:215`).
- So a **fed** diary automatically makes IG/FB reel captions richer. A **stale** diary yields empty
  context *by design* — the gate (v2.44.17, 09-Jul) exists because a month-old healed "buff buttrot"
  note kept getting captioned as current news.
- Current state: last entry `2026-07-09`, ~11 days stale. The June→July gap was ~a month of nothing.
  Reel captions are running on near-zero diary context right now.

**2. It starved for the exact reason Bubba under-writes memory: no schedule + high-friction write + discretion that decays.**
- The diary was hand-authored in ad-hoc Claude sessions. Nothing schedules it. When no one writes it,
  it stops — which is its current state.
- Same pattern in memory: the write path is a 6-flag shell command with no tool/alias binding, and the
  "MANDATORY on every session start" `session.bootstrap` write has fired **4 times ever, none since 2-Jul.**
  More exhortation won't fix this — the persona files are already saturated with "remember proactively."

**3. The provenance memory system is the WRONG home for a diary.**
- It's an immutable, keyword-searchable **fact log** (FTS5/BM25 + a pure-Python TF-IDF "vector" search
  that whiffs on synonyms). Good at "recall this fact by keyword."
- It has **no time-ordered read at all** — no `--since`, no `recent`, no date range in the CLI or engine.
  Every read comes back ranked by keyword relevance, never by date. A diary is read by *when*; the tool
  can't ask "when."
- Its own past experiment proves the point: `archive:daily-logs` (444 raw session transcripts) polluted
  fact recall so badly it's now force-excluded from every normal query. Verbose daily content there is a
  known anti-pattern.
- Verdict: the diary's primary home stays the dated files. We can *optionally* also write each entry to a
  fenced `diary:farm` stream (immutable copy, feeds the memory as a byproduct) but only if paired with a
  ~10-line date reader, because the tool can't read it back chronologically.

**4. Two narrative stores that drifted apart.**
- `content/diary/*.md` → reel-caption fuel (the reader globs `*.md`).
- `content/field-notes/*.mdx` → the public website page (`lib/content.ts` reads `.mdx`; "the weekly farm
  update system that replaces diary for public-facing"). The diary `.md` files are **not rendered on the
  site** since the 16-Jul retheme (`lib/content.ts:292` filters `.mdx`, every diary file is `.md`).
- Net: Bubba's recent diary writes feed captions but are invisible on the site. That split is actually the
  right shape — frequent raw diary = caption fuel; weekly curated field-note = public page — it just needs
  to be intentional, not accidental.

**5. The v2.48.1 "digest"/"insights" LaunchAgents are IG analytics, not a diary.**
- `ig-insights-fetch` / `ig-weekly-digest` pull Graph-API performance metrics into `ig_media_insights`
  and post a performance recap to Discord. `reports.py`/`daily_summaries` is a working scheduled
  daily-TEXT generator but its content is the predator-detection report and it never touches social.
  None of these overlaps a flock-narrative diary — nothing to reuse-and-skip there.

**6. Correction (housekeeping).** Bubba's Discord model is `anthropic/claude-opus-4-8` via claude-cli,
not Codex gpt-5.5 — reverted 2-Jul, confirmed against live `openclaw.json` + `sessions.json` + gateway
heartbeats. (An old memory note claimed Codex; corrected.)

---

## The approach (decided): daily channel-digest → diary

**Source = the main `#meet-the-lobsters` channel** (guild `1471632570616643657`, channel
`1471632572953006337` — the only one wired `requireMention=False`, i.e. where free conversation lives).
Pull the last 24h **via the bot** (`channels.discord.token` present; `actions.search`/`actions.messages`
already enabled) rather than from local session `.jsonl` — Discord is the canonical record and captures the
Boss's *own* words, not Bubba's filtered session view.

**Flow:**
1. Once a day (evening), a job pulls the day's messages from the channel.
2. An LLM in **Bubba's voice** distills only the **farm-relevant** signal — what the Boss and Bubba actually
   said about the flock/farm — and drops the bot cross-talk (Larry/Egon/Vladimir/Horst/usage pings/banter).
3. It writes `content/diary/YYYY-MM-DD.md` (the format the caption pipeline already reads).
4. It posts the entry to `#farm-2026`. **Boss reaction = publish** (promote to the public site as a
   field-note, and/or let it flow into captions). No reaction → it still sits in `content/diary` as caption
   fuel, but nothing public happens.

**Why a dedicated cron, not the hourly heartbeat:** the heartbeat fires every hour and is scoped to
alerts/standing-tasks (per the 20-Jul HEARTBEAT.md rewrite). A daily digest wants one clean once-a-day
firing in an isolated session — which is exactly the dormant `daily-email-summary` cron pattern
(`0 12 * * *`, `session_target:isolated`, `delivery:announce`). Reuse that shape.

**"Distill, not dump" is also the safety.** The channel is noisy, so the job must summarize, not paste. And
because it summarizes **what was actually said**, it can't hallucinate a bird ID or an event the way a
camera-data roll-up could — the VLM mis-IDs individuals (documented) and `share_worth=strong` has tagged a
clipped frame. Ground truth comes from the Boss's words: if he said "lost one to a coyote," that's real; if
nobody said it, it doesn't appear. The writer must never assert a specific bird/event not present in the
day's text — the hand-written entries already model this ("IDs not pinned from the photo — not guessing").

---

## What exists vs. what to build

**Already exists (do not rebuild):**
- Diary store: `farm-2026/content/diary/*.md`.
- Auto-feed to reels: `_load_farm_context()` (21-day gate) → IG/FB reel captions.
- Reaction gate: `discord-reaction-sync` + every outbound lane filtering `discord_reactions > 0`.
- Public surface: `content/field-notes/*.mdx` (weekly).
- Bot read/search access to the channel (token + `actions.search`).
- Scheduling pattern: the dormant `daily-email-summary` cron (`~/.openclaw/state/openclaw.sqlite`, `cron_jobs`).

**To build (this is the whole scope):**
1. **The distiller/writer** — pull 24h of `#meet-the-lobsters` via the bot → LLM (Bubba voice) → farm-relevant
   distill → `content/diary/YYYY-MM-DD.md` → post to `#farm-2026`.
2. **The reaction→publish promotion** — on Boss reaction, roll the entry into a public `field-notes/*.mdx`
   (fixes the `.md`/`.mdx` render gap intentionally: daily `.md` = caption fuel, promoted/weekly `.mdx` = site).
3. **(Optional) memory byproduct** — also write each entry to a fenced `diary:farm` provenance stream +
   a ~10-line `--since` reader, so the memory fills automatically and is finally readable by date.

---

## Open choices (small, for whoever builds it)
- **Which model distills:** Bubba's own `claude-opus-4-8` via claude-cli (best voice match, on-subscription),
  a cheap OpenRouter/OpenAI model, or the local LM Studio text model. Lean opus-4-8 for voice fidelity.
- **Fire time:** after the day's farm activity but before the 21:00 S7 reel would want fresh context — ~20:00.
- **Weekly rollup:** whether the public `.mdx` field-note is per-day-promoted or a Sunday roll-up of the
  week's diary entries. The site already frames field-notes as weekly.
- **Backfill:** optionally seed the first run from the last few days of channel history so the diary isn't
  blank on day one.

## Guardrails (non-negotiable)
- Nothing posts to public IG/FB/site without the Boss's Discord reaction. The 21:00 S7 reel is the one
  no-approval lane — the diary must never feed it an unverified claim; keep the writer to what was said.
- No invented individual bird IDs or events. Distill only; when unsure, say so, like the hand-written entries.
- Summarize, never paste raw channel logs (noise + it's how the memory system got polluted before).

## Key references
- Consumer: `farm-guardian/tools/pipeline/daily_reel_runner.py:604,611,651,666`; `codex_reel_curator.py:215`
- Store + site loader: `farm-2026/content/diary/*.md`; `farm-2026/lib/content.ts:292` (diary, wants `.mdx`), `:315` (`getAllFieldNotes`)
- Raw material if wanted as texture: `farm-guardian/tools/pipeline/store.py:46` (`image_archive`); `farm-2026/content/flock-profiles.json`
- Cron pattern: dormant `daily-email-summary` in `~/.openclaw/state/openclaw.sqlite` (`cron_jobs`)
- Bot + channel: `~/.openclaw/openclaw.json` → `channels.discord` (token, `actions.search`); main channel `1471632572953006337` (`requireMention=False`)
- Memory tool (optional side-write): `~/bubba-workspace/memory-system/toolMemorySystemWithProvenance.py` — no date read exists; would need a `--since`/`recent` addition
