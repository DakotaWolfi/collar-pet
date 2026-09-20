#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -eq 0 ]]; then
    echo "Run this as your normal PM35 desktop user, not with sudo."
    exit 1
fi

BIN_DIR="$HOME/.local/bin"
LIB_DIR="$HOME/.local/share/collarpet-link"
CFG_DIR="$HOME/.config/collarpet"
DESKTOP_DIR="$HOME/Desktop/Collar Pet"

mkdir -p "$BIN_DIR" "$LIB_DIR" "$DESKTOP_DIR"

echo "[1/4] Installing GUI dependency..."
sudo apt update
sudo apt install -y python3-tk jq network-manager

echo "[2/4] Installing Collar Pet dashboard..."
cat > "$LIB_DIR/collarpet_dashboard.py" <<'PY'
#!/usr/bin/env python3
import os
import json
import socket
import subprocess
import threading
import tkinter as tk
from tkinter import ttk
from pathlib import Path

HOME = str(Path.home())
CFG = os.path.join(HOME, ".config/collarpet/link.conf")
BLE_HELPER = os.path.join(HOME, ".local/share/collarpet-link/cp_ble_scan.py")
CP_CONNECT = os.path.join(HOME, ".local/bin/cp-connect")

def read_conf():
    data = {}
    try:
        with open(CFG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return data

CONF = read_conf()
UUID = CONF.get("COLLARPET_BLE_UUID", "8d13f2c0-27d7-4e31-9fd1-c0a1c011a001")
SSH_PORT = int(CONF.get("COLLARPET_SSH_PORT", "22"))

BG = "#10141c"
PANEL = "#1b2230"
PANEL2 = "#222c3d"
TEXT = "#ecf2ff"
MUTED = "#94a3b8"
GREEN = "#3ddc97"
YELLOW = "#ffd166"
RED = "#ff5d73"
BLUE = "#63b3ff"
PURPLE = "#b388ff"

root = tk.Tk()
root.title("Collar Pet Link")
root.configure(bg=BG)
root.geometry("800x480")
root.minsize(640, 400)

try:
    root.attributes("-fullscreen", True)
except Exception:
    pass

title = tk.Label(root, text="COLLAR PET", fg=TEXT, bg=BG, font=("DejaVu Sans", 28, "bold"))
title.pack(pady=(14, 2))

subtitle = tk.Label(root, text="PM35 SERVICE LINK", fg=BLUE, bg=BG, font=("DejaVu Sans", 11, "bold"))
subtitle.pack(pady=(0, 12))

main = tk.Frame(root, bg=BG)
main.pack(fill="both", expand=True, padx=16)

left = tk.Frame(main, bg=PANEL, bd=0)
left.pack(side="left", fill="both", expand=True, padx=(0, 8))

right = tk.Frame(main, bg=PANEL, bd=0)
right.pack(side="right", fill="both", expand=True, padx=(8, 0))

def card_header(parent, text):
    tk.Label(parent, text=text, fg=PURPLE, bg=PANEL, font=("DejaVu Sans", 13, "bold")).pack(anchor="w", padx=16, pady=(14, 8))

card_header(left, "PM35")
card_header(right, "COLLAR PET")

pm_wifi = tk.Label(left, text="Wi-Fi: checking…", fg=MUTED, bg=PANEL, font=("DejaVu Sans", 14))
pm_wifi.pack(anchor="w", padx=16, pady=4)

pm_ip = tk.Label(left, text="IP: —", fg=MUTED, bg=PANEL, font=("DejaVu Sans", 14))
pm_ip.pack(anchor="w", padx=16, pady=4)

pm_state = tk.Label(left, text="Network state: —", fg=MUTED, bg=PANEL, font=("DejaVu Sans", 14))
pm_state.pack(anchor="w", padx=16, pady=4)

pet_ble = tk.Label(right, text="BLE: scanning…", fg=MUTED, bg=PANEL, font=("DejaVu Sans", 14))
pet_ble.pack(anchor="w", padx=16, pady=4)

pet_wifi = tk.Label(right, text="Wi-Fi: —", fg=MUTED, bg=PANEL, font=("DejaVu Sans", 14))
pet_wifi.pack(anchor="w", padx=16, pady=4)

pet_ip = tk.Label(right, text="IP: —", fg=MUTED, bg=PANEL, font=("DejaVu Sans", 14))
pet_ip.pack(anchor="w", padx=16, pady=4)

pet_ssh = tk.Label(right, text="SSH: —", fg=MUTED, bg=PANEL, font=("DejaVu Sans", 14))
pet_ssh.pack(anchor="w", padx=16, pady=4)

pet_signal = tk.Label(right, text="Signal: —", fg=MUTED, bg=PANEL, font=("DejaVu Sans", 14))
pet_signal.pack(anchor="w", padx=16, pady=4)

bottom = tk.Frame(root, bg=BG)
bottom.pack(fill="x", padx=16, pady=14)

status = tk.Label(bottom, text="Ready", fg=MUTED, bg=BG, font=("DejaVu Sans", 11))
status.pack(side="left")

def run(cmd):
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def get_pm35():
    wifi = "Disconnected"
    state = "offline"
    ip = "—"
    try:
        r = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
        for line in r.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 4 and parts[1] == "wifi":
                dev, typ, st, con = parts[:4]
                state = st
                if st == "connected":
                    wifi = con or "Connected"
                    ir = run(["ip", "-4", "-o", "addr", "show", "dev", dev])
                    for ln in ir.stdout.splitlines():
                        if " inet " in ln:
                            ip = ln.split()[3].split("/")[0]
                            break
                break
    except Exception:
        pass
    return wifi, ip, state

def port_open(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=1.0):
            return True
    except Exception:
        return False

def scan_ble():
    if not os.path.exists(BLE_HELPER):
        return {"found": False, "error": "BLE helper missing"}
    try:
        r = run(["python3", BLE_HELPER, UUID, "4"])
        if r.stdout.strip():
            return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:
        return {"found": False, "error": str(e)}
    return {"found": False}

refresh_busy = False

def refresh_worker():
    global refresh_busy
    if refresh_busy:
        return
    refresh_busy = True
    root.after(0, lambda: status.config(text="Refreshing…", fg=BLUE))

    wifi, ip, st = get_pm35()
    ble = scan_ble()

    def apply():
        global refresh_busy
        pm_wifi.config(text=f"Wi-Fi: {wifi}", fg=GREEN if st == "connected" else RED)
        pm_ip.config(text=f"IP: {ip}", fg=TEXT if ip != "—" else MUTED)
        pm_state.config(text=f"Network state: {st}", fg=TEXT)

        if ble.get("found"):
            pet_ble.config(text="BLE: ONLINE", fg=GREEN)
            connected = ble.get("wifi_connected", False)
            pet_wifi.config(text=f"Wi-Fi: {'connected' if connected else 'offline'}", fg=GREEN if connected else YELLOW)

            pip = ble.get("ip") or "—"
            pet_ip.config(text=f"IP: {pip}", fg=TEXT if pip != "—" else MUTED)

            ssh_ok = bool(pip != "—" and port_open(pip, SSH_PORT))
            pet_ssh.config(text=f"SSH: {'ready' if ssh_ok else 'not reachable'}", fg=GREEN if ssh_ok else YELLOW)

            sig = ble.get("wifi_signal")
            if sig is None:
                pet_signal.config(text="Signal: —", fg=MUTED)
            else:
                col = GREEN if sig >= 60 else YELLOW if sig >= 30 else RED
                pet_signal.config(text=f"Signal: {sig}%", fg=col)

            status.config(text="Collar Pet detected", fg=GREEN)
        else:
            pet_ble.config(text="BLE: not detected", fg=RED)
            pet_wifi.config(text="Wi-Fi: —", fg=MUTED)
            pet_ip.config(text="IP: —", fg=MUTED)
            pet_ssh.config(text="SSH: —", fg=MUTED)
            pet_signal.config(text="Signal: —", fg=MUTED)
            status.config(text="Collar Pet not seen over BLE", fg=YELLOW)

        refresh_busy = False

    root.after(0, apply)

def refresh():
    threading.Thread(target=refresh_worker, daemon=True).start()

def connect():
    status.config(text="Opening automatic Collar Pet link…", fg=BLUE)
    cmd = f'x-terminal-emulator -e bash -lc "{CP_CONNECT}; echo; read -r -p \'Press Enter to close...\'"'
    subprocess.Popen(cmd, shell=True)

def toggle_fullscreen(event=None):
    root.attributes("-fullscreen", not bool(root.attributes("-fullscreen")))

def quit_app(event=None):
    root.destroy()

btn_style = {
    "font": ("DejaVu Sans", 13, "bold"),
    "bd": 0,
    "padx": 18,
    "pady": 10,
    "cursor": "hand2"
}

tk.Button(bottom, text="CONNECT", command=connect, bg=GREEN, fg="#08120d", activebackground=GREEN, **btn_style).pack(side="right", padx=(8, 0))
tk.Button(bottom, text="REFRESH", command=refresh, bg=BLUE, fg="#08111c", activebackground=BLUE, **btn_style).pack(side="right", padx=(8, 0))
tk.Button(bottom, text="EXIT", command=quit_app, bg=PANEL2, fg=TEXT, activebackground=PANEL2, **btn_style).pack(side="right", padx=(8, 0))

root.bind("<F11>", toggle_fullscreen)
root.bind("<Escape>", quit_app)

refresh()

def auto_refresh():
    refresh()
    root.after(10000, auto_refresh)

root.after(10000, auto_refresh)
root.mainloop()
PY

chmod +x "$LIB_DIR/collarpet_dashboard.py"

echo "[3/4] Installing launcher command..."
cat > "$BIN_DIR/cp-dashboard" <<EOF
#!/usr/bin/env bash
exec python3 "$LIB_DIR/collarpet_dashboard.py"
EOF
chmod +x "$BIN_DIR/cp-dashboard"

echo "[4/4] Creating desktop shortcut..."
cat > "$DESKTOP_DIR/00-Collar-Pet-Dashboard.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Collar Pet Dashboard
Comment=Colorful PM35 Collar Pet status and connection panel
Exec=$BIN_DIR/cp-dashboard
Terminal=false
Categories=Network;Development;
EOF
chmod +x "$DESKTOP_DIR/00-Collar-Pet-Dashboard.desktop"

echo
echo "Done."
echo
echo "Launch with:"
echo "  cp-dashboard"
echo
echo "Or click:"
echo "  Desktop -> Collar Pet -> Collar Pet Dashboard"
echo
echo "ESC closes it. F11 toggles fullscreen."
