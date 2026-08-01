# 01-Aug-2026 — Rename cameras to what they actually are, and add the dashcam

## Why

Three cameras are now plugged into the MacBook Air. Two of them are called things
that don't say what they are (`mba-cam`, `usb-cam`), and the third — a Jieli
dashcam, the best-looking picture of the three — isn't in the system at all.

There's also a real bug underneath. The camera service picks cameras by their
*position in a list*, and macOS reshuffles that list every time something is
plugged or unplugged. Today the position moved twice in one afternoon. Until now
we could double-check a camera by its resolution, but the dashcam and the Air's
built-in camera are both 1280x720, so that check no longer works. Left alone, the
next restart publishes the wrong camera's pictures under the wrong name — exactly
what happened over 21–23 July.

## New names

| Old | New | What it actually is |
|---|---|---|
| `mba-cam` | `macbook-air-facetime` | MacBook Air's built-in FaceTime HD camera, 1280x720 |
| `usb-cam` | `usb-webcam-1080p` | Generic USB webcam, 1920x1080, no brand or model on the device |
| — (new) | `jieli-dashcam` | Car dashcam in PC-camera mode, Jieli Technology, 1280x720, wide-angle yard view |

## Scope

**In:**
- Stop the camera service trusting list position; make it confirm which camera it opened.
- Run three copies of the service on the Air, one per camera, on ports 8089 / 8090 / 8091.
- Rename the two old cameras everywhere: both config files, code, deploy files, docs.
- Add the dashcam as a real camera in both config files.
- Rename the old names in the existing photo archive so the reels don't go blank for a week.
- Turn off the dead GWTC camera service, which currently points at a laptop with no camera.

**Out:**
- Not touching the 21–23 July mislabelled photos. They're documented as wrong in three
  places; rewriting them would hide the mistake. They stay flagged.
- Not touching `nesting-box` or `iphone-cam` archive entries — old names, no config, leave alone.
- Not building any new reel for the dashcam yet. Get it capturing first.
- Not renaming any other camera.

## Steps

1. **Fix the camera service** so it checks what it opened instead of trusting a number.
   Look the camera up by name, open it, then confirm the picture size matches what that
   camera claims it can do. If two cameras are the same size, keep trying the other
   positions until only one fits. Each copy of the service stops as soon as it finds its own
   camera, so three copies don't fight over the hardware.
2. **Deploy the fixed service to the MacBook Air** and set up three startup files, one per
   camera, each naming its camera. Stagger their start times so they don't collide.
3. **Check each one by eye** — pull a picture from all three ports and confirm the yard is
   the yard, the run is the run, and the fence is the fence. Resolution can't tell them apart
   any more, so looking is the only real check.
4. **Update both config files** — Guardian's and the pipeline's — with the new names, the new
   addresses, and the dashcam. Both files, every time; they're the classic thing to half-do.
5. **Rename the old names in the photo archive**, after backing it up, so existing photos
   stay attached to their camera and the reels keep working.
6. **Turn off the dead GWTC camera service** and drop its entry, since that laptop's USB
   ports aren't working.
7. **Update the docs** — hardware inventory, the camera table in the agent instructions,
   and the changelog.
8. **Restart both services and verify** — cameras online, pictures correct, nothing silently
   pointing at the wrong thing.

## Docs to update

- `HARDWARE_INVENTORY.md` — camera roster, new names, the dashcam, the list-position trap.
- `CLAUDE.md` — camera roster table and the naming warning.
- `CHANGELOG.md` — top entry, what changed and why.
