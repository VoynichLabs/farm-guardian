# CLAUDE.md — Farm Guardian

This file provides guidance to AI coding agents working in this repository.

## SEVERAL outside devices died at once — THE BIRDCATRAZ CIRCUIT TRIPPED (moisture)

**The outdoor circuit at Birdcatraz powers everything outside, it trips from moisture, and it does
NOT reset itself — someone has to walk out and physically flip the outlet's breaker.** This has
happened; 07-Aug-2026 cost 3¼ hours overnight.

**✅ 13-Aug-2026 (v2.71.0) — YOU WILL NOW BE TOLD. `com.farmguardian.birdcatraz-watchdog`
(every 5 min) probes `farm-pi5` and posts a Discord alert mentioning Boss after ~10 minutes of
silence.** The Pi has no battery backup, so it is the canary for this exact fault. The alert
already distinguishes **"circuit tripped, flip the breaker"** (Pi *and* other outdoor devices
dark) from **"just the Pi"** (its own adapter/cable) by probing `house-yard`/`duo2`/`s7-cam` —
so if you are reading an alert, that judgement is already made for you; don't redo it. It posts
one alert per outage and one recovery notice, never a stream. Source:
`tools/birdcatraz-watchdog/watchdog.py`; plan + verification:
[`docs/13-Aug-2026-birdcatraz-power-watchdog-plan.md`](docs/13-Aug-2026-birdcatraz-power-watchdog-plan.md).
**⚠️ If you ever write another stdlib Discord poster: Discord 403s urllib's default
`Python-urllib/3.x` User-Agent. Set an explicit one** — this cost a silent failure during
verification, and `tools/s7-battery-monitor/monitor.py` still carries the same latent bug.

**Signature — this is a power fault, not a camera fault:**

- **Multiple unrelated outside devices die at the same instant.** 07-Aug: `duo2` (Reolink, its own
  power brick) and both `farm-pi5` cameras went together. Different hardware, different hosts,
  one circuit.
- **Indoor gear is untouched** — the Mac Mini kept running throughout.
- Confirm with boot times. Outside devices rebooted while the Mini did not = the circuit tripped:

```bash
ssh markb@192.168.0.17 'uptime -s'; sysctl -n kern.boottime
```

**Nothing on the Mini can fix this.** Don't diagnose adapters, don't restart services, don't sweep
the LAN — tell Boss the Birdcatraz breaker needs flipping. Do NOT confuse it with the Reolink
power-adapter trap below: that one is a *single* camera absent from the network.

## `s7-cam` IS DARK BUT THE PHONE LOOKS FINE — CHECK WHICH SSID IT IS ON

**On 24-Aug-2026 the S7 rebooted and reconnected to `653 Pudding Hill 2G Guest` instead of
`653 Pudding Hill 2G Private`. The guest SSID has CLIENT ISOLATION, so the phone had working
internet while being firewalled off from every device on the farm LAN. `s7-cam` was dark for
16½ hours. Nothing was broken — not the phone, not the app, not the camera, not the network.**

**⛔ A LAN SWEEP CANNOT DETECT THIS, AND ITS SILENCE IS NOT EVIDENCE.** A full `/24` ping sweep
did not surface the phone's MAC, and a threaded probe of port 8080 across all 254 addresses
found nothing. **Both results were correct and both were misleading** — an isolated guest client
is invisible to LAN scanning *even while holding an address in the same `192.168.0.0/24` range*
(it had `.89`, right beside `house-yard` at `.88`). **Absent from ARP ≠ off the network. It may
be one SSID away.**

**⛔ Do not conclude "the phone is off" from a clean `Host is down` with all ports closed.** That
guidance elsewhere in this file is incomplete. It matches a phone that is off *and* a phone on
the guest network, and those need opposite responses.

**The guest SSID is the SAME PHYSICAL ROUTER** — guest BSSID `5e:a6:e6:16:f1:0f` vs the AX55's
`5c:a6:e6:16:f1:10`, locally-administered bit set. It is not a second box and does not look
like one.

**Diagnosis needs ADB, which now works** (SM-G930V, serial `4fad774d` — the replacement handset
has a healthy USB port; every "no ADB path exists" note in this repo describes the retired
SM-G930F). Plug into the Mini and ask:

```bash
adb shell dumpsys wifi | grep -m1 mWifiInfo      # which SSID?
adb shell ip -4 addr show wlan0                  # .249 = right net, anything else = wrong net
adb shell ping -c2 192.168.0.10                  # 100% loss to the Mini = CLIENT ISOLATION
```

**✅ ROOT CAUSE FIXED 25-Aug-2026 (v2.71.6) — this should not recur.** The reason `.249` was ever
held by a fragile static-IP-on-the-phone is that the router's reservation pointed at the
**retired** handset's MAC. It now points at the live phone (`2C-0E-3D-09-77-A4`), the phone is on
**DHCP**, and the guest SSID is forgotten. **Verified by rebooting the phone**: it rejoined
Private unattended, took `.249` by reservation in ~5s, and IP Webcam auto-started. See
[`docs/25-Aug-2026-router-dhcp-reservation-fix.md`](docs/25-Aug-2026-router-dhcp-reservation-fix.md).

**⛔ ROUTER WORK IS OURS, NOT BOSS'S.** He is non-technical and has said so explicitly — never
hand him a router procedure or wait on his approval for one. Use
`tools/router/dhcp_reservations.py`. **Password is `Bubba123`; the older
`~/bubba-workspace/tools/router/` scripts hardcode `118Oplas`, which is WRONG, and ten failed
logins lock the router for two hours.**

**Historical fix (if it somehow recurs): FORGET the guest network so Private is the only saved
SSID.** The static `.249` used to be stored **per saved network** on the Private entry, so it
returned by itself — recovery took under 4 seconds. **`svc wifi disable/enable` does NOT work** — Android re-selected Guest every time
across 44s despite Private having the higher `PRIO`; priority does not decide this, do not waste
time toggling.

**⚠️ Driving this phone's UI over ADB: the screen is LANDSCAPE** (`mCurrentOrientation=1`), so
`screencap` returns **2560x1440** while `wm size` reports native portrait `1440x2560`. `input
tap` uses the landscape space — screenshot first and compute coordinates from the image, or
every tap misses.

**🔴 NOTHING ALERTED FOR 16½ HOURS, AND THE DASHBOARD LIED.** `birdcatraz-watchdog` watches
`farm-pi5` only and stayed green (135 clean ticks). Worse, Guardian's `/api/cameras` reported
`s7-cam online=true` while the capture layer logged its 2,830th consecutive failure — **do not
trust that `online` flag to mean a camera is working.** Both are open follow-ups. Full detail:
[`docs/25-Aug-2026-s7-guest-network-incident.md`](docs/25-Aug-2026-s7-guest-network-incident.md).

## A Reolink camera is serving nothing but "snapshot returned None" — CHECK THE PORTS FIRST

**Do not trust the log line `not found on LAN by name — camera is probably powered off`. It is
wrong more often than it is right.** It only means discovery's *name sweep* missed the camera; it
says nothing about whether the camera answers at its configured IP. On 07-Aug-2026 it claimed
`duo2` was powered off while all four Reolink ports were open and the camera was serving 3.9 MB
4K JPEGs to curl. Probe before you believe it:

```bash
for p in 80 554 8000 9000; do nc -z -w 2 192.168.0.155 $p && echo "$p OPEN"; done
```

**Ports open + no frames = Guardian's problem, not the camera's.** The cause is almost certainly
that a failed startup connect still registered the snapshot poller: `take_snapshot` then returns
`None` forever **with no log line at all**, and the 300s re-scan never retries it because the
camera already counts as `active`. Fix is a plain restart:

```bash
launchctl kickstart -k gui/$(id -u)/com.farmguardian.guardian
```

Verify by **ordering**, not by the absence of warnings — `Connected to camera '<id>'` must appear
*before* `registered in snapshot mode`. Also note `Async camera operation failed: ` with an empty
message always means a `concurrent.futures.TimeoutError` off the 10s cap in `_run_async`.

**✅ FIXED in v2.66.0 (07-Aug-2026)** — a camera whose connect fails is no longer registered, so
the 300s re-scan retries it and it self-heals ~5 minutes after the camera is genuinely back. If
you still see this shape, the fix regressed; restart as above and check
[`docs/07-Aug-2026-duo2-failed-reconnect-incident.md`](docs/07-Aug-2026-duo2-failed-reconnect-incident.md).

Note `com.farmguardian.guardian-restart.plist` kickstarts Guardian **nightly at 03:00**. That is
how a brief outage got latched permanently; v2.66.0 removes the sting, but nobody has written down
why the nightly restart exists at all.

Same doc carries a latent trap: **duo2's password is NOT the one in `.env` — do not "sanitize" its
`config.json` entry to the placeholder**, or the overlay will stamp house-yard's password onto it
and kill the camera. (Correctness, not secrecy — these are chicken cameras and Boss is explicit
that plaintext passwords in this repo are fine. Don't raise it as a security issue.)

## ✅ MEASURED 10-Aug-2026 — the new VLM prompt is faster and the bantam priming is gone

This was the standing "first thing to do this session" item (unmeasured since 06-Aug because
the S7 was off charging at the time it was written). It's now been run for real against live
`data/guardian.db`, reported here instead of the command being handed to Boss unread.

**Speed — meaningfully faster.** The old prompt (hash `sha256:7be43bfba7e...`, matching the
documented cutover point) averages **5,767 ms** over 666 rows spanning 06-Aug 13:33–16:55 (the
banner's own 5,509 ms baseline was a slightly narrower window — same ballpark). The prompt/
schema has actually been touched **multiple times since 06-Aug** (v2.67.0/v2.68.0/v2.70.0), so
there are now 4 distinct post-cutover hashes rather than the single "new prompt" the banner
anticipated — every one of them averages **4,230–4,772 ms**, a consistent **15–25% drop**
across all of them, not just one lucky sample.

**Quality — both of Boss's complaints are fixed.** Zero of the last 3,557 s7-cam frames (since
08-Aug) mention "bantam" in `caption_draft` or `share_reason` — the old priming is gone; recent
labels are varied and specific ("dark-feathered turkey," "grey hen with white-tipped feathers,"
"rooster... red comb"). Captions are short, factual single sentences, not essays. `share_worth`
mix over the same window: skip 2,250 ≫ decent 768 > strong 539 — skip still dominates (no
flooding risk), though the order is `decent > strong` rather than the banner's expected
`strong > decent`; not investigated further, flag to Boss if it feels off in practice.

**No rollback needed.** Prompt file: `tools/pipeline/prompt.md.full-20260806` remains the
rollback target if ever needed (`cp` it back + `launchctl kickstart -k
gui/$(id -u)/com.farmguardian.pipeline`); all 22 output fields are still enforced by
server-side grammar sampling regardless of prompt wording. **Rationale pointer was broken and
is now fixed:** the original banner cited "CHANGELOG v2.64.0," which does not exist — the
version sequence jumps v2.63.3 → v2.65.0 (both 06-Aug-2026, confirmed by grep). The prompt cut
was never given its own CHANGELOG header; treat the 06-Aug-2026 date and this measurement as
the record of record instead of chasing a v2.64.0 entry that isn't there.

---

## First thing to read if you are new here

**[`docs/HOW_IT_ALL_FITS.md`](docs/HOW_IT_ALL_FITS.md)** — 10,000-ft view of where every photo on this Mac Mini lives, how the tagging pipeline works, how images land on Instagram `@pawel_and_pawleen`, and where the secrets actually live (reference only, never committed). If someone asks "how does this all fit together," that's the document that answers them.

**[`docs/SOCIAL_MEDIA_MAP.md`](docs/SOCIAL_MEDIA_MAP.md)** — every social-media surface the farm publishes to or reads from, the code path, the LaunchAgent, the cadence, the trust signal. Read this any time someone asks "where does X get posted from?" or "why did Y end up on Instagram/Facebook/Nextdoor?" Single source of truth — do not fork into other docs.

**S7 social exception (rebuilt 28-Jul-2026):** S7 Reels run separately from the mixed Reel, in two lanes, and neither requires final approval. Both send a Discord notice mentioning Mark as `<@293569238386606080>`. Mark's Discord user ID is `293569238386606080`.

- **`com.farmguardian.ig-s7-daily-reel` — daily 21:00 local, dawn-to-dusk.** Covers **ONE local calendar day**, not a rolling window. ⚠️ **Do not "restore" the 12:00 noon slot on the strength of the 22-Jul-2026 CHANGELOG entry.** That entry moved the lane to noon to stop reels straddling two days — but the hour was never the cause. A rolling 24h look-back at *any* hour splices the back half of yesterday onto the front half of today; noon just relabelled the problem. 21:00 + a single-day window is what actually fixes it, and reverting the hour without also reverting the window would simply reintroduce the straddle. Frames that got a Discord reaction are **guaranteed** into the reel and hold ~1.8s on screen vs 1.0s for the rest; un-reacted frames still carry the bulk of the reel by design (`s7_daily_reel_require_source_reactions` stays `false`). A run that slips past midnight builds the *previous* day rather than silently producing nothing.
- **`com.farmguardian.ig-s7-weekly-gems-reel` — Sundays 10:30 local.** The week's best Discord-reacted `s7-cam` gems. **This replaced the `ig-s7-backlog-reel` lane (09/13/17/20 daily), whose backlog is finished** — 1,746 gems consumed, and it was firing on an irregular 1–2 day cadence over near-fresh material. It is a **7-day window, not a queue**: eligible gems arrive at ~112/week, so any oldest-first drain below that rate would silently re-grow the backlog. Surplus gems age out of the window instead of accumulating. The consumption marker string is still `used-in-backlog-reel` — **do not rename it**, 1,746 historical rows carry it and renaming re-exposes every one.

Full detail: [`docs/28-Jul-2026-s7-dawn-to-dusk-reel-plan.md`](docs/28-Jul-2026-s7-dawn-to-dusk-reel-plan.md) and CHANGELOG v2.54.0.

## Related Repositories

This project is part of a two-repo system:

- **[farm-guardian](https://github.com/VoynichLabs/farm-guardian)** (this repo) — Python backend: camera discovery, YOLO detection, deterrence, visit tracking, alerts, REST API, local dashboard. Runs on the Mac Mini.
- **[farm-2026](https://github.com/VoynichLabs/farm-2026)** — Next.js public website at [farm.markbarney.net](https://farm.markbarney.net). Embeds live Guardian camera feeds and detection data via the Cloudflare tunnel at `guardian.markbarney.net`. Deployed on Railway.

The website's Guardian components (`app/components/guardian/`) consume this repo's REST API. Changes to API response shapes in `api.py` or `dashboard.py` must be coordinated with the TypeScript types in `farm-2026/app/components/guardian/types.ts`.
# Mark's Coding Standards
These should be present in the CLAUDE.md file and the agents.md file. 

## Non-negotiables

- No guessing: for unfamiliar or recently changed libraries/frameworks, locate and read docs (or ask for docs) before coding.
- Quality over speed: slow down, think, and get a plan approved before implementation.
- Production-only: no mocks, stubs, placeholders, fake data, or simulated logic shipped in final code.
- SRP/DRY: enforce single responsibility and avoid duplication; search for existing utilities/components before adding new ones.
- Real integration: assume env vars/secrets/external APIs are healthy; if something breaks, treat it as an integration/logic bug to fix.

## Workflow (how work should be done)
1. Deep analysis: understand existing architecture and reuse opportunities before touching code.
2. Plan architecture: define responsibilities and reuse decisions clearly before implementation.
3. Implement modularly: build small, focused modules/components and compose from existing patterns.
4. Verify integration: validate with real services and real flows (no scaffolding).

## Plans (required)
- Create a plan doc in `docs/` named `{DD-Mon-YYYY}-{goal}-plan.md` before substantive edits.
- Plan content must include:
  - Scope: what is in and out.
  - Architecture: responsibilities, modules to reuse, and where new code will live.
  - TODOs: ordered steps, including verification steps.
  - Docs/Changelog touchpoints: what will be updated if behavior changes.
- Seek approval on the plan before implementing.

## File headers (required for TS/JS/Py)
- Every TypeScript, JavaScript, or Python file you create or edit must start with:

  ```
  Author: {Your Model Name}
  Date: {timestamp}
  PURPOSE: Verbose details about functionality, integration points, dependencies
  SRP/DRY check: Pass/Fail - did you verify existing functionality?
  ```

- If you touch a file, update its header metadata.
- Do not add this header to file types that cannot support comments (e.g., JSON, SQL migrations).

## Code quality expectations
- Naming: meaningful names; avoid one-letter variables except tight loops.
- Error handling: exhaustive, user-safe errors; handle failure modes explicitly.
- Comments: explain non-obvious logic and integration boundaries inline (especially streaming and external API glue).
- Reuse: prefer shared helpers and `shadcn/ui` components over custom one-offs.
- Architecture discipline: prefer repositories/services patterns over raw SQL or one-off DB calls.
- Pragmatism: fix root causes; avoid unrelated refactors and avoid over-engineering and under engineering.

## UI/UX expectations (especially streaming)
- State transitions must be clear: when an action starts, collapse/disable prior controls and reveal live streaming states.
- Avoid clutter: do not render huge static lists or "everything at once" views.
- Streaming: keep streams visible until the user confirms they have read them.
- Design: avoid "AI slop" (default fonts, random gradients, over-rounding). Make deliberate typography, color, and motion choices.

## Docs, changelog, and version control
- Any behavior change requires:
  - Updating relevant docs.
  - Updating the top entry of `CHANGELOG.md` (SemVer; what/why/how; include author/model name).
- Commits: do not commit unless explicitly requested; when asked, use descriptive commit messages and follow user instructions exactly.
- Keep technical depth in docs/changelog rather than dumping it into chat.

## Communication style
- Keep responses tight and non-jargony; do not dump chain-of-thought.
- Ask only essential questions after consulting docs first.
- Mention when a web search could surface important, up-to-date information.
- Call out when docs/plans are unclear (and what you checked).
- Pause on errors, think, then request input if truly needed.
- Do not dump details into chat; keep them in docs/changelog.
- What you say to the user in your reply, "Will be forgotten almost instantly." If it is important, it needs to be in the documentation and your commit messages. 
- End completed tasks with "done" (or "next" if awaiting instructions).


## LM Studio — LOAD-BEARING PRODUCTION DEPENDENCY, READ BEFORE TOUCHING

This Mac Mini runs LM Studio (`http://localhost:1234`). **LM Studio is a
production dependency of the Farm Guardian image pipeline.** The pipeline
LaunchAgent (`com.farmguardian.pipeline`) calls LM Studio every camera
cycle for every enabled camera — `tools/pipeline/vlm_enricher.py` POSTs
each captured frame to `/v1/chat/completions` against the current VLM
(**qwen/qwen3-vl-4b** since v2.44.3, 01-Jul-2026 — verified loaded
2026-07-22. It was chosen for speed to keep up with the S7 cadence; its
looser `share_worth` judgement is a known, accepted trade-off. An earlier
qwen3.5-9b era ended with v2.44.3 — do NOT "restore" qwen3.5-9b on the
strength of the older CHANGELOG v2.40.19 note.
The pipeline loads it itself at 16k context via
`vlm_enricher.ensure_model_loaded` at daemon startup, so it survives a
reboot) to produce `share_worth`,
`caption_draft`, and the rest of the structured output that drives the
Discord gem lane, the IG gem-reaction pipeline, and the FB cross-post.
**If LM Studio goes down, the whole gem/caption/curation stack stops
producing.**

**Do NOT suggest "freeing resources" by quitting LM Studio.** It looks
like an idle GUI app holding memory but it is the VLM backend for every
active camera. The correct lever for memory pressure is model choice /
context-length reduction / unloading stale co-resident models inside
LM Studio, never quitting the app.

**⚠️ CORRECTED 25-Jul-2026 (v2.53.0): Guardian NOW CALLS LM STUDIO TOO.**
The old rule below — "Guardian = detection, no LM Studio" — is no longer
true and is kept only so the correction is legible. `llm_verify.py` sends
borderline night predator detections to the same loaded `qwen/qwen3-vl-4b`
for a second opinion before an alert fires. Guardian is a **read-only**
consumer: loaded-model check before every call, **never** loads a model,
single in-flight via a module lock. `ensure_model_loaded()` in the pipeline
remains this repo's ONLY load path. See the "Night predator alerts" section
near the top of this file.

`vision.py` was removed in v2.17.0 because over-engineered species
refinement wasn't worth the operational complexity — that is still true,
and v2.53.0 did not bring it back. The verifier answers one yes/no
question about a single bbox; it does not classify species.

Before you write or modify ANY code that opens a connection to LM
Studio, read **`docs/13-Apr-2026-lm-studio-reference.md`** in full.
That doc covers the API, the safe model-load pattern, the locally
available models, and the 2026-04-13 watchdog incident that took the
whole machine down because the previous `vision.py` raced a research
sweep on the same model. The hard rules:

1. Never call `/api/v1/models/load` without first checking what's
   loaded (instances stack — loading the same model twice doubles
   memory).
2. Always pass `context_length` on load (default is the model's max,
   which can be 131k+ tokens and reserves gigabytes of KV cache).
3. Never call `/v1/chat/completions` against a model name that isn't
   already loaded — that endpoint **silently auto-loads** the model,
   which is what crashed the box on 2026-04-13.

The brooder narrator plan
(`docs/13-Apr-2026-brooder-vlm-narrator-plan.md`) is the canonical
example of how to call LM Studio safely from a Guardian-adjacent
tool. Use it as the template for any new integration.

## Night predator alerts — READ THIS BEFORE TOUCHING DETECTION THRESHOLDS

**If `duo2` (or any IR camera) is firing `person` alerts at night with the box hugging the frame edge, it is a SPIDER WEB ON THE LENS.** Physically confirmed by Boss 25-Jul-2026: fine strands strung from the camera housing bridge to the lens glass. Anchored on the bridge, a strand sits directly in front of the IR LEDs *and* millimetres from the glass, so it takes the illuminator side-on at full power while being hopelessly out of focus — clipping to a fat white vertical bar exactly where YOLO wants to see a person. That geometry is also why every false positive hugs the frame border. It only happens after dark because that is when the illuminator is on, and spiders rebuild nightly because insects gather at the IR glow. **The fix is a microfiber cloth, not code.** Expect to redo it every couple of weeks in summer.

**DO NOT raise the detection threshold. Boss has rejected this twice.** v2.52.1 already reverted one such bump (`person` 0.85 → 0.70). A raised threshold also raises the floor on real threats — it is the blunt instrument this whole subsystem exists to replace.

**DO NOT put a paid or remote API on the alert path, and do not add a config option for one.** v2.52.1 pointed the verifier at a metered vision API. It made 3,813 calls in two nights, ran the balance to zero at 00:02:20 on 25-Jul-2026, then returned `402 Payment Required` 1,147 times in a row. Because verification is fail-open, the alarm silently degraded to "alert on everything" and posted 139 Discord alerts overnight. `llm_verify.py` now talks to `http://localhost:1234` and nothing else, and holds no key-reading logic to re-point. **If you find yourself shopping for a hosted vision endpoint from inside this repo, you have already taken a wrong turn** — `qwen/qwen3-vl-4b` is loaded on this Mini, free, and answers in ~1.2s.

**Guardian DOES call LM Studio as of v2.53.0.** This corrects the long-standing "Guardian = detection, no LM Studio; pipeline = VLM enrichment" split stated in the LM Studio section below — that line is now wrong for Guardian. Guardian is a strictly **read-only** consumer: it checks `/v1/models` before every call, **never** loads a model (the pipeline's `ensure_model_loaded()` at daemon startup remains this repo's only load path), and holds a module-level lock so it is single-in-flight, because both processes share one LM Studio.

The alert path is four gates, cheapest first — alert-cooldown pre-check → static-region artifact filter (`artifact_filter.py`) → local VLM (`llm_verify.py`) → graduated fail-open (⚠️ UNVERIFIED alerts + a health notice, never silence). Replayed against the real 25-Jul night: 136 alerts → **0**, with a real person walking across house-yard still alerting. Verify changes with `scripts/replay-artifact-filter.py --with-vlm` before shipping; **the house-yard 21:44 real-person case is the regression test that matters** — if it goes quiet, the change is wrong no matter how good the duo2 numbers look. Full detail: `docs/25-Jul-2026-night-alert-artifact-suppression-plan.md` and CHANGELOG v2.53.0.

## Heat-lamp orange cast — READ THIS BEFORE "FIXING" THE BROODER COLOR

Every 1–2 weeks an agent sees orange / red brooder frames on `usb-cam`, `mba-cam`, or `s7-cam` and reaches for a new WB algorithm. Boss has been through this loop 4–5 times. Stop. Read **`docs/16-Apr-2026-heat-lamp-orange-cast-investigation.md`** first. It covers: (a) the gray-world + orange-desat code that already exists in `tools/usb-cam-host/usb_cam_host.py` and the S7 `http_startup_gets` settings, (b) the real root cause (**sensor exposure clipping, not WB**), (c) pre-buried wrong theories, (d) the fix path that actually works (exposure control), (e) recovery recipes for S7 settings regression and MBA stale-code drift.

## A Reolink that "died" — SUSPECT THE STOCK POWER ADAPTER FIRST

**The power adapters Reolink ships are unreliable and have now failed twice on this farm** —
once on another Reolink a few weeks before 30-Jul-2026, and again on `duo2` on 30-Jul-2026.
Both times the camera was **fine**; both times a third-party adapter fixed it instantly and
permanently. Before you diagnose anything else, swap the adapter.

**The signature** (from the 30-Jul-2026 duo2 case):

- The camera goes **completely absent from the network** — no ICMP, no ARP, all ports closed,
  nothing on a full `/24` sweep, no fresh DHCP lease. It looks unambiguously dead.
- It is also **invisible in the Reolink phone app**, because the app reaches it through
  Reolink's cloud, so that adds no information.
- **Repeated power cycling at the wall does nothing** — and this is the trap.
- Frames were **pristine right up to the instant it stopped**: full-size, unique, every cycle,
  no packet loss or soft frames beforehand. A degrading radio or a dying sensor does not look
  like this. A power cut does.

**⛔ Do NOT conclude "dead unit, start a warranty claim" from failed power cycles.** That
inference is invalid and an agent made it on 30-Jul-2026, telling Boss he was out the money for
the camera. **Cycling the outlet tests nothing when the break is downstream of it** — in the
cord or the adapter — so every failed cycle reads as more confirmation while the test never
actually runs. Absence from the network proves absence from the network; it does **not**
distinguish a dead camera from an unpowered one. Confirm power independently (status LED, IR
illuminators glowing after dark, or simply a known-good adapter) before calling anything dead.

Note the failure can present as **two separate faults days apart** — duo2 lost its cloud/P2P
path on 27-Jul while serving flawless local video until 05:04:26 on 30-Jul. Do not assume one
progressive hardware failure; check the adapter.

Full case: [`docs/30-Jul-2026-reolink-s7-offline-incident.md`](docs/30-Jul-2026-reolink-s7-offline-incident.md).

## A MacBook Air camera is offline — 30-SECOND TRIAGE, DO THIS BEFORE ANYTHING ELSE

The three cameras on the MacBook Air (`macbook-air-facetime` :8089, `usb-webcam-1080p` :8090,
`jieli-dashcam` :8091) drop out regularly. **There are two completely different causes and
one field tells them apart.** Getting this wrong costs either a pointless drive to the farm
or an 11-hour outage. Ask the health endpoint:

```bash
for p in 8089 8090 8091; do echo "== :$p"; curl -s --max-time 5 http://192.168.0.50:$p/health; echo; done
```

| `acquire_stalled_s` | Means | Do |
|---|---|---|
| **climbing** (non-zero, rising) | Camera is **present and fine**; the service process is wedged | **Nothing.** It exits and restarts itself at 300s. `launchctl kickstart -k gui/$(id -u)/com.farmguardian.cam-<name>` if you're impatient. **Do not go to the farm.** |
| **`0.0`** while the camera is missing | Camera is genuinely **off the USB bus** | Hands-on. Replug. No software can fix this and a restart will not help. |

**⛔ Do NOT diagnose a wedged service as dead hardware.** On 04-Aug-2026 `macbook-air-facetime`
was down 11 hours. Only the first 8h35m was the camera being absent — for the last **2h42m**
it was present and working while the service couldn't take it. A fresh process on the same
machine read full 1280x720 from it at the same moment. The old log line said *"device is not
currently plugged in"* the whole time, which was flatly untrue and is exactly what sends
someone out to check cables. That message is fixed and the self-restart (v2.61.0) exists so
this can't cost 11 hours again.

**⛔ Do NOT "fix" a service that restarts itself.** That is the feature. If one restarts every
~5 minutes the fault is NOT in-process — that's the hub, go look at hardware. And do not
remove the `os._exit(1)`: `sys.exit` there would unwind only the grabber thread and leave the
web server up serving a camera-less 503 forever, which is the exact silent failure it fixes.

**⛔ The root cause of these dropouts is a POWER problem and cannot be fixed in code.** The
bus-powered USB hub on the Air supplies 500 mA; the dashcam alone wants 500 mA and the USB
webcam another 100. Whichever camera comes up second loses. Three cameras have been lost to
it in four days (dashcam 01-Aug, USB webcam 02-Aug, and on 04-Aug the **built-in** FaceTime,
which is on the same USB controller and no longer gets a pass). **An externally powered hub
is the fix, was expected to arrive 04-Aug-2026, and any recurrence should start by checking
whether it was actually fitted.** Do not add retries, thresholds, or config options to work
around it. Full evidence in `HARDWARE_INVENTORY.md`; self-heal detail in
[`docs/04-Aug-2026-camera-host-stall-self-heal-plan.md`](docs/04-Aug-2026-camera-host-stall-self-heal-plan.md) and CHANGELOG v2.61.0.

**Two traps specific to these three cameras:** the dashcam always needs a physical replug
after it loses power (it does not come back on its own), and **resolution cannot tell the
dashcam and the built-in apart** — both are 1280x720. To check which camera you're looking
at, pull `/photo.jpg` and *look at it*. :8089 is the run, :8091 is the wide garden view.

**⛔ Restart the Air's camera services ONE AT A TIME, never together.** On 04-Aug-2026 all
three restarted during the powered-hub swap and **all three resolved to the same cv2 index** —
every one of them served the built-in FaceTime camera for 23 minutes under three different
names. The v2.57.0 identity gate only checks the *relative* margin between the best and
second-best candidate; when a sibling already holds the other index there is no second
candidate, the check is skipped, and a scene mismatch of **37.1** gets accepted (a true match
scores **0.4**). That hole is **not yet fixed** — see
[`docs/04-Aug-2026-camera-identity-collision-incident-and-fix-plan.md`](docs/04-Aug-2026-camera-identity-collision-incident-and-fix-plan.md).

**To check for a collision, the byte-hash test is ONE-WAY — matching hashes prove a collision,
NON-matching hashes prove NOTHING.** Fetch `/photo.jpg` from every endpoint **concurrently** and
hash them; any two matching means two services on one camera. But **each service runs its own
independent grabber loop, so two services on ONE lens still capture at different instants and
never byte-match.** An earlier version of this note said to use the hash test instead of looking
at framing; that advice produced a **false negative** on 05-Aug-2026 and nearly cleared a live
collision. Framing similarity is weak evidence *for* a collision, but two frames that are
pixel-identical in geometry are one camera, full stop — cameras yards apart cannot align.

**The definitive test is ground truth on the host, selecting by NAME rather than index:**

```bash
ssh markb@192.168.0.50 '/Users/markb/.local/bin/ffmpeg -hide_banner -loglevel error \
  -f avfoundation -framerate 30 -video_size 1280x720 -i "FaceTime HD Camera" \
  -frames:v 1 -y /tmp/gt.jpg </dev/null'
```

Then compare that against what the endpoint serves. **Give ffmpeg a hard kill — it can hang on a
wedged camera and hold it open, and an orphaned probe is itself enough to cause the next
collision** (it removes a candidate, and single-candidate resolution is the bug). Check
`pgrep -fl ffmpeg` before and after; the `timeout` binary does not exist on the Air.

And do not trust `/health`'s `resolved_device_name` — it is recorded once at startup and never
re-checked (it reported `USB CAMERA #4` for a camera that had physically left the machine).

**Reading the resolution log is faster than any of the above.** In
`~/.local/farm-services/usb-cam-host/<camera>.log`, a genuine match scores **≤1** with a distant
runner-up; the 05-Aug failure was `difference 37.8, next best n/a`. **`next best n/a` is the
tell** — it means only one candidate was openable, so the margin check was skipped entirely:

```
GOOD:  'USB PHY 2.0 #2' identified by picture -> cv2 index 0 (difference 0.7,  next best 30.9)
BAD:   'USB PHY 2.0 #2' identified by picture -> cv2 index 1 (difference 37.8, next best n/a)
```

**Fix is a plain service restart, once both cameras are actually openable** —
`launchctl kickstart -k gui/$(id -u)/com.farmguardian.cam-jieli-dashcam`. Restarting while a
camera is still wedged just re-runs the failure and re-latches onto the wrong one. Note the
dashcam plist sets `USB_CAM_START_DELAY=50`, so give it ~60s before judging the result.

**🟡 05-Aug-2026 06:30 EDT — recovered from an overnight outage; TWO cameras still down.**
Guardian itself was never at fault (`http://localhost:6530/` returned 200 throughout — the
dashboard looked empty only because its feeds were).

Overnight, `house-yard` and `duo2` both went off the LAN at the *same instant*
(2026-08-05T07:00Z / 03:00 EDT) and the MacBook Air went off at ~20:27 EDT 04-Aug. Boss
restored power/network. Recovery notes:

- `house-yard`, `s7-cam`, `macbook-air-facetime` came back on their own once the LAN returned.
- **`duo2` did NOT.** Its RTSP port 554 was open again but Guardian kept logging
  `snapshot returned None` (2,370 consecutive). **A `launchctl kickstart -k` of
  `com.farmguardian.guardian` fixed it instantly.** The HTTP-snapshot cameras self-heal after a
  network outage; the RTSP path (`CameraCapture`) holds a stale connection and needs the
  restart. Reach for this first when one RTSP camera is dead while the snapshot cameras are fine.

**Resolved same morning. Final state: `house-yard`, `duo2`, `macbook-air-facetime`,
`jieli-dashcam` all live.** Boss moved both USB cameras onto the MacBook Air's powered hub.

### ✅ RESOLVED 06-Aug-2026 (v2.63.2) — `usb-webcam-1080p` was never intermittent, it was `gain=0`

Everything below this line describes the pre-06-Aug hunt for the cause and is history — the
current roster table above already says "WORKING, and it was never broken; ignore older
intermittent/replug notes." Root cause, confirmed by daylight retest: the camera's V4L2 `gain`
control was pinned at **0** (default is 32), which blackens output on any host — that single
fact explains every black-frame symptom below on both GWTC and the MacBook Air. Fix:
`gain=32` in `/etc/farmcam/usb-webcam-1080p.env`, confirmed live in this session's audit
(10-Aug-2026: `farm-pi5.local:8090/health` reports `v4l2_ctrls:"gain=32,auto_exposure=3"`,
`camera_open:true`, healthy 1920x1080). The camera has since also moved off GWTC/the MacBook
Air entirely — see the roster table (`usb-webcam-1080p` now lives on `farm-pi5`, the Birdcatraz
Pi) — so the GWTC-specific recovery commands below no longer apply to anything live.

**Historical symptom writeup below (pre-06-Aug-2026), kept for reference only — do not act on
any of it, the fix above is the whole story.**

**It is NOT dead — an earlier note in this file called it a dead camera and that was an
overclaim, corrected by Boss 05-Aug-2026.** It served a clean 1920x1080 daylight frame on GWTC on
04-Aug and works some of the time. What it does is lose its **video** function and not get it
back, while the rest of the device keeps working. Symptoms seen across two machines and two
operating systems:

- **On GWTC (Windows):** enumerated as a camera, `camera_open: true`, grabs incrementing, and
  **every frame pure black** — `min=0 max=0 std=0` across 1920x1080, in full daylight. Survived
  `restart-usbcam.ps1` (counters reset 28557 → 242, frames still black).
- **On the MacBook Air (macOS):** present on the USB bus and drawing its requested 100 mA, and
  its **microphone enumerates fine in the AVFoundation *audio* list** — but its video interface
  never appears in the video list at all.

USB descriptor, power negotiation and audio all worked; only video dropped — which reads as a
cable/power/hang theory right up until the actual cause (`gain=0`) turned out to be neither.

**Note on reading `system_profiler SPUSBDataType` here — a trap fallen into along the way.** A
single physical USB 3 hub shows up as *two* logical hubs (a 5 Gb/s branch and a 480 Mb/s
branch, same `Location ID` prefix). The 2.0 branch reports `Current Available (mA): 500`
because 500 mA is the **USB 2.0 per-port spec ceiling**, not because the hub is bus-powered — a
self-powered hub reports the same number. Don't read "500" on the 2.0 branch as evidence of a
bus-powered hub.

### ✅ RESOLVED — the held dashcam reel lane was restored

`com.farmguardian.ig-jieli-dashcam-timelapse-reel` is loaded and live (confirmed in this
session's LaunchAgent audit, 10-Aug-2026 — no `.HELD` suffix on disk, firing daily 21:30 per
its `StartCalendarInterval`, matching the "dashcam reel 21:30" line in the live daily schedule
below). The 05-Aug-2026 hold (see prior CHANGELOG/incident docs for why it was held) is over;
no restore action needed.

**✅ CLOSED 05-Aug-2026 — the v2.61.0 self-heal is NOT cycling.** `macbook-air-facetime` opened
its camera exactly **once** on 05-Aug and `jieli-dashcam` 20 times across its whole log life, with
`acquire_stalled_s: 0.0` on both. (Don't be alarmed by a raw `grep -c` of
`camera opened successfully` — that counts the entire log history, not today. Date-filter it.)

**✅ DONE — Birdcatraz Pi migration complete (v2.62.0 bring-up 05-Aug-2026, v2.63.0 Linux camera
host 06-Aug-2026).** This section used to describe a coming plan and an open identity gap; both
are closed. `farm-pi5` (Raspberry Pi 5, Ethernet) is live, and a dedicated
`tools/camera-host-linux/camera_host.py` gives each camera **structural** identity via
serial-derived `/dev/v4l/by-id/` paths — no index, no name-matching, no picture-comparison
fallback at all, which is a stronger guarantee than either the macOS or Windows identity paths
this section originally asked for. Both `usb-webcam-1080p` and `jieli-dashcam` run there today,
confirmed healthy in this session's live audit (10-Aug-2026: both `/health` endpoints return
`ok:true`, `camera_open:true`). systemd (`Restart=always`) replaces the macOS watchdog stack for
these two cameras. See `docs/05-Aug-2026-birdcatraz-pi5-camera-host-architecture-plan.md` and
`docs/05-Aug-2026-birdcatraz-pi5-bringup-log.md`.

## Hardware Inventory — READ THIS BEFORE TOUCHING ANY CAMERA

The single source of truth for what every camera *is*, what device hosts it, where its frames flow, and the device-not-location naming rule with worked examples lives in **`HARDWARE_INVENTORY.md`** at the repo root. Read it before adding, renaming, or moving any camera. The frontend devs and the next backend agent both depend on it.

## Operational skills — read before working with the S7, Discord, Instagram, or Facebook

Runbooks capture the how-to for cross-agent operations on this repo. Any agent picking up S7, Discord, IG, or FB posting work should read the relevant one first rather than re-deriving it:

- **`docs/skills-farm-2026-discord-post.md`** — how to post a camera frame from Guardian to the `#farm-2026` Discord channel. Webhook wiring, channel ID, a copy-paste-ready `post.sh`, failure modes, what not to post. **No credentials in the doc** — the webhook URL lives in `.env` (gitignored).
- **`~/bubba-workspace/skills/farm-pi5-camera-host/SKILL.md`** — **READ THIS BEFORE TOUCHING THE BIRDCATRAZ PI OR ITS CAMERAS.** How to reach `farm-pi5`, the `by-id` identity model and why no fallback is permitted, systemd service management, adding a camera, the 30-second triage table, the one-source-frame proof that settles "are you processing the image?", the **IR-cut filter diagnosis** for a camera that is perfect at night and washed out by day, and the traps that have already cost time (`custom.toml` silently does nothing on this image — use `userconf.txt`; never build a `$6$` hash through a shell; a card-less Pi still answers TCP; orphaned `ffmpeg` probes hold cameras open).
- **`docs/skills-s7-adb-operations.md`** — **⚠️ CORRECTED 10-Aug-2026 (v2.70.0): ADB works again**
  on the replacement S7 handset (SM-G930V, healthy USB port) — see Camera 2 (s7-cam) below.
  Everything in this bullet describes the **retired** SM-G930F only. **PERMANENTLY INAPPLICABLE as of 2026-08-01 (was "currently" inapplicable since 2026-05-06) — for that phone.** ADB-via-GWTC was the recovery path on the 2026-05-02 → 2026-05-06 GWTC-USB setup. The phone then went back to a standalone wall brick, and on 2026-08-01 it moved to a Qi pad because water killed its micro-USB port for both power and data. Power-chain history: MBA-USB → standalone (2026-04-26) → GWTC-USB (2026-05-02) → standalone again (≤2026-05-06) → Qi pad (2026-08-01). **The runbook can no longer be revived by re-tethering — the port does not enumerate, so no USB host is possible on this phone at all.** In the meantime the on-phone failure-mode notes (IP Webcam on Configuration screen = server stopped, etc.) are still useful when walking to the phone. For "S7 not broadcasting" diagnosis in the current standalone configuration, use the watchdog log + `/status.json` probe path.
- **`docs/16-Apr-2026-s7-ipwebcam-frozen-incident.md`** — the incident post-mortem those two runbooks reference. 30-second human recovery recipe.
- **Instagram posting to `@pawel_and_pawleen`** — **READ FIRST: [`docs/20-Apr-2026-ig-scheduled-posting-architecture.md`](docs/20-Apr-2026-ig-scheduled-posting-architecture.md) plus [`docs/SOCIAL_MEDIA_MAP.md`](docs/SOCIAL_MEDIA_MAP.md).** Those docs describe how Instagram posting works right now — LaunchAgent-based publishing, Discord reactions as the mixed-lane quality gate, zero CLI for Boss, the explicit S7 exception, and the disabled throwback/on-this-day paths. Earlier plan docs are still correct for account voice / hashtag rules / history but don't describe the live flow.
  - **Current architecture (re-verified 10-Aug-2026 against the live plists — 29 loaded `com.farmguardian.*` LaunchAgents, cross-checked filename-vs-`launchctl list` with an exact 1:1 match):** `discord-reaction-sync` scrapes `#farm-2026` reactions into `image_archive.discord_reactions` every 30 min; `social-publisher` handles hourly reacted-gem stories only; archive/on-this-day fallback is DISABLED (`tools/social/config.json::archive_fallback_enabled=false`); per-cycle auto-posting is DEAD (`instagram.enabled=false`) — do not re-enable. Live daily schedule: **house-yard reel 09:00, carousel 12:30, duo2 reel 15:00, s7 dawn-to-dusk reel 21:00, **dashcam reel 21:30**, insights-fetch 23:30, chicken-daily-pick weekdays 09:30, nextdoor throwback (mostly no-op) 08:00, nextdoor today 18:30; weekly — s7 gems reel Sun 10:30, house-yard weekly time-lapse Sun 11:00, duo2 weekly time-lapse Sun 11:15, digest Sun 20:00; monthly (1st) — house-yard time-lapse 08:00, duo2 time-lapse 08:15.** The two weekly/monthly time-lapse pairs (added 03-Aug-2026, see [`docs/03-Aug-2026-multi-day-timelapse-reels-plan.md`](docs/03-Aug-2026-multi-day-timelapse-reels-plan.md)) are **daylight-hours-only**, unlike the all-hours daily house-yard/duo2 reels — that's deliberate and does not contradict the all-hours directive on the daily lanes (different reel shape, see `docs/SOCIAL_MEDIA_MAP.md`'s shared-infrastructure notes). duo2's two new lanes have no historical backlog and will skip silently until ~7/~30 days of the new permanent keyframe capture have accrued. The 4×/day `s7-backlog` lane is retired (see the S7 exception above). **🔴 The 18:00 mixed `ig-daily-reel` lane is ALSO RETIRED (13-Aug-2026, per Boss) — do not re-enable it and do not "restore" it from an older schedule line.** It was reaction-gated with no filler, so its length equalled Boss's Discord tap count (down to 8 frames / 7 seconds), and since only `s7-cam` ever posts gems to Discord it was never actually mixed — just a shorter duplicate of the 21:00 S7 reel. Boss wants the 18:00 slot kept free for a smarter replacement. Rationale and the trap in `select_daily_reel_gems` (it windows on frame time, not reaction time): [`docs/SOCIAL_MEDIA_MAP.md`](docs/SOCIAL_MEDIA_MAP.md) and CHANGELOG v2.70.7. Every surviving reel lane **auto-publishes** (`approval_required=False` in `daily_reel_runner.py`) — the old "Boss must react to approve" gate is gone. Full lane table: [`docs/SOCIAL_MEDIA_MAP.md`](docs/SOCIAL_MEDIA_MAP.md).
  - **⚠️ A REACTION NEVER EXPIRES — do not add a time window to any reaction-gated lane (v2.71.1, 14-Aug-2026, per Boss).** Boss's reaction is a commitment to publish. Two lanes were discarding late reactions **silently**, and both are now fixed: `scripts/diary-promote-on-reaction.py` scanned Discord back a fixed 72h (react on day 4 → lost forever; **44 diary entries written, 3 ever promoted**, and the fix immediately recovered 3), and `select_daily_reel_gems` windowed on `ts` = when the **photo was taken**, not when Boss reacted. The correct pattern in this repo is **eligibility bounded by CONSUMPTION, not age** — `reel_posted_at IS NULL` / `ig_story_id IS NULL` / a remembered Discord message id — exactly like the zero-loss Story backstop below. **`select_s7_weekly_gems_reel_gems`' 7-day window is the documented exception and is CORRECT** — see its docstring's arrival-rate measurement before touching it. **If you write another stdlib Discord scanner, handle 429 by retrying the SAME page** (`_discord_get` in the promoter); retrying a later page silently skips history. Detail: [`docs/14-Aug-2026-reactions-never-expire-plan.md`](docs/14-Aug-2026-reactions-never-expire-plan.md).
  - **Zero-loss backstop:** `select_all_unposted_story_gems` in `tools/pipeline/ig_selection.py` has NO time window. If a gem got a Discord reaction but wasn't published (agent down, API error, whatever), the next hourly `social-publisher` tick picks it up. That's the whole point — Boss's reaction is a commitment to publish. Large backlogs drain at the configured social-publisher success cap (`tools/social/config.json`, currently 5/tick), with bounded look-ahead over failing old rows. Only local file/path-style permanent failures get marked `story-permanent-skip` so dead rows stop poisoning the FIFO queue; transient API/git errors remain retryable.
  - **Reacted Story priority invariant:** on-this-day/archive fallback stories are currently disabled. If they are redesigned later, they are allowed only when the reacted Story queue depth is zero. Do not use `gems_posted == 0` as a proxy for "queue empty"; failed gem attempts still mean the queue exists and must block archive fallback.
  - **IG publish quota — SHARED across every IG publishing lane, 25 per rolling 24h.** Instagram's Graph API hard-caps Business accounts at 25 `media` publishes per rolling 24-hour window, and this counts reacted stories, daily carousels, mixed Reels, S7 time-lapse Reels, and any future re-enabled on-this-day fallback stories. When you hit the cap, Graph 403s every subsequent container-status poll until the oldest publishes age past 24h. The publisher/reel runners detect the 403 and stop cleanly so the next tick resumes when a slot frees. **Do not "fix" the 403 by cranking up timeouts or retrying with new tokens — it's a hard quota, not an auth problem.** `scripts/pipeline-digest.py` reports this as rolling-24h usage, not local calendar-day usage.
  - **Quality gate:** Human reactions on Discord `#farm-2026` are the filter for mixed gem lanes. VLM `share_worth=strong, image_quality=sharp` is not sufficient by itself — it tagged a heat-lamp-orange-cast clipped frame as strong+sharp. Cross-reference from Discord message back to image_archive row is by `(camera_id, ts ±60s)` — NOT sha256 (Discord CDN re-encodes). Larry/Bubba/Egon are other Claude instances; their reactions don't count. Mark's Discord user ID is `293569238386606080`; mention format is `<@293569238386606080>`.
  - **Plan docs** (historical context, mostly stale on architecture — read the scheduled-posting doc above for what's actually running):
    - `docs/19-Apr-2026-instagram-posting-plan.md` — account voice / hashtag / framing rules (still canonical for content policy).
    - `docs/20-Apr-2026-ig-poster-implementation-plan.md` — V2.0 photo-only implementation history (CHANGELOG v2.29.0).
    - `docs/20-Apr-2026-ig-next-phases-plan.md` — spec for carousels/stories/reels.
    - `docs/20-Apr-2026-ig-phase-2-3-stories-reels-plan.md` — the v2.31.0 stories+reels implementation (partially superseded by the scheduled pivot a few hours later).
  - **Emergency CLI** `scripts/ig-post.py --mode {photo,story,reel}` still works for force-posts but is not used by any scheduled lane. Don't put it in automation.
  - **Tokens** live in macOS keychain + `/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env` (0600, gitignored). Never expire.
  - **Hard architectural gotcha:** IG's media fetcher rejects `guardian.markbarney.net/api/v1/images/gems/{id}/image?size=1920` because the URL must end in `.jpg`/`.png`/`.mp4`. Story posts now use the Mac Mini-backed Guardian route `https://guardian.markbarney.net/api/v1/images/story-assets/<name>.jpg`; feed/carousel/reel lanes still use the farm-2026 public media path.
  - **Content rules** (from the 19-Apr plan — still authoritative): Do NOT frame Guardian as a security/predator system. Stick to brooder/yorkie/flock/coop/yard-diary content. Hashtag library (`tools/pipeline/hashtags.yml`) has a runtime `forbidden` list that rejects `#markbarney*`, `#builtwithai`, `#aiassistedfarm`, etc. — don't re-introduce.
  - **Early real posts:** first carousel `https://www.instagram.com/p/DXVpa4Ek4Lb/`, second `https://www.instagram.com/p/DXVumJjExmr/` (caption has stale "watching for hawks" line — do not re-attempt that post). Reaction-gated carousel (v2.32.0) at `https://www.instagram.com/p/DXXUSxHE-dx/`.
- **Throwback/on-this-day archive lanes — DISABLED 2026-05-03.** Boss rejected the current throwback/on-this-day selection quality after irrelevant old winter photos polluted daily Reel material. `scripts/archive-throwback.py` exits unless `FARM_ARCHIVE_THROWBACK_ENABLED=1`; `scripts/on-this-day-stories.py` and direct `tools.on_this_day.post_daily --publish/--auto-story` paths exit unless `FARM_ON_THIS_DAY_STORIES_ENABLED=1`; `tools/social/config.json::archive_fallback_enabled=false` blocks social-publisher archive fallback; `tools/nextdoor/crosspost.py` refuses the `throwback` lane unless `FARM_NEXTDOOR_THROWBACK_ENABLED=1`; `scripts/discord-reaction-sync.py` ignores new `Archive` webhook drops. Future TODO: redesign as exact-date-only sourcing, e.g. May 3 2025 / May 3 2024 for May 3, with strict date provenance and better captions before re-enabling. Until then, there is enough live content and no throwback path should post.

- **Reciprocate harvester — DISABLED since 2026-04-23, last output `engagers-2026-04-23`.** The plist is renamed `com.farmguardian.reciprocate.plist.disabled` and is not loaded; nothing runs on a 4h cadence. The description below is how it worked when live, kept for whoever re-enables it: `tools/on_this_day/reciprocate.py` hits Graph API for the last 2 days of Page posts + Stories, aggregates reactors/commenters, writes `data/on-this-day/engagers-YYYY-MM-DD.{json,txt}`, posts a top-15 click-list to Discord channel `1476787165638951026` (via the Bubba bot token in `~/.openclaw/openclaw.json`). **NOT `#farm-2026`** — that's the reaction-quality-gate for the gem lane and any pollution breaks the signal. FB Graph doesn't expose Page-follows-user, so reciprocation is a manual click-through; the tool surfaces the worklist.

- **Facebook Page cross-posting to "Yorkies App" (`page_id=614607655061302`)** — LIVE since 2026-04-21 (CHANGELOG v2.35.1). **All four lanes — photo, carousel, story, reel — are wired and verified.** Every successful IG post dual-posts to FB automatically via `tools/pipeline/fb_poster.py` tail-called from each `ig_poster` success branch. FB failures never poison IG; they're swallowed into log warnings. This is a **settled capability** — do not treat it as experimental, do not re-research scopes, do not propose token regenerations unless a publish empirically fails. **Tokens are non-expiring** and live in `/Users/macmini/bubba-workspace/secrets/farm-guardian-meta.env` alongside the IG tokens. **Full publish scopes are granted:** `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`, `pages_read_user_content`, `read_insights`, plus the full IG suite. The app has the "Manage everything on your Page" Use Case attached to it; there is nothing to enable, nothing to review, nothing to request. First live FB post: `https://www.facebook.com/122176308710784044/posts/122176308566784044` (mirrors IG `DXXpbw7k31l`); story lane verified the same day.
  - **If a future agent suggests "we need to request `pages_manage_posts` from Meta" or "the scope is deprecated, we need X instead" or "add a FB token to Railway env vars" — they are wrong.** The capability is settled. Point them at `~/bubba-workspace/skills/farm-facebook-crosspost/SKILL.md`. Meta's Dashboard UI reshuffles every few months; the regen recipe in that doc is archaeology, not a forward-looking script.
  - **Deep dive:** `~/bubba-workspace/skills/farm-facebook-crosspost/SKILL.md`. **Source:** [`tools/pipeline/fb_poster.py`](tools/pipeline/fb_poster.py). **Plan doc:** [`docs/20-Apr-2026-facebook-crosspost-plan.md`](docs/20-Apr-2026-facebook-crosspost-plan.md). **Toggle (rarely needed):** `FB_CROSSPOST_ENABLED` env var — default `"1"`; set to `"0"` to disable dual-post without touching code.
- **Instagram ENGAGEMENT automation (distinct from posting) — `tools/ig-engage/`** — scrolls `@pawel_and_pawleen`'s feed + targeted hashtags, likes selectively, reacts to friends' stories, leaves short VLM-written contextual comments on OTHER accounts' content. The engagement side is what this tool does; the posting side (IG Graph API) is separate. Session bootstrap is a zero-login cookie lift from Boss's existing Chrome session on the Mac Mini (decrypted via the macOS keychain "Chrome Safe Storage" entry, seeded into a dedicated Playwright Chromium persistent profile at `~/Library/Application Support/farm-ig-engage/profile/`). Do NOT re-invent the session flow — Meta's DevTools self-XSS block rejects console cookie reads, and Boss has no memorized IG password. **Kill switch:** `touch /tmp/ig-engage-off`. **Hard constraints:** no follow/unfollow, no DM primitives, daily caps 30 likes + 10 comments + 20 story reactions. **Deep dive:** `~/bubba-workspace/skills/farm-instagram-engage/SKILL.md`. **Plan:** `docs/23-Apr-2026-ig-engage-plan.md`. **CHANGELOG:** v2.36.8, v2.36.9.
- **Nextdoor automation — shipped, not "in progress."** `tools/nextdoor/` — same architectural
  pattern as IG engagement, extended to Boss's Hampton CT neighborhood. **⚠️ CORRECTED
  10-Aug-2026: outbound cross-posting is not "one reaction-gated post per week, Sunday
  mornings"** — the original 23-Apr-2026 plan below describes intent, not what shipped. Live
  LaunchAgent `com.farmguardian.nextdoor-crosspost` fires **twice daily**, confirmed against
  the loaded plist (`StartCalendarInterval`: 08:00 and 18:30) and `scripts/nextdoor-crosspost.py`
  → `tools/nextdoor/crosspost.py`'s own header: **18:30 is the `today` lane** (1 reacted
  LIVE-CAM gem/day, per-lane daily cap via `budget.py`), **08:00 is the `throwback` lane**
  (disabled unless `FARM_NEXTDOOR_THROWBACK_ENABLED=1`, per the Throwback/on-this-day section
  above — so it fires but no-ops most days). Boss logs in via Apple Sign-In — 21 Nextdoor
  cookies including the 820-char `ndbr_idt` session JWT verified 2026-04-23 to decrypt cleanly
  with the IG bootstrap's exact crypto path. Shared cookie-decrypt module: `tools/chrome_session/decrypt.py`.
  **Kill switch:** `touch /tmp/nextdoor-off`. **Hard constraints:** no neighbor-request/friend
  automation, no DMs, 10 likes + 3 comments/day, audience floor "just my neighborhood" only.
  **Deep dive:** `~/bubba-workspace/skills/farm-nextdoor-engage/SKILL.md`. **Original plan
  (intent, not final shape):** `docs/23-Apr-2026-nextdoor-plan.md`.
- **Browser automation stack on this Mini — FOUR tools enabled, all of them always on the table** (2026-04-23, CHANGELOG v2.37.1):
  1. **Playwright + persistent profiles** — the workhorse. `farm-guardian/venv/`. Cookie lift via `tools/chrome_session/decrypt.py`; per-track profiles under `~/Library/Application Support/farm-*/profile/`. Powers the IG + Nextdoor tracks.
  2. **`playwright codegen` wrapper** — `tools/chrome_session/codegen.py --profile {ig|nextdoor}`. Attaches codegen to an already-bootstrapped profile so you're logged in when the recorder window opens. Add new profiles by extending the `PROFILES` dict.
  3. **`chrome-devtools` MCP** — user-scope registered in `~/.claude.json` as of 2026-04-23. `claude mcp list` confirms healthy. Appears as `mcp__chrome-devtools__*` tools in Claude Code sessions **after session restart**. For interactive DOM inspection / debug.
  4. **Claude-for-Chrome browser extension** — installed live in Boss's Chrome. For judgment-heavy one-shots. Hand off via briefs under `bubba-workspace/skills/<task>/claude-for-chrome-brief.md` (example: `farm-nextdoor-engage/claude-for-chrome-brief.md`).
  **Canonical index / which-tool-when:** `~/bubba-workspace/skills/browser-automation/SKILL.md`. When standing up a new browser-driven track, read that first.

## Project

Farm Guardian — a Python service that watches Reolink security cameras via ONVIF/RTSP, detects predator animals using YOLOv8, automates camera deterrents (spotlight/siren/PTZ), tracks animal visits in SQLite, generates daily intelligence reports, and serves a local web dashboard with REST API. Runs on a Mac Mini M4 Pro (64GB) on the same local network as the cameras.

## Commands

```bash
# Setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run
python guardian.py

# Run with debug logging
python guardian.py --debug
```

No test suite yet. This is a v2 production system (Phases 1-4 complete).

## Recent Changes (17-Apr-2026)

**Yard-diary capture — thrice-daily seasonal stockpile (17-Apr-2026):** `scripts/yard-diary-capture.py` (installed copy lives at `~/bin/yard-diary-capture.py` to dodge TCC on `~/Documents/`) is fired three times a day by `~/Library/LaunchAgents/com.farmguardian.yard-diary-capture.plist` at 07:00 / 12:00 / 16:00 local. Each run pulls a 4K snapshot from the Reolink `house-yard` via the existing `/api/v1/cameras/house-yard/snapshot` endpoint, writes the master to `data/yard-diary/{YYYY-MM-DD}-{morning|noon|evening}.jpg` (gitignored, kept indefinitely on the Mini), renders a 1920px copy with `DD-Mon-YYYY` burned into the lower-right via Pillow, publishes that copy to `farm-2026/public/photos/yard-diary/`, and commits+pushes farm-2026. **Purpose: raw stockpile for a year-end timelapse reel** (cherry bloom → summer green → autumn burn → snow), not curated daily site content. The `/yard` route on farm-2026 is a secondary surface — the primary artifact is the year-end ffmpeg assembly from the 4K masters. If you're about to stop the LaunchAgent, disable the captures, or delete masters, **don't** — the stockpile must keep accruing. Label prefix `com.farmguardian.*` is intentional and piggybacks on the known-working Guardian TCC grant family (see the "LaunchAgent relabeled" note below for the TCC-label-family principle). Plan: `docs/17-Apr-2026-yard-diary-capture-plan.md`. Log: `data/pipeline-logs/yard-diary.log`.

---

## Recent Changes (14-Apr-2026)

**Host-portable `usb-cam` (v2.26.0, 14-Apr-2026):** The generic USB webcam is no longer hardcoded to the Mac Mini. A new FastAPI snapshot service (`tools/usb-cam-host/usb_cam_host.py`) runs on whichever host the camera is physically plugged into and serves `GET /photo.jpg` + `GET /health` on port `8089`. Cross-platform via `cv2.VideoCapture(index)` (no backend flag — OpenCV auto-picks AVFoundation / dshow / V4L2). 15-frame warmup for AE/AWB convergence; no Laplacian burst ranking (Boss distrusts Laplacian-vs-GLM calibration). Guardian's `config.json` and `tools/pipeline/config.json` both flipped `usb-cam` to the HTTP path (`http_url` / `ip_webcam`); zero Guardian or pipeline code changed — reuses `HttpUrlSnapshotSource` (v2.24.0) and `capture_ip_webcam`. Deploy artifacts in `deploy/usb-cam-host/` (launchd plist for macOS, Shawl-wrappable `.bat` for Windows, install guides). Moving the camera later is: new host → install agent → change one URL in each config file. **Plan:** `docs/14-Apr-2026-portable-usb-cam-host-plan.md`. **System state snapshot (live now):** `docs/14-Apr-2026-system-state-snapshot.md`.

**Image archive REST surface (v2.25.0, 14-Apr-2026):** `/api/v1/images/*` — public `/gems`, `/recent`, `/stats`, `/gems/{id}`, `/gems/{id}/image`; private `/review/*` for promote/demote/flag/unflag/delete with an append-only `image_archive_edits` audit table. Public SQL always prefixes `WHERE has_concerns = 0`; public response models omit `concerns`/`has_concerns`/`vlm_json`; review endpoints 503 when `GUARDIAN_REVIEW_TOKEN` is unset. Lazy thumbnails cached under `data/cache/thumbs/`. Plan: `docs/14-Apr-2026-image-archive-api-plan.md`. Layer-2 follow-ups: `docs/14-Apr-2026-followups-post-layer1.md`. **farm-2026 frontend consumes this via the Cloudflare tunnel at `guardian.markbarney.net`.**

**LaunchAgent relabeled to `com.farmguardian.guardian` (16-Apr-2026, fixed).** The old label `com.farm.guardian` had been failing to spawn since the 14-Apr-2026 power outage with `posix_spawn ... Operation not permitted`. The previous writeup guessed the fix was **System Settings → Privacy & Security → App Management** re-approval — that was wrong. App Management does not expose launchd service entries and the venv Python binary is not listable there. Reboot also did not clear it. The real root cause: macOS TCC persists per-label denies; the `com.farm.guardian` label was permanently held in a denied state in the TCC database. The fix was to rename the Label in the plist (and the plist filename) to `com.farmguardian.guardian` — a fresh label carries no TCC history and spawns cleanly. **If a future agent relabel gets denied again, the surgical fix is another label rename, not App Management.** Plist path is now `~/Library/LaunchAgents/com.farmguardian.guardian.plist`. Logs redirected to `/tmp/guardian.out.log` + `/tmp/guardian.err.log` (matching the working `com.farmguardian.usb-cam-host` pattern). Guardian's own internal logger still writes to `guardian.log` in the project directory — that path is fine because the *process* can write there; it's only launchd's `StandardOutPath` redirect that sometimes ran into TCC. The service now starts automatically on boot — no more `nohup` workaround.

---

## Recent Changes (08-Apr-2026)

**Remote camera control API (v2.7.0):** Five new endpoints in `api.py` for full remote camera control over the Cloudflare tunnel: snapshot, position readback, zoom, autofocus, guard control. A remote Claude session can now control the camera from anywhere.

**Step-and-dwell patrol (v2.6.0):** Patrol rewritten. Camera steps through 11 positions at 30° intervals, dwells 8 seconds at each for clean stationary frames. Replaces continuous sweep that produced motion-blurred garbage.

**Cloudflare tunnel live:** Guardian dashboard exposed at `https://guardian.markbarney.net` via Cloudflare tunnel from the Mac Mini. No port forwarding needed.

**Preset save/recall API (v2.8.0):** Three new endpoints — list presets, save current position as preset, recall preset. Camera moves autonomously to saved position with no polling or overshoot. Bypasses reolink_aio validation to send raw `setPos` command.

**Four-camera config (v2.12.0)** — *historical; the fleet is seven cameras now and GWTC is disabled. See the Environment roster table.* GWTC laptop added as 4th camera. Gateway laptop (then `192.168.0.68`, now `.69`) streams its built-in webcam at 1280x720@15fps H.264 via ffmpeg + MediaMTX on port 8554. Uses `rtsp_url_override` — same pattern as the S7. Named `gwtc` (device name, not location). No code changes needed — config-only addition. All four cameras: house-yard (Reolink PTZ), s7-cam (Samsung S7 via IP Webcam RTSP), usb-cam (USB on Mac Mini), gwtc (Gateway laptop webcam via MediaMTX RTSP). Detection disabled on all except house-yard.

**Three-camera config (v2.11.0):** S7 phone restored (was only discharged, not dead). Cameras named by device, not location — locations change.

**USB camera support (v2.9.0):** USB camera added to Mac Mini. Config uses `"source": "usb"`, `"device_index": 0`. Capture, discovery, and guardian.py handle USB cameras via AVFoundation. 1920x1080, no network latency.

**TODO:**
- ~~**Save camera presets** — no presets exist yet.~~ **Done — five presets (0-4) are live on the camera.** Read them from `AGENTS_CAMERA.md` before touching preset slots; do NOT run the procedure in `docs/08-Apr-2026-preset-setup-plan.md`, which would overwrite three of them.

---

## Camera Control — Principles

**For all camera-specific technical details, API shapes, endpoints, and procedures, read `AGENTS_CAMERA.md`.** That file is the single source of truth for camera operations.

Durable rules:
- **Never suggest using the Reolink phone app.** We ARE the Reolink app. The camera is an HTTP server. Anything the app can do, we can do with raw JSON commands. If the `reolink_aio` library doesn't expose a feature, bypass it and call `host.send_setting()` directly.
- **Never declare something impossible without reading the full library source.** The `reolink_aio` library is ~5000 lines (`venv/lib/python3.11/site-packages/reolink_aio/api.py`). Skimming it will miss critical capabilities. Read the actual methods, the enums, the body construction. Check both the HTTP API and the Baichuan protocol module.
- **Never trust a GitHub issue as the final word.** An open issue saying "not supported" might mean the library hasn't wired it up, not that the camera can't do it. Verify against the actual firmware behavior.
- **Autofocus wait is non-negotiable.** After any camera movement, trigger autofocus and wait 3 seconds before taking a snapshot. Every blurry image in this project's history was caused by skipping this.
- **Zoom is out of scope.** Camera stays at zoom 0 (widest). Do not add zoom features.

## Architecture

Read `docs/02-Apr-2026-v2-system-plan.md` for the full v2 architecture document with module specifications.

**All plans live in `docs/`:**
- `docs/01-Apr-2026-v1-guardian-plan.md` — Original v1 plan
- `docs/02-Apr-2026-v2-system-plan.md` — Full v2 architecture spec (15 modules)
- `docs/02-Apr-2026-smart-devices-plan.md` — Smart plug deterrent integration (future)
- `docs/04-Apr-2026-full-cleanup-plan.md` — Stabilization & cleanup
- `docs/06-Apr-2026-sweep-patrol-plan.md` — Continuous sweep patrol design
- `docs/06-Apr-2026-s7-nesting-box-camera-setup.md` — S7 phone camera setup plan & findings
- `docs/06-Apr-2026-per-camera-rtsp-transport-plan.md` — Per-camera RTSP transport fix (TCP/UDP)
- `docs/08-Apr-2026-camera-setup-handoff.md` — Camera control handoff (April-2026 historical; its operational state is dead — `AGENTS_CAMERA.md` supersedes it)
- `docs/08-Apr-2026-absolute-ptz-investigation.md` — **READ THIS** — why absolute PTZ doesn't work, preset approach, speed calibration
- `docs/08-Apr-2026-remote-camera-api-plan.md` — Remote camera control API design (v2.7.0)
- `docs/08-Apr-2026-rtsp-substream-plan.md` — RTSP substream investigation
- `docs/08-Apr-2026-gwtc-webcam-stream-plan.md` — GWTC webcam stream plan
- `docs/17-Apr-2026-gwtc-windows-stabilization-plan.md` — 17-Apr-2026 Debian-wipe reversal record. Historical rationale only; superseded by the 18-Apr doc below.
- `docs/18-Apr-2026-gwtc-current-state-and-install-walkthrough.md` — **READ FIRST FOR GWTC.** Current live state (Windows autologon, `cam` account), the one Windows-Update landmine that breaks it, and the full interactive Debian install walkthrough (SD card is pre-staged in Boss's hands) for when the switch-trigger fires.
- `docs/13-Apr-2026-phase-a-reolink-snapshot-polling-plan.md` — **DONE in v2.18.0** — house-yard switched from RTSP to HTTP snapshot polling (4K JPEG)
- `docs/13-Apr-2026-phase-b-gwtc-snapshot-endpoint-plan.md` — Phase B: stand up an HTTP snapshot service on the Gateway laptop, switch `gwtc` over
- `docs/13-Apr-2026-phase-c-usb-highres-and-motion-bursts-plan.md` — Phase C: `usb-cam` to high-res snapshots + ONVIF motion-event-triggered snapshot bursts on house-yard. **C1 (USB high-res) is effectively delivered by v2.26.0 `usb-cam-host` via a different architecture (HTTP service instead of local AVFoundation adapter) — read the v2.26.0 plan alongside.** C2 (motion bursts) is still open.
- `docs/14-Apr-2026-portable-usb-cam-host-plan.md` — **DONE in v2.26.0** — host-portable `usb-cam` via `tools/usb-cam-host/` HTTP snapshot service; moves cleanly between Mini / MBA / GWTC / any host.
- `docs/14-Apr-2026-image-archive-api-plan.md` — **DONE in v2.25.0** — `/api/v1/images/*` REST surface over the image archive, powers farm-2026's gems/retrospective pages.
- `docs/14-Apr-2026-followups-post-layer1.md` — v2.25.0 layer-2 follow-up list for the frontend dev.
- `docs/14-Apr-2026-modularization-plan.md` — unexecuted proposal (Apr-2026); never approved, line counts stale.
- `docs/14-Apr-2026-audio-triggered-capture-plan.md` — planned: audio-triggered capture on `usb-cam`.
- `docs/14-Apr-2026-system-state-snapshot.md` — historical snapshot (14-Apr-2026), three months stale. For current state read this file plus `HARDWARE_INVENTORY.md`, `docs/SOCIAL_MEDIA_MAP.md`, and `docs/22-Jul-2026-mac-mini-ecosystem-audit.md`.
- `docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md` — **READ THIS BEFORE TROUBLESHOOTING THE GATEWAY LAPTOP.** Pre-buries four wrong theories and gives the 30-second diagnostic recipe that actually works.
- `docs/13-Apr-2026-lm-studio-reference.md` — **READ THIS** before adding any LM Studio integration. API surface, locally available models, safe model-load pattern, the 2026-04-13 watchdog incident and what we changed because of it.
- `docs/13-Apr-2026-brooder-vlm-narrator-plan.md` — planned standalone tool: sample brooder snapshots → glm-4.6v-flash → JSONL narrative log. Awaits Boss approval. Will be revised to incorporate "find the best image" rather than blind 5-min sampling.

**Entry point:** `guardian.py` — orchestrates all modules, runs as a foreground process.

**Modules (15 total):**

*Phase 1 — Core pipeline:*
- `discovery.py` — Scans local network for ONVIF cameras. Stores IPs and stream URLs.
- `capture.py` — Frame acquisition. Two parallel modes: (1) `CameraCapture` for RTSP streams (`gwtc`, `mba-cam`); (2) `CameraSnapshotPoller` + `SnapshotSource` adapters for HTTP-snapshot cameras (`house-yard` via `ReolinkSnapshotSource` since v2.18.0; `s7-cam` and **`usb-cam`** via `HttpUrlSnapshotSource` — the latter added in v2.24.0 for the S7 battery path, reused by `usb-cam` in v2.26.0 via the portable `usb-cam-host` service). `UsbSnapshotSource` still exists in the file for anyone who wants to reach AVFoundation directly, but no camera in `config.json` currently dispatches to it. Both modes produce `FrameResult`; the snapshot path also carries the original camera-encoded JPEG for zero-loss display.
- `detect.py` — Runs YOLOv8 inference on frames. Classifies objects. Returns detections with bounding boxes.
- `alerts.py` — Posts Discord messages with snapshots when predator-class animals are detected. Rate-limits alerts.
- `logger.py` — Writes events to SQLite database and legacy JSONL files. Saves snapshots.
- `dashboard.py` — FastAPI web dashboard + API host. Live feeds, PTZ controls, reports, settings. Accessible at `http://macmini:6530`.
- `static/index.html` + `static/app.js` — Dashboard frontend (Tailwind CSS, vanilla JS, no build step).

*Phase 2 — Intelligence foundation:*
- `database.py` — SQLite abstraction layer (8 tables). WAL mode for concurrent reads. Daily backups.
- ~~`vision.py`~~ — **Removed in v2.17.0.** GLM species refinement was over-engineered for this farm. YOLO's class label is now what flows to alerts. Boss directive: "just show me the picture, no classification."
- `tracker.py` — Groups individual detections into animal visit tracks. Used for alert dedup (one Discord post per visit, not one per frame).

*Phase 3 — Deterrence:*
- `camera_control.py` — Reolink camera hardware control via reolink_aio. PTZ move/stop, spotlight, siren, autofocus, guard control, snapshot, position readback. **Does NOT yet support preset save — needs `send_setting()` bypass (see Camera Control section above).**
- `patrol.py` — Step-and-dwell patrol (v2.6.0). 11 positions at 30° intervals, 8-second dwell at each. Replaces continuous sweep. Configurable via `ptz.sweep` in config.
- `deterrent.py` — Automated response engine. 4 escalation levels, per-species rules, cooldowns, effectiveness tracking.
- `ebird.py` — eBird API polling for regional raptor early warning. 30-min intervals during hawk hours.

*Phase 4 — Reporting:*
- `reports.py` — Daily intelligence reports. Species breakdown, deterrent stats, hourly heatmaps, 7-day trends. Exports JSON + Markdown.
- `api.py` — REST API at `/api/v1/`. Endpoints for detections, patterns, camera control, snapshot, position, zoom, autofocus, guard. Exposed via Cloudflare tunnel at `https://guardian.markbarney.net`.

**Config:** `config.json` (copied from `config.example.json`). Contains camera IPs, per-camera RTSP transport (`"tcp"`/`"udp"`), Discord webhook, detection thresholds, deterrent rules, PTZ presets, eBird API key, report settings.

**TWO SEPARATE CONFIG FILES — DO NOT FORGET THE SECOND ONE.** Guardian (the main service on port 6530) reads `config.json` at the repo root. The VLM image pipeline (the `com.farmguardian.pipeline` LaunchAgent) reads **a different file** at `tools/pipeline/config.json`. When a camera moves between hosts (e.g. usb-cam jumping from Mini → MBA 18-Apr-2026), or a port changes, you MUST update both files or the dashboard and the pipeline will diverge — one will see the camera, the other won't, and it looks like "the camera is partially online." Both files are TRACKED in git (corrected 06-Jul-2026 — this doc previously claimed they were gitignored; `.gitignore` deliberately commits them). **Corrected again 06-Aug-2026: the root `config.json` no longer contains the camera password.** It holds the literal placeholder `YOUR_CAMERA_PASSWORD`; the real one comes from `CAMERA_PASSWORD` in `.env`, overlaid at load time by `guardian.py:1113`. If you curl the camera directly, read it from `.env` — the config value will just get you `login failed / rspCode -7`. If you edit either one, commit the change. Grep both before declaring done: `grep -n 'http_base_url\|ip_webcam_base' config.json tools/pipeline/config.json`. After editing, reload **both** services: `launchctl kickstart -k gui/$(id -u)/com.farmguardian.guardian` AND `launchctl kickstart -k gui/$(id -u)/com.farmguardian.pipeline`.

**For NEW cameras (or removing/listing them), use `scripts/add-camera.py` instead of editing the JSON by hand.** The CLI does atomic writes across both files, refuses duplicates, probes the URL before committing, and detects existing drift via `scripts/add-camera.py list`. Examples + design rationale: `docs/19-Apr-2026-add-camera-cli.md`. Hand-editing is still fine for tweaking an existing camera's cadence/context, but for add/remove the CLI is the single point of truth — every previous agent who hand-edited has at some point forgotten the second file.

## Network & Machine Access — READ BEFORE TROUBLESHOOTING REACHABILITY

**Two docs are authoritative — read both before you theorize about why something is unreachable:**

- **`~/bubba-workspace/memory/reference/network.md`** — Bubba (this Mac Mini) keeps the master inventory of every machine on the LAN: IPs, MAC addresses (with one known error — see below), SSH keys, users, service ports, the router's admin creds, known quirks.
- **`docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md`** in this repo — full writeup of an afternoon spent misdiagnosing the Gateway laptop. Pre-buries the four wrong theories so you don't repeat them, and gives you the diagnostic recipe that actually works in 30 seconds.

The fast facts you cannot afford to be wrong about:

- **ICMP is blocked between wired and wireless on this router** (TP-Link Archer AX55). Mac Mini on Ethernet ↔ laptop on WiFi will never ping each other regardless of state. Use `nc -z -w 1 <ip> <port>` or direct `ssh`, never `ping`, to test reachability.
- **Windows Firewall is DISABLED on the Gateway laptop.** Don't invent firewall theories — there isn't one to block you. The machine was wiped before being repurposed and has no security suite installed.
- **GWTC off the LAN — WAIT 3 MINUTES BEFORE ESCALATING (verified 2026-04-21).** Two watchdogs now handle the vast majority of dropouts autonomously: `farmcam-watchdog` (ffmpeg dshow zombies) and `farmcam-wifi-watchdog` (adapter wedges — bounces `Restart-NetAdapter` every 2 min when gateway is unreachable). Verified 2026-04-21: `C:\farm-services\wifi-watchdog.log` showed 5 successful auto-recoveries on 2026-04-20 alone, each pulling GWTC back within ~25s. **If you see GWTC unreachable, wait 3 minutes and re-probe before proposing any physical intervention.** Catching a dropout mid-bounce looks identical to a hung state from the Mini side, but the watchdog will usually clear it. Only escalate if 5+ minutes of unreachability persists. Historical failure modes (still possible but rarer now) are below: (a) the Windows lock-screen pre-login WiFi gap, fixed by typing PIN `5196` on the coop USB keyboard (which is kept off so turkeys don't mash keys); (b) a fully hung state that only a hard power cycle clears (see `feedback_gwtc_hard_reboot_freely.md` — treat GWTC as disposable). Earlier writeups blaming WSL2 routing were a misdiagnosis — same symptom, different cause. **Nothing on the Mac Mini side can fix a machine that's not on the network**; SSH/scripted router logins/rerunning sweeps with different flags won't help. Wait, or walk to the coop.
- **GWTC reboots leave ffmpeg in a dshow zombie state — but a watchdog auto-recovers it.** After login, once services are up, the Shawl + ffmpeg + mediamtx services all report `Running`, ffmpeg has a live PID, port 8554 is open — but the `gwtc` RTSP path 404s because ffmpeg is wedged on the dshow camera open and never registers as a publisher. Neither Shawl's `--restart` policy nor the `:loop` retry in `start-camera.bat` triggers (the wedged ffmpeg never exits). **The `farmcam-watchdog` Windows service (deployed 13-Apr-2026) detects this within ~90s and kills the wedged ffmpeg PID; Shawl then respawns it cleanly. You should not need to intervene.** If the watchdog itself is broken (`sc query farmcam-watchdog` not `RUNNING`), fall back to manual recovery: `ssh markb@<gwtc-ip> 'tasklist | findstr ffmpeg'` then `taskkill /F /PID <pid>`. Watchdog code lives at `deploy/gwtc/farm-watchdog.ps1` with install recipe at `deploy/gwtc/install-watchdog.md`. Full failure-mode writeup: `docs/13-Apr-2026-gwtc-laptop-troubleshooting-incident.md` "Addendum -- Post-Reboot dshow Zombie Pattern" section.
- **GWTC WiFi adapter wedges under weak-signal dropouts — a separate watchdog auto-recovers it.** The coop sits at ~34% signal to the house AP, and the built-in Realtek 8723DU chipset sometimes hangs after a transient dropout (e.g., someone walks into the coop and their body blocks 2.4 GHz). Symptom: GWTC is fully off the LAN — ping returns `Host is down`, the router has no ARP entry, SSH/RTSP/Guardian frame all dead. The Windows desktop still looks normal on-screen. **The `farmcam-wifi-watchdog` scheduled task (deployed 19-Apr-2026) runs every 2 minutes as SYSTEM, pings the gateway 3× via `ping.exe -n 1 -w 2000`, and runs `Restart-NetAdapter -Name "Wi-Fi"` if all 3 fail.** It's a task (not a service) because `schtasks /SC MINUTE /MO 2` is simpler than a service supervisor for a 500-ms script. Script: `C:\farm-services\wifi-watchdog.ps1`. Log: `C:\farm-services\wifi-watchdog.log` (absence of log = watchdog has never tripped). This does NOT replace the dshow watchdog above — they cover different failure modes (network vs. camera). If GWTC is still unreachable for more than ~5 minutes, the watchdog either isn't running or the failure isn't adapter-level; fall back to a hard power cycle. **Do not** use `Test-Connection -TimeoutSeconds` in any Windows PowerShell 5.1 script on GWTC — that flag is PS 6+ only; use `ping.exe` instead. Full writeup: `docs/18-Apr-2026-gwtc-current-state-and-install-walkthrough.md` "WiFi dropout incident + watchdog" section.
- **GWTC MAC is `F0:35:75:81:2C:45`** (confirmed 2026-04-21 via ARP on the Mac Mini after a successful WiFi-watchdog recovery — GWTC came back on `.68` with that MAC, distinct from the Katana's `FC:6D:77:B8:E8:DB` at `.3`). The earlier "unknown — don't ARP-hunt" note in the network doc is now superseded.
- **GWTC does NOT run LM Studio.** Any prior memory or doc claiming GWTC has LM Studio on port 9099 is wrong — that was a cross-wired reference to a different machine. GWTC is a single-purpose chicken-coop camera streamer. Its only distinctive service signature is MediaMTX on port `8554`:
  ```bash
  for i in $(seq 2 254); do (timeout 2 bash -c ":</dev/tcp/192.168.0.$i/8554" 2>/dev/null && echo "192.168.0.$i has MediaMTX (= GWTC)") & done; wait
  ```
  If port 8554 isn't open anywhere on the /24, GWTC is off-network — **wait 3 minutes and re-probe before escalating** (the WiFi watchdog usually clears it autonomously; see the 3-minute-wait rule above).
- **SSH into GWTC** (once you've found its IP): `ssh -o StrictHostKeyChecking=no markb@<ip>` — Bubba's `id_ed25519` is in `C:\ProgramData\ssh\administrators_authorized_keys` on the laptop.
- **Router admin is read-only by default.** Never change router settings without Boss approval. Terry Kath rule: if you change something that kills connectivity to Bubba, you lose the ability to be told to undo it.

## Multi-Machine Claude Orchestration — USE THIS WHEN A TASK NEEDS HANDS ON ANOTHER BOX

**The default reflex of every agent should be: don't ask Boss to relay a task to another Claude. Spawn one yourself.**

The farm has multiple machines, several of which run Claude Code (Mac Mini "Bubba" — primary; MacBook Air at `192.168.0.50` — `c` alias installed; Windows laptops at `192.168.0.68`/`.194` etc.). Whenever something needs hands at another machine — granting a TCC permission, running a GUI app, reading a local file you can't `scp`, anything where being-on-that-box matters — **invoke a fresh headless Claude on that box over SSH**. Don't ask Boss to copy-paste your prompt into a session he's sitting in front of.

**The pattern (from the Mac Mini, targeting the MacBook Air):**

```bash
ssh -i ~/.ssh/id_ed25519 markb@192.168.0.50 'c -p "Granular task description here. Be self-contained — the remote Claude has no context from this conversation. Tell it what to do, what success looks like, and what to print on completion."'
```

- `c` is the alias on every machine for `claude --dangerously-skip-permissions` — already in `~/.zshrc`/`~/.bash_profile`/`~/.bashrc` on the Air, the Mini, and GWTC.
- `-p` (print mode) runs the prompt non-interactively, prints the result, exits. No TTY needed.
- The remote Claude runs **on that box's filesystem and GUI session** — it can spawn TCC prompts, open Finder, drive AppleScript, read files only on that disk. None of which the calling agent can do over plain SSH.

**Per-machine quick reference:**

| Box | SSH | Claude available? |
|---|---|---|
| Mac Mini (Bubba) | local — you're already here | yes (this is the orchestrator most of the time) |
| MacBook Air | `ssh -i ~/.ssh/id_ed25519 markb@192.168.0.50` | yes — `c` alias, OAuth-logged-in |
| **`farm-pi5`** (Birdcatraz camera host) | `ssh -i ~/.ssh/id_ed25519 markb@192.168.0.17` | not installed — bare OS as of 05-Aug-2026. Debian 13 trixie, Pi 5 4 GB. sudo needs the password (`echo 12345 \| sudo -S …`). **Both USB cameras live here now.** See [`docs/05-Aug-2026-birdcatraz-pi5-bringup-log.md`](docs/05-Aug-2026-birdcatraz-pi5-bringup-log.md) |
| Gateway laptop (GWTC) | `ssh -o StrictHostKeyChecking=no markb@192.168.0.69` (IP moved from `.68`; rediscover via /24 sweep on `:8554`) | yes — pinned `c.cmd`. **🔴 Its camera is fully retired from Guardian (10-Aug-2026, see Camera 4) — Boss no longer wants it, unrelated to the dead-webcam hardware issue.** Also currently unreachable at the network level (confirmed 10-Aug-2026: SSH and MediaMTX both closed, ARP-incomplete, no host on the /24 answering :8554) — a separate, likely transient issue from the permanent camera retirement |
| MSI Katana 15 HX (Boss's machine) | `ssh markb@192.168.0.4` — **IP drifted from `.3` to `.4`** (MAC `fc:6d:77:b8:e8:db`, verified 23-Jul-2026). Windows: default shell is cmd.exe, so wrap commands in `powershell -NoProfile -Command "..."` | yes |
| Larry's MSI laptop (Dominator) | `ssh -o StrictHostKeyChecking=no user@192.168.0.194` | box answers SSH, but its camera services are down (likely sitting at the Windows login screen — AtLogOn tasks need an interactive login). Verify before relying on it |
| Egon's Linode | ~~`ssh … euclid@172.104.147.157`~~ | **DECOMMISSIONED per Boss.** The IP still answered SSH on 2026-07-22 — if the instance was actually deleted, that address now belongs to a stranger. **Do not SSH credentials at it** until someone confirms in the Linode dashboard |

**Verify before you trust this table.** These are DHCP/hobby boxes; `nc -z -w 3 <ip> <port>` first (never `ping` — ICMP is blocked between wired and wireless).

**When you should use this pattern (non-exhaustive):**

- Granting TCC permissions (Camera, Microphone, Accessibility, Screen Recording) — these need a logged-in GUI session and can't be granted over plain SSH. A local Claude can fire the prompt for Boss to click.
- Triggering AppleScript / Automator / `osascript` flows that need to run as the logged-in GUI user.
- Reading or modifying files on a disk you can't mount (e.g., another machine's keychain, login items, browser profiles).
- Running interactive installers, GUI app first-launch dialogs, or `defaults` writes that take effect per-user-session.
- Anything you'd otherwise type out as "ask the Boss to do this on the other machine" — that's the smell that means: spawn a Claude there.

**Caveats:**

- The remote Claude has **no context from your conversation** — your prompt must be self-contained. State the task, the success criteria, where to look for relevant docs (paths on *that* machine, not on yours), and what to print so you can verify completion.
- Don't spawn a Claude on a box for a task you could trivially do over plain SSH (e.g., `tail` a log, `ls` a directory). Use this pattern when *the locality matters*.
- The `--dangerously-skip-permissions` flag is in the `c` alias because every farm Claude runs in trusted-LAN, single-user mode. Don't unset it when invoking — the headless print mode will block on every permission prompt otherwise.
- Output comes back as the SSH command's stdout. If the task needs to ping you back asynchronously, have the remote Claude write a marker file (e.g., `/tmp/<task>-done.flag`) and you poll for it.
- Coordinated edits to the same file from two Claudes simultaneously is not safe. Either serialize, or have one Claude commit and the other pull before editing.

**Cross-reference:** `bubba-workspace/skills/macbook-air/SKILL.md` has the per-machine details for the Air; `bubba-workspace/memory/reference/network.md` has the master device table; `bubba-workspace/skills/larry-access/SKILL.md` and `egon-gateway/SKILL.md` document the Windows and Linode targets respectively.

## Places on the farm — BIRDCATRAZ

**Birdcatraz** is the outdoor enclosed poultry compound: **the chicken coop and the turkey pen
together**. It is where the flock lives, where the machine in the coop sits, and where the
S7 phone is. When someone says "out there" or "at the coop", this is the place they mean.

**⚠️ Birdcatraz is a PLACE, not a camera name.** The device-not-location naming rule still holds
without exception — never `birdcatraz-cam`. Use it in prose, in VLM `context` strings, and in
plan docs; never as a camera id, a config key, or a service name. See the naming rule in
`HARDWARE_INVENTORY.md`.

**🔜 A Raspberry Pi 5 (4 GB) on wired Ethernet is coming to replace the machine at Birdcatraz.**
This is a re-architecture, not a port — the plan deliberately deletes five layers of accumulated
camera-identity workarounds rather than carrying them to Linux. Read
[`docs/05-Aug-2026-birdcatraz-pi5-camera-host-architecture-plan.md`](docs/05-Aug-2026-birdcatraz-pi5-camera-host-architecture-plan.md)
before touching anything camera-host related, and **do not start porting `usb_cam_host.py` to
Linux** — that is the specific thing the plan says not to do.

## Environment

- **Machine:** Mac Mini M4 Pro, 14-core, 64GB RAM, macOS 26.3. **LAN IP `192.168.0.10`** (drifted 22-Aug-2026 during the new-internet install; was `.217`, `.54`, and `.71` before that — grep any doc still citing those. Verify with `ifconfig | grep "inet 192"` rather than trusting this line.)
- **Python:** 3.13 (Homebrew)

> ⚠️ **Cameras and hosts move around constantly.** Boss rearranges hardware whenever it suits him — there is no "final setup" and this table is a point-in-time snapshot, not a contract. **Always verify against the live configs and a probe before trusting any row here.** Fast check:
> ```bash
> grep -n 'http_base_url\|ip_webcam_base' config.json tools/pipeline/config.json
> curl -s --max-time 5 http://<host>:8089/health
> ```
> When a camera moves, the ONLY things that must change are the URL in **both** config files and, on the new host, the `usb-cam-host` service. Then reload both LaunchAgents.

**Camera roster — SIX cameras, config-entry count and dashboard-visible count now agree, verified
2026-08-10 (after both `dominator-cam` and `gwtc` were retired the same day).** `config.json`
and `tools/pipeline/config.json` both carry exactly six camera entries, and Guardian's live
`GET /api/cameras` returns the same six: `house-yard`, `s7-cam`, `usb-webcam-1080p`,
`macbook-air-facetime`, `jieli-dashcam`, `duo2`. (Earlier the same day this line went through
several wrong counts in quick succession — six, then seven, with a "seven configured but six
visible because `gwtc` is disabled" split in between — before `gwtc` itself was retired and the
split became moot. If you ever see a config-entry count differ from what the dashboard shows
again, it means a disabled-but-still-configured camera exists; check for one before trusting
either number blindly.) `enabled` is Guardian's view (`config.json`); the pipeline
(`tools/pipeline/config.json`) has its own flags and is noted where the two disagree.

| # | camera | source | detection | state |
|---|---|---|---|---|
| 1 | `house-yard` | Reolink E1 Outdoor Pro, HTTP snapshot `192.168.0.88` | **ON** | live |
| 2 | `s7-cam` | Galaxy S7 IP Webcam, HTTP snapshot `192.168.0.249:8080` | off | live |
| 3 | `usb-webcam-1080p` | **`farm-pi5`** (Raspberry Pi 5 at Birdcatraz), `http://farm-pi5.local:8090` | off | ✅ **WORKING, and it was never broken.** Daylight-confirmed 06-Aug-2026: 1920x1080, mean 128.9, 0% clipped. Its V4L2 `gain` had been pinned at **0** (default 32), which blackens output on any host — that single fact explains the black frames on GWTC and the dead video interface on the Air. Fix is `gain=32` in `/etc/farmcam/usb-webcam-1080p.env`. **Ignore older "intermittent / needs a replug" notes about this camera** |
| 5 | `macbook-air-facetime` | MacBook Air `192.168.0.50:8089` — the built-in **FaceTime HD @ 1280x720** (was `mba-cam`) | off | live, enabled in both. **Can disappear from the system entirely with the lid still open** (verified 01-Aug-2026 via `ioreg -r -k AppleClamshellState`) — re-seating the USB hub restores it, since the built-in sits on the same USB controller. The service 503s rather than substituting another camera, which is correct. ⚠️ archive rows from 21-Jul 13:31Z to 23-Jul 12:55Z are actually USB-camera footage — see HARDWARE_INVENTORY.md |
| 5b | `jieli-dashcam` | **`farm-pi5`** (Raspberry Pi 5 at Birdcatraz), `http://farm-pi5.local:8091` — car dashcam in PC-camera mode, Jieli "USB PHY 2.0", 1280x720 wide-angle | off | **Moved to the Pi 05-Aug-2026** (was MacBook Air `:8091`). Time-lapse material, never a gem. **Re-aimed frequently by Boss — never record what it points at, in config, docs, or code.** The old bus-power constraint is gone: it has a powered hub on the Pi |
| 7 | `duo2` | Reolink Duo 2 WiFi, `rtsp://…@192.168.0.155:554` | **ON** | live |

**🔴 `dominator-cam` RETIRED 10-Aug-2026 — do not re-add it.** Boss doesn't want it anymore; it's out of both config files (`scripts/add-camera.py remove dominator-cam`) and its `dominator-cam-bisoncam` scheduled task on the Dominator (`192.168.0.194`) is disabled, not just stopped. Its companion `usb-cam` role on that same box already moved to the Birdcatraz Pi on 05-Aug-2026 (`usb-webcam-1080p`) and is unaffected. See `docs/10-Aug-2026-dominator-cam-retirement-plan.md`.

**🔴 `gwtc` ALSO RETIRED 10-Aug-2026, same reason — do not re-add it.** Boss: with the Birdcatraz
Pi (`farm-pi5`) now covering camera duty out there, neither laptop-hosted camera is needed. Out
of both config files (`scripts/add-camera.py remove gwtc` + a hand-fix for a
`timelapse_reel_daylight_only_cameras` list reference the removal tool doesn't reach). **Unlike
the Dominator, GWTC's on-box services (`mediamtx`, `farmcam`, both watchdogs) are NOT yet
disabled** — the laptop was unreachable (off the LAN, confirmed by a full `/24` port-8554 sweep)
at retirement time, so there was nothing to SSH into. Not urgent — nothing in Guardian consumes
its feed anymore either way — but don't assume it's fully torn down if you ever do reach the
box. See `docs/10-Aug-2026-gwtc-retirement-plan.md`.

(The old note that used to sit here — "usb-cam/mba-cam config divergence, usb-cam → GWTC,
23-Jul-2026" — described ids and a hosting arrangement that no longer exist on either count;
removed rather than corrected a third time, since the roster table above is the actual source
of truth for current camera hosting.)

**🔴 CAMERAS RENAMED 01-Aug-2026 — `usb-cam` and `mba-cam` DO NOT EXIST.** They are now
`usb-webcam-1080p` (the generic 1920x1080 USB webcam) and `macbook-air-facetime` (the Air's
built-in 1280x720), and a third camera `jieli-dashcam` was added. 44,525 archive rows were
migrated to the new ids.

**✅ SUPERSEDED 05/06-Aug-2026 (v2.62.0/v2.63.3) — the "all three on one USB hub on the Air" part
above is no longer true.** `usb-webcam-1080p` and `jieli-dashcam` both moved off the MacBook Air
to `farm-pi5` (the Birdcatraz Pi), and the Air was deliberately reduced to exactly one camera —
`macbook-air-facetime` — to remove the multi-camera identity-collision risk this section's
"do not identify by position" warning below was written about. Confirmed live and healthy in
this session's audit (10-Aug-2026): both Pi cameras respond `ok:true` on `farm-pi5.local:8090`
and `:8091`. See the roster table above and `docs/05-Aug-2026-birdcatraz-pi5-bringup-log.md`.
The identity-collision lesson below is still correct and still applies — just not to three
cameras on the Air anymore, since there's only one there now.

**⚠️ DO NOT identify a camera by its position in a device list, and DO NOT use resolution as
proof of identity.** ffmpeg and OpenCV number the same cameras *differently on the same machine
at the same moment* — measured quiescent on the Air, ffmpeg said `[0] FaceTime [1] USB PHY
[2] USB CAMERA` while OpenCV saw `[0] USB PHY [1] USB CAMERA [2] FaceTime`. The old
`USB_CAM_DEVICE_NAME_CONTAINS` looked the name up in ffmpeg's list and handed that number to
OpenCV, so binding to "FaceTime" would have served the **turkey-run camera** under the MacBook
Air's name. Position also moved twice in one afternoon as cameras were plugged in. And the
dashcam and FaceTime are both 1280x720, so the old resolution tell is dead. `usb_cam_host.py`
now proves identity before serving and **serves nothing rather than guessing**. To check a
camera, pull `/photo.jpg` and look at it. Plan:
[`docs/01-Aug-2026-camera-rename-and-dashcam-plan.md`](docs/01-Aug-2026-camera-rename-and-dashcam-plan.md), CHANGELOG v2.57.0.

**⚠️ A camera name is the DEVICE, and `PREFER_EXTERNAL` can silently break that.** If a USB camera is plugged into a host that also has a built-in camera, `usb-cam-host` defaults to serving the *external* one — so the host's endpoint changes identity while every config, archive row and reel keeps the old label. This actually happened: `mba-cam` frames from **21-Jul 13:31Z to 23-Jul 12:55Z** are the USB camera in the turkey pen, not the MacBook Air's FaceTime HD, and a reel got built from them before anyone noticed. **Resolution is the tell** — the 2013 FaceTime HD cannot exceed 1280x720, the USB camera is 1920x1080. After ANY physical camera move, run `curl http://<host>:8089/health` and check `resolved_device_name` + `resolution` before trusting a label.

**⚠️ MBA gotcha when the USB camera is unplugged from it:** `usb-cam-host` defaults to `USB_CAM_PREFER_EXTERNAL=true`, and in that mode it **deliberately refuses to serve the built-in camera** ("serving the built-in as usb-cam is precisely the bug this avoids"). So pulling the USB cam does NOT auto-fall-back to FaceTime — the endpoint just 503s. The MBA's plist now sets `USB_CAM_PREFER_EXTERNAL=false` so it serves FaceTime at index 0. Env changes need `launchctl bootout` + `bootstrap` (a `kickstart` re-runs the job from launchd's CACHED plist and silently ignores your edit), and the grabber needs a restart after any physical swap to re-enumerate.

- **Camera 1 (house-yard):** Reolink E1 Outdoor Pro — ONVIF, RTSP, 4K, PTZ, WiFi. IP `192.168.0.88`. Shows as **`FarmGuardian1`** in the Reolink app and in the frame's bottom-right OSD. **🔴 Boss hand-aimed this camera in the Reolink app and it is exactly where he wants it: pan `2214` (110.7°), looking at Birdcatraz — re-aimed and re-saved 07-Aug-2026 after the power cut, confirmed final by Boss. The aim is saved as camera preset id `6` name `Main`, which Boss created; he deleted every other preset, so it is the ONLY enabled one on the camera. That is the way back if the aim is knocked off. Do NOT move the camera, and do NOT `preset/goto` any id but 6. Turning on patrol would destroy the aim AND force zoom to 0. Nothing currently overrides it — camera-side PTZ guard off with no home position, AI auto-tracking off, all six on-camera cruise slots empty, `ptz.patrol_enabled` and `sky_watch.enabled` both false, no deterrent rule uses PTZ, and no scheduled job sends anything but `/snapshot`.**
  **⚠️ The older `pan 1885` / 94.2° / preset id 5 `boss-birdcatraz-aim` is DEAD — preset 5 no longer exists. If you see those numbers in a CHANGELOG entry or an old doc, they are history, not a target.**
  **⚠️ A power cut moves this camera.** Preset data survives (it lives on the camera), but the physical aim does not — the PTZ re-homes its motors on boot and lands near, not on, where it was. The 07-Aug outage left it ~16° off. **After any power event, check the framing, not just that the camera is online**; recover with `preset/goto` id 6. Guardian does not do this automatically yet.

**⚠️ Only PAN readback is trustworthy on this camera — tilt and zoom both lie.** Tilt returns 945 at many angles. Zoom drifts with no command sent: measured `19` in daylight and `27` after the night IR switch 26 minutes later, with identically-framed snapshots either side and no PTZ command in the log. **Judge this camera by the picture, not the numbers** — a changed tilt or zoom reading is not evidence it moved, and "restoring" zoom to a remembered number means moving a lens that was never out of place. The old "always leave zoom at 0" rule is dead. Full detail + recovery procedure: `AGENTS_CAMERA.md` → "Current Pointing". **Polls the camera's HTTP `cmd=Snap` endpoint for native 4K JPEGs** (`source: "snapshot"`, `snapshot_method: "reolink"`); we no longer use RTSP for this camera. Snapshot interval 5s for the dashboard, 2s during the night detection window so YOLO has more chances per minute. The RTSP path was abandoned because the lossy WiFi link mangled HEVC reference packets — see CHANGELOG v2.16.0/v2.17.0/v2.18.0 and `docs/13-Apr-2026-phase-a-reolink-snapshot-polling-plan.md`.
- **Camera 2 (s7-cam):** Samsung Galaxy S7 phone running the IP Webcam app. **Consumed as HTTP snapshots at `http://192.168.0.249:8080`** (`http_base_url`), not RTSP — the `rtsp://192.168.0.249:5554/camera` path still exists on the phone but no config uses it. No auth required. Fixed camera, no PTZ. Detection disabled.

  **🔴 THE PHONE WAS REPLACED 10-Aug-2026 — READ THIS BEFORE THE HISTORY BELOW.** The water-damaged handset was retired and a different Galaxy S7 took over the `s7-cam` identity. It holds the same address `192.168.0.249` (set as a **static IP on the phone**, not a router reservation), so no config changed. Full execution log, including everything that differs: [`docs/10-Aug-2026-s7-galaxy-replacement-swap-log.md`](docs/10-Aug-2026-s7-galaxy-replacement-swap-log.md).

  - **It is a different model on much older firmware: SM-G930V (`heroqltevzw`, Verizon), Android 6.0.1**, build `G930VVRS4APH1`. The old one was SM-G930F on Android 8.0.0. **Consequence: the Android-8 / Samsung-Experience-9 menu paths in `docs/skills-s7-adb-operations.md` and `docs/01-Aug-2026-s7-factory-reset-runbook.md` do not exist on this phone**, and `locksettings` / `svc power stayon` are unavailable to the shell (both are Android 7+). Doze is plain AOSP, handled with `dumpsys deviceidle whitelist +com.pas.webcam`.
  - **⚠️ ADB WORKS AGAIN. This phone's USB port is healthy** (adb serial `4fad774d`). **Every claim in this repo that the S7 has no ADB path and can only be fixed by walking to the coop describes the RETIRED handset and is now false** — including the "PERMANENTLY INAPPLICABLE" banner on `docs/skills-s7-adb-operations.md`. A wedged or black-camera IP Webcam is now recoverable from the Mini (`adb shell am force-stop com.pas.webcam`, relaunch) instead of requiring a drive.
  - **⚡ POWER: a proper 3 A USB wall charger and cable, NOT a Qi pad (Boss, 10-Aug-2026).** This is the single biggest operational win of the swap, bigger than the camera itself. **It retires the "open problem" that made the old phone unviable:** that handset could only take ~5 W over Qi, which Boss confirmed was *net-negative* against the camera's draw, so it had to be carried indoors and powered off to charge — taking `s7-cam` down for hours at a time. A 3 A charger comfortably exceeds the draw, so **`s7-cam` can now run continuously.**
  - **⚠️ CONSEQUENCE — the "an s7-cam outage is ROUTINE, it's just charging" rule below is OBSOLETE.** That guidance (and Boss's *"it comes inside to charge, that's not even worth noting"*) existed because charging windows were unavoidable. They no longer are. **An extended `s7-cam` outage is now worth actually looking at** rather than being written off. Still don't pester Boss about indoor or dog frames if the phone does come inside for some other reason — that part of his directive stands.
  - **It self-starts clean.** Verified by reboot 10-Aug-2026: IP Webcam auto-started its server unattended, `.Rolling` in front, 8080 listening, static `.249` held, nothing touched. The old phone's cold-boot black-camera bug does not reproduce. `photo_rotation` still resets to `-1` on every boot, as it always did — the 10-minute watchdog re-asserts it.
  - **It was stripped to one app** (330 → 200 packages) on Boss's explicit instruction. Play Services and the IMS trio were kept — see the ⛔ rule at the end of this bullet, which still fully applies. Note `pm disable-user` is **denied to the shell user on Android 6**; the strip used `pm uninstall -k --user 0`, which has **no per-app undo on API 23** (no `install-existing`), so restoring any single package means a factory reset.
  - **Sensor is a Samsung ISOCELL S5K2L1 (`s5k2l1sx`, id `0x20c1`), not the Sony IMX260** the docs long claimed — confirmed 10-Aug-2026 and the VLM `context` string corrected. Same spec either way (12 MP, f/1.7, dual-pixel AF), so the prompt's "best camera in the fleet" framing stands. **To re-check a Samsung sensor: `adb shell 'dmesg | grep sensor_match_id'`** — the `/sys/devices/virtual/camera/rear/*` nodes are root-only and return `Permission denied` to the shell user (with an SELinux `avc: denied` in logcat).

  - **⛔ `com.farmguardian.s7-settings-watchdog` IS RETIRED (10-Aug-2026) — do NOT restart it.** All three settings it re-pushed every 10 minutes are redundant: `orientation` and `focusmode` persist in the app and came back correct unaided across a verified reboot, and `photo_rotation` — the one that genuinely does revert to `-1` on every boot — is already handled per-frame by `force_portrait` at `capture.py:81` and its mirror in `tools/pipeline/capture.py`. Its liveness half had **no recovery path at all** and logged a false `STALL` every 10 minutes for weeks. Booted out and the plist renamed `.disabled-10Aug2026` (a `bootout` alone reloads at next login). Reasoning, the boot measurements, and the restore command: [`docs/10-Aug-2026-s7-settings-watchdog-retired.md`](docs/10-Aug-2026-s7-settings-watchdog-retired.md).
  - **⚠️ `EXIF Orientation=1` on `s7-cam` after a reboot is EXPECTED and is NOT a fault.** `photo_rotation` reverts on every boot; `force_portrait` rotates any wider-than-tall frame 90° CW regardless of EXIF, so the pictures are correct even while the tag says landscape. I misdiagnosed exactly this on 10-Aug-2026 — read a metadata tag, declared the frames sideways, and started "fixing" a camera that was working. **Judge this camera by the picture, not the metadata**, same rule as the Reolink above.

  **Everything from here down describes the RETIRED handset** (SM-G930F) and is kept because its failure modes and the portrait decision still govern how `s7-cam` works. **Note its many statements that the 10-minute watchdog is the re-assertion or recovery layer are now historical — that job is retired (see above).** **Power & host: Qi WIRELESS CHARGING PAD (state of the OLD phone as of 2026-08-01).** Power-chain history: MBA-USB → standalone brick (2026-04-26) → GWTC-USB (2026-05-02, ADB-authorised so the watchdog could remotely restart IP Webcam) → standalone brick again (≤2026-05-06) → **Qi pad (2026-08-01)**. The phone got wet and its micro-USB port stopped working for BOTH power and data; confirmed 2026-08-01 with a known-good DATA cable directly in the Mac mini, the phone does not enumerate on the USB bus at all (no Samsung vendor ID 1256 / 0x04E8 in `ioreg -rc IOUSBHostDevice`). Qi is now the only working charging path. **This is permanent unless the port is repaired: there is no ADB host of any kind and no way to create one.** adb-over-USB needs a working port; adb-over-network is refused (5555 closed) because Android 8.0.0 predates wireless-debugging pairing and `adb tcpip` would itself need one working USB session. Do not plan any recovery, cleanup or settings sweep around ADB on this phone. Diagnostic: `tools/s7-charge-diagnose.sh`. Caveat: Qi on an SM-G930F is ~5W and this phone has a documented history of browning out on weak power — keep the screen off/dim and treat power as the first suspect on any new stall. **Confirmed by Boss 02-Aug-2026: running the phone (camera app + WiFi + screen) while it rests on the Qi pad drains it net-negative — the pad can't keep up with active draw.** To actually charge it, Boss powers the phone off, which takes s7-cam fully offline for a stretch. **⛔ This is ROUTINE — do not report it as an incident.** The phone is carried indoors to charge, so an s7-cam outage of a few hours, and archive frames showing a room, a desk or the farm dog instead of the flock, are both completely expected. Boss 06-Aug-2026: "It comes inside to charge. That's not even worth noting to me." Do not open an investigation, do not flag the indoor frames as mislabeled, and do not ask him about it. The VLM prompt explicitly permits describing the dog or an indoor scene — a good photo of the dog is worth surfacing, not discarding. **Before treating an s7-cam outage as an incident, check whether it's simply a charging window** — a clean `ConnectionError: ... Host is down` in `/tmp/pipeline.err.log` (ports closed, no partial responses) is consistent with the phone being off; this is a different signature from the known HTTP-wedge mode above (`/photoaf.jpg` returning 0 bytes while the phone is still on the network). Phone reachable only over WiFi at `192.168.0.249:8080` (HTTP) / `:5554` (RTSP). **Cold-boot black-screen: FIXED 2026-05-21 (v2.40.16)** by disabling the phone's swipe lock screen — the keyguard was blocking camera init on boot. Focus (`Aggressive`/continuous-picture) and orientation (Portrait) are now persisted in the IP Webcam app menu (HTTP `/settings/` are runtime-only and do NOT survive reboot), so a power-cycle now self-heals to green + sharp + portrait with zero intervention. A mid-run HTTP-server wedge (rare) may still need a hands-on Stop/Start at the phone — there is no remote ADB escape hatch in the current standalone configuration. **Orientation is PORTRAIT (fixed on phone, deliberate decision 2026-04-21, v2.35.2).** IP Webcam's `orientation=portrait` + `photo_rotation=90` settings drive an EXIF Orientation=6 tag on the JPEG; `capture.py:_apply_exif_rotation` bakes the rotation in at the capture boundary before `cv2.imdecode` (which ignores EXIF). Every downstream consumer sees 1080×1920 portrait pixels. Physical phone rotation does NOT change orientation — it's set via `http://192.168.0.249:8080/settings/orientation?set=...` (values: `portrait`, `landscape`, `upsidedown`, `upsidedown_portrait`). Portrait is the conscious choice because the s7-cam's primary content destination is IG stories + reels, which are native 9:16. Backend helper is adaptive (reads whatever EXIF says), so flipping back to landscape later only requires flipping the phone-side setting; the pipeline follows. **Settings re-assertion:** `com.farmguardian.s7-settings-watchdog` ticks every 10 min and re-curls `/settings/orientation?set=portrait` + `/settings/photo_rotation?set=90` + `/settings/focusmode?set=continuous-picture` as a WiFi backup. Since focus + orientation are now persisted in-app (2026-05-21), the watchdog is redundant for those; `photo_rotation` is the one still applied only over HTTP. **White balance is no longer pushed (v2.40.16, 2026-05-22):** the old `whitebalance=incandescent` GET was a brooder heat-lamp compensation, but the S7 moved to the nesting box and the lamp is gone, so it now cool/blue-shifts an already-neutral scene (the washed-out, oddly-colored look). The phone keeps its default auto WB — do NOT re-add the incandescent push in `config.json` or the watchdog plist. **Known wedge mode:** if IP Webcam's HTTP server stalls (`/photoaf.jpg` returns 0 bytes), the three settings re-curls in the same tick will also fail (`fm=0 or=0 pr=0` in the watchdog log) and the camera serves landscape until the app self-recovers — the 2026-05-06 incident captured in CHANGELOG. The deployed plist still carries an SSH-to-GWTC ADB-recovery branch from before the standalone-power switch; it is harmless dead code (left in place during the v2.40.16 WB-only edit to keep that change surgical; safe to delete whenever the plist is next reworked). **⛔ Before doing ANY app cleanup / `pm disable-user` on this phone, read `docs/skills-s7-adb-operations.md` → "DO NOT DISABLE Google Play Services" (07-Jul-2026): IP Webcam depends on `com.google.android.gms` and disabling it kills the camera (recovery = full reboot); disabling `com.sec.imsservice` triggers an undismissable OS crash-loop. Only pure consumer apps (FB/IG/WhatsApp/Office) are safe to disable, and Boss wants location/Bluetooth/NFC left ON.** (That rule was learned on a 07-Jul-2026 bench session with the S7 temporarily USB-tethered to the Mac Mini for ADB — a temporary hookup, NOT a permanent power/host change. That tethering option is now GONE — as of 2026-08-01 the micro-USB port no longer enumerates at all, so the Qi-pad state above stands and no future bench session can use ADB.)
- **Camera 3 — see the roster table above, row `usb-webcam-1080p` (this bullet's old id was
  `usb-cam`, renamed 01-Aug-2026).** This bullet described its pre-05-Aug-2026 history hosted on
  the Mac Mini, then the MacBook Air, then GWTC, with GWTC-specific white-balance settings and
  recovery commands. All superseded by the Birdcatraz Pi migration (v2.62.0/v2.63.0) — it now
  runs on `farm-pi5` with structural `/dev/v4l/by-id/` identity, confirmed healthy in this
  session's audit. The historical gray-world-WB-off note doesn't transfer to the Pi's own
  capture path; don't assume it still applies without checking `tools/camera-host-linux/` first.

  **🔴 MOVED TO THE MACBOOK AIR 05-Aug-2026 — both configs now point at `192.168.0.50:8090`, NOT GWTC.** The camera was physically on the Air while both config files still polled GWTC `192.168.0.69:8089`, which is **serving a pure-black 1920x1080 frame** from something that is not this camera (GWTC's own webcam is disabled; OBS Virtual Camera is installed on that box). That black feed is what appeared on the dashboard as "the USB webcam is back up." **GWTC `:8089` is a black-frame trap — do not point anything at it.** The Air's service (`com.farmguardian.cam-usb-webcam-1080p`, port 8090, `USB_CAM_START_DELAY=25`) is un-parked and loaded.

  **Current state: `/health` returns `ok:false` + 503 with `acquire_stalled_s: 0.0`.** Per the 30-second triage table above that means the camera is genuinely **absent from the video bus** — hands-on replug, no software fix. It is enumerated on the USB bus with its serial and drawing power, but its *video interface* is not in the AVFoundation list. **This is correct, honest behaviour and needs no action beyond a replug** — the service is loaded and will bind the moment the camera returns. Do NOT re-point it at GWTC to make the tile look alive.
- **Camera 4 (gwtc) — RETIRED 10-Aug-2026.** Removed from both config files entirely (was
  previously just `enabled: false`); Boss doesn't want it, the Birdcatraz Pi covers this duty
  now. See `docs/10-Aug-2026-gwtc-retirement-plan.md`. **The hardware/troubleshooting detail
  below is kept as historical reference, not a live runbook** — it's genuinely useful if anyone
  ever has hands on this laptop again (it documents a real electrical trap: don't reset its USB
  root hub remotely, it takes the WiFi NIC down with it) but none of it describes an active
  Guardian camera anymore.
  - **⚠️ State verified 23-Jul-2026 (historical): the laptop is FINE, the camera is DEAD.** GWTC is fully reachable at `192.168.0.69` (SSH 22 + MediaMTX 8554 both open), up since 20-Jul, and all three services (`farmcam`, `farmcam-watchdog`, `mediamtx`) report Running. `Get-PnpDevice` shows `Hy-HD-Camera` as not present, and `ffmpeg -list_devices` sees only an "OBS Virtual Camera" (OBS has been installed on the box at some point). So `start-camera.bat`, which opens `video="Hy-HD-Camera"`, exits instantly and Shawl respawns it forever.
  - **🔧 CORRECTION 25-Jul-2026 — the webcam is NOT "off the bus"; it is failing USB enumeration, and it was taking the network down with it.** The earlier claim above ("not on the device bus at all") was wrong. The camera is electrically present on **`Port_#0008.Hub_#0001`** and retrying enumeration in a permanent loop; it cannot return its device descriptor, so Windows labels it `Unknown USB Device (Device Descriptor Request Failed)` / `VID_0000&PID_0002` / `CM_PROB_FAILED_POST_START` instead of by name. Port-location mapping proves identity: `Hy-HD-Camera`'s last location is that same `Port_#0008.Hub_#0001`. It died between **4 and 7 June 2026**. **Why this matters far beyond the camera:** the **Realtek 8723DU WiFi NIC is a USB device on `Port_#0007` of the same root hub** — one port away — so the failed device's endless enumeration retries disturb the bus the network depends on. That is the mechanism behind the recurring "GWTC vanished off the LAN" dropouts, and it is why re-plugging the USB hub on port 6 *restores the network*: it forces a root-hub re-enumeration that bounces the WiFi NIC. **The dead webcam has been disabled** (`Disable-PnpDevice -InstanceId 'USB\VID_0000&PID_0002\5&2FF55CF5&0&8'` → `CM_PROB_DISABLED`, verified WiFi and `usb-cam` unaffected) to stop the bus noise. **⛔ NEVER reset the USB root hub or the Intel xHCI controller on GWTC remotely — the WiFi NIC is a child of it, so you would kill the only way back in, on a box with no screen and no keyboard.** Disable/reset individual leaf devices by exact InstanceId only. Full topology and evidence: [`docs/24-Jul-2026-gwtc-offline-incident.md`](docs/24-Jul-2026-gwtc-offline-incident.md).
  - **This is a CRASH LOOP, not the documented dshow zombie — do not confuse them.** The zombie is ffmpeg *alive but wedged*, and `farmcam-watchdog` fixes it in ~90s. Here ffmpeg **dies in 0-1s**, so the watchdog correctly logs `only alive 0s -- within startup grace, no action` every 30s and never intervenes. **Waiting 3 minutes will not help and neither will restarting anything from the Mac Mini.** Symptom from here: port 8554 open but `rtsp://192.168.0.69:8554/gwtc` returns `404 Not Found`.
  - **Screen: black BY DESIGN — do not try to "fix" it.** GWTC is a **touchscreen** laptop (panel `NV116WHM-T16`, the T = touch), and the chickens kept touching it, so Boss deliberately disabled the screen at the hardware level. Evidence that it is intentional and not failed: Windows still enumerates the panel and reports it active/full-power/brightness 90, but the display stays dark, and there is **no active HID touch-screen device** in `Get-PnpDevice` (the digitizer was disabled). It runs headless perfectly — SSH, the camera host, and the watchdogs are all up regardless of the screen. **Do not diagnose this as a dead backlight or replace the panel.** (Two earlier writeups got this wrong: first "brightness pinned low", then "fried backlight" — both incorrect. The brightness value *does* take in software; the panel is simply disabled on purpose.) If a future need ever requires the screen back, the thing to re-enable is the touch digitizer + display, and the anti-chicken concern comes back with it. A `farmcam-screen-on` scheduled task was created on the wrong "brightness" theory and then removed 23-Jul-2026 as pointless; `deploy/gwtc/screen-on.ps1` + `register-screen-task.ps1` remain in the repo as dead artifacts only.
  - **Fix needs hands on the laptop:** the camera is absent at the hardware/driver level. Check for a function-key camera toggle or physical shutter first (the MSI Dominator has exactly this trap — see `HARDWARE_INVENTORY.md`), then Device Manager, then whether OBS grabbed or replaced the device. Nothing on the Mini can restore a camera that Windows cannot enumerate. Streams via ffmpeg → MediaMTX at `rtsp://192.168.0.69:8554/gwtc` (**IP moved from `.68` to `.69`**; DHCP is not pinned, so it can hop again — rediscover with the port-8554 sweep in the Network section). 1280x720, 15fps, H.264. Services auto-start via Shawl, with the `farmcam-watchdog` Shawl service handling post-reboot recovery. Detection disabled. In the chicken coop.
- **Camera 5 (`macbook-air-facetime`, this bullet's old id was `mba-cam`, renamed 01-Aug-2026):**
  MacBook Air built-in FaceTime HD via `usb-cam-host` on `192.168.0.50:8089`. **⚠️ CORRECTED
  10-Aug-2026 — no longer the optional/toggled "brooder monitor" this bullet used to describe.**
  As of v2.63.3 (06-Aug-2026) the Air was deliberately reduced to exactly this one
  permanently-loaded camera (to remove the identity-collision precondition — see the CAMERAS
  RENAMED banner above), and the roster table shows it `enabled: true` in both configs, live,
  no load/unload toggle. **Live-probe note (10-Aug-2026):** the box itself answers (ARP + ping
  succeed), but port 8089 was closed/unreachable during this session's audit — worth a
  `launchctl kickstart -k` check on that LaunchAgent if it's still down when you read this,
  rather than assuming it's fine because the host is up.
- **Camera 6 (dominator-cam) — RETIRED 10-Aug-2026.** Was the built-in webcam on the MSI "Dominator" laptop at `192.168.0.194:8089`. Boss no longer wants it; removed from both config files and its `dominator-cam-bisoncam` scheduled task disabled on the Dominator. Not a fault, not offline-by-design anymore — gone on purpose. See `docs/10-Aug-2026-dominator-cam-retirement-plan.md`. Do not re-add it without a fresh ask from Boss.
- **Camera 7 (duo2):** Reolink Duo 2 WiFi, dual-lens, RTSP at `192.168.0.155:554` with credentials embedded in the config URL. **Detection enabled.** Feeds the 15:00 daily time-lapse reel and is the largest archive consumer (~50 GB rolling raw window at ~5.9 MB/frame — bounded by design, not a leak).
- **Network:** All devices on same local WiFi network. Reolink IPs are DHCP and have drifted before (`config.json` says `.88`/`.155`; other docs have claimed `.89`/`.156`) — verify against the router or Guardian's API before believing any hardcoded address.

## Key Dependencies

- `opencv-python` — RTSP stream capture and frame processing
- `ultralytics` — YOLOv8 model loading and inference
- `onvif-zeep` — ONVIF camera discovery and control
- `reolink-aio` — Reolink camera control (PTZ, spotlight, siren)
- `aiohttp` — Async HTTP (required by reolink-aio)
- `requests` — Discord webhook and eBird API HTTP posts
- `Pillow` — Image saving and manipulation
- `fastapi` + `uvicorn` — Local web dashboard + REST API
- `python-multipart` — Form support for FastAPI
- `sqlite3` (stdlib) — Structured detection/track/alert storage

---

## Coding Standards (MANDATORY — from the boss)

These standards apply to ALL code in this repository. Non-negotiable.

### Mission & Critical Warnings

- Every Python file you create or edit must start with this header (update it whenever you touch the file):
  ```
  Author: {Your Model Name}
  Date: {DD-Month-YYYY}
  PURPOSE: Verbose details about functionality, integration points, dependencies
  SRP/DRY check: Pass/Fail — did you verify existing functionality?
  ```
- Comment the non-obvious parts of your code; explain integrations inline where logic could confuse future contributors.
- If you edit file headers, update the metadata to reflect your changes; never add headers to formats that do not support comments (JSON, etc.).
- Changing behavior requires updating relevant docs and the top entry of `CHANGELOG.md` (SemVer, what/why/how, include author).
- Never guess about unfamiliar or recently updated libraries/frameworks — ask for docs or locate them yourself.
- Mention when a web search could surface critical, up-to-date information.
- Ask clarifying questions only after checking docs; call out where a plan or docs are unclear.
- The user does not care about speed. Slow down, ultrathink, and secure plan approval before editing.

### Role, User Context & Communication

- You are an elite software architect with 20+ years of experience. Enforce SRP/DRY obsessively.
- The user is a hobbyist / non-technical executive. Keep explanations concise, friendly, and free of jargon.
- The project serves ~4–5 users. Ship pragmatic, production-quality solutions rather than enterprise abstractions.
- **Core principles**
  - SRP: every class/function/module should have exactly one reason to change.
  - DRY: reuse utilities/components; search before creating anything new.
  - Modular reuse: study existing patterns and compose from them.
  - Production readiness only: no stubs, mocks, placeholders, or fake data.
  - Robust naming, strong error handling, and commented complex logic.
- **Design & style guidelines**
  - Avoid "AI slop": no unnecessary abstractions, no over-engineered class hierarchies.
  - Create intentional, high-quality code with purposeful structure.
- **Communication rules**
  - Keep responses tight; never echo chain-of-thought.
  - Ask only essential questions after consulting docs.
  - Pause when errors occur, think, then request input if truly needed.
  - End completed tasks with "done" (or "next" if awaiting instructions).
- **Development context**
  - Small hobby project: consider cost/benefit of every change.
  - Assume environment variables, secrets, and external APIs are healthy; treat issues as your bug to diagnose.

### Workflow, Planning & Version Control

1. **Deep analysis** — Study existing architecture for reuse opportunities before touching code.
2. **Plan architecture** — Create `{date}-{goal}-plan.md` inside `docs/` with scope, objectives, and TODOs; seek user approval.
3. **Implement modularly** — Follow established patterns; keep components/functions focused.
4. **Verify integration** — Use real APIs/services; never rely on mocks or placeholder flows.
5. **Version control discipline** — Update `CHANGELOG.md` at the top (SemVer ordering) with what/why/how and your model name.
6. **Documentation expectations** — Provide architectural explanations, highlight SRP/DRY fixes, point to reused modules.

### File Conventions

- **File headers** — Required for all Python file changes; update the metadata each time you modify a file.
- **Commenting** — Add inline comments when logic, integration points, or failure modes are not obvious.
- **No placeholders** — Ship only real implementations; remove TODO scaffolding before submitting.
- **Naming & structure** — Use consistent naming, exhaustive error handling, and shared helpers/utilities.

### Error Handling

- Camera disconnection → log warning, retry with backoff, don't crash
- YOLO inference failure → log error, skip frame, continue
- Vision model timeout → fall back to YOLO class, log warning
- Deterrent action failure → log error, skip action, don't block pipeline
- eBird API failure → log error, skip poll cycle, retry next interval
- Discord API failure → log error, buffer alert, retry
- SQLite write failure → log error, continue (JSONL fallback still writes)
- Never silently swallow exceptions

### What NOT To Do

- Don't add external/hosted web services — the dashboard is local-network only (Phase 5 will add hosting)
- Don't add cloud APIs for detection — everything runs locally
- Don't add a second database — SQLite is the single data store (Phase 5 adds PostgreSQL sync)
- Don't over-abstract — this has 15 modules, each with one clear responsibility
- Don't create empty placeholder files — every file ships with real code
- Don't add dependencies that aren't in requirements.txt
- Don't ship stubs, mocks, or fake data

### Prohibited Actions


- Never commit secrets, API keys, or credentials
- Never add headers to JSON or other non-comment formats
- Never guess at library behavior — check documentation first
- Never ship placeholder or stub code

---

## Remote Camera Operations

**All camera-specific procedures, API endpoints, shapes, and operational knowledge live in `AGENTS_CAMERA.md`.** Read that file before any camera work. It contains everything a remote assistant needs to operate the camera correctly — learned from real mistakes, not guesses.
