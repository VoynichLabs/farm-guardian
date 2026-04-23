# ⚠️ SUPERSEDED — DO NOT FOLLOW THIS PLAN

**This doc was written 2026-04-22 before the author discovered the existing catalog pipeline.**

**Current authoritative plan:** [`~/bubba-workspace/projects/photos-curation/photo-catalog/PLAN-23-Apr-2026-resume.md`](../../bubba-workspace/projects/photos-curation/photo-catalog/PLAN-23-Apr-2026-resume.md) (lives alongside the code that actually exists).

## Why this was wrong

This plan proposed building a greenfield VLM-over-iCloud-library tool at `farm-guardian/tools/icloud-vlm/`. In reality, an equivalent pipeline has been running intermittently since mid-March 2026 at `~/bubba-workspace/projects/photos-curation/photo-catalog/`, has produced **34,923 JSON sidecars covering 24,406 unique UUIDs (54% of the 45,158-image library)**, and was last active 2026-04-01. The right move is to resume that pipeline — not replace it.

## What to read instead

1. **`~/bubba-workspace/projects/photos-curation/photo-catalog/PLAN-23-Apr-2026-resume.md`** — accurate current-state plan with the 3 known code defects to fix before restart, the gap analysis, and the post-run consolidation steps.
2. **`~/bubba-workspace/projects/photos-curation/photo-catalog/CATALOG_STATUS.md`** — authoritative coverage numbers as of 2026-04-23.
3. **`~/bubba-workspace/memory/projects/photo-catalog.md`** — the older project memory (still mostly correct, predates the stats above).

## What of this old draft is still useful (if anything)

- The LM Studio safety principles (native `/api/v1/chat`, reasoning disabled, no silent `/v1/models/load`) transfer verbatim to the resume plan.
- The FTS5 schema sketch is a reasonable starting point for the post-run consolidation index.
- Everything else — directory layout, tool name, enumeration approach, model name — is stale or wrong.

Leaving this doc in the repo as a historical pointer so anyone who grep'd it doesn't waste a session re-deriving the situation. Don't delete.
