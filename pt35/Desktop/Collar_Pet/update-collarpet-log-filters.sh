#!/usr/bin/env bash
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
[[ "${1:-}" == collarpet || "${1:-}" == pt35 ]] || { echo 'Use: script {collarpet|pt35} [--rollback]'; exit 2; }
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cat > "$STAGE/live_logs.py" <<'CP_FILTER_LIVE_LOGS_PY'
"""Bounded live diagnostics. Installed only inside the collar runtime process."""
import asyncio
from collections import deque
import logging
import re
import sys
import threading
import time
import uuid
CATEGORIES=('esp','audio','vu','song_scan','song_worker','gear','luma','mind','neural','bleenv','errors','other')

def category(text,default='other'):
    upper=text.upper()
    # Empty error fields are health information, not error messages.
    checked=re.sub(r"\b(?:LAST_)?ERRORS?\s*[:=]\s*(?:-|NONE|NULL|FALSE|0|\"\"|'')(?=\s|$|[,;])",'',upper)
    if re.search(r'\b(?:ERROR|FAILED|TRACEBACK|EXCEPTION|WARNING)\b',checked):return 'errors'
    if re.search(r'\bLUMA\b',upper):return 'luma' 
    if re.search(r'\bMIND\b',upper):return 'mind'
    if re.search(r'\bNEURAL\b',upper):return 'neural'
    if re.search(r'\bBLE[ _-]?ENV(?:IRONMENT)?\b',upper):return 'bleenv'
    if '[SONGWORKER' in upper or '[SONGDB' in upper or 'SONG WORKER' in upper:return 'song_worker'
    if '[SONG' in upper or '[NOWPLAYING]' in upper:return 'song_scan'
    if '[GEAR VU' in upper or '[VU' in upper:return 'vu'
    if '[ESP' in upper or '[UART' in upper:return 'esp'
    if '[AUDIO' in upper:return 'audio'
    if '[TAIL' in upper or '[EARS' in upper:return 'gear'
    return default

class Buffer:
    def __init__(self):self.rows=deque(maxlen=2000);self.seq=0;self.instance=uuid.uuid4().hex;self.lock=threading.Lock()
    def add(self,text,kind=None):
        text=str(text).strip()
        if not text:return
        with self.lock:
            self.seq+=1;self.rows.append({'seq':self.seq,'time':time.strftime('%H:%M:%S'),'category':kind or category(text),'text':text[:1200]})
    def packet(self,cursor,categories):
        with self.lock:
            oldest=self.rows[0]['seq'] if self.rows else self.seq+1
            if cursor is None:cursor=max(0,self.seq-100)
            dropped=max(0,oldest-cursor-1)
            rows=[];end=cursor
            for row in self.rows:
                if row['seq']<=cursor:continue
                end=row['seq']
                if row['category'] in categories:rows.append(row)
                if len(rows)>=100:break
            return {'ok':True,'instance':self.instance,'cursor':max(end,min(cursor,self.seq)),'dropped':dropped,'events':rows}

class Tee:
    def __init__(self,original,buffer,default='other'):self.original=original;self.buffer_log=buffer;self.default=default;self.pending='';self.lock=threading.RLock()
    def write(self,text):
        result=self.original.write(text)
        with self.lock:
            self.pending+=text
            while '\n' in self.pending:
                line,self.pending=self.pending.split('\n',1);self.buffer_log.add(line,category(line,self.default))
            if len(self.pending)>2400:self.buffer_log.add(self.pending[:1200],self.default);self.pending=''
        return result
    def flush(self):return self.original.flush()
    def __getattr__(self,name):return getattr(self.original,name)

class Handler(logging.Handler):
    def __init__(self,buffer):super().__init__(logging.INFO);self.buffer_log=buffer
    def emit(self,record):self.buffer_log.add(self.format(record),'errors' if record.levelno>=logging.WARNING else None)

class Diagnostics:
    def __init__(self,ns):
        self.ns=ns;self.buffer=Buffer();self.stdout=Tee(sys.stdout,self.buffer);self.stderr=Tee(sys.stderr,self.buffer,'errors');self.handler=Handler(self.buffer)
        self.handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
    def start(self):sys.stdout=self.stdout;sys.stderr=self.stderr;self.ns['LOGGER'].addHandler(self.handler)
    def close(self):
        if sys.stdout is self.stdout:sys.stdout=self.stdout.original
        if sys.stderr is self.stderr:sys.stderr=self.stderr.original
        self.ns['LOGGER'].removeHandler(self.handler)
    def snapshot(self):
        n=self.ns;now=time.monotonic();a=n['audio'];esp=n['esp'];worker=n.get('_song_worker_process')
        alive=bool(worker and worker.is_alive());last=n.get('_song_diagnostics',{})
        self.buffer.add(f"[ESP] link={esp.connected} handshake={esp.handshake} age={now-esp.last_rx:.1f}s",'esp')
        self.buffer.add(f"[AUDIO] available={a.available} level={a.dbfs:.1f}dBFS gate={n['SONG_AUDIO_GATE_DBFS']:.1f} age={now-a.last_update:.1f}s music={a.music} error={a.last_error or '-'}",'audio')
        self.buffer.add(f"[VU] LED={n['VU_MODE']} sensitivity={n['VU_SENS']} level={a.level:.2f} peak={a.peak_level:.2f} gear={n['GEAR_VU_ACTIVE']}",'vu')
        self.buffer.add(f"[SONGWORKER] alive={alive} pid={getattr(worker,'pid',None)} submitted={n['_song_job_seq']} last_result={last.get('seq','-')} result_age={now-last['received']:.1f}s worker={last.get('elapsed',0):.2f}s" if last.get('received') else f"[SONGWORKER] alive={alive} pid={getattr(worker,'pid',None)} submitted={n['_song_job_seq']} no result received",'song_worker')
        self.buffer.add(f"[SONGSCAN] buffer={n['_song_audio_frames']/max(1,n['AUDIO_RATE']):.1f}s acquire={n['SONG_ACQUIRE_MIN_VOTES']} hold={n['SONG_HOLD_MIN_VOTES']} locked={a.song_title or '-'} candidate={last.get('candidate','-')} decision={last.get('decision','waiting')} confirmation={last.get('confirm',0)}",'song_scan')
    async def health(self):
        while not self.ns['stop_event'].is_set():
            try:self.snapshot()
            except Exception as exc:self.buffer.add('Diagnostic snapshot failed: '+str(exc),'errors')
            await asyncio.sleep(3)
    async def stream(self,request,reader,writer):
        import json
        if type(request.get('once',False)) is not bool:raise ValueError('Invalid once flag')
        cats=request.get('categories',list(CATEGORIES))
        if not isinstance(cats,list) or len(cats)>len(CATEGORIES) or any(c not in CATEGORIES for c in cats):raise ValueError('Unknown log categories')
        cursor=request.get('cursor')
        if cursor is not None and (type(cursor) is not int or cursor<0):raise ValueError('Invalid log cursor')
        if request.get('instance')!=self.buffer.instance:cursor=None
        while not self.ns['stop_event'].is_set() and not reader.at_eof():
            packet=self.buffer.packet(cursor,set(cats));cursor=packet['cursor']
            writer.write(json.dumps(packet).encode()+b'\n');await asyncio.wait_for(writer.drain(),3)
            if request.get('once'):return
            await asyncio.sleep(1)
CP_FILTER_LIVE_LOGS_PY
cat > "$STAGE/patch_runtime.py" <<'CP_FILTER_PATCH_RUNTIME_PY'
def patched(source):
    if "# COLLARPET_LIVE_LOGS_V1" not in source:raise ValueError("Install the live-logs update first")
    return source
CP_FILTER_PATCH_RUNTIME_PY
cat > "$STAGE/patch_dashboard.py" <<'CP_FILTER_PATCH_DASHBOARD_PY'
import ast
from pathlib import Path

def patched(source,payload):
    if '# PT35_LIVE_LOGS_V1' not in source:raise ValueError('Install the live-logs update first')
    tree=ast.parse(source)
    node=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='LOG_CATEGORIES' for t in n.targets))
    categories=ast.literal_eval(node.value)
    for key,title in [('luma','Luma'),('mind','Mind'),('neural','Neural'),('bleenv','BLE environment')]:
        if not any(id==key for id,label in categories):categories.insert(next((i for i,(id,label) in enumerate(categories) if id=='errors'),len(categories)),(key,title))
    lines=source.splitlines(keepends=True)
    lines[node.lineno-1:node.end_lineno]=['LOG_CATEGORIES='+repr(categories)+'\n']
    result=''.join(lines);ast.parse(result);return result
CP_FILTER_PATCH_DASHBOARD_PY
cat > "$STAGE/install.py" <<'CP_FILTER_INSTALL_PY'
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
from patch_dashboard import patched as dashboard_patch

LIVE=Path('/home/jenna/collarpet/collarpet.py')
RUNTIME_PY='/home/jenna/mindtest/bin/python'
WRAPPER=Path('/usr/local/sbin/collarpet-service-control')
ROOT=Path('/usr/local/lib/collarpet-menu')
RULE=Path('/etc/sudoers.d/collarpet-menu')
LATEST=Path('/var/lib/collarpet-log-filters/latest')
UNIT='collarpet.service'
BACKUPS=Path('/var/backups/collarpet-log-filters')

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
    if '# COLLARPET_MENU_RPC_V1' not in WRAPPER.read_text():raise RuntimeError('Install the collar menu integration first')
    files={str(ROOT/'live_logs.py'):(payload/'live_logs.py').read_bytes()}
    files[str(LATEST)]=b''
    for filename in files:
        path=Path(filename)
        if path.is_symlink() or (path.exists() and not path.is_file()):raise RuntimeError('Unexpected target: '+filename)
    # Validate with the existing runtime Python before stopping it.
    check=payload/'candidate.py';check.write_bytes(candidate)
    run([RUNTIME_PY,'-m','py_compile',str(check),str(payload/'live_logs.py')])
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
    for name in ('install.py','patch_runtime.py','patch_dashboard.py'):shutil.copy2(payload/name,backup/name)
    say('Backup: '+str(backup))
    run(['systemd-run','--quiet','--collect','--unit='+state['timer'],'--on-active=5m',
         '/usr/bin/python3',str(backup/'install.py'),'--restore',str(backup),'--automatic'])
    try:
        if LIVE.read_bytes()!=original:raise RuntimeError('Runtime changed during preparation')
        say('Restarting only CollarPet to apply the Luma log filter and error classification fix...')
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
        for request in ({'op':'telemetry'},{'op':'logs','categories':['luma'],'once':True}):
            response=run(['sudo','-u','jenna','sudo','-n',str(WRAPPER),'menu'],input=json.dumps(request)+'\n',timeout=12)
            if not json.loads(response.stdout).get('ok'):raise RuntimeError('Telemetry/log RPC verification failed')
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
    say('Rework installed; live menu verified. Main unit and BLE services preserved.')
    say('Rollback: sudo bash update-collarpet-log-filters.sh collarpet --rollback')

def pt35(payload,rollback=False):
    if os.geteuid()==0:raise RuntimeError('Run PT35 mode without sudo')
    base=Path.home()/'.local/share/collarpet-link';target=base/'dashboard.py';latest=base/'log-filters-dashboard-backup'
    if not target.is_file() or target.is_symlink():raise RuntimeError('Expected installed dashboard')
    if rollback:
        backup=Path(latest.read_text().strip())
        if backup.parent!=base or not backup.name.startswith('dashboard.py.before-log-filters-'):raise RuntimeError('Unexpected backup')
        write(target,backup.read_bytes(),0o755,(os.getuid(),os.getgid()));say('Previous dashboard restored. Reopen the app.');return
    source=target.read_text();candidate=dashboard_patch(source,payload)
    if candidate==source:say('Live logs already installed. Reopen the app.');return
    fd,backup=tempfile.mkstemp(prefix='dashboard.py.before-log-filters-',dir=base);os.close(fd);shutil.copy2(target,backup)
    write(target,candidate.encode(),target.stat().st_mode&0o777,(os.getuid(),os.getgid()))
    latest.write_text(backup+'\n')
    say('Live logs installed. Close and reopen Collar Pet. Backup: '+backup)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',nargs='?',choices=['collarpet','pt35']);parser.add_argument('--payload');parser.add_argument('--rollback',action='store_true');parser.add_argument('--restore');parser.add_argument('--automatic',action='store_true')
    args=parser.parse_args();payload=Path(args.payload or Path(__file__).parent)
    if args.mode=='pt35':pt35(payload,args.rollback);return
    if os.geteuid()!=0:raise SystemExit('Use sudo for collarpet mode.')
    if args.mode!='collarpet' and not args.restore:raise SystemExit('Choose collarpet mode.')
    with open('/run/collarpet-menu-install.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if args.restore:restore(args.restore,args.automatic)
        elif args.rollback:restore(LATEST.read_text().strip())
        else:collar(payload)

if __name__=='__main__':
    try:main()
    except Exception as exc:raise SystemExit('ERROR: '+str(exc))
CP_FILTER_INSTALL_PY
python3 "$STAGE/install.py" --payload "$STAGE" "$@"
