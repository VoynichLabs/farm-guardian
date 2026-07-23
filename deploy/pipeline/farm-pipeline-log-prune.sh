#!/bin/bash
# Keep the pipeline's /tmp logs under control. If a log exceeds the
# threshold, keep the tail and overwrite in place so the running
# pipeline's stderr/stdout fd (the inode) is preserved.
#
# Deployed copy lives at ~/bin/farm-pipeline-log-prune.sh (LaunchAgent
# points there); this file in the repo is the source of truth.
#
# 23-Jul-2026: widened beyond the two pipeline logs. An audit found
# /tmp/guardian.out.log at 93 MB and the discord-reaction-sync pair at
# ~8 MB combined, none of them covered here — every long-running farm
# service that launchd redirects into /tmp needs to be in this list or it
# grows forever. Threshold dropped to 25 MB because 50 MB let
# guardian.out.log sit at ~93 MB between the once-daily runs.
set -u
THRESHOLD_BYTES=$((25 * 1024 * 1024))   # 25 MB
KEEP_LINES=10000

prune_one() {
    local path="$1"
    [ -f "$path" ] || return 0
    local size
    size=$(stat -f%z "$path" 2>/dev/null || echo 0)
    [ "$size" -le "$THRESHOLD_BYTES" ] && return 0
    local tmp
    tmp=$(mktemp /tmp/farm-pipeline-log-prune.XXXXXX) || return 1
    tail -n "$KEEP_LINES" "$path" > "$tmp" && cat "$tmp" > "$path"
    rm -f "$tmp"
    echo "$(date -u +%FT%TZ) pruned $path: was $size bytes, kept last $KEEP_LINES lines"
}

prune_one /tmp/pipeline.err.log
prune_one /tmp/pipeline.out.log
prune_one /tmp/guardian.err.log
prune_one /tmp/guardian.out.log
prune_one /tmp/discord-reaction-sync.err.log
prune_one /tmp/discord-reaction-sync.out.log
prune_one /tmp/cloudflared-guardian.log
prune_one /tmp/lmstudio-watchdog.log
