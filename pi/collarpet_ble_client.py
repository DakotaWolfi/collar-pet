"""The subset of Bleak used by CollarPet, transported over a local Unix socket."""
import asyncio
import inspect
import json
import os
from types import SimpleNamespace

SOCKET = os.environ.get('COLLARPET_BLE_SOCKET', '/run/collarpet-ble/broker.sock')


class BrokerError(RuntimeError):
    pass


def invoke(callback, *args):
    if callback is None:
        return
    try:
        result = callback(*args)
        if inspect.isawaitable(result):
            asyncio.create_task(result)
    except Exception as exc:
        asyncio.get_running_loop().call_exception_handler({'message': 'BLE callback failed', 'exception': exc})


class Channel:
    def __init__(self, event=None, lost=None):
        self.event = event
        self.lost = lost
        self.writer = None
        self.reader_task = None
        self.pending = {}
        self.sequence = 0
        self.closing = False

    async def open(self):
        reader, self.writer = await asyncio.wait_for(asyncio.open_unix_connection(SOCKET, limit=131072), 3)
        self.reader_task = asyncio.create_task(self.read(reader))

    async def read(self, reader):
        try:
            while line := await reader.readline():
                message = json.loads(line)
                if 'event' in message:
                    invoke(self.event, message)
                else:
                    future = self.pending.pop(message.get('id'), None)
                    if future is not None and not future.done():
                        if message.get('ok'):
                            future.set_result(message.get('result'))
                        else:
                            future.set_exception(BrokerError(message.get('error', 'BLE request failed')))
        except (OSError, ValueError, asyncio.CancelledError):
            pass
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(BrokerError('BLE service disconnected'))
            self.pending.clear()
            if not self.closing:
                invoke(self.lost)

    async def request(self, op, **args):
        if self.writer is None or self.writer.is_closing() or self.reader_task.done():
            raise BrokerError('BLE service unavailable')
        self.sequence += 1
        key = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            self.writer.write((json.dumps({'id': key, 'op': op, **args}) + '\n').encode())
            await self.writer.drain()
            return await asyncio.wait_for(future, 45)
        except asyncio.TimeoutError:
            # Closing the session releases a request that completed after its caller gave up.
            await self.close()
            raise BrokerError('BLE service request timed out') from None
        finally:
            self.pending.pop(key, None)

    async def close(self):
        self.closing = True
        if self.writer is not None:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except OSError:
                pass
        if self.reader_task and self.reader_task is not asyncio.current_task():
            self.reader_task.cancel()
            await asyncio.gather(self.reader_task, return_exceptions=True)


class BleakScanner:
    def __init__(self, detection_callback=None):
        self.callback = detection_callback
        self.channel = None
        self.is_scanning = False

    def event(self, message):
        if message['event'] == 'scanner_fault':
            self.is_scanning = False
        if message['event'] != 'advertisement':
            return
        d, a = message['device'], message['advertisement']
        device = SimpleNamespace(**d)
        advertisement = SimpleNamespace(**{**a, 'manufacturer_data': {int(k): bytes.fromhex(v) for k,v in a['manufacturer_data'].items()}, 'service_data': {k: bytes.fromhex(v) for k,v in a['service_data'].items()}})
        invoke(self.callback, device, advertisement)

    async def start(self):
        if self.channel:
            await self.stop()
        self.channel = Channel(self.event, lambda: setattr(self, 'is_scanning', False))
        try:
            await self.channel.open()
            await self.channel.request('scan_start')
            self.is_scanning = True
        except BaseException:
            await self.channel.close()
            self.channel = None
            raise

    async def stop(self):
        channel, self.channel = self.channel, None
        self.is_scanning = False
        if channel:
            try:
                await channel.request('scan_stop')
            finally:
                await channel.close()

    @classmethod
    async def find_device_by_address(cls, address, timeout=4):
        future = asyncio.get_running_loop().create_future()
        def seen(device, advertisement):
            if device.address.lower() == address.lower() and not future.done():
                future.set_result(device)
        scanner = cls(seen)
        try:
            await scanner.start()
            try:
                return await asyncio.wait_for(future, timeout)
            except asyncio.TimeoutError:
                return None
        finally:
            await scanner.stop()


class BleakClient:
    def __init__(self, device, disconnected_callback=None):
        self.address = getattr(device, 'address', device)
        self.disconnected_callback = disconnected_callback
        self.channel = None
        self.is_connected = False
        self.services = []
        self.notifications = {}

    def lost(self):
        was_connected, self.is_connected = self.is_connected, False
        if was_connected:
            invoke(self.disconnected_callback, self)

    def event(self, message):
        if message['event'] == 'disconnected':
            self.lost()
        elif message['event'] == 'notification':
            uuid = message['uuid']
            invoke(self.notifications.get(uuid), SimpleNamespace(uuid=uuid), bytearray.fromhex(message['data']))

    async def connect(self):
        self.channel = Channel(self.event, self.lost)
        try:
            await self.channel.open()
            result = await self.channel.request('connect', address=self.address)
            self.services = [SimpleNamespace(uuid=uuid) for uuid in result['services']]
            self.is_connected = True
        except BaseException:
            await self.channel.close()
            self.channel = None
            raise

    async def disconnect(self):
        channel, self.channel = self.channel, None
        try:
            if channel:
                await channel.request('disconnect')
        finally:
            if channel:
                await channel.close()
            self.lost()

    async def read_gatt_char(self, uuid):
        return bytearray.fromhex(await self.channel.request('read', uuid=uuid))

    async def write_gatt_char(self, uuid, data, response=False):
        await self.channel.request('write', uuid=uuid, data=bytes(data).hex(), response=bool(response))

    async def start_notify(self, uuid, callback):
        uuid = str(uuid).lower()
        self.notifications[uuid] = callback
        try:
            await self.channel.request('notify', uuid=uuid)
        except BaseException:
            self.notifications.pop(uuid, None)
            raise

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

