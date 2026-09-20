#!/usr/bin/env bash
set -Eeuo pipefail
if [[ $EUID == 0 ]]; then
    echo 'Run this on PT35 as jenna, without sudo.'
    exit 1
fi
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cat > "$STAGE/rescue.py" <<'PT35_MANUAL_RESCUE_PY'
#!/usr/bin/python3
"""Explicit PT35 rescue hotspot control. Status never changes networking."""
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import uuid

HOME=Path.home()
STATE=HOME/'.local/state/collarpet/rescue.json'

def nm(*args):
    p=subprocess.run(['nmcli','--wait','20',*args],capture_output=True,text=True,timeout=25,env={**os.environ,'LC_ALL':'C'})
    if p.returncode:raise RuntimeError(p.stderr.strip() or 'NetworkManager command failed')
    return p.stdout.strip()

def fields(line):
    result=[''];escaped=False
    for char in line:
        if escaped:result[-1]+=char;escaped=False
        elif char=='\\':escaped=True
        elif char==':':result.append('')
        else:result[-1]+=char
    if escaped:result[-1]+='\\'
    return result

def cfg():
    result={}
    for line in (HOME/'.config/collarpet/link.conf').read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1);parts=shlex.split(value,comments=True)
            if len(parts)==1:result[key.strip()]=parts[0]
    return result

def profiles(c):
    name=c.get('RESCUE_CONNECTION_NAME','CollarPet-Service');ssid=c.get('RESCUE_SSID','CollarPet-Service')
    matches=[]
    for line in nm('-t','-f','UUID,NAME,TYPE','connection','show').splitlines():
        row=fields(line)
        if len(row)!=3 or row[1]!=name:continue
        id=row[0]
        if row[2] not in ('wifi','802-11-wireless'):raise RuntimeError('Rescue profile name belongs to a different connection')
        values=nm('-g','802-11-wireless.mode,802-11-wireless.ssid','connection','show','uuid',id).splitlines()
        values=[fields(v)[0] for v in values]
        if values!=['ap',ssid]:raise RuntimeError('Rescue profile name exists with different Wi-Fi settings; left unchanged')
        matches.append(id)
    if len(matches)>1:raise RuntimeError('Multiple rescue profiles share the name; resolve duplicates first')
    return matches

def ensure_manual(id):
    value=nm('-g','connection.autoconnect','connection','show','uuid',id).strip().lower()
    if value=='no':return
    if value!='yes':raise RuntimeError('Could not read rescue autoconnect setting; no changes made')
    nm('connection','modify','uuid',id,'connection.autoconnect','no')

def active():
    result={}
    for line in nm('-t','-f','UUID,DEVICE','connection','show','--active').splitlines():
        row=fields(line)
        if len(row)==2:result[row[0]]=row[1]
    return result

def save(state):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.rescue-',dir=STATE.parent)
    try:
        with os.fdopen(fd,'w') as stream:json.dump(state,stream)
        os.chmod(temp,0o600);os.replace(temp,STATE)
    finally:
        if os.path.exists(temp):os.unlink(temp)

def operate(action):
    c=cfg();ids=profiles(c);id=ids[0] if ids else None
    current=active();running=bool(id and id in current)
    ssid=c.get('RESCUE_SSID','CollarPet-Service')
    if action=='status':return {'ok':True,'active':running,'ssid':ssid}
    if action=='prepare':
        if id:ensure_manual(id)
        return {'ok':True,'active':running,'message':'Existing rescue profile is manual-only.' if id else 'No saved rescue profile; nothing activated.'}
    if action=='stop':
        if not running:return {'ok':True,'active':False,'message':'Rescue Wi-Fi is already off.'}
        ensure_manual(id)
        state=json.loads(STATE.read_text()) if STATE.exists() else {}
        nm('connection','down','uuid',id)
        message='Rescue Wi-Fi stopped.'
        if state.get('profile')==id and state.get('previous'):
            try:
                nm('connection','up','uuid',state['previous'],'ifname',state['device'])
                message+=' Previous Wi-Fi restored.'
            except Exception:message+=' Previous Wi-Fi is unavailable; select a network from the Wi-Fi menu.'
        save({})
        return {'ok':True,'active':False,'message':message}
    if action!='start':raise ValueError('Use start, stop, status, or prepare')
    if running:
        ensure_manual(id)
        return {'ok':True,'active':True,'message':'Rescue Wi-Fi is already on: '+ssid}
    devices=[]
    for line in nm('-t','-f','DEVICE,TYPE','device','status').splitlines():
        row=fields(line)
        if len(row)==2 and row[1]=='wifi':devices.append(row[0])
    iface=c.get('RESCUE_WIFI_IFACE')
    if iface and iface not in devices:raise RuntimeError('Configured rescue Wi-Fi interface is unavailable')
    if not iface:
        iface=next((d for d in devices if nm('-g','WIFI-PROPERTIES.AP','device','show',d)=='yes'),None)
    if not iface:raise RuntimeError('No hotspot-capable Wi-Fi interface found')
    previous=next((key for key,device in current.items() if device==iface and key!=id),None)
    password=c.get('RESCUE_PSK','')
    if not 8<=len(password)<=63:raise RuntimeError('RESCUE_PSK must contain 8–63 characters')
    settings=['connection.autoconnect','no','802-11-wireless.mode','ap','802-11-wireless.band','bg',
              '802-11-wireless-security.key-mgmt','wpa-psk','802-11-wireless-security.psk',password,
              'ipv4.method','shared','ipv6.method','disabled']
    if not id:
        id=str(uuid.uuid4())
        nm('connection','add','type','wifi','ifname',iface,'con-name',c.get('RESCUE_CONNECTION_NAME','CollarPet-Service'),
           'ssid',ssid,'connection.uuid',id,*settings)
    else:ensure_manual(id)
    save({'profile':id,'device':iface,'previous':previous})
    try:nm('connection','up','uuid',id,'ifname',iface)
    except Exception as exc:
        # A failed switch should restore the previous network where possible.
        if previous:
            try:nm('connection','up','uuid',previous,'ifname',iface)
            except Exception:pass
        raise RuntimeError('Could not start rescue Wi-Fi: '+str(exc))
    return {'ok':True,'active':True,'ssid':ssid,'message':'Rescue Wi-Fi on: '+ssid+'. Waiting for CollarPet; it stays on until STOP AP.'}

def main():
    if len(sys.argv)!=2 or sys.argv[1] not in ('start','stop','status','prepare'):raise ValueError('Use cp-rescue {start|stop|status|prepare}')
    STATE.parent.mkdir(parents=True,exist_ok=True)
    with (STATE.parent/'rescue.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        print(json.dumps(operate(sys.argv[1])))

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc)}));sys.exit(1)
PT35_MANUAL_RESCUE_PY
cat > "$STAGE/cp-connect" <<'PT35_MANUAL_CP_CONNECT'
#!/usr/bin/env bash
set -Eeuo pipefail
source "$HOME/.config/collarpet/link.conf"
KEY="$HOME/.ssh/id_ed25519_collarpet"

discover_ip() {
    local j
    j="$(python3 "$HOME/.local/share/collarpet-link/discover.py" "$HELLO_PORT" "${1:-3}" 2>/dev/null || true)"
    [[ -n "$j" ]] || return 1
    printf '%s' "$j" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("_source_ip") or d.get("ip",""))'
}

ssh_args=(-p "$COLLARPET_SSH_PORT")
[[ -f "$KEY" ]] && ssh_args+=(-i "$KEY")

echo "Looking for Collar Pet on the current LAN..."
if IP="$(discover_ip 4)" && [[ -n "$IP" ]]; then
    echo "Found Collar Pet at $IP"
    exec ssh "${ssh_args[@]}" "$COLLARPET_USER@$IP"
fi

echo "CollarPet is not reachable on the current network yet. Wi-Fi was left unchanged."
echo "Wait for CollarPet to finish booting and retry, or use RESCUE in the dashboard."
exit 3

PT35_MANUAL_CP_CONNECT
cat > "$STAGE/ui.txt" <<'PT35_MANUAL_UI_TXT'
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


PT35_MANUAL_UI_TXT
cat > "$STAGE/patch.py" <<'PT35_MANUAL_PATCH_PY'
import ast
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import json
import sys

def patched(source,ui):
    if '# PT35_MANUAL_RESCUE_V1' in source:return source
    anchor="button('CONNECT',lambda:terminal('cp-connect'))"
    if '# PT35_COLLAR_MENU_DASHBOARD_V1' not in source or source.count(anchor)!=1:
        raise ValueError('Expected the current menu dashboard; no changes made.')
    source=source.replace(anchor,ui+'\n'+anchor+"\nrescue_button=button('RESCUE',rescue_toggle)\nroot.after(0,poll_rescue)",1)
    source=source.replace('CONNECT can create the rescue hotspot.','Nearby via BLE. Use RESCUE to start service Wi-Fi.')
    source=source.replace('CollarPet not reachable. Use CONNECT or REFRESH.','Waiting for CollarPet. REFRESH retries; RESCUE starts service Wi-Fi.')
    ast.parse(source)
    return source

def main(payload):
    home=Path.home();base=home/'.local/share/collarpet-link';binary=home/'.local/bin'
    dashboard=base/'dashboard.py';connect=binary/'cp-connect';rescue=binary/'cp-rescue'
    for target in (dashboard,connect,rescue):
        if target.is_symlink() or (target.exists() and not target.is_file()):raise SystemExit('Unexpected target: '+str(target))
    candidate=patched(dashboard.read_text(),(payload/'ui.txt').read_text())
    ast.parse((payload/'rescue.py').read_text())
    subprocess.run(['bash','-n',str(payload/'cp-connect')],check=True)
    backup=Path(tempfile.mkdtemp(prefix='manual-rescue-backup-',dir=base))
    for target in (dashboard,connect,rescue):
        if target.exists():shutil.copy2(target,backup/target.name)
    # Disable automatic activation of the matching saved AP only. Never switch Wi-Fi here.
    check=subprocess.run(['/usr/bin/python3',str(payload/'rescue.py'),'prepare'],capture_output=True,text=True,timeout=100)
    try:result=json.loads(check.stdout)
    except ValueError:raise SystemExit('Could not check rescue profile: '+(check.stderr or check.stdout)[-400:])
    if not result.get('ok'):raise SystemExit('No files changed: '+result.get('error','profile check failed'))
    print(result['message'])
    targets={dashboard:candidate,connect:(payload/'cp-connect').read_text(),rescue:(payload/'rescue.py').read_text()}
    written=[]
    try:
        for target,text in targets.items():
            target.parent.mkdir(parents=True,exist_ok=True)
            fd,temp=tempfile.mkstemp(prefix='.manual-rescue-',dir=target.parent)
            try:
                with os.fdopen(fd,'w') as stream:stream.write(text)
                os.chmod(temp,0o755);os.replace(temp,target);written.append(target)
            finally:
                if os.path.exists(temp):os.unlink(temp)
    except BaseException:
        for target in written:
            old=backup/target.name
            if old.exists():shutil.copy2(old,target)
            else:target.unlink(missing_ok=True)
        raise
    print('Installed. Close and reopen Collar Pet. CONNECT never starts a hotspot.')
    print('RESCUE starts it; STOP AP stops it and attempts to restore previous Wi-Fi.')
    print('The hotspot stays on when the dashboard or SSH window closes. Stop it with STOP AP or cp-rescue stop.')
    print('Backup: '+str(backup))

if __name__=='__main__':main(Path(sys.argv[1]))

PT35_MANUAL_PATCH_PY
python3 "$STAGE/patch.py" "$STAGE"
