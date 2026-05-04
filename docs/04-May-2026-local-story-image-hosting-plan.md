# Local Story Image Hosting Plan

**Author:** Bubba (GPT-5.5)
**Date:** 04-May-2026
**PURPOSE:** Plan for replacing GitHub raw URL dependence in the Farm Guardian social Story publisher with Mac Mini-backed public image URLs that Meta can fetch during Instagram Story container creation.
**SRP/DRY check:** Pass — reuse existing Farm Guardian image archive/storage and public serving paths where possible; avoid introducing a second image store.

## Problem

The Story publisher currently depends on GitHub raw URLs for generated Story assets. That made the pipeline fragile: if the asset is not pushed to the expected branch/path, Meta receives a 404 and rejects Story creation. The boss also rejected GitHub as the image-storage strategy.

Meta Graph API still requires a publicly reachable image URL ending in `.jpg`, `.jpeg`, or `.png` when creating media containers. A local filesystem path is not enough. The correct architecture is: keep assets on the Mac Mini, expose only the needed files through a stable public HTTPS route.

## Goal

Make new Story posts use Mac Mini-backed public image URLs, not `raw.githubusercontent.com`, while preserving the existing queue, quota, and DB writeback behavior.

## Scope

1. Audit current publisher URL generation and the existing Farm Guardian public/static image routes.
2. Pick the smallest production route that serves local story images with extension URLs.
3. Update Story asset URL generation to use that local-public route.
4. Leave GitHub retry improvements only if harmless, but remove GitHub raw as the canonical Story host.
5. Verify public fetch returns HTTP 200 and image content for several story assets.
6. Run the smallest relevant tests/checks.
7. Rerun `social-publisher` once only after URL verification.

## Non-goals

- No mass posting loop.
- No image deletion or migration that risks data loss.
- No secrets in logs, code, docs, or commits.
- No new placeholder storage service.

## Acceptance Criteria

- New Story asset URLs are not `raw.githubusercontent.com`.
- New Story asset URLs are public HTTPS URLs backed by Mac Mini/local Farm Guardian storage.
- URLs end in `.jpg`, `.jpeg`, or `.png` and return HTTP 200 with image content via `curl`.
- One safe publisher rerun succeeds or reports a concrete blocker.
- Health checker shows Story posting activity and queue progress, or the remaining blocker is named exactly.

## Rollback

Before code changes, record the current git diff and HEAD. If the change breaks posting or serving, revert only the Story URL-generation change and keep the current queue/DB untouched.

## Execution Notes

Implemented 04-May-2026:

- Added `GET /api/v1/images/story-assets/{filename}` to Guardian's public image API. It serves files from `data/story-assets/`, restricts names to a single path segment, and only allows `.jpg`, `.jpeg`, and `.png`.
- Changed `tools.pipeline.ig_poster.post_gem_to_story()` so prepared 9:16 Story JPEGs are copied to `data/story-assets/` and Meta receives `https://guardian.markbarney.net/api/v1/images/story-assets/<name>.jpg`.
- Preserved the existing `raw_url` result key for compatibility with current logs and callers, but for Story posts it now contains the local Guardian HTTPS URL.
- Left feed, carousel, reel, and disabled on-this-day/archive paths on the existing farm-2026 media flow.
- Recorded rollback context before edits at `/tmp/farm-guardian-plan-records/2026-05-04-before-local-story-hosting.txt`.
- Verified three public Story asset URLs with `curl`: all returned HTTP 200, `image/jpeg`, and complete JPEG bytes.
- Restarted `com.farmguardian.guardian` so the new route is live behind the existing Cloudflare tunnel.
- Ran one `social-publisher` tick after URL verification. It posted 5 reacted gem Stories using Guardian-hosted Story URLs, wrote IG/FB IDs to the ledger, and skipped archive fallback because the reacted-gem queue was non-empty.
- Ran `scripts/pipeline-digest.py --slot noon --dry-run`: it reported 6 Stories since midnight, 354 queued gems, and IG rolling quota usage at 5/25.
