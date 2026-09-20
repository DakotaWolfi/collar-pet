#!/usr/bin/env bash
set -euo pipefail

FILE="$HOME/.local/bin/pt35-buttons.py"
cp "$FILE" "$FILE.bak"

python3 <<'PY'
from pathlib import Path

p = Path.home() / ".local/bin/pt35-buttons.py"
s = p.read_text()

old = """def run_command(cmd):
    if not cmd:
        return
    log(f\"run: {cmd}\")
    subprocess.Popen([\"bash\", \"-lc\", cmd], start_new_session=True)
"""

new = """def get_gui_env():
    env = os.environ.copy()
    wanted = {\"DISPLAY\", \"WAYLAND_DISPLAY\", \"XAUTHORITY\", \"DBUS_SESSION_BUS_ADDRESS\",
              \"XDG_RUNTIME_DIR\", \"XDG_SESSION_TYPE\", \"XDG_CURRENT_DESKTOP\", \"DESKTOP_SESSION\"}

    if env.get(\"DISPLAY\") or env.get(\"WAYLAND_DISPLAY\"):
        return env

    uid = os.getuid()
    for proc in Path(\"/proc\").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != uid:
                continue
            raw = (proc / \"environ\").read_bytes()
            penv = {}
            for item in raw.split(b\"\\0\"):
                if b\"=\" not in item:
                    continue
                k, v = item.split(b\"=\", 1)
                penv[k.decode(errors=\"ignore\")] = v.decode(errors=\"ignore\")
            if not (penv.get(\"DISPLAY\") or penv.get(\"WAYLAND_DISPLAY\")):
                continue
            for k in wanted:
                if penv.get(k):
                    env[k] = penv[k]
            return env
        except (PermissionError, FileNotFoundError, ProcessLookupError, OSError):
            pass
    return env

def run_command(cmd):
    if not cmd:
        return
    env = get_gui_env()
    log(f\"run: {cmd} DISPLAY={env.get('DISPLAY','')} WAYLAND_DISPLAY={env.get('WAYLAND_DISPLAY','')}\")
    subprocess.Popen([\"bash\", \"-lc\", cmd], env=env, start_new_session=True)
"""

if old not in s:
    raise SystemExit("Could not find run_command() in installed daemon; no changes made.")

if "from pathlib import Path" not in s:
    s = s.replace("import time\n", "import time\nfrom pathlib import Path\n", 1)

s = s.replace(old, new, 1)
s = s.replace(
    'subprocess.Popen(["wtype", "-k", key], start_new_session=True)',
    'subprocess.Popen(["wtype", "-k", key], env=get_gui_env(), start_new_session=True)'
)
s = s.replace(
    'subprocess.Popen(["xdotool", "key", xmap.get(key, key)], start_new_session=True)',
    'subprocess.Popen(["xdotool", "key", xmap.get(key, key)], env=get_gui_env(), start_new_session=True)'
)

p.write_text(s)
print("Patched", p)
PY

systemctl --user restart pt35-buttons.service

echo
echo "PT35 button daemon patched and restarted."
echo "Backup: $HOME/.local/bin/pt35-buttons.py.bak"
echo "Press Select and Start now."
