#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

sudo install -m 0755 "$HERE/collarpet-fan.py" /usr/local/sbin/collarpet-fan.py
sudo install -m 0644 "$HERE/collarpet-fan.service" /etc/systemd/system/collarpet-fan.service
sudo systemctl daemon-reload
sudo systemctl enable --now collarpet-fan.service

echo
echo "[OK] CollarPet fan controller installed and started."
echo
echo "Live log:"
echo "  journalctl -fu collarpet-fan.service"
