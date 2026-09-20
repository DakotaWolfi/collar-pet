#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -eq 0 ]]; then
    echo "Run this as your normal PM35 user, not with sudo."
    exit 1
fi

USER_NAME="$USER"
BIN_DIR="$HOME/.local/bin"
CFG_DIR="$HOME/.config/collarpet"
LIB_DIR="$HOME/.local/share/collarpet-link"
DESKTOP_DIR="$HOME/Desktop/Collar Pet"

mkdir -p "$BIN_DIR" "$CFG_DIR" "$LIB_DIR" "$DESKTOP_DIR"

echo "[1/6] Installing PM35 link dependencies..."
sudo apt update
sudo apt install -y bluez dbus python3-dbus network-manager openssh-client iproute2 iputils-ping netcat-openbsd jq

for grp in bluetooth dialout; do
    if getent group "$grp" >/dev/null 2>&1; then
        sudo usermod -aG "$grp" "$USER_NAME"
    fi
done

if [[ ! -f "$CFG_DIR/link.conf" ]]; then
cat > "$CFG_DIR/link.conf" <<'EOF'
COLLARPET_NAME="CollarPet"
COLLARPET_USER="jenna"
COLLARPET_SSH_PORT="22"
RESCUE_SSID="CollarPet-Service"
RESCUE_PSK="CP-rescue-9mQ4-vK7x"
COLLARPET_BLE_UUID="8d13f2c0-27d7-4e31-9fd1-c0a1c011a001"
HELLO_PORT="47842"
BLE_SCAN_SECONDS="8"
RESCUE_WAIT_SECONDS="60"
EOF
fi

echo "[2/6] Installing BLE discovery helper..."
cat > "$LIB_DIR/cp_ble_scan.py" <<'PY'
#!/usr/bin/env python3
import sys, time, json
import dbus

UUID = sys.argv[1].lower()
timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0

bus = dbus.SystemBus()
mgr = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
objs = mgr.GetManagedObjects()

adapter_path = None
for path, ifaces in objs.items():
    if "org.bluez.Adapter1" in ifaces:
        adapter_path = path
        break

if not adapter_path:
    print(json.dumps({"found": False, "error": "No Bluetooth adapter"}))
    sys.exit(2)

adapter = dbus.Interface(bus.get_object("org.bluez", adapter_path), "org.bluez.Adapter1")
try:
    adapter.SetDiscoveryFilter({"Transport": dbus.String("le")})
except Exception:
    pass

started = False
try:
    try:
        adapter.StartDiscovery()
        started = True
    except dbus.exceptions.DBusException as e:
        if "InProgress" not in str(e):
            raise

    deadline = time.time() + timeout
    best = None
    while time.time() < deadline:
        objs = mgr.GetManagedObjects()
        for path, ifaces in objs.items():
            dev = ifaces.get("org.bluez.Device1")
            if not dev:
                continue
            sdata = dev.get("ServiceData", {})
            match = None
            for key, value in sdata.items():
                if str(key).lower() == UUID:
                    match = [int(x) for x in value]
                    break
            if match is None or len(match) < 7:
                continue

            version = match[0]
            flags = match[1]
            ip = ".".join(str(x) for x in match[2:6])
            if ip == "0.0.0.0":
                ip = ""
            signal = None if match[6] == 255 else match[6]
            result = {
                "found": True,
                "address": str(dev.get("Address", "")),
                "name": str(dev.get("Name", dev.get("Alias", ""))),
                "rssi": int(dev.get("RSSI", -127)),
                "version": version,
                "wifi_connected": bool(flags & 0x01),
                "ssh_enabled": bool(flags & 0x02),
                "ip": ip,
                "wifi_signal": signal
            }
            if best is None or result["rssi"] > best["rssi"]:
                best = result

        if best:
            print(json.dumps(best))
            sys.exit(0)
        time.sleep(0.35)

    print(json.dumps({"found": False}))
    sys.exit(1)
finally:
    if started:
        try:
            adapter.StopDiscovery()
        except Exception:
            pass
PY
chmod +x "$LIB_DIR/cp_ble_scan.py"

echo "[3/6] Installing cp-connect..."
cat > "$BIN_DIR/cp-connect" <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
source "$HOME/.config/collarpet/link.conf"

BLE_HELPER="$HOME/.local/share/collarpet-link/cp_ble_scan.py"
SSH_KEY="$HOME/.ssh/id_ed25519_collarpet"
HOTSPOT_CON="CollarPet-Rescue-AP"
HOTSPOT_STARTED=0
OLD_WIFI_CON=""

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }

wifi_if() {
    nmcli -t -f DEVICE,TYPE,STATE device status | awk -F: '$2=="wifi"{print $1; exit}'
}

active_wifi_connection() {
    local ifc="$1"
    nmcli -t -f NAME,TYPE,DEVICE connection show --active | awk -F: -v d="$ifc" '$2=="802-11-wireless" && $3==d {print $1; exit}'
}

udp_hello() {
    local target="$1"
    python3 - "$target" "$HELLO_PORT" <<'PY'
import socket, sys, json
target=sys.argv[1]
port=int(sys.argv[2])
s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(2.0)
if target.endswith(".255") or target == "255.255.255.255":
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
s.sendto(b"COLLARPET_HELLO\n", (target, port))
try:
    data, addr=s.recvfrom(4096)
    obj=json.loads(data.decode("utf-8","replace"))
    obj["_reply_ip"]=addr[0]
    print(json.dumps(obj))
except Exception:
    sys.exit(1)
PY
}

hello_current_lan() {
    local bc
    while read -r bc; do
        [[ -n "$bc" ]] || continue
        udp_hello "$bc" && return 0
    done < <(ip -4 -o addr show scope global | awk '{for(i=1;i<=NF;i++) if($i=="brd") print $(i+1)}')
    return 1
}

probe_ssh() {
    local ip="$1"
    timeout 2 bash -c "</dev/tcp/$ip/$COLLARPET_SSH_PORT" >/dev/null 2>&1
}

open_ssh() {
    local ip="$1"
    local key_args=()
    [[ -f "$SSH_KEY" ]] && key_args=(-i "$SSH_KEY")
    say "Opening SSH to ${COLLARPET_USER}@${ip}:${COLLARPET_SSH_PORT}"
    ssh "${key_args[@]}" -o ConnectTimeout=5 -o ServerAliveInterval=10 -p "$COLLARPET_SSH_PORT" "${COLLARPET_USER}@${ip}"
}

cleanup() {
    if [[ "$HOTSPOT_STARTED" -eq 1 ]]; then
        say "Stopping rescue AP"
        sudo nmcli connection down "$HOTSPOT_CON" >/dev/null 2>&1 || true
        if [[ -n "$OLD_WIFI_CON" ]]; then
            say "Restoring Wi-Fi: $OLD_WIFI_CON"
            sudo nmcli connection up "$OLD_WIFI_CON" >/dev/null 2>&1 || true
        fi
    fi
}
trap cleanup EXIT INT TERM

say "Looking for Collar Pet over BLE"
ble_json="$("$BLE_HELPER" "$COLLARPET_BLE_UUID" "$BLE_SCAN_SECONDS" 2>/dev/null || true)"
echo "$ble_json" | jq . 2>/dev/null || echo "$ble_json"

ip="$(echo "$ble_json" | jq -r '.ip // empty' 2>/dev/null || true)"

if [[ -n "$ip" ]]; then
    say "BLE says Collar Pet is at $ip. Sending Wi-Fi hello"
    if hello="$(udp_hello "$ip" 2>/dev/null)"; then
        echo "$hello" | jq .
        actual_ip="$(echo "$hello" | jq -r '._reply_ip // .ip // empty')"
        if [[ -n "$actual_ip" ]] && probe_ssh "$actual_ip"; then
            open_ssh "$actual_ip"
            exit $?
        fi
    else
        warn "BLE was heard, but $ip is not reachable from the PM35 right now."
    fi
fi

say "Trying Collar Pet discovery on the PM35's current LAN"
if hello="$(hello_current_lan 2>/dev/null)"; then
    echo "$hello" | jq .
    ip="$(echo "$hello" | jq -r '._reply_ip // .ip // empty')"
    if [[ -n "$ip" ]] && probe_ssh "$ip"; then
        open_ssh "$ip"
        exit $?
    fi
fi

IFACE="$(wifi_if)"
if [[ -z "$IFACE" ]]; then
    echo "No Wi-Fi interface found on the PM35."
    exit 2
fi

OLD_WIFI_CON="$(active_wifi_connection "$IFACE")"

say "Normal Wi-Fi path failed. Starting rescue AP '$RESCUE_SSID' on $IFACE"
sudo nmcli connection delete "$HOTSPOT_CON" >/dev/null 2>&1 || true
sudo nmcli device wifi hotspot ifname "$IFACE" con-name "$HOTSPOT_CON" ssid "$RESCUE_SSID" password "$RESCUE_PSK"
HOTSPOT_STARTED=1

sleep 2
HOTSPOT_IP="$(ip -4 -o addr show dev "$IFACE" | awk '{print $4; exit}')"
HOTSPOT_BCAST="$(ip -4 -o addr show dev "$IFACE" | awk '{for(i=1;i<=NF;i++) if($i=="brd") print $(i+1); exit}')"
say "Rescue AP ready at ${HOTSPOT_IP:-unknown}; waiting for Collar Pet to join"

deadline=$((SECONDS + RESCUE_WAIT_SECONDS))
while (( SECONDS < deadline )); do
    if [[ -n "$HOTSPOT_BCAST" ]]; then
        if hello="$(udp_hello "$HOTSPOT_BCAST" 2>/dev/null)"; then
            echo "$hello" | jq .
            ip="$(echo "$hello" | jq -r '._reply_ip // .ip // empty')"
            if [[ -n "$ip" ]] && probe_ssh "$ip"; then
                open_ssh "$ip"
                exit $?
            fi
        fi
    fi

    ble_json="$("$BLE_HELPER" "$COLLARPET_BLE_UUID" 2 2>/dev/null || true)"
    ip="$(echo "$ble_json" | jq -r '.ip // empty' 2>/dev/null || true)"
    if [[ -n "$ip" ]] && probe_ssh "$ip"; then
        udp_hello "$ip" 2>/dev/null | jq . || true
        open_ssh "$ip"
        exit $?
    fi
    sleep 2
done

echo
echo "Collar Pet did not join the rescue AP within ${RESCUE_WAIT_SECONDS}s."
echo "On Collar Pet, check:"
echo "  systemctl status collarpet-link-agent"
exit 3
SH
chmod +x "$BIN_DIR/cp-connect"

echo "[4/6] Installing one-time SSH pairing helper..."
cat > "$BIN_DIR/cp-pair-ssh" <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
source "$HOME/.config/collarpet/link.conf"
KEY="$HOME/.ssh/id_ed25519_collarpet"

if [[ ! -f "$KEY" ]]; then
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    ssh-keygen -t ed25519 -f "$KEY" -N "" -C "PM35 Collar Pet service key"
fi

echo "This copies the PM35 Collar Pet SSH key to the pet."
read -r -p "Collar Pet IP or hostname: " HOST
ssh-copy-id -i "${KEY}.pub" -p "$COLLARPET_SSH_PORT" "${COLLARPET_USER}@${HOST}"
SH
chmod +x "$BIN_DIR/cp-pair-ssh"

echo "[5/6] Creating desktop launcher..."
cat > "$DESKTOP_DIR/00-Connect-Collar-Pet.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Connect to Collar Pet
Comment=BLE discovery, Wi-Fi hello, rescue AP fallback, then SSH
Exec=x-terminal-emulator -e bash -lc '$BIN_DIR/cp-connect; echo; read -r -p "Press Enter to close..."'
Terminal=false
Categories=Network;Development;
EOF
chmod +x "$DESKTOP_DIR/00-Connect-Collar-Pet.desktop"

echo "[6/6] Done."
echo "Reboot PM35 once:"
echo "  sudo reboot"
