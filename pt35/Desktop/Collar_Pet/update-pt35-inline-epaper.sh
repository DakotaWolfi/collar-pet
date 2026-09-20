#!/usr/bin/env bash
set -Eeuo pipefail
if [[ $EUID == 0 ]]; then
    echo 'Run this on PT35 as jenna, without sudo.'
    exit 1
fi
python3 - <<'PT35_INLINE_PATCH'
from pathlib import Path
import ast
import os
import shutil
import tempfile


def patched(source,component):
    if '# PT35_INLINE_EPAPER_V1' in source:return source
    def replace(old,new):
        nonlocal source
        if source.count(old)!=1:raise ValueError('Dashboard differs from the supplied version; no files changed. Missing layout anchor: '+old[:65])
        source=source.replace(old,new,1)
    replace('root.configure(bg="#10151d")','''root.configure(bg="#10151d")
root.columnconfigure(0,weight=1)
root.rowconfigure(1,weight=1)''')
    replace('top.pack(fill="x",padx=14,pady=(10,4))','top.grid(row=0,column=0,sticky="ew",padx=14,pady=(10,4))')
    replace('card.pack(fill="both",expand=True,padx=14,pady=6)','''card.grid(row=1,column=0,sticky="nsew",padx=14,pady=6)
card.columnconfigure(0,weight=44,uniform="content")
card.columnconfigure(1,weight=56,uniform="content")
card.rowconfigure(0,weight=1)
stats=tk.Frame(card,bg="#19222e")
stats.grid(row=0,column=0,sticky="nsew",padx=(0,4),pady=6)''')
    replace('label(card,name,11,True','label(stats,name,11,True')
    replace('v=label(card,"—",12,bg="#19222e")','v=label(stats,"—",12,bg="#19222e"); v.config(width=1,wraplength=150)')
    replace('v.grid(row=i,column=1,sticky="w"','v.grid(row=i,column=1,sticky="ew"')
    replace('card.columnconfigure(1,weight=1)','stats.columnconfigure(1,weight=1)')
    replace('status.pack(fill="x",padx=16,pady=(0,4))','status.grid(row=2,column=0,sticky="ew",padx=16,pady=(0,4))')
    replace('bar.pack(fill="x",padx=14,pady=(2,10))','bar.grid(row=3,column=0,sticky="ew",padx=14,pady=(2,10))')
    replace('font=("DejaVu Sans",size,"bold" if bold else "normal")','font=("DejaVu Sans",-round(size*1.25),"bold" if bold else "normal")')
    replace('def discover():',component+'\n\ndef discover():')
    replace('stderr=subprocess.DEVNULL)','stderr=subprocess.DEVNULL,timeout=22)')
    # Both the original LAN dashboard and the BLE-patched version are supported.
    if '    if not d:\n        last_ip=None' not in source:
        replace('    if not d:\n        state.config','    if not d:\n        last_ip=None\n        state.config')
    replace('''    state.config(text="SEARCHING",fg="#ffd166")
    def work():
        global busy
        d=discover()
        root.after(0,lambda:show(d))
        busy=False
    threading.Thread(target=work,daemon=True).start()''','''    state.config(text="SEARCHING",fg="#ffd166")
    inline_epaper.refresh()
    def work():
        try:d=discover()
        except Exception:d=None
        discovery_results.put(d)
    threading.Thread(target=work,daemon=True).start()''')
    # The image is now in the main window; E-PAPER forces an immediate fetch.
    tree=ast.parse(source)
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='epaper')
    lines=source.splitlines(keepends=True)
    lines[node.lineno-1:node.end_lineno]=['def epaper():\n    inline_epaper.refresh()\n']
    source=''.join(lines)
    # Keep the previous equal-width button fix, or apply it for the LAN original.
    if '# PT35_BUTTON_GRID_V1' not in source:
        replace('''def button(text,cmd,w):
    b=tk.Button(bar,text=text,command=cmd,width=w,font=("DejaVu Sans",10,"bold"),relief="flat",bd=0,pady=5)
    b.pack(side="left",padx=3)''','''# PT35_BUTTON_GRID_V1
def button(text,cmd,w):
    column=len(bar.winfo_children())
    close=text == "X"
    b=tk.Button(bar,text=text,command=cmd,width=2 if close else 1,
                font=("DejaVu Sans",-13,"bold"),relief="flat",bd=0,pady=7,padx=2)
    if close:bar.columnconfigure(column,weight=0)
    else:bar.columnconfigure(column,weight=1,uniform="actions")
    b.grid(row=0,column=column,sticky="ew",padx=2)''')
    compile(source,'dashboard.py','exec')
    return source


def install(component):
    path=Path.home()/'.local/share/collarpet-link/dashboard.py'
    if not path.is_file() or path.is_symlink():raise SystemExit('Expected a regular PT35 dashboard at '+str(path))
    source=path.read_text()
    updated=patched(source,component)
    if updated==source:
        print('Inline e-paper already installed. Reopen cp-dashboard.');return
    fd,backup=tempfile.mkstemp(prefix='dashboard.py.before-inline-',dir=path.parent)
    os.close(fd);shutil.copy2(path,backup)
    fd,temp=tempfile.mkstemp(prefix='.dashboard-inline-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:stream.write(updated)
        os.chmod(temp,path.stat().st_mode & 0o777)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    print('Installed inline e-paper with automatic refresh. Backup: '+backup)
    print('Close and reopen cp-dashboard.')

install('# PT35_INLINE_EPAPER_V1\nimport queue\nimport time\nfrom urllib.request import Request\n\nclass InlineEpaper:\n    """One background fetch at a time; all Tk operations stay on the UI thread."""\n    def __init__(self,parent):\n        self.target=None\n        self.generation=0\n        self.pending=False\n        self.due=0\n        self.results=queue.Queue()\n        self.frame=None\n        self.photo=None\n        self.last_bytes=None\n        self.last_success=None\n        self.closed=False\n        self.panel=tk.Frame(parent,bg="#19222e")\n        self.panel.grid(row=0,column=1,sticky="nsew",padx=(6,10),pady=10)\n        self.panel.columnconfigure(0,weight=1)\n        self.panel.rowconfigure(1,weight=1)\n        label(self.panel,"E-PAPER",11,True,fg="#91a8be",bg="#19222e").grid(row=0,column=0,sticky="w")\n        self.canvas=tk.Canvas(self.panel,bg="#e9e9e9",highlightthickness=0,width=1,height=1)\n        self.canvas.grid(row=1,column=0,sticky="nsew",pady=6)\n        self.note=label(self.panel,"Waiting for CollarPet",10,fg="#91a8be",bg="#19222e")\n        self.note.grid(row=2,column=0,sticky="ew")\n        self.canvas.bind(\'<Configure>\',lambda event:self.draw())\n        self.panel.bind(\'<Destroy>\',self.destroyed)\n        self.tick()\n\n    def destroyed(self,event):\n        if event.widget is self.panel:self.closed=True\n\n    def set_target(self,target):\n        if target == self.target:return\n        self.target=target\n        self.generation+=1\n        self.frame=None\n        self.photo=None\n        self.last_bytes=None\n        self.last_success=None\n        self.due=0\n        self.note.config(text="Loading…" if target else "Waiting for Wi-Fi connection")\n        self.draw()\n        self.refresh()\n\n    def refresh(self):\n        self.due=0\n        self.fetch()\n\n    def fetch(self):\n        if self.closed or self.pending or not self.target:return\n        self.pending=True\n        target,generation=self.target,self.generation\n        def work():\n            try:\n                request=Request(f"http://{target}:{HTTP}/display.png",headers={\'Cache-Control\':\'no-cache\'})\n                with urlopen(request,timeout=3) as response:\n                    data=response.read(2*1024*1024+1)\n                if len(data)>2*1024*1024:raise ValueError(\'Image exceeds size limit\')\n                with Image.open(io.BytesIO(data)) as source:\n                    if source.format!=\'PNG\' or source.width*source.height>2000000:\n                        raise ValueError(\'Unexpected display image\')\n                    frame=source.convert(\'RGB\')\n                self.results.put((generation,data,frame,None))\n            except Exception as exc:\n                self.results.put((generation,None,None,str(exc)))\n        threading.Thread(target=work,daemon=True).start()\n\n    def accept(self,result):\n        generation,data,frame,error=result\n        self.pending=False\n        if generation!=self.generation:return\n        self.due=time.monotonic()+3\n        if error:\n            self.note.config(text=("Last image • connection unavailable" if self.frame else "E-paper unavailable • retrying"))\n            if not self.frame:self.draw()\n            return\n        self.last_success=time.strftime(\'%H:%M:%S\')\n        if data!=self.last_bytes:\n            self.last_bytes=data\n            self.frame=frame\n            self.draw()\n        self.note.config(text="Checked "+self.last_success+" • auto refresh 3s")\n\n    def tick(self):\n        if self.closed:return\n        try:\n            while True:self.accept(self.results.get_nowait())\n        except queue.Empty:pass\n        if time.monotonic()>=self.due:self.fetch()\n        self.panel.after(150,self.tick)\n\n    def draw(self):\n        self.canvas.delete(\'all\')\n        width,height=self.canvas.winfo_width(),self.canvas.winfo_height()\n        if not self.frame:\n            self.canvas.create_text(width/2,height/2,text="Waiting for e-paper…" if self.target else "Connect to CollarPet\\nto view its screen",\n                width=max(1,width-20),justify=\'center\',fill=\'#555\',font=(\'DejaVu Sans\',-14))\n            return\n        scale=min(max(1,width-16)/self.frame.width,max(1,height-16)/self.frame.height)\n        size=(max(1,int(self.frame.width*scale)),max(1,int(self.frame.height*scale)))\n        resampling=getattr(Image,\'Resampling\',Image)\n        self.photo=ImageTk.PhotoImage(self.frame.resize(size,resampling.NEAREST))\n        self.canvas.create_image(width/2,height/2,image=self.photo,anchor=\'center\')\n\ninline_epaper=InlineEpaper(card)\ndiscovery_results=queue.Queue()\n\ndef poll_discovery():\n    global busy\n    try:\n        while True:\n            result=discovery_results.get_nowait()\n            busy=False\n            show(result)\n            inline_epaper.set_target(last_ip)\n    except queue.Empty:pass\n    root.after(100,poll_discovery)\n\nroot.after(100,poll_discovery)\n')
PT35_INLINE_PATCH
