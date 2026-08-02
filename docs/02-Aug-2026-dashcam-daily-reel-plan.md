# 02-Aug-2026 — Daily Instagram reel from the dashcam

## Why

The dashcam has the best picture on the farm's MacBook Air and already captures all
day. Every other stationary camera gets its own daily time-lapse reel; this one
doesn't. Adding it is cheap — the lane machinery already exists and six cameras use it.

## What I checked before saying yes

- **Instagram's posting limit is tighter than it looks — corrected.** Instagram caps the
  account at 25 posts per rolling 24 hours across everything. My first estimate of "2–6 a
  day, plenty of room" was wrong: it came from a table that only records reels. The real
  counter says **22 of 25 used**, and **19 of those 22 are reacted-gem stories** — the
  hourly lane driven by Discord reactions. Recent days: 7, 22, 15.
- **So the new lane runs LAST, at 21:30**, after S7 at 21:00. On a heavy-reaction day
  something has to be skipped, and it should be the brand-new lane rather than an
  established one. The lane already skips cleanly when no slots remain — it logs and
  posts nothing. The 24h look-back still covers the whole day.
- **No time slot collision.** Existing: 09:00 house-yard, 12:30 carousel, 15:00 duo2,
  18:00 mixed, 21:00 S7, 23:30 stats.
- **The work is small.** Each camera's reel is a ~30-line file plus a settings block
  plus a scheduler entry. Nothing new gets invented.

## The one real risk, and why I'm shipping anyway

**The dashcam gets re-aimed often.** A time-lapse works because the frame holds still
and the world moves. If the camera is repointed halfway through a day, the reel becomes
a jump-cut between two unrelated scenes and looks broken rather than intentional.

I'm shipping the simple version regardless, because:

- We don't yet know how often it actually gets moved during daylight — it's been in
  service about a day. Building for a problem we haven't measured is guessing.
- A bad reel is visible and harmless. It gets spotted immediately and costs nothing.
  That's a far cheaper way to learn than writing scene-change detection blind.
- If it does become a nuisance, the fix is known and written down below.

**The fix if it becomes a nuisance:** build the reel from the longest stretch of the
day where the camera didn't move, instead of the whole day. Detecting "the camera moved"
is a comparison between consecutive frames — the same trick already used to tell the
cameras apart.

## Scope

**In:**
- A daily dashcam reel at 20:15, auto-posting like the others, with a Discord notice.
- Landscape output — the dashcam shoots 16:9, same as the duo2 and house-yard lanes.
- Fast pacing, matching the other outdoor time-lapse lanes.

**Out:**
- **No scene-change detection.** Deferred deliberately, see above.
- **No sunrise/sunset "golden window" selection.** That narrows the reel to specific
  light on the assumption of a fixed view — wrong for a camera that moves. It stays on
  the plain daylight-only path.
- **No description of what the camera points at**, anywhere. That was removed yesterday
  and is not coming back.

## Steps

1. Add the dashcam frame selector alongside the other six.
2. Add its lane settings — landscape, fast pacing, auto-post, Discord notice.
3. Add the small launcher script, matching the existing ones.
4. Add the scheduler entry for 20:15 and load it.
5. Dry-run it end to end — build a reel from real frames, publish nothing, and watch it.
6. Update the docs and changelog; commit and push.

## Docs to update

- `CHANGELOG.md` — new entry, stating plainly that the moving-camera problem is known
  and deferred, so nobody rediscovers it and rebuilds the lane.
- `docs/SOCIAL_MEDIA_MAP.md` — the lane table, which is the single source of truth for
  what posts where.
- `CLAUDE.md` — the daily schedule list.
