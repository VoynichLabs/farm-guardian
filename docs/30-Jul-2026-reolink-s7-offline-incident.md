# 30-Jul-2026 — s7-cam, duo2 and house-yard outage: investigation record

**Investigated by:** Claude Fable 5, 30-Jul-2026 ~10:00–11:10 EDT.
**Status:** `house-yard` recovered. `s7-cam` and `duo2` are hardware failures needing physical attention.
No code or config changed by this investigation.

---

## One-line answer

`s7-cam` is water-damaged and dead. **`duo2` is ALIVE and fully working** — recovered 12:00
on 30-Jul, verified live at full 4608x1728 with a current clock. It was a **connectivity**
failure, never a damaged camera: it lost its cloud path on the 27th, kept serving perfect
local video until it stopped dead at 05:04:26 on the 30th, then came back once Boss brought it
indoors and replaced the power cord / reseated the Wi-Fi antenna. `house-yard` had a separate,
milder fault and recovered on a power cycle.

> ### ⚠️ I called this a dead camera. That was wrong.
> Mid-investigation I concluded duo2 was a water-killed hardware failure and advised starting a
> warranty claim. Two facts from Boss overturned it: the Reolink app had been blind since the
> **27th**, while Guardian was still pulling live frames on the **30th** — impossible for a
> camera that died on the 27th — and the camera was **still powered**. The lesson is that
> "did not survive a power cycle" is worthless evidence when the fault may be *in the power
> delivery itself*: cycling the wall outlet proves nothing if the cord or PSU is the broken
> part. Never again conclude "dead unit" from failed power cycles alone.

---

## ⚠️ Correction to an earlier reading in this same investigation

An earlier pass of this document concluded the two Reolinks "died 12 minutes apart this
morning (10:06 and 10:18)" and inferred a shared, recent cause. **That was wrong**, and the
way it was wrong is worth recording because the trap is easy to fall into again:

The error was in the query, not the data. I looked for the earliest row carrying the
**most recent** sha256 — and that hash only starts at 10:18, so it reported "frozen since
10:18." It silently skipped an earlier, larger frozen block. The true frame history is:

| Frame | Dimensions | Rows | Span (UTC) |
|---|---|---|---|
| unique every cycle | 4608x1728 | 1 each | … up to 09:04:16 — **camera genuinely live** |
| `a3a5dd2081` | 4608x1728 | **1867** | 09:04:26 → 14:18:24 — frozen |
| `655f61a9c4` | **2304x864** | 298+ | 14:18:31 → still writing now — frozen |

So `duo2` froze at **05:04:26 local**, and at 10:18 the main RTSP stream dropped and Guardian
fell back to the **sub-stream** — which was already showing the same frozen 05:04:21 scene at
half resolution. That resolution change is the only thing that happened at 10:18.

Two lessons: **group by hash across the whole window rather than anchoring on the latest one**,
and **the camera's burned-in OSD timestamp is the ground truth**, not the filename, the row
count, or the hash. Frames filed five hours apart (`13-00-01.jpg`, `14-18-31.jpg`) both read
`30/07/2026 05:04:21 THU`. Read the pixels before trusting the metadata.

## Current state

| Camera | State | Evidence |
|---|---|---|
| `house-yard` | ✅ **recovered** | live 4K, OSD `11:05:47`, at `192.168.0.2`; guardian.log `snapshots resumed after 301 failures` 10:56:34 |
| `duo2` | ✅ **ALIVE — camera fine** | back at `.155` 12:00; direct RTSP grab at 12:05:01 gives sharp full-res 4608x1728. ⚠️ Guardian's snapshot session is still stuck and needs a restart |
| `s7-cam` | ❌ **dead — water damage** | confirmed by Boss |

### duo2 — what actually failed, and what fixed it

The camera was **never damaged**. Proof it was healthy long after the app lost it: a frame
filed `2026-07-29T18:00:01Z` carries the camera's own OSD clock reading `29/07/2026 13:59:56`,
matching real time to within 5 seconds. Full 3.7–3.9 MB frames arrived every 10s, each one
unique, right up to the instant it stopped. That is a pristine link, not a degrading one.

Two distinct faults, both on the **connectivity** side:

1. **27-Jul — lost the cloud/P2P path.** The Reolink app goes through Reolink's servers, so
   app-blind-but-LAN-perfect means the camera stopped reaching the internet while local RTSP
   stayed flawless.
2. **30-Jul 05:04:26 — stopped dead.** Froze at an exact second and left the LAN.

### ✅ ROOT CAUSE CONFIRMED BY BOSS: the stock Reolink power adapter

Boss settled it — swapping to a **non-Reolink power adapter** made the camera work
"flawlessly". This is the **second occurrence**: the same thing happened to another Reolink on
this farm a few weeks earlier. **Reolink's bundled adapters fail, and they are now the first
thing to check when a Reolink vanishes.** Promoted to `CLAUDE.md` so the next agent checks the
adapter before spending an afternoon on network forensics.

This retroactively explains everything that looked contradictory:

| Observation | Under the adapter theory |
|---|---|
| Absent from the network, no ARP, no lease | Camera unpowered — correct, and uninformative |
| Perfect full-size frames until an exact second | Power removed instantly; no gradual degradation |
| Repeated power cycles achieved nothing | The break is downstream of the outlet |
| Invisible in the Reolink app too | Cloud path needs the camera powered as well |

The Wi-Fi antenna reseat was a red herring — a coincidental second change made at the same
moment. Nothing was wrong with the radio.

**Ethernet is NOT an option here** — Boss confirms duo2 is too far from the house to reach with
a cable. Disregard the earlier suggestion to wire it; its wired reservation
(`EC-71-DB-58-70-7E`→`192.168.0.14`) stays unused. Wi-Fi plus a decent adapter is the
configuration.

## Timeline (local EDT)

| When | What |
|---|---|
| 29-Jul ~12:56 | `s7-cam` last frame — water damage |
| 30-Jul 03:00 | `s7-cam` hard down in guardian.log |
| **30-Jul 05:04:21** | **`duo2` freezes** — pre-dawn, IR mode. Camera OSD ground truth |
| 30-Jul 05:04:26 | first duplicate archive row — 1867 copies of one frame follow |
| 30-Jul 05:04:35 | Guardian's duo2 snapshot path starts failing — 14s later |
| 30-Jul ~10:06 | `house-yard` capture fails (separate, milder fault) |
| 30-Jul 10:18:31 | duo2's main RTSP stream drops; Guardian falls back to the (also frozen) sub-stream |
| 30-Jul ~10:50 | Boss power cycles |
| 30-Jul 10:56:34 | **`house-yard` recovers** |
| 30-Jul 11:05 | `duo2` still absent — no lease, nothing on any address |
| 30-Jul ~11:55 | Boss brings duo2 indoors; new power cord fitted **and** a Wi-Fi antenna reseated |
| 30-Jul 12:00 | **`duo2` re-associates at `.155`** — ping, ARP (`78:93:c3:8e:36:0d`), ports 80/554/8000 all open |
| 30-Jul 12:05:01 | direct RTSP grab: sharp, full 4608x1728, current clock — **camera confirmed healthy** |
| 30-Jul ~12:06 | Boss confirms it is live in the Reolink app again — cloud path restored too |

The through-line is **overnight moisture**, not the 29-Jul rearrangement. `duo2` froze pre-dawn
in IR mode; its last frame shows bright arc-shaped streaks across the upper frame, consistent
with moisture on the lens catching the IR illuminator. (Be careful with that visual on its own
— the documented spider-web artifact looks superficially similar, though it presents as
vertical bars hugging the frame edge rather than broad arcs. The load-bearing evidence is the
timing plus the confirmed-wet S7, not the streaks.)

`house-yard` is also outdoors and survived, so this is not blanket weather damage — `duo2`
likely has a specific ingress point.

## ~~Why duo2 is a hardware failure~~ — RETRACTED, and why the reasoning failed

This section originally argued duo2 was dead hardware. **It was wrong** and is kept only so the
faulty reasoning stays visible. The observations were accurate — while it was down there really
was no ICMP, no ARP, all ports closed at `.155`/`.14`/`.89`/`.2`, nothing on a full `/24` sweep,
and no fresh DHCP lease. The *inference* was the error:

> "A camera that fails to associate after a power cycle is not a DHCP, config, or Wi-Fi problem."

That reads as decisive but assumes the camera is actually receiving power during the cycle.
**If the fault is in the power cord or PSU, cycling the wall outlet changes nothing** — and
each failed cycle then reads as more confirmation the camera is dead. The evidence was
self-reinforcing in the wrong direction, and it led to advice (start a warranty claim, expect
to be out the money) that would have been costly had Boss accepted it.

**Rule for next time:** absence from the network proves absence from the network. It does not
distinguish *dead camera* from *unpowered camera*. Before concluding hardware death, confirm
power independently of the outlet — a status LED, IR illuminators glowing at night, or a known
good cord — and treat repeated failed power cycles as an untested assumption rather than
mounting evidence.

## Ruled out (do not re-litigate)

- **The Mac Mini's network change.** The MacBook Air, untouched and independent, could not reach
  the cameras either. (Real but unrelated: `en0` Ethernet is now `inactive`; the Mini fell back
  to Wi-Fi at **`192.168.0.217`**. Docs calling it wired at `.54`/`.105` are stale.)
- **DHCP drift.** Reservations intact; repeated sweeps found nothing at any other address.
- **The 2.4 GHz radio.** GWTC's Realtek 8723DU is 2.4-only and fully reachable throughout.
- **A shared outlet event this morning.** This followed from the 10:06/10:18 error corrected above.

## Reolinks have TWO MACs — this matters

`house-yard` came back at `.2`, an address with **no reservation**, using MAC
`EC-71-DB-4C-AD-53` rather than its reserved `BC-09-B9-89-E4-FD`→`.88`. Its OSD confirms it is
`FarmGuardian1`, so it is genuinely the same camera on its **other interface**. This is also why
`duo2` holds two reservations (`78-93-C3-8E-36-0D`→`.155` and `EC-71-DB-58-70-7E`→`.14`) — the
CHANGELOG's "drifted from .14/.15 after the 4-Jul ethernet→WiFi flip" is the same phenomenon.

**Consequence:** a reservation pins only one interface. When a camera comes up on the other one
it lands on an arbitrary address, which is exactly how `house-yard` ended up at `.2`. Worth
reserving *both* MACs per camera.

⚠️ **Do not brute-force Reolink credentials while diagnosing.** Both configured passwords were
rejected at `.2` during this investigation, which briefly suggested a foreign camera. It was
lockout from repeated attempts — Reolink locks out after successive failures, and Guardian was
authenticating against that same camera fine. Read the OSD or ask Guardian; don't retry passwords.

## ✅ Router change applied 30-Jul-2026 ~11:45 (approved by Boss)

Added the missing DHCP reservation for house-yard's **second (Wi-Fi) interface**:

| Device | MAC | Reserved IP |
|---|---|---|
| FarmGuardian1 | `EC-71-DB-4C-AD-53` | **`192.168.0.2`** ← new |
| FarmGuardian1 | `BC-09-B9-89-E4-FD` | `192.168.0.88` |
| Galaxy-S7 | `8C-F5-A3-B6-5A-E5` | `192.168.0.249` |
| Duo2 | `78-93-C3-8E-36-0D` | `192.168.0.155` |
| Duo2 | `EC-71-DB-58-70-7E` | `192.168.0.14` |
| 653Pudding (GWTC) | `F0-35-75-81-2C-45` | `192.168.0.69` |

**Both interfaces of both Reolinks are now pinned**, so neither camera can drift again
whichever interface it comes up on. `.2` was chosen because the camera already held it —
pinning in place avoided touching `config.json` or disturbing a camera that had just
recovered. Verified still capturing afterwards. The router labels the new entry
`FarmGuardian1` itself, which is independent confirmation that `.2` is house-yard.

Note for whoever automates the router GUI next: `page.fill()` sets the value **without**
waking the firmware's validation, so the SAVE button stays disabled and the click silently
does nothing. Use `press_sequentially()`. And verify against the **Address Reservation
section only** — searching the whole page body gives a false pass, because the MAC also
appears in the DHCP Client List as an active lease. Both traps cost a cycle here.

## duo2 SD card + native time-lapse — ANSWERED FROM THE CAMERA, 30-Jul-2026

Boss fitted a new 128 GB SD card and asked whether the camera can make time-lapses natively.
Both questions answered by querying the camera directly (`GetAbility`, `GetHddInfo`, and
command probes) rather than trusting docs — Reolink's support page excludes the original Duo
family but predates the Duo 2, so it cannot settle it.

**1. ⚠️ The SD card is NOT mounted.** `GetHddInfo` returns:

```json
{"capacity": 0, "format": 0, "mount": 0, "number": 0, "size": 0, "storageType": 2}
```

`mount: 0`, `capacity: 0`, `format: 0` — the camera reports **no card at all**, not merely an
unformatted one. **Until this is fixed the camera has no local storage and any recording plan
is moot.**

**Almost certainly the filesystem: Reolink cameras read FAT32 only, and every microSD of 64 GB
or larger ships exFAT from the factory.** A new 128 GB card is exFAT out of the box, so the
camera cannot mount it and reports zeros. Card *size* is not the problem — the Duo 2 WiFi
supports up to 256 GB (only the Duo 2 LTE is capped at 128 GB).

**Remote format is possible in principle but CANNOT fix this case.** Findings, so nobody
re-derives them:

- The `Format` command **exists** on this firmware. Probing distinguishes it clearly:
  `{"HddInfo":{"number":0}}` returns `rspCode=-13 set config failed` (right shape, operation
  failed) while `{"HddInfo":{"id":0}}` returns `rspCode=-4 param error` (wrong shape).
  **So the correct parameter shape is `param={"HddInfo":{"number":<slot>}}`** — worth keeping
  for when there IS a readable card.
- It fails with `-13` for every slot number including a non-existent one, i.e. it fails before
  slot validation — consistent with "no mountable card".
- `FormatHdd`, `GetStorageInfo`, `GetSdInfo` are all `not support` on this model.
- A **remote `Reboot`** (`{"cmd":"Reboot","action":0,"param":{}}`) succeeded and the camera came
  back cleanly in ~90s — but `GetHddInfo` was unchanged. Remote reboot is a genuinely useful
  tool here; it just doesn't solve this.

### ✅ RESOLVED — formatted from the phone app, no trip to the camera

Boss hit **Format in the Reolink app** and it worked. `GetHddInfo` now returns
`capacity: 122095` (~119.2 GB), `mount: 1`, `format: 1`, fully free.

> **⚠️ Correction worth internalising: `GetHddInfo` reporting all zeros does NOT mean the card
> is absent.** I read `capacity: 0, mount: 0` as "no card physically detected" and was about to
> send Boss up a ladder. The app formatted that same card seconds later. The zeros only mean
> **not mounted** — an unreadable filesystem reports identically to an empty slot over this API.
> **Always try the app's Format before concluding the card is missing or the slot is faulty.**

The FAT32/exFAT theory was still almost certainly the cause (a new 128 GB card ships exFAT,
Reolink reads FAT32), and the in-camera format converted it. The API's `Format` command could
not do it, but the app's could — so the app reaches a code path the documented API does not.

**Recording state after the fix** (`GetRecV20` — note plain `GetRec` is `not support` on this
firmware): recording is **on**, `overwrite: 1`, `preRec: 1`, `postRec: 15 Seconds`, with the
weekly schedule fully enabled (168/168 hours) for `MD`, `AI_PEOPLE`, `AI_VEHICLE` and
`AI_DOG_CAT` — but **`TIMING` is 0/168, so there is no continuous recording**, only
motion/AI-triggered clips.

**`TIMING` was then enabled (168/168) via `SetRecV20`, so the camera now records continuously.**
All other tracks were left untouched.

> **⚠️ Framing error, corrected by Boss.** I presented this as a trade-off against "your
> security footage" and held off changing it on those grounds. **This is not a security
> system — it is chicken observation**, which `CLAUDE.md` states explicitly ("Do NOT frame
> Guardian as a security/predator system"). With motion clips carrying no special value, the
> trade-off I described was imaginary and the decision was obvious: continuous coverage is the
> whole point. Re-read that rule before reasoning about what footage is "worth" keeping.

With overwrite on, the card holds a rolling window (roughly a day and a half at 4K) — which is
in the same range as the existing 48h raw retention, and is a source for building time-lapses
from real video instead of one snapshot per 10s.

**2. The Duo 2 has NO native Time Lapse feature.** Every time-lapse command is rejected by the
firmware:

| Command | Result |
|---|---|
| `GetTimeLapse` | `rspCode=-9 not support` |
| `GetTimelapse` | `rspCode=-9 not support` |
| `GetSnapTimeLapse` | `rspCode=-9 not support` |
| `GetTimeLapseCfg` | `rspCode=-9 not support` |
| `GetAiSnapTlps` | exists, but `enable: 0` and `GetAbility` reports `supportAiSnaptlps permit: 0` |

`aiSnaptlps` is an AI-triggered snapshot burst (`snapDuration` 3000 ms), **not** a time-lapse,
and it is not permitted on this model anyway. This matches Reolink excluding the whole Duo
line — the dual-lens panoramic pipeline evidently can't feed their time-lapse function.
**Do not go looking for this feature again; the camera has already said no four different ways.**

**What IS supported** (from `GetAbility`): `sdCard permit 6`, `supportRecordEnable permit 6`,
and an extensive FTP suite (`supportFtpTask`, `supportFtpPicCaptureMode`, `ftpSubStream`,
`supportFtpEnable`). So once the card mounts, continuous recording to SD works, and
`reolink_aio` already has `request_vod_files()`, `get_vod_source()` and `download_vod()` to
pull that footage. Building the time-lapse **locally from recorded video** would beat the
current one-snapshot-per-10s lane on quality and would survive a network outage — which is
exactly what cost ~7 hours of footage today.

## Open items
- Rows labelled `house-yard` between 07:51 and 10:06 today came from the `.2` binding made by
  discovery's match-by-name. Given the OSD confirms identity, provenance is fine — but the
  underlying name-matching weakness stands (see the spawned task).
- `tools/pipeline/config.json:108` has `"reolink_base": "http://192.168.0.89"` — a dead key;
  `reolink_snapshot` routes via `localhost:6530`.
- `usb-cam` stopped writing rows 29-Jul 10:02, but GWTC is reachable — that is the documented
  grabber failure with its own fix (`C:\farm-services\restart-usbcam.ps1`), unrelated to this.

## Notes for whoever picks this up

- Router read **read-only** (DHCP Server + Address Reservation). No setting changed.
- `ping` alone is invalid across the wired/wireless boundary on this router; it was usable here
  only because the Mini is currently on Wi-Fi. `nc -z` plus `arp -n` is the reliable pair, and
  **`ARP incomplete` is the strongest "it is not on the air" signal available.**
- `dominator-cam` down is expected (only live when Boss starts it).
