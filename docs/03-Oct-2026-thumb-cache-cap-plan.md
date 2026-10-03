# 03-Oct-2026 — Bounded thumbnail cache (cannot fill a disk)

Author: Claude Opus 5.5 — 03-Oct-2026

## Problem

v2.76.0 moved the resized-image cache (`images_thumb.py`) onto the Samsung SSD through the
`data/cache/thumbs` symlink, but the cache still had no upper bound: every gem x every size
variant is kept forever. On 03-Oct-2026 it held 2,685 files / ~934 MB and grows with every
new gem. The internal drive has filled twice (17-Sep, 2-Oct); the Boss's directive is that
this cache must not be able to fill any disk.

## Requirements (Boss directive, 03-Oct-2026)

1. Cache lives on the Samsung SSD, never the internal drive. If the SSD is not mounted the
   route resizes on the fly and writes nothing to disk.
2. Hard size cap with least-recently-used eviction; only a fixed whitelist of widths can be
   cached so callers cannot mint unlimited variants.
3. Cap and path configurable in config with a safe default.
4. A test proving eviction keeps the cache under the cap.
5. Report free space (internal + SSD) before/after and the cache's steady-state size.

## Design

- New config block `images.thumb_cache`:
  - `dir` — absolute cache directory (default
    `/Volumes/Samsung 9100 SSD/farm-guardian-data/cache/thumbs`).
  - `require_mount` — mount point that must be a real mount before any write (default
    `/Volumes/Samsung 9100 SSD`). The resolved cache dir must also sit under it, so a
    dangling symlink or an unmounted `/Volumes/...` path can never land on the internal disk.
  - `max_bytes` — hard cap (default 2 GiB).
  - `min_free_bytes` — skip writing if the SSD has less than this free (default 20 GiB).
- Writes go through one guarded path: mount check → free-space check → write via temp file →
  add to running total → evict oldest until at or below 90% of the cap. A single lock guards
  the running total and eviction.
- LRU: a cache hit touches the file's mtime (throttled to once per hour per file, since
  macOS does not reliably update atime). Eviction deletes the oldest mtime first.
- Running total is seeded by a directory scan at configure() and re-synced by every eviction
  scan, so out-of-band deletes cannot make it drift for long.
- Whitelist: `images_thumb` only caches sizes {480, 720, 1920} (the API's thumb / card /
  1920); any other size raises. `full` (0) is the untouched original and never cached.
- No mount / not enough free space → encode in memory and return; nothing written. Same
  ETag as the cached copy (the bytes are identical), so browser caching is unaffected.

## TODO

- [ ] `images_thumb.py`: config, mount guard, cap + LRU eviction, size whitelist.
- [ ] `images_api.py`: pass `images.thumb_cache` config into configure().
- [ ] `config.json` / `config.example.json`: add the block with the defaults.
- [ ] Tests: eviction stays under cap and keeps recently used files; unmounted → no writes;
      non-whitelisted size rejected.
- [ ] Run tests, restart Guardian once (backup/rollback tag first), verify live, CHANGELOG.

## Addendum (same day) — 12-hour expiry (Boss update)

The cache only needs about 12 hours of frames. Added `images.thumb_cache.max_age_seconds`
(default 43200 = 12 h): resized copies not used for that long are deleted, on top of the size
cap. "Used" = the file's mtime, set on write and refreshed on a cache hit (hourly throttle),
so tiles the website keeps showing stay warm. The prune runs at startup and on image access at
most every 10 minutes. It only ever deletes files inside the cache dir — archive originals,
gems and anything the social pipelines read are untouched.

- [x] `max_age_seconds` config + default, expiry in the eviction scan, throttled on-access prune.
- [x] Tests: default is 12 h; startup expires copies past 12 h, keeps younger ones and the
      original; prune on access; configurable.
