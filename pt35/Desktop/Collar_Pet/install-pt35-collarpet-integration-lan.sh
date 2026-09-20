#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -eq 0 ]]; then
    echo "Run on the PT35 as normal user jenna, NOT with sudo."
    exit 1
fi

sudo apt update
sudo apt install -y python3-tk python3-pil python3-pil.imagetk network-manager openssh-client curl

BASE="$HOME/.local/share/collarpet-link"
BIN="$HOME/.local/bin"
CFGDIR="$HOME/.config/collarpet"
CFG="$CFGDIR/link.conf"
mkdir -p "$BASE" "$BIN" "$CFGDIR" "$HOME/Desktop" "$HOME/.ssh"

cat > "$CFG" <<'EOF'
COLLARPET_NAME="CollarPet"
COLLARPET_USER="jenna"
COLLARPET_SSH_PORT="22"
HELLO_PORT="47842"
HTTP_PORT="47843"
RESCUE_SSID="CollarPet-Service"
RESCUE_PSK="CP-rescue-9mQ4-vK7x"
RESCUE_CONNECTION_NAME="CollarPet-Service"
DISCOVERY_SECONDS="3"
RESCUE_WAIT_SECONDS="60"
EOF

cat > "$BASE/discover.py" <<'PY'
#!/usr/bin/env python3
import json, socket, sys, time
port=int(sys.argv[1]) if len(sys.argv)>1 else 47842
timeout=float(sys.argv[2]) if len(sys.argv)>2 else 3.0
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.settimeout(0.35)
deadline=time.time()+timeout
next_send=0
while time.time()<deadline:
    if time.time()>=next_send:
        try: s.sendto(b"COLLARPET_HELLO v4",("255.255.255.255",port))
        except Exception: pass
        next_send=time.time()+0.6
    try:
        data,addr=s.recvfrom(8192)
        obj=json.loads(data.decode(errors="replace"))
        if isinstance(obj,dict) and obj.get("name")=="CollarPet":
            obj["_source_ip"]=addr[0]
            print(json.dumps(obj))
            sys.exit(0)
    except socket.timeout:
        pass
    except Exception:
        pass
sys.exit(1)
PY
chmod 755 "$BASE/discover.py"

cat > "$BIN/cp-discover" <<'SH'
#!/usr/bin/env bash
set -e
source "$HOME/.config/collarpet/link.conf"
exec python3 "$HOME/.local/share/collarpet-link/discover.py" "$HELLO_PORT" "${DISCOVERY_SECONDS:-3}"
SH
chmod 755 "$BIN/cp-discover"

cat > "$BIN/cp-pair-ssh" <<'SH'
#!/usr/bin/env bash
set -e
source "$HOME/.config/collarpet/link.conf"
KEY="$HOME/.ssh/id_ed25519_collarpet"
if [[ ! -f "$KEY" ]]; then
    ssh-keygen -t ed25519 -f "$KEY" -N "" -C "PT35 -> CollarPet"
fi
JSON="$(cp-discover 2>/dev/null || true)"
IP="$(printf '%s' "$JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("ip") or d.get("_source_ip",""))' 2>/dev/null || true)"
if [[ -z "$IP" ]]; then
    read -r -p "Collar Pet IP or hostname: " IP
fi
ssh-copy-id -i "$KEY.pub" -p "$COLLARPET_SSH_PORT" "$COLLARPET_USER@$IP"
SH
chmod 755 "$BIN/cp-pair-ssh"

cat > "$BIN/cp-connect" <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
source "$HOME/.config/collarpet/link.conf"
KEY="$HOME/.ssh/id_ed25519_collarpet"

discover_ip() {
    local j
    j="$(python3 "$HOME/.local/share/collarpet-link/discover.py" "$HELLO_PORT" "${1:-3}" 2>/dev/null || true)"
    [[ -n "$j" ]] || return 1
    printf '%s' "$j" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("ip") or d.get("_source_ip",""))'
}

ssh_args=(-p "$COLLARPET_SSH_PORT")
[[ -f "$KEY" ]] && ssh_args+=(-i "$KEY")

echo "Looking for Collar Pet on the current LAN..."
if IP="$(discover_ip 4)" && [[ -n "$IP" ]]; then
    echo "Found Collar Pet at $IP"
    exec ssh "${ssh_args[@]}" "$COLLARPET_USER@$IP"
fi

echo "Not found. Creating rescue hotspot '$RESCUE_SSID'..."
WIFI_IF="$(nmcli -t -f DEVICE,TYPE device status | awk -F: '$2=="wifi"{print $1; exit}')"
[[ -n "$WIFI_IF" ]] || { echo "No Wi-Fi interface found."; exit 2; }
OLD_CONN="$(nmcli -t -f NAME,TYPE connection show --active | awk -F: '$2=="802-11-wireless"{print $1; exit}')"

nmcli connection delete "$RESCUE_CONNECTION_NAME" >/dev/null 2>&1 || true
nmcli device wifi hotspot ifname "$WIFI_IF" con-name "$RESCUE_CONNECTION_NAME" ssid "$RESCUE_SSID" password "$RESCUE_PSK"

cleanup() {
    nmcli connection down "$RESCUE_CONNECTION_NAME" >/dev/null 2>&1 || true
    [[ -n "$OLD_CONN" ]] && nmcli connection up "$OLD_CONN" >/dev/null 2>&1 || true
}
trap cleanup EXIT

end=$((SECONDS + RESCUE_WAIT_SECONDS))
while (( SECONDS < end )); do
    if IP="$(discover_ip 2)" && [[ -n "$IP" ]]; then
        echo "Found Collar Pet on rescue network at $IP"
        ssh "${ssh_args[@]}" "$COLLARPET_USER@$IP" || true
        exit 0
    fi
    sleep 1
done

echo "Collar Pet did not join rescue network."
exit 3
SH
chmod 755 "$BIN/cp-connect"

cat > "$BASE/dashboard.py" <<'PY'
#!/usr/bin/env python3
import io, json, subprocess, threading, tkinter as tk
from pathlib import Path
from urllib.request import urlopen
from PIL import Image, ImageTk

HOME=Path.home()
CFG=HOME/".config/collarpet/link.conf"
BASE=HOME/".local/share/collarpet-link"
KEY=HOME/".ssh/id_ed25519_collarpet"

def load_cfg():
    d={}
    for line in CFG.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k,v=line.split("=",1)
            d[k]=v.strip().strip('"')
    return d

C=load_cfg()
PORT=int(C.get("HELLO_PORT","47842"))
HTTP=int(C.get("HTTP_PORT","47843"))
USER=C.get("COLLARPET_USER","jenna")
SSHPORT=C.get("COLLARPET_SSH_PORT","22")

root=tk.Tk()
root.title("Collar Pet")
root.geometry("800x480")
root.configure(bg="#10151d")
last_ip=None
busy=False

def label(parent,text,size=12,bold=False,fg="#d9e2ef",bg="#10151d"):
    return tk.Label(parent,text=text,font=("DejaVu Sans",size,"bold" if bold else "normal"),fg=fg,bg=bg,anchor="w")

top=tk.Frame(root,bg="#10151d"); top.pack(fill="x",padx=14,pady=(10,4))
label(top,"COLLAR PET",20,True,fg="#78d7ff").pack(side="left")
state=label(top,"SEARCHING",12,True,fg="#ffd166"); state.pack(side="right")

card=tk.Frame(root,bg="#19222e",highlightthickness=1,highlightbackground="#33475b")
card.pack(fill="both",expand=True,padx=14,pady=6)
rows={}
for i,name in enumerate(("Wi-Fi","IP","SSH","CPU","Load / RAM","Uptime","App","E-paper")):
    label(card,name,11,True,fg="#91a8be",bg="#19222e").grid(row=i,column=0,sticky="w",padx=(14,8),pady=5)
    v=label(card,"—",12,bg="#19222e"); v.grid(row=i,column=1,sticky="w",padx=8,pady=5); rows[name]=v
card.columnconfigure(1,weight=1)

status=label(root,"LAN discovery only • Bluetooth untouched",10,fg="#91a8be")
status.pack(fill="x",padx=16,pady=(0,4))
bar=tk.Frame(root,bg="#10151d"); bar.pack(fill="x",padx=14,pady=(2,10))

def discover():
    p=subprocess.run(["python3",str(BASE/"discover.py"),str(PORT),"3"],text=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    if p.returncode: return None
    return json.loads(p.stdout)

def fmt_uptime(v):
    if v is None: return "—"
    v=int(v); d,v=divmod(v,86400); h,v=divmod(v,3600); m,_=divmod(v,60)
    return f"{d}d {h}h {m}m" if d else f"{h}h {m}m"

def show(d):
    global last_ip
    if not d:
        state.config(text="OFFLINE",fg="#ff6b6b")
        for r in rows.values(): r.config(text="—")
        status.config(text="Not found on current LAN. CONNECT can create the rescue hotspot.")
        return
    last_ip=d.get("ip") or d.get("_source_ip")
    state.config(text="ONLINE",fg="#79e08f")
    rows["Wi-Fi"].config(text=d.get("ssid") or "connected")
    rows["IP"].config(text=last_ip or "—")
    rows["SSH"].config(text="ready" if d.get("ssh_enabled") else "off")
    t=d.get("cpu_temp"); rows["CPU"].config(text="—" if t is None else f"{t:.1f} °C")
    l=d.get("cpu_load"); r=d.get("ram_used")
    rows["Load / RAM"].config(text=f"{'—' if l is None else f'{l:.0f}%'} / {'—' if r is None else f'{r:.0f}%'}")
    rows["Uptime"].config(text=fmt_uptime(d.get("uptime")))
    svc=d.get("main_service") or {}
    rows["App"].config(text=svc.get("state","—"))
    rows["E-paper"].config(text="available" if d.get("epaper_available") else "not exported yet")
    sig=d.get("wifi_signal")
    status.config(text="Found via LAN" + (f" • signal {sig}%" if sig is not None else ""))

def refresh():
    global busy
    if busy: return
    busy=True
    state.config(text="SEARCHING",fg="#ffd166")
    def work():
        global busy
        d=discover()
        root.after(0,lambda:show(d))
        busy=False
    threading.Thread(target=work,daemon=True).start()

def connect():
    subprocess.Popen(["x-terminal-emulator","-e","bash","-lc","cp-connect; echo; read -r -p 'Press Enter to close...'"])

def control(action):
    if not last_ip:
        status.config(text="Collar Pet is not discovered."); return
    if not KEY.exists():
        status.config(text="SSH key missing — run PAIR SSH once."); return
    cmd=["ssh","-i",str(KEY),"-p",SSHPORT,"-o","BatchMode=yes","-o","ConnectTimeout=4",
         f"{USER}@{last_ip}","sudo","-n","/usr/local/sbin/collarpet-service-control",action]
    def work():
        p=subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        msg=(p.stdout or p.stderr).strip() or f"{action}: done"
        root.after(0,lambda:status.config(text=msg[:100]))
        root.after(800,refresh)
    threading.Thread(target=work,daemon=True).start()

def epaper():
    if not last_ip:
        status.config(text="Collar Pet is not discovered."); return
    win=tk.Toplevel(root); win.title("Collar Pet E-paper"); win.configure(bg="#e9e9e9")
    out=tk.Label(win,text="Loading…",bg="#e9e9e9",fg="#111"); out.pack(padx=10,pady=10)
    def work():
        try:
            data=urlopen(f"http://{last_ip}:{HTTP}/display.png",timeout=4).read()
            im=Image.open(io.BytesIO(data)).convert("1")
            scale=min(740/im.width,400/im.height,1.0)
            if scale!=1:
                im=im.resize((int(im.width*scale),int(im.height*scale)),Image.Resampling.NEAREST)
            photo=ImageTk.PhotoImage(im)
            def done():
                out.config(image=photo,text=""); out.image=photo
            root.after(0,done)
        except Exception as e:
            root.after(0,lambda:out.config(text=f"No e-paper export yet\n{e}"))
    threading.Thread(target=work,daemon=True).start()

def pair():
    subprocess.Popen(["x-terminal-emulator","-e","bash","-lc","cp-pair-ssh; echo; read -r -p 'Press Enter to close...'"])

def button(text,cmd,w):
    b=tk.Button(bar,text=text,command=cmd,width=w,font=("DejaVu Sans",10,"bold"),relief="flat",bd=0,pady=5)
    b.pack(side="left",padx=3)

button("CONNECT",connect,11)
button("E-PAPER",epaper,10)
button("START",lambda:control("start"),8)
button("RESTART",lambda:control("restart"),9)
button("PAIR SSH",pair,10)
button("REFRESH",refresh,9)
button("X",root.destroy,4)

refresh()
def periodic():
    refresh(); root.after(15000,periodic)
root.after(15000,periodic)
root.mainloop()
PY
chmod 755 "$BASE/dashboard.py"

cat > "$BIN/cp-dashboard" <<'SH'
#!/usr/bin/env bash
exec python3 "$HOME/.local/share/collarpet-link/dashboard.py"
SH
chmod 755 "$BIN/cp-dashboard"

cat > "$HOME/Desktop/Collar-Pet.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Collar Pet
Comment=Collar Pet service dashboard
Exec=$HOME/.local/bin/cp-dashboard
Terminal=false
Categories=Network;Utility;
EOF
chmod +x "$HOME/Desktop/Collar-Pet.desktop"

echo
echo "Done."
echo "  cp-dashboard"
echo "  cp-discover"
echo "  cp-connect"
echo "  cp-pair-ssh"
echo
echo "Bluetooth is NOT used by this integration."
