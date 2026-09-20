#!/usr/bin/env python3
import os
import shutil
import subprocess
import time
from evdev import InputDevice, list_devices, ecodes

# ---------------- USER CONFIG ----------------
# Put the real launch commands here when known.
TRAINER_CMD = os.environ.get("PT35_TRAINER_CMD", "").strip()
NORMAL_UI_CMD = os.environ.get("PT35_NORMAL_CMD", "").strip()

# Optional app-specific shortcuts. Leave blank to do nothing.
X_CMD = os.environ.get("PT35_X_CMD", "").strip()   # e.g. sensor/status screen
Y_CMD = os.environ.get("PT35_Y_CMD", "").strip()   # e.g. song recognition

SELECT_LONG_SECONDS = float(os.environ.get("PT35_SELECT_LONG", "1.5"))

# Short presses for the remaining buttons.
# These are injected as ordinary keyboard keys if wtype/xdotool is available.
KEY_ACTIONS = {
    "BTN_SOUTH": "enter",      # usually A
    "BTN_EAST": "esc",         # usually B
    "BTN_TL": "pageup",        # L
    "BTN_TR": "pagedown",      # R
}
# ------------------------------------------------

def log(msg):
    print(f"[pt35-buttons] {msg}", flush=True)

def run_command(cmd):
    if not cmd:
        return
    log(f"run: {cmd}")
    subprocess.Popen(["bash", "-lc", cmd], start_new_session=True)

def inject_key(key):
    # Prefer Wayland-friendly wtype, then xdotool for X11.
    if shutil.which("wtype"):
        subprocess.Popen(["wtype", "-k", key], start_new_session=True)
        return True
    if shutil.which("xdotool"):
        xmap = {
            "enter": "Return",
            "esc": "Escape",
            "pageup": "Page_Up",
            "pagedown": "Page_Down",
            "print": "Print",
        }
        subprocess.Popen(["xdotool", "key", xmap.get(key, key)], start_new_session=True)
        return True
    return False

def screenshot():
    # First try to trigger the desktop's normal Print Screen action.
    if inject_key("print"):
        log("Print Screen triggered")
        return

    # Fall back to common screenshot tools.
    for cmd in (["gnome-screenshot", "-i"], ["grim"]):
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, start_new_session=True)
            log(f"screenshot via {cmd[0]}")
            return
    log("No screenshot injector/tool found. Install 'wtype' (Wayland) or 'xdotool' (X11).")

def find_gamepad():
    candidates = []
    for path in list_devices():
        dev = InputDevice(path)
        name = (dev.name or "").lower()
        info = dev.info
        caps = dev.capabilities()
        keycodes = set(caps.get(ecodes.EV_KEY, []))

        # Strong match for the replacement firmware.
        if "pocketterm35" in name and any(c >= ecodes.BTN_GAMEPAD for c in keycodes):
            return dev

        # Fallback: TinyUSB replacement VID:PID and gamepad buttons.
        if info.vendor == 0xCAFE and info.product == 0x4011 and any(c >= ecodes.BTN_GAMEPAD for c in keycodes):
            candidates.append(dev)

    return candidates[0] if candidates else None

def key_name(code):
    name = ecodes.KEY.get(code, f"CODE_{code}")
    if isinstance(name, list):
        return name[0]
    return name

def main():
    dev = find_gamepad()
    if not dev:
        raise SystemExit("PT35 gamepad input device not found.")

    log(f"using {dev.path}: {dev.name}")

    pressed_at = {}
    combo_active = False
    combo_suppressed = set()

    # Linux names expected from the TinyUSB gamepad descriptor.
    SELECT_NAMES = {"BTN_SELECT", "BTN_MODE"}
    START_NAMES = {"BTN_START"}

    for event in dev.read_loop():
        if event.type != ecodes.EV_KEY:
            continue

        name = key_name(event.code)

        # Track press/release.
        if event.value == 1:
            pressed_at[name] = time.monotonic()

            # If Select+Start are held together, suppress both local actions.
            down = set(pressed_at)
            if down & SELECT_NAMES and down & START_NAMES:
                combo_active = True
                combo_suppressed |= (down & SELECT_NAMES) | (down & START_NAMES)
                log("Select+Start combo: local launch actions suppressed")

        elif event.value == 0:
            started = pressed_at.pop(name, None)
            held = (time.monotonic() - started) if started is not None else 0.0

            if name in combo_suppressed:
                combo_suppressed.discard(name)
                if not combo_suppressed:
                    combo_active = False
                continue

            if name in SELECT_NAMES:
                if held >= SELECT_LONG_SECONDS:
                    screenshot()
                else:
                    if TRAINER_CMD:
                        run_command(TRAINER_CMD)
                    else:
                        log("Select tap -> Trainer (PT35_TRAINER_CMD is not configured)")
                continue

            if name in START_NAMES:
                if NORMAL_UI_CMD:
                    run_command(NORMAL_UI_CMD)
                else:
                    log("Start tap -> Collar Pet UI (PT35_NORMAL_CMD is not configured)")
                continue

            # X/Y app shortcuts. Depending on Linux's ordering, these should
            # normally appear as BTN_NORTH / BTN_WEST with TinyUSB.
            if name == "BTN_NORTH":
                if X_CMD:
                    run_command(X_CMD)
                else:
                    log("X shortcut pressed (PT35_X_CMD is not configured)")
                continue

            if name == "BTN_WEST":
                if Y_CMD:
                    run_command(Y_CMD)
                else:
                    log("Y shortcut pressed (PT35_Y_CMD is not configured)")
                continue

            if name in KEY_ACTIONS:
                if not inject_key(KEY_ACTIONS[name]):
                    log(f"{name} -> {KEY_ACTIONS[name]} (install wtype or xdotool to inject keys)")
                continue

            # Helpful while we learn the exact Linux names produced by this firmware.
            if name.startswith("BTN_"):
                log(f"unmapped button: {name} (code {event.code}, held {held:.2f}s)")

if __name__ == "__main__":
    main()
