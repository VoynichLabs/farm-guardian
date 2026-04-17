#!/bin/bash
# Author: Claude Opus 4.7 (1M context)
# Date: 17-Apr-2026
# PURPOSE: Daily yard-diary capture. Pulls a 4K snapshot from the Reolink
#          (house-yard) via Guardian's local API, stores the master under
#          farm-guardian/data/yard-diary/, publishes a 1920px JPEG into
#          farm-2026/public/photos/yard-diary/, and commits+pushes so
#          Railway redeploys with the new frame baked into the build.
#          One frame per day keyed by YYYY-MM-DD. Idempotent: re-running
#          on the same day overwrites. Runs via launchd at noon local.
# SRP/DRY check: Pass — single responsibility: capture + publish one frame.

set -euo pipefail

GUARDIAN_HOST="http://localhost:6530"
MASTERS_DIR="$HOME/Documents/GitHub/farm-guardian/data/yard-diary"
SITE_REPO="$HOME/Documents/GitHub/farm-2026"
PUBLISHED_DIR="$SITE_REPO/public/photos/yard-diary"
LOG_DIR="$HOME/Documents/GitHub/farm-guardian/data/pipeline-logs"
LOG_FILE="$LOG_DIR/yard-diary.log"

TODAY=$(date +%Y-%m-%d)
MASTER="$MASTERS_DIR/${TODAY}.jpg"
PUBLISHED="$PUBLISHED_DIR/${TODAY}.jpg"

mkdir -p "$MASTERS_DIR" "$PUBLISHED_DIR" "$LOG_DIR"

log() { echo "[$(date -Iseconds)] $*" >> "$LOG_FILE"; }

log "capture start (${TODAY})"

if ! curl -sf -o "$MASTER" --max-time 30 "${GUARDIAN_HOST}/api/v1/cameras/house-yard/snapshot"; then
  log "ERROR: snapshot fetch failed"
  exit 1
fi

BYTES=$(stat -f%z "$MASTER")
if [ "$BYTES" -lt 50000 ]; then
  log "ERROR: snapshot suspiciously small ($BYTES bytes) — aborting"
  rm -f "$MASTER"
  exit 1
fi

if ! sips -Z 1920 "$MASTER" --out "$PUBLISHED" >/dev/null 2>&1; then
  log "ERROR: sips resize failed"
  exit 1
fi

log "captured ${TODAY}: master=${BYTES}B published=$(stat -f%z "$PUBLISHED")B"

cd "$SITE_REPO"
if ! git diff --quiet -- "public/photos/yard-diary/${TODAY}.jpg" || \
   [ -n "$(git status --porcelain public/photos/yard-diary/${TODAY}.jpg)" ]; then
  git add "public/photos/yard-diary/${TODAY}.jpg"
  git commit -m "yard-diary: ${TODAY}" >/dev/null
  if git push >/dev/null 2>&1; then
    log "pushed ${TODAY} to origin"
  else
    log "WARN: git push failed (will retry on next run)"
  fi
else
  log "no change to publish for ${TODAY}"
fi

log "capture done (${TODAY})"
