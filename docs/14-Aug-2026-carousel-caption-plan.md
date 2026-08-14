# 14-Aug-2026 — Fix the daily carousel's caption, and the diary behind it

**Author:** Claude Opus 5
**Status:** ✅ SHIPPED 14-Aug-2026 (CHANGELOG v2.71.2). Scope grew mid-session: Boss then said
the diary itself is *"most of the time really bad"*, which turned out to share a root cause with
the caption complaint — both were written from the wrong source. See "The diary" below.
**Trigger:** Boss looked at the daily Instagram post and called it stupid — *"it wasn't a reel,
it was just the gems as a post. And then the caption wasn't even correct. It was just a random
caption for one of the pictures."*

He is right on both counts, and the caption half is a genuine bug.

## What is actually wrong

`scripts/ig-daily-carousel.py::_build_caption` picks **one** gem — the one with the highest
bird count — and uses that single photo's VLM `caption_draft` verbatim as the caption for the
whole carousel. The other six photos are not consulted at all.

Confirmed by live dry-run today (7 photos):

> *"A golden-brown rooster with bright red comb and wattle stares directly into the…"*

That is a description of picture #4. It is the caption on all seven.

**The reel lanes already do this properly.** `daily_reel_runner._generate_reel_caption()` gathers
the `caption_draft` of every frame, and asks the loaded local VLM to synthesize one cohesive
caption across the set — with the farm diary and the living-flock roster injected for real
narrative and real bird names. Its output yesterday:

> *"The flock's afternoon was quiet and golden — chickens pecking, turkeys snoozing on…"*

So the farm already owns a good caption writer. The carousel simply never called it.

**Contributing factor, now resolved:** the diary that feeds caption narrative had been writing
into a dead folder since 1-Aug (fixed this morning, v2.71.1). Even the reel captions were
working from news up to two weeks stale — the staleness checker was reporting YELLOW with
`newest_eligible: 2026-07-31` every day. It now reads 19 fresh entries through 13-Aug. Captions
generated from today forward have real, current material to work with for the first time in two
weeks; that is worth knowing before judging any caption written before today.

## Scope

**In:** `scripts/ig-daily-carousel.py::_build_caption` calls
`daily_reel_runner._generate_reel_caption()` instead of copying one gem's draft. Keep the
existing hashtag wrapper and the existing fallback.

**Out — no new caption system.** This is a one-function change that deletes a bad local
implementation in favour of the shared good one. SRP/DRY: the caption writer already exists,
is already used by five lanes, already handles LM Studio being unreachable, and already has a
literal fallback.

## On "it wasn't a reel" — recommendation: keep it a carousel

Not dodging the point, but converting this lane to a Reel would reproduce the exact thing Boss
killed yesterday.

- The gems are **still photos**, about 7 a day. A Reel of 7 stills is a 7-second slideshow —
  which is precisely what the 18:00 mixed lane was when he said *"only eight frames… what the
  hell is going on here"* and retired it (v2.70.7).
- A carousel shows all 7 at full quality, swipeable, each one readable. That is the right
  container for a handful of good stills.
- The farm already posts **four** Reels a day (house-yard 09:00, duo2 15:00, S7 21:00, dashcam
  21:30). All four are time-lapses of largely static scenes. The carousel is the only lane
  showing curated, chosen photographs — it is the best content on the account, presented worst.

So the recommendation is: **fix how it reads, don't change what it is.** If after seeing a
properly-captioned carousel Boss still wants the 12:30 slot to be a Reel, that is a separate
and easy change — but the caption is the actual defect and should not be bundled with a format
argument.

## TODOs

1. Rewrite `_build_caption` to call `_generate_reel_caption`, keeping hashtags + fallback.
2. Verify with `--dry-run` that the caption references the whole set, not one photo.
3. Compare before/after captions on the same gem set in the plan doc.
4. CHANGELOG + `docs/SOCIAL_MEDIA_MAP.md`.

## The diary — same disease, worse case

Boss: *"Bubba also posts the farm diary to the channel. If I think that is good, I react to it.
**Most of the time it's really bad.**"* Only 6 of 44 entries ever earned a reaction, so the
72h-window fix (v2.71.1) was necessary but nowhere near sufficient — a promotion pipeline for
entries nobody wants is worth nothing.

**Root cause: the diary's only source was the Discord transcript**, so it summarised a chatroom
rather than a farm. Read side by side, the pattern is unmistakable:

| Entry | Reaction | What it actually reports |
|---|---|---|
| 13-Aug "Bronze tom over the netting" | ✅ liked | one real event, its consequence, and an action ("that gap is now a known gap") |
| 10-Aug "A portrait for Henriessa, and a cable that mattered" | ✅ liked | a bird identified, a fault fixed |
| 04-Aug | ✗ ignored | a charming bulldog-meets-turkeys story — buried under a paragraph on **2 GB vs 4 GB Raspberry Pi boards** |
| 12-Aug "Quiet day, Boss away" | ✗ ignored | **"Nothing to report from the birds today"** — then Doom soundtrack trivia, Latin, and an imaginary giraffe itinerary |

The entries Boss likes are simply the days the chatter happened to be about birds.

**The 12-Aug entry is the proof.** On the day it declared nothing to report, the database held:

| | |
|---|---|
| frames captured | **7,910** |
| VLM-described | **7,910** |
| strong-tier photos | **395** |
| photos a human reacted to | **14** |

A full day of the farm, unread, while the diary wrote up a giraffe joke.

**Fix:** `gather_camera_day()` feeds the entry what the cameras saw — **photos Boss reacted to
first** (his reaction is the best available signal of what mattered, better than any VLM score),
then strong-tier scenes **thinned to one per hour** so the entry reads morning-to-evening
instead of fixating on the busiest hour. The prompt makes that the spine and demotes the
conversation to supporting detail, with chatroom trivia banned outright and "the day was quiet"
forbidden whenever camera material exists. A quiet chat is not a quiet farm, so the old
bail-out below 200 chars of conversation now requires **both** sources to be thin.

### Before / after, same job

**Before (12-Aug, the old path):**

> Nothing to report from the birds today. The Discord channel ran almost entirely off-topic —
> Doom soundtrack trivia, Latin, and a wholly imaginary giraffe-riding itinerary…

**After (14-Aug, live dry-run):**

> A busy day of portraits at Birdcatraz… **almost every reacted frame was a bird looking
> straight down the lens** — a copper-and-dark rooster at 10:05, a dark hen with a reddish head
> and yellow legs at 10:17… **The afternoon's news was a large pumpkin in the run.** From 16:10
> onward the birds were on it…

## Verification (14-Aug-2026, live)

**Carousel caption**, same 7-gem set, before and after:

- Before: *"A golden-brown rooster with bright red comb and wattle stares directly into the…"* — picture #4, captioning all seven.
- After: *"Today's flock was busy pecking pumpkins and watching the sun dip low—some rooste…"* — the set.

**Diary:** 2,876 chars of camera material gathered for 14-Aug; entry written with zero chatroom
trivia; hedging preserved where the source hedged ("what the camera read as a carved one").

## Separate observation worth Boss's attention

Photo reactions and diary reactions both land in `#farm-2026`, on the same channel, with the
same 👍. They drive completely different machinery — a photo reaction publishes that picture to
Instagram; a diary reaction publishes that write-up to the website. Nothing distinguishes them
except what the message happens to contain. That is workable but it is why "what am I reacting
to?" is a fair question, and it is worth deciding whether the diary should move to its own
channel.
