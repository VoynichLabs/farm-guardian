# 22-Aug-2026 — New Internet Install: Breakage Audit & Recovery Plan

**Status:** Pre-install audit. **No code has been changed.** This is the inventory + the
post-install checklist. Do not "fix" hardcoded IPs before the new subnet is known — you would
be guessing twice.

---

## THE ONE QUESTION THAT FORKS EVERYTHING

**Is the TP-Link Archer AX55 staying as the LAN router (new ISP box in bridge/passthrough), or
is the ISP's box becoming the router?**

| | AX55 stays behind the ISP box | ISP box becomes the router |
|---|---|---|
| Subnet | stays `192.168.0.x` | likely `192.168.1.x` or `10.0.0.x` |
| SSID / WiFi password | unchanged | **new** |
| DHCP reservations | preserved | **gone** |
| Blast radius | ~zero on the LAN; only the tunnel matters | **every WiFi device needs hands** |

**Recommendation: ask the installer to bridge the ISP box and keep the AX55.** That single
choice turns this from a multi-hour re-provisioning job into a no-op. Everything below is
written for both branches.

---

## PRE-INSTALL BASELINE (captured 22-Aug-2026, all six cameras UP)

Verified live before the install — this is the known-good state to compare against afterwards.

| Device | IP | MAC (authoritative) | Link | IP method |
|---|---|---|---|---|
| Router (AX55) | 192.168.0.1 | `5c:a6:e6:16:f1:10` | — | — |
| `house-yard` Reolink E1 | 192.168.0.88 | `bc:09:b9:89:e4:fd` | **WiFi** | **DHCP** |
| `duo2` Reolink Duo 2 | 192.168.0.155 | `78:93:c3:8e:36:0d` | **WiFi** | **DHCP** |
| `s7-cam` Galaxy S7 | 192.168.0.249 | `2c:0e:3d:09:77:a4` | **WiFi** | **STATIC ON PHONE** |
| `farm-pi5` (2 cameras) | 192.168.0.17 | `88:a2:9e:a2:e6:23` | **Ethernet** | **DHCP** |
| MacBook Air | 192.168.0.50 | `64:76:ba:a2:7e:64` | WiFi | DHCP + mDNS |
| Mac Mini (Bubba) | 192.168.0.217 | — | Ethernet | DHCP |

**Keep this table.** After the install, the MACs are how you find each device on a new subnet.

### DHCP reservations currently on the AX55 — LOST if the router is replaced

From `~/bubba-workspace/memory/reference/network.md` (Advanced → Network → DHCP Server →
Address Reservation). **This table is STALE and should not be recreated verbatim:**

| Reserved MAC | → IP | Status |
|---|---|---|
| `BC-09-B9-89-E4-FD` FarmGuardian1 | .88 | ✅ still valid — recreate |
| `78-93-C3-8E-36-0D` Duo2 | .155 | ✅ still valid — recreate |
| `8C-F5-A3-B6-5A-E5` "Galaxy-S7" | .249 | ❌ **RETIRED handset.** Live phone is `2C:0E:3D:09:77:A4` |
| `EC-71-DB-58-70-7E` Duo2 (2nd) | .14 | ❓ unverified, no live device |
| `F0-35-75-81-2C-45` GWTC | .69 | ❌ **retired 10-Aug-2026** — do not recreate |

Note `farm-pi5` and the MacBook Air have **no reservation** and don't need one — both are found
via mDNS. That is why they are the only zero-touch devices in the fleet.

---

## BREAKS EITHER WAY (do these regardless of the branch)

### 1. Cloudflare tunnel runs on QUIC — this is the sneaky one
`~/Library/LaunchAgents/com.cloudflare.tunnel.farm-guardian.plist` runs
`cloudflared --protocol quic`, which needs **outbound UDP/7844**. Plenty of ISP-supplied routers
block or mangle outbound UDP.

**Failure signature: the `cloudflared` process is healthy and logging, but
`guardian.markbarney.net` is dead from outside.** That will eat an hour if you don't know it.

Fix is a one-word edit — change `quic` to `http2` (falls back to TCP/443):
```bash
sed -i '' 's|<string>quic</string>|<string>http2</string>|' ~/Library/LaunchAgents/com.cloudflare.tunnel.farm-guardian.plist && launchctl bootout gui/$(id -u)/com.cloudflare.tunnel.farm-guardian; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cloudflare.tunnel.farm-guardian.plist
```
The tunnel is outbound-only, so **CGNAT / no public IP does not matter** and no port forwarding
is needed. Only UDP blocking matters.

### 2. The outage window itself will fire a false Birdcatraz alarm
`com.farmguardian.birdcatraz-watchdog` probes `farm-pi5` every 5 min, `FAIL_THRESHOLD=2`, so
**~10 minutes into the internet drop it posts a Discord alert mentioning Boss saying the
Birdcatraz circuit tripped.** It is designed to post once and then a recovery notice.

**That alert is EXPECTED during the install and is not a breaker trip. Do not walk out to
Birdcatraz.** (Note: the LAN may well keep working while only the WAN is down — in that case
the watchdog stays quiet. It only fires if the LAN goes down too, e.g. the router is replaced.)

### 3. Content lanes that lose material permanently vs. lanes that self-heal

**Safe — bounded by CONSUMPTION, not age. These fully recover, no action needed:**
- `select_all_unposted_story_gems` — no time window at all. Backlog drains at 5/tick hourly.
- Diary promotion + reaction-gated lanes — a reaction never expires (v2.71.1).

**Lossy — bounded by a real time window. Material captured-but-not-captured during the outage
is gone:**
- `select_s7_daily_reel_gems` — ONE local calendar day. A long outage today = a short 21:00 reel.
- `select_s7_weekly_gems_reel_gems` — 7-day window (documented, correct — do not "fix" it).
- `select_multiday_timelapse_gems` — the weekly (Sun 11:00/11:15) and monthly (1st, 08:00/08:15)
  house-yard + duo2 time-lapses. A gap shows as a jump in the finished reel.

### 3b. ⚠️ TWO LANES AUTO-PUBLISH TO PUBLIC INSTAGRAM TONIGHT — Boss's call before 21:00

The thin-reel outcome is **not merely cosmetic**: `ig-s7-daily-reel` (21:00) and
`ig-jieli-dashcam-timelapse-reel` (21:30) both run `approval_required=False`, so a short or
gap-ridden reel goes **live on `@pawel_and_pawleen`**, not just into a log.

If the install eats a chunk of daylight, consider parking both lanes for tonight:
```bash
launchctl bootout gui/$(id -u)/com.farmguardian.ig-s7-daily-reel
launchctl bootout gui/$(id -u)/com.farmguardian.ig-jieli-dashcam-timelapse-reel
```
and bootstrap them back tomorrow:
```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.farmguardian.ig-s7-daily-reel.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.farmguardian.ig-jieli-dashcam-timelapse-reel.plist
```
**Boss's decision, but he should get to make it before 21:00.** Nothing needs recovering either
way — do not try to backfill.

### 4. Outbound API jobs during the WAN outage
Discord / Instagram / Facebook / eBird / Nextdoor all fail closed and retry on their next tick.
The IG 25-per-rolling-24h publish quota is untouched by an outage. No action.

---

## BREAKS ONLY IF THE SUBNET OR SSID CHANGES

Ranked by **how hard the device is to get hands on** — worst first.

### 🔴 1. `s7-cam` — Galaxy S7, STATIC IP set on the phone, in the coop
**VERIFIED 22-Aug-2026, not assumed.** The router's reservation table pins
`8C-F5-A3-B6-5A-E5` → `.249` — but that is the **RETIRED** handset. The device actually sitting
at `.249` right now is `2C:0E:3D:09:77:A4`, the replacement phone swapped in 10-Aug-2026.

**So the reservation does not cover the current phone, and `.249` is held by the phone's own
static config.** This confirms CLAUDE.md's static-on-phone claim by direct measurement.

On a new subnet the phone is both unreachable **and unable to route** — it won't even reach the
internet, so IP Webcam keeps running while nothing can see it.

**No remote recovery path exists.** `adb devices` on this Mini is **empty** — the handset is not
tethered here. (CLAUDE.md says ADB works on the new phone; true *when tethered*, and it isn't.)
This is a walk to Birdcatraz and on-screen work.

⚠️ **The replacement is SM-G930V on Android 6.0.1, and the Android-8 menu paths in the existing
S7 runbooks do not exist on it.** On Android 6 the static/DHCP toggle is under **"Advanced
options" while joining the network** — so if the phone has already auto-joined, you must
**forget the network and re-join** to reach that screen. Budget for that or it is a confused
second trip.

**Recommendation: set it to DHCP and add a router reservation for `2C:0E:3D:09:77:A4`.** The
static-on-phone setup is exactly what makes this a physical trip every time the network changes.

### 🟠 2. `house-yard` + `duo2` — Reolink, WiFi, headless, no screen
Both are **DHCP**, so on a new subnet with the *same* SSID they simply pick up new addresses and
you only need to update the two config files. **But both are on WiFi** — a new SSID means they
cannot join at all and need re-provisioning, which on a Reolink realistically means the phone app.

⚠️ **This is in direct tension with the standing repo rule "never suggest using the Reolink app."**
That rule is about *operating* the camera (we are the app — use raw JSON commands). It does not
cover **joining a camera to a brand-new WiFi network**, which the HTTP API cannot do for you
because the API is only reachable once the camera is already on the network. Flagging rather than
silently violating it. If the SSID is preserved, this never comes up.

⚠️ **`house-yard` is hand-aimed and a power event moves it.** It re-homes its PTZ motors on boot
and lands *near*, not on, the saved aim. After the install, **check the framing, not just that it
is online** — recover with `preset/goto` id **6** (`Main`, pan 2214 / 110.7°). Do not goto any
other preset id.

### 🟢 3. `farm-pi5` — Ethernet + DHCP + mDNS. Safe.
Confirmed `dynamic` on eth0. It will take a new address automatically and `farm-pi5.local`
follows it. **Both cameras it hosts (`usb-webcam-1080p` :8090, `jieli-dashcam` :8091) need no
config change at all** — both configs already point at `farm-pi5.local`, not an IP.

This is the model the rest of the fleet should copy.

### 🟢 4. MacBook Air — WiFi + mDNS, config already uses `Marks-MacBook-Air.local`. 
Re-joins by itself if the SSID is unchanged; needs GUI hands if it isn't.
**Chicken-and-egg to be aware of:** the `ssh … 'c -p "…"'` remote-Claude pattern only works
*after* the Air is back on the network, so it cannot be used to put it back on the network.

### ⚪ 5. GWTC / Dominator — both retired 10-Aug-2026. Ignore.
GWTC is already off the LAN. Nothing in Guardian consumes either feed. One line of note only.

---

## mDNS IS LOAD-BEARING — VERIFY IT AFTER THE INSTALL

**Three of the six cameras resolve via `.local`.** If the new router has **AP/client isolation**
on by default (several ISP boxes do), mDNS dies and those three go dark *even on the same
subnet*, with everything looking otherwise healthy.

First thing to check post-install:
```bash
dscacheutil -q host -a name farm-pi5.local; dscacheutil -q host -a name Marks-MacBook-Air.local
```
If those come back empty, turn OFF client/AP isolation on the new router before debugging
anything else.

---

## HARDCODED IPs — FULL INVENTORY

**Live config (must be updated if the subnet changes):**
| File | Line | Value |
|---|---|---|
| `config.json` | 6 | `house-yard` ip `192.168.0.88` |
| `config.json` | 23, 30 | `s7-cam` ip + `http_base_url` `192.168.0.249:8080` |
| `config.json` | 82, 89 | `duo2` ip + `rtsp_url_override` `192.168.0.155` |
| `tools/pipeline/config.json` | 133, 134 | `house-yard` `reolink_base` + VLM context string |
| `tools/pipeline/config.json` | 158 | `s7-cam` `ip_webcam_base` |

⚠️ **Both config files must be edited together** — the standing repo trap. Verify with:
```bash
grep -n 'http_base_url\|ip_webcam_base\|reolink_base\|rtsp_url_override' config.json tools/pipeline/config.json
```
Then reload **both** services:
```bash
launchctl kickstart -k gui/$(id -u)/com.farmguardian.guardian && launchctl kickstart -k gui/$(id -u)/com.farmguardian.pipeline
```

**Live tooling:**
- `tools/birdcatraz-watchdog/watchdog.py:89` — `PI_HOSTS = ("farm-pi5.local", "192.168.0.17")`.
  Already falls back to mDNS first, so it self-heals. **No change needed.**

**Dead / retired — do NOT waste time on these:**
- `tools/flock-response/playback.py:38`, `measure_latency.py`, `push-sounds-to-gwtc.sh` — GWTC `.68`, retired.
- `deploy/gwtc/*.ps1` — GWTC, retired (`wifi-watchdog.ps1:32` hardcodes gateway `192.168.0.1`).
- `deploy/dominator-cam/*.bat` — Dominator `.194`, retired.
- `deploy/s7-settings-watchdog/*` — watchdog retired 10-Aug-2026.
- `tools/s7_http_smoke.py`, `scripts/add-camera.py` — CLI defaults / doc examples only.

**Outside this repo, will be wrong after the install:**
- `~/bubba-workspace/memory/reference/network.md` — the master device table. Flag for update; do
  not rewrite blind.
- Router admin credentials in that same doc will be different if the router is replaced.

---

## POST-INSTALL CHECKLIST (run in this order)

```bash
# 1. What subnet are we on now?
ifconfig | grep "inet 192\|inet 10\.\|inet 172\." ; netstat -rn | grep '^default'

# 2. Does mDNS still work? (if empty -> disable AP/client isolation on the router)
dscacheutil -q host -a name farm-pi5.local ; dscacheutil -q host -a name Marks-MacBook-Air.local

# 3. Find the WiFi devices by MAC. ARP cache is COLD on a new subnet - you MUST
#    sweep first or these greps return nothing and it reads as "the cameras are gone".
SUB=$(ifconfig | awk '/inet 192|inet 10\.|inet 172\./{split($2,a,"."); print a[1]"."a[2]"."a[3]; exit}')
echo "sweeping $SUB.0/24 ..."; for i in $(seq 1 254); do (ping -c1 -W1 $SUB.$i >/dev/null 2>&1 &); done; sleep 8
arp -an | grep -iE 'b9:89:e4:fd|c3:8e:36|3d:9:77:a4'

# 4. Is the tunnel actually passing traffic? (process-up is NOT proof)
curl -sS -o /dev/null -w '%{http_code}\n' --max-time 15 https://guardian.markbarney.net/

# 5. Guardian's own view of the fleet
curl -s --max-time 5 http://localhost:6530/api/cameras | python3 -m json.tool | grep -E '"name"|"online"'

# 6. house-yard framing — a power event moves the aim. LOOK at the picture.
curl -s --max-time 10 'http://localhost:6530/api/v1/cameras/house-yard/snapshot' -o /tmp/hy-check.jpg && open /tmp/hy-check.jpg
```

---

## FOLLOW-UP WORK (propose, do not do today)

Convert hardcoded IPs to mDNS hostnames wherever the device supports it — exactly what
`farm-pi5` and the MacBook Air already do, and the reason those two are the only zero-touch
devices in the fleet. Candidates: the two Reolinks (check for a `.local` name) and the S7
(switch to DHCP + reservation first). This is a separate plan doc and needs Boss's approval;
it must not be bundled into install-day firefighting.

## Docs/Changelog touchpoints
No behavior changed, so **no CHANGELOG entry is warranted yet.** If the subnet does change and
the config files are edited, that IS a behavior change and needs a CHANGELOG entry plus an
update to the Environment roster table in `CLAUDE.md` and to
`~/bubba-workspace/memory/reference/network.md`.

---

## VERIFIED KNOWN-GOOD BASELINE (22-Aug-2026, immediately pre-install)

Measured, not assumed. This is what "working" looks like — compare against it afterwards.

- `https://guardian.markbarney.net/` → **HTTP 200** (tunnel passing real traffic, not just process-up)
- Guardian `/api/cameras` → **all six online**: `house-yard`, `s7-cam`, `usb-webcam-1080p`,
  `macbook-air-facetime`, `jieli-dashcam`, `duo2`
- All six device TCP probes UP; `farm-pi5.local` → `.17` and `Marks-MacBook-Air.local` → `.50`
  both resolving via mDNS
- Mac Mini `192.168.0.217`, gateway `192.168.0.1`, DNS served by the router itself (`192.168.0.1`)

⚠️ **DNS is the router.** `/etc/resolv.conf` points at `192.168.0.1`. If the router is replaced,
DNS changes with it — a resolver that is slow or filtering will look exactly like "the APIs are
down" (Discord/IG/FB posts failing) while the link itself is fine. Check DNS before debugging
any outbound job.

**Neither Reolink advertises an mDNS/Bonjour name** (checked via `dns-sd -B _http._tcp`), so the
follow-up "convert to hostnames" work cannot cover those two — they need a DHCP reservation on
the new router instead. That is the only durable fix for them.

---

# ✅ POST-INSTALL VERIFICATION — 22-Aug-2026 11:21 EDT — ALL CLEAR

**Outcome: the good branch. The LAN was never touched; only the WAN changed.**

| Check | Result |
|---|---|
| Subnet / gateway / DNS | `192.168.0.x`, gw `192.168.0.1`, DNS `192.168.0.1` — **unchanged** |
| Tunnel from outside | `guardian.markbarney.net` → **HTTP 200**. QUIC survived; **no `http2` fallback needed** |
| mDNS | `farm-pi5.local`→`.17`, `Marks-MacBook-Air.local`→`.50` — both resolving |
| All 6 devices | UP at their **original IPs** (.88 / .249 / .155 / .17 / .50 / .1) |
| Guardian `/api/cameras` | **all six online** |
| `farm-pi5` uptime | since 17-Aug — **did not reboot** |
| Birdcatraz watchdog | **135 ticks today, 00:04→11:19, ZERO gaps, zero alerts** |
| `house-yard` aim | **unchanged** — verified by pixel comparison of the 09:00 pre-install archive frame against a fresh 11:21 snapshot: identical post, wire, shepherd's hook, treeline |

**The watchdog's unbroken 5-minute cadence is the proof the LAN never dropped** — which is also
why the S7's static IP, the single biggest risk in this plan, was never exercised. It is still
static-on-phone and still the fleet's one device that cannot survive a subnet change. **The risk
is deferred, not retired.**

### The one thing that actually changed
**The Mac Mini's own LAN IP drifted `192.168.0.217` → `192.168.0.10`.** Harmless — the Mini is a
client, and the tunnel reaches Guardian over `localhost`. Stale references corrected in
`CLAUDE.md` (Environment section) and `~/bubba-workspace/memory/reference/network.md`.

### Still worth doing (unchanged by this outcome)
1. **S7 → DHCP + a reservation for `2C:0E:3D:09:77:A4`.** The current reservation is for the
   retired handset. Next time the network really does change, this is a trip to the coop.
2. **Rebuild the reservation table** — it still lists GWTC (retired) and the old S7 MAC.
3. The `quic` → `http2` one-liner above stays on file in case the ISP ever starts filtering UDP.
