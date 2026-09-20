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

