#!/usr/bin/env bash
set -Eeuo pipefail

[[ "${1:-}" == "collarpet" ]] || {
  echo "Usage: sudo bash update-collarpet-shared-ble-scanfix.sh collarpet [--rollback]"
  exit 2
}

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run with sudo."
  exit 2
fi

TARGET=/home/jenna/collarpet/collarpet.py
UNIT=collarpet.service
PY=/home/jenna/mindtest/bin/python
BACKUP_DIR=/var/backups/collarpet-shared-ble-scanfix
MARKER="# COLLARPET_SHARED_BLE_SCANFIX_V1"

mkdir -p "$BACKUP_DIR"

latest_backup() {
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'collarpet.py.*.bak' -printf '%T@ %p\n' 2>/dev/null |
    sort -nr | head -n1 | cut -d' ' -f2-
}

if [[ "${2:-}" == "--rollback" ]]; then
  BAK="$(latest_backup || true)"
  [[ -n "$BAK" && -f "$BAK" ]] || { echo "No rollback backup found."; exit 1; }
  echo "Restoring $BAK"
  systemctl stop "$UNIT" || true
  cp -a "$BAK" "$TARGET"
  "$PY" -m py_compile "$TARGET"
  systemctl start "$UNIT"
  sleep 3
  systemctl is-active --quiet "$UNIT"
  echo "Rollback complete."
  exit 0
fi

[[ -s "$TARGET" ]] || { echo "Refusing to patch missing/empty $TARGET"; exit 1; }

STAMP="$(date +%Y%m%d-%H%M%S)"
BAK="$BACKUP_DIR/collarpet.py.$STAMP.bak"
cp -a "$TARGET" "$BAK"
echo "Backup: $BAK"

TMP="$(mktemp)"
cp "$TARGET" "$TMP"
trap 'rm -f "$TMP"' EXIT

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8")

marker = "# COLLARPET_SHARED_BLE_SCANFIX_V1"
if marker in s:
    print("Shared BLE scan fix already present.")
    raise SystemExit(0)

old = '''        try:
            print(f"[REMOTE] reserving BLE adapter for {remote.address or getattr(device, 'address', '?')}...")
            ble_pause_requested.set()
            try:
                await asyncio.wait_for(ble_scanner_paused.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                print("[REMOTE] scanner pause timed out; trying connection anyway")

            # Give BlueZ a brief moment to finish StopDiscovery before Connect().
            await asyncio.sleep(0.25)
            print(f"[REMOTE] connecting {remote.address or getattr(device, 'address', '?')}...")
'''

new = '''        try:
            # COLLARPET_SHARED_BLE_SCANFIX_V1
            # With the shared BLE broker, keep our environmental scan subscription
            # alive. The broker itself briefly stops the physical BlueZ scan only
            # around Connect(), then resumes it while preserving subscribers.
            if not COLLARPET_SHARED_BLE:
                print(f"[REMOTE] reserving BLE adapter for {remote.address or getattr(device, 'address', '?')}...")
                ble_pause_requested.set()
                try:
                    await asyncio.wait_for(ble_scanner_paused.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    print("[REMOTE] scanner pause timed out; trying connection anyway")

                # Direct-BLE fallback still needs the old StopDiscovery settling delay.
                await asyncio.sleep(0.25)
            else:
                print("[REMOTE] shared BLE broker active; environmental scan remains subscribed")

            print(f"[REMOTE] connecting {remote.address or getattr(device, 'address', '?')}...")
'''

if old not in s:
    raise SystemExit("Expected remote BLE reservation block not found; no changes made.")

s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
print("Patched remote connection path to preserve broker scan subscription.")
PY

echo "Syntax checking candidate..."
"$PY" -m py_compile "$TMP"

WAS_ACTIVE=0
if systemctl is-active --quiet "$UNIT"; then WAS_ACTIVE=1; fi

systemctl stop "$UNIT" || true
install -o "$(stat -c %u "$TARGET")" -g "$(stat -c %g "$TARGET")" -m "$(stat -c %a "$TARGET")" "$TMP" "$TARGET"
"$PY" -m py_compile "$TARGET"

if [[ "$WAS_ACTIVE" -eq 1 ]]; then
  systemctl start "$UNIT"
  sleep 4
  if ! systemctl is-active --quiet "$UNIT"; then
    echo "CollarPet failed after patch; rolling back."
    systemctl stop "$UNIT" || true
    cp -a "$BAK" "$TARGET"
    "$PY" -m py_compile "$TARGET"
    systemctl start "$UNIT" || true
    exit 1
  fi
fi

echo
echo "Shared BLE scan fix installed."
echo "The remote may still cause a brief physical scan interruption during Connect(),"
echo "but CollarPet remains subscribed and scanning resumes immediately afterward."
echo
echo "Watch:"
echo "  tail -f /home/jenna/collarpet/logs/collarpet-console.log"
echo
echo "Rollback:"
echo "  sudo bash $0 collarpet --rollback"
