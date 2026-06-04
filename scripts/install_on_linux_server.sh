#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/vibe-content-agent}"
APP_USER="${APP_USER:-vibe-agent}"
SERVICE_NAME="${SERVICE_NAME:-vibe-content-agent}"
PORT="${PORT:-8088}"
ARCHIVE_PATH="${1:-}"

if [[ -z "$ARCHIVE_PATH" ]]; then
  echo "Usage: sudo APP_DIR=/opt/vibe-content-agent $0 /path/to/vibe-content-agent.tgz"
  exit 2
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo $0 ${ARCHIVE_PATH}"
  exit 2
fi

apt-get update
apt-get install -y python3 python3-venv python3-pip sqlite3 curl tar

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
fi

mkdir -p "$APP_DIR"
tar -xzf "$ARCHIVE_PATH" -C "$APP_DIR"
cd "$APP_DIR"

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"

mkdir -p data config content docs
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod -R u+rwX "$APP_DIR/data" "$APP_DIR/config" "$APP_DIR/content" "$APP_DIR/docs"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<UNIT
[Unit]
Description=Vibe Content Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment=PYTHONPATH=${APP_DIR}/src
ExecStart=${APP_DIR}/.venv/bin/uvicorn vibe_agent.api:app --host 127.0.0.1 --port ${PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
sleep 3
systemctl is-active "$SERVICE_NAME"
curl -fsS "http://127.0.0.1:${PORT}/health"
echo
echo "Installed ${SERVICE_NAME} in ${APP_DIR}. App listens on 127.0.0.1:${PORT}."
