#!/usr/bin/env bash
# =============================================================================
# setup_autostart.sh
# Sets up a systemd service so that app.py runs automatically on boot.
#
# Usage:
#   chmod +x setup_autostart.sh
#   sudo ./setup_autostart.sh
#
# To remove the service later:
#   sudo systemctl disable vibration-monitor
#   sudo systemctl stop vibration-monitor
#   sudo rm /etc/systemd/system/vibration-monitor.service
#   sudo systemctl daemon-reload
# =============================================================================

set -e

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
SERVICE_NAME="vibration-monitor"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${SCRIPT_DIR}/.venv/bin/python"
APP_SCRIPT="${SCRIPT_DIR}/app.py"

# Detect the user who called sudo (or fall back to current user)
RUN_USER="${SUDO_USER:-$(whoami)}"
RUN_GROUP="$(id -gn "$RUN_USER")"

# --------------------------------------------------------------------------
# Pre-flight checks
# --------------------------------------------------------------------------
echo "=============================================="
echo "  Engine Vibration Monitor — Autostart Setup"
echo "=============================================="
echo ""

# Must be run as root
if [[ "$EUID" -ne 0 ]]; then
    echo "[ERROR] Please run this script with sudo:"
    echo "        sudo $0"
    exit 1
fi

if [[ ! -f "$PYTHON_BIN" ]]; then
    echo "[ERROR] Virtual environment not found at: $PYTHON_BIN"
    echo "        Please create it first:"
    echo "          python3 -m venv .venv"
    echo "          .venv/bin/pip install -r requirements.txt"
    exit 1
fi

if [[ ! -f "$APP_SCRIPT" ]]; then
    echo "[ERROR] app.py not found at: $APP_SCRIPT"
    exit 1
fi

# --------------------------------------------------------------------------
# Create the systemd service unit file
# --------------------------------------------------------------------------
echo "[1/4] Writing systemd service file to ${SERVICE_FILE} ..."

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Engine Vibration Monitor Dashboard
After=network.target
Wants=network.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${PYTHON_BIN} ${APP_SCRIPT}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

# Allow access to serial ports (Witmotion / Pixhawk)
SupplementaryGroups=dialout

[Install]
WantedBy=multi-user.target
EOF

echo "    Done."

# --------------------------------------------------------------------------
# Reload systemd, enable and start the service
# --------------------------------------------------------------------------
echo "[2/4] Reloading systemd daemon ..."
systemctl daemon-reload

echo "[3/4] Enabling ${SERVICE_NAME} service (auto-start on boot) ..."
systemctl enable "${SERVICE_NAME}"

echo "[4/4] Starting ${SERVICE_NAME} service now ..."
systemctl start "${SERVICE_NAME}"

# --------------------------------------------------------------------------
# Status summary
# --------------------------------------------------------------------------
echo ""
echo "=============================================="
echo "  Setup complete!"
echo "=============================================="
echo ""
echo "  Service name : ${SERVICE_NAME}"
echo "  Running as   : ${RUN_USER}:${RUN_GROUP}"
echo "  App path     : ${APP_SCRIPT}"
echo "  Dashboard URL: http://localhost:7777"
echo ""
echo "  Useful commands:"
echo "    Check status : sudo systemctl status ${SERVICE_NAME}"
echo "    View logs    : sudo journalctl -u ${SERVICE_NAME} -f"
echo "    Stop service : sudo systemctl stop ${SERVICE_NAME}"
echo "    Disable boot : sudo systemctl disable ${SERVICE_NAME}"
echo ""

# Show live status
systemctl status "${SERVICE_NAME}" --no-pager || true
