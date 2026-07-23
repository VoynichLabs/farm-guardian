# 22-Jul-2026 — Mac Mini Ecosystem Audit (read-only)

Author: Claude Fable 5. Scope: whole Mac Mini ecosystem (farm-guardian, LaunchAgents, social lanes, OpenClaw/Bubba, credentials findability, satellite reachability, farm-2026 touchpoints). Read-only — nothing was changed. This doc is the findings + a work breakdown for a cleanup assistant. Coverage note: 8 of 11 audit surfaces completed (launchagents-detail, services-health, and 4 of 8 doc-triage chunks were cut short); findings below are verified evidence from the completed surfaces plus direct inspection.

## TL;DR

The system mostly works. Every social lane that should post is posting; yard-diary, FB cross-post, Bubba's gateway, and the tunnel are verified healthy. The real problems are: **both Reolink cameras were off-network at audit time**, ~6 things fail on a loop and nobody notices, and the "read this first" docs (CLAUDE.md, SOCIAL_MEDIA_MAP, HOW_IT_ALL_FITS, HARDWARE_INVENTORY, lm-studio-reference) are all wrong about the current system in ways that will burn the next agent.

## STATUS — most of this was fixed 23-Jul-2026

| # | Item | Status |
|---|---|---|
| 1 | Both Reolinks unreachable | ✅ **Was transient.** `house-yard` and `duo2` are both capturing normally (verified by fresh archive frames). No outage. |
| 2 | `anthropic-token-refresh` failing every 15 min | ✅ **Retired.** Its target file was deleted by the OpenClaw 6.11 auth migration; the gateway no longer needs it. Booted out, plist renamed `.retired-23jul2026`. |
| 3 | codex_reel_curator 401 | ✅ **Fixed by removing the dependency (v2.51.5).** The Codex subscription is gone for good, so captions moved to the local VLM — see below. |
| 4 | OpenClaw `daily-email-summary` cron erroring | ✅ **Fixed and verified.** Its `toolsAllow` list was both unenforceable under the claude-cli runtime *and* wrong (no shell tool, yet the job shells out to `himalaya`). Cleared it; a manual run now reports `ok`. |
| 5 | `phoenix.sh` March-snapshot landmine | ✅ **Defused.** Ran `phoenix.sh backup` against the healthy gateway, so `last-known-good.json` is now today's config instead of 2026-03-01. Safety net kept, landmine gone. |
| 6 | Larry up / Egon answering SSH | ⚠️ **Boss call.** Documented in CLAUDE.md; Egon marked do-not-SSH pending a Linode dashboard check. |

**Also fixed in the same pass:** `~/.openclaw/secrets.json` locked to 0600; log rotation widened to cover `guardian.out.log` (was 93 MB, uncovered), the discord-reaction-sync pair, cloudflared and the lmstudio watchdog, with the threshold dropped 50 MB → 25 MB (~130 MB reclaimed immediately); the stale `qwen3.5-9b` fallback default in `daily_reel_runner.py` corrected to the live model so a missing config key can never trigger LM Studio's auto-load.

**Reel captions — fixed 23-Jul-2026 by deleting the dependency, not restoring it.** Boss confirmed the Codex subscription is gone for good, so `codex login` was never the answer. `tools/pipeline/codex_reel_curator.py` has been deleted and all caption synthesis moved to the local VLM already running for the pipeline (`qwen/qwen3-vl-4b`):

- The mixed lane was never visibly broken — it already fell through to LM Studio.
- **The two yard lanes were the real damage.** `house-yard` and `duo2` are `vlm_bypass` cameras with no per-frame descriptions, so on Codex failure they returned a hardcoded literal and posted near-identical captions for two weeks. They now synthesize on the local VLM from their scene hint plus the farm diary.
- **Correction to this audit's first write-up:** it claimed *three* daily camera reels were posting the literal. That was wrong. `s7-cam` is not `vlm_bypass` — 17,449 of 17,449 rows in the last 7 days carry a `caption_draft` — so the s7 lane always took the drafts path and its captions were fine throughout.
- An intermediate fix attached sampled frames to the caption call to ground those lanes in real footage. It worked, but Boss's call is that a permanently-fixed yard time-lapse looks the same every day and does not justify the vision calls — so the frame-attachment code was removed entirely rather than left dead behind a flag.
- `BRAND_RULES` (no "chicks" for the grown flock, never any predator/hawk framing) was the durable asset inside the deleted module; it now lives in `tools/pipeline/caption_brand.py` and is injected into every caption prompt.

**Deliberately NOT changed (they look broken but aren't):** `usb-cam`, `dominator-cam` and `mba-cam` log ConnectTimeouts every pipeline cycle. That is *expected* — they are opportunistic cameras on hosts that sleep or get powered down (the MSI Dominator is Boss's day-to-day laptop; the MacBook Air sleeps overnight). Disabling them would break the intended "comes back when the host wakes" behavior. The log noise was the real problem and rotation now handles it. `mba-cam` does still capture ~2 GB/day that no publishing lane consumes — that one is a genuine Boss call, not a bug.

---

## Fix-now (actually broken) — original findings, kept for the record

1. **Both Reolinks (house-yard + duo2) unreachable** at every documented IP; no RTSP host on the /24 at audit time. house-yard is the only detection-enabled camera. Needs hands: check camera power/WiFi; then pin static DHCP leases on the router and reconcile the IP mess (config says .88/.155, pipeline config .89, HARDWARE_INVENTORY .156, running Guardian reported .2). [ST-02, D1-08]
2. **`com.bubba.anthropic-token-refresh` has failed every 15 min since 02-Jul** (1,528 failed runs) — the OpenClaw 6.11 upgrade moved auth into sqlite and deleted the file it syncs. Gateway doesn't need it anymore. Retire: bootout + delete the plist. [OB-01/CM-01]
3. **codex_reel_curator fails with OpenAI 401 on every reel build since ~07-Jul** (45 failures; reels post fine via fallback, but curation has literally never worked in production). Fix the codex CLI's OpenAI key or rip the codex call out. [SL-01]
4. **OpenClaw cron `daily-email-summary` errors every run** ("claude-cli cannot enforce toolsAllow") — remove the toolsAllow restriction or switch the job's model runtime. [OB-02]
5. **`phoenix.sh` is a landmine**: its restore source is a March 1 config snapshot. Anyone running `phoenix.sh health` during an outage rolls Bubba's config back 4.5 months. Refresh its backup or delete it. [OB-04]
6. **Larry (.194) is UP** (contradicting "down for a while") but serving no camera ports — likely parked at the Windows login screen while pipeline entries may still be pointed at it. Egon's Linode **still answers SSH** despite "decommissioned" — check the Linode dashboard; if it was deleted, that IP is a stranger and the egon skills/docs must say DECOMMISSIONED so no agent ships credentials at it. [ST-03, ST-04]

## Doc drift (the "contradictory shit" — worst offenders)

All canon docs failed verification. Highest-value corrections:

- **CLAUDE.md**: says 4 cameras (there are 7); says usb-cam is on the Mini :8089 (it's on the MacBook Air; Mini's own IP is .54 not .71); names the wrong production VLM (**live model is qwen/qwen3-vl-4b since v2.44.3, 01-Jul** — the doc says qwen3.5-9b "reverted from" it, which invites a harmful "fix"); wrong reel/carousel times; describes the reciprocate lane (dead since 23-Apr) as live; GWTC IP is now .69 not .68; presets TODO is false (5 presets exist). [CD-01..05, D3-02, SL-03]
- **SOCIAL_MEDIA_MAP.md** ("single source of truth"): 11 weeks stale; wrong on every posting time; missing the 22-Jul three-fixed-reels redesign and ~6 newer lanes; still claims the mixed reel has an approval gate (it's `approval_required=False`, auto-publishes). Rewrite the lane table from the live plists: carousel 12:30, mixed reel 18:00 auto, house-yard reel 09:00, s7 12:00, duo2 15:00, s7-backlog 09/13/17/20, insights 23:30, weekly digest Sun 20:00. [SL-02, CD-08, RH-04]
- **HOW_IT_ALL_FITS.md**: claims live per-cycle IG auto-posting (dead since May) and that reels/stories "have no code" (three reel lanes post daily). Rewrite or demote with a banner. [CD-09]
- **HARDWARE_INVENTORY.md**: "Six Cameras" header over 8 rows; usb-cam host wrong; repeats the GWTC-runs-LM-Studio myth that CLAUDE.md explicitly retracted. [CD-06/07]
- **docs/13-Apr-2026-lm-studio-reference.md**: keep (safety rules still load-bearing) but fix 4 stale claims incl. wrong model names. [D3-01]
- **~15 old plan docs** need one-line SUPERSEDED/DONE/NOT-IMPLEMENTED banners (full list with exact wording in the audit worklist below); the dangerous ones are 08-Apr preset-setup-plan (running it would overwrite 3 of 5 live presets) and 13-Apr s7-phone-setup (its pkill+nohup recipe would double-spawn Guardian under launchd). [D0-*, D1-*, D3-*]
- **Nextdoor posts DAILY at 18:30** and has for months; CLAUDE.md's hard constraint says 1/week Sundays. Boss call: bless daily and fix the doc, or cut cadence back. [SL-04]

## Cruft (safe deletions, ~15 GB + noise)

- `.score100-backup-20260712-180753/` — fully superseded by git history. Delete. [RH-02]
- `data/backups` — 12 GB, growing ~240 MB/day, **zero rotation**. Add rotation (keep ~14 dailies + monthlies); one-time prune reclaims ~10 GB. Boss picks retention. [RH-06]
- ~1.8 GB dead-lane reel MP4s (usb/gwtc/mba/dominator dirs in data/reels — keep the posted/ state files). [RH-09]
- SQLite litter at data/ root (`guardian 2.db`/`guardian 3.db` sidecars, zero-byte strays) + `preset_test_*.jpg`. [RH-10/11]
- ~180 OpenClaw backup files (55 openclaw.json variants — each a live-token snapshot, several world-readable; 104 auth-profiles baks; 9 plist baks). Keep `last-good`, the newest dated one, and the `known-good-5.12`/`6.10` pairs (rollback script depends on them). [OB-03, CM-06]
- 3 dead OpenClaw workspaces (~600 MB), stale `~/.openclaw/logs` (36 MB), bubba-workspace root .baks/JPGs, 557 MB CLI image cache. [OB-06/09/12]
- 11 dead scripts/tools in the repo (iphone lane, s7 smoke/battery tools, six dead ig-* reel shims, test-siren). Keep ig-post.py, add-camera.py, env-gated throwback scripts, usb-cam-host, discord_harvester. [RH-14]
- LaunchAgent graveyard: rename-or-remove the loaded-but-no-op `archive-throwback`; delete `.retired`/`.bak`/`.disabled-*` plists that predate May. [RH-15]
- mba-cam raw capture burns ~2 GB/day of frames no lane consumes — Boss call: warm standby or off. [RH-08]
- OpenClaw cron: delete the June-15-only `trading-monitor` job before it wakes up in 2027; purge 7 disabled Feb–Jun jobs. [OB-10]

## Credentials map (findability — all good)

Everything is findable: Reolink passwords in tracked config.json **and** .env `CAMERA_PASSWORD`; Discord webhook in .env + config; IG/FB Graph tokens in `bubba-workspace/secrets/farm-guardian-meta.env` (0600, all 12 keys, consumers verified); Bubba's Discord token in openclaw.json; router/WiFi/GWTC passwords in `bubba-workspace/memory/reference/network.md`; tunnel cred in `~/.cloudflared`. Loose ends: `GUARDIAN_REVIEW_TOKEN` has code consumers but no value anywhere (review endpoints dormant — document or provision); README points the eBird key at config.json but it lives in .env; `tailnet.env` is an orphan; `~/.openclaw/secrets.json` is 0644 and holds gmail/Linode/root creds — chmod 600. [CM-*]

## Verified working (leave alone)

Guardian service + tunnel + public site; yard-diary end-to-end (3/day, zero missed slots all week); all live IG lanes + FB cross-post; reacted-gem story queue (~188 deep) draining at the 22/day self-cap **by design** — quota is saturated, not broken; Bubba gateway 2026.6.11 healthy (logs now at `~/Library/Logs/openclaw/`, not `~/.openclaw/logs/`); MacBook Air hosting usb-cam-host :8089; S7 phone; recent CHANGELOG entries all verify — changelog discipline is trustworthy.

## Work breakdown for the cleanup assistant

Ordered; each task is self-contained. Get Boss approval on B4, C5, C6 cadence/retention questions first.

- **A. Unbreak (1 sitting)**
  - A1. Reolink outage: confirm cameras back (Boss checks power/WiFi), then reconcile IPs across config.json / tools/pipeline/config.json / HARDWARE_INVENTORY / AGENTS_CAMERA and pin static DHCP leases. Restart guardian + pipeline agents after config edits.
  - A2. `launchctl bootout gui/501/com.bubba.anthropic-token-refresh` + delete plist, script note in bubba-workspace.
  - A3. Fix or remove codex_reel_curator's OpenAI dependency (`tools/pipeline/codex_reel_curator.py`; key lives in codex CLI auth, not repo .env).
  - A4. Fix OpenClaw `daily-email-summary` cron (drop toolsAllow or change runtime); verify next run.
  - A5. Refresh or delete phoenix.sh + its March snapshot; delete billing-spam-watchdog leftovers.
  - A6. Resolve Larry/Egon status with Boss; mark egon-gateway + larry-access skills DEAD or archive them.
- **B. Docs truth pass (1-2 sittings, mostly mechanical)**
  - B1. CLAUDE.md: 7-camera roster, correct VLM model (qwen/qwen3-vl-4b), usb-cam→MBA, Mini IP .54, GWTC .69/disabled, reel times, reciprocate=disabled, presets exist, machine table caveats.
  - B2. Rewrite SOCIAL_MEDIA_MAP.md lane table from live plists (times listed above); add missing lanes.
  - B3. HOW_IT_ALL_FITS.md + HARDWARE_INVENTORY.md rewrites (or demote with banners pointing at fixed B1/B2).
  - B4. Nextdoor cadence: Boss decision, then align doc or plist.
  - B5. Banner pass over the ~15 stale plan docs (exact wording per finding IDs D0-01..06, D1-01..06, D3-01..06 in the workflow journal; the two dangerous ones first).
  - B6. Small fixes: README eBird key location; AGENTS_CAMERA venv path (python3.13) + IP line; farm-2026 types.ts enum drift (F2-01/02).
- **C. Cruft purge (1 sitting, needs the deletion list above)**
  - C1. Delete .score100-backup, preset_test jpgs, sqlite litter, dead-lane reel MP4s, dead scripts (RH-14 keep-list applies).
  - C2. OpenClaw backup prune (keep-list in OB-03); chmod 600 secrets.json; delete dead workspaces + stale logs + tmp.
  - C3. LaunchAgent graveyard cleanup; archive-throwback plist to .disabled.
  - C4. Commit the live config.json threshold change (0.7, Boss's dashboard tuning from 22-Jul) — it's production truth.
  - C5. data/backups rotation (Boss picks retention) + one-time prune.
  - C6. mba-cam raw capture: Boss call, then disable or document.
- **D. Watch items**
  - D1. 23-Jul ~09:05: verify first-ever house-yard reel run succeeded (`/tmp/ig-house-yard-cam-timelapse-reel.err.log`). Note: today's retime means no s7/duo2 reel posted 22-Jul (UTC-dated state files; self-heals tomorrow).
  - D2. IG quota stays saturated at ~22/25 daily; adding lanes will starve the story backlog further.
  - D3. Not audited (session cut short): per-plist launchagent detail, services-health deep pass, docs chunks covering ~48 mid-period docs. Low expected value; pick up only if something above doesn't reconcile.

Full raw findings (all IDs cited above, with evidence and exact fix text): workflow journal at `~/.claude/projects/-Users-macmini-Documents-GitHub-farm-guardian/c4126771-dfc8-4be4-86c1-062ed3c09af6/subagents/workflows/wf_8b3b0094-170/journal.jsonl`.
