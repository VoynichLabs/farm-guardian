# iPhone Opportunistic Camera — Plan

**Date:** 19-Apr-2026
**Author:** Claude Opus 4.7 (1M context) — "Bubba"
**Goal:** When Boss plugs an iPhone into the Mac Mini, surface it to Farm Guardian as a live camera (`iphone-cam`) so he can take good pictures of the birds through the normal Guardian dashboard + API. When no iPhone is plugged in, the service idles cleanly — no screen-capture fallthrough, no churn.

## Scope

**In:**
- One minimal code change to `tools/usb-cam-host/usb_cam_host.py`: a new `USB_CAM_DEVICE_NAME_CONTAINS` env var that resolves the AVFoundation device by substring-matching its name before `cv2.VideoCapture(index)`. No match → `_open()` returns `None` and the existing backoff loop retries.
- A second `usb-cam-host` LaunchAgent (`com.farmguardian.iphone-cam-host`) on port `8091`, name-gated on `"iPhone"`, with neutral image processing (no heat-lamp WB/desat/sharpen — iPhone already color-corrects).
- New `iphone-cam` entry in both Guardian's `config.json` and `tools/pipeline/config.json`, pointing at `http://127.0.0.1:8091/photo.jpg`. Detection **off**.
- Docs: `HARDWARE_INVENTORY.md` row, `CHANGELOG.md` entry, this plan.

**Out:**
- Detection on iPhone frames.
- Auto-start on USB hotplug (LaunchAgent stays `RunAtLoad + KeepAlive`; service sits there with the grabber retrying every 3s — cheap, and first frame is live within ~3s of plugging in).
- Any PyObjC / AVFoundation framework dependency. Name resolution uses `ffmpeg -f avfoundation -list_devices true -i ""` stderr parsing. Zero new Python deps.
- Wireless Continuity Camera (works the same way — the device shows up in the AVFoundation list whether USB or wireless — but we don't advertise the wireless path since Boss said "plugged in").

## Architecture

### Why not raw `USB_CAM_DEVICE_INDEX`?

Current AVFoundation device list with iPhone plugged in:
```
[0] Mark's evil iPhone 16 Pro Max Camera
[1] Mark's evil iPhone 16 Pro Max Desk View Camera
[2] Capture screen 0
[3] Capture screen 1
```
When the iPhone unplugs, `[0]` becomes `Capture screen 0`. A raw-index gate would silently publish the Mac Mini's screen into Guardian's archive. The name gate makes that impossible.

### Name resolution

A new helper in `usb_cam_host.py`:
```python
def _resolve_device_index_by_name(needle: str) -> Optional[int]:
    """Run ffmpeg's AVFoundation device listing, parse stderr, return the
    first video-device index whose name contains `needle` (case-insensitive).
    Excludes screen captures defensively. Returns None if no match."""
```
- Darwin-only. On other platforms, if `USB_CAM_DEVICE_NAME_CONTAINS` is set we log a warning and fall back to the existing index behavior.
- Invoked inside `_open()` before `cv2.VideoCapture(...)`. If the name is set and doesn't resolve, `_open()` returns `None` — the grabber's existing `RECONNECT_BACKOFF_S` wait handles "iPhone absent" as a normal transient state, same as "camera unplugged."
- `USB_CAM_DEVICE_INDEX` remains the fallback when `USB_CAM_DEVICE_NAME_CONTAINS` is unset, so the existing Logitech-on-MBA deployment is completely unaffected.

### LaunchAgent

New plist at `~/Library/LaunchAgents/com.farmguardian.iphone-cam-host.plist`. Fresh label → fresh TCC Camera grant on first launch (expected — will surface one prompt the first time the iPhone is actually connected and the grabber opens the device). Same shape as the existing `com.farmguardian.usb-cam-host.plist`, differing only in:
- `Label` → `com.farmguardian.iphone-cam-host`
- `USB_CAM_PORT` → `8091`
- `USB_CAM_DEVICE_NAME_CONTAINS` → `iPhone`
- `USB_CAM_WIDTH`/`HEIGHT` → `3840` × `2160` (iPhone sensor is 4032×3024 native; 4K UHD is closer to widescreen framing and works cleanly through Guardian's existing snapshot path)
- `USB_CAM_AUTO_WB=false`, `USB_CAM_ORANGE_DESAT=1.0`, `USB_CAM_SHARPEN_AMOUNT=0.0`, `USB_CAM_HIGHLIGHT_STRENGTH=0.0` — iPhone output is already finished; all brooder-tuned knobs off
- Log path → `/tmp/iphone-cam-host.out.log` / `.err.log`

### Guardian integration

Guardian's `config.json` gets:
```json
{
  "name": "iphone-cam",
  "type": "fixed",
  "source": "snapshot",
  "snapshot_method": "http_url",
  "http_base_url": "http://127.0.0.1:8091",
  "http_photo_path": "/photo.jpg",
  "http_trigger_focus": false,
  "snapshot_interval": 10.0,
  "detection_enabled": false
}
```
10s cadence — lighter than brooder (5s) since the iPhone is opportunistic and we don't need fast cadence for bird portraits. Zero impact when the service is serving 503 — `HttpUrlSnapshotSource` already tolerates that.

`tools/pipeline/config.json` gets a matching block with `enabled: true`, `cycle_seconds: 60`, `capture_method: ip_webcam`, `ip_webcam_base: http://127.0.0.1:8091`. The pipeline's own retry + backoff handles "iPhone absent" as a normal transient.

## TODOs (ordered)

1. Edit `tools/usb-cam-host/usb_cam_host.py`:
   - Add `DEVICE_NAME_CONTAINS` env var.
   - Add `_resolve_device_index_by_name()` helper (ffmpeg stderr parse, darwin-only).
   - Use it in `_open()` before `cv2.VideoCapture(...)`.
   - Update the file header (date + v2.28.x note).
   - Log the resolved device's name + index on every successful open, so operators can see which hardware the service latched onto.
2. Create `~/Library/LaunchAgents/com.farmguardian.iphone-cam-host.plist`.
3. Add `iphone-cam` to `config.json` (Guardian) and `tools/pipeline/config.json` (pipeline). Run the CLAUDE.md grep sanity check.
4. Update `HARDWARE_INVENTORY.md` row + "What Runs Where" table.
5. Update `CHANGELOG.md` top entry with a v2.28.x bump.
6. Load the LaunchAgent: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.farmguardian.iphone-cam-host.plist` (plus enable/kickstart as the existing plists do).
7. Reload Guardian + pipeline: `launchctl kickstart -k gui/$(id -u)/com.farmguardian.guardian && launchctl kickstart -k gui/$(id -u)/com.farmguardian.pipeline`.
8. Verify: `curl -sI http://127.0.0.1:8091/health` (expect 200 when iPhone is connected, 503 when absent); `curl -s http://localhost:6530/api/cameras | jq '.[] | select(.name=="iphone-cam")'`.
9. Commit + push — verbose message, changelog first entry, no credentials.

## Docs / changelog touchpoints

- `CHANGELOG.md` — top entry: v2.28.x "`iphone-cam` opportunistic camera; name-gated `usb-cam-host`."
- `HARDWARE_INVENTORY.md` — new row under "The Four Cameras" (now five when iPhone present), plus a note in "What Runs Where" under the Mac Mini.
- `docs/14-Apr-2026-portable-usb-cam-host-plan.md` — unchanged; the name-gate extension is backwards-compatible and the portable pattern still holds.

## Risks + how they're handled

- **Fresh-label TCC prompt**: Expected, one-time, on first grabber open after iPhone is first connected. Boss will need to click "Allow" on the dialog that pops up on whichever display the Mini is driving (or headlessly via screen-sharing). Not a blocker — just a first-run step.
- **iPhone as device `[0]` when plugged in, but user might have other Continuity devices in the future**: Name-gate matches first device whose name contains `iPhone` (case-insensitive). If Boss later plugs in an iPad too, this will pick whichever AVFoundation lists first. Fine for now; we can tighten to `iPhone 16 Pro Max` or a model-specific substring if it ever becomes ambiguous.
- **iPhone battery drain**: Grabber at 2 Hz holds the camera open for the life of the service while iPhone is connected. That's the same load Continuity Camera places on the phone in a Zoom/FaceTime call — negligible for a plugged-in device.
- **Two usb-cam-host processes on one box**: The existing Mini-side `com.farmguardian.usb-cam-host.plist` is present on disk but currently unloaded (`launchctl list` shows no entry). Even if it were running, ports don't collide (8089 vs 8091) and each process opens its own `cv2.VideoCapture` against a different device. No shared state.
