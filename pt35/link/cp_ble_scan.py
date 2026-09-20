#!/usr/bin/env python3
import sys, time, json
import dbus

UUID = sys.argv[1].lower()
timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0

bus = dbus.SystemBus()
mgr = dbus.Interface(bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
objs = mgr.GetManagedObjects()

adapter_path = None
for path, ifaces in objs.items():
    if "org.bluez.Adapter1" in ifaces:
        adapter_path = path
        break

if not adapter_path:
    print(json.dumps({"found": False, "error": "No Bluetooth adapter"}))
    sys.exit(2)

adapter = dbus.Interface(bus.get_object("org.bluez", adapter_path), "org.bluez.Adapter1")
try:
    adapter.SetDiscoveryFilter({"Transport": dbus.String("le")})
except Exception:
    pass

started = False
try:
    try:
        adapter.StartDiscovery()
        started = True
    except dbus.exceptions.DBusException as e:
        if "InProgress" not in str(e):
            raise

    deadline = time.time() + timeout
    best = None
    while time.time() < deadline:
        objs = mgr.GetManagedObjects()
        for path, ifaces in objs.items():
            dev = ifaces.get("org.bluez.Device1")
            if not dev:
                continue
            sdata = dev.get("ServiceData", {})
            match = None
            for key, value in sdata.items():
                if str(key).lower() == UUID:
                    match = [int(x) for x in value]
                    break
            if match is None or len(match) < 7:
                continue

            version = match[0]
            flags = match[1]
            ip = ".".join(str(x) for x in match[2:6])
            if ip == "0.0.0.0":
                ip = ""
            signal = None if match[6] == 255 else match[6]
            result = {
                "found": True,
                "address": str(dev.get("Address", "")),
                "name": str(dev.get("Name", dev.get("Alias", ""))),
                "rssi": int(dev.get("RSSI", -127)),
                "version": version,
                "wifi_connected": bool(flags & 0x01),
                "ssh_enabled": bool(flags & 0x02),
                "ip": ip,
                "wifi_signal": signal
            }
            if best is None or result["rssi"] > best["rssi"]:
                best = result

        if best:
            print(json.dumps(best))
            sys.exit(0)
        time.sleep(0.35)

    print(json.dumps({"found": False}))
    sys.exit(1)
finally:
    if started:
        try:
            adapter.StopDiscovery()
        except Exception:
            pass
