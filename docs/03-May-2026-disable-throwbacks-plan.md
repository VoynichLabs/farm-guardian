# 03-May-2026 disable throwbacks plan

## Scope

In scope:
- Disable the archive throwback Discord pump so old catalog/gallery photos stop entering `#farm-2026` as reaction-gated synthetic drops.
- Disable on-this-day archive Story publishing, including the current `social-publisher` fallback path, any stale direct `on-this-day-stories.py` LaunchAgent that might still be loaded, and direct `post_daily.py --publish/--auto-story` use.
- Disable the Nextdoor throwback cross-post lane so no archive/throwback content posts while the source logic is untrusted.
- Make reaction sync ignore new `Archive` webhook drops so stale Discord messages cannot become new synthetic gems.
- Update social docs and changelog so future agents do not re-enable the throwback paths by accident.

Out of scope:
- Deleting existing data, Discord messages, `image_archive` rows, or posted IG/FB media.
- Redesigning the mixed daily Reel selector or weekly yard Reel. That needs a separate plan after the bad throwback paths are stopped.
- Changing live camera/VLM capture behavior.

## Architecture

The safest short-term deactivation is fail-closed at script/config boundaries:

- `scripts/archive-throwback.py` exits successfully unless `FARM_ARCHIVE_THROWBACK_ENABLED=1` is explicitly set.
- `scripts/on-this-day-stories.py` exits successfully unless `FARM_ON_THIS_DAY_STORIES_ENABLED=1` is explicitly set.
- `tools/on_this_day/post_daily.py` keeps dry-run selection available but exits successfully for publish/auto-story unless `FARM_ON_THIS_DAY_STORIES_ENABLED=1` is explicitly set.
- `tools/social/config.json` gets `archive_fallback_enabled: false`.
- `tools/social/publisher.py` reads that flag and skips `_post_archive_one()` when disabled.
- `tools/nextdoor/crosspost.py` refuses `lane="throwback"` unless `FARM_NEXTDOOR_THROWBACK_ENABLED=1`.
- `scripts/discord-reaction-sync.py` skips messages authored by the `Archive` webhook.

This avoids relying on launchd state. Even if a stale LaunchAgent fires on the Mac Mini, the script returns 0 without posting.

Future TODO: redesign throwbacks as exact-date-only "on this day" sourcing, e.g. May 3 2025 / May 3 2024 for May 3. The current on-this-day behavior does not appear to work well enough and must not use loose back-catalog/gallery selection.

## TODO

1. [x] Add fail-closed kill switches to `archive-throwback.py` and `on-this-day-stories.py`.
2. [x] Add `archive_fallback_enabled` to social config and honor it in `social-publisher`.
3. [x] Disable the Nextdoor throwback lane and reaction-sync ingestion of `Archive` webhook drops.
4. [x] Update docs and changelog.
5. [x] Run compile and config checks.
6. [x] Commit and push.

## Docs/Changelog Touchpoints

- `CHANGELOG.md`
- `CLAUDE.md`
- `docs/SOCIAL_MEDIA_MAP.md`
- `docs/20-Apr-2026-ig-scheduled-posting-architecture.md`
- `tools/on_this_day/README.md`
