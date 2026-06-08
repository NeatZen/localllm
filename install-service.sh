#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/neatai-ui.service"

if [ ! -f "$SERVICE_FILE" ]; then
  echo "Error: neatai-ui.service not found in $SCRIPT_DIR"
  exit 1
fi

echo "Installing NeatAi UI service..."
echo "Make sure you've edited neatai-ui.service with your username and paths first!"
echo ""

sudo cp "$SERVICE_FILE" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable neatai-ui
sudo systemctl start neatai-ui
sudo systemctl status neatai-ui
