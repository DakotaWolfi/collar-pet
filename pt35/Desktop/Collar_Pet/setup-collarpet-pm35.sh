#!/usr/bin/env bash
set -Eeuo pipefail

# Collar Pet PM35 Service Terminal setup
# Target: Raspberry Pi OS Desktop on a Raspberry Pi 4B
#
# Run as your NORMAL desktop user:
#   chmod +x setup-collarpet-pm35.sh
#   ./setup-collarpet-pm35.sh
#
# Do NOT run the whole script with sudo. It will ask for sudo where needed.

trap 'echo; echo "[ERROR] Setup stopped at line $LINENO. Fix the error and run the script again; it is designed to be rerunnable."' ERR

if [[ "${EUID}" -eq 0 ]]; then
    echo "Please run this as your normal Raspberry Pi user, NOT with sudo."
    exit 1
fi

USER_NAME="${USER}"
HOME_DIR="${HOME}"
BIN_DIR="${HOME_DIR}/.local/bin"
TOOLS_DIR="${HOME_DIR}/.local/share/collarpet-tools"
CFG_DIR="${HOME_DIR}/.config/collarpet"
PROJECT_DIR="${HOME_DIR}/CollarPet"
DESKTOP_DIR="${HOME_DIR}/Desktop"
CP_DESKTOP="${DESKTOP_DIR}/Collar Pet"
PY_VENV="${TOOLS_DIR}/venv"

mkdir -p "$BIN_DIR" "$TOOLS_DIR" "$CFG_DIR" "$PROJECT_DIR" "$CP_DESKTOP"

echo "=================================================="
echo "  Collar Pet PM35 Service Terminal Setup"
echo "=================================================="
echo
echo "User:    $USER_NAME"
echo "Home:    $HOME_DIR"
echo "Project: $PROJECT_DIR"
echo

# Keep ~/.local/bin available in future shells.
if ! grep -qs 'HOME/.local/bin' "${HOME_DIR}/.profile"; then
    cat >> "${HOME_DIR}/.profile" <<'EOF'

# User-local programs (Collar Pet tools, PlatformIO, Arduino CLI)
if [ -d "$HOME/.local/bin" ]; then
    PATH="$HOME/.local/bin:$PATH"
fi
EOF
fi
export PATH="${BIN_DIR}:${PATH}"

echo "[1/10] Updating APT package metadata..."
sudo apt update

echo "[2/10] Installing development, serial, network, audio and diagnostic tools..."

# Pre-answer Wireshark's non-root capture question.
echo 'wireshark-common wireshark-common/install-setuid boolean true' | sudo debconf-set-selections

BASE_PACKAGES=(
    git git-lfs curl wget ca-certificates rsync unzip zip
    build-essential cmake ninja-build pkg-config
    python3 python3-full python3-venv python3-pip python3-dev
    minicom picocom screen
    usbutils pciutils i2c-tools
    openssh-client mosh
    nmap arp-scan avahi-utils dnsutils net-tools iproute2
    tcpdump wireshark
    ffmpeg mpv audacity imagemagick
    v4l-utils
    jq tmux htop tree file
)

sudo apt install -y "${BASE_PACKAGES[@]}"

# VS Code is available from Raspberry Pi OS repositories on supported images.
if apt-cache show code >/dev/null 2>&1; then
    echo "[3/10] Installing VS Code..."
    sudo apt install -y code
else
    echo "[3/10] 'code' is not available from this image's APT repositories; skipping VS Code."
    echo "       Everything else will still work."
fi

echo "[4/10] Setting USB/serial/network capture permissions..."
for grp in dialout plugdev i2c spi gpio wireshark; do
    if getent group "$grp" >/dev/null 2>&1; then
        sudo usermod -aG "$grp" "$USER_NAME"
    fi
done

# Official PlatformIO udev rules.
sudo curl -fsSL \
    https://raw.githubusercontent.com/platformio/platformio-core/develop/platformio/assets/system/99-platformio-udev.rules \
    -o /etc/udev/rules.d/99-platformio-udev.rules
sudo udevadm control --reload-rules
sudo udevadm trigger || true

echo "[5/10] Installing PlatformIO Core in its own environment..."
PIO_INSTALLER="${TOOLS_DIR}/get-platformio.py"
curl -fsSL \
    https://raw.githubusercontent.com/platformio/platformio-core-installer/master/get-platformio.py \
    -o "$PIO_INSTALLER"
python3 "$PIO_INSTALLER"

# PlatformIO installer normally places executables here.
if [[ -x "${HOME_DIR}/.platformio/penv/bin/platformio" ]]; then
    ln -sf "${HOME_DIR}/.platformio/penv/bin/platformio" "${BIN_DIR}/platformio"
    ln -sf "${HOME_DIR}/.platformio/penv/bin/platformio" "${BIN_DIR}/pio"
fi

echo "[6/10] Installing esptool + serial Python helpers in an isolated venv..."
python3 -m venv "$PY_VENV"
"$PY_VENV/bin/python" -m pip install --upgrade pip setuptools wheel
"$PY_VENV/bin/python" -m pip install --upgrade esptool pyserial rich

ln -sf "$PY_VENV/bin/esptool" "${BIN_DIR}/esptool"
ln -sf "$PY_VENV/bin/esptool" "${BIN_DIR}/esptool.py"

echo "[7/10] Installing Arduino CLI..."
ARDUINO_INSTALLER="${TOOLS_DIR}/arduino-install.sh"
curl -fsSL \
    https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh \
    -o "$ARDUINO_INSTALLER"
chmod +x "$ARDUINO_INSTALLER"
BINDIR="$BIN_DIR" sh "$ARDUINO_INSTALLER"

if command -v arduino-cli >/dev/null 2>&1; then
    arduino-cli config init --overwrite >/dev/null 2>&1 || true
fi

echo "[8/10] Creating Collar Pet config and helper commands..."

if [[ ! -f "${CFG_DIR}/terminal.conf" ]]; then
cat > "${CFG_DIR}/terminal.conf" <<'EOF'
# Collar Pet PM35 terminal defaults.
# Edit these whenever the hardware/network names are known.

COLLARPET_HOST="collarpet.local"
COLLARPET_USER="jenna"
COLLARPET_UART_BAUD="115200"

# Leave blank for automatic detection of ttyUSB/ttyACM devices.
COLLARPET_UART_DEVICE=""
EOF
fi

cat > "${BIN_DIR}/cp-ports" <<'EOF'
#!/usr/bin/env bash
set -u
echo "=== USB devices ==="
lsusb
echo
echo "=== Serial devices ==="
found=0
for p in /dev/ttyUSB* /dev/ttyACM* /dev/serial/by-id/*; do
    [[ -e "$p" ]] || continue
    ls -l "$p"
    found=1
done
if [[ "$found" -eq 0 ]]; then
    echo "No ttyUSB/ttyACM devices detected."
fi
echo
if command -v pio >/dev/null 2>&1; then
    echo "=== PlatformIO device list ==="
    pio device list || true
fi
EOF

cat > "${BIN_DIR}/cp-uart" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
source "$HOME/.config/collarpet/terminal.conf"

device="${1:-${COLLARPET_UART_DEVICE:-}}"
baud="${2:-${COLLARPET_UART_BAUD:-115200}}"

if [[ -z "$device" ]]; then
    for candidate in /dev/serial/by-id/* /dev/ttyUSB* /dev/ttyACM*; do
        if [[ -e "$candidate" ]]; then
            device="$candidate"
            break
        fi
    done
fi

if [[ -z "$device" || ! -e "$device" ]]; then
    echo "No serial adapter found."
    echo
    "$HOME/.local/bin/cp-ports"
    echo
    echo "Usage: cp-uart /dev/ttyUSB0 115200"
    exit 1
fi

echo "Opening $device at $baud baud."
echo "Picocom exit: Ctrl+A, then Ctrl+X"
exec picocom -b "$baud" "$device"
EOF

cat > "${BIN_DIR}/cp-ssh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
source "$HOME/.config/collarpet/terminal.conf"

host="${1:-${COLLARPET_HOST:-collarpet.local}}"
user="${2:-${COLLARPET_USER:-}}"

if [[ -n "$user" ]]; then
    exec ssh "${user}@${host}"
else
    exec ssh "$host"
fi
EOF

cat > "${BIN_DIR}/cp-net" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
source "$HOME/.config/collarpet/terminal.conf"

echo "=== This PM35 ==="
hostname
hostname -I || true
ip -brief address
echo

echo "=== Collar Pet target ==="
host="${COLLARPET_HOST:-collarpet.local}"
echo "Target: $host"
ping -c 2 "$host" || true
echo

echo "=== mDNS/Avahi devices ==="
avahi-browse -art 2>/dev/null | head -n 80 || true
echo

default_iface="$(ip route | awk '/default/ {print $5; exit}')"
cidr="$(ip -o -f inet addr show "$default_iface" 2>/dev/null | awk '{print $4; exit}')"
if [[ -n "${cidr:-}" ]]; then
    echo "=== Host discovery on $cidr ==="
    nmap -sn "$cidr" || true
fi
EOF

cat > "${BIN_DIR}/cp-i2c" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
bus="${1:-1}"

if [[ ! -e "/dev/i2c-${bus}" ]]; then
    echo "/dev/i2c-${bus} does not exist."
    echo "Available I2C devices:"
    ls -1 /dev/i2c-* 2>/dev/null || true
    exit 1
fi

echo "Scanning I2C bus ${bus}..."
i2cdetect -y "$bus"
EOF

cat > "${BIN_DIR}/cp-audio-test" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
mkdir -p "$HOME/CollarPet/tests"
out="$HOME/CollarPet/tests/audio-$(date +%Y%m%d-%H%M%S).wav"

echo "=== ALSA capture devices ==="
arecord -l || true
echo
echo "Recording 5 seconds to:"
echo "$out"
echo

# Use ALSA default capture device. If Collar Pet uses a USB/I2S device,
# select it later with arecord -D hw:X,Y.
if ! arecord -d 5 -f S16_LE -r 48000 -c 1 "$out"; then
    echo
    echo "Default capture failed. Use 'arecord -L' / 'arecord -l' to select the device."
    exit 1
fi

echo "Playing recording..."
aplay "$out"
EOF

cat > "${BIN_DIR}/cp-camera-test" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

echo "=== Video devices ==="
v4l2-ctl --list-devices || true
echo

dev="${1:-/dev/video0}"
if [[ ! -e "$dev" ]]; then
    echo "$dev not found."
    exit 1
fi

echo "Opening $dev. Press q to exit."
exec mpv --profile=low-latency --untimed "av://v4l2:${dev}"
EOF

cat > "${BIN_DIR}/cp-esp-info" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

device="${1:-}"
if [[ -z "$device" ]]; then
    for candidate in /dev/serial/by-id/* /dev/ttyUSB* /dev/ttyACM*; do
        [[ -e "$candidate" ]] || continue
        device="$candidate"
        break
    done
fi

if [[ -z "$device" ]]; then
    echo "No ESP/serial device detected."
    exit 1
fi

echo "Querying ESP on $device ..."
exec "$HOME/.local/bin/esptool" --port "$device" chip-id
EOF

cat > "${BIN_DIR}/cp-flash-esp" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage:"
    echo "  cp-flash-esp /dev/ttyUSB0 firmware.bin [address]"
    echo
    echo "Default address: 0x0"
    echo
    echo "NOTE: use the correct flash address for the firmware image you built."
    exit 1
fi

device="$1"
firmware="$2"
address="${3:-0x0}"

[[ -e "$device" ]] || { echo "Serial device not found: $device"; exit 1; }
[[ -f "$firmware" ]] || { echo "Firmware file not found: $firmware"; exit 1; }

echo "About to flash:"
echo "  Port:    $device"
echo "  Image:   $firmware"
echo "  Address: $address"
echo
read -r -p "Continue? [y/N] " answer
[[ "$answer" =~ ^[Yy]$ ]] || exit 0

exec "$HOME/.local/bin/esptool" --port "$device" write-flash "$address" "$firmware"
EOF

chmod +x \
    "${BIN_DIR}/cp-ports" \
    "${BIN_DIR}/cp-uart" \
    "${BIN_DIR}/cp-ssh" \
    "${BIN_DIR}/cp-net" \
    "${BIN_DIR}/cp-i2c" \
    "${BIN_DIR}/cp-audio-test" \
    "${BIN_DIR}/cp-camera-test" \
    "${BIN_DIR}/cp-esp-info" \
    "${BIN_DIR}/cp-flash-esp"

echo "[9/10] Creating desktop launchers..."

make_launcher() {
    local filename="$1"
    local name="$2"
    local comment="$3"
    local command="$4"
    cat > "${CP_DESKTOP}/${filename}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=${name}
Comment=${comment}
Exec=x-terminal-emulator -e bash -lc '${command}; echo; read -r -p "Press Enter to close..."'
Terminal=false
Categories=Development;
EOF
    chmod +x "${CP_DESKTOP}/${filename}.desktop"
}

make_launcher "01-Ports" "Collar Pet - USB & Serial" "List connected USB and serial devices" 'cp-ports'
make_launcher "02-UART" "Collar Pet - UART Console" "Open detected serial adapter at configured baud rate" 'cp-uart'
make_launcher "03-SSH" "Collar Pet - SSH" "SSH to the Collar Pet SBC" 'cp-ssh'
make_launcher "04-Network" "Collar Pet - Network Scan" "Ping, mDNS and LAN discovery" 'cp-net'
make_launcher "05-I2C" "Collar Pet - I2C Scan" "Scan local Pi I2C bus 1" 'cp-i2c 1'
make_launcher "06-ESP-Info" "Collar Pet - ESP Info" "Read chip information from connected ESP" 'cp-esp-info'
make_launcher "07-Audio" "Collar Pet - Audio Test" "Record and play a five-second audio test" 'cp-audio-test'
make_launcher "08-Camera" "Collar Pet - Camera Preview" "Open /dev/video0 with low latency" 'cp-camera-test'

if command -v code >/dev/null 2>&1; then
    cat > "${CP_DESKTOP}/09-VS-Code.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Collar Pet - VS Code
Comment=Open the Collar Pet project directory
Exec=code "${PROJECT_DIR}"
Terminal=false
Categories=Development;
EOF
    chmod +x "${CP_DESKTOP}/09-VS-Code.desktop"
fi

cat > "${CP_DESKTOP}/10-Config.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Collar Pet - Terminal Config
Comment=Edit Collar Pet hostname, SSH username and UART settings
Exec=sh -c 'if command -v code >/dev/null 2>&1; then code "${CFG_DIR}/terminal.conf"; else x-terminal-emulator -e nano "${CFG_DIR}/terminal.conf"; fi'
Terminal=false
Categories=Development;
EOF
chmod +x "${CP_DESKTOP}/10-Config.desktop"

cat > "${PROJECT_DIR}/PM35-README.txt" <<'EOF'
COLLAR PET PM35 SERVICE TERMINAL
================================

Useful terminal commands:

  cp-ports
      List USB and serial devices.

  cp-uart [device] [baud]
      Serial console. If no device is supplied it tries ttyUSB/ttyACM automatically.
      Example: cp-uart /dev/ttyUSB0 115200

  cp-ssh [host] [user]
      SSH to Collar Pet using ~/.config/collarpet/terminal.conf defaults.

  cp-net
      Show PM35 network details, ping Collar Pet, browse mDNS and scan the local LAN.

  cp-i2c [bus]
      I2C scan of the PM35 itself. Default bus: 1.

  cp-esp-info [device]
      Ask a connected ESP chip for identification information.

  cp-flash-esp DEVICE FIRMWARE.bin [ADDRESS]
      Flash an ESP image after an interactive confirmation.
      The image address is firmware/build dependent; default is 0x0.

  cp-audio-test
      Record 5 seconds from the default ALSA capture device and play it back.

  cp-camera-test [device]
      Low-latency preview of /dev/video0 or another V4L2 camera.

Other installed tools:
  git / git-lfs
  VS Code (if available from Raspberry Pi OS repository)
  PlatformIO: pio / platformio
  Arduino CLI: arduino-cli
  Espressif esptool: esptool
  minicom / picocom / screen
  ssh / mosh
  nmap / arp-scan / avahi
  Wireshark / tcpdump
  ffmpeg / mpv / Audacity
  ImageMagick
  v4l2-ctl
  i2c-tools
  jq / tmux / htop

Config:
  ~/.config/collarpet/terminal.conf

Projects:
  ~/CollarPet

IMPORTANT:
After the installer adds you to hardware-access groups, REBOOT once.
EOF

# Add a convenient symlink to the readme on the tool desktop.
ln -sf "${PROJECT_DIR}/PM35-README.txt" "${CP_DESKTOP}/README.txt"

echo "[10/10] Basic verification..."
echo
printf "%-18s %s\n" "Git:" "$(git --version 2>/dev/null || echo MISSING)"
printf "%-18s %s\n" "Python:" "$(python3 --version 2>/dev/null || echo MISSING)"
printf "%-18s %s\n" "PlatformIO:" "$(pio --version 2>/dev/null || echo 'restart shell / check install')"
printf "%-18s %s\n" "Arduino CLI:" "$(arduino-cli version 2>/dev/null || echo MISSING)"
printf "%-18s %s\n" "esptool:" "$(esptool version 2>/dev/null | head -n1 || echo MISSING)"
printf "%-18s %s\n" "FFmpeg:" "$(ffmpeg -version 2>/dev/null | head -n1 || echo MISSING)"
printf "%-18s %s\n" "ImageMagick:" "$(magick -version 2>/dev/null | head -n1 || convert -version 2>/dev/null | head -n1 || echo MISSING)"
printf "%-18s %s\n" "Wireshark:" "$(wireshark --version 2>/dev/null | head -n1 || echo MISSING)"
echo

echo "=================================================="
echo "Setup complete."
echo
echo "Desktop tools:  ${CP_DESKTOP}"
echo "Project folder: ${PROJECT_DIR}"
echo "Config file:    ${CFG_DIR}/terminal.conf"
echo
echo "REBOOT ONCE before using USB serial/Wireshark so"
echo "the new group memberships take effect:"
echo
echo "    sudo reboot"
echo
echo "After reboot, try:"
echo "    cp-ports"
echo "    cp-uart"
echo "    cp-net"
echo "=================================================="
