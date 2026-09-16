#!/usr/bin/env bash
set -euo pipefail
sudo systemctl disable --now collarpet-fan.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/collarpet-fan.service
sudo rm -f /usr/local/sbin/collarpet-fan.py
sudo systemctl daemon-reload
echo "[OK] CollarPet fan controller removed."
