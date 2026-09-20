#!/usr/bin/env bash
set -euo pipefail

FILE="$HOME/.local/bin/pt35-buttons.py"
cp "$FILE" "$FILE.before_numeric_fix"

python3 <<'PY'
from pathlib import Path

p = Path.home() / ".local/bin/pt35-buttons.py"
s = p.read_text()

# Replace the name-based handling loop with code-based handling.
start = s.index("def key_name(code):")
end = s.index("\ndef main():", start)

replacement = r'''# Exact Linux event codes measured on this PT35 with evtest.
BTN_A      = 304  # BTN_SOUTH
BTN_B      = 305  # BTN_EAST
BTN_X      = 307  # BTN_NORTH
BTN_Y      = 308  # BTN_WEST
BTN_L      = 310  # BTN_TL
BTN_R      = 311  # BTN_TR
BTN_SELECT = 314
BTN_START  = 315

BUTTON_LABELS = {
    BTN_A: "A",
    BTN_B: "B",
    BTN_X: "X",
    BTN_Y: "Y",
    BTN_L: "L",
    BTN_R: "R",
    BTN_SELECT: "SELECT",
    BTN_START: "START",
}

'''
s = s[:start] + replacement + s[end+1:]

main_start = s.index("def main():")
if 'if __name__ == "__main__":' not in s:
    raise SystemExit("Could not find end of main()")
main_end = s.index('if __name__ == "__main__":', main_start)

new_main = r'''def main():
    dev = find_gamepad()
    if not dev:
        raise SystemExit("PT35 gamepad input device not found.")

    log(f"using {dev.path}: {dev.name}")
    log("numeric button map active: A=304 B=305 X=307 Y=308 L=310 R=311 Select=314 Start=315")

    pressed_at = {}
    combo_suppressed = set()

    for event in dev.read_loop():
        if event.type != ecodes.EV_KEY:
            continue
        if event.code not in BUTTON_LABELS:
            continue
        if event.value not in (0, 1):
            continue

        code = event.code
        label = BUTTON_LABELS[code]

        if event.value == 1:
            pressed_at[code] = time.monotonic()
            log(f"{label} down")

            if BTN_SELECT in pressed_at and BTN_START in pressed_at:
                combo_suppressed.update((BTN_SELECT, BTN_START))
                log("Select+Start combo: local launch actions suppressed")
            continue

        started = pressed_at.pop(code, None)
        held = (time.monotonic() - started) if started is not None else 0.0
        log(f"{label} up ({held:.2f}s)")

        if code in combo_suppressed:
            combo_suppressed.discard(code)
            continue

        if code == BTN_SELECT:
            if held >= SELECT_LONG_SECONDS:
                screenshot()
            elif TRAINER_CMD:
                run_command(TRAINER_CMD)
            else:
                log("Select tap -> Trainer (PT35_TRAINER_CMD is not configured)")
            continue

        if code == BTN_START:
            if NORMAL_UI_CMD:
                run_command(NORMAL_UI_CMD)
            else:
                log("Start tap -> Collar Pet UI (PT35_NORMAL_CMD is not configured)")
            continue

        if code == BTN_X:
            if X_CMD:
                run_command(X_CMD)
            else:
                log("X shortcut pressed (PT35_X_CMD is not configured)")
            continue

        if code == BTN_Y:
            if Y_CMD:
                run_command(Y_CMD)
            else:
                log("Y shortcut pressed (PT35_Y_CMD is not configured)")
            continue

        simple = {
            BTN_A: "enter",
            BTN_B: "esc",
            BTN_L: "pageup",
            BTN_R: "pagedown",
        }
        if code in simple:
            if not inject_key(simple[code]):
                log(f"{label} -> {simple[code]} (no key injector available)")

'''
s = s[:main_start] + new_main + s[main_end:]
p.write_text(s)
print("Patched", p)
PY

systemctl --user daemon-reload
systemctl --user restart pt35-buttons.service

echo
echo "Numeric button-code fix installed."
echo "Backup: $FILE.before_numeric_fix"
echo
echo "For a direct visible test run:"
echo "  systemctl --user stop pt35-buttons.service"
echo "  PT35_TRAINER_CMD=/home/jenna/.local/bin/cp-petmind-training PT35_NORMAL_CMD=/home/jenna/.local/bin/cp-dashboard python3 ~/.local/bin/pt35-buttons.py"
