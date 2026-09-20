#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Run this on the PT35 as jenna, without sudo."
  exit 1
fi

APP="$HOME/.local/bin/cp-petmind-training"
[[ -x "$APP" ]] || {
  echo "Missing $APP"
  echo "Install the PetMind training UI first."
  exit 1
}

DESKTOP="$HOME/Desktop"
if command -v xdg-user-dir >/dev/null 2>&1; then
  d="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
  [[ -n "$d" ]] && DESKTOP="$d"
fi

APPLICATIONS="$HOME/.local/share/applications"
ICON="$HOME/.local/share/collarpet-link/collarpet-icon.png"

mkdir -p "$DESKTOP" "$APPLICATIONS"

quote_exec() {
  local v="$1"
  v="${v//\\/\\\\}"
  v="${v//\"/\\\"}"
  v="${v//\$/\\\$}"
  v="${v//\`/\\\`}"
  v="${v//%/%%}"
  printf '"%s"' "$v"
}

ENTRY="[Desktop Entry]
Type=Application
Version=1.0
Name=PetMind Training
Comment=CollarPet real-world PetMind training recorder
Exec=$(quote_exec "$APP")
Terminal=false
StartupNotify=false
Categories=Utility;Development;
"

if [[ -f "$ICON" ]]; then
  ENTRY+="Icon=$ICON
"
fi

printf '%s' "$ENTRY" > "$APPLICATIONS/petmind-training.desktop"
printf '%s' "$ENTRY" > "$DESKTOP/PetMind-Training.desktop"

chmod +x "$DESKTOP/PetMind-Training.desktop"
chmod 0644 "$APPLICATIONS/petmind-training.desktop"

# Mark trusted where supported by the desktop environment.
if command -v gio >/dev/null 2>&1; then
  gio set "$DESKTOP/PetMind-Training.desktop" metadata::trusted true >/dev/null 2>&1 || true
fi

echo "Created:"
echo "  $DESKTOP/PetMind-Training.desktop"
echo "  $APPLICATIONS/petmind-training.desktop"
echo
echo "You should now have a 'PetMind Training' icon on the PT35 desktop."
