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

path=Path.home()/'.local/share/collarpet-link/dashboard.py'
if not path.is_file() or path.is_symlink():
    raise SystemExit('Expected a regular PT35 dashboard at '+str(path))
source=path.read_text()
if '# PT35_COMPACT_EPAPER_V1' in source:
    raise SystemExit('Compact preview already installed. Close and reopen cp-dashboard.')
if '# PT35_INLINE_EPAPER_V1' not in source:
    raise SystemExit('Install the inline e-paper dashboard first. No changes made.')
changes=[
    ('self.panel.grid(row=0,column=1,sticky="nsew",padx=(6,10),pady=10)',
     'self.panel.grid(row=0,column=1,sticky="new",padx=(4,6),pady=6)'),
    ('self.panel.rowconfigure(1,weight=1)','self.panel.rowconfigure(1,weight=0)'),
    ('highlightthickness=0,width=1,height=1)', 'highlightthickness=0,width=1,height=120)'),
    ('self.canvas.grid(row=1,column=0,sticky="nsew",pady=6)',
     'self.canvas.grid(row=1,column=0,sticky="ew",pady=2)'),
    ('width,height=self.canvas.winfo_width(),self.canvas.winfo_height()',
     '''# PT35_COMPACT_EPAPER_V1
        width=self.canvas.winfo_width()
        if width<=1:return
        # Follow the image aspect ratio, with a compact 180-pixel height cap.
        height=min(180,max(40,round((width-4)*self.frame.height/self.frame.width)+4)) if self.frame else 120
        if self.canvas.winfo_reqheight()!=height:
            self.canvas.configure(height=height)'''),
    ('scale=min(max(1,width-16)/self.frame.width,max(1,height-16)/self.frame.height)',
     'scale=min(max(1,width-4)/self.frame.width,max(1,height-4)/self.frame.height)'),
]
for old,new in changes:
    if source.count(old)!=1:
        raise SystemExit('Dashboard differs from the expected inline version; no changes made.')
    source=source.replace(old,new,1)
ast.parse(source)
fd,backup=tempfile.mkstemp(prefix='dashboard.py.before-compact-',dir=path.parent)
os.close(fd)
shutil.copy2(path,backup)
fd,temporary=tempfile.mkstemp(prefix='.dashboard-compact-',dir=path.parent)
try:
    with os.fdopen(fd,'w') as stream:stream.write(source)
    os.chmod(temporary,path.stat().st_mode & 0o777)
    os.replace(temporary,path)
finally:
    if os.path.exists(temporary):os.unlink(temporary)
print('Compact e-paper preview installed. Backup: '+backup)
print('Close and reopen cp-dashboard. Automatic refresh is unchanged.')
PY
