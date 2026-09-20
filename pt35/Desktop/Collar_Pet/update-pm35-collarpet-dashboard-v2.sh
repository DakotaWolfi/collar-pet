#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -eq 0 ]]; then
    echo "Run this as your normal PM35 user, not with sudo."
    exit 1
fi

LIB_DIR="$HOME/.local/share/collarpet-link"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/Desktop/Collar Pet"

mkdir -p "$LIB_DIR" "$BIN_DIR" "$DESKTOP_DIR"

cat > "$LIB_DIR/collarpet_dashboard.py" <<'PY'
#!/usr/bin/env python3
import os, json, socket, subprocess, threading, tkinter as tk
from pathlib import Path

HOME=str(Path.home())
CFG=os.path.join(HOME,".config/collarpet/link.conf")
BLE_HELPER=os.path.join(HOME,".local/share/collarpet-link/cp_ble_scan.py")
CP_CONNECT=os.path.join(HOME,".local/bin/cp-connect")

def read_conf():
    d={}
    try:
        with open(CFG,encoding="utf-8") as f:
            for line in f:
                line=line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k,v=line.split("=",1)
                    d[k.strip()]=v.strip().strip('"').strip("'")
    except Exception:
        pass
    return d

C=read_conf()
UUID=C.get("COLLARPET_BLE_UUID","8d13f2c0-27d7-4e31-9fd1-c0a1c011a001")
HELLO_PORT=int(C.get("HELLO_PORT","47842"))
SSH_PORT=int(C.get("COLLARPET_SSH_PORT","22"))

BG="#10141c"; PANEL="#1b2230"; TEXT="#edf4ff"; MUTED="#8fa0b8"
GREEN="#43e39f"; YELLOW="#ffd166"; RED="#ff6178"; BLUE="#61b7ff"; PURPLE="#b695ff"

root=tk.Tk()
root.title("Collar Pet")
root.configure(bg=BG)

sw=root.winfo_screenwidth()
sh=root.winfo_screenheight()
root.geometry(f"{sw}x{sh}+0+0")
try: root.attributes("-fullscreen",True)
except Exception: pass

def lbl(parent,text,size=12,fg=TEXT,bg=PANEL,bold=False):
    return tk.Label(parent,text=text,font=("DejaVu Sans",size,"bold" if bold else "normal"),fg=fg,bg=bg)

# Header
hdr=tk.Frame(root,bg=BG)
hdr.pack(fill="x",padx=12,pady=(8,4))
lbl(hdr,"COLLAR PET",22,TEXT,BG,True).pack(side="left")
transport_lbl=lbl(hdr,"SEARCHING…",10,BLUE,BG,True)
transport_lbl.pack(side="right",padx=(8,2))

# Main pet card
pet=tk.Frame(root,bg=PANEL)
pet.pack(fill="both",expand=True,padx=12,pady=4)

top=tk.Frame(pet,bg=PANEL)
top.pack(fill="x",padx=12,pady=(10,4))

pet_name=lbl(top,"Collar Pet",15,TEXT,PANEL,True); pet_name.pack(side="left")
pet_state=lbl(top,"● OFFLINE",13,RED,PANEL,True); pet_state.pack(side="right")

# status grid
grid=tk.Frame(pet,bg=PANEL)
grid.pack(fill="both",expand=True,padx=12,pady=4)

fields={}
rows=[
    ("Wi-Fi","wifi"),
    ("IP","ip"),
    ("SSH","ssh"),
    ("Signal","signal"),
    ("PM35","pm35"),
]
for i,(name,key) in enumerate(rows):
    lbl(grid,name,11,MUTED,PANEL,True).grid(row=i,column=0,sticky="w",padx=(0,8),pady=4)
    v=lbl(grid,"—",12,TEXT,PANEL,False)
    v.grid(row=i,column=1,sticky="w",pady=4)
    fields[key]=v

grid.columnconfigure(1,weight=1)

message=lbl(pet,"Waiting for discovery…",10,MUTED,PANEL)
message.pack(fill="x",padx=12,pady=(2,8))

# Bottom controls
bar=tk.Frame(root,bg=BG)
bar.pack(fill="x",padx=12,pady=(4,10))

def button(text,cmd,bg,fg):
    return tk.Button(bar,text=text,command=cmd,font=("DejaVu Sans",11,"bold"),
                     bg=bg,fg=fg,activebackground=bg,activeforeground=fg,
                     bd=0,padx=15,pady=8,cursor="hand2")

refresh_busy=False

def run(cmd):
    return subprocess.run(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)

def get_pm35():
    ssid="offline"; ip="—"; state="disconnected"
    r=run(["nmcli","-t","-f","DEVICE,TYPE,STATE,CONNECTION","device","status"])
    for line in r.stdout.splitlines():
        p=line.split(":")
        if len(p)>=4 and p[1]=="wifi":
            dev,_,state,con=p[:4]
            ssid=con or state
            if state=="connected":
                ir=run(["ip","-4","-o","addr","show","dev",dev])
                for ln in ir.stdout.splitlines():
                    if " inet " in ln:
                        ip=ln.split()[3].split("/")[0]; break
            break
    return ssid,ip,state

def broadcasts():
    r=run(["ip","-4","-o","addr","show","scope","global"])
    out=[]
    for ln in r.stdout.splitlines():
        parts=ln.split()
        for i,x in enumerate(parts):
            if x=="brd" and i+1<len(parts):
                out.append(parts[i+1])
    return out

def udp_hello(target):
    s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    s.settimeout(1.0)
    s.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
    try:
        s.sendto(b"COLLARPET_HELLO\n",(target,HELLO_PORT))
        data,addr=s.recvfrom(4096)
        obj=json.loads(data.decode("utf-8","replace"))
        obj["_reply_ip"]=addr[0]
        return obj
    except Exception:
        return None
    finally:
        s.close()

def discover_lan():
    for bc in broadcasts():
        obj=udp_hello(bc)
        if obj:
            return obj
    return None

def discover_ble():
    if not os.path.exists(BLE_HELPER):
        return None
    try:
        r=run(["python3",BLE_HELPER,UUID,"3"])
        if r.stdout.strip():
            obj=json.loads(r.stdout.strip().splitlines()[-1])
            return obj if obj.get("found") else None
    except Exception:
        pass
    return None

def port_open(ip,port):
    try:
        with socket.create_connection((ip,port),timeout=0.8):
            return True
    except Exception:
        return False

def setfield(key,text,color=TEXT):
    fields[key].config(text=text,fg=color)

def refresh_worker():
    global refresh_busy
    if refresh_busy: return
    refresh_busy=True
    root.after(0,lambda: transport_lbl.config(text="SEARCHING…",fg=BLUE))

    pmssid,pmip,pmstate=get_pm35()

    # Prefer LAN because it proves useful communication, then BLE.
    petinfo=discover_lan()
    method="LAN" if petinfo else None

    if not petinfo:
        ble=discover_ble()
        if ble:
            petinfo=ble
            method="BLE"

    if petinfo:
        ip=petinfo.get("_reply_ip") or petinfo.get("ip") or ""
        wifi=petinfo.get("ssid") or ("connected" if petinfo.get("wifi_connected") else "offline")
        sig=petinfo.get("wifi_signal")
        ssh=bool(ip and port_open(ip,SSH_PORT))
        name=petinfo.get("name") or petinfo.get("hostname") or "Collar Pet"

        def apply_ok():
            global refresh_busy
            pet_name.config(text=name)
            pet_state.config(text="● ONLINE",fg=GREEN)
            transport_lbl.config(text=f"FOUND VIA {method}",fg=GREEN)

            setfield("wifi",wifi,GREEN if wifi!="offline" else YELLOW)
            setfield("ip",ip or "—",TEXT)
            setfield("ssh","READY" if ssh else "NOT REACHABLE",GREEN if ssh else YELLOW)
            setfield("signal","—" if sig is None else f"{sig}%",MUTED if sig is None else (GREEN if sig>=60 else YELLOW if sig>=30 else RED))
            setfield("pm35",f"{pmssid}  •  {pmip}",GREEN if pmstate=="connected" else YELLOW)

            message.config(text=f"Collar Pet is reachable over {method.lower()}.",fg=GREEN)
            refresh_busy=False
        root.after(0,apply_ok)
    else:
        def apply_bad():
            global refresh_busy
            pet_name.config(text="Collar Pet")
            pet_state.config(text="● NOT FOUND",fg=RED)
            transport_lbl.config(text="NO LINK",fg=RED)
            setfield("wifi","—",MUTED)
            setfield("ip","—",MUTED)
            setfield("ssh","—",MUTED)
            setfield("signal","—",MUTED)
            setfield("pm35",f"{pmssid}  •  {pmip}",GREEN if pmstate=="connected" else YELLOW)
            message.config(text="Not found on LAN or BLE. CONNECT can still try rescue AP.",fg=YELLOW)
            refresh_busy=False
        root.after(0,apply_bad)

def refresh():
    threading.Thread(target=refresh_worker,daemon=True).start()

def connect():
    subprocess.Popen([
        "x-terminal-emulator","-e","bash","-lc",
        f'{CP_CONNECT}; echo; read -r -p "Press Enter to close..."'
    ])

def quit_app():
    root.destroy()

button("EXIT",quit_app,"#293342",TEXT).pack(side="left")
button("REFRESH",refresh,BLUE,"#07111c").pack(side="right",padx=(6,0))
button("CONNECT",connect,GREEN,"#07140e").pack(side="right",padx=(6,0))

root.bind("<Escape>",lambda e:quit_app())
root.bind("<F11>",lambda e:root.attributes("-fullscreen",not bool(root.attributes("-fullscreen"))))

refresh()
def periodic():
    refresh()
    root.after(8000,periodic)
root.after(8000,periodic)
root.mainloop()
PY

chmod +x "$LIB_DIR/collarpet_dashboard.py"

cat > "$BIN_DIR/cp-dashboard" <<EOF
#!/usr/bin/env bash
exec python3 "$LIB_DIR/collarpet_dashboard.py"
EOF
chmod +x "$BIN_DIR/cp-dashboard"

cat > "$DESKTOP_DIR/00-Collar-Pet-Dashboard.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Collar Pet Dashboard
Comment=Automatic Collar Pet discovery and connection
Exec=$BIN_DIR/cp-dashboard
Terminal=false
Categories=Network;
EOF
chmod +x "$DESKTOP_DIR/00-Collar-Pet-Dashboard.desktop"

echo
echo "Dashboard updated."
echo "Start it with:"
echo "  cp-dashboard"
