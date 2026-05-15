#!/bin/bash
# Keep the pipeline's /tmp logs under control. If a log exceeds the
# threshold, keep the tail and overwrite in place so the running
# pipeline's stderr/stdout fd (the inode) is preserved.
#
# Deployed copy lives at ~/bin/farm-pipeline-log-prune.sh (LaunchAgent
# points there); this file in the repo is the source of truth.
set -u
THRESHOLD_BYTES=$((50 * 1024 * 1024))   # 50 MB
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
