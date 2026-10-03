# 03-Oct-2026 — Gem "card" image variant (lighter farm-2026 home page)

Author: Claude Opus 5.5 — 03-Oct-2026

## Problem

The farm-2026 SEO audit (farm-2026 `docs/SEO-CHECKLIST.md`, items 15 and 17) found the
home page heavy on phones, almost all of it gem photos. Measured 03-Oct-2026 with a
mobile-emulated headless Chromium against the live site:

- The home page's `RecentGemsRail` loads 12 gems through `GemCard`, which uses
  `row.full_url` = `/api/v1/images/gems/{id}/image?size=1920`.
- Those 12 requests total ~6.9 MB of the page's ~12.3 MB (the audit's ~22 MB was a
  Lighthouse run; same cause).
- Root cause: every recent gem is a 1080x1920 portrait s7-cam frame. `size=1920` fits the
  image inside a 1920x1920 box, so it is not downscaled at all — it is a quality-85
  re-encode of the full frame (~420-710 KB each), shown in a tile a couple of hundred CSS
  pixels tall.
- The existing `thumb` (480 px long edge, 270x480 for portrait) is too soft for the
  single-column gallery on high-DPI phones, which is why GemCard prefers `full_url`.

## Scope

In:
- New size value `card` on `GET /api/v1/images/gems/{id}/image`: 720 px long edge, WebP
  (quality 78), generated once and cached on disk like the existing thumbnails.
- New `card_url` field in the public gem row shape (`/gems`, `/gems/{id}`, `/recent`
  share `_shape_public_row`). Additive only.
- `images_thumb.get_thumb` returns the media type; cache filename and ETag carry the format
  so a WebP can never be served (or 304'd) as a JPEG and vice versa.
- Gem image responses get a long cache lifetime (30 days, immutable). Content for a given
  URL never changes — the cache key is the source sha256.
- Thumbnail cache moved to the Samsung SSD (`data/cache/thumbs` becomes a symlink, the same
  pattern `data/archive` already uses). `configure()` no longer crashes Guardian startup if
  the SSD is missing; it logs and image requests fall back to the existing placeholder.

Out (unchanged on purpose):
- `thumb`, `1920`, `full` bytes, the route's default size (`thumb`), and `full_url`
  (GemLightbox uses it for the full-viewport view).
- story-assets / reel-assets routes (the `.jpg`/`.mp4`-terminated URLs Meta fetches).
- No `srcset`; one card size covers rail tiles and single-column gallery cards.

## farm-2026 (separate branch + PR, not merged to main by this work)

- `app/components/guardian/types.ts`: add `card_url?: string` to `GemRow` (optional so the
  site keeps working against an older Guardian).
- `app/components/gems/GemCard.tsx`: `src={row.card_url || row.full_url || row.thumb_url}`.
- CHANGELOG entry.

## TODOs

1. images_thumb.py: format-aware generation, cache path, ETag, media type; tolerant configure.
2. images_api.py: `card` size, `card_url`, media type passthrough, cache header.
3. Test file `test_images_thumb.py` (real Pillow encode against a temp JPEG; no mocks).
4. Move thumbs cache to SSD (rsync, then swap dir for symlink).
5. CHANGELOG v2.76.0.
6. Deploy: commit on branch, tag rollback point, one `launchctl kickstart -k` of
   `com.farmguardian.guardian` (same action the nightly 03:00 restart agent performs),
   verify `/api/v1/images/ping`, `card` bytes, headers, 304. Roll back to `main` and kickstart
   if broken.
7. Merge branch to main, push, confirm level with origin.
8. farm-2026 branch + PR; local build pointed at live Guardian to measure the home page.

## Verification

- Before/after bytes for the home page's 12 gem requests.
- `curl -D -` on a GET for the new variant: `content-type: image/webp`, cache-control, ETag;
  `If-None-Match` returns 304.
- Existing sizes return byte-identical responses before/after.
- Pipeline tests in `tools/pipeline/test_*.py` still pass.
