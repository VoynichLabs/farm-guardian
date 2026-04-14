# S7 Phone Setup — HTTP Snapshot Mode

**Author:** Claude Opus 4.6 (1M context)
**Date:** 13-April-2026 (written); executed 14-April-2026; this doc updated to reflect what actually happened.
**Supersedes (operationally):** the RTSP-streaming parts of `docs/06-Apr-2026-s7-nesting-box-camera-setup.md`. That doc is still the authoritative reference for mounting and WiFi config.
**Status:** **LIVE** — `s7-cam` is running in `http_url` snapshot mode since 2026-04-14 11:01 EDT. Guardian log confirms `Camera 's7-cam' registered in snapshot mode (method=http_url)` and `/api/cameras/s7-cam/frame` serves 1920×1080 JPEGs from the phone. This doc is now a recovery / re-deploy playbook, not a pending-action list.

## Important discovery made during execution (2026-04-14)

Prior docs claimed the S7 had been running "IP Webcam" all along. **It wasn't.** The phone was actually running **RTSP Camera Server (`com.miv.rtspcamera`)**, a visually similar but functionally different Android app. RTSP Camera Server is RTSP-only (no HTTP `/photo.jpg` endpoint at all — its `:8080` is a dumb playlist-server that returns the same `.m3u` file for every path) and, crucially, it auto-records the RTSP stream to internal storage in 1-hour chunks. By the time the S7 was re-powered, `/sdcard/RTSPRecords` had **19 GB** of looped chicken-coop recordings — that, not streaming to the Mac Mini, was the primary battery and storage drain. Streaming over RTSP to Guardian was secondary. Every Guardian doc that said "S7 running IP Webcam" was wrong — the real IP Webcam (`com.pas.webcam`) was installed for the first time as part of the v2.24.0 cutover. Lesson for the next agent: verify `adb shell pm list packages | grep -iE 'webcam|rtsp'` before acting on the assumption that a specific app is installed.

## Why this changed (original motivation)

RTSP streaming from the S7 was killing its battery faster than USB could charge it. We switched to pulling HTTP snapshots on a timer. See `docs/13-Apr-2026-s7-battery-http-snapshot-plan.md` for the architectural rationale. This doc is the checklist that describes how the cutover actually ran.

## Prereqs (for a re-deploy / recovery)

- Samsung Galaxy S7, charged enough to boot, USB charger plugged in.
- IP Webcam (`com.pas.webcam`) by Pavel Khlebovich. **Install it fresh; don't trust that it's already there** — on 14-Apr the phone had RTSP Camera Server instead.
- Mac Mini reachable on the same WiFi at `192.168.0.105`.
- If you need to install from a Mac: adb (`brew install --cask android-platform-tools`, or pull the portable tarball via `curl -L -o pt.zip https://dl.google.com/android/repository/platform-tools-latest-darwin.zip && unzip pt.zip`), USB cable with **data lines** (charge-only cables will enumerate the phone on MTP without exposing ADB — the 14-Apr incident had a cable swap mid-job that dropped the device), and USB debugging enabled on the phone with the Mac's RSA fingerprint authorized.

## Step 1 — Boot the phone, let it connect to WiFi

The phone has a static IP reservation as `192.168.0.249`. Once WiFi connects, check `192.168.0.249` responds on something (easiest: open `http://192.168.0.249:8080` in a browser on any device on the network once IP Webcam is running — it'll serve its own web UI).

If WiFi fails: static IP config is at **Settings → WiFi → long-press network → Modify → Advanced**. Static IP `192.168.0.249`, gateway `192.168.0.1`, DNS `8.8.8.8`. See the 06-Apr doc for the full walkthrough.

## Step 2 — Open IP Webcam and configure for HTTP-only photo serving

The goal is: **no RTSP stream, HTTP photo endpoint on, preview runs but nothing encodes video continuously.**

In IP Webcam settings (tap the app, then scroll down before pressing the "Start server" button at the bottom):

1. **Video preferences → Video resolution**: `1280x720` is fine. Higher is welcome if the phone can handle it — `1920x1080` works on the S7 for still photos even when full-rate encoding would fail.
2. **Video preferences → Photo resolution**: pick the highest offered (often 4160x3120 on the S7). This is what `/photo.jpg` serves. Higher here is free — we only pull one frame every 5–10 s, not 30 fps.
3. **Video preferences → Quality**: 70–80%. Higher than the RTSP setting — we're paying the encode cost once per snapshot, not continuously.
4. **Connections → Login/password**: leave empty for now, or set `admin` / a known password. If set, record both in the server-side config. Local network only; not a secrets-management emergency.
5. **Connections → Port**: default `8080`. Do not change.
6. **Service control → Run in background**: ON.
7. **Service control → Stop on low battery / Stop on power disconnect**: OFF (we want it to keep trying; USB will be plugged in).
8. **Power management → Prevent sleep while streaming**: ON.
9. **Power management → Dim screen**: ON. Set the minimum brightness slider as low as it goes. Saves a surprising amount of power and doesn't disturb the chickens.
10. **Audio → Audio mode**: OFF / None. We don't use it; why pay the CPU cost.
11. **Video streaming → Recording**: verify nothing is writing to local storage. The app should not be recording to phone disk. (This is what fills the 32 GB.)

Then press **Start server** at the bottom.

## Step 3 — Verify the HTTP snapshot endpoint

On any device on the same WiFi (laptop browser is fine):

1. Open `http://192.168.0.249:8080/` — IP Webcam's own dashboard should load.
2. Open `http://192.168.0.249:8080/photo.jpg` — should return a single JPEG of the current view.
3. Open `http://192.168.0.249:8080/focus` — should trigger the phone's autofocus (listen for the lens / watch the preview refocus).

From the Mac Mini terminal:

```bash
curl -sS -o /tmp/s7-test.jpg http://192.168.0.249:8080/photo.jpg && \
  file /tmp/s7-test.jpg && \
  ls -l /tmp/s7-test.jpg
```

Expected: `JPEG image data`, non-trivial file size (hundreds of KB at 1080p, low MBs at 4K).

## Step 4 — Flip the Guardian config

Edit `~/Documents/GitHub/farm-guardian/config.json`. Replace the existing `s7-cam` block with:

```json
{
  "name": "s7-cam",
  "ip": "192.168.0.249",
  "port": 8080,
  "username": "",
  "password": "",
  "type": "fixed",
  "source": "snapshot",
  "snapshot_method": "http_url",
  "http_base_url": "http://192.168.0.249:8080",
  "http_photo_path": "/photo.jpg",
  "http_trigger_focus": false,
  "snapshot_interval": 5.0,
  "detection_enabled": false
}
```

If you set a login/password in step 2.4, fill `username` and `password` accordingly — the server uses HTTP basic auth in that case.

Restart Guardian:

```bash
cd ~/Documents/GitHub/farm-guardian
# If running via your usual launcher, use that. Otherwise:
pkill -f "python guardian.py"; sleep 2
source venv/bin/activate && nohup python guardian.py > guardian.log 2>&1 &
```

Tail the log for the first couple minutes:

```bash
tail -f guardian.log | grep -E "s7-cam|http_url|snapshot"
```

You should see:

- `Camera 's7-cam' online (http_url snapshot) — http://192.168.0.249:8080`
- `Snapshot polling started for 's7-cam' — source=http:s7-cam, interval=5.0s`
- No `snapshot returned None (consecutive=…)` warnings. If you see those, go back to step 3 and verify `/photo.jpg` from a browser on the Mac Mini specifically (not just your laptop).

Dashboard at `http://localhost:6530` should show the S7 feed live, updating every 5 seconds.

## Step 5 — Rough battery validation

Leave it running for an hour. Check the phone — with USB plugged in, it should be holding steady charge or slowly climbing, *not* slowly draining. If it's still draining with USB in, the HTTP path isn't the full solution and the phone hardware itself (battery, USB port) may be the bottleneck. Capture phone temperature by hand — it should be warm at worst, not hot.

## Step 6 — Update `HARDWARE_INVENTORY.md`

Once the switch is working, change the `s7-cam` row in the hardware inventory at the repo root:

- Column "RTSP / source URL": change `rtsp://192.168.0.249:5554/camera` to `http://192.168.0.249:8080/photo.jpg`
- Column "Capture method": change `RTSP via OpenCV` to `http_url snapshot poll`
- Column "Currently aimed at": update if the phone got repositioned during the fix.

## Rollback

If this produces worse results than RTSP (e.g. image quality unacceptable, or IP Webcam HTTP server keeps crashing on this phone and RTSP was stabler):

1. In IP Webcam, keep video streaming on.
2. Revert the `s7-cam` config block in `config.json` to the pre-v2.24.0 state:

```json
{
  "name": "s7-cam",
  "ip": "192.168.0.249",
  "port": 5554,
  "username": "",
  "password": "",
  "type": "fixed",
  "rtsp_transport": "udp",
  "rtsp_url_override": "rtsp://192.168.0.249:5554/camera",
  "detection_enabled": false
}
```

3. Restart Guardian.

The `HttpUrlSnapshotSource` code stays in place either way — it's generic and will be reused by GWTC Phase B (`docs/13-Apr-2026-phase-b-gwtc-snapshot-endpoint-plan.md`).
