#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  echo "Run this on the PT35 as jenna, without sudo."
  exit 1
fi
TARGET="$HOME/.local/bin/cp-petmind-training"
[[ -f "$TARGET" ]] || { echo "Missing $TARGET"; exit 1; }
BACK="$HOME/.local/share/collarpet-training-backups"
mkdir -p "$BACK"
STAMP=$(date +%Y%m%d-%H%M%S)
cp "$TARGET" "$BACK/cp-petmind-training.$STAMP.bak"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
cat > "$TMP" <<'PYGUI'
#!/usr/bin/env python3
# PT35_PETMIND_TRAINING_UI_V2
import json, queue, subprocess, threading, tkinter as tk, time
from tkinter import ttk
from pathlib import Path

HOME=Path.home(); BASE=HOME/".local/share/collarpet-link"; KEY=HOME/".ssh/id_ed25519_collarpet"; CFG=HOME/".config/collarpet/link.conf"
C={}
if CFG.exists():
    for line in CFG.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k,v=line.split("=",1); C[k.strip()]=v.strip().strip('"')
PORT=int(C.get("HELLO_PORT",47842)); USER=C.get("COLLARPET_USER","jenna"); SSHPORT=C.get("COLLARPET_SSH_PORT","22")
jobs=queue.Queue()

root=tk.Tk(); root.title("PetMind Training Recorder"); root.geometry("760x450"); root.minsize(700,430)
transport=tk.StringVar(value="AUTO"); duration=tk.IntVar(value=30)
command_busy=False; status_busy=False; next_status_poll=0.0
last_known=None

def discover():
    p=subprocess.run(["python3",str(BASE/"discover.py"),str(PORT),"2"],capture_output=True,text=True,timeout=8)
    if p.returncode:return None
    try:
        d=json.loads(p.stdout); return d.get("_source_ip") or d.get("ip")
    except Exception:return None

def wifi_request(args):
    ip=discover()
    if not ip or not KEY.exists(): raise RuntimeError("LAN unavailable")
    p=subprocess.run(["ssh","-i",str(KEY),"-p",SSHPORT,"-o","BatchMode=yes","-o","ConnectTimeout=3",
                      f"{USER}@{ip}","/usr/local/bin/cp-petmind-record",*args],
                     capture_output=True,text=True,timeout=10)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout or "SSH command failed")[-300:])
    try: obj=json.loads(p.stdout)
    except Exception: raise RuntimeError("Bad recorder response: "+p.stdout[-200:])
    return "Wi-Fi",obj

def ble_request(args):
    if args and args[0]=="status":
        raise RuntimeError("Live status is unavailable over one-way BLE fallback")
    p=subprocess.run(["sudo","-n","/usr/local/sbin/cp-training-ble-send",*args],
                     capture_output=True,text=True,timeout=12)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout or "BLE marker failed")[-300:])
    return "BLE",{"ok":True,"message":p.stdout.strip() or "BLE marker sent","unconfirmed":True}

def send(args):
    mode=transport.get()
    if mode in ("AUTO","Wi-Fi"):
        try:return wifi_request(args)
        except Exception:
            if mode=="Wi-Fi":raise
    return ble_request(args)

def set_status_card(obj=None, via=None, error=None, note=None):
    global last_known
    if error:
        link_var.set("OFFLINE / ERROR"); link_value.config(fg="#b00020")
        last_action_var.set(error[:120]); return
    if obj and obj.get("status"):
        s=obj["status"]; last_known=s
        active=bool(s.get("active"))
        rec_var.set("● RECORDING" if active else "■ STOPPED")
        rec_value.config(fg="#0a7d28" if active else "#555555")
        label=s.get("label")
        remain=int(s.get("label_seconds_remaining") or 0)
        label_var.set((label.upper()+f"  ({remain}s)") if label else "—")
        frames_var.set(str(s.get("frames",0)))
        f=s.get("file")
        file_var.set(Path(f).name if f else "—")
        link_var.set(via or "Wi-Fi"); link_value.config(fg="#0a6aa1")
        if note:last_action_var.set(note)
    elif via=="BLE":
        link_var.set("BLE • SENT / UNCONFIRMED"); link_value.config(fg="#9a6500")
        if note:last_action_var.set(note)

def command_done(kind,payload):
    global command_busy,next_status_poll
    command_busy=False
    if kind=="error":
        set_status_card(error=payload)
    else:
        via,obj,args=payload
        msg=obj.get("message") or "Command accepted"
        if obj.get("status"):
            set_status_card(obj,via,note=msg)
        else:
            # BLE fallback is transmit-only. Update what we can, but make it explicit.
            if args and args[0]=="start":
                rec_var.set("● RECORDING ?"); rec_value.config(fg="#9a6500")
            elif args and args[0]=="stop":
                rec_var.set("■ STOPPED ?"); rec_value.config(fg="#9a6500")
                label_var.set("—")
            elif args and args[0]=="clear":
                label_var.set("—")
            elif args and args[0]=="label" and len(args)>=3:
                label_var.set(args[1].upper()+f"  (~{args[2]}s)")
            set_status_card(via="BLE",note=msg+" — collar receipt not confirmed")
    next_status_poll=time.monotonic()+0.6

def background(args):
    global command_busy
    if command_busy:return
    command_busy=True; last_action_var.set("Sending "+ " ".join(args)+" …")
    def work():
        try:
            via,obj=send(args);jobs.put(("command",("ok",(via,obj,args))))
        except Exception as e:jobs.put(("command",("error",str(e))))
    threading.Thread(target=work,daemon=True).start()

def rescue(action):
    h=HOME/".local/bin/cp-rescue"
    if not h.exists():last_action_var.set("Recovery helper cp-rescue is not installed.");return
    last_action_var.set("Recovery AP "+action+" …")
    def work():
        try:
            p=subprocess.run([str(h),action],capture_output=True,text=True,timeout=100)
            obj=json.loads(p.stdout)
            if not obj.get("ok"):raise RuntimeError(obj.get("error","Recovery AP failed"))
            jobs.put(("rescue",(action,obj)))
        except Exception as e:jobs.put(("rescue_error",str(e)))
    threading.Thread(target=work,daemon=True).start()

def poll_status():
    global status_busy
    if status_busy or command_busy:return
    if transport.get()=="BLE":
        link_var.set("BLE ONLY");link_value.config(fg="#9a6500");return
    status_busy=True
    def work():
        try:
            via,obj=wifi_request(["status"]);jobs.put(("status",(via,obj)))
        except Exception as e:jobs.put(("status_error",str(e)))
    threading.Thread(target=work,daemon=True).start()

def drain_jobs():
    global status_busy,next_status_poll
    try:
        while True:
            kind,val=jobs.get_nowait()
            if kind=="command":
                k,p=val;command_done(k,p)
            elif kind=="status":
                status_busy=False;via,obj=val;set_status_card(obj,via)
            elif kind=="status_error":
                status_busy=False
                if transport.get()=="Wi-Fi":
                    link_var.set("Wi-Fi unavailable");link_value.config(fg="#b00020")
                elif transport.get()=="AUTO":
                    link_var.set("BLE fallback ready");link_value.config(fg="#9a6500")
            elif kind=="rescue":
                action,obj=val;last_action_var.set(obj.get("message") or ("Recovery AP "+action+" complete"));next_status_poll=time.monotonic()+1
            elif kind=="rescue_error":
                last_action_var.set("Recovery AP ERROR: "+val)
    except queue.Empty:pass
    if time.monotonic()>=next_status_poll:
        next_status_poll=time.monotonic()+2.0;poll_status()
    root.after(100,drain_jobs)

top=tk.Frame(root);top.pack(fill="x",padx=10,pady=(7,3))
tk.Label(top,text="PETMIND TRAINING",font=("DejaVu Sans",18,"bold")).pack(side="left")
ttk.Combobox(top,textvariable=transport,values=["AUTO","Wi-Fi","BLE"],state="readonly",width=8).pack(side="right")

ctrl=tk.LabelFrame(root,text="Recorder");ctrl.pack(fill="x",padx=10,pady=3)
for t,a in [("START",["start"]),("STOP",["stop"]),("STATUS",["status"]),("CLEAR LABEL",["clear"])]:
    tk.Button(ctrl,text=t,command=lambda x=a:background(x)).pack(side="left",expand=True,fill="x",padx=3,pady=4)

dur=tk.Frame(root);dur.pack(fill="x",padx=10)
tk.Label(dur,text="Label duration (s):").pack(side="left")
tk.Spinbox(dur,from_=5,to=255,textvariable=duration,width=6).pack(side="left",padx=5)

labels=tk.LabelFrame(root,text="Context label");labels.pack(fill="x",padx=10,pady=3)
for i,n in enumerate(["quiet","resting","speech","music","walking","crowd","machinery","dark_active","ef_badges","fox","riding","outdoor","other"]):
    tk.Button(labels,text=n.replace("_"," ").upper(),command=lambda x=n:background(["label",x,str(duration.get())])).grid(row=i//5,column=i%5,sticky="ew",padx=2,pady=2)
for c in range(5):labels.columnconfigure(c,weight=1)

events=tk.LabelFrame(root,text="Instant event marker");events.pack(fill="x",padx=10,pady=3)
for n in ["bang","clap","surprise","unexpected_noise","fox_appeared"]:
    tk.Button(events,text=n.replace("_"," ").upper(),command=lambda x=n:background(["event",x])).pack(side="left",expand=True,fill="x",padx=2,pady=3)

res=tk.Frame(root);res.pack(fill="x",padx=10,pady=3)
tk.Button(res,text="START RESCUE AP",command=lambda:rescue("start")).pack(side="left",expand=True,fill="x",padx=(0,3))
tk.Button(res,text="STOP RESCUE AP",command=lambda:rescue("stop")).pack(side="left",expand=True,fill="x",padx=(3,0))

# Useful feedback instead of a raw JSON dump.
panel=tk.LabelFrame(root,text="Live recorder status");panel.pack(fill="x",padx=10,pady=(3,7))
for c in (1,3,5):panel.columnconfigure(c,weight=1)

rec_var=tk.StringVar(value="—");label_var=tk.StringVar(value="—");frames_var=tk.StringVar(value="—")
file_var=tk.StringVar(value="—");link_var=tk.StringVar(value="CHECKING…");last_action_var=tk.StringVar(value="Ready")
def stat(row,col,title,var,span=1):
    tk.Label(panel,text=title,font=("DejaVu Sans",9,"bold"),anchor="w").grid(row=row,column=col,sticky="w",padx=(6,3),pady=1)
    w=tk.Label(panel,textvariable=var,anchor="w");w.grid(row=row,column=col+1,columnspan=span,sticky="ew",padx=(0,8),pady=1);return w
rec_value=stat(0,0,"REC",rec_var);stat(0,2,"LABEL",label_var);stat(0,4,"FRAMES",frames_var)
link_value=stat(1,0,"LINK",link_var);stat(1,2,"FILE",file_var,3)
tk.Label(panel,text="LAST",font=("DejaVu Sans",9,"bold"),anchor="w").grid(row=2,column=0,sticky="nw",padx=(6,3),pady=1)
tk.Label(panel,textvariable=last_action_var,anchor="w",justify="left",wraplength=630).grid(row=2,column=1,columnspan=5,sticky="ew",padx=(0,8),pady=1)

transport.trace_add("write",lambda *_:globals().__setitem__("next_status_poll",0.0))
drain_jobs();root.mainloop()

PYGUI
python3 -m py_compile "$TMP"
install -m 0755 "$TMP" "$TARGET"
echo "Updated PT35 PetMind Training UI."
echo "Backup: $BACK/cp-petmind-training.$STAMP.bak"
echo "Close and reopen the PetMind Training app."
