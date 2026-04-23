# Author: Claude Opus 4.7 (1M context)
# Date: 23-April-2026
# PURPOSE: Unified social publisher that owns every scheduled IG+FB
#          story publish across both the gem lane and the archive
#          lane. Exists because the IG Graph API caps
#          @pawel_and_pawleen at 25 publishes per rolling 24h
#          (stories + feed + carousels combined), and prior to this
#          module the two story-lane LaunchAgents competed for that
#          quota on a first-come-first-served basis — which meant
#          the archive lane (90-min cadence, 16×/day) tended to burn
#          Boss's quota before the gem lane (today's live, reaction-
#          curated content) got its turn.
#
#          Plan doc: docs/23-Apr-2026-smart-publishing-queue-plan.md.
#
# SRP/DRY check: Pass — pure orchestration. The actual publish paths
#                (9:16 prep, farm-2026 commit, Graph API upload, DB
#                writeback) stay in tools.pipeline.ig_poster and
#                tools.on_this_day.post_daily unchanged.
