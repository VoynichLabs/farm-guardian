#!/usr/bin/env bash
# Push the local sounds/ tree to GWTC so playback.py can address the WAVs by
# Windows path. Idempotent: re-run after adding new exemplars.
#
# Author: Claude Opus 4.7 (1M context) -- Bubba
# Date:   16-April-2026
# Usage:  ./deploy/push-sounds-to-gwtc.sh [--dry-run]
# Env:    GWTC_HOST (default 192.168.0.68), GWTC_USER (default markb),
#         GWTC_REMOTE_PATH (default C:/farm-sounds)

set -euo pipefail

HOST="${GWTC_HOST:-192.168.0.68}"
USER="${GWTC_USER:-markb}"
REMOTE_PATH="${GWTC_REMOTE_PATH:-C:/farm-sounds}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCAL_SOUNDS="$(cd "${SCRIPT_DIR}/.." && pwd)/sounds"

if [[ ! -d "${LOCAL_SOUNDS}" ]]; then
  echo "error: local sounds dir not found at ${LOCAL_SOUNDS}" >&2
  exit 1
fi

if [[ "${1:-}" == "--dry-run" ]]; then
  echo "[dry-run] target: ${USER}@${HOST}:${REMOTE_PATH}"
  echo "[dry-run] local:  ${LOCAL_SOUNDS}"
  echo "[dry-run] would mkdir remote dir then scp -r '${LOCAL_SOUNDS}/.' to it"
  exit 0
fi

# Ensure the remote root exists; PowerShell mkdir is idempotent with -Force.
ssh -o StrictHostKeyChecking=no "${USER}@${HOST}" \
  "powershell -NoProfile -Command \"New-Item -ItemType Directory -Path '${REMOTE_PATH}' -Force | Out-Null\""

# scp -r preserves the subdir layout. Windows OpenSSH accepts forward-slash
# paths on the remote side; PowerShell on GWTC accepts both styles when the
# WAV is later passed to System.Media.SoundPlayer.
scp -r -o StrictHostKeyChecking=no "${LOCAL_SOUNDS}/." "${USER}@${HOST}:${REMOTE_PATH}/"

echo "done. remote tree at ${USER}@${HOST}:${REMOTE_PATH}"
