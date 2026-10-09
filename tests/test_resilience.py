import asyncio
from contextlib import closing
import math
import os
import socket
import sqlite3
import subprocess
import sys
import time
import threading
from pathlib import Path

import httpx
import psutil
import pytest
from simulator.errors import DomainError
from simulator.excel import HeavyTasks
from simulator.maintenance import maintain
from simulator.models import Configuration, Device, Point, Strategy, thermal_template
from simulator.modbus import ModbusService
from simulator.runtime import DeviceRuntime, Runtime
from simulator.storage import (
    CONFIG_SCHEMA,
    DatabaseWorker,
    Storage,
    atomic_json,
    read_json,
)
from test_core import port


@pytest.mark.parametrize(
    "kind,params",
    [
        ("fixed", {"value": 12}),
        ("random", {"min": 10, "max": 20}),
        ("sine", {"mean": 30, "amplitude": 5, "period": 10}),
        ("ramp", {"start": 10, "end": 20, "duration": 10}),
        ("walk", {"min": 0, "max": 20, "step": 1}),
        ("noise", {"mean": 30, "amplitude": 5, "noise": 1}),
        ("sequence", {"values": [[1, 10], [2, 20]]}),
        ("replay", {"values": [[0, 10], [5, 20]], "speed": 1}),
        ("expression", {"expression": "10+t"}),
    ],
)
def test_all_generators_execute_and_freeze_time(kind, params):
    p = Point(
        name=kind,
        type="Float32",
        strategy=Strategy(kind=kind, interval=0.1, params=params),
    )
    d = DeviceRuntime(Device(points=[p]))
    d.status = "running"
    stamp = d.stamp
    for i in range(1, 101):
        d.tick(stamp + i * 0.1)
    assert math.isfinite(d.value(p.id))
    assert not d.states[p.id].error
    progress = d.states[p.id].elapsed
    d.paused = True
    d.tick(stamp + 20)
    assert d.states[p.id].elapsed == progress
    d.paused = False
    d.tick(stamp + 20.1)
    assert d.states[p.id].elapsed == pytest.approx(progress + 0.1)


def test_deep_dependencies_without_recursion_limit():
    points = [Point(id="0", name="0")]
    for i in range(1, 1100):
        points.append(
            Point(
                id=str(i),
                name=str(i),
                address=i,
                strategy=Strategy(kind="link", dependencies=[str(i - 1)]),
            )
        )
    assert len(Device(points=points).points) == 1100


def test_history_estimate_rejects_unsustainable_config():
    d = thermal_template()
    payload = Configuration(devices=[d]).model_dump()
    payload["settings"].update(
        history_enabled=True,
        history_points=[p.id for p in d.points],
        history_interval=0.1,
    )
    with pytest.raises(ValueError, match="历史容量估算"):
        Configuration.model_validate(payload)


@pytest.mark.asyncio
async def test_database_locked_does_not_block_modbus(tmp_path):
    s = Storage(tmp_path)
    await s.open()
    d = Device(id="d", port=port(), points=[Point(id="p", name="value", initial=23)])
    rt = Runtime()
    rt.apply(s.config)
    await s.commit(Configuration(devices=[d]), rt)
    svc = ModbusService(rt)
    await svc.start("d")
    blocker = sqlite3.connect(tmp_path / "config.db", timeout=0.1)
    blocker.execute("BEGIN IMMEDIATE")
    cfg = s.config.model_copy(deep=True)
    cfg.devices[0].name = "changed"
    commit = asyncio.create_task(s.commit(cfg, rt))
    await asyncio.sleep(0.05)
    reader, writer = await asyncio.open_connection("127.0.0.1", d.port)
    started = time.monotonic()
    writer.write(bytes.fromhex("000100000006010300000001"))
    await writer.drain()
    response = await asyncio.wait_for(reader.readexactly(11), 0.5)
    assert response[-2:] == bytes.fromhex("0017") and time.monotonic() - started < 0.5
    with pytest.raises(DomainError):
        await commit
    assert s.config.version == 1 and rt.get("d").value("p") == 23
    blocker.rollback()
    blocker.close()
    writer.close()
    await writer.wait_closed()
    await svc.close()
    await s.close(rt)


@pytest.mark.asyncio
async def test_disk_degraded_queue_bound_and_corrupt_snapshot_fallback(tmp_path):
    s = Storage(tmp_path)
    await s.open()
    rt = Runtime()
    rt.apply(s.config)
    d = thermal_template()
    await s.commit(Configuration(devices=[d]), rt)
    rt.get(d.id).assign([{"id": d.points[1].id, "value": 71}])
    await s.snapshot(rt)
    rt.get(d.id).assign([{"id": d.points[1].id, "value": 72}])
    await s.snapshot(rt)
    (tmp_path / "snapshot.json").write_text("corrupt")
    restored = Runtime()
    restored.apply(s.config)
    await s.restore(restored)
    assert restored.get(d.id).value(d.points[1].id) == 71
    for i in range(12000):
        s.enqueue("event", {"time": time.time(), "value": i})
    assert len(s.queue) == 10000 and s.dropped == 2000 and s.queue_bytes <= 16 * 1024**2
    s.metrics_cache["disk_free"] = 0
    with pytest.raises(DomainError, match="磁盘"):
        await s.commit(s.config, rt)
    assert rt.get(d.id).value(d.points[1].id) == 72
    s.queue.clear()
    s.queue_bytes = 0
    s.refresh_metrics()
    await s.close(rt)


@pytest.mark.asyncio
async def test_history_rows_retention_and_offline_backup_restore(tmp_path):
    s = Storage(tmp_path)
    await s.open()
    rt = Runtime()
    rt.apply(s.config)
    d = thermal_template()
    payload = Configuration(devices=[d]).model_dump()
    payload["settings"].update(
        history_enabled=True, history_points=[d.points[1].id], retention_days=0.01
    )
    await s.commit(Configuration.model_validate(payload), rt)
    rt.get(d.id).status = "running"
    s.sample(rt)
    await s.flush()
    rows = await s.history(d.id, d.points[1].id, time.time() - 5, time.time() + 5)
    assert (
        len(rows) == 1 and rows[0]["value"] == 60 and rows[0]["metadata"]["unit"] == "℃"
    )
    await s.telemetry_db.call(
        lambda c: (c.execute("UPDATE samples SET time=0"), c.commit())
    )
    await s.cleanup()
    assert not await s.history(d.id, d.points[1].id, 0, 100)
    backup = tmp_path / "backups/config.0.db"
    with pytest.raises(RuntimeError, match="已有应用实例"):
        maintain(tmp_path, "restore", backup)
    await s.close(rt)
    (tmp_path / "config.db").write_bytes(b"bad database")
    maintain(tmp_path, "restore", backup)
    recovered = Storage(tmp_path)
    await recovered.open()
    assert recovered.config.version == 1 and not recovered.error
    fresh = Runtime()
    fresh.apply(recovered.config)
    await recovered.close(fresh)
    assert list((tmp_path / "recovery").glob("*/config.db"))


@pytest.mark.asyncio
async def test_snapshot_can_preserve_non_finite_raw_bits(tmp_path):
    s = Storage(tmp_path)
    await s.open()
    p = Point(name="float", type="Float32", writable=True)
    d = Device(points=[p])
    rt = Runtime()
    rt.apply(s.config)
    await s.commit(Configuration(devices=[d]), rt)
    rt.get(d.id).write("holding", 0, [0x7F80, 0], "external")
    await s.snapshot(rt)
    assert s.last_snapshot > 0
    fresh = Runtime()
    fresh.apply(s.config)
    await s.restore(fresh)
    assert fresh.get(d.id).raw(p.id) == [0x7F80, 0]
    await s.close(rt)


@pytest.mark.asyncio
async def test_heavy_task_timeout_is_killable(tmp_path):
    # An extremely short deadline tests that running child processes are
    # terminated rather than abandoned in an unbounded executor.
    heavy = HeavyTasks(tmp_path, timeout=0.001)
    with pytest.raises(DomainError, match="超时"):
        await heavy.run("export", {"devices": [], "kind": "config"})
    assert not heavy.active and heavy.waiting == 0
    assert not list(tmp_path.iterdir())


def partial_export_worker(operation, payload, destination, result):
    """Inject a real worker failure after it has created an output file."""
    Path(destination).write_bytes(b"partial xlsx")
    if payload.get("hang"):
        time.sleep(30)
    Path(result).write_text('{"errors":[{"message":"injected export failure"}]}')


@pytest.mark.asyncio
async def test_failed_export_removes_partial_output(tmp_path, monkeypatch):
    monkeypatch.setattr("simulator.excel.child", partial_export_worker)
    heavy = HeavyTasks(tmp_path)
    result = await heavy.run("export", {})
    assert result["errors"] and not list(tmp_path.iterdir())
    await heavy.close()


@pytest.mark.asyncio
async def test_heavy_shutdown_cancels_running_and_queued_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr("simulator.excel.child", partial_export_worker)
    heavy = HeavyTasks(tmp_path)
    output = tmp_path / "running.xlsx"
    jobs = [asyncio.create_task(heavy.run("export", {"hang": True}, output))]
    jobs.extend(asyncio.create_task(heavy.run("export", {})) for _ in range(2))
    try:
        async with asyncio.timeout(5):
            while not output.exists():
                await asyncio.sleep(0.02)
        assert heavy.waiting == 3
        await asyncio.wait_for(heavy.close(), 3)
        assert all(job.cancelled() for job in jobs)
        assert not heavy.active and not heavy.jobs and heavy.waiting == 0
        assert not list(tmp_path.iterdir())
        with pytest.raises(DomainError, match="停止"):
            await heavy.run("export", {})
    finally:
        await heavy.close()
        await asyncio.gather(*jobs, return_exceptions=True)


@pytest.mark.asyncio
async def test_database_shutdown_drains_full_queue_and_rejects_new_jobs(tmp_path):
    worker = await asyncio.to_thread(
        DatabaseWorker, tmp_path / "full.db", CONFIG_SCHEMA
    )
    started, release = threading.Event(), threading.Event()

    def blocked(conn):
        started.set()
        assert release.wait(5)

    first = worker.submit(blocked)
    assert await asyncio.to_thread(started.wait, 2)
    accepted = [
        worker.submit(lambda c: c.execute("SELECT 1").fetchone()[0]) for _ in range(32)
    ]
    closing = asyncio.create_task(worker.close())
    try:
        await asyncio.sleep(0.05)
        with pytest.raises(DomainError, match="停止"):
            worker.submit(lambda c: 0)
        release.set()
        assert await closing
        assert first.result() is None and all(job.result() == 1 for job in accepted)
    finally:
        release.set()
        await closing
        await worker.close()


@pytest.mark.asyncio
async def test_backup_releases_sqlite_handle_before_rotating(tmp_path, monkeypatch):
    storage = Storage(tmp_path)
    await storage.open()
    runtime = Runtime()
    runtime.apply(storage.config)
    connections = []
    connect = sqlite3.connect
    replace = os.replace

    def capture_connection(*args, **kwargs):
        connection = connect(*args, **kwargs)
        if Path(args[0]).name == "config.new.db":
            connections.append(connection)
        return connection

    def check_closed(source, destination):
        if Path(source).name == "config.new.db":
            assert connections
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connections[-1].execute("SELECT 1")
        return replace(source, destination)

    monkeypatch.setattr(sqlite3, "connect", capture_connection)
    monkeypatch.setattr(os, "replace", check_closed)
    try:
        await storage.backup()
        await storage.backup()
        assert (storage.backups / "config.0.db").exists()
        assert (storage.backups / "config.1.db").exists()
    finally:
        await storage.close(runtime)


@pytest.mark.asyncio
async def test_empty_backup_and_uri_characters_restore_successfully(tmp_path):
    directory = tmp_path / "data # 存储"
    storage = Storage(directory)
    await storage.open()
    runtime = Runtime()
    runtime.apply(storage.config)
    await storage.backup()
    backup = directory / "backups/config.0.db"
    await storage.close(runtime)
    (directory / "config.db").write_bytes(b"damaged")
    maintain(directory, "restore", backup)
    recovered = Storage(directory)
    await recovered.open()
    try:
        assert not recovered.error and not recovered.history_error
        assert recovered.config.devices == [] and recovered.telemetry_reader
    finally:
        await recovered.close(runtime)


@pytest.mark.asyncio
async def test_unknown_backup_version_is_rejected_before_touching_live_database(
    tmp_path,
):
    storage = Storage(tmp_path)
    await storage.open()
    runtime = Runtime()
    runtime.apply(storage.config)
    await storage.backup()
    backup = tmp_path / "backups/config.0.db"
    await storage.close(runtime)
    with closing(sqlite3.connect(backup)) as conn, conn:
        conn.execute("PRAGMA user_version=99")
    live = tmp_path / "config.db"
    original = live.read_bytes()
    with pytest.raises(ValueError, match="版本"):
        maintain(tmp_path, "restore", backup)
    assert live.read_bytes() == original
    assert not (tmp_path / "recovery").exists()


def test_snapshot_capacity_and_corruption_preserve_valid_fallback(tmp_path):
    current, previous = tmp_path / "snapshot.json", tmp_path / "snapshot.previous.json"
    atomic_json(current, {"value": 1}, previous)
    atomic_json(current, {"value": 2}, previous)
    assert read_json(previous) == {"value": 1}
    with pytest.raises(ValueError, match="32MiB"):
        atomic_json(current, {"value": "x" * (32 * 1024**2)}, previous)
    assert read_json(current) == {"value": 2} and read_json(previous) == {"value": 1}
    current.write_text("corrupt")
    atomic_json(current, {"value": 3}, previous)
    assert read_json(current) == {"value": 3} and read_json(previous) == {"value": 1}
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_endpoint_binding_failure_and_slow_partial_header():
    occupied = socket.socket()
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    occupied_port = occupied.getsockname()[1]
    d = Device(id="d", port=occupied_port, frame_seconds=0.05, points=[Point(name="p")])
    rt = Runtime()
    rt.apply(Configuration(devices=[d]))
    service = ModbusService(rt)
    with pytest.raises(DomainError):
        await service.start("d")
    assert rt.get("d").status == "fault" and not service.endpoints
    occupied.close()
    await service.start("d")
    reader, writer = await asyncio.open_connection("127.0.0.1", occupied_port)
    writer.write(b"\0")
    await writer.drain()
    assert await asyncio.wait_for(reader.read(), 0.5) == b""
    writer.close()
    await writer.wait_closed()
    await service.close()


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX SIGSTOP injection; Windows supervisor is separately validated on Windows",
)
def test_external_supervisor_recovers_frozen_process(tmp_path):
    http_port = port()
    with open(tmp_path / "supervisor.log", "w") as log:
        supervisor = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "simulator.supervisor",
                "--port",
                str(http_port),
                "--data-dir",
                str(tmp_path / "data"),
                "--interval",
                ".1",
                "--startup-grace",
                "1",
            ],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            child = None
            while time.monotonic() < deadline:
                children = psutil.Process(supervisor.pid).children()
                if children:
                    try:
                        if (
                            httpx.get(
                                f"http://127.0.0.1:{http_port}/api/health/live",
                                timeout=0.2,
                                trust_env=False,
                            ).status_code
                            == 200
                        ):
                            child = children[0]
                            break
                    except httpx.HTTPError:
                        pass
                time.sleep(0.1)
            assert child is not None
            tcp_port = port()
            device = Device(
                id="restart-device",
                port=tcp_port,
                auto_start=True,
                points=[
                    Point(
                        id="restart-point",
                        name="value",
                        initial=23,
                        writable=True,
                        write_mode="control",
                    )
                ],
            )
            payload = Configuration(devices=[device]).model_dump()
            payload["settings"]["snapshot_seconds"] = 1
            assert (
                httpx.put(
                    f"http://127.0.0.1:{http_port}/api/config",
                    json=payload,
                    timeout=5,
                    trust_env=False,
                ).status_code
                == 200
            )
            assert (
                httpx.post(
                    f"http://127.0.0.1:{http_port}/api/devices/restart-device/actions/start",
                    trust_env=False,
                ).status_code
                == 200
            )
            assert (
                httpx.post(
                    f"http://127.0.0.1:{http_port}/api/devices/restart-device/assign",
                    json={"items": [{"id": "restart-point", "value": 55}]},
                    trust_env=False,
                ).status_code
                == 200
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = httpx.get(
                    f"http://127.0.0.1:{http_port}/api/health", trust_env=False
                ).json()
                if state["storage"]["last_snapshot"] > 0:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("No successful snapshot")
            os.kill(child.pid, __import__("signal").SIGSTOP)
            deadline = time.monotonic() + 35
            recovered = False
            while time.monotonic() < deadline:
                children = psutil.Process(supervisor.pid).children()
                if children and children[0].pid != child.pid:
                    try:
                        recovered = (
                            httpx.get(
                                f"http://127.0.0.1:{http_port}/api/health/live",
                                timeout=0.2,
                                trust_env=False,
                            ).status_code
                            == 200
                        )
                    except httpx.HTTPError:
                        pass
                    if recovered:
                        break
                time.sleep(0.1)
            assert recovered, (tmp_path / "supervisor.log").read_text()
            from pymodbus.client import ModbusTcpClient

            protocol = ModbusTcpClient("127.0.0.1", port=tcp_port, timeout=1)
            try:
                assert protocol.connect()
                assert protocol.read_holding_registers(
                    0, count=1, device_id=1
                ).registers == [55]
            finally:
                protocol.close()
        finally:
            supervisor.terminate()
            try:
                supervisor.wait(timeout=20)
            except subprocess.TimeoutExpired:
                supervisor.kill()
                supervisor.wait()


def test_valid_ranges_and_noise_base():
    d = DeviceRuntime(
        Device(
            missing_address="zero",
            valid_ranges={"holding": [[0, 10]]},
            points=[Point(name="mapped", address=0)],
        )
    )
    assert d.read("holding", 1, 2) == [0, 0]
    with pytest.raises(Exception):
        d.read("holding", 10, 2)
    with pytest.raises(ValueError):
        Device(valid_ranges={"holding": [[10, 20]]}, points=[Point(name="outside")])
    p = Point(
        name="noise",
        type="Float32",
        strategy=Strategy(
            kind="noise",
            params={"base": "fixed", "value": 10, "noise": 0, "distribution": "normal"},
        ),
    )
    rt = DeviceRuntime(Device(points=[p]))
    rt.status = "running"
    rt.tick(rt.stamp + 1)
    assert rt.value(p.id) == 10


def test_capture_limit_and_external_write_audit():
    rt = Runtime()
    p = Point(name="control", writable=True, write_mode="control")
    d = Device(points=[p])
    rt.apply(Configuration(devices=[d]))
    captured = []
    rt.on_event = captured.append
    rt.get(d.id).write("holding", 0, [20], "external")
    assert captured[-1]["kind"] == "external_write"
    assert captured[-1]["changes"][0]["after"] == 20
    rt.capture_until = time.monotonic() + 60
    rt.capture_limit = 100
    rt.capture({"time": time.time(), "request": "f" * 500})
    assert rt.capture_until == 0 and captured[-1]["kind"] == "capture_limit"


def test_configuration_unknown_database_version_is_not_overwritten(tmp_path):
    with closing(sqlite3.connect(tmp_path / "config.db")) as db, db:
        db.execute("PRAGMA user_version=99")

    async def check():
        s = Storage(tmp_path)
        await s.open()
        assert "数据库版本" in s.error
        rt = Runtime()
        rt.apply(s.config)
        await s.close(rt)

    asyncio.run(check())
    with closing(sqlite3.connect(tmp_path / "config.db")) as db, db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99


@pytest.mark.asyncio
async def test_delayed_protocol_write_uses_latest_runtime_after_config_change():
    from simulator.models import Faults

    rt = Runtime()
    d = Device(
        id="d",
        port=port(),
        faults=Faults(delay_ms=100),
        points=[Point(id="p", name="value", writable=True, write_mode="control")],
    )
    config = Configuration(devices=[d])
    rt.apply(config)
    service = ModbusService(rt)
    await service.start("d")
    reader, writer = await asyncio.open_connection("127.0.0.1", d.port)
    writer.write(bytes.fromhex("00010000000601060000002a"))
    await writer.drain()
    await asyncio.sleep(0.03)
    changed = config.model_copy(deep=True)
    changed.devices[0].name = "renamed"
    rt.apply(changed)
    assert await asyncio.wait_for(reader.readexactly(12), 1) == bytes.fromhex(
        "00010000000601060000002a"
    )
    assert rt.get("d").value("p") == 42
    writer.close()
    await writer.wait_closed()
    await service.close()


@pytest.mark.asyncio
async def test_stop_last_endpoint_closes_idle_clients_without_waiting_for_idle_timeout():
    rt = Runtime()
    d = Device(id="idle", port=port(), idle_seconds=300, points=[Point(name="value")])
    rt.apply(Configuration(devices=[d]))
    service = ModbusService(rt)
    await service.start(d.id)
    reader, writer = await asyncio.open_connection("127.0.0.1", d.port)
    await asyncio.sleep(0.01)
    await asyncio.wait_for(service.stop(d.id), 1)
    assert await asyncio.wait_for(reader.read(), 0.5) == b""
    assert not service.endpoints and service.connections == 0
    assert rt.get(d.id).status == "stopped"
    writer.close()
    await writer.wait_closed()
    # Immediate rebinding is possible without leaked sockets or tasks.
    await service.start(d.id)
    await service.close()


@pytest.mark.asyncio
async def test_backup_failure_does_not_report_verified_config_commit_as_failed(
    tmp_path, monkeypatch
):
    s = Storage(tmp_path)
    await s.open()
    rt = Runtime()
    rt.apply(s.config)

    async def fail_backup():
        raise OSError("backup disk unavailable")

    monkeypatch.setattr(s, "backup", fail_backup)
    d = thermal_template()
    result = await s.commit(Configuration(devices=[d]), rt)
    assert (
        result.version == 1
        and rt.get(d.id).config.model_dump() == result.devices[0].model_dump()
    )
    assert not s.config_pending and not s.error
    assert "backup disk unavailable" in s.nonessential_errors()
    s.enqueue("event", {"time": time.time(), "kind": "test"})
    await s.flush()
    assert "backup disk unavailable" in s.nonessential_errors()
    with closing(sqlite3.connect(tmp_path / "config.db")) as persisted:
        assert persisted.execute("SELECT version FROM config").fetchone()[0] == 1
    await s.close(rt)


@pytest.mark.asyncio
async def test_small_history_budget_reuses_pages_across_many_cleanup_cycles(tmp_path):
    s = Storage(tmp_path)
    await s.open()
    rt = Runtime()
    rt.apply(s.config)
    candidate = s.config.model_copy(deep=True)
    candidate.settings.telemetry_budget_mb = 1
    await s.commit(candidate, rt)
    for cycle in range(20):
        for i in range(100):
            s.enqueue(
                "event",
                {"time": time.time(), "cycle": cycle, "i": i, "payload": "x" * 1800},
            )
        while s.queue:
            await s.flush()
            assert not s.history_error
        await s.cleanup()
        s.refresh_metrics()
        assert (
            sum(
                v
                for k, v in s.metrics_cache["files"].items()
                if k.startswith("telemetry.db")
            )
            <= 1024**2
        )
    assert s.dropped == 0
    rows = await s.telemetry_db.call(
        lambda c: c.execute("SELECT count(*) FROM events").fetchone()[0]
    )
    assert 0 < rows < 2000
    for i in range(500):
        s.enqueue("event", {"time": time.time(), "payload": "y" * 60000})
    assert s.queue_bytes <= 16 * 1024**2 and len(s.queue) < 500 and s.dropped > 0
    s.queue.clear()
    s.queue_bytes = 0
    await s.close(rt)


@pytest.mark.asyncio
async def test_readonly_history_and_snapshot_failure_are_isolated_and_recoverable(
    tmp_path, monkeypatch
):
    import simulator.storage as storage_module

    s = Storage(tmp_path)
    await s.open()
    rt = Runtime()
    rt.apply(s.config)
    d = thermal_template()
    await s.commit(Configuration(devices=[d]), rt)
    await s.telemetry_db.call(lambda c: c.execute("PRAGMA query_only=ON"))
    s.enqueue("event", {"time": time.time(), "kind": "must fail"})
    await s.flush()
    assert "readonly" in s.history_error.lower() and s.dropped == 1
    original = storage_module.atomic_json

    def fail_snapshot(*args):
        raise OSError("snapshot disk full")

    monkeypatch.setattr(storage_module, "atomic_json", fail_snapshot)
    await s.snapshot(rt)
    assert "snapshot disk full" in s.snapshot_error and not s.snapshot_busy
    rt.get(d.id).assign([{"id": d.points[1].id, "value": 81}])
    assert rt.get(d.id).value(d.points[1].id) == 81
    await s.telemetry_db.call(lambda c: c.execute("PRAGMA query_only=OFF"))
    s.enqueue("event", {"time": time.time(), "kind": "recovered"})
    await s.flush()
    assert not s.history_error and "snapshot disk full" in s.nonessential_errors()
    monkeypatch.setattr(storage_module, "atomic_json", original)
    await s.snapshot(rt)
    assert not s.nonessential_errors() and s.last_snapshot > 0
    await s.close(rt)


@pytest.mark.asyncio
async def test_connection_flood_preserves_existing_client_and_bounded_stop():
    rt = Runtime()
    d = Device(
        id="flood",
        port=port(),
        max_connections=2,
        points=[Point(name="value", initial=23)],
    )
    rt.apply(Configuration(devices=[d]))
    svc = ModbusService(rt)
    await svc.start(d.id)
    connections = []
    try:
        for _ in range(40):
            connections.append(await asyncio.open_connection("127.0.0.1", d.port))
        await asyncio.sleep(0.05)
        assert svc.connections == 2
        reader, writer = connections[0]
        writer.write(bytes.fromhex("000100000006010300000001"))
        await writer.drain()
        assert (await asyncio.wait_for(reader.readexactly(11), 0.5))[-2:] == b"\x00\x17"
        assert await asyncio.wait_for(connections[-1][0].read(), 0.5) == b""
        await asyncio.wait_for(svc.stop(d.id), 1)
        assert not svc.endpoints and svc.connections == 0
    finally:
        for _, writer in connections:
            writer.close()
        await svc.close()


@pytest.mark.asyncio
async def test_slow_read_client_times_out_without_blocking_healthy_client():
    rt = Runtime()
    d = Device(
        id="slow-reader",
        port=port(),
        frame_seconds=0.05,
        points=[Point(name=str(i), address=i) for i in range(125)],
    )
    rt.apply(Configuration(devices=[d]))
    svc = ModbusService(rt)
    await svc.start(d.id)
    _, slow_writer = await asyncio.open_connection("127.0.0.1", d.port, limit=1024)
    slow_writer.get_extra_info("socket").setsockopt(
        socket.SOL_SOCKET, socket.SO_RCVBUF, 1024
    )
    await asyncio.sleep(0.01)
    endpoint = next(iter(svc.endpoints.values()))
    accepted = next(iter(endpoint.clients))
    accepted.get_extra_info("socket").setsockopt(
        socket.SOL_SOCKET, socket.SO_SNDBUF, 4096
    )
    reader, writer = await asyncio.open_connection("127.0.0.1", d.port)
    try:
        slow_writer.write(bytes.fromhex("00010000000601030000007d") * 1000)
        await slow_writer.drain()
        writer.write(bytes.fromhex("000200000006010300000001"))
        await writer.drain()
        assert (await asyncio.wait_for(reader.readexactly(11), 0.5))[-2:] == b"\x00\x00"
        deadline = time.monotonic() + 1.5
        while accepted in endpoint.clients and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert accepted not in endpoint.clients and not writer.transport.is_closing()
        await asyncio.wait_for(svc.stop(d.id), 1)
    finally:
        slow_writer.close()
        writer.close()
        await svc.close()


@pytest.mark.asyncio
async def test_expensive_history_query_is_interrupted_and_reader_recovers(tmp_path):
    s = Storage(tmp_path)
    await s.open()
    rt = Runtime()
    rt.apply(s.config)

    # An isolated SQL view produces far more work than the result cap; the
    # actual SQLite VM must interrupt, rather than merely time out its caller.
    def install_expensive_view(conn):
        conn.execute("DROP TABLE samples")
        conn.execute("""CREATE VIEW samples AS
            WITH RECURSIVE n(id) AS (SELECT 1 UNION ALL SELECT id+1 FROM n WHERE id<100000000)
            SELECT id,0.0 AS time,'d' AS device,'p' AS point,0 AS version,0.0 AS value,'[]' AS raw,'{}' AS metadata FROM n""")
        conn.commit()

    await s.telemetry_db.call(install_expensive_view)
    started = time.monotonic()
    with pytest.raises(DomainError, match="interrupted"):
        await s.history("d", "p", -1, 1)
    assert time.monotonic() - started < 3
    assert (
        await s.telemetry_reader.call(lambda c: c.execute("SELECT 23").fetchone()[0])
        == 23
    )
    await s.close(rt)
