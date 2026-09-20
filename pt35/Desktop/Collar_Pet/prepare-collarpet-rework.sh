#!/usr/bin/env bash
set -Eeuo pipefail
MODE=${1:-}
[[ "$MODE" == pt35 || "$MODE" == collect ]] || { echo 'Use: bash prepare-collarpet-rework.sh pt35, or sudo bash prepare-collarpet-rework.sh collect on the collar'; exit 2; }
[[ "$MODE" != pt35 || $EUID != 0 ]] || { echo 'PT35 mode: run without sudo.'; exit 1; }
python3 - "$MODE" <<'PY'
import ast
import datetime
import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile

MODE=sys.argv[1]
BUTTONS="# PT35_CONTEXT_BUTTONS_V1\n_original_update_buttons=update_buttons\n_original_rescue_result=rescue_result\n\ndef layout_context_buttons():\n    children=bar.winfo_children()\n    def named(text):\n        return next((w for w in children if w.cget('text')==text),None)\n    connect=named('CONNECT')\n    common=[named('PAIR SSH'),named('REFRESH'),named('X')]\n    # Offline: use the two main action slots for connection and rescue.\n    # Online: use those same slots for the service action and collar menu.\n    if last_ip:\n        primary=[app_button,menu_button] if app_state=='active' else [app_button,rescue_button]\n        if app_state=='active' and rescue_active:primary.append(rescue_button)\n    else:\n        primary=[connect,rescue_button]\n    visible=[w for w in primary+common if w is not None]\n    for column in range(len(children)):\n        bar.columnconfigure(column,weight=0,uniform='',minsize=0)\n    for widget in children:widget.grid_remove()\n    for column,widget in enumerate(visible):\n        close=widget.cget('text')=='X'\n        bar.columnconfigure(column,weight=0 if close else 1,uniform='' if close else 'actions')\n        widget.grid(row=0,column=column,sticky='ew',padx=2)\n\ndef update_buttons():\n    _original_update_buttons()\n    layout_context_buttons()\n\ndef rescue_result(result,error):\n    _original_rescue_result(result,error)\n    layout_context_buttons()\n\n"
if MODE=='pt35':
    target=Path.home()/'.local/share/collarpet-link/dashboard.py'
    if not target.is_file() or target.is_symlink():raise SystemExit('Expected an installed dashboard.')
    source=target.read_text()
    if '# PT35_CONTEXT_BUTTONS_V1' in source:raise SystemExit('Context-sensitive buttons already installed. Reopen Collar Pet.')
    if '# PT35_MANUAL_RESCUE_V1' not in source:raise SystemExit('Install the manual-rescue update first. No changes made.')
    anchor="button('CONNECT',lambda:terminal('cp-connect'))"
    if source.count(anchor)!=1:raise SystemExit('Unexpected dashboard version; no changes made.')
    source=source.replace(anchor,BUTTONS+'\n'+anchor,1)
    ast.parse(source)
    fd,backup=tempfile.mkstemp(prefix='dashboard.py.before-context-',dir=target.parent)
    os.close(fd);shutil.copy2(target,backup)
    fd,temp=tempfile.mkstemp(prefix='.context-buttons-',dir=target.parent)
    try:
        with os.fdopen(fd,'w') as stream:stream.write(source)
        os.chmod(temp,target.stat().st_mode&0o777);os.replace(temp,target)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    print('Offline buttons: CONNECT, RESCUE, PAIR SSH, REFRESH, X.')
    print('Connected: START/RESTART and MENU use the main slots; STOP AP remains accessible while rescue is active.')
    print('Close and reopen Collar Pet. Backup: '+backup)
else:
    if os.geteuid()!=0:raise SystemExit('Run collect mode on the Orange Pi with sudo.')
    owner=pwd.getpwnam('jenna');home=Path(owner.pw_dir)
    output=home/('collarpet-rework-info-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip')
    errors=[]
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for file in [home/'collarpet/collarpet.py',Path('/usr/local/lib/collarpet-menu/runtime_menu.py'),
                     home/'collarpet/state/gear.json',Path('/run/collarpet/display.png')]:
            if file.is_file():archive.write(file,'current/'+file.name)
            else:errors.append('Missing: '+str(file))
        assets=home/'collarpet/assets/wolves'
        for file in sorted(assets.glob('wolf_*.png')):
            if file.is_file() and file.stat().st_size<2000000:archive.write(file,'wolves/'+file.name)
        try:
            result=subprocess.run(['journalctl','-u','collarpet.service','--since','15 minutes ago','--no-pager','-o','cat','-n','12000'],capture_output=True,text=True,timeout=20)
            lines=[line for line in result.stdout.splitlines() if any(tag in line for tag in ('[SONG','[NOWPLAYING]','[AUDIO','[TAIL','[EARS','[EPD','[REMOTE CMD]'))]
            archive.writestr('recent-diagnostics.log','\n'.join(lines)+'\n')
            if result.returncode:errors.append(result.stderr)
        except Exception as exc:errors.append(str(exc))
        try:
            result=subprocess.run(['systemctl','show','collarpet.service','-p','Environment','--value'],capture_output=True,text=True,timeout=10)
            selected=[value for value in shlex.split(result.stdout) if value.startswith(('COLLARPET_SONG_','COLLARPET_AUDIO_','COLLARPET_TAIL_','COLLARPET_EARS_','COLLARPET_EPAPER='))]
            archive.writestr('effective-settings.txt','\n'.join(selected)+'\n')
        except Exception as exc:errors.append(str(exc))
        archive.writestr('collection-notes.txt','Read-only collection. No services restarted, settings changed, or audio recorded.\n'+'\n'.join(errors))
    os.chmod(output,0o600);os.chown(output,owner.pw_uid,owner.pw_gid)
    print('Read-only diagnostic bundle: '+str(output))
    print('Upload this ZIP, ideally collected just after a false song detection in a quiet room.')
PY
