#!/usr/bin/env bash
set -euo pipefail

VPS_HOST="${VPS_HOST:-}"
VPS_USER="${VPS_USER:-root}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/opt/vibe-content-agent}"
BACKUP_ROOT="${BACKUP_ROOT:-backups}"
STAMP="${STAMP:-$(date +%Y%m%d-%H%M%S)}"
BACKUP_DIR="${BACKUP_DIR:-${BACKUP_ROOT}/vps-migration-${STAMP}}"
SSH_OPTS=(
  -o ConnectTimeout=20
  -o ServerAliveInterval=5
  -o ServerAliveCountMax=3
)

mkdir -p "$BACKUP_DIR"

if [[ -z "$VPS_HOST" ]]; then
  echo "Usage: VPS_HOST=203.0.113.10 $0"
  exit 2
fi

echo "==> Checking VPS connection: ${VPS_USER}@${VPS_HOST}"
ssh "${SSH_OPTS[@]}" "${VPS_USER}@${VPS_HOST}" \
  "echo ok && systemctl is-active vibe-content-agent || true && test -d '${REMOTE_APP_DIR}'"

echo "==> Creating remote archive"
REMOTE_ARCHIVE="/tmp/vibe-content-agent-${STAMP}.tgz"
ssh "${SSH_OPTS[@]}" "${VPS_USER}@${VPS_HOST}" \
  "cd '${REMOTE_APP_DIR}' && tar --ignore-failed-read --exclude=.venv --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache --exclude=.ruff_cache -czf '${REMOTE_ARCHIVE}' data config content docs scripts .env docker-compose.yml Dockerfile pyproject.toml README.md src tests 2>/tmp/vibe-backup-warnings.log"

echo "==> Downloading archive"
scp "${SSH_OPTS[@]}" "${VPS_USER}@${VPS_HOST}:${REMOTE_ARCHIVE}" "${BACKUP_DIR}/vibe-content-agent.tgz"

echo "==> Capturing service metadata"
ssh "${SSH_OPTS[@]}" "${VPS_USER}@${VPS_HOST}" "systemctl cat vibe-content-agent" > "${BACKUP_DIR}/vibe-content-agent.service.txt" || true
ssh "${SSH_OPTS[@]}" "${VPS_USER}@${VPS_HOST}" "nginx -T" > "${BACKUP_DIR}/nginx.txt" 2>/dev/null || true
ssh "${SSH_OPTS[@]}" "${VPS_USER}@${VPS_HOST}" "rm -f '${REMOTE_ARCHIVE}'" || true

echo "==> Verifying local archive"
tar -tzf "${BACKUP_DIR}/vibe-content-agent.tgz" >/dev/null
du -sh "${BACKUP_DIR}"
echo "Backup is ready: ${BACKUP_DIR}/vibe-content-agent.tgz"
