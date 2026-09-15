# Birddor killed on the pen roof — 14-Sep-2026, 21:32:04 EDT

**Author:** Claude Opus 5 · **Date:** 15-September-2026

Birddor — Easter Egger cockerel, yellow band #1, hatched 06-Apr-2026 on Boss's desk, the first
bird hatched on the farm in 2026 and the senior ornitharch — was killed overnight on 14-Sep-2026.
He was found at 07:45 the next morning, headless, on the ground in front of the Birdcatraz pen.
He is the first ornitharch to die. He left no known offspring.

## Timeline (all times EDT; `image_archive.ts` is UTC, subtract 4)

| Time | Source | Event |
|---|---|---|
| ~19:30 | Boss | Flock shut in for the night. **Birddor was not among them** |
| 19:10 | archive | `s7-cam` — the camera *inside* Birdcatraz — goes dark. Still dark |
| 19:26 | archive | `macbook-air-facetime` (turkey pen) drops off the network. Still down |
| **19:57:40** | house-yard | Birddor flies up onto the pink tarp roof of the pen and settles |
| ~20:00 | Boss | Coyotes heard howling near the house |
| 19:57 → 21:31 | house-yard | Present in **every** frame, ~125 consecutive frames, both eyes returning IR |
| 21:07:30 | duo2 | Guardian's only detection all night: `person`, conf 0.73, bbox `4,2 → 862,707` — full-frame, the documented spider-web-on-lens artifact, not a person |
| **21:31:38** | house-yard | Last frame with Birddor on the roof. Bright eyeshine, settled |
| **21:32:02.5–03.3** | duo2 video | **A large bird crosses the frame fast, low, from the right** |
| **~21:32:04–06** | duo2 video | **It takes Birddor off the roof. He is too heavy to lift; both birds tumble to the ground** |
| 21:32:07.4 | duo2 video | Movement at the pen and on the ground just below it |
| **21:32:23** | house-yard | Roof empty. Stays empty for the remaining 9 hours of darkness |
| 07:45 | Boss | Body found, headless, in front of the pen |

**Death window: 45 seconds, 21:31:38–21:32:23 EDT.** Boss was awake and heard nothing.

## Conclusion: great horned owl

**The strike is on camera.** Boss found it by watching the duo2 clip through: at 21:32:04 a large
bird comes in fast and low from the right, and by 21:32:06 it has hold of Birddor. It cannot lift
him, and both birds tumble off the roof to the ground. A flicker-normalised motion scan of the
same seconds tracks the incoming bird across the frame from `(2428,1498)` to `(4505,1373)` between
21:32:02.5 and 21:32:03.3, then a second disturbance at `(2753,859)` — at the pen and the ground
directly below it — at 21:32:07.4.

⚠️ **Method note, because it nearly cost the finding:** duo2's night exposure flickers frame to
frame, and a naive frame-difference scan scores that flicker far above the actual bird. Normalise
each frame to zero mean / unit variance *before* differencing. An earlier scan without this
missed the strike entirely and produced a wrong "nothing visible in the video" reading. **Watching
the footage found it; the automated scan only confirmed it afterwards.**

Species is inferred, not seen — the bird is a fast dark shape at this range and no plumage or face
is resolvable. Every line of evidence converges on great horned owl:

- **Elevated, exposed roost.** He was 6+ ft up on the pen roof. A coyote cannot reach it.
- **Silent, and under 45 seconds.** Boss was awake. Owls are silent by design; a raccoon or
  coyote working that pen would have set off every bird inside. None of the locked-in birds were
  touched or disturbed.
- **Head taken, body left, intact, directly below.** A great horned owl cannot lift an adult
  rooster, so it kills, feeds on head and neck, and abandons the carcass. This is the textbook
  signature — **and the video shows exactly that failure to lift**, the two birds coming down
  together rather than the attacker carrying him away.
- **Not coyote.** Coyotes were heard ~90 min earlier, but a coyote carries the whole bird off or
  eats far more of it. Boss's own first instinct, and it is correct.
- **Raccoon is the only real alternative** — it climbs and it also decapitates. It is ruled
  against by *silence* and *tidiness*: raccoons are loud and leave scattered feathers and an
  opened body. Neither was present.

Boss saw Birddor on the roof that evening and tried to get him down. He would not come. The
exposure is what made him reachable.

## How the evidence was recovered

Detection and alerting contributed **nothing** — no alerts, no tracks, and zero detections after
21:07:30, including zero at dawn on 15-Sep (every other day this month ran 3–415). The timeline
came entirely from the 4K snapshot archive: a background-subtraction scan of the pen roofline
across the night, then frame-by-frame inspection of the 45 s cadence around the transition.
Scripts are throwaway; the method is: normalise each frame for brightness/contrast (so the IR
cut-filter switch does not swamp the signal), median-stack the night as a background, and rank
frames by the peak residual inside a tight ROI on the roost spot.

## What this exposed — open items

1. **🔴 `house-yard` has no SD card.** `GetHddInfo` returns `[]` — the camera reports no storage
   device at all, while `GetRecV20` shows recording enabled with a full 24/7 schedule and nothing
   to write to. This is the one camera with a usable view of the pen roof, and it gave us
   **45-second stills** of the most important event on the farm this year. **Put a card in it.**
2. **`duo2` records fine and could not help.** 128 GB card, mounted, continuous 24/7, 489 files
   for 14-Sep. But the pen subtends ~85×60 px in its frame and is barely lit by IR at that
   range. Its footage for 21:29:44–21:33:44 was cut and handed to Boss; it is the record of the
   window, not an identification.
   ⚠️ `reolink_aio.request_vod_files()` returns **0 files** for a range spanning midnight, which
   reads exactly like "the camera isn't recording." Issue the raw `Search` command **one calendar
   day at a time** via `Host.send`, with `param.Search = {channel, onlyStatus: 0, streamType:
   "main"|"sub", StartTime: {...00:00:00}, EndTime: {...23:59:59}}`, and read
   `value.SearchResult.File`. Download a file with
   `cmd=Download&source=<File.name>&output=<name>&token=<token>`.
3. **Two Birdcatraz cameras died before the kill and are still dead** — `s7-cam` 19:10, the
   MacBook Air 19:26, sixteen minutes apart. **This is not a breaker trip**: `farm-pi5`, on the
   same outdoor circuit, ran clean all night. The Air is off the network entirely (no ARP, no
   SSH, mDNS name gone), not merely lid-shut. Boss is handling both physically.
   Separately, the Air's config entry resolves `Marks-MacBook-Air.local`, which does not resolve
   even when the host is up — the router knows it as `Marks-Air` with a DHCP reservation at
   `192.168.0.50`. Worth switching both config files to the IP.
4. **Nothing watches a bird roosting outside the pen.** The whole event was visible to
   `house-yard` for 94 minutes and nothing looked at it. A bird on that roofline after dark is
   both detectable and actionable — it is the one warning that would have mattered here.

## Records updated

- `farm-2026/content/flock-profiles.json` — Birddor `status: deceased`, `deceased_date`,
  `cause_of_death`, note added. Matches the convention used for Little Big Red Junior.
- `farm-2026/content/hatches/2026/2026-04-06-01-birdadette.md` — `lost_date` / `lost_cause` /
  `lifecycle_summary` filled, and a `## Death` section written.
- `farm-guardian/config/flock_bands.json` — yellow #1 carries `deceased_date`, retiring the band.
- `farm-guardian/tools/pipeline/roster.py` — `_local_bands()` now honours `deceased_date`. Without
  this the **offline fallback** would keep resolving yellow #1 to Birddor, which is precisely the
  failure `get_confirmed_bands()` documents and refuses to make. Verified: he is excluded from
  `get_active_ornitharchs()`, `get_confirmed_bands()` and `_local_bands()`, so the VLM prompt will
  not list him as a bird to look for and cannot confabulate sightings of him into captions or reels.
