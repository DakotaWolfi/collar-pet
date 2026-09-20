#!/usr/bin/env bash
set -euo pipefail

WORK="$HOME/pt35-keyboard-arrow-build"
OUT="$HOME/Downloads/kbd_gamepad_fw_arrows.uf2"
REPO="https://github.com/black-ghost-off/pocketTerm35-keyboard-firmware.git"

echo "==> Installing build dependencies..."
sudo apt update
sudo apt install -y git cmake make gcc-arm-none-eabi libnewlib-arm-none-eabi build-essential python3

echo "==> Cloning pocketTerm35 keyboard firmware..."
rm -rf "$WORK"
git clone "$REPO" "$WORK"
cd "$WORK"

echo "==> Applying PT35 arrow-key patch..."
python3 <<'PY'
from pathlib import Path

p = Path("main.c")
s = p.read_text()

old_keyboard = """    uint8_t modifier = 0;
    uint8_t keys[6] = {0};
    int nkeys = 0;

    for (int r = 1; r < NUM_ROWS; r++) {
"""

new_keyboard = """    uint8_t modifier = 0;
    uint8_t keys[6] = {0};
    int nkeys = 0;

    // PT35 patch: in normal/gamepad mode keep the physical D-pad as
    // ordinary keyboard arrow keys. In mouse mode row 0 remains reserved
    // for pointer movement, so no arrow-key reports are generated.
    if (device_mode == DEVICE_MODE_GAMEPAD) {
        if (pressed_cur[0][0] && nkeys < 6) keys[nkeys++] = HID_KEY_ARROW_UP;
        if (pressed_cur[0][1] && nkeys < 6) keys[nkeys++] = HID_KEY_ARROW_LEFT;
        if (pressed_cur[0][2] && nkeys < 6) keys[nkeys++] = HID_KEY_ARROW_DOWN;
        if (pressed_cur[0][3] && nkeys < 6) keys[nkeys++] = HID_KEY_ARROW_RIGHT;
    }

    for (int r = 1; r < NUM_ROWS; r++) {
"""

old_gamepad = """    bool up = pressed_cur[0][0], left = pressed_cur[0][1];
    bool down = pressed_cur[0][2], right = pressed_cur[0][3];

    uint8_t hat = GAMEPAD_HAT_CENTERED;
    if (up && right) hat = GAMEPAD_HAT_UP_RIGHT;
    else if (down && right) hat = GAMEPAD_HAT_DOWN_RIGHT;
    else if (down && left) hat = GAMEPAD_HAT_DOWN_LEFT;
    else if (up && left) hat = GAMEPAD_HAT_UP_LEFT;
    else if (up) hat = GAMEPAD_HAT_UP;
    else if (down) hat = GAMEPAD_HAT_DOWN;
    else if (left) hat = GAMEPAD_HAT_LEFT;
    else if (right) hat = GAMEPAD_HAT_RIGHT;

    uint32_t buttons = 0;
"""

new_gamepad = """    // PT35 patch: the D-pad is intentionally NOT exposed as a gamepad hat
    // in normal mode. It remains a keyboard arrow pad; X/Y/A/B/L/R are
    // still independent gamepad buttons.
    uint8_t hat = GAMEPAD_HAT_CENTERED;

    uint32_t buttons = 0;
"""

if old_keyboard not in s:
    raise SystemExit("ERROR: expected process_keyboard block was not found; upstream source changed.")
if old_gamepad not in s:
    raise SystemExit("ERROR: expected process_gamepad block was not found; upstream source changed.")

s = s.replace(old_keyboard, new_keyboard, 1)
s = s.replace(old_gamepad, new_gamepad, 1)
p.write_text(s)
print("Patch applied.")
PY

echo "==> Building firmware..."
chmod +x build.sh
./build.sh

echo "==> Copying UF2 to Downloads..."
cp build/kbd_gamepad_fw.uf2 "$OUT"

echo
echo "SUCCESS"
echo "Firmware: $OUT"
sha256sum "$OUT"
echo
echo "Normal mode:"
echo "  D-pad         -> keyboard arrows"
echo "  L/R/X/Y/B/A   -> gamepad buttons"
echo "Mouse mode:"
echo "  D-pad         -> mouse movement"
echo "  A/B           -> left/right click"
echo "  X/Y           -> scroll"
