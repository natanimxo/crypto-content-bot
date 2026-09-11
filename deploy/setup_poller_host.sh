#!/usr/bin/env bash
set -euo pipefail

# Idempotent setup/update script for the always-on Telegram approval poller
# (Hetzner CX22 or similar Ubuntu/Debian box, 2026-09-11 -- see BACKLOG.md's
# "Telegram / bot reliability" section for why this exists: GitHub Actions'
# scheduler only actually fired the poll every ~3.6-4.9 hours regardless of
# the requested interval, and Telegram drops an un-fetched callback_query
# well under 10 minutes, so most taps were lost no matter how the cron was
# tuned).
#
# Safe to re-run any time to deploy an update -- initial setup and every
# later update are the exact same command:
#
#   sudo bash deploy/setup_poller_host.sh
#
# What this does NOT do: touch an existing .env, or write any secret value
# anywhere. On first run it copies .env.example (which has no real values --
# just the variable names) to /opt/telegram-bot/.env with 600 permissions,
# then STOPS and tells you to fill it in by hand, directly on the box. Every
# run after that leaves .env completely alone.

REPO_URL="https://github.com/natanimxo/crypto-content-bot.git"
DEPLOY_ROOT="/opt/telegram-bot"
APP_DIR="$DEPLOY_ROOT/app"
VENV_DIR="$DEPLOY_ROOT/venv"
ENV_FILE="$DEPLOY_ROOT/.env"
SERVICE_USER="telegrambot"
SERVICE_NAME="telegram-approval-poller"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 1
fi

echo "== 1/7: system packages =="
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip

echo "== 2/7: service user =="
if ! id -u "$SERVICE_USER" &>/dev/null; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
  echo "  created user: $SERVICE_USER"
else
  echo "  user already exists: $SERVICE_USER"
fi
mkdir -p "$DEPLOY_ROOT"
chown "$SERVICE_USER":"$SERVICE_USER" "$DEPLOY_ROOT"

echo "== 3/7: application code =="
if [[ -d "$APP_DIR/.git" ]]; then
  echo "  existing checkout found -- updating to latest main"
  sudo -u "$SERVICE_USER" git -C "$APP_DIR" fetch origin
  sudo -u "$SERVICE_USER" git -C "$APP_DIR" reset --hard origin/main
else
  echo "  no existing checkout -- cloning"
  sudo -u "$SERVICE_USER" git clone --quiet "$REPO_URL" "$APP_DIR"
fi
echo "  now at: $(sudo -u "$SERVICE_USER" git -C "$APP_DIR" rev-parse --short HEAD)"

echo "== 4/7: python environment =="
if [[ ! -d "$VENV_DIR" ]]; then
  sudo -u "$SERVICE_USER" python3 -m venv "$VENV_DIR"
fi
sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --quiet --upgrade pip
sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo "== 5/7: .env =="
NEEDS_SECRETS=0
if [[ -f "$ENV_FILE" ]]; then
  echo "  $ENV_FILE already exists -- leaving it untouched"
else
  cp "$APP_DIR/.env.example" "$ENV_FILE"
  chown "$SERVICE_USER":"$SERVICE_USER" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "  created $ENV_FILE from .env.example (mode 600, owner $SERVICE_USER)"
  NEEDS_SECRETS=1
fi

echo "== 6/7: systemd unit =="
cp "$APP_DIR/deploy/telegram-approval-poller.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable --quiet "$SERVICE_NAME"

echo "== 7/7: (re)start =="
systemctl restart "$SERVICE_NAME"
sleep 2
systemctl --no-pager --lines=5 status "$SERVICE_NAME" || true

echo
echo "Done. Live logs:  journalctl -u $SERVICE_NAME -f"
if [[ "$NEEDS_SECRETS" -eq 1 ]]; then
  echo
  echo ">>> ACTION NEEDED: edit $ENV_FILE and fill in real values (nano $ENV_FILE), then:"
  echo ">>>   sudo systemctl restart $SERVICE_NAME"
  echo ">>> Until then it will keep retrying harmlessly (Restart=always) and logging"
  echo ">>> a 'DATABASE_URL is not set' error every ~15s -- that's expected, not a bug."
fi
