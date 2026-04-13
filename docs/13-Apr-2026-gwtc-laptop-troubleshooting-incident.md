# GWTC Laptop Reachability Incident — Troubleshooting Writeup

**Author:** Claude Opus 4.6
**Date:** 13-April-2026
**Purpose:** Document an afternoon of misdiagnosing the Gateway laptop (gwtc) as unreachable so future Claude sessions don't burn an hour of Boss's time speculating about turkeys flipping firewalls.

This is paired with the "Network & Machine Access" section in `CLAUDE.md` and the network reference at `~/bubba-workspace/memory/reference/network.md`.

---

## TL;DR

If gwtc seems "unreachable" from the Mac Mini:

1. **Don't ping.** ICMP is blocked between wired and wireless on this router. Ping will always fail across mediums regardless of state.
2. **Don't theorize about Windows Firewall.** It's `DISABLED` on the laptop (see network doc). Any "the firewall blocked you" theory is wrong before it starts.
3. **Don't trust the MAC entries in the network doc as ground truth.** As of this writeup, the doc lists `FC:6D:77:B8:E8:DB` as GWTC's MAC — **that is actually the MSI Katana's MAC** (verified 2026-04-13 by SSH-checking hostname/model on the host at `.3`). GWTC's real MAC is unknown to us right now until someone reads it off the laptop's `ipconfig /all`.
4. **Find GWTC by SERVICE SIGNATURE, not by IP.** Its IP drifts on DHCP. Its hostname is "GWTC" but mDNS doesn't resolve from the Mac Mini. Its **two distinctive services** are:
    - **MediaMTX RTSP on port 8554**
    - **LM Studio on port 9099** (non-standard — explicitly NOT 1234)
   Either one being open on a host is high-confidence evidence that's GWTC. Both being open = certainty.
5. **The diagnostic recipe is below.** Run that, not the lazy `ping 192.168.0.68`.

---

## Diagnostic Recipe (memorize this)

```bash
# Find GWTC by service signature on the /24
echo "-- MediaMTX (RTSP, GWTC's nestbox service) --"
for i in $(seq 2 254); do
  (nc -z -w 1 192.168.0.$i 8554 2>/dev/null && echo "  192.168.0.$i") &
done; wait

echo "-- LM Studio (GWTC runs it on 9099, non-standard) --"
for i in $(seq 2 254); do
  (nc -z -w 1 192.168.0.$i 9099 2>/dev/null && echo "  192.168.0.$i") &
done; wait

echo "-- SSH (sshd is on every machine in the docs, less specific but useful for cross-check) --"
for i in $(seq 2 254); do
  (nc -z -w 1 192.168.0.$i 22 2>/dev/null && echo "  192.168.0.$i") &
done; wait
```

**Interpreting the result:**

- A host with `8554 OPEN` is GWTC. SSH into it via `ssh -o StrictHostKeyChecking=no markb@<that-ip>`.
- A host with `9099 OPEN` is GWTC.
- A host with neither, but `22 OPEN`, **may** be GWTC (sshd-only, services crashed) **or** may be one of the other documented hosts. Cross-check with `ssh markb@<ip> 'powershell -c "hostname; (Get-CimInstance Win32_ComputerSystem).Model"'` — GWTC's hostname is `GWTC`. Other Windows machines (the MSI Katana at `.3` reports `MSI`) are not GWTC.
- **Nothing has 8554 or 9099 anywhere on /24** = GWTC is genuinely off-network. See the next section.

---

## What "GWTC is genuinely off-network" actually means

If the diagnostic recipe finds nothing matching GWTC's signature, the laptop is either:

1. **WiFi disassociated.** The laptop is powered on, MediaMTX is happily serving frames to `127.0.0.1`, but the WiFi NIC is detached from the LAN. From outside the laptop you cannot reach it. From inside (Boss looking at the screen) it looks fine — that's the source of disagreement when Boss says "the laptop is online" and the Mac Mini says it's not.
2. **WSL2 virtual-adapter routing poisoning** (the documented recurring bug). Windows accepts inbound packets but routes responses out a 172.x.x.x WSL2 virtual adapter that doesn't connect back to the LAN. Symptom: SSH and other inbound TCP connects appear to time out from the Mac Mini, but the laptop's local services work fine. Per the network doc, the **only** fix is at the laptop's console: `netsh winsock reset; netsh int ip reset` and reboot.
3. **Joined a different SSID by mistake.** The router exposes both the private SSID (`653 Pudding Hill 2G Private`) and the default open SSIDs (`TP-Link_F110` / `TP-Link_F110_5G`). If the laptop ever joined the wrong one, it'd be on a different subnet and unreachable.

**You cannot tell which of the three from the Mac Mini side.** All three present identically as "no service signature anywhere on `/24`". The only way to distinguish is laptop-side console access: open PowerShell on the laptop and run:

```powershell
hostname
ipconfig /all | findstr /i "IPv4 SSID"
Get-Service -Name mediamtx, farmcam, farm-guardian -ErrorAction SilentlyContinue | Format-Table Name, Status
netsh wlan show interfaces
```

That tells you in one shot: hostname, current IP(s), connected SSID, and whether the GWTC services are running.

---

## What I ruled out today (don't re-tread)

I spent an hour misdiagnosing. The wrong theories I'm pre-burying so the next session doesn't repeat them:

1. ❌ **"My port scans triggered Windows Defender to auto-block the Mac Mini's IP."**
   Wrong because: (a) Windows Firewall is `DISABLED` on the laptop per network doc; (b) the timeline didn't fit — gwtc started failing at 14:57:59, my port scans didn't start until ~15:02. The failure preceded my probes.
2. ❌ **"The MAC `FC:6D:77:B8:E8:DB` is GWTC, so I'll find it by ARP-hunting that MAC."**
   Wrong because that MAC is actually the MSI Katana's. The network doc had the attribution wrong. SSH-confirmed: `markb@192.168.0.3` returns hostname=MSI, model=Katana 15 HX B14WGK.
3. ❌ **"Cloudflare tunnel or Mac Mini network state changed."**
   Wrong: the Mac Mini's networking is fine. I can reach every other LAN device. GWTC specifically is not on the LAN.
4. ❌ **"The laptop must be powered down."**
   Boss confirmed it's powered up and "broadcasting". The disconnect is between *powered up* (true) and *reachable on the LAN from the Mac Mini* (not true).

---

## Authoritative facts from Boss (don't re-confirm by speculation)

These are facts Boss stated; record them so we don't burn time re-asking:

- **The Gateway laptop was wiped before being repurposed as the coop machine.** It does not have Windows Defender or other security suites installed.
- **Nothing runs on it except** MediaMTX (RTSP server, port 8554), `farmcam` (ffmpeg capture from the built-in webcam), and the original `farm-guardian` Python app. Plus LM Studio on port 9099 (per network doc).
- **No human or animal interacts with the machine** beyond Boss occasionally checking it. It does not change state on its own. If something looks "configured differently", it isn't — investigate the network or the WSL2 routing bug instead.
- **The laptop's screen is normally visible.** When Boss says "I just looked at it, it's running", he means he physically saw the screen and it appeared fine. That's reliable evidence of *power* and *Windows running*, but NOT evidence of *LAN reachability*.

---

## Today's chronology, for the record

```
14:43–14:53  gwtc reachable, Guardian streaming RTSP frames OK
14:53        I restarted Guardian for Phase C1 (USB cam snapshot mode)
14:57        I restarted Guardian for Phase C2 (motion-watcher addition)
14:57:28     Guardian back up, gwtc capture thread connects briefly
14:57:59     gwtc capture: first "connection failed". Continuous failures from here.
~15:02       I started network probes against 192.168.0.68 (port scan).
             So my probes were AFTER gwtc had already gone unreachable.
~15:30       Boss confirms laptop is powered up and physically present.
             /24 scan finds 7 alive hosts. None matches GWTC signature.
```

The 5-minute gap between the last successful gwtc connection (14:53:10) and the first failure (14:57:59) is unexplained. Possibilities consistent with the evidence:
- Laptop's WiFi flapped during that window.
- WSL2 routing got poisoned during that window.
- DHCP lease renewed and rebound to a different IP that no longer matches our config.

**No action by the Mac Mini in that window touched the laptop.** The Guardian restarts only opened RTSP connections to the laptop's own MediaMTX, which is normal client behaviour.

---

## What to update in the long-term docs

Two long-term followups (I am not doing them in this session because they're outside Phase A/B/C and Boss is going to physically check the laptop now):

1. **Fix the MAC attribution in `~/bubba-workspace/memory/reference/network.md`.** The line `Gateway Laptop (GWTC) | FC:6D:77:B8:E8:DB | 192.168.0.68` is wrong; that MAC belongs to the MSI Katana. Once Boss reads GWTC's actual MAC off `ipconfig /all`, update both the GWTC row and add a Katana row with the correct MAC.
2. **Update GWTC's documented IP after this incident resolves.** The doc already notes "was .3 before 07-Apr-2026 reboot" — which suggests the IP routinely drifts. Could pin a DHCP reservation in the router, but per the router safety rules, that requires Boss approval and is risky if mistyped. Alternative: update the doc whenever the IP changes, and rely on the service-signature scan above as the ground-truth lookup.

---

## Don't do this again

Future Claude sessions: the diagnostic recipe above should resolve "is GWTC reachable?" in under 30 seconds. If the answer is "no", **stop probing, stop theorizing**, and ask Boss to console-check the laptop with the four PowerShell commands listed above. Speculating about Windows Defender, port-scan auto-blocks, or chickens with admin rights wastes everyone's time.

---

# Addendum — 13-April-2026 evening: Post-Reboot dshow Zombie Pattern

A second GWTC failure mode, distinct from the reachability incident above. **The host is reachable; the camera publisher is wedged.** Don't conflate the two.

> **Update (later that evening):** The watchdog described in the **Automated Recovery** subsection below is now installed on GWTC as the `farmcam-watchdog` Windows service. **In normal operation you should not need to do anything when this fails — the watchdog detects it within ~90s of a wedge and recovers it autonomously.** The manual two-command fix (further down) is the fallback if the watchdog itself is broken. Boss's directive that prompted the build: "Wouldn't some better idea be to have some script on that GWTC that automatically runs when it reboots and does the restart or whatever?" Yes. This is that.

## Symptom (verified 2026-04-13 18:18)

After GWTC reboots, from the Mini you observe **all of**:

- `nc -z -w 2 192.168.0.68 8554` → OPEN. (MediaMTX is up and listening.)
- `ssh markb@192.168.0.68 'sc query mediamtx'` and `'sc query farmcam'` → both `STATE: 4 RUNNING`.
- `ssh markb@192.168.0.68 'tasklist | findstr ffmpeg'` → `ffmpeg.exe` is alive (e.g. PID 2516, ~80MB RAM).
- `ssh markb@192.168.0.68 'tasklist | findstr mediamtx'` → `mediamtx.exe` is alive.
- BUT: any consumer pulling `rtsp://192.168.0.68:8554/nestbox` gets `Server returned 404 Not Found`, and the mediamtx log shows a continuous stream of:
  ```
  INF [RTSP] [conn <ip>:<port>] closed: no stream is available on path 'nestbox'
  ```
  Look at `C:\farm-services\logs\mediamtx.log` — the most recent `is publishing to path 'nestbox'` line will be from **before** the reboot.

If all of those line up: this is the wedge. Don't bother running the upper troubleshooting recipe — that one's for "is the box reachable at all," which is already answered yes.

## Diagnosis

Windows reboot leaves the dshow camera handle in a state where ffmpeg's `dshow` input cannot open the `Hy-HD-Camera` device. The ffmpeg process spawned by Shawl after the reboot is alive but **wedged on the device open** — it never produces frames, never registers as a publisher with mediamtx. Crucially:

- Shawl's `--restart` policy doesn't trigger because ffmpeg never exits non-zero — it just sits there forever.
- The `:loop` retry in `C:\farm-services\start-camera.bat` doesn't trigger either — the `goto loop` only fires when the inner ffmpeg call returns, and a wedged ffmpeg never returns.

So both retry mechanisms are bypassed by the failure mode. The only escape is to forcibly kill the wedged ffmpeg and let Shawl respawn it.

## Fix (verified 2026-04-13 18:21 — restored gwtc to live in ~10s of operator time)

```bash
# 1. From the Mini, find the wedged ffmpeg PID:
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'tasklist | findstr ffmpeg'
#   ffmpeg.exe                    <PID>  Services                   0     <NN> K

# 2. Kill it. Shawl's --restart policy will respawn ffmpeg within ~3s:
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'taskkill /F /PID <PID>'

# 3. Verify a NEW ffmpeg PID appeared (different from step 1):
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'tasklist | findstr ffmpeg'

# 4. Confirm it's actually publishing now:
ssh -o StrictHostKeyChecking=no markb@192.168.0.68 'powershell -Command "Get-Content C:\farm-services\logs\mediamtx.log -Tail 5"'
# Look for: INF [RTSP] [session ...] is publishing to path 'nestbox', 1 track (H264)

# 5. End-to-end check from the Mini:
ffmpeg -hide_banner -loglevel warning -rtsp_transport tcp \
  -i rtsp://192.168.0.68:8554/nestbox -frames:v 1 -y /tmp/gwtc-test.jpg
file /tmp/gwtc-test.jpg   # should report: JPEG image data, ... 1280x720
```

After the fresh ffmpeg starts, **Guardian on the Mini will reconnect on its own** within its 60s capture-retry back-off. No Guardian restart needed.

## Why the obvious fixes don't work

- **`sc stop farmcam && sc start farmcam`** — Stopping the Shawl-wrapped service kills the supervisor, but the orphaned ffmpeg may persist (Shawl's process-tree handling under Windows services is imperfect). Then `sc start farmcam` returns `1056: An instance of the service is already running` because Shawl's still considered alive. The straight `taskkill /F` on the ffmpeg PID is the reliable path because Shawl is already running and watching — it just needs the wedged child to actually die.
- **Restarting MediaMTX** — Doesn't help. MediaMTX is working correctly; it has no publisher to serve from. Restarting the consumer-side service to fix a publisher wedge solves nothing.
- **Restarting Guardian on the Mini** — Doesn't help. Guardian is *trying* to consume; the upstream is broken. Guardian will auto-reconnect within 60s once the upstream is healthy.
- **Rebooting GWTC again** — Doesn't help. Same dshow handle behavior, same wedge.
- **Speculating that another app is "claiming" the camera** — Doesn't help. The Hy-HD-Camera isn't shared with anything else on this host. The wedge is internal to the ffmpeg + dshow + Windows-reboot interaction. Confirmed by: ffmpeg is the only process holding the device.

## The rule

**If GWTC just rebooted and `nestbox` is 404'ing while services report Running: wait ~90s — the watchdog will fix it.** If after 2 minutes it hasn't recovered, *then* kill the ffmpeg PID manually as a fallback. Don't burn time inspecting Shawl logs, restarting services in a dance, or asking Boss to rerun `ipconfig`.

This pattern is also documented in `~/bubba-workspace/memory/reference/network.md` under the GWTC entry and in the Bubba auto-memory at `feedback`-level so future Bubba sessions surface it without needing to read this file.

---

## Automated Recovery — `farmcam-watchdog` (deployed 13-April-2026)

Documentation is a band-aid. Boss called it: "Wouldn't some better idea be to have some script on that GWTC that automatically runs when it reboots and does the restart or whatever?" The answer is yes, so we built one.

**What it is:** A PowerShell watchdog wrapped as a Shawl-managed Windows service called `farmcam-watchdog`. Auto-starts on boot. Probes `rtsp://localhost:8554/nestbox` every 30s using `ffprobe`. If no publisher AND ffmpeg has been alive ≥60s (past startup grace), it kills ffmpeg by PID. Shawl's existing `--restart` policy on `farmcam` then respawns ffmpeg in ~3s with a fresh dshow open.

**Worst-case recovery time after a wedge:** ~90s (30s probe interval + 60s wedge threshold + ~3s respawn). Best case ~30s.

**Why this catches what Shawl misses:** Shawl restarts ffmpeg only when ffmpeg *exits non-zero*. Wedged-ffmpeg never exits — it sits in the dshow open call forever. The watchdog detects the wedge externally (publisher absent from mediamtx) and forces the exit, which Shawl then handles normally. We're not replacing Shawl's supervision; we're giving it a kick when its trigger condition (process exit) doesn't fire.

**Where to find it in this repo:**

- `deploy/gwtc/farm-watchdog.ps1` — the script.
- `deploy/gwtc/install-watchdog.md` — install / update / uninstall recipes, the constraints (no UTF-8 multibyte chars in the script, etc.), and how to test the wedge-recovery path.

**Live state on GWTC as of 2026-04-13 18:32:**

```
sc query farmcam-watchdog
  STATE: 4 RUNNING

C:\farm-services\logs\watchdog.log
  2026-04-13 18:32:30 watchdog started -- pid=10880, probe=30s, wedge_threshold=60s, target=rtsp://localhost:8554/nestbox
```

The probe is verified end-to-end: `ffprobe` against `rtsp://localhost:8554/nestbox` with the live publisher returns exit 0 with `codec_name=h264 width=1280 height=720`. With no publisher, exits non-zero — which the watchdog treats as the trigger condition.

**What the watchdog does NOT do:**

- Does not restart MediaMTX. If MediaMTX is dead, that's a different problem and needs a different fix.
- Does not restart Guardian on the Mini. Guardian auto-reconnects on its own 60s back-off after the upstream comes back.
- Does not reboot GWTC. Same wedge would just reproduce.
- Does not silence the manual recipe up in the **Fix** section above — that recipe is the fallback if `sc query farmcam-watchdog` ever returns anything other than `STATE: 4 RUNNING`, or if the watchdog log shows the watchdog itself has a bug. Keep the recipe in muscle memory for that case.
