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
import tempfile

path = Path.home() / '.local/share/collarpet-link/dashboard.py'
if not path.is_file() or path.is_symlink():
    raise SystemExit('Expected a regular PT35 dashboard at ' + str(path))
source = path.read_text()
marker = '# PT35_BUTTON_GRID_V1'
if marker in source:
    raise SystemExit('Button layout already updated. Close and reopen cp-dashboard.')
old = '''def button(text,cmd,w):
    b=tk.Button(bar,text=text,command=cmd,width=w,font=("DejaVu Sans",10,"bold"),relief="flat",bd=0,pady=5)
    b.pack(side="left",padx=3)'''
new = '''# PT35_BUTTON_GRID_V1
# Six equally sized action buttons; the close button gets only its own width.
# Pixel font size keeps desktop DPI scaling from clipping the labels.
def button(text,cmd,w):
    column=len(bar.winfo_children())
    close=text == "X"
    b=tk.Button(bar,text=text,command=cmd,width=2 if close else 1,
                font=("DejaVu Sans",-13,"bold"),relief="flat",bd=0,pady=7,padx=2)
    if close:
        bar.columnconfigure(column,weight=0)
    else:
        bar.columnconfigure(column,weight=1,uniform="actions")
    b.grid(row=0,column=column,sticky="ew",padx=2)'''
if source.count(old) != 1:
    raise SystemExit('Dashboard layout differs from the supplied version; no changes made.')
updated=source.replace(old,new,1)
ast.parse(updated)
fd, backup=tempfile.mkstemp(prefix='dashboard.py.before-buttons-',dir=path.parent)
os.close(fd)
shutil.copy2(path,backup)
fd, temporary=tempfile.mkstemp(prefix='.dashboard-buttons-',dir=path.parent)
try:
    with os.fdopen(fd,'w') as stream:
        stream.write(updated)
    os.chmod(temporary,path.stat().st_mode & 0o777)
    os.replace(temporary,path)
finally:
    if os.path.exists(temporary):os.unlink(temporary)
print('Button row updated. Backup: '+backup)
print('Close and reopen cp-dashboard to see the change.')
PY
