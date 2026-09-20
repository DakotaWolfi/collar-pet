# pocketTerm35 keyboard + gamepad firmware (Pico SDK / TinyUSB)

Composite USB device exposing **two separate HID interfaces**: a keyboard
(with an FN function layer) and a gamepad that doubles as a mouse. The OS
enumerates them as two distinct HID devices under one USB connection.

## Layout

- Matrix pins are identical to `code.py`: rows `GP16,GP10-GP15`, columns `GP0-GP9`.
- **Row 0** (D-pad, L, R, X, Y, B, A) drives the **gamepad** interface, in
  either of two modes:
  - **Gamepad mode** (default): d-pad -> hat switch, L/R/X/Y/B/A -> gamepad
    buttons.
  - **Mouse mode**: d-pad moves the cursor, A/B are
    left/right click, Y/X scroll up/down. L/R are unused.
  - **Switching modes**: hold Select+Start (row 6, col 3/col 5) together for
    4 seconds. The caps-lock LED (GP22) blinks 3x to confirm the switch, then
    restores to the host's real caps-lock state. The gamepad and mouse
    reports are multiplexed onto the same HID interface via distinct report
    IDs (`REPORT_ID_GAMEPAD` / `REPORT_ID_MOUSE` in `usb_descriptors.h`).
- **Rows 1-6** drive the **keyboard** interface in either mode. Holding FN (row 6, col 0 or 8)
  switches to `FN_MAP`: F-keys, media prev/play/next (real USB consumer
  control), lock-screen (Win+L), shifted symbols, and backlight brightness.
- `FN_MUTE` toggles GP19, `FN_BL_CONTROL_SCREEN` toggles GP21 (indicator
  pins), matching the GPIO side-effects in the original `code.py` — the
  breathing-light animation itself was not ported (cosmetic only).
- Backlight PWM (GP20) and secondary/AD PWM (GP18) keep the same 0-65535
  duty range and inverted-brightness behavior as `code.py` (NPN pulldown:
  lower duty = brighter).
- Caps Lock LED (GP22) is now driven from the host's real keyboard LED
  report instead of being toggled in software — more correct than the
  original's manual GP22 toggle.

## Build

Requires the [Raspberry Pi Pico SDK](https://github.com/raspberrypi/pico-sdk)
and its toolchain (`cmake`, `arm-none-eabi-gcc`).

```sh
export PICO_SDK_PATH=/path/to/pico-sdk   # or PICO_SDK_FETCH_FROM_GIT=1
mkdir build && cd build
cmake -DPICO_BOARD=pico ..    # or pico_w, waveshare_rp2040_zero, etc.
make -j$(nproc)
```

This produces `kbd_gamepad_fw.uf2`.

## Flash

Press BOOTSEL , press RESET, release RESET, release BOOTSEL, check dmesg/new mounted device RPI-RP2, then copy the UF2 to RPI-RP2:

```sh
cp build/kbd_gamepad_fw.uf2 /media/$USER/RPI-RP2/
```

## Notes / things to verify on real hardware

- `USB_VID`/`USB_PID` in `usb_descriptors.c` use TinyUSB's example
  `0xCafe:0x4011`, fine for personal use but get a real VID:PID (e.g.
  from [pid.codes](https://pid.codes)) before distributing this.
- Debounce is a fixed 20us settle time per column, no re-sample — bump
  `sleep_us()` in `matrix_scan()` if you see chatter on your switches.
- One-shot actions (lock screen, shifted symbols, media keys) block for
  ~15ms via `board_delay()` while sending press+release; negligible for a
  keypress but will introduce a small hitch in matrix scanning if held
  and re-triggered rapidly.
- Only `HID_KEY_L` on the FN layer at row 3 col 8 is intentionally kept
  identical to the base layer, mirroring an apparent quirk already present
  in `code.py`'s `FN_MAP` (worth double-checking against your intended
  layout).
