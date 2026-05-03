# 03-May-2026 reacted Story priority fix plan

## Scope

In scope:
- Fix the hourly social publisher so a non-empty reacted-gem Story queue always blocks the on-this-day archive fallback.
- Let the publisher skip over a small number of failing oldest gem rows within the same tick so one bad row does not stall every later reacted gem.
- Clarify the pipeline digest quota label as rolling 24h usage and make its Story count include archive fallback Stories from the shared ledger.
- Update docs and changelog with the actual priority rule.

Out of scope:
- Changing the Discord reaction sync matching rules.
- Changing the Instagram Graph API quota cap.
- Changing on-this-day archive selection or iPhone ingest ranking.
- Running live posts from this development machine, because it does not have the production database or Mac Mini secrets.

## Architecture

`tools/social/publisher.py` remains the single decider for hourly Story publishing. It already delegates actual gem Story publishing to `tools.pipeline.ig_poster.post_gem_to_story()` and archive fallback to `tools.on_this_day.post_daily.run_auto_story_cycle()`.

The bug is in the decision boundary after the gem lane. `_drain_gem_queue()` currently returns only posted count and quota status, so `run_tick()` treats `gems_posted == 0` as equivalent to "queue empty." That is false when the queue exists but the attempted oldest rows fail. The fix is to return queue depth and attempt count from `_drain_gem_queue()` and only allow archive fallback when queue depth is zero.

To reduce backlog stalls, `_drain_gem_queue()` will still cap successful posts at `max_per_tick`, but may attempt a bounded look-ahead beyond that success cap. Local file/path-style permanent failures will be marked in `image_archive.ig_story_skip_reason` with a `story-permanent-skip:` prefix so future selector runs skip dead rows. Transient API/git failures are logged and retried on future ticks. Quota-style errors still stop the whole tick.

`scripts/pipeline-digest.py` stays read-only. Its quota line will say "rolling 24h" because it reads `tools.social.ledger.count_last_24h()`, not local calendar-day usage. Its Story count will combine `image_archive` reacted-gem Story metadata with social-ledger `archive` lane rows so "Stories posted" and "quota used" are talking about the same publishing surface.

## TODO

1. [x] Patch `tools/social/publisher.py` to return queue depth and block archive fallback whenever queue depth is greater than zero.
2. [x] Add bounded look-ahead in the gem drain loop so bad oldest rows do not prevent all later reacted gems from posting.
3. [x] Mark local file/path-style permanent failures as `story-permanent-skip` and exclude them from future Story queue selections.
4. [x] Patch `scripts/pipeline-digest.py` wording from "today" to "rolling 24h" and include archive fallback Stories in the Story count.
5. [x] Update `CLAUDE.md`, `docs/SOCIAL_MEDIA_MAP.md`, and the Instagram architecture doc with the clarified priority invariant.
6. [x] Update `CHANGELOG.md`.
7. [x] Verify with compile checks and a local monkeypatch test of the gem-drain behavior.
8. [x] Fetch, rebase if needed, commit, and push.

## Docs/Changelog Touchpoints

- `CHANGELOG.md`
- `CLAUDE.md`
- `docs/SOCIAL_MEDIA_MAP.md`
- `docs/20-Apr-2026-ig-scheduled-posting-architecture.md`
- `tools/on_this_day/README.md`
