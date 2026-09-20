#!/usr/bin/env bash
# CollarPet-sourced JSON menu and PT35 dashboard update.
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
if [[ "${1:-}" == --help ]]; then
    echo 'Orange Pi: sudo bash update-collarpet-pt35-menu.sh collarpet'
    echo 'PT35: bash update-collarpet-pt35-menu.sh pt35'
    echo 'Append --rollback on either device to restore the previous version.'
    exit 0
fi
[[ $# == 1 || ( $# == 2 && "$2" == --rollback ) ]] || { echo 'Use: script {collarpet|pt35} [--rollback]'; exit 2; }
[[ "$1" == collarpet || "$1" == pt35 ]] || exit 2
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cat > "$STAGE/runtime_menu.py" <<'CP_MENU_RUNTIME_MENU_PY'
"""Collar-owned menu schema and validated commands in the existing asyncio loop."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import uuid

SOCKET=Path('/run/collarpet-menu/control.sock')

class Menu:
    def __init__(self,namespace):
        self.ns=namespace
        self.instance=uuid.uuid4().hex

    def entries(self):
        n=self.ns
        items=[]
        def add(id,label,group,type,command=None,**extra):
            items.append(dict(id=id,label=label,group=group,type=type,command=command,enabled=True,**extra))
        def toggle(id,label,group,var,command,**extra):
            add(id,label,group,'boolean',command,value=bool(n[var]),**extra)
        toggle('stealth','Stealth mode','Collar','STEALTH','STEALTH',persistent=False)
        add('brightness','LED brightness (%)','Collar','integer','COLLAR_LED',value=n['COLLAR_LED_PERCENT'],min=0,max=100,persistent=True)
        add('find','Find collar','Collar','action','FIND')
        add('display.refresh','Refresh physical e-paper','Collar','action','DISPLAY|REFRESH')
        toggle('gear.enabled','Enable gear','Gear','GEAR_MAIN_ENABLED','GEAR|ENABLE',persistent=True)
        toggle('tail.enabled','Enable tail','Tail','TAIL_ENABLED','GEAR|TAIL|ENABLE',persistent=True)
        toggle('ears.enabled','Enable ears','Ears','EARS_ENABLED','GEAR|EARS|ENABLE',persistent=True)
        toggle('ears.active','Keep ears connected','Ears','EARS_ACTIVE_MODE','GEAR|EARS|ACTIVE',persistent=True)
        for id,label,var,method in [('tail.active','Keep tail connected','TAIL_ACTIVE_MODE','set_active_mode'),
                                    ('tail.song','Wag for known songs','TAIL_WAG_KNOWN_SONG','set_song_wag')]:
            if callable(getattr(n['gear_manager'],method,None)):
                toggle(id,label,'Tail',var,None,method=method,persistent=True)
        for device,group,var in [('TAIL','Tail','TAIL_ENABLED'),('EARS','Ears','EARS_ENABLED')]:
            for op,label in [('CONNECT','Connect'),('RELEASE','Release'),('LEARN','Learn nearby gear'),('FORGET','Forget learned gear'),('BATT','Read battery')]:
                add(device.lower()+'.'+op.lower(),label,group,'action','GEAR|'+device+'|'+op)
                items[-1]['enabled']=bool(n['GEAR_MAIN_ENABLED'] and n[var]) if op in ('CONNECT','LEARN','BATT') else True
                if op in ('LEARN','FORGET'):items[-1]['confirm']=label+' for '+group.lower()+'?'
        for id,label,group,key,command in [('tail.move','Tail movement','Tail','TAIL_MOVE_COMMANDS','TAIL|MOVE'),
                                          ('tail.led','Tail LED effect','Tail','TAIL_LED_COMMANDS','TAIL|LED'),
                                          ('ears.command','Ear mode','Ears','EAR_COMMANDS','EAR|CMD')]:
            add(id,label,group,'choice',command,choices=sorted(n[key]),value=None)
            items[-1]['enabled']=bool(n['GEAR_MAIN_ENABLED'] and n['TAIL_ENABLED' if group=='Tail' else 'EARS_ENABLED'])
        add('pet','Pet interaction','Pet','choice','PET',choices=['ATTENTION','WAKE','CALM'],value=None)
        add('haptic','Haptic effect','Pet','choice','HAPTIC',choices=['CLICK','DOUBLE','FOX','ATTENTION','WAKE'],value=None)
        add('flash','Flash effect','Collar','choice','FLASHBANG',choices=['WHITE','COLOR'],value=None,confirm='Trigger the bright flash effect?')
        for op in ('REBOOT','SHUTDOWN'):
            add('power.'+op.lower(),op.title()+' collar','Power','action','POWER|'+op,confirm=op.title()+' the Orange Pi? This disconnects CollarPet.')
        return items

    def schema(self):
        items=[{k:v for k,v in item.items() if k not in ('command','method')} for item in self.entries()]
        revision=hashlib.sha256(json.dumps(items,sort_keys=True).encode()).hexdigest()[:20]
        return {'schema_version':1,'title':'CollarPet menu','instance':self.instance,'revision':revision,'items':items}

    def handle(self,request):
        if not isinstance(request,dict):raise ValueError('Expected an object')
        if request.get('op')=='get':return {'ok':True,'menu':self.schema()}
        if request.get('op')!='set':raise ValueError('Unknown operation')
        current=self.schema()
        if request.get('instance')!=self.instance or request.get('revision')!=current['revision']:
            return {'ok':False,'error':'Settings changed or CollarPet restarted. Reload the menu and try again.','menu':current}
        item=next((i for i in self.entries() if i['id']==request.get('id')),None)
        if not item or not item['enabled']:raise ValueError('Unavailable menu item')
        if item.get('confirm') and request.get('confirmed') is not True:raise ValueError('Confirmation required')
        value=request.get('value')
        kind=item['type']
        if kind=='boolean' and type(value) is not bool:raise ValueError('Expected boolean')
        if kind=='integer' and (type(value) is not int or not item['min']<=value<=item['max']):raise ValueError('Value outside allowed range')
        if kind=='choice' and value not in item['choices']:raise ValueError('Unknown choice')
        if kind=='action' and value is not None:raise ValueError('Action takes no value')
        if item.get('method'):
            getattr(self.ns['gear_manager'],item['method'])(value)
        else:
            command=item['command']
            if kind!='action':command+='|'+(str(int(value)) if kind=='boolean' else str(value))
            self.ns['remote_command_handler']('pt35-menu',command.encode('utf-8'))
        return {'ok':True,'message':'Accepted by CollarPet. Gear actions may complete asynchronously.','menu':self.schema()}


async def serve(namespace):
    menu=Menu(namespace)
    SOCKET.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chmod(SOCKET.parent,0o700)
    SOCKET.unlink(missing_ok=True)
    clients=set()
    async def client(reader,writer):
        task=asyncio.current_task()
        if len(clients)>=8:
            writer.close();return
        clients.add(task)
        try:
            raw=await asyncio.wait_for(reader.readline(),5)
            if len(raw)>8192:raise ValueError('Request too large')
            response=menu.handle(json.loads(raw))
        except Exception as exc:response={'ok':False,'error':str(exc)}
        try:
            writer.write(json.dumps(response,allow_nan=False).encode()+b'\n')
            await asyncio.wait_for(writer.drain(),3)
        except Exception:pass
        finally:
            writer.close()
            clients.discard(task)
    server=await asyncio.start_unix_server(client,path=str(SOCKET),limit=8192)
    os.chmod(SOCKET,0o600)
    try:
        async with server:await namespace['stop_event'].wait()
    finally:
        for task in list(clients):task.cancel()
        await asyncio.gather(*list(clients),return_exceptions=True)
        SOCKET.unlink(missing_ok=True)

CP_MENU_RUNTIME_MENU_PY
cat > "$STAGE/client.py" <<'CP_MENU_CLIENT_PY'
#!/usr/bin/env python3
"""Fixed root-owned RPC client, reached only through the existing sudo wrapper."""
import json
import socket
import sys

def main():
    raw=sys.stdin.buffer.readline(8193)
    if not raw or len(raw)>8192:raise ValueError('Missing or oversized JSON request')
    value=json.loads(raw)
    if not isinstance(value,dict) or value.get('op') not in ('get','set'):raise ValueError('Unknown menu operation')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(8)
        sock.connect('/run/collarpet-menu/control.sock')
        sock.sendall(json.dumps(value).encode()+b'\n')
        with sock.makefile('rb') as stream:response=stream.readline(262145)
    if len(response)>262144:raise ValueError('Oversized menu response')
    result=json.loads(response)
    print(json.dumps(result))
    return 0 if result.get('ok') else 1

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:
        print(json.dumps({'ok':False,'error':'Menu unavailable: '+str(exc)}));sys.exit(1)

CP_MENU_CLIENT_PY
cat > "$STAGE/patch_runtime.py" <<'CP_MENU_PATCH_RUNTIME_PY'
import ast

MARK='# COLLARPET_PT35_MENU_V1'
HELPER='''# COLLARPET_PT35_MENU_V1
async def pt35_menu_task():
    # A menu failure must never take down the pet's main tasks.
    import runpy
    while not stop_event.is_set():
        try:
            module = runpy.run_path('/usr/local/lib/collarpet-menu/runtime_menu.py')
            await module['serve'](globals())
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception('PT35 menu unavailable; runtime continues')
            await asyncio.sleep(15)


'''

def patched(source):
    ast.parse(source)
    if MARK in source:return source
    for anchor in ['def remote_command_handler(sender, data):','async def main():',
                   '        asyncio.create_task(remote_mirror_task(), name="remote-mirror"),']:
        if source.count(anchor)!=1:raise ValueError('Runtime differs from inspected version; no changes made: '+anchor)
    source=source.replace('async def main():',HELPER+'async def main():',1)
    anchor='        asyncio.create_task(remote_mirror_task(), name="remote-mirror"),'
    source=source.replace(anchor,anchor+'\n        asyncio.create_task(pt35_menu_task(), name="pt35-menu"),',1)
    compile(source,'collarpet.py','exec')
    return source

CP_MENU_PATCH_RUNTIME_PY
cat > "$STAGE/dashboard.py" <<'CP_MENU_DASHBOARD_PY'
#!/usr/bin/env python3
# PT35_COLLAR_MENU_DASHBOARD_V1
import io
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from urllib.request import urlopen
from PIL import Image, ImageTk

HOME=Path.home()
BASE=HOME/'.local/share/collarpet-link'
KEY=HOME/'.ssh/id_ed25519_collarpet'
C={}
for line in (HOME/'.config/collarpet/link.conf').read_text().splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        k,v=line.split('=',1);C[k.strip()]=v.strip().strip('"')
PORT=int(C.get('HELLO_PORT',47842));HTTP=int(C.get('HTTP_PORT',47843))
USER=C.get('COLLARPET_USER','jenna');SSHPORT=C.get('COLLARPET_SSH_PORT','22')
BG='#10151d';CARD='#19222e';FG='#d9e2ef';MUTED='#91a8be'
root=tk.Tk();root.title('Collar Pet');root.geometry('800x480');root.configure(bg=BG)
root.columnconfigure(0,weight=1);root.rowconfigure(2,weight=1)
last_ip=None;app_state=None;status_pending=False;command_pending=False;next_status=0
jobs=queue.Queue()

def label(parent,text,size=14,bold=False,bg=BG,fg=FG):
    return tk.Label(parent,text=text,font=('DejaVu Sans',-size,'bold' if bold else 'normal'),bg=bg,fg=fg,anchor='w')

def background(function,callback):
    def work():
        try:result,error=function(),None
        except Exception as exc:result,error=None,str(exc)
        jobs.put((callback,result,error))
    threading.Thread(target=work,daemon=True).start()

def ssh_request(target,action,request=None):
    if not KEY.exists():raise RuntimeError('Use PAIR SSH first.')
    args=['ssh','-i',str(KEY),'-p',SSHPORT,'-o','BatchMode=yes','-o','ConnectTimeout=4',
          '-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2',f'{USER}@{target}',
          'sudo','-n','/usr/local/sbin/collarpet-service-control',action]
    p=subprocess.run(args,input=json.dumps(request)+'\n' if request is not None else None,
                     text=True,capture_output=True,timeout=30)
    if action=='menu':
        try:return json.loads(p.stdout)
        except ValueError:raise RuntimeError((p.stderr or p.stdout or 'No menu response. Install the collar-side menu update.')[-400:])
    if p.returncode:raise RuntimeError((p.stderr or p.stdout or 'Command failed')[-400:])
    return p.stdout.strip() or 'Command completed'

def service_action(state):
    if state=='active':return 'RESTART','restart'
    if state in ('inactive','failed'):return 'START','start'
    return 'APP …',None

header=tk.Frame(root,bg=BG);header.grid(row=0,column=0,sticky='ew',padx=12,pady=(6,2))
label(header,'COLLAR PET',22,True,fg='#78d7ff').pack(side='left')
connection=label(header,'SEARCHING',14,True,fg='#ffd166');connection.pack(side='right')
stats=tk.Frame(root,bg=CARD);stats.grid(row=1,column=0,sticky='ew',padx=12,pady=3)
for col in (1,3):stats.columnconfigure(col,weight=1,uniform='values')
rows={}
for name,row,col,span in [('Wi-Fi',0,0,3),('IP',1,0,3),('SSH',2,0,1),('CPU',2,2,1),
                          ('Load / RAM',3,0,1),('Uptime',3,2,1),('App',4,0,1),('E-paper',4,2,1)]:
    label(stats,name,13,True,bg=CARD,fg=MUTED).grid(row=row,column=col,sticky='w',padx=(10,8),pady=3)
    value=label(stats,'—',14,bg=CARD);value.config(width=1)
    value.grid(row=row,column=col+1,columnspan=span,sticky='ew',padx=(0,10),pady=3);rows[name]=value
# Wrap long network names within their full-width value cell.
rows['Wi-Fi'].bind('<Configure>',lambda event:rows['Wi-Fi'].configure(wraplength=max(100,event.width)))
status=label(root,'BLE proximity • Wi-Fi status • SSH controls',12,fg=MUTED)
status.grid(row=3,column=0,sticky='ew',padx=12,pady=2)
bar=tk.Frame(root,bg=BG);bar.grid(row=4,column=0,sticky='ew',padx=10,pady=(2,8))

def fmt_uptime(value):
    if value is None:return '—'
    day,seconds=divmod(int(value),86400);hour,seconds=divmod(seconds,3600)
    return (str(day)+'d ' if day else '')+f'{hour}h {seconds//60}m'

def show(data,error=None):
    global last_ip,app_state
    if not data or data.get('ble_only'):
        last_ip=None;app_state=None
        for widget in rows.values():widget.config(text='—')
        nearby=bool(data and data.get('ble_only'))
        connection.config(text='NEARBY (BLE)' if nearby else 'OFFLINE',fg='#ffd166' if nearby else '#ff6b6b')
        status.config(text='CONNECT can create the rescue hotspot.' if nearby else 'CollarPet not reachable. Use CONNECT or REFRESH.')
    else:
        last_ip=data.get('_source_ip') or data.get('ip');app_state=(data.get('main_service') or {}).get('state')
        connection.config(text='ONLINE',fg='#79e08f')
        rows['Wi-Fi'].config(text=data.get('ssid') or '—');rows['IP'].config(text=last_ip or '—')
        rows['SSH'].config(text='ready' if data.get('ssh_enabled') else 'off')
        temp=data.get('cpu_temp');rows['CPU'].config(text='—' if temp is None else f'{temp:.1f} °C')
        percent=lambda x:'—' if x is None else f'{x:.0f}%'
        rows['Load / RAM'].config(text=percent(data.get('cpu_load'))+' / '+percent(data.get('ram_used')))
        rows['Uptime'].config(text=fmt_uptime(data.get('uptime')));rows['App'].config(text=app_state or '—')
        rows['E-paper'].config(text='available' if data.get('epaper_available') else 'not exported yet')
        signal=data.get('wifi_signal');ble=data.get('ble') or {}
        status.config(text=('BLE + LAN' if ble.get('found') else 'LAN connected')+(f' • Wi-Fi {signal}%' if signal is not None else ''))
    inline_epaper.set_target(last_ip)
    update_buttons()

def update_buttons():
    text,action=service_action(app_state)
    app_button.config(text=text,state='normal' if last_ip and action and not command_pending else 'disabled')
    menu_button.config(state='normal' if last_ip and app_state=='active' else 'disabled')

def refresh():
    global status_pending,next_status
    inline_epaper.refresh()
    if status_pending:return
    status_pending=True
    target=last_ip
    def work():
        if target:
            try:
                with urlopen(f'http://{target}:{HTTP}/status.json',timeout=3) as response:result=json.load(response)
                result['_source_ip']=target
                return result
            except Exception:pass
        p=subprocess.run(['/usr/bin/python3',str(BASE/'discover.py'),str(PORT),'3'],capture_output=True,text=True,timeout=22)
        return json.loads(p.stdout) if p.returncode==0 else None
    def done(result,error):
        global status_pending,next_status
        status_pending=False;next_status=time.monotonic()+5
        show(result,error)
    background(work,done)

def control():
    global command_pending
    _,action=service_action(app_state)
    if not last_ip or not action or command_pending:return
    target=last_ip;command_pending=True;update_buttons()
    status.config(text=action.title()+' requested…')
    def done(result,error):
        global command_pending
        command_pending=False;update_buttons()
        status.config(text=(error or result)[:140]);root.after(800,refresh)
    background(lambda:ssh_request(target,action),done)

def terminal(command):
    try:subprocess.Popen(['x-terminal-emulator','-e','bash','-lc',command+"; echo; read -r -p 'Press Enter to close…'"])
    except Exception as exc:status.config(text=str(exc))

class CollarMenu:
    def __init__(self,target):
        self.target=target;self.schema=None;self.pending=False;self.closed=False
        self.win=tk.Toplevel(root);self.win.title('CollarPet menu');self.win.geometry('760x440');self.win.configure(bg=BG)
        self.win.columnconfigure(0,weight=1);self.win.rowconfigure(1,weight=1)
        self.note=label(self.win,'Loading menu from CollarPet…',13);self.note.grid(row=0,column=0,sticky='ew',padx=10,pady=6)
        self.book=ttk.Notebook(self.win);self.book.grid(row=1,column=0,sticky='nsew',padx=8)
        bottom=tk.Frame(self.win,bg=BG);bottom.grid(row=2,column=0,sticky='ew',padx=8,pady=6)
        tk.Button(bottom,text='RELOAD MENU',command=self.load).pack(side='left')
        tk.Button(bottom,text='CLOSE',command=self.win.destroy).pack(side='right')
        self.win.bind('<Destroy>',lambda event:setattr(self,'closed',True) if event.widget is self.win else None)
        self.load()

    def request(self,payload):
        if self.pending or self.closed:return
        if last_ip!=self.target:
            self.note.config(text='Connection changed. Close and reopen the menu.');return
        self.pending=True;self.note.config(text='Waiting for CollarPet…')
        def done(result,error):
            if self.closed:return
            self.pending=False
            if last_ip!=self.target:
                self.note.config(text='Connection changed. Close and reopen the menu.');return
            if error:self.note.config(text=error[:160]);return
            if isinstance(result,dict) and result.get('menu'):
                try:self.render(result['menu'])
                except Exception as exc:self.note.config(text='Invalid menu: '+str(exc));return
            if not result.get('ok'):self.note.config(text=result.get('error','Menu unavailable')[:160])
            else:self.note.config(text=result.get('message','Live settings from CollarPet')[:160])
            if payload['op']=='set':root.after(500,refresh)
        background(lambda:ssh_request(self.target,'menu',payload),done)

    def load(self):self.request({'op':'get'})

    def apply(self,item,value):
        if self.pending or not self.schema:return
        if item.get('confirm') and not messagebox.askyesno('Confirm',item['confirm'],parent=self.win):return
        self.request({'op':'set','instance':self.schema['instance'],'revision':self.schema['revision'],
                      'id':item['id'],'value':value,'confirmed':bool(item.get('confirm'))})

    def render(self,schema):
        if schema.get('schema_version')!=1 or not isinstance(schema.get('items'),list):raise ValueError('unsupported schema')
        self.schema=schema
        selected=self.book.index(self.book.select()) if self.book.tabs() else 0
        for widget in self.book.winfo_children():widget.destroy()
        groups={}
        for item in schema['items']:
            group=item.get('group','Settings')
            if group not in groups:
                outer=tk.Frame(self.book,bg=CARD);self.book.add(outer,text=group)
                canvas=tk.Canvas(outer,bg=CARD,highlightthickness=0)
                scrollbar=ttk.Scrollbar(outer,orient='vertical',command=canvas.yview)
                canvas.configure(yscrollcommand=scrollbar.set);scrollbar.pack(side='right',fill='y');canvas.pack(fill='both',expand=True)
                inner=tk.Frame(canvas,bg=CARD);window=canvas.create_window(0,0,window=inner,anchor='nw')
                inner.bind('<Configure>',lambda event,c=canvas:c.configure(scrollregion=c.bbox('all')))
                canvas.bind('<Configure>',lambda event,c=canvas,w=window:c.itemconfigure(w,width=event.width))
                groups[group]=inner
            row=tk.Frame(groups[group],bg=CARD);row.pack(fill='x',padx=10,pady=4)
            caption=item['label']+(' (temporary)' if item.get('persistent') is False else '')
            label(row,caption,14,bg=CARD).pack(side='left',padx=(0,10))
            enabled='normal' if item.get('enabled',True) else 'disabled';kind=item['type']
            if kind=='boolean':
                value=bool(item.get('value'))
                tk.Button(row,text='ON' if value else 'OFF',width=9,state=enabled,
                          command=lambda i=item,v=value:self.apply(i,not v)).pack(side='right')
            elif kind in ('integer','choice'):
                var=tk.StringVar(value=str(item.get('value') if item.get('value') is not None else (item.get('choices') or [''])[0]))
                def submit(i=item,v=var):
                    try:value=int(v.get()) if i['type']=='integer' else v.get()
                    except ValueError:self.note.config(text='Enter a whole number.');return
                    self.apply(i,value)
                tk.Button(row,text='APPLY',state=enabled,command=submit).pack(side='right',padx=(6,0))
                if kind=='integer':control=tk.Spinbox(row,from_=item['min'],to=item['max'],textvariable=var,width=7,state=enabled)
                else:control=ttk.Combobox(row,values=item['choices'],textvariable=var,width=18,state='readonly' if enabled=='normal' else 'disabled')
                control.pack(side='right')
            elif kind=='action':tk.Button(row,text='RUN',width=9,state=enabled,command=lambda i=item:self.apply(i,None)).pack(side='right')
        if self.book.tabs():self.book.select(min(selected,len(self.book.tabs())-1))

def open_menu():
    if last_ip and app_state=='active':CollarMenu(last_ip)

def button(text,command,close=False):
    col=len(bar.winfo_children())
    widget=tk.Button(bar,text=text,command=command,width=2 if close else 1,font=('DejaVu Sans',-13,'bold'),pady=7,relief='flat')
    bar.columnconfigure(col,weight=0 if close else 1,uniform='' if close else 'actions')
    widget.grid(row=0,column=col,sticky='ew',padx=2)
    return widget

button('CONNECT',lambda:terminal('cp-connect'))
app_button=button('APP …',control)
menu_button=button('MENU',open_menu)
button('PAIR SSH',lambda:terminal('cp-pair-ssh'))
button('REFRESH',refresh)
button('X',root.destroy,True)

from urllib.request import Request
class InlineEpaper:
    """One background fetch at a time; all Tk operations stay on the UI thread."""
    def __init__(self,parent):
        self.target=None
        self.generation=0
        self.pending=False
        self.due=0
        self.results=queue.Queue()
        self.frame=None
        self.photo=None
        self.last_bytes=None
        self.last_success=None
        self.closed=False
        self.panel=tk.Frame(parent,bg="#19222e")
        self.panel.grid(row=2,column=0,sticky="new",padx=12,pady=2)
        self.panel.columnconfigure(0,weight=1)
        self.panel.rowconfigure(1,weight=0)
        
        self.canvas=tk.Canvas(self.panel,bg="#19222e",highlightthickness=0,width=1,height=170)
        self.canvas.grid(row=1,column=0,sticky="ew",pady=0)
        self.note=label(self.panel,"Waiting for CollarPet",10,fg="#91a8be",bg="#19222e")
        self.note.grid(row=0,column=0,sticky="ew")
        self.canvas.bind('<Configure>',lambda event:self.draw())
        self.panel.bind('<Destroy>',self.destroyed)
        self.tick()

    def destroyed(self,event):
        if event.widget is self.panel:self.closed=True

    def set_target(self,target):
        if target == self.target:return
        self.target=target
        self.generation+=1
        self.frame=None
        self.photo=None
        self.last_bytes=None
        self.last_success=None
        self.due=0
        self.note.config(text="Loading…" if target else "Waiting for Wi-Fi connection")
        self.draw()
        self.refresh()

    def refresh(self):
        self.due=0
        self.fetch()

    def fetch(self):
        if self.closed or self.pending or not self.target:return
        self.pending=True
        target,generation=self.target,self.generation
        def work():
            try:
                request=Request(f"http://{target}:{HTTP}/display.png",headers={'Cache-Control':'no-cache'})
                with urlopen(request,timeout=3) as response:
                    data=response.read(2*1024*1024+1)
                if len(data)>2*1024*1024:raise ValueError('Image exceeds size limit')
                with Image.open(io.BytesIO(data)) as source:
                    if source.format!='PNG' or source.width*source.height>2000000:
                        raise ValueError('Unexpected display image')
                    frame=source.convert('RGB')
                self.results.put((generation,data,frame,None))
            except Exception as exc:
                self.results.put((generation,None,None,str(exc)))
        threading.Thread(target=work,daemon=True).start()

    def accept(self,result):
        generation,data,frame,error=result
        self.pending=False
        if generation!=self.generation:return
        self.due=time.monotonic()+3
        if error:
            self.note.config(text=("Last image • connection unavailable" if self.frame else "E-paper unavailable • retrying"))
            if not self.frame:self.draw()
            return
        self.last_success=time.strftime('%H:%M:%S')
        if data!=self.last_bytes:
            self.last_bytes=data
            self.frame=frame
            self.draw()
        self.note.config(text="Checked "+self.last_success+" • auto refresh 3s")

    def tick(self):
        if self.closed:return
        try:
            while True:self.accept(self.results.get_nowait())
        except queue.Empty:pass
        if time.monotonic()>=self.due:self.fetch()
        self.panel.after(150,self.tick)

    def draw(self):
        self.canvas.delete('all')
        width,height=self.canvas.winfo_width(),self.canvas.winfo_height()
        if not self.frame:
            self.canvas.create_text(width/2,height/2,text="Waiting for e-paper…" if self.target else "Connect to CollarPet\nto view its screen",
                width=max(1,width-20),justify='center',fill='#91a8be',font=('DejaVu Sans',-14))
            return
        scale=min(max(1,width-4)/self.frame.width,max(1,height-4)/self.frame.height)
        size=(max(1,int(self.frame.width*scale)),max(1,int(self.frame.height*scale)))
        resampling=getattr(Image,'Resampling',Image)
        self.photo=ImageTk.PhotoImage(self.frame.resize(size,resampling.NEAREST))
        self.canvas.create_image(width/2,height/2,image=self.photo,anchor='center')

inline_epaper=InlineEpaper(root)
update_buttons()

def tick():
    try:
        while True:
            callback,result,error=jobs.get_nowait()
            try:callback(result,error)
            except Exception as exc:status.config(text='Dashboard error: '+str(exc)[:100])
    except queue.Empty:pass
    if not status_pending and time.monotonic()>=next_status:refresh()
    root.after(100,tick)

root.after(0,tick)
root.mainloop()

CP_MENU_DASHBOARD_PY
cat > "$STAGE/install.py" <<'CP_MENU_INSTALL_PY'
#!/usr/bin/env python3
"""Targeted two-device menu update; preserves the main unit and BLE services."""
import argparse
import ast
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from patch_runtime import patched

LIVE=Path('/home/jenna/collarpet/collarpet.py')
RUNTIME_PY='/home/jenna/mindtest/bin/python'
WRAPPER=Path('/usr/local/sbin/collarpet-service-control')
ROOT=Path('/usr/local/lib/collarpet-menu')
RULE=Path('/etc/sudoers.d/collarpet-menu')
LATEST=Path('/var/lib/collarpet-menu/latest')
UNIT='collarpet.service'
BACKUPS=Path('/var/backups/collarpet-menu')

def run(args,check=True,timeout=60,**kw):
    result=subprocess.run(args,text=True,capture_output=True,timeout=timeout,**kw)
    if check and result.returncode:raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Command failed: '+args[0])
    return result

def ctl(*args,**kw):return run(['systemctl',*args],**kw)
def say(message):print(message,flush=True)

def write(path,data,mode=0o644,owner=(0,0)):
    path=Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):raise RuntimeError('Refusing non-regular file: '+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.menu-update-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data)
        os.chmod(temp,mode);os.chown(temp,*owner);os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)

def stop():
    ctl('stop',UNIT)
    for prop,allowed in [('ActiveState',('inactive','failed')),('MainPID',('0',)),('ControlPID',('0',))]:
        if ctl('show',UNIT,'-p',prop,'--value').stdout.strip() not in allowed:
            raise RuntimeError('CollarPet did not fully stop; refusing file changes')

def restore(backup,automatic=False):
    backup=Path(backup)
    if automatic and ((backup/'committed').exists() or (backup/'restored').exists()):return
    state=json.loads((backup/'state.json').read_text())
    stop()
    for filename,item in state['files'].items():
        path=Path(filename)
        if item['exists']:write(path,(backup/'files'/filename.lstrip('/')).read_bytes(),item['mode'],(item['uid'],item['gid']))
        else:path.unlink(missing_ok=True)
    if state['active']:ctl('start',UNIT)
    (backup/'restored').write_text(time.ctime())
    ctl('stop',state['timer']+'.timer',check=False)
    say('Previous files and runtime state restored. Backup: '+str(backup))

def collar(payload):
    if ROOT.is_symlink():raise RuntimeError('Refusing symlink menu directory')
    for path in (LIVE,WRAPPER):
        if not path.is_file() or path.is_symlink():raise RuntimeError('Expected regular file: '+str(path))
    command=ctl('show',UNIT,'-p','ExecStart','--value').stdout
    if RUNTIME_PY+' -u '+str(LIVE) not in command:raise RuntimeError('Unexpected CollarPet service command')
    if ctl('show',UNIT,'-p','User','--value').stdout.strip() not in ('','root'):raise RuntimeError('Expected restored root CollarPet service')
    unit=ctl('cat',UNIT).stdout
    original=LIVE.read_bytes();candidate=patched(original.decode()).encode()
    wrapper=WRAPPER.read_text()
    if 'collarpet.service' not in wrapper or '[ "$#" -eq 1 ]' not in wrapper:raise RuntimeError('Unknown control wrapper; no changes made')
    if '# COLLARPET_MENU_RPC_V1' not in wrapper:
        anchor='case "$1" in\n'
        if wrapper.count(anchor)!=1:raise RuntimeError('Unexpected wrapper format')
        wrapper=wrapper.replace(anchor,anchor+'    # COLLARPET_MENU_RPC_V1\n    menu) exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin /usr/bin/python3 -I /usr/local/lib/collarpet-menu/client.py ;;\n',1)
    files={str(LIVE):candidate,str(WRAPPER):wrapper.encode(),str(RULE):b'jenna ALL=(root) NOPASSWD: /usr/local/sbin/collarpet-service-control menu\n'}
    for name in ('runtime_menu.py','client.py','install.py','patch_runtime.py'):
        files[str(ROOT/name)]=(payload/name).read_bytes()
    files[str(LATEST)]=b''
    for filename in files:
        path=Path(filename)
        if path.is_symlink() or (path.exists() and not path.is_file()):raise RuntimeError('Unexpected target: '+filename)
    # Validate with the existing runtime Python before stopping it.
    check=payload/'candidate.py';check.write_bytes(candidate)
    run([RUNTIME_PY,'-m','py_compile',str(check),str(payload/'runtime_menu.py')])
    rule=payload/'sudoers';rule.write_bytes(files[str(RULE)]);run(['visudo','-cf',str(rule)])
    shell=payload/'wrapper';shell.write_text(wrapper);run(['sh','-n',str(shell)])
    parent=BACKUPS;parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    backup=Path(tempfile.mkdtemp(prefix=time.strftime('%Y%m%d-%H%M%S-'),dir=parent));backup.chmod(0o700)
    state={'active':ctl('is-active','--quiet',UNIT,check=False).returncode==0,'files':{},'timer':'collarpet-menu-'+backup.name}
    for filename in files:
        path=Path(filename);item={'exists':path.exists()}
        if path.exists():
            st=path.stat();item.update(mode=st.st_mode&0o777,uid=st.st_uid,gid=st.st_gid)
            dest=backup/'files'/filename.lstrip('/');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
        state['files'][filename]=item
    (backup/'state.json').write_text(json.dumps(state))
    for name in ('install.py','patch_runtime.py'):shutil.copy2(payload/name,backup/name)
    say('Backup: '+str(backup))
    run(['systemd-run','--quiet','--collect','--unit='+state['timer'],'--on-active=5m',
         '/usr/bin/python3',str(backup/'install.py'),'--restore',str(backup),'--automatic'])
    try:
        if LIVE.read_bytes()!=original:raise RuntimeError('Runtime changed during preparation')
        say('Restarting only CollarPet to add its local menu endpoint...')
        stop()
        ROOT.mkdir(parents=True,exist_ok=True,mode=0o755)
        os.chown(ROOT,0,0);os.chmod(ROOT,0o755)
        for filename,data in files.items():
            if filename==str(LATEST):continue
            old=state['files'][filename]
            mode=old['mode'] if old['exists'] else (0o440 if filename==str(RULE) else 0o644)
            owner=(old['uid'],old['gid']) if old['exists'] else (0,0)
            if filename==str(WRAPPER):mode=0o755;owner=(0,0)
            elif filename==str(RULE):mode=0o440;owner=(0,0)
            elif Path(filename).parent==ROOT:mode=0o644;owner=(0,0)
            write(filename,data,mode,owner)
        ctl('start',UNIT)
        deadline=time.monotonic()+60
        while True:
            result=run(['sudo','-u','jenna','sudo','-n',str(WRAPPER),'menu'],input='{"op":"get"}\n',check=False,timeout=12)
            try:
                response=json.loads(result.stdout)
                valid=result.returncode==0 and response.get('ok') and response['menu']['schema_version']==1 and len(response['menu']['items'])>=10
            except Exception:valid=False
            if valid and ctl('is-active','--quiet',UNIT,check=False).returncode==0:break
            if time.monotonic()>=deadline:raise RuntimeError('Live menu verification failed: '+(result.stderr or result.stdout)[-500:])
            time.sleep(1)
        if ctl('cat',UNIT).stdout!=unit:raise RuntimeError('Main unit changed unexpectedly')
        if not state['active']:stop()
        write(LATEST,(str(backup)+'\n').encode(),0o600)
        (backup/'committed').write_text(time.ctime())
        ctl('stop',state['timer']+'.timer',check=False)
    except BaseException:
        say('Update failed; restoring previous files...')
        try:restore(backup)
        except Exception as exc:say('Rollback failed: '+str(exc)+'; backup: '+str(backup))
        raise
    say('Collar menu installed and live JSON verified. Main service unit and BLE services preserved.')
    say('Rollback: sudo bash update-collarpet-pt35-menu.sh collarpet --rollback')

def pt35(payload,rollback=False):
    base=Path.home()/'.local/share/collarpet-link';path=base/'dashboard.py';latest=base/'menu-dashboard-backup'
    if not path.is_file() or path.is_symlink():raise RuntimeError('Expected an installed PT35 dashboard')
    if rollback:
        backup=Path(latest.read_text().strip())
        if backup.parent!=base or not backup.name.startswith('dashboard.py.before-menu-'):raise RuntimeError('Unexpected backup path')
        shutil.copy2(backup,path);say('Previous dashboard restored. Reopen cp-dashboard.');return
    text=path.read_text()
    if not any(mark in text for mark in ('# PT35_INLINE_EPAPER_V1','# CP_BLE_DASHBOARD_V1','# PT35_COLLAR_MENU_DASHBOARD_V1')):
        raise RuntimeError('Unknown dashboard; no changes made')
    if not (base/'discover.py').is_file():raise RuntimeError('PT35 discovery helper missing')
    if text==(payload/'dashboard.py').read_text():say('Dashboard already current. Reopen cp-dashboard.');return
    ast.parse((payload/'dashboard.py').read_text())
    run(['/usr/bin/python3','-c','import tkinter; from PIL import Image,ImageTk'])
    fd,backup=tempfile.mkstemp(prefix='dashboard.py.before-menu-',dir=base);os.close(fd);shutil.copy2(path,backup)
    write(path,(payload/'dashboard.py').read_bytes(),path.stat().st_mode&0o777,(os.getuid(),os.getgid()))
    latest.write_text(backup+'\n')
    say('PT35 dashboard installed. Close and reopen cp-dashboard. Backup: '+backup)
    say('Rollback: bash update-collarpet-pt35-menu.sh pt35 --rollback')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',nargs='?',choices=['collarpet','pt35']);parser.add_argument('--payload');parser.add_argument('--rollback',action='store_true');parser.add_argument('--restore');parser.add_argument('--automatic',action='store_true')
    args=parser.parse_args();payload=Path(args.payload or Path(__file__).parent)
    if args.mode=='pt35':
        if os.geteuid()==0:raise SystemExit('Run PT35 mode as jenna, without sudo.')
        pt35(payload,args.rollback);return
    if os.geteuid()!=0:raise SystemExit('Use sudo for collarpet mode.')
    if args.mode!='collarpet' and not args.restore:raise SystemExit('Choose collarpet or pt35 mode.')
    with open('/run/collarpet-menu-install.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if args.restore:restore(args.restore,args.automatic)
        elif args.rollback:restore(LATEST.read_text().strip())
        else:collar(payload)

if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit('ERROR: '+str(exc))

CP_MENU_INSTALL_PY
python3 "$STAGE/install.py" --payload "$STAGE" "$@"
