#!/usr/bin/env bash
# Build kbd_gamepad_fw.uf2 for the Raspberry Pi Pico (RP2040).
#
# The "tusb.h: No such file" / "bsp/board.h: No such file" errors happen
# when PICO_SDK_PATH points at a pico-sdk checkout whose submodules
# (lib/tinyusb, lib/cyw43-driver, ...) were never pulled in.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
PICO_BOARD="${PICO_BOARD:-pico}"

# Default SDK location: reuse $PICO_SDK_PATH if set, else vendor one next
# to this project.
DEFAULT_SDK_DIR="${SCRIPT_DIR}/pico-sdk"
PICO_SDK_PATH="${PICO_SDK_PATH:-$DEFAULT_SDK_DIR}"

if [ ! -d "${PICO_SDK_PATH}" ]; then
    echo "==> PICO_SDK_PATH (${PICO_SDK_PATH}) not found, cloning pico-sdk..."
    git clone -b master https://github.com/raspberrypi/pico-sdk.git "${PICO_SDK_PATH}"
fi

if [ ! -f "${PICO_SDK_PATH}/pico_sdk_init.cmake" ]; then
    echo "Error: '${PICO_SDK_PATH}' does not look like a pico-sdk checkout" >&2
    exit 1
fi

echo "==> Ensuring pico-sdk submodules (tinyusb, etc.) are present..."
git -C "${PICO_SDK_PATH}" submodule update --init --recursive

export PICO_SDK_PATH

mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

echo "==> Configuring (PICO_BOARD=${PICO_BOARD})..."
cmake -DPICO_BOARD="${PICO_BOARD}" ..

echo "==> Building..."
make -j"$(nproc)"

echo "==> Done: ${BUILD_DIR}/kbd_gamepad_fw.uf2"
