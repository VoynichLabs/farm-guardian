# 05-Nov-2026 — Labelled S7 post dataset (Boss reactions as labels)

Author: Claude Haiku 5.5 Medium

## Scope

**In:** every Samsung S7 gem post in #farm-2026 since the score moved to the 0–100 scale in July 2026. Each post becomes one row: the score the Boss saw, whether the Boss reacted, the archive frame behind the post, and that frame's scoring details (sub-scores, activity, bird count, scene, caption, image quality).

**Out:** no pipeline behaviour changes (posting floor, scoring, reactions sync are untouched). No pictures viewed. No per-person reactor lookups. No new Discord client. Yard and road cameras are not part of this.

## Why this table

Reactions from the Boss are the only human quality signal we have. Joined to the score and the frame details, they give a labelled set: reacted (positive) versus passed over (negative). The reacted side is exact, because the sync stamps the post's message ID on the archive row. The passed-over side has no stamp, so each post is linked to its frame by caption text, score, and capture time.

## Architecture

- **Discord listing:** one paged read of the channel, reusing `tools/discord_harvester.py` for the bot token, API base, channel ID and headers. Rate limits wait and retry the same page; any other failure raises. Cached once to the SSD so later steps never re-list.
- **Archive:** `data/guardian.db`, read-only.
- **Linker:**
  - reacted posts: exact match on the stamped message ID;
  - passed-over posts: the post text (caption before the score line) must be a prefix of the archive caption, the archive score must equal the posted score, and the frame must be captured at or shortly before the post time. Ambiguous matches are flagged, not guessed.
- **Output:** a CSV plus a readme in `/Volumes/Team 4TB/kaggle/s7-reaction-20261105/` (big data goes on the SSD, not the internal drive).
- **Code:** `scripts/s7-reaction-dataset.py` (new). Findings in `docs/05-Nov-2026-s7-reaction-findings.md`.

## TODOs

1. Write this plan (done when committed).
2. One channel listing, cached as JSON on the SSD.
3. Link every post; record link method and ambiguity count.
4. Validate the caption linker by re-linking reacted posts without their stamp and checking it recovers the stamped frame.
5. Write the CSV and readme.
6. Break reactions down by score bands (same-era only), sub-scores, activity, bird count, scene. Flag that the score details are model outputs.
7. Independent audit of the claims (adversarial check).
8. Findings doc, memory notes, commit and push this branch only.

## Docs and changelog touchpoints

- New: this plan, the findings doc, the script.
- No behaviour change, so no CHANGELOG entry is required; the findings doc records the reasoning.
- Memory notes for the next session: Discord pull limits and the time-match mistake.
