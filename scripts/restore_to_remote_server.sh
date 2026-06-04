#!/usr/bin/env bash
set -euo pipefail

TARGET_HOST="${TARGET_HOST:-}"
TARGET_USER="${TARGET_USER:-root}"
TARGET_TMP_DIR="${TARGET_TMP_DIR:-/root}"
APP_DIR="${APP_DIR:-/opt/vibe-content-agent}"
ARCHIVE_PATH="${1:-}"
SSH_OPTS=(
  -o ConnectTimeout=20
  -o ServerAliveInterval=5
  -o ServerAliveCountMax=3
)

if [[ -z "$TARGET_HOST" || -z "$ARCHIVE_PATH" ]]; then
  echo "Usage: TARGET_HOST=192.168.1.50 $0 backups/.../vibe-content-agent.tgz"
  exit 2
fi

if [[ ! -f "$ARCHIVE_PATH" ]]; then
  echo "Archive not found: ${ARCHIVE_PATH}"
  exit 2
fi

REMOTE_ARCHIVE="${TARGET_TMP_DIR}/vibe-content-agent.tgz"
REMOTE_INSTALLER="${TARGET_TMP_DIR}/install_on_linux_server.sh"

echo "==> Uploading archive and installer to ${TARGET_USER}@${TARGET_HOST}"
scp "${SSH_OPTS[@]}" "$ARCHIVE_PATH" "${TARGET_USER}@${TARGET_HOST}:${REMOTE_ARCHIVE}"
scp "${SSH_OPTS[@]}" "scripts/install_on_linux_server.sh" "${TARGET_USER}@${TARGET_HOST}:${REMOTE_INSTALLER}"

echo "==> Installing on remote server"
ssh "${SSH_OPTS[@]}" "${TARGET_USER}@${TARGET_HOST}" \
  "APP_DIR='${APP_DIR}' bash '${REMOTE_INSTALLER}' '${REMOTE_ARCHIVE}'"

echo "Remote install complete: ${TARGET_HOST} -> ${APP_DIR}"
