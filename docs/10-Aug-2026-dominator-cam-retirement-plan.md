# 10-Aug-2026 — Retire `dominator-cam`

## Why

Boss: we're not using `dominator-cam` anymore, take it out of the project.

`dominator-cam` is the built-in BisonCam NB Pro webcam on Larry's MSI Dominator GT72
(`192.168.0.194:8089`), started by the `dominator-cam-bisoncam` scheduled task
(`deploy/dominator-cam/README.md`). It's been opportunistic since 2026-05-06 and already shown
as "offline by design" in CLAUDE.md (Boss deliberately turned it off 05-Aug-2026) — this makes
that permanent rather than a standing "it's off for now" note.

## Scope

**In:**
- Remove `dominator-cam` from both Guardian config files.
- Stop it from auto-starting on the Dominator at next login.
- Update CLAUDE.md and HARDWARE_INVENTORY.md so the camera roster reflects six cameras, not
  seven.
- CHANGELOG entry.

**Out:**
- The Dominator's companion `usb-cam` role (`dominator-cam-usbcam` task, port 8090) — that
  camera already moved to the Birdcatraz Pi on 05-Aug-2026 as `usb-webcam-1080p` and isn't part
  of this request. Not touching it.
- Deleting the scheduled task or the `deploy/dominator-cam/` artifacts outright. Disabling the
  task is enough to satisfy "not using it anymore" and keeps the door open without any risk to
  Larry/Boss's own machine; deleting things on hardware I don't own by default isn't the right
  default action level here.
- Any change to the reel lane plist — it's already `.plist.disabled` on disk, unloaded, and
  needs no further action.

## Architecture / reuse

No code changes. `scripts/add-camera.py remove` is the existing, repo-mandated tool for atomic
removal across `config.json` + `tools/pipeline/config.json` — reuse it rather than hand-editing
either file (CLAUDE.md is explicit that hand-edits are how the two files drift).

## TODOs

1. Confirm current live state before touching anything: probe `192.168.0.194:8089/photo.jpg`
   and check the scheduled task's status over SSH.
2. `scripts/add-camera.py remove dominator-cam` — removes it from both config files atomically.
3. SSH to the Dominator (`user@192.168.0.194`, plain cmd.exe, no WSL hop needed for this) and
   disable `dominator-cam-bisoncam` so it doesn't fire at the next login:
   `schtasks /change /tn dominator-cam-bisoncam /disable`.
4. Reload Guardian + pipeline so both stop expecting the camera:
   `launchctl kickstart -k gui/$(id -u)/com.farmguardian.guardian` and
   `...com.farmguardian.pipeline`.
5. Update CLAUDE.md: drop the roster row, replace the "Camera 6" prose section with a short
   retirement note, seven → six cameras in the summary line.
6. Update HARDWARE_INVENTORY.md: mark the `dominator-cam` row retired (light-touch — this file
   is stale well beyond this one camera; not doing a full resync here, out of scope).
7. Note the retirement at the top of `deploy/dominator-cam/README.md` so nobody reads it as a
   live runbook.
8. CHANGELOG top entry.

## Verification

- `scripts/add-camera.py list` no longer shows `dominator-cam` in either config.
- Guardian dashboard / `/api/v1/cameras` no longer lists it after the service reload.
- `schtasks /query /tn dominator-cam-bisoncam` on the Dominator shows `Scheduled Task State:
  Disabled`.

## Results

Executed same session, 10-Aug-2026. See CHANGELOG top entry for the summary.
