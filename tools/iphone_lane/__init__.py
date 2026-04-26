# Author: Claude Opus 4.7 (1M context)
# Date: 26-April-2026
# PURPOSE: iPhone live-ingest lane. Walks the Photos.app library for any
#          asset added in the last N hours that hasn't been ingested yet,
#          runs each through the standard VLM pipeline (same enricher and
#          schema the cameras use), persists into image_archive with
#          camera_id="iphone", and posts strong-tier results into Discord
#          #farm-2026 for Boss to react. From there the existing reaction
#          gate carries them out to IG/FB exactly like camera gems.
# SRP/DRY check: Pass — reuses pipeline.vlm_enricher, pipeline.store,
#                pipeline.gem_poster, pipeline.quality_gate. The only
#                lane-specific code is Photos.sqlite enumeration, HEIC->JPEG
#                conversion via sips, and the dedupe ledger.
