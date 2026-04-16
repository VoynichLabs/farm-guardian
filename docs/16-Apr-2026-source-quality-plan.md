# 16-Apr-2026 — Max-volume capture plan (Bubba side)

Author: Claude Opus 4.7 (1M context) — Bubba
Branch: `bubba/source-quality-plan-16-Apr`
Status: DRAFT — flags decision points and bottlenecks for the other dev. **No code changes in this plan.** Other-dev is live in the repo; I am out of his lane.

## The goal, in Boss's words

> "I want lots of pictures. I want as many fucking pictures as I can get without overloading any machine. … a lot of them are going to be junk, but there's going to be a couple of really, like, gem ones in there. And that's what Farm 2026 has a front end to show off."

**Volume strategy.** Every camera as fast as its host allows. Cull junk via retention, surface gems via the pipeline, display gems via farm-2026. My earlier plan framed this as a quality problem — it isn't. It's a throughput problem where quality is an emergent property of sample count.

## What the other dev already shipped today (read before re-recommending)

| Commit | What |
|---|---|
| v2.27.7 | S7 `/photoaf.jpg`, continuous-picture AF, incandescent WB, startup-GETs to survive phone reboot. Cadence 60s. |
| v2.27.6 | usb-cam stale IP fix. |
| v2.27.4 | usb-cam-host WB + orange-desat tuning. |
| v2.27.3 | gwtc cadence 600s → 60s. |
| v2.27.0 | usb-cam-host continuous-capture: daemon thread keeps camera warm, `/photo.jpg` 75ms. |

All four cameras have a working capture path today. The only lever remaining for "more volume" is **cadence**.

## Per-camera max sustainable cadence

Numbers below are my read of the physics + what's been measured. Need the other dev to confirm before changing anything.

### S7 (IP Webcam on worn S7 phone)

| Cadence | Images/day | Notes |
|---|---|---|
| 60s (today) | 1,440 | dev set this 2h ago, quoted Boss's earlier "30s-to-a-minute" + battery concern |
| **10s** | 8,640 | `/photoaf.jpg` has ~1s AF overhead per call. At 10s the phone camera is awake 10% of the time. **Requires AC-powered S7, not battery.** |
| 5s | 17,280 | camera is awake ~20% of wall-clock. Thermals on the S7 are the likely limit before battery. Still viable on AC for bursts, risky 24/7. |

**Bottleneck:** S7 hardware (worn battery, phone thermals). **Blocker for sub-30s cadence:** phone must be on a reliable wall-wart charger. Confirm before cadence drops.

**Data volume:** 677 KB × 8,640/day = **~5.6 GB/day** at 10s.

### usb-cam (USB webcam on Mac Mini, served by usb-cam-host)

Per v2.27.0: camera is continuously warm, `/photo.jpg` responds in 75ms (not a fresh capture — it returns the most recent grabbed frame from a 0.5s-interval grabber thread).

| Cadence | Images/day | Notes |
|---|---|---|
| today's pipeline cadence: 60s | 1,440 | wildly under-sampled given the host has frames every 0.5s |
| **2s** | 43,200 | safe; roughly matches the grabber's 0.5s interval with headroom. The daemon does atomic frame publishing, so readers never see a stale frame older than ~0.5s. |
| 0.5s | 172,800 | matches grabber rate — each `/photo.jpg` is a distinct frame |

**Bottleneck:** disk + retention at high cadence, not the camera. The usb-cam daemon is built for this; it's the *pipeline* that currently can't keep up because VLM calls are ~98s each.

**Data volume:** ~200 KB × 43,200/day = **~8.6 GB/day** at 2s. At 0.5s: **~34 GB/day** — impractical without aggressive retention.

### gwtc (Gateway laptop webcam via MediaMTX RTSP)

Dev set cadence to 60s today (v2.27.3). Laptop is a chicken-coop box that has post-reboot dshow-zombie issues that `farmcam-watchdog` auto-recovers.

| Cadence | Images/day | Notes |
|---|---|---|
| 60s (today) | 1,440 | |
| 15-30s | 2,880–5,760 | probably fine; the laptop is just streaming MJPEG/H.264 at 15fps, RTSP pull is cheap |
| 5s | 17,280 | unknown if the dshow pipeline is robust at this rate for weeks. Would need to watch for zombies more carefully. |

**Bottleneck:** the laptop's dshow stability, not any fundamental capture limit. 30s is a safe bet; faster requires watchdog attention.

### house-yard (Reolink E1 Outdoor PTZ, HTTP snapshot polling)

Already polls at 5s (day) / 2s (night). This is the fastest-cadence camera in the system. Dev hasn't touched it. Probably already at max sustainable — Reolink rate-limits HTTP snapshots, and the 4K JPEG payload is ~800 KB.

**Bottleneck:** Reolink firmware rate limiting + network. Leave alone.

## Where "overloading" actually bites

In order of how painful each bottleneck is when you push volume:

1. **LM Studio / VLM inference (~98s per call on Gemma-4 31B).** If every frame hits the VLM, and we're capturing 40-60k frames/day across all cameras, the pipeline can't keep up. Must have queue + drop-oldest (my earlier plan called this out). Today the pipeline is stalled anyway, so this bite is deferred.

2. **Mac Mini disk (78 GB free right now).** At max volume across all four cameras, we'd accumulate 20-40+ GB/day. Retention is what keeps this livable. Current retention: strong 90d, decent 90d, concerns never. If decent stays at 90 days of a 40 GB/day flow, we run out of disk in ~2 days. Retention for decent needs to drop to ~14 days, or we need to cut decent entirely and only keep strong + concerns + a smart-sampled subset.

3. **S7 battery.** Only at S7 <30s cadence. Mitigation: keep it on AC.

4. **GWTC laptop stability.** Only at GWTC <30s cadence. Mitigation: watchdog is already running.

5. **Network.** Not a real limit at any sane cadence given everything is on the same LAN.

## Concrete proposal (for other dev to execute, not me)

Assuming Boss signs off:

| Camera | Today | Propose | Daily volume |
|---|---|---|---|
| S7 | 60s | **10s if on AC, 30s if on battery** | ~5.6 GB/day or ~1.9 GB/day |
| usb-cam | 60s | **2s** | ~8.6 GB/day |
| gwtc | 60s | **15s** | ~0.9 GB/day |
| house-yard | 5s (day) / 2s (night) | unchanged | ~35 GB/day (already the biggest) |

**Total worst case:** ~50 GB/day. With 78 GB free, we fill disk in < 2 days unless retention tightens.

**Retention proposal (paired with the cadence bump):**

- strong: 365d (keep gems essentially forever — they're rare and farm-2026 shows them)
- decent: 7d (aggressive — this is the bulk) OR: don't store `decent` to DB at all, only on disk for 48h
- concerns: null (unchanged — keep forever)
- raw capture (pre-VLM): 72h rolling, disk-only, no DB row

Without this retention change, the cadence bump fills disk. Both halves need to ship together.

## Pipeline status

- The pipeline is the thing that turns raw captures into DB rows with share_worth. Today it's stalled.
- Not in scope for this plan — restart the pipeline conversation after source volume is landed.
- When we do restart it: **capture must be decoupled from VLM.** Capture lands JPEGs on disk at max cadence; VLM is a separate worker that picks the newest un-captioned frame per camera and drops the backlog. This is the single most important architectural change for the volume goal.

## Decisions Boss needs to make

1. **S7 on AC or battery?** If AC, we can push to 10s. If battery, 30s is the floor.
2. **Retention cut for `decent`?** 7d / 14d / keep-90-but-no-decent-rows-at-all. Pick one.
3. **Is 2s on usb-cam too fast?** It's 8.6 GB/day. I'd pick 2s; happy to go slower if you want less disk burn.

Once you pick, the other dev can land the cadence bump + retention change as one release. I stay out of it unless handed a piece explicitly.

## What I am not doing in this branch

- Not editing capture code.
- Not editing pipeline config.
- Not editing retention code.
- Not starting any capture processes.
- Not probing hardware while other dev is live.

---

**done. awaiting Boss on the three decisions above.**
