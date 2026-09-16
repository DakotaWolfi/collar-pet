#!/usr/bin/env python3
import glob
import time
from pathlib import Path

POLL_SECONDS = 0.5

# Existing board pwm-fan states:
# 0 -> 0
# 1 -> 50
# 2 -> 102
# 3 -> 170
# 4 -> 255
#
# Wearable-oriented temperature curve.
UP_THRESHOLDS = [
    (10.0, 1),
    (35.0, 2),
    (40.0, 3),
    (45.0, 4),
]

# Down thresholds provide hysteresis.
DOWN_THRESHOLDS = {
    4: 43.0,
    3: 38.0,
    2: 33.0,
    1: 8.0,
}

THERMAL_NAMES = {
    "cpub_thermal_zone",
    "cpul_thermal_zone",
    "ddr_thermal_zone",
    "npu_thermal_zone",
    "gpu_thermal_zone",
}

START_KICK_SECONDS = 0.8
START_KICK_STATE = 4


def find_zone_paths():
    zones = {}
    for p in glob.glob("/sys/class/thermal/thermal_zone*"):
        try:
            name = Path(p, "type").read_text().strip()
        except OSError:
            continue
        if name in THERMAL_NAMES:
            zones[name] = Path(p)
    return zones


def find_pwm_fan():
    for p in glob.glob("/sys/class/thermal/cooling_device*"):
        try:
            if Path(p, "type").read_text().strip() == "pwm-fan":
                return Path(p)
        except OSError:
            pass
    raise RuntimeError("pwm-fan cooling device not found")


def read_temp_c(zone):
    return int((zone / "temp").read_text().strip()) / 1000.0


def choose_up_state(temp_c):
    state = 0
    for threshold, candidate in UP_THRESHOLDS:
        if temp_c >= threshold:
            state = candidate
    return state


def apply_hysteresis(temp_c, current):
    wanted = choose_up_state(temp_c)
    if wanted >= current:
        return wanted
    state = current
    while state > wanted:
        down = DOWN_THRESHOLDS.get(state)
        if down is None or temp_c >= down:
            break
        state -= 1
    return state


def write_state(cdev, state):
    (cdev / "cur_state").write_text(f"{state}\n")


def main():
    zones = find_zone_paths()
    print("[fan] sensors:", ", ".join(sorted(zones)))
    missing = THERMAL_NAMES - set(zones)
    if missing:
        print("[fan] missing sensors:", ", ".join(sorted(missing)))

    cdev = find_pwm_fan()
    max_state = int((cdev / "max_state").read_text().strip())
    print(f"[fan] cooling device={cdev} max_state={max_state}")

    current = 0
    fan_running = False

    while True:
        temps = {}
        for name, zone in zones.items():
            try:
                temps[name] = read_temp_c(zone)
            except OSError:
                pass

        if not temps:
            hottest_name = "NO SENSOR"
            hottest = 999.0
            wanted = max_state
        else:
            hottest_name, hottest = max(temps.items(), key=lambda item: item[1])
            wanted = min(apply_hysteresis(hottest, current), max_state)

        if wanted > 0 and not fan_running:
            try:
                write_state(cdev, min(START_KICK_STATE, max_state))
                time.sleep(START_KICK_SECONDS)
            except OSError as exc:
                print(f"[fan] start-kick failed: {exc}")

        try:
            write_state(cdev, wanted)
            fan_running = wanted > 0
            if wanted != current:
                print(f"[fan] {hottest_name}={hottest:.1f}C state {current}->{wanted}")
            current = wanted
        except OSError as exc:
            print(f"[fan] state write failed: {exc}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
