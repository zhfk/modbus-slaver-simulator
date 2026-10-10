import asyncio
import json
import socket
import struct

import pytest
from fastapi.testclient import TestClient

from simulator.api import create_app
from simulator.models import Configuration, Device, Faults, Point
from simulator.modbus import ModbusService
from simulator.runtime import Runtime
from simulator.storage import Storage


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as http:
        config = Configuration(
            devices=[
                Device(
                    id="d",
                    name="原设备",
                    points=[
                        Point(id="p", name="原点位", initial=7, writable=True),
                        Point(id="q", name="第二点位", address=1, initial=8),
                    ],
                ),
                Device(id="other", port=1503),
            ]
        )
        assert http.put("/api/config", json=config.model_dump()).status_code == 200
        yield http


def assign(client, value, **extra):
    return client.post(
        "/api/devices/d/assign", json={"items": [{"id": "p", "value": value}], **extra}
    )


def history(client):
    response = client.get("/api/devices/d/assignments")
    assert response.status_code == 200, response.text
    return response.json()


def test_success_failure_source_atomicity_and_no_read_audit(client):
    assert assign(client, 12, origin="web").status_code == 200
    assert assign(client, 12).status_code == 200  # Same value is still an operation.
    assert assign(client, 65536).status_code == 422
    assert (
        client.post(
            "/api/devices/d/assign",
            json={
                "items": [
                    {"id": "p", "value": 30},
                    {"id": "q", "value": -1},
                ]
            },
        ).status_code
        == 422
    )
    for _ in range(3):
        client.get("/api/devices/d/points")
        client.post("/api/devices/d/preview-value", json={"id": "p", "value": 20})
    rows = history(client)["items"]
    assert len(rows) == 4 and len({r["id"] for r in rows}) == 4
    assert [r["outcome"] for r in rows] == ["failed", "failed", "success", "success"]
    assert [r["origin"] for r in rows] == ["api", "api", "api", "web"]
    assert rows[0]["count"] == 2
    assert [(c["before"], c["after"]) for c in rows[0]["changes"]] == [(12, 12), (8, 8)]
    assert rows[0]["changes"][0]["requested"] == 30
    assert rows[-1]["changes"][0]["before_raw"] == [7]
    assert rows[-1]["changes"][0]["after_raw"] == [12]
    assert rows[-1]["config_version"] == 1
    assert client.get("/api/devices/other/assignments").json()["items"] == []
    assert client.get("/api/devices/missing/assignments").status_code == 404


@pytest.mark.parametrize(
    "items",
    [
        None,
        [],
        [{"id": {}, "value": 1}],
        [{"id": "missing", "value": 1}],
        [{"id": "p", "value": {"bad": [1]}}],
        [{"id": "p", "value": 1}, {"id": "p", "value": 2}],
    ],
)
def test_rejected_request_records_bounded_failure(client, items):
    assert client.post("/api/devices/d/assign", json={"items": items}).status_code in (
        404,
        422,
    )
    row = history(client)["items"][0]
    assert row["outcome"] == "failed" and row["error"]
    assert row["omitted"] == 0
    assert len({c["id"] for c in row["changes"]}) == len(row["changes"])
    assert all(c["before"] == c["after"] for c in row["changes"])
    assert client.app.state.runtime.get("d").value("p") == 7
    assert len(json.dumps(row)) < 4096


def test_cache_count_order_metadata_and_reset(client):
    for value in range(105):
        assert assign(client, value).status_code == 200
    rows = history(client)["items"]
    assert len(rows) == 100
    assert [r["changes"][0]["after"] for r in rows] == list(reversed(range(5, 105)))
    config = client.get("/api/config").json()
    config["devices"][0]["name"] = "新名称"
    config["devices"][0]["points"][0]["name"] = "新点位"
    assert client.put("/api/config", json=config).status_code == 200
    assert client.post("/api/devices/d/actions/reset").status_code == 200
    assert history(client)["items"] == rows
    store = client.app.state.context["storage"]
    client.portal.call(store.flush)
    assert len(client.portal.call(store.assignments, "d")) == 100


def test_history_survives_clean_restart_with_sampling_disabled(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        config = Configuration(
            devices=[Device(id="d", points=[Point(id="p", name="点")])]
        )
        assert client.put("/api/config", json=config.model_dump()).status_code == 200
        assert not client.get("/api/config").json()["settings"]["history_enabled"]
        assign(client, 37)
        rows = history(client)["items"]
    with TestClient(create_app(tmp_path)) as restarted:
        assert history(restarted)["items"] == rows
        assert not history(restarted)["warning"]


@pytest.mark.parametrize("fallback_snapshot", [False, True])
def test_new_writes_after_snapshot_restore_are_recorded(tmp_path, fallback_snapshot):
    from pymodbus.client import ModbusTcpClient

    config = Configuration(
        devices=[
            Device(
                id="d",
                port=port(),
                points=[Point(id="p", name="点", initial=7, writable=True)],
            )
        ]
    )
    with TestClient(create_app(tmp_path)) as client:
        assert client.put("/api/config", json=config.model_dump()).status_code == 200
        assert assign(client, 37, origin="web").status_code == 200
    snapshot = tmp_path / "snapshot.json"
    assert snapshot.exists()
    if fallback_snapshot:
        (tmp_path / "snapshot.previous.json").write_bytes(snapshot.read_bytes())
        snapshot.write_text("broken", encoding="utf-8")

    with TestClient(create_app(tmp_path)) as restarted:
        # Restored values must survive activation, not be replaced by initials.
        assert restarted.app.state.runtime.get("d").value("p") == 37
        assert restarted.app.state.runtime.get("d").states["p"].hold
        assert assign(restarted, 41, origin="web").status_code == 200
        rows = history(restarted)["items"]
        assert len(rows) == 2
        assert rows[0]["origin"] == "web"
        assert rows[0]["config_version"] == 1
        assert (rows[0]["changes"][0]["before"], rows[0]["changes"][0]["after"]) == (
            37,
            41,
        )
        assert assign(restarted, 65536).status_code == 422
        assert restarted.post("/api/devices/d/actions/start").status_code == 200
        modbus = ModbusTcpClient("127.0.0.1", port=config.devices[0].port, timeout=2)
        try:
            assert modbus.connect()
            assert not modbus.write_register(0, 42, device_id=1).isError()
            assert modbus.write_register(65535, 1, device_id=1).isError()
        finally:
            modbus.close()
        rows = history(restarted)["items"]
        assert len(rows) == 5
        assert [row["origin"] for row in rows] == [
            "modbus",
            "modbus",
            "api",
            "web",
            "web",
        ]
        assert [row["outcome"] for row in rows] == [
            "failed",
            "success",
            "failed",
            "success",
            "success",
        ]
        assert rows[1]["changes"][0]["after"] == 42
        assert not history(restarted)["warning"]

    with TestClient(create_app(tmp_path)) as again:
        assert history(again)["items"] == rows
        assert assign(again, 53, origin="web").status_code == 200
        assert len(history(again)["items"]) == 6


@pytest.mark.parametrize(
    "data",
    [
        "bad-json",
        '{"id":"broken","time":1}',
        '{"id":"broken","time":1,"count":1,"changes":[null],"outcome":"success"}',
    ],
)
def test_corrupt_history_falls_back_to_cache_without_500(client, data):
    assign(client, 15)
    store = client.app.state.context["storage"]
    client.portal.call(store.flush)
    client.portal.call(
        store.telemetry_db.call,
        lambda c: (c.execute("UPDATE assignments SET data=?", (data,)), c.commit()),
    )
    result = history(client)
    assert result["items"][0]["changes"][0]["after"] == 15
    assert "读取失败" in result["warning"]


def test_storage_degradation_does_not_prevent_write(client, monkeypatch):
    store = client.app.state.context["storage"]
    monkeypatch.setattr(store, "writable", lambda: False)
    assert assign(client, 25).status_code == 200
    assert "持久化降级" in history(client)["warning"]
    store.dropped = 5
    assert "丢弃 5" in history(client)["warning"]
    store.assignment_pruned = 3
    assert "清理过 3 条" in history(client)["warning"]


@pytest.mark.asyncio
async def test_large_batch_bounded_cache_detail_and_storage_retention(tmp_path):
    device = Device(
        id="d", points=[Point(id=f"p{i}", name=f"点{i}", address=i) for i in range(101)]
    )
    store, runtime = Storage(tmp_path), Runtime()
    await store.open()
    try:
        await store.commit(Configuration(devices=[device]), runtime)
        runtime.on_event = lambda row: store.enqueue("event", row)
        items = [{"id": p.id, "value": 50} for p in device.points]
        runtime.get("d").assign(items)
        row = runtime.get("d").assignments[-1]
        assert (
            row["count"] == 101 and len(row["changes"]) == 20 and row["omitted"] == 81
        )
        for value in range(102):
            runtime.get("d").assign([{"id": "p0", "value": value}])
        assert len(runtime.get("d").assignments) == 100
        await store.flush()
        assert len(await store.assignments("d")) == 100
        # Count retention is independent of the sampling retention period.
        await store.telemetry_db.call(
            lambda c: (c.execute("UPDATE assignments SET time=0"), c.commit())
        )
        await store.cleanup()
        assert len(await store.assignments("d")) == 100
        plan = await store.telemetry_reader.call(
            lambda c: c.execute(
                "EXPLAIN QUERY PLAN SELECT data FROM assignments WHERE device='d' ORDER BY time DESC,operation DESC LIMIT 100"
            ).fetchall()
        )
        assert "assignment_lookup" in str(plan)
        await store.commit(Configuration(version=1), runtime)
        await store.cleanup()
        assert await store.assignments("d") == []
    finally:
        await store.close(runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pdu",
    [
        "050000ff00",
        "060000002a",
        "0f000000010101",
        "100000000102002a",
        "160000ff000055",
        "17000000010000000102002a",
    ],
)
async def test_real_modbus_write_sources_and_failures(pdu):
    device = Device(
        id="d",
        port=port(),
        functions=[1, 3, 5, 6, 15, 16, 22, 23],
        points=[
            Point(id="c", name="线圈", area="coil", type="Bool", writable=True),
            Point(id="p", name="寄存器", writable=True),
        ],
    )
    runtime, service = Runtime(), None
    runtime.apply(Configuration(devices=[device]))
    service = ModbusService(runtime)
    await service.start("d")
    reader, writer = await asyncio.open_connection("127.0.0.1", device.port)
    source_port = writer.get_extra_info("sockname")[1]

    async def request(raw):
        writer.write(struct.pack(">HHHB", 1, 0, len(raw) + 1, 1) + raw)
        await writer.drain()
        header = await asyncio.wait_for(reader.readexactly(7), 1)
        return await asyncio.wait_for(
            reader.readexactly(int.from_bytes(header[4:6], "big") - 1), 1
        )

    try:
        raw = bytes.fromhex(pdu)
        assert (await request(raw))[0] == raw[0]
        row = runtime.get("d").assignments[-1]
        assert row["outcome"] == "success" and row["origin"] == "modbus"
        assert (
            row["host"] == "127.0.0.1"
            and row["port"] == source_port
            and row["function"] == raw[0]
        )
        assert row["changes"][0]["before_raw"] == [0]
        assert row["changes"][0]["after_raw"] == runtime.get("d").raw(
            row["changes"][0]["id"]
        )
        before = len(runtime.get("d").assignments)
        assert (await request(bytes.fromhex("0300000001")))[0] == 3
        assert len(runtime.get("d").assignments) == before
        bad = bytes([raw[0], 0xFF, 0xFF]) + raw[3:]
        assert (await request(bad))[0] == raw[0] | 0x80
        failed = runtime.get("d").assignments[-1]
        assert failed["outcome"] == "failed" and failed["request"] == bad.hex()
        assert failed["host"] == "127.0.0.1" and failed["port"] == source_port
    finally:
        writer.close()
        await writer.wait_closed()
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["timeout_rate", "disconnect_rate"])
async def test_fault_injected_failed_writes_recorded_without_mutation(fault):
    device = Device(
        id="d",
        port=port(),
        faults=Faults(**{fault: 1}),
        points=[Point(id="p", name="寄存器", writable=True, initial=7)],
    )
    runtime = Runtime()
    runtime.apply(Configuration(devices=[device]))
    service = ModbusService(runtime)
    await service.start("d")
    reader, writer = await asyncio.open_connection("127.0.0.1", device.port)
    try:
        writer.write(bytes.fromhex("00010000000601060000002a"))
        await writer.drain()
        if fault == "disconnect_rate":
            assert await asyncio.wait_for(reader.read(), 1) == b""
        else:
            await asyncio.sleep(0.05)
        row = runtime.get("d").assignments[-1]
        assert row["outcome"] == "failed" and "故障注入" in row["error"]
        assert row["changes"][0]["before"] == row["changes"][0]["after"] == 7
        assert runtime.get("d").value("p") == 7
    finally:
        writer.close()
        await writer.wait_closed()
        await service.close()


@pytest.mark.asyncio
async def test_delayed_rejected_write_records_current_point_metadata():
    config = Configuration(
        devices=[
            Device(
                id="d",
                port=port(),
                faults=Faults(delay_ms=100),
                points=[Point(id="p", name="旧点位", writable=True, initial=7)],
            )
        ]
    )
    runtime = Runtime()
    runtime.apply(config)
    service = ModbusService(runtime)
    await service.start("d")
    reader, writer = await asyncio.open_connection("127.0.0.1", config.devices[0].port)
    try:
        writer.write(bytes.fromhex("00010000000601060000002a"))
        await writer.drain()
        await asyncio.sleep(0.03)
        new = config.model_copy(deep=True)
        new.devices[0].points[0].name = "新点位"
        new.devices[0].points[0].writable = False
        runtime.apply(new)
        assert (await asyncio.wait_for(reader.readexactly(9), 1))[-2:] == b"\x86\x02"
        record = runtime.get("d").assignments[-1]
        assert record["outcome"] == "failed"
        assert record["changes"][0]["name"] == "新点位"
        assert record["changes"][0]["after"] == 7
    finally:
        writer.close()
        await writer.wait_closed()
        await service.close()


@pytest.mark.asyncio
async def test_actual_disk_budget_pruning_reports_history_gap(tmp_path):
    store, runtime = Storage(tmp_path), Runtime()
    await store.open()
    try:
        config = Configuration(
            devices=[Device(id="d", points=[Point(id="p", name="点")])]
        )
        config.settings.telemetry_budget_mb = 1
        await store.commit(config, runtime)
        runtime.on_event = lambda row: store.enqueue("event", row)
        runtime.get("d").assign([{"id": "p", "value": 12}])
        await store.flush()
        for cycle in range(8):
            for i in range(100):
                store.enqueue(
                    "event",
                    {"time": 1, "payload": "x" * 2000, "cycle": cycle, "index": i},
                )
            while store.queue:
                await store.flush()
                assert not store.history_error
        assert store.assignment_pruned > 0
        assert len(runtime.get("d").assignments) == 1
        assert await store.assignments("d") == []
    finally:
        await store.close(runtime)


@pytest.mark.asyncio
async def test_shutdown_drains_assignment_tail_beyond_one_batch(tmp_path):
    store, runtime = Storage(tmp_path), Runtime()
    await store.open()
    config = Configuration(devices=[Device(id="d", points=[Point(id="p", name="点")])])
    await store.commit(config, runtime)
    runtime.on_event = lambda row: store.enqueue("event", row)
    for value in range(2000):
        runtime.get("d").assign([{"id": "p", "value": value}])
    expected = list(reversed(runtime.get("d").assignments))
    assert len(store.queue) == 2000
    assert await store.close(runtime)
    assert store.dropped == 0 and not store.queue
    fresh = Storage(tmp_path)
    await fresh.open()
    try:
        assert await fresh.assignments("d") == expected
    finally:
        await fresh.close(runtime)
