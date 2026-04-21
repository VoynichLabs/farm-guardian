# Author: Claude Opus 4.7 (1M context)
# Date: 21-April-2026
# PURPOSE: Package marker for the on-this-day Facebook archive pipeline.
#          The package mines the 21k+ Qwen-described iPhone photo
#          catalog at ~/bubba-workspace/projects/photos-curation/
#          photo-catalog/master-catalog.csv for historical matches on
#          today's calendar date in 2022/2024/2025 (2023 skipped per
#          Boss), and publishes the best candidate to the Yorkies FB
#          Page via the existing fb_poster + git_helper pipeline.
#
#          Public modules:
#            - selector.py        — Photos.sqlite + catalog join/rank
#            - catalog_backfill.py — diff & re-run process_batch on misses
#            - caption.py         — deterministic caption composer
#            - post_daily.py      — CLI orchestrator (--dry-run default)
#
# SRP/DRY check: Pass — reuses tools/pipeline/git_helper.py and
#                tools/pipeline/fb_poster.py unchanged. The only new
#                surface is "historical photo selection from catalog",
#                which didn't exist before.
