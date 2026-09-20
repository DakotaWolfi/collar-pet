#!/usr/bin/env bash
# Add BLE discovery to the PT35 LAN integration.
# CollarPet: sudo bash enable-collarpet-ble-discovery.sh collarpet
# PT35:      bash enable-collarpet-ble-discovery.sh pt35
set -Eeuo pipefail
MODE=${1:-}
[[ $# == 1 && ( "$MODE" == collarpet || "$MODE" == pt35 ) ]] || { echo 'Usage: enable-collarpet-ble-discovery.sh {collarpet|pt35}'; exit 2; }
if [[ "$MODE" == collarpet && $EUID != 0 ]]; then echo 'Use sudo for the CollarPet side.'; exit 1; fi
if [[ "$MODE" == pt35 && $EUID == 0 ]]; then echo 'Run the PT35 side as jenna, without sudo.'; exit 1; fi
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cat > "$STAGE/beacon.py" <<'CP_BLE_BEACON_PY'
#!/usr/bin/env python3
"""One static identity advertisement. Never scans, resets or power-cycles BlueZ."""
import signal
import sys
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

UUID = '8d13f2c0-27d7-4e31-9fd1-c0a1c011a001'
ADV = 'org.bluez.LEAdvertisement1'
PROPS = 'org.freedesktop.DBus.Properties'
MANAGER = 'org.bluez.LEAdvertisingManager1'


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    loop = GLib.MainLoop()
    state = {'registered': False, 'exit': 0}

    class Advertisement(dbus.service.Object):
        PATH = '/com/collarpet/pt35_identity'

        @dbus.service.method(PROPS, in_signature='s', out_signature='a{sv}')
        def GetAll(self, interface):
            if interface != ADV:
                return {}
            # One ServiceData field: 16-byte UUID + 3-byte marker + 2-byte
            # overhead = 21 bytes. No UUID-list duplication or local name.
            return {'Type': dbus.String('broadcast'),
                    'ServiceData': dbus.Dictionary({UUID: dbus.Array([3, 0x43, 0x50], signature='y')}, signature='sv')}

        @dbus.service.method(PROPS, in_signature='ss', out_signature='v')
        def Get(self, interface, prop):
            return self.GetAll(interface)[prop]

        @dbus.service.method(ADV, in_signature='', out_signature='')
        def Release(self):
            state['registered'] = False
            print('BlueZ released the beacon; LAN agent remains independent.', flush=True)
            loop.quit()

    obj = Advertisement(bus, Advertisement.PATH)
    objects = dbus.Interface(bus.get_object('org.bluez', '/'), 'org.freedesktop.DBus.ObjectManager').GetManagedObjects(timeout=8)
    adapter = next((p for p, interfaces in objects.items() if MANAGER in interfaces and interfaces.get('org.bluez.Adapter1', {}).get('Powered')), None)
    if adapter is None:
        print('No powered advertising-capable adapter. Beacon not started; no Bluetooth settings changed.', file=sys.stderr)
        return 1
    manager = dbus.Interface(bus.get_object('org.bluez', adapter), MANAGER)

    def success():
        state['registered'] = True
        print('CollarPet BLE identity beacon registered on ' + str(adapter), flush=True)

    def failure(error):
        state['exit'] = 1
        print('Beacon registration failed (no retry/reset): ' + str(error), file=sys.stderr, flush=True)
        loop.quit()

    def stop():
        loop.quit()
        return False

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, stop)
    manager.RegisterAdvertisement(obj.PATH, dbus.Dictionary({}, signature='sv'),
                                  reply_handler=success, error_handler=failure, timeout=10)
    try:
        loop.run()
    finally:
        if state['registered']:
            try:
                manager.UnregisterAdvertisement(obj.PATH, timeout=3)
            except dbus.DBusException:
                pass
    return state['exit']


if __name__ == '__main__':
    sys.exit(main())

CP_BLE_BEACON_PY
cat > "$STAGE/beacon.service" <<'CP_BLE_BEACON_SERVICE'
[Unit]
Description=CollarPet BLE identity beacon for PT35 discovery
After=bluetooth.service collarpet.service
Wants=bluetooth.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/lib/collarpet-link/beacon.py
Restart=no
TimeoutStopSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict

[Install]
WantedBy=multi-user.target

CP_BLE_BEACON_SERVICE
cat > "$STAGE/ble_scan.py" <<'CP_BLE_BLE_SCAN_PY'
#!/usr/bin/env python3
"""Fresh BLE observations only; cache entries alone do not prove presence."""
import json
from pathlib import Path
import sys

UUID = '8d13f2c0-27d7-4e31-9fd1-c0a1c011a001'


def match(props):
    uuids = {str(u).lower() for u in props.get('UUIDs', [])}
    data = {str(k).lower(): v for k, v in props.get('ServiceData', {}).items()}
    if UUID not in uuids and UUID not in data:
        return None
    return {'found': True, 'name': 'CollarPet', 'address': str(props.get('Address', '')),
            'rssi': int(props['RSSI']) if 'RSSI' in props else None}


def scan(seconds=4):
    import fcntl
    import dbus
    import dbus.mainloop.glib
    from gi.repository import GLib
    lock_path = Path.home() / '.cache' / 'collarpet'
    lock_path.mkdir(parents=True, exist_ok=True)
    with (lock_path / 'ble-scan.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'found': False, 'error': 'Another PT35 BLE scan is running'}
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()
        loop = GLib.MainLoop()
        manager = dbus.Interface(bus.get_object('org.bluez', '/'), 'org.freedesktop.DBus.ObjectManager')
        objects = manager.GetManagedObjects(timeout=3)
        adapter_path = next((p for p, i in objects.items() if i.get('org.bluez.Adapter1', {}).get('Powered')), None)
        if adapter_path is None:
            return {'found': False, 'error': 'No powered Bluetooth adapter'}
        known = {str(p): dict(i['org.bluez.Device1']) for p, i in objects.items() if 'org.bluez.Device1' in i}
        result = {'found': False}
        started = False

        def inspect(path, changes):
            if not str(path).startswith(str(adapter_path) + '/'):
                return
            props = known.setdefault(str(path), {})
            props.update(changes)
            found = match(props)
            if found:
                result.update(found)
                loop.quit()

        def added(path, interfaces):
            if 'org.bluez.Device1' in interfaces:
                inspect(path, interfaces['org.bluez.Device1'])

        def changed(interface, changes, invalidated, path=None):
            if interface == 'org.bluez.Device1':
                for key in invalidated:
                    known.get(str(path), {}).pop(str(key), None)
                # Ignore unrelated cached-device property changes.
                if any(key in changes for key in ('RSSI', 'ServiceData', 'ManufacturerData', 'UUIDs')):
                    inspect(path, changes)

        receiver1 = bus.add_signal_receiver(added, dbus_interface='org.freedesktop.DBus.ObjectManager', signal_name='InterfacesAdded', bus_name='org.bluez')
        receiver2 = bus.add_signal_receiver(changed, dbus_interface='org.freedesktop.DBus.Properties', signal_name='PropertiesChanged', bus_name='org.bluez', path_keyword='path')
        adapter = dbus.Interface(bus.get_object('org.bluez', adapter_path), 'org.bluez.Adapter1')
        try:
            adapter.SetDiscoveryFilter(dbus.Dictionary({'Transport': dbus.String('le'), 'DuplicateData': dbus.Boolean(True)}, signature='sv'), timeout=3)
            adapter.StartDiscovery(timeout=3)
            started = True
            def expire():
                loop.quit()
                return False
            timer = GLib.timeout_add(max(500, int(seconds * 1000)), expire)
            loop.run()
        finally:
            receiver1.remove()
            receiver2.remove()
            if started:
                try:
                    adapter.StopDiscovery(timeout=3)
                except dbus.DBusException:
                    pass
        return result


if __name__ == '__main__':
    try:
        result = scan(float(sys.argv[1]) if len(sys.argv) > 1 else 4)
    except Exception as exc:
        result = {'found': False, 'error': str(exc)}
    print(json.dumps(result))
    sys.exit(0 if result.get('found') else 1)

CP_BLE_BLE_SCAN_PY
cat > "$STAGE/discover.py" <<'CP_BLE_DISCOVER_PY'
#!/usr/bin/env python3
# CP_BLE_DISCOVERY_V1
"""BLE proximity first, then LAN discovery; never use a BLE address as SSH IP."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import time


def ble_observation():
    try:
        r = subprocess.run(['/usr/bin/python3', str(Path(__file__).with_name('ble_scan.py')), '4'],
                           text=True, capture_output=True, timeout=16)
        return json.loads(r.stdout)
    except Exception as exc:
        return {'found': False, 'error': str(exc)}


def lan_discovery(port, seconds, target="255.255.255.255"):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.3)
        deadline, next_send = time.monotonic() + seconds, 0
        while time.monotonic() < deadline:
            if time.monotonic() >= next_send:
                try:
                    sock.sendto(b'COLLARPET_HELLO v4', (target, port))
                except OSError:
                    pass
                next_send = time.monotonic() + 0.6
            try:
                data, addr = sock.recvfrom(8192)
                obj = json.loads(data)
                if isinstance(obj, dict) and obj.get('name') == 'CollarPet':
                    obj['_source_ip'] = addr[0]
                    obj['ip'] = addr[0]
                    return obj
            except (OSError, ValueError):
                pass
    return None


def discover(port, seconds):
    ble = ble_observation()
    lan = lan_discovery(port, seconds)
    if lan:
        lan['ble'] = ble
        lan['ble_only'] = False
        lan['discovery'] = 'ble+lan' if ble.get('found') else 'lan'
        return lan
    if ble.get('found'):
        return {'name': 'CollarPet', 'ble_only': True, 'discovery': 'ble', 'ble': ble, 'ip': '', '_source_ip': ''}
    return None


if __name__ == '__main__':
    result = discover(int(sys.argv[1]) if len(sys.argv) > 1 else 47842, float(sys.argv[2]) if len(sys.argv) > 2 else 3)
    if result:
        print(json.dumps(result))
    sys.exit(0 if result else 1)

CP_BLE_DISCOVER_PY
cat > "$STAGE/patch_pt35.py" <<'CP_BLE_PATCH_PT35_PY'
#!/usr/bin/env python3
from pathlib import Path
import ast
import sys


def patch_dashboard(source):
    if '# CP_BLE_DASHBOARD_V1' in source:
        return source
    anchors = {
        '    if not d:\n        state.config(text="OFFLINE",fg="#ff6b6b")': '    if not d:\n        last_ip=None\n        state.config(text="OFFLINE",fg="#ff6b6b")',
        '    last_ip=d.get("ip") or d.get("_source_ip")': '''    # CP_BLE_DASHBOARD_V1
    if d.get("ble_only"):
        last_ip=None
        state.config(text="NEARBY (BLE)",fg="#ffd166")
        for r in rows.values(): r.config(text="—")
        rows["Wi-Fi"].config(text="Not reachable from PT35")
        rssi=(d.get("ble") or {}).get("rssi")
        strength=f" • BLE {rssi} dBm" if rssi is not None else ""
        status.config(text="CollarPet nearby"+strength+" • CONNECT tries rescue Wi-Fi")
        return
    last_ip=d.get("ip") or d.get("_source_ip")''',
        '    status.config(text="Found via LAN" + (f" • signal {sig}%" if sig is not None else ""))': '''    ble=d.get("ble") or {}
    via="Found via BLE + LAN" if ble.get("found") else "Found via LAN"
    strength=f" • BLE {ble['rssi']} dBm" if ble.get("rssi") is not None else ""
    status.config(text=via + strength + (f" • Wi-Fi {sig}%" if sig is not None else ""))''',
        'LAN discovery only • Bluetooth untouched': 'BLE proximity discovery • Wi-Fi status and controls'
    }
    for old, new in anchors.items():
        if source.count(old) != 1:
            raise ValueError('Dashboard differs from the inspected LAN version; no files changed')
        source = source.replace(old, new, 1)
    compile(source, 'dashboard.py', 'exec')
    return source


if __name__ == '__main__':
    base, stage = map(Path, sys.argv[1:])
    old = (base / 'discover.py').read_text()
    if '# CP_BLE_DISCOVERY_V1' not in old and ('s.sendto(b"COLLARPET_HELLO v4"' not in old or 'port=int(sys.argv[1])' not in old):
        raise SystemExit('Unknown discovery helper: install the LAN PT35 integration first, or supply its current source.')
    result = patch_dashboard((base / 'dashboard.py').read_text())
    (stage / 'dashboard.py').write_text(result)
    print('PT35 dashboard patch validated.')

CP_BLE_PATCH_PT35_PY

python3 -m py_compile "$STAGE"/*.py
if [[ "$MODE" == collarpet ]]; then
    export PATH=/usr/sbin:/usr/bin:/sbin:/bin
    AGENT=/usr/local/lib/collarpet-link/agent.py
    [[ -f "$AGENT" ]] || { echo 'Install integrate-collarpet-pt35.sh first.'; exit 1; }
    if grep -Eq 'RegisterAdvertisement|import dbus|BleakScanner' "$AGENT"; then
        echo 'An older BLE agent is still installed. Run integrate-collarpet-pt35.sh first to avoid duplicate advertisers.'
        exit 1
    fi
    systemctl is-active --quiet bluetooth.service || { echo 'Bluetooth service is not active. Start it before enabling the beacon.'; exit 1; }
    systemctl cat collarpet.service > "$STAGE/runtime.before"
    if ! /usr/bin/python3 -c 'import dbus; from gi.repository import GLib' 2>/dev/null; then
        apt-get install -y python3-dbus python3-gi
    fi
    BACKUP=$(mktemp -d /root/collarpet-ble-backup-$(date +%Y%m%d-%H%M%S)-XXXXXX)
    for file in /usr/local/lib/collarpet-link/beacon.py /etc/systemd/system/collarpet-ble-beacon.service; do
        [[ ! -e "$file" ]] || cp -a --parents "$file" "$BACKUP"
    done
    install -o root -g root -m 0755 "$STAGE/beacon.py" /usr/local/lib/collarpet-link/beacon.py
    install -o root -g root -m 0644 "$STAGE/beacon.service" /etc/systemd/system/collarpet-ble-beacon.service
    systemctl daemon-reload
    systemctl enable collarpet-ble-beacon.service
    systemctl restart collarpet-ble-beacon.service
    sleep 2
    systemctl cat collarpet.service > "$STAGE/runtime.after"
    cmp "$STAGE/runtime.before" "$STAGE/runtime.after"
    echo "Backup: $BACKUP"
    systemctl --no-pager --full status collarpet-ble-beacon.service || true
    echo 'Check registration: journalctl -u collarpet-ble-beacon.service -n 15 --no-pager'
    echo 'The physical BLE test is: run cp-ble-scan on PT35 with CollarPet nearby.'
    echo 'Disable only the beacon if needed: sudo systemctl disable --now collarpet-ble-beacon.service'
else
    BASE="$HOME/.local/share/collarpet-link"
    BIN="$HOME/.local/bin"
    [[ -f "$BASE/discover.py" && -f "$BASE/dashboard.py" ]] || { echo 'Install the PT35 LAN dashboard first.'; exit 1; }
    python3 "$STAGE/patch_pt35.py" "$BASE" "$STAGE"
    if ! /usr/bin/python3 -c 'import dbus; from gi.repository import GLib' 2>/dev/null; then
        sudo apt-get install -y python3-dbus python3-gi
    fi
    BACKUP=$(mktemp -d "$HOME/collarpet-ble-backup-$(date +%Y%m%d-%H%M%S)-XXXXXX")
    for file in discover.py dashboard.py ble_scan.py; do
        [[ ! -e "$BASE/$file" ]] || cp -a "$BASE/$file" "$BACKUP/"
    done
    [[ ! -e "$BIN/cp-ble-scan" ]] || cp -a "$BIN/cp-ble-scan" "$BACKUP/"
    mkdir -p "$BIN"
    install -m 0755 "$STAGE/ble_scan.py" "$BASE/ble_scan.py"
    install -m 0755 "$STAGE/discover.py" "$BASE/discover.py"
    install -m 0755 "$STAGE/dashboard.py" "$BASE/dashboard.py"
    cat > "$BIN/cp-ble-scan" <<'SH'
#!/bin/sh
exec /usr/bin/python3 "$HOME/.local/share/collarpet-link/ble_scan.py" "${1:-4}"
SH
    chmod 755 "$BIN/cp-ble-scan"
    echo "Backup: $BACKUP"
    echo 'Close and reopen cp-dashboard. It now shows NEARBY (BLE) or BLE + LAN.'
    echo 'Try: cp-ble-scan; cp-discover; cp-dashboard'
    echo 'Rollback PT35: copy discover.py and dashboard.py from the backup above into ~/.local/share/collarpet-link/'
fi
