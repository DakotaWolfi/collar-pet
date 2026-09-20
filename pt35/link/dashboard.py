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
# PT35_FULLSCREEN_V1
root.attributes('-fullscreen',True)
root.bind('<F11>',lambda event:root.attributes('-fullscreen',not root.attributes('-fullscreen')))
root.bind('<Escape>',lambda event:root.attributes('-fullscreen',False))
try:
    _app_icon=tk.PhotoImage(file=str(BASE/'collarpet-icon.png'))
    root.iconphoto(True,_app_icon)
except tk.TclError:
    pass

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
        gear_battery_text.set('T:--  E:--')
        for widget in rows.values():widget.config(text='—')
        nearby=bool(data and data.get('ble_only'))
        connection.config(text='NEARBY (BLE)' if nearby else 'OFFLINE',fg='#ffd166' if nearby else '#ff6b6b')
        status.config(text='Nearby via BLE. Use RESCUE to start service Wi-Fi.' if nearby else 'Waiting for CollarPet. REFRESH retries; RESCUE starts service Wi-Fi.')
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
    logs_button.config(state='normal' if last_ip and app_state=='active' else 'disabled')

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

# PT35_GEAR_BATTERY_V1
gear_battery_text=tk.StringVar(value='T:--  E:--')
gear_battery_label=tk.Label(header,textvariable=gear_battery_text,font=('DejaVu Sans',-15,'bold'),bg=BG,fg=FG)
gear_battery_label.pack(side='right',padx=10)
gear_battery_pending=False
gear_battery_target=None

def poll_gear_battery():
    global gear_battery_pending,gear_battery_target
    target=last_ip if app_state=='active' else None
    if target!=gear_battery_target:
        gear_battery_target=target;gear_battery_text.set('T:--  E:--')
    if target and not gear_battery_pending:
        gear_battery_pending=True
        def done(result,error):
            global gear_battery_pending
            gear_battery_pending=False
            if last_ip!=target or app_state!='active':gear_battery_text.set('T:--  E:--');return
            def value(key):
                x=result.get(key) if isinstance(result,dict) and result.get('ok') else None
                return str(x)+'%' if type(x) is int and 0<=x<=100 else '--'
            gear_battery_text.set('T:'+value('tail')+'  E:'+value('ears'))
        background(lambda:ssh_request(target,'menu',{'op':'telemetry'}),done)
    root.after(30000,poll_gear_battery)
root.after(1000,poll_gear_battery)


# PT35_TOUCH_MENU_V1
class CollarMenu:
    def __init__(self,target):
        self.target=target;self.schema=None;self.pending=False;self.closed=False
        self.group='Gear';self.page=0;self.leaf=None;self.choice_page=0;self.draft=None
        self.win=tk.Toplevel(root);self.win.title('CollarPet menu');self.win.configure(bg=BG)
        self.win.attributes('-fullscreen',True)
        self.win.columnconfigure(0,weight=1);self.win.rowconfigure(2,weight=1)
        head=tk.Frame(self.win,bg=BG);head.grid(row=0,column=0,sticky='ew',padx=10,pady=6)
        self.note=label(head,'Loading menu…',14);self.note.pack(side='left',fill='x',expand=True)
        label(head,'',14,True).pack(side='right')
        battery=tk.Label(head,textvariable=gear_battery_text,font=('DejaVu Sans',-16,'bold'),bg=BG,fg=FG);battery.pack(side='right')
        self.tabs=tk.Frame(self.win,bg=BG);self.tabs.grid(row=1,column=0,sticky='ew',padx=8)
        self.body=tk.Frame(self.win,bg=CARD);self.body.grid(row=2,column=0,sticky='nsew',padx=8,pady=6)
        self.nav=tk.Frame(self.win,bg=BG);self.nav.grid(row=3,column=0,sticky='ew',padx=8,pady=(0,8))
        self.win.bind('<Escape>',lambda event:self.back())
        self.win.bind('<Destroy>',lambda event:setattr(self,'closed',True) if event.widget is self.win else None)
        self.load()

    def request(self,payload):
        if self.pending or self.closed:return
        if last_ip!=self.target:self.note.config(text='Connection changed. Reopen menu.');return
        self.pending=True;self.note.config(text='Waiting for collar…')
        def done(result,error):
            if self.closed:return
            self.pending=False
            if last_ip!=self.target:self.note.config(text='Connection changed. Reopen menu.');return
            if error:self.note.config(text=error[:65]);return
            if not isinstance(result,dict):self.note.config(text='Invalid menu response');return
            if result.get('menu'):
                try:self.render(result['menu'])
                except Exception as exc:self.note.config(text='Invalid menu: '+str(exc)[:45]);return
            self.note.config(text=('Sent to collar' if payload['op']=='set' else 'Collar settings') if result.get('ok') else result.get('error','Unavailable')[:65])
        background(lambda:ssh_request(self.target,'menu',payload),done)

    def load(self):self.request({'op':'get'})
    def apply(self,item,value):
        if self.pending or not self.schema:return
        if item.get('confirm') and not messagebox.askyesno('Confirm',item['confirm'],parent=self.win):return
        self.request({'op':'set','instance':self.schema['instance'],'revision':self.schema['revision'],
                      'id':item['id'],'value':value,'confirmed':bool(item.get('confirm'))})
    def button(self,parent,text,command,row,col,enabled=True):
        w=tk.Button(parent,text=text,command=command,font=('DejaVu Sans',-17,'bold'),bg=CARD,fg=FG,
                    activebackground='#30455b',activeforeground=FG,relief='flat',wraplength=max(180,(self.win.winfo_screenwidth()-60)//2),
                    state='normal' if enabled else 'disabled',padx=8,pady=12)
        w.grid(row=row,column=col,sticky='nsew',padx=4,pady=4);return w
    def select_group(self,group):
        self.group=group;self.page=0;self.leaf=None;self.draw()
    def open_choice(self,item):
        self.leaf=item['id'];self.choice_page=0;self.draft=item.get('value');self.draw()
    def back(self):
        if self.leaf:self.leaf=None;self.draw()
        else:self.win.destroy()
    def move_page(self,delta):
        if self.leaf:self.choice_page+=delta
        else:self.page+=delta
        self.draw()
    def change_number(self,item,delta):
        self.draft=max(item['min'],min(item['max'],int(self.draft or 0)+delta));self.draw()
    def render(self,schema):
        if schema.get('schema_version')!=1 or not isinstance(schema.get('items'),list):raise ValueError('unsupported schema')
        self.schema=schema;self.draw()
    def draw(self):
        if not self.schema:return
        for frame in (self.tabs,self.body,self.nav):
            for widget in frame.winfo_children():widget.destroy()
        groups=list(dict.fromkeys(i.get('group','Settings') for i in self.schema['items']))
        if self.group not in groups:self.group=groups[0] if groups else 'Settings'
        for col,group in enumerate(groups):
            self.tabs.columnconfigure(col,weight=1)
            tk.Button(self.tabs,text=group,command=lambda g=group:self.select_group(g),font=('DejaVu Sans',-14,'bold'),
                      bg='#356080' if group==self.group else CARD,fg=FG,pady=12,relief='flat').grid(row=0,column=col,sticky='ew',padx=2)
        for row in range(3):self.body.rowconfigure(row,weight=1,uniform='tiles')
        for col in range(2):self.body.columnconfigure(col,weight=1,uniform='tiles')
        item=next((i for i in self.schema['items'] if i['id']==self.leaf),None)
        if item and item['type']=='integer':
            self.note.config(text=item['label']+': '+str(self.draft))
            for index,delta in enumerate((-10,10,-1,1)):
                self.button(self.body,f'{delta:+d}',lambda d=delta,i=item:self.change_number(i,d),index//2,index%2)
            self.button(self.body,'APPLY '+str(self.draft),lambda i=item:self.apply(i,int(self.draft)),2,0,item.get('enabled',True))
            self.button(self.body,'BACK',self.back,2,1)
            page=0;pages=1
        else:
            entries=item['choices'] if item else [i for i in self.schema['items'] if i.get('group','Settings')==self.group]
            pages=max(1,(len(entries)+5)//6)
            page=max(0,min(self.choice_page if item else self.page,pages-1))
            if item:self.choice_page=page;self.note.config(text=item['label'])
            else:self.leaf=None;self.page=page
            for index,value in enumerate(entries[page*6:page*6+6]):
                if item:
                    text=str(value)+(' ✓' if value==item.get('value') else '')
                    callback=lambda v=value,i=item:self.apply(i,v);enabled=item.get('enabled',True)
                else:
                    text=value['label'];kind=value['type'];enabled=value.get('enabled',True)
                    if kind=='boolean':
                        text+='\n'+('ON' if value.get('value') else 'OFF')
                        callback=lambda i=value:self.apply(i,not bool(i.get('value')))
                    elif kind in ('choice','integer'):
                        text+='  ›';callback=lambda i=value:self.open_choice(i)
                    else:callback=lambda i=value:self.apply(i,None)
                self.button(self.body,text,callback,index//2,index%2,enabled)
        actions=[('BACK' if self.leaf else 'CLOSE',self.back,True),('◀',lambda:self.move_page(-1),page>0),
                 (f'{page+1}/{pages}',self.load,True),('▶',lambda:self.move_page(1),page+1<pages)]
        for col,(text,command,enabled) in enumerate(actions):
            self.nav.columnconfigure(col,weight=1,uniform='nav');self.button(self.nav,text,command,0,col,enabled)


# PT35_LIVE_LOGS_V1
LOG_CATEGORIES=[('esp', 'ESP'), ('audio', 'Audio'), ('vu', 'VU'), ('song_scan', 'Song scans'), ('song_worker', 'Song worker'), ('gear', 'Gear'), ('luma', 'Luma'), ('mind', 'Mind'), ('neural', 'Neural'), ('bleenv', 'BLE environment'), ('errors', 'Errors'), ('other', 'Other')]
LOG_CONFIG=HOME/'.config/collarpet/log-view.json'

class LogFilters(CollarMenu):
    def __init__(self,owner):
        self.owner=owner
        super().__init__(owner.target)
        self.win.title('Log filters');self.group='Logs';self.draw()
    def load(self):
        self.render({'schema_version':1,'instance':'local','revision':'local','items':[
            dict(id=id,label=title,group='Logs',type='boolean',value=id in self.owner.categories) for id,title in LOG_CATEGORIES]})
        self.note.config(text='Choose what to show in live logs')
    def apply(self,item,value):
        if value:self.owner.categories.add(item['id'])
        else:self.owner.categories.discard(item['id'])
        try:
            LOG_CONFIG.parent.mkdir(parents=True,exist_ok=True)
            temp=LOG_CONFIG.with_suffix('.tmp');temp.write_text(json.dumps(sorted(self.owner.categories)));temp.replace(LOG_CONFIG)
        except Exception as exc:self.note.config(text='Could not save filters: '+str(exc)[:45]);return
        self.load();self.owner.restart()

class LiveLogs:
    def __init__(self,target):
        self.target=target;self.closed=False;self.paused=False;self.worker=None;self.stop_stream=None;self.proc=None
        self.inbox=queue.Queue(maxsize=30);self.generation=0;self.restart_timer=None
        self.categories={'esp','audio','vu','song_scan','song_worker','errors'}
        try:
            saved=json.loads(LOG_CONFIG.read_text())
            if isinstance(saved,list):self.categories={v for v in saved if v in dict(LOG_CATEGORIES)}
        except Exception:pass
        self.win=tk.Toplevel(root);self.win.title('CollarPet live logs');self.win.configure(bg=BG);self.win.attributes('-fullscreen',True)
        self.win.columnconfigure(0,weight=1);self.win.rowconfigure(1,weight=1)
        self.note=label(self.win,'Connecting to collar logs…',14);self.note.grid(row=0,column=0,sticky='ew',padx=8,pady=6)
        self.text=tk.Text(self.win,bg=BG,fg=FG,font=('DejaVu Sans Mono',-13),wrap='word',state='disabled',takefocus=False)
        self.text.grid(row=1,column=0,sticky='nsew',padx=8)
        self.text.tag_config('errors',foreground='#ff8888');self.text.tag_config('song_scan',foreground='#82e0aa');self.text.tag_config('song_worker',foreground='#78d7ff')
        controls=tk.Frame(self.win,bg=BG);controls.grid(row=2,column=0,sticky='ew',padx=4,pady=5)
        for col,(title,callback) in enumerate([('FILTERS',lambda:LogFilters(self)),('PAUSE',self.pause),('↑',lambda:self.text.yview_scroll(-1,'pages')),('↓',lambda:self.text.yview_scroll(1,'pages')),('CLEAR',self.clear),('CLOSE',self.close)]):
            controls.columnconfigure(col,weight=1)
            b=tk.Button(controls,text=title,command=callback,font=('DejaVu Sans',-14,'bold'),pady=13);b.grid(row=0,column=col,sticky='ew',padx=2)
            if title=='PAUSE':self.pause_button=b
        self.win.bind('<Escape>',lambda event:self.close())
        self.win.bind('<Destroy>',lambda event:self.stop() if event.widget is self.win else None)
        self.restart();root.after(100,self.drain)
    def stop(self):
        self.closed=True
        if self.stop_stream:self.stop_stream.set()
        if self.proc and self.proc.poll() is None:
            try:self.proc.terminate()
            except OSError:pass
    def close(self):self.stop();self.win.destroy()
    def clear(self):self.text.config(state='normal');self.text.delete('1.0','end');self.text.config(state='disabled')
    def pause(self):
        self.paused=not self.paused;self.pause_button.config(text='RESUME' if self.paused else 'PAUSE')
        self.note.config(text='Paused — incoming lines are skipped' if self.paused else 'Live log stream')
        if not self.paused:self.text.see('end')
    def restart(self):
        if self.closed:return
        if self.restart_timer:root.after_cancel(self.restart_timer)
        self.restart_timer=root.after(350,self.connect)
    def connect(self):
        self.restart_timer=None
        if self.closed:return
        if self.stop_stream:self.stop_stream.set()
        if self.proc and self.proc.poll() is None:
            try:self.proc.terminate()
            except OSError:pass
        self.generation+=1;generation=self.generation;stop=threading.Event();self.stop_stream=stop
        categories=sorted(self.categories);target=self.target
        def put(packet):
            try:self.inbox.put_nowait((generation,packet))
            except queue.Full:
                try:self.inbox.get_nowait()
                except queue.Empty:pass
                try:self.inbox.put_nowait((generation,{'error':'Viewer fell behind; some log batches were skipped.'}))
                except queue.Full:pass
        def work():
            cursor=None;instance=None
            while not stop.is_set():
                proc=None
                try:
                    args=['ssh','-T','-i',str(KEY),'-p',SSHPORT,'-o','BatchMode=yes','-o','ConnectTimeout=4','-o','ServerAliveInterval=5','-o','ServerAliveCountMax=2',f'{USER}@{target}','sudo','-n','/usr/local/sbin/collarpet-service-control','menu']
                    proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
                    if stop.is_set():proc.terminate();return
                    self.proc=proc
                    proc.stdin.write(json.dumps({'op':'logs','categories':categories,'cursor':cursor,'instance':instance})+'\n');proc.stdin.flush()
                    # Keep stdin open for the SSH session; the request itself is one line.
                    while not stop.is_set():
                        line=proc.stdout.readline(262145)
                        if not line:break
                        if len(line)>262144:raise ValueError('Oversized log response')
                        try:packet=json.loads(line)
                        except ValueError:raise RuntimeError(line.strip()[:180] or 'Invalid log response')
                        if not packet.get('ok'):raise RuntimeError(packet.get('error','Logs unavailable'))
                        cursor=packet.get('cursor');instance=packet.get('instance');put(packet)
                    if not stop.is_set():put({'error':'Log stream disconnected. Retrying…'})
                except Exception as exc:
                    if not stop.is_set():put({'error':str(exc)[:180]})
                finally:
                    if proc:
                        if proc.poll() is None:proc.terminate()
                        try:proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:proc.kill();proc.wait()
                        for stream in (proc.stdin,proc.stdout):
                            try:stream.close()
                            except Exception:pass
                if stop.wait(3):break
        self.worker=threading.Thread(target=work,daemon=True);self.worker.start()
    def drain(self):
        if self.closed:return
        if last_ip!=self.target:self.note.config(text='Collar connection changed. Reopen logs.');self.stop();return
        for _ in range(10):
            try:generation,packet=self.inbox.get_nowait()
            except queue.Empty:break
            if generation!=self.generation:continue
            if packet.get('error'):self.note.config(text=packet['error']);continue
            if self.paused:continue
            self.note.config(text='LIVE • '+', '.join(title for id,title in LOG_CATEGORIES if id in self.categories))
            self.text.config(state='normal')
            if packet.get('dropped'):self.text.insert('end',f"[logs] {packet['dropped']} older lines expired from the collar buffer.\n",'errors')
            for event in packet.get('events',[]):
                self.text.insert('end',event['time']+' '+event['text']+'\n',event['category'])
            count=int(self.text.index('end-1c').split('.')[0])
            if count>1200:self.text.delete('1.0',f'{count-1200}.0')
            self.text.config(state='disabled');self.text.see('end')
        root.after(100,self.drain)

log_view=None
def open_logs():
    global log_view
    if not last_ip or app_state!='active':return
    if log_view is not None and not log_view.closed:
        log_view.win.lift();return
    log_view=LiveLogs(last_ip)

def open_menu():
    if last_ip and app_state=='active':CollarMenu(last_ip)

def button(text,command,close=False):
    col=len(bar.winfo_children())
    widget=tk.Button(bar,text=text,command=command,width=2 if close else 1,font=('DejaVu Sans',-13,'bold'),pady=7,relief='flat')
    bar.columnconfigure(col,weight=0 if close else 1,uniform='' if close else 'actions')
    widget.grid(row=0,column=col,sticky='ew',padx=2)
    return widget

# PT35_MANUAL_RESCUE_V1
rescue_active=None
rescue_pending=False
rescue_check_pending=False

def rescue_result(result,error):
    global rescue_active
    if error or not result or not result.get('ok'):
        rescue_active=None
        rescue_button.config(text='RESCUE',state='normal')
        status.config(text=(error or (result or {}).get('error','Rescue status unavailable'))[:130])
        return
    rescue_active=result['active']
    rescue_button.config(text='STOP AP' if rescue_active else 'RESCUE',state='normal')

def rescue_call(action):
    p=subprocess.run([str(HOME/'.local/bin/cp-rescue'),action],capture_output=True,text=True,timeout=100)
    try:return json.loads(p.stdout)
    except ValueError:raise RuntimeError((p.stderr or p.stdout or 'No rescue Wi-Fi response')[-180:])

def rescue_toggle():
    global rescue_pending
    if rescue_pending or rescue_check_pending:return
    rescue_pending=True;rescue_button.config(state='disabled')
    # Recheck current NetworkManager state before choosing START or STOP.
    def work():
        current=rescue_call('status')
        if not current.get('ok'):return current
        return rescue_call('stop' if current['active'] else 'start')
    def done(result,error):
        global rescue_pending,last_ip,next_status
        rescue_pending=False;rescue_result(result,error)
        if result and result.get('ok'):
            status.config(text=result.get('message','')[:140])
            last_ip=None;next_status=0
    background(work,done)

def poll_rescue():
    global rescue_check_pending
    if not rescue_pending and not rescue_check_pending:
        rescue_check_pending=True
        def done(result,error):
            global rescue_check_pending
            rescue_check_pending=False
            rescue_result(result,error)
        background(lambda:rescue_call('status'),done)
    root.after(10000,poll_rescue)



# PT35_CONTEXT_BUTTONS_V1
_original_update_buttons=update_buttons
_original_rescue_result=rescue_result

def layout_context_buttons():
    children=bar.winfo_children()
    def named(text):
        return next((w for w in children if w.cget('text')==text),None)
    connect=named('CONNECT')
    common=[named('PAIR SSH'),named('REFRESH'),named('X')]
    # Offline: use the two main action slots for connection and rescue.
    # Online: use those same slots for the service action and collar menu.
    if last_ip:
        primary=[app_button,menu_button,logs_button] if app_state=='active' else [app_button,rescue_button]
        if app_state=='active' and rescue_active:primary.append(rescue_button)
    else:
        primary=[connect,rescue_button]
    visible=[w for w in primary+common if w is not None]
    for column in range(len(children)):
        bar.columnconfigure(column,weight=0,uniform='',minsize=0)
    for widget in children:widget.grid_remove()
    for column,widget in enumerate(visible):
        close=widget.cget('text')=='X'
        bar.columnconfigure(column,weight=0 if close else 1,uniform='' if close else 'actions')
        widget.grid(row=0,column=column,sticky='ew',padx=2)

def update_buttons():
    _original_update_buttons()
    layout_context_buttons()

def rescue_result(result,error):
    _original_rescue_result(result,error)
    layout_context_buttons()


button('CONNECT',lambda:terminal('cp-connect'))
rescue_button=button('RESCUE',rescue_toggle)
root.after(0,poll_rescue)
app_button=button('APP …',control)
menu_button=button('MENU',open_menu)
logs_button=button('LOGS',open_logs)
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

