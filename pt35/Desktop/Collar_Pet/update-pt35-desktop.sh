#!/usr/bin/env bash
set -Eeuo pipefail
if [[ $EUID == 0 ]]; then
    echo 'Run this on PT35 as jenna, without sudo.'
    exit 1
fi
python3 - <<'PY'
import ast
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from PIL import Image, ImageDraw

home=Path.home()
base=home/'.local/share/collarpet-link'
dashboard=base/'dashboard.py'
if not dashboard.is_file() or dashboard.is_symlink():raise SystemExit('Installed PT35 dashboard not found.')
source=dashboard.read_text()
if '# PT35_COLLAR_MENU_DASHBOARD_V1' not in source:raise SystemExit('This update needs the current menu dashboard. No changes made.')
marker='# PT35_FULLSCREEN_V1'
icon=base/'collarpet-icon.png'
if marker not in source:
    anchor="root=tk.Tk();root.title('Collar Pet');root.geometry('800x480');root.configure(bg=BG)"
    if source.count(anchor)!=1:raise SystemExit('Unexpected dashboard layout. No changes made.')
    source=source.replace(anchor,anchor+'''
# PT35_FULLSCREEN_V1
root.attributes('-fullscreen',True)
root.bind('<F11>',lambda event:root.attributes('-fullscreen',not root.attributes('-fullscreen')))
root.bind('<Escape>',lambda event:root.attributes('-fullscreen',False))
try:
    _app_icon=tk.PhotoImage(file=str(BASE/'collarpet-icon.png'))
    root.iconphoto(True,_app_icon)
except tk.TclError:
    pass
''',1)
ast.parse(source)
launcher='''#!/usr/bin/python3
"""Launch the dashboard independently so a launcher terminal can exit immediately."""
from pathlib import Path
import subprocess
home=Path.home()
cache=home/'.cache/collarpet'
cache.mkdir(parents=True,exist_ok=True)
log=cache/'dashboard.log'
if log.exists() and log.stat().st_size>1048576:
    log.replace(cache/'dashboard.previous.log')
with log.open('ab') as output:
    subprocess.Popen(['/usr/bin/python3',str(home/'.local/share/collarpet-link/dashboard.py')],
        stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True,cwd=str(home))
'''
ast.parse(launcher)
# Ask the desktop for its configured directory, including localized desktops.
desktop=home/'Desktop'
if shutil.which('xdg-user-dir'):
    result=subprocess.run(['xdg-user-dir','DESKTOP'],capture_output=True,text=True,timeout=5)
    if result.returncode==0 and result.stdout.strip():desktop=Path(result.stdout.strip())
binary=home/'.local/bin/cp-dashboard'
applications=home/'.local/share/applications'
# Quote according to Desktop Entry Exec rules, not shell rules.
def execquote(value):
    value=str(value).replace('\\','\\\\').replace('"','\\"').replace('`','\\`').replace('$','\\$').replace('%','%%')
    return '"'+value+'"'
entry='[Desktop Entry]\nType=Application\nVersion=1.0\nName=Collar Pet\nComment=CollarPet fullscreen dashboard and menu\nExec='+execquote(binary)+'\nIcon='+str(icon)+'\nTerminal=false\nStartupNotify=false\nCategories=Utility;Network;\n'
files={dashboard:source.encode(),binary:launcher.encode(),desktop/'Collar-Pet.desktop':entry.encode(),applications/'collarpet.desktop':entry.encode()}
for target in [*files,icon]:
    if target.is_symlink() or (target.exists() and not target.is_file()):raise SystemExit('Refusing non-regular target: '+str(target))
backup=Path(tempfile.mkdtemp(prefix='desktop-backup-',dir=base))
for index,target in enumerate([*files,icon]):
    if target.exists():shutil.copy2(target,backup/(str(index)+'-'+target.name))
(backup/'paths.txt').write_text('\n'.join(str(p) for p in [*files,icon])+'\n')
# A simple CollarPet launcher icon: a cyan paw above a collar and tag.
image=Image.new('RGBA',(256,256),(0,0,0,0));draw=ImageDraw.Draw(image)
draw.rounded_rectangle((4,4,252,252),radius=52,fill='#19222e',outline='#78d7ff',width=5)
for box in [(48,53,88,108),(91,29,129,88),(136,34,174,93),(179,64,212,114)]:draw.ellipse(box,fill='#78d7ff')
draw.rounded_rectangle((82,98,180,165),radius=33,fill='#78d7ff')
draw.arc((40,100,220,205),0,180,fill='#d9e2ef',width=13)
draw.rounded_rectangle((113,190,147,228),radius=7,fill='#78d7ff')
draw.ellipse((126,197,134,205),fill='#19222e')
image.save(icon)
for target,data in files.items():
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.collarpet-desktop-',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data)
        os.chmod(temp,0o755 if target==binary or target.suffix=='.desktop' else 0o644)
        os.replace(temp,target)
    finally:
        if os.path.exists(temp):os.unlink(temp)
if shutil.which('gio'):
    subprocess.run(['gio','set',str(desktop/'Collar-Pet.desktop'),'metadata::trusted','true'],capture_output=True,timeout=5)
if shutil.which('update-desktop-database'):
    subprocess.run(['update-desktop-database',str(applications)],capture_output=True,timeout=10)
print('Fullscreen and Collar Pet desktop icon installed.')
print('Close the current dashboard and launch Collar Pet from the desktop.')
print('If the desktop asks, choose Allow Launching. F11 toggles fullscreen; Escape leaves fullscreen.')
print('Backup: '+str(backup))
print('Startup errors, if any: '+str(home/'.cache/collarpet/dashboard.log'))
PY
