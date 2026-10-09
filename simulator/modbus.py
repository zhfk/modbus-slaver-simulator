"""Bounded asyncio transport; PyModbus decodes validated PDUs.

Library datastore objects never escape the adapter. All data operations use
our single runtime, including partial register writes and policy validation.
"""

import asyncio
import contextlib
import random
import struct
import time
import uuid

from contextlib import asynccontextmanager

from pymodbus.pdu import DecodePDU

from .errors import DomainError, ProtocolError


class Endpoint:
    def __init__(self, key, runtime, owner):
        self.key, self.runtime, self.owner = key, runtime, owner
        self.units = {}
        self.server = None
        self.clients = {}
        self.tasks = set()
        self.last_request = None
        self.requests = self.errors = 0
        self.latencies = []
        self.decoder = DecodePDU(is_server=True)
        self.rng = random.Random(42)
        self.closing = False

    async def open(self):
        self.server = await asyncio.start_server(
            self.handle, *self.key, limit=1024, backlog=32
        )

    async def close(self):
        self.closing = True
        if self.server:
            self.server.close()
        for writer in list(self.clients):
            writer.transport.abort()
        tasks = [task for task in self.tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Python 3.12 waits for accepted connections in Server.wait_closed().
        # Close/cancel those connections first; idle clients must not keep a
        # stopped endpoint alive until their 300-second idle timeout.
        if self.server:
            await asyncio.wait_for(self.server.wait_closed(), 2)

    def process(self, device, pdu):
        fn = pdu[0]
        if fn == 43 and device.config.read_identity:
            if len(pdu) != 4 or pdu[1] != 14 or pdu[2] not in (1, 4):
                raise ProtocolError(3)
            identity = device.config.identity
            objects = [
                v.encode("utf-8")
                for v in (identity.vendor, identity.product, identity.revision)
            ]
            first = pdu[3]
            if first > 2:
                raise ProtocolError(2)
            indexes = range(first, 3) if pdu[2] == 1 else [first]
            result = bytes([43, 14, pdu[2], 0x81, 0, 0, len(indexes)])
            return result + b"".join(
                bytes([i, len(objects[i])]) + objects[i] for i in indexes
            )
        if fn not in device.config.functions:
            raise ProtocolError(1)
        if fn in (1, 2, 3, 4, 5, 6):
            if len(pdu) != 5:
                raise ProtocolError(3)
        elif fn in (15, 16):
            if len(pdu) < 6:
                raise ProtocolError(3)
        if fn == 22:
            if len(pdu) != 7:
                raise ProtocolError(3)
            if self.decoder.decode(pdu) is None:
                raise ProtocolError(3)
            address, and_mask, or_mask = struct.unpack(">HHH", pdu[1:])
            prior = device.read("holding", address, 1)[0]
            value = (prior & and_mask) | (or_mask & (~and_mask & 0xFFFF))
            device.write("holding", address, [value], "external")
            return pdu
        if fn == 23:
            if len(pdu) < 10:
                raise ProtocolError(3)
            read_address, read_count, write_address, write_count, size = struct.unpack(
                ">HHHHB", pdu[1:10]
            )
            if (
                not 1 <= read_count <= 125
                or not 1 <= write_count <= 121
                or size != write_count * 2
                or len(pdu) != 10 + size
            ):
                raise ProtocolError(3)
            if self.decoder.decode(pdu) is None:
                raise ProtocolError(3)
            device.read("holding", read_address, read_count)  # validate before mutation
            device.write(
                "holding",
                write_address,
                list(struct.unpack(">" + "H" * write_count, pdu[10:])),
                "external",
            )
            values = device.read("holding", read_address, read_count)
            return bytes([23, read_count * 2]) + struct.pack(
                ">" + "H" * read_count, *values
            )
        address, count = struct.unpack(">HH", pdu[1:5])
        if fn in (1, 2, 3, 4):
            if not 1 <= count <= (2000 if fn in (1, 2) else 125):
                raise ProtocolError(3)
        if fn == 5 and count not in (0, 0xFF00):
            raise ProtocolError(3)
        if fn in (15, 16):
            expected = (count + 7) // 8 if fn == 15 else count * 2
            if (
                not 1 <= count <= (1968 if fn == 15 else 123)
                or pdu[5] != expected
                or len(pdu) != 6 + expected
            ):
                raise ProtocolError(3)
        if self.decoder.decode(pdu) is None:
            raise ProtocolError(3)
        if fn in (1, 2):
            values = device.read("coil" if fn == 1 else "discrete", address, count)
            packed = bytearray((count + 7) // 8)
            for i, value in enumerate(values):
                if value:
                    packed[i // 8] |= 1 << (i % 8)
            return bytes([fn, len(packed)]) + packed
        if fn in (3, 4):
            values = device.read("holding" if fn == 3 else "input", address, count)
            return bytes([fn, len(values) * 2]) + struct.pack(
                ">" + "H" * len(values), *values
            )
        if fn in (5, 6):
            device.write(
                "coil" if fn == 5 else "holding",
                address,
                [int(count == 0xFF00) if fn == 5 else count],
                "external",
            )
            return pdu
        if fn == 15:
            values = [int(bool(pdu[6 + i // 8] & (1 << (i % 8)))) for i in range(count)]
            device.write("coil", address, values, "external")
        elif fn == 16:
            device.write(
                "holding",
                address,
                list(struct.unpack(">" + "H" * count, pdu[6:])),
                "external",
            )
        return pdu[:5]

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        if self.closing:
            writer.transport.abort()
            self.tasks.discard(task)
            return
        configs = [self.runtime.get(key).config for key in self.units.values()]
        limit = min((c.max_connections for c in configs), default=1)
        if len(self.clients) >= limit or self.owner.connections >= 64:
            writer.close()
            await writer.wait_closed()
            self.tasks.discard(task)
            return
        peer = writer.get_extra_info("peername")
        client = {
            "id": uuid.uuid4().hex,
            "host": str(peer[0]) if peer else "未知",
            "port": peer[1] if peer else None,
            "connected_at": time.time(),
            "last_request": None,
            "last_unit_id": None,
            "requests": 0,
            "devices": {},
        }
        self.clients[writer] = client
        try:
            while True:
                configs = [self.runtime.get(key).config for key in self.units.values()]
                idle = min((c.idle_seconds for c in configs), default=300)
                frame_timeout = min((c.frame_seconds for c in configs), default=5)
                # The first byte may wait for idle time. The remainder has a
                # separate deadline, so slowly streamed partial headers expire.
                first = await asyncio.wait_for(reader.readexactly(1), idle)
                header = first + await asyncio.wait_for(
                    reader.readexactly(6), frame_timeout
                )
                tx, protocol, length, unit = struct.unpack(">HHHB", header)
                if protocol != 0 or not 2 <= length <= 254:
                    break
                pdu = await asyncio.wait_for(
                    reader.readexactly(length - 1), frame_timeout
                )
                started = time.monotonic()
                self.last_request, self.requests = time.time(), self.requests + 1
                key = self.units.get(unit)
                client["last_request"] = self.last_request
                client["last_unit_id"] = unit
                client["requests"] += 1
                if key is not None:
                    # At most 256 Unit IDs per TCP connection, including reuse.
                    visit = client["devices"].get(unit)
                    if visit is None or visit["device"] != key:
                        visit = {"device": key, "requests": 0, "last_request": None}
                        client["devices"][unit] = visit
                    visit["requests"] += 1
                    visit["last_request"] = self.last_request
                response, error = None, None
                if key is None:
                    error = "未知 Unit ID"
                    if any(c.unknown_unit == "exception" for c in configs):
                        response = bytes([pdu[0] | 0x80, 11])
                else:
                    device = self.runtime.get(key)
                    faults = device.config.faults
                    if self.rng.random() < faults.disconnect_rate:
                        break
                    if faults.delay_ms:
                        await asyncio.sleep(faults.delay_ms / 1000)
                    if self.rng.random() < faults.timeout_rate:
                        error = "故障注入：无响应"
                    elif device.status != "running" or self.units.get(unit) != key:
                        error = "设备已停止"
                    else:
                        try:
                            response = self.process(self.runtime.get(key), pdu)
                        except ProtocolError as exc:
                            response, error = (
                                bytes([pdu[0] | 0x80, exc.code]),
                                f"协议异常 {exc.code}",
                            )
                elapsed = (time.monotonic() - started) * 1000
                self.latencies.append(elapsed)
                if len(self.latencies) > 1000:
                    del self.latencies[: len(self.latencies) - 1000]
                if error:
                    self.errors += 1
                self.runtime.diagnostics.append(
                    {
                        "id": uuid.uuid4().hex,
                        "time": time.time(),
                        "device": key,
                        "endpoint": f"{self.key[0]}:{self.key[1]}",
                        "host": str(peer),
                        "unit_id": unit,
                        "function": pdu[0],
                        "address": int.from_bytes(pdu[1:3], "big")
                        if len(pdu) >= 3
                        else None,
                        "quantity": int.from_bytes(pdu[3:5], "big")
                        if len(pdu) >= 5
                        else None,
                        "ms": round(elapsed, 3),
                        "error": error,
                        "transaction_id": tx,
                        "request": (header + pdu).hex(),
                        "response": (
                            struct.pack(">HHHB", tx, 0, len(response) + 1, unit)
                            + response
                        ).hex()
                        if response
                        else "",
                    }
                )
                self.runtime.capture(self.runtime.diagnostics.latest())
                if response:
                    writer.write(
                        struct.pack(">HHHB", tx, 0, len(response) + 1, unit) + response
                    )
                    await asyncio.wait_for(writer.drain(), frame_timeout)
        except (asyncio.IncompleteReadError, TimeoutError, ConnectionError, OSError):
            pass
        finally:
            self.clients.pop(writer, None)
            writer.close()
            try:
                with contextlib.suppress(ConnectionError, OSError, TimeoutError):
                    await asyncio.wait_for(writer.wait_closed(), 1)
            finally:
                # close() may still be flushing to a peer that never reads.
                # Timeout or cancellation must release its transport as well.
                writer.transport.abort()
                self.tasks.discard(task)


class ModbusService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.endpoints = {}
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def limited_lock(self):
        try:
            await asyncio.wait_for(self.lock.acquire(), 2)
        except TimeoutError as exc:
            raise DomainError("设备控制繁忙，请稍后重试", 503) from exc
        try:
            yield
        finally:
            self.lock.release()

    @property
    def connections(self):
        return sum(len(e.clients) for e in self.endpoints.values())

    async def start(self, key):
        async with self.limited_lock():
            device = self.runtime.get(key)
            if device.status == "running":
                return
            if device.status in ("starting", "stopping"):
                raise DomainError("设备状态过渡中", 409)
            device.status, device.error = "starting", ""
            endpoint_key = (device.config.host, device.config.port)
            endpoint = self.endpoints.get(endpoint_key)
            try:
                if endpoint is None:
                    endpoint = Endpoint(endpoint_key, self.runtime, self)
                    endpoint.units[device.config.unit_id] = key
                    await endpoint.open()
                    self.endpoints[endpoint_key] = endpoint
                else:
                    endpoint.units[device.config.unit_id] = key
                device.status, device.stamp = "running", time.monotonic()
                self.runtime.event("start", key, "设备已启动")
            except OSError as exc:
                if endpoint:
                    await endpoint.close()
                device.status, device.error = "fault", str(exc)
                self.runtime.event("start_failed", key, str(exc))
                raise DomainError(
                    f"监听失败，请检查端口占用、IP 与权限：{exc}", 409
                ) from exc

    async def stop(self, key):
        async with self.limited_lock():
            device = self.runtime.get(key)
            device.status = "stopping"
            endpoint_key = (device.config.host, device.config.port)
            endpoint = self.endpoints.get(endpoint_key)
            if endpoint:
                endpoint.units.pop(device.config.unit_id, None)
                if not endpoint.units:
                    self.endpoints.pop(endpoint_key, None)
                    await endpoint.close()
            device.status = "stopped"
            self.runtime.event("stop", key, "设备已停止")

    async def close(self):
        for key in list(self.runtime.devices):
            await self.stop(key)

    def metrics(self):
        return [
            {
                "host": key[0],
                "port": key[1],
                "connections": len(e.clients),
                "units": list(e.units),
                "last_request": e.last_request,
                "requests": e.requests,
                "errors": e.errors,
                "p95_ms": sorted(e.latencies)[int((len(e.latencies) - 1) * 0.95)]
                if e.latencies
                else 0,
            }
            for key, e in self.endpoints.items()
        ]
