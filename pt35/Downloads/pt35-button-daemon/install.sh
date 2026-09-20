#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing dependencies..."
sudo apt update
sudo apt install -y python3-evdev

# Install a key injector when available.
if command -v apt >/dev/null 2>&1; then
    sudo apt install -y wtype 2>/dev/null || sudo apt install -y xdotool
fi

echo "Giving $USER access to input devices..."
sudo usermod -aG input "$USER"

mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"
install -m 0755 "$SCRIPT_DIR/pt35-buttons.py" "$HOME/.local/bin/pt35-buttons.py"
install -m 0644 "$SCRIPT_DIR/pt35-buttons.service" "$HOME/.config/systemd/user/pt35-buttons.service"

systemctl --user daemon-reload
systemctl --user enable pt35-buttons.service

echo
echo "Installed."
echo "IMPORTANT: log out/in once (or reboot) so the new 'input' group membership applies."
echo "Then start with:"
echo "  systemctl --user start pt35-buttons.service"
echo "Watch button events with:"
echo "  journalctl --user -u pt35-buttons.service -f"
echo
echo "Configure launch commands by editing:"
echo "  ~/.config/systemd/user/pt35-buttons.service"
