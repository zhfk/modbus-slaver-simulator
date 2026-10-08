import asyncio
import math
import socket
import struct
import time

import pytest
from pydantic import ValidationError
from pymodbus.client import AsyncModbusTcpClient

from simulator.codec import decode, encode
from simulator.errors import DomainError, ProtocolError
from simulator.models import Configuration, Device, Point, Strategy, thermal_template
from simulator.modbus import ModbusService
from simulator.runtime import ByteRing, DeviceRuntime, Runtime
from simulator.storage import Storage


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize(
    "kind,value",
    [
        ("Int16", -32768),
        ("UInt16", 65535),
        ("Int32", -2147483648),
        ("UInt32", 4294967295),
        ("Float32", -12.5),
        ("Float64", 1.5),
    ],
)
@pytest.mark.parametrize(
    "byte,word",
    [("big", "big"), ("little", "big"), ("big", "little"), ("little", "little")],
)
def test_encoding_roundtrip(kind, value, byte, word):
    p = Point(name="test", type=kind, initial=value, byte_order=byte, word_order=word)
    assert decode(p, encode(p, value)) == value


def test_scaled_rounding_and_overflow():
    p = Point(name="value", scale=0.1)
    assert encode(p, 25.35) == [254]
    assert decode(p, [254]) == pytest.approx(25.4)
    with pytest.raises(ValueError):
        encode(p, 7000)
    with pytest.raises(ValueError):
        encode(p, math.inf)


@pytest.mark.parametrize("value", [1e100, 1e308, -1e308])
def test_large_finite_integer_is_rejected_as_range_error(value):
    with pytest.raises(ValueError, match="范围"):
        encode(Point(name="value"), value)


def test_unencodable_strategy_preserves_last_value_and_other_points():
    bad = Point(
        name="bad", initial=7, strategy=Strategy(kind="fixed", params={"value": 1e100})
    )
    good = Point(
        name="good", address=1, strategy=Strategy(kind="fixed", params={"value": 42})
    )
    device = DeviceRuntime(Device(points=[bad, good]))
    device.status = "running"
    device.tick(device.stamp + 1)
    assert device.value(bad.id) == 7 and device.states[bad.id].error
    assert device.value(good.id) == 42 and not device.states[good.id].error


def test_trend_resumes_within_one_second_after_explicit_reset():
    point = Point(name="signal")
    runtime = Runtime()
    config = Device(points=[point])
    runtime.apply(Configuration(devices=[config]))
    device = runtime.get(config.id)
    runtime.subscribe(config.id, [point.id])
    device.status = "running"
    device.tick(device.stamp + 60)
    assert runtime.subscribe(config.id, [point.id])[point.id]
    device.status = "stopped"
    device.reset()
    runtime.subscribe(config.id, [point.id])
    device.status = "running"
    device.tick(device.stamp + 1.1)
    assert len(runtime.subscribe(config.id, [point.id])[point.id]) == 1


@pytest.mark.parametrize(
    "fields",
    [
        {"scale": 0},
        {"area": "input", "writable": True},
        {"area": "coil"},
        {"type": "Float32", "address": 65535},
        {"write_mode": "control", "strategy": Strategy(kind="fixed")},
    ],
)
def test_invalid_point_rejected(fields):
    with pytest.raises(ValidationError):
        Point(name="bad", **fields)


def test_overlap_cycle_and_invalid_expression():
    a = Point(name="a", id="a")
    b = Point(
        name="b", id="b", address=1, strategy=Strategy(kind="link", dependencies=["a"])
    )
    with pytest.raises(ValidationError):
        Device(points=[a, a])
    data = a.model_dump()
    data["strategy"] = Strategy(kind="link", dependencies=["b"]).model_dump()
    with pytest.raises(ValidationError):
        Device(points=[Point.model_validate(data), b])
    with pytest.raises(ValidationError):
        Strategy(
            kind="expression", params={"expression": "__import__('os').system('id')"}
        )


def test_partial_register_and_batch_write_atomicity():
    p = Point(name="float", type="Float32", writable=True, initial=1)
    d = DeviceRuntime(Device(points=[p]))
    d.write("holding", 1, [123], "external")
    assert d.raw(p.id) == [0x3F80, 123]
    assert d.states[p.id].hold
    prior = d.raw(p.id)
    with pytest.raises(ProtocolError):
        d.write("holding", 1, [9, 10], "external")
    assert d.raw(p.id) == prior
    with pytest.raises(DomainError):
        d.assign([{"id": p.id, "value": 3}, {"id": "missing", "value": 1}])
    assert d.raw(p.id) == prior


def test_non_finite_external_bits_preserved():
    p = Point(name="float", type="Float32", writable=True)
    d = DeviceRuntime(Device(points=[p]))
    d.write("holding", 0, [0x7F80, 0], "external")
    assert d.view(p.id)["quality"] == "non_finite"
    assert d.view(p.id)["value"] is None
    assert d.raw(p.id) == [0x7F80, 0]


def test_thermal_pause_hold_and_reset():
    c = thermal_template()
    d = DeviceRuntime(c)
    d.status = "running"
    command, target, actual, alarm = c.points
    d.assign([{"id": command.id, "value": True}, {"id": target.id, "value": 100}])
    start = d.stamp
    for i in range(1, 401):
        d.tick(start + i * 0.1)
    assert d.value(actual.id) > 80
    assert d.value(alarm.id)
    d.paused = True
    clock, current = d.clock, d.value(actual.id)
    d.tick(start + 50)
    assert d.clock == clock and d.value(actual.id) == current
    d.assign([{"id": actual.id, "value": 40}])
    d.paused = False
    d.tick(start + 51)
    assert d.value(actual.id) == 40
    d.control_points([actual.id], "resume")
    d.assign([{"id": command.id, "value": False}])
    for i in range(520, 820):
        d.tick(start + i * 0.1)
    assert d.value(actual.id) < 30
    d.status = "stopped"
    d.reset()
    assert d.value(actual.id) == 25


def test_temp_override_and_snapshot_random_sequence():
    p = Point(
        name="random",
        strategy=Strategy(kind="random", interval=0.01),
        write_mode="temporary",
        override_seconds=0.01,
    )
    d = DeviceRuntime(Device(points=[p]))
    d.status = "running"
    d.assign([{"id": p.id, "value": 7}])
    d.tick(time.monotonic())
    assert d.value(p.id) == 7
    saved = d.snapshot()
    fresh = DeviceRuntime(d.config)
    fresh.restore_snapshot(saved)
    assert fresh.raw(p.id) == d.raw(p.id)
    assert fresh.states[p.id].rng.random() == d.states[p.id].rng.random()


def test_ring_byte_and_count_bounds():
    ring = ByteRing(2, 1000)
    for i in range(10):
        ring.append({"i": i})
    assert len(ring.values()) == 2 and ring.dropped == 8
    ring.append({"large": "x" * 1001})
    assert ring.bytes <= 1000 and ring.dropped == 9


@pytest.mark.asyncio
async def test_modbus_all_functions_shared_endpoint_and_malformed():
    tcp_port = port()
    a = Device(
        id="a",
        port=tcp_port,
        unit_id=1,
        points=[
            Point(id="c", name="coil", area="coil", type="Bool", writable=True),
            Point(id="di", name="input", area="discrete", type="Bool", initial=True),
            Point(id="h", name="holding", writable=True),
            Point(id="i", name="input", area="input", initial=42),
        ],
    )
    b = Device(
        id="b", port=tcp_port, unit_id=2, points=[Point(name="second", initial=8)]
    )
    rt = Runtime()
    rt.apply(Configuration(devices=[a, b]))
    svc = ModbusService(rt)
    await svc.start("a")
    await svc.start("b")
    client = AsyncModbusTcpClient("127.0.0.1", port=tcp_port, timeout=0.5, retries=0)
    assert await client.connect()
    try:
        assert not (await client.write_coil(0, True, device_id=1)).isError()
        assert (await client.read_coils(0, count=1, device_id=1)).bits[0]
        assert not (await client.write_coils(0, [False], device_id=1)).isError()
        assert not (await client.read_coils(0, count=1, device_id=1)).bits[0]
        assert (await client.read_discrete_inputs(0, count=1, device_id=1)).bits[0]
        assert not (await client.write_register(0, 15, device_id=1)).isError()
        assert (
            await client.read_holding_registers(0, count=1, device_id=1)
        ).registers == [15]
        assert not (await client.write_registers(0, [16], device_id=1)).isError()
        assert (
            await client.read_input_registers(0, count=1, device_id=1)
        ).registers == [42]
        assert (
            await client.read_holding_registers(99, count=1, device_id=1)
        ).exception_code == 2
        await svc.stop("a")
        assert (
            await client.read_holding_registers(0, count=1, device_id=2)
        ).registers == [8]
        assert len(svc.endpoints) == 1
        reader, writer = await asyncio.open_connection("127.0.0.1", tcp_port)
        # Deliberately fragment the header and coalesce two complete ADUs.
        message = struct.pack(">HHHB", 7, 0, 6, 2) + bytes.fromhex("0300000001")
        writer.write(message[:3])
        await writer.drain()
        writer.write(message[3:] + message)
        await writer.drain()
        replies = await asyncio.wait_for(reader.readexactly(22), 1)
        assert replies[:2] == b"\x00\x07" and replies[11:13] == b"\x00\x07"
        writer.close()
        await writer.wait_closed()
        bad_reader, bad_writer = await asyncio.open_connection("127.0.0.1", tcp_port)
        bad_writer.write(struct.pack(">HHHB", 1, 0, 1000, 2))
        await bad_writer.drain()
        assert await asyncio.wait_for(bad_reader.read(), 1) == b""
        bad_writer.close()
    finally:
        client.close()
        await svc.close()
    assert not svc.endpoints and svc.connections == 0


@pytest.mark.asyncio
async def test_storage_commit_version_restore_and_retention(tmp_path):
    s = Storage(tmp_path)
    await s.open()
    rt = Runtime()
    rt.apply(s.config)
    d = thermal_template()
    c = Configuration(devices=[d])
    await s.commit(c, rt)
    assert s.config.version == 1 and (tmp_path / "backups/config.0.db").exists()
    with pytest.raises(DomainError):
        await s.commit(c, rt)
    rt.get(d.id).assign([{"id": d.points[1].id, "value": 77}])
    await s.snapshot(rt)
    await s.close(rt)
    fresh = Storage(tmp_path)
    await fresh.open()
    rt2 = Runtime()
    rt2.apply(fresh.config)
    await fresh.restore(rt2)
    assert rt2.get(d.id).value(d.points[1].id) == 77
    assert fresh.config.version == 1
    await fresh.close(rt2)


@pytest.mark.asyncio
async def test_optional_mask_read_write_and_identity():
    from simulator.modbus import Endpoint
    from simulator.models import Identity

    d = Device(
        id="d",
        functions=[3, 6, 22, 23],
        read_identity=True,
        identity=Identity(vendor="Vendor", product="Model", revision="1"),
        points=[
            Point(
                id="p",
                name="register",
                initial=0x00FF,
                writable=True,
                write_mode="control",
            )
        ],
    )
    rt = Runtime()
    rt.apply(Configuration(devices=[d]))
    service = ModbusService(rt)
    endpoint = Endpoint(("127.0.0.1", 0), rt, service)
    device = rt.get("d")
    assert endpoint.process(device, bytes.fromhex("160000ff000055")) == bytes.fromhex(
        "160000ff000055"
    )
    assert device.raw("p") == [0x55]
    response = endpoint.process(device, bytes.fromhex("17000000010000000102002a"))
    assert response == bytes.fromhex("1702002a") and device.raw("p") == [42]
    with pytest.raises(ProtocolError):
        endpoint.process(device, bytes.fromhex("170001000100000001020063"))
    assert device.raw("p") == [42]
    response = endpoint.process(device, bytes.fromhex("2b0e0100"))
    assert (
        response[:7] == bytes.fromhex("2b0e0181000003")
        and b"Vendor" in response
        and b"Model" in response
    )
    individual = endpoint.process(device, bytes.fromhex("2b0e0401"))
    assert individual[6] == 1 and individual[7] == 1 and individual.endswith(b"Model")
