# PT35 Keyboard Firmware Resources

This directory contains source and release artifacts for PT35 keyboard/gamepad firmware work.

## Included

- `arrow-firmware-source/`: curated source tree for Pico SDK build
- `../Downloads/kbd_gamepad_fw.uf2`: base firmware image
- `../Downloads/kbd_gamepad_fw_arrows.uf2`: arrow-key firmware image

## Intentionally excluded from Git

The original temporary bundle included heavy/local-only content not needed for source publication:

- embedded full `pico-sdk/` clone
- local `.git/` folder from temporary build checkout
- `backup_orinigal_firmware/` runtime dump

For reproducible builds, use your own SDK checkout and set `PICO_SDK_PATH`.
