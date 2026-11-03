# Farm Guardian footage on Team 4TB

Author: GPT-6
Date: 01-November-2026

Boss requested that all raw footage goes onto the new SSD. Preserve existing footage and database paths. Copy archive, gems and private curation together from Samsung, plus internal event snapshots and yard-diary masters. Verify copies, briefly stop capture writers for final sync, switch existing paths to Team SSD links, restart and verify live archive writes and dashboard reads. Keep Samsung originals as rollback copies. No unrelated dirty changes or publishing. Derived reel files may also be relocated to prevent internal growth. A missing drive must not cause internal fallback.

Implementation refinement: preserve historical month folders on Samsung via links in the new archive; transfer the active November month and route every future month to Team. Add explicit GUARDIAN_CAPTURE_TMPDIR handling for RTSP scratch so temporary raw frames cannot silently fall back to internal storage. Move thumbnail cache configuration too.

## Completed and verified — 01-November-2026, 22:39 EST

- Actual mount: `/dev/disk17s1` at `/Volumes/Team 4TB`; destination remains on that filesystem.
- Existing repo paths `data/archive`, `data/gems`, `data/private`, `events`, `data/yard-diary`, `data/discord-drops`, `data/reels`, `data/reel-assets`, `data/story-assets`, and `data/cache/thumbs` now link into `/Volumes/Team 4TB/data/farm-guardian/`.
- November archive copied; older month directories link back to unchanged Samsung history. Future month directories are created on Team.
- Guardian and pipeline were unloaded for final sync, then bootstrapped successfully. Guardian API reports fresh house-yard and duo2 captures. S7 was already offline before this work.
- New duo2 raw JPEG at 22:38:51 EST resolved to Team and its file device matched the SSD mount. Existing October image served HTTP 200 through the image API.
- All migrated internal originals were verified by full SHA-256 comparison before their temporary internal rollback copies were removed. Twelve representative archive images matched preserved Samsung originals; final archive dry-run compares all source paths. Old Samsung November originals remain as rollback copies, not active writers.
- Missing explicit capture scratch directory was tested: FileNotFoundError occurs before capture; no tempfile internal fallback.
- Archive/event links have no alternate internal destination. `/Volumes` is root-owned and not writable by this agent, so an absent volume cannot be recreated by capture directory creation. No forced unmount test was done against the live SSD.
- Production pipeline LaunchAgent sets `GUARDIAN_CAPTURE_TMPDIR` to the new SSD `tmp` directory. Thumbnail config also requires Team to be mounted.
- Original config, capture source and LaunchAgent plists, plus the old/new path manifest and checksum-verification marker, are under `/Volumes/Team 4TB/data/farm-guardian/operations/2026-11-01-storage-cutover/`. Samsung root links can be restored using that manifest if required, but recent frames exist only on Team and must be preserved. No unrelated favicon changes were touched, and nothing was committed or pushed.
