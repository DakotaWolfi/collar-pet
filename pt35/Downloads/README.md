# PT35 Downloads Bundle (Curated)

This folder mirrors the useful PT35 files from `temp resurces/current/pt35/Downloads`.

## Keyboard/Gamepad firmware

- `kbd_gamepad_fw.uf2`: base firmware image
- `kbd_gamepad_fw_arrows.uf2`: arrow-key variant image
- `build_pt35_arrow_firmware.sh`: rebuilds arrow-key variant from upstream source and applies patch

## Button daemon

- `pt35-buttons.py`: user-space button daemon
- `pt35-buttons.service`: systemd user unit
- `install.sh`: installs daemon and dependencies
- `pt35-button-daemon/`: same daemon package as a subfolder
- `pt35-button-daemon.zip`: packaged daemon archive

## Patch helpers

- `fix_pt35_gui_launch.sh`: patches daemon to launch GUI commands with session environment
- `fix_pt35_numeric_buttons.sh`: switches mapping to explicit Linux numeric event codes

## Network/link installer

- `install-pm35-collarpet-link.sh`: installs PM35 link tools and `link.conf` defaults

## Device target

These files are intended for PT35/PM35 host use, not for the Orange Pi runtime.
