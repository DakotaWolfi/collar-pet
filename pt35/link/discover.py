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

