# Instagram outage and time-lapse recovery — 29-Oct-2026

Author: Claude Opus 5.5 Medium — 29-Oct-2026
Status: fixed and running; the catch-up of old videos runs over the coming weeks.

## In plain words

Two separate things broke, and neither one told anybody.

1. **9 August — the daily time-lapse videos stopped posting.** That day the Duo2, Guardian yard
   camera (house-yard) and dashcam videos were upgraded to use hundreds of photos each for smoother
   motion (v2.69.0). The step that writes the caption looked each photo up in the database and never
   closed the connection. Jobs that macOS starts on a schedule may only hold 256 things open at once,
   so the job crashed with "unable to open database file" right before posting, every day. Run by hand
   from the Terminal it worked, because a Terminal gets a far higher limit, so testing never showed it.
   The video file was already built each time, so about 230 finished videos sat on disk unposted.
2. **8 October — Facebook cancelled the farm's login key.** The same day the VoynichLabs Facebook Page
   was created and its Instagram link attempted under the Boss's login. Every posting job (S7 daily,
   carousel, everything) then got "session has been invalidated" and posted nothing. Bubba re-approved
   the key on 28-Oct evening.

Both failures were written only to log files in `/tmp` that nobody reads. Nothing reached Discord.
The real lesson is that the posting system had no alarm, so that is the first thing fixed.

## Timeline

| When | What |
|---|---|
| 09-Aug | v2.69.0 dense time-lapse path. duo2, house-yard-cam, jieli-dashcam lanes stop posting (last posted marker 2026-08-09). |
| 08-Oct | Last farm post of any kind. Farm Meta key invalidated (error 190 / subcode 460). VoynichLabs Page created the same day. Four VoynichLabs music videos were posted to the farm account that day. |
| 08-Oct → 28-Oct | Every lane fails; carousels and S7 reels built and dropped. |
| 28-Oct 21:20 | Bubba re-authorizes the Yorkies app; farm key works again. |
| 29-Oct 12:31 | First farm carousel in three weeks posts. |
| 29-Oct evening | Root cause of the 9-Aug break found and fixed; duo2, house-yard and S7 daily reels post; alarm added; catch-up job added; daily window changed to the full 24 hours. |

## What changed (farm-guardian v2.79.0)

- **Leak fixed.** `tools/pipeline/daily_reel_runner.py` `_fetch_gem_row` closes its connection
  (`contextlib.closing`; sqlite's own `with` only commits). `main()` also lifts the open-file soft cap
  to 8192. Proven: the old code fails at row 186 under `ulimit -n 256`; the new code runs 700.
- **A finished post can no longer be marked failed.** `ig_poster._sweep_expired_assets` (housekeeping
  that runs after publishing) could raise and make the lane think the post failed; a manual retry then
  posted the 29-Oct duo2 reel twice. The duplicate was deleted; the sweep can no longer raise.
- **Alarm.** `scripts/pipeline-digest.py` (Discord, noon and 8 pm) now checks the farm key, the
  VoynichLabs key, the age of the newest farm post, and the age of each daily time-lapse lane's last
  real post. Any problem leads the message with a mention of Mark. The 9-Aug break would have been
  flagged within two days.
- **Whole day, quiet parts fast.** Boss: "the birds are up and awake at 4 in the morning, I really do
  want to see all 24 hours. I just don't want you to spend a lot of time on stuff when there's no
  action." The duo2, house-yard and dashcam daily reels now cover midnight to midnight. Each minute's
  frame is compared with the previous one; the day's quietest 40% of minutes keep only 1 frame in 6, so
  still stretches (asleep, empty yard) race by and anything moving plays at normal speed, day or night.
  This is measured from the pictures, not the clock. About 50 seconds per reel. (Until now the window
  had silently been sunrise to sunset, about 7:15 am to 6 pm this time of year.)
- **Catch-up.** `scripts/ig-timelapse-backlog.py`, LaunchAgent `com.farmguardian.ig-timelapse-backlog`
  at 11:40 and 17:40. Posts the unposted finished videos newest first, one per camera per run, only while
  6 or more Instagram quota slots stay free. Captions name the day shown. At 29-Oct: S7 19 days, duo2 75,
  house-yard 76, dashcam 61. State: `data/reels/backlog-posted.json`.
- **S7 weekly gems reel** assembly timeout now scales with photo count (the 25-Oct one timed out).
- **VoynichLabs** posts through its own route (see `08-Oct-2026-second-instagram-account-plan.md`),
  one music video a week, Thursdays 19:00.

## How to check it is working

- Discord `#farm-2026` noon and 8 pm digests: no 🚨 lines means every key works and every daily
  time-lapse posted in the last 36 hours.
- `ls -t data/reels/<lane>/posted | head` for duo2-timelapse, house-yard-cam-timelapse,
  jieli-dashcam-timelapse, s7: a new marker every day.
- `/tmp/ig-timelapse-backlog.err.log`: one "posted" line per camera per run until each says "caught up".

## Gotchas for the next person

- Posted markers are keyed by the **UTC date the lane ran** (S7 keys by the day covered). An evening
  manual re-run after 20:00 EDT writes tomorrow's key and silently skips tomorrow's run. Re-date the
  marker after a manual evening run.
- Never test a scheduled lane only from the Terminal. Use `launchctl kickstart gui/$(id -u)/<label>`
  so it runs under launchd's real limits.
- If a manual retry follows a "failed" publish, first check Instagram for the post. The failure may
  have come after the publish.
