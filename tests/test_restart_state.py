import socket
import sqlite3
import subprocess
import sys
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from pymodbus.client import ModbusTcpClient

from simulator.api import create_app
from simulator.errors import DomainError
from simulator.models import Configuration, Device, Point
from simulator.modbus import ModbusService
from simulator.runtime import Runtime
from simulator.storage import Storage


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def config(port):
    return Configuration(
        devices=[
            Device(id="run", port=port, points=[Point(id="p", name="数值", initial=7)]),
            Device(id="pause", port=port, unit_id=2),
            Device(id="stop", port=port, unit_id=3, auto_start=True),
        ]
    ).model_dump()


def configure(http, port):
    assert http.put("/api/config", json=config(port)).status_code == 200
    response = http.post("/api/devices/actions/start", json={"ids": ["run", "pause"]})
    assert response.status_code == 200, response.text
    assert all(item["outcome"] == "success" for item in response.json()["items"])
    assert http.post("/api/devices/pause/actions/pause").status_code == 200
    response = http.post("/api/devices/actions/stop", json={"ids": ["stop"]})
    assert (
        response.status_code == 200
        and response.json()["items"][0]["outcome"] == "skipped"
    )


def check_recovered(http, port):
    rows = {d["id"]: d for d in http.get("/api/devices").json()}
    assert rows["run"]["status"] == rows["pause"]["status"] == "running"
    assert not rows["run"]["paused"] and rows["pause"]["paused"]
    assert rows["stop"]["status"] == "stopped"
    client = ModbusTcpClient("127.0.0.1", port=port, timeout=1)
    try:
        assert client.connect()
        assert client.read_holding_registers(0, count=1, device_id=1).registers == [7]
    finally:
        client.close()


def test_graceful_shutdown_retains_intent_and_pause_without_snapshot(tmp_path):
    port = free_port()
    with TestClient(create_app(tmp_path)) as http:
        configure(http, port)
        before = http.get("/api/config").json()
        before["devices"][0]["name"] = "新名称"
        assert http.put("/api/config", json=before).status_code == 200
    # No value snapshot is necessary for remembered communication/pause state.
    for path in tmp_path.glob("snapshot*"):
        path.unlink()
    with TestClient(create_app(tmp_path)) as http:
        check_recovered(http, port)
        assert http.post("/api/devices/pause/actions/resume").status_code == 200
        assert http.post("/api/devices/run/actions/stop").status_code == 200
    with TestClient(create_app(tmp_path)) as http:
        rows = {d["id"]: d for d in http.get("/api/devices").json()}
        assert rows["run"]["status"] == "stopped"
        assert rows["pause"]["status"] == "running" and not rows["pause"]["paused"]


def test_real_process_kill_recovers_confirmed_state(tmp_path):
    web_port, modbus_port = free_port(), free_port()
    command = [
        sys.executable,
        "-c",
        "import sys,uvicorn;from simulator.api import create_app;uvicorn.run(create_app(sys.argv[1]),host='127.0.0.1',port=int(sys.argv[2]),log_level='error')",
        str(tmp_path),
        str(web_port),
    ]

    def launch():
        process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                assert process.poll() is None
                try:
                    if http.get("/api/health/ready").status_code == 200:
                        return process
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
            raise AssertionError("backend readiness timeout")
        except BaseException:
            process.kill()
            process.wait(timeout=5)
            raise

    with httpx.Client(base_url=f"http://127.0.0.1:{web_port}", timeout=5) as http:
        process = launch()
        try:
            configure(http, modbus_port)
            process.kill()  # No lifespan close or shutdown snapshot.
            process.wait(timeout=5)
            for path in tmp_path.glob("snapshot*"):
                path.unlink()
            process = launch()
            check_recovered(http, modbus_port)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


def test_delete_and_endpoint_change_clear_remembered_state(tmp_path):
    port = free_port()
    with TestClient(create_app(tmp_path)) as http:
        configure(http, port)
        assert http.post("/api/devices/run/actions/stop").status_code == 200
        candidate = http.get("/api/config").json()
        candidate["devices"][0]["port"] = free_port()
        assert http.put("/api/config", json=candidate).status_code == 200
        rows = http.get("/api/devices").json()
        assert rows[0]["restart_state"] is None
        assert (
            http.delete(
                "/api/devices/run",
                params={"version": http.get("/api/config").json()["version"]},
            ).status_code
            == 200
        )
        candidate = http.get("/api/config").json()
        candidate["devices"].append(Device(id="run", port=port, unit_id=4).model_dump())
        assert http.put("/api/config", json=candidate).status_code == 200
        assert http.get("/api/devices").json()[-1]["restart_state"] is None
    with sqlite3.connect(tmp_path / "config.db") as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM device_state WHERE device='run'"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.asyncio
async def test_state_storage_failure_does_not_break_other_shared_units(tmp_path):
    storage = Storage(tmp_path)
    await storage.open()
    runtime = Runtime()
    runtime.apply(Configuration.model_validate(config(free_port())))
    service = ModbusService(runtime)
    service.persist_state = storage.remember_device
    try:
        await service.start("run")
        storage.error = "模拟磁盘故障"
        with pytest.raises(DomainError, match="持久化不可用"):
            await service.start("pause")
        endpoint = next(iter(service.endpoints.values()))
        assert endpoint.units == {1: "run"} and runtime.get("run").status == "running"
        with pytest.raises(DomainError, match="持久化不可用"):
            await service.pause("run", True)
        assert not runtime.get("run").paused
        with pytest.raises(DomainError, match="通信已停止"):
            await service.stop("run")
        assert runtime.get("run").status == "stopped" and not service.endpoints
        assert storage.restart_state(runtime.get("run").config)["running"]
        assert storage.restart_error
    finally:
        await service.close()
        await storage.close(runtime)


def test_bind_failure_does_not_record_start_and_restore_failure_keeps_intent(tmp_path):
    port = free_port()
    with TestClient(create_app(tmp_path)) as http:
        assert http.put("/api/config", json=config(port)).status_code == 200
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", port))
            occupied.listen()
            assert http.post("/api/devices/run/actions/start").status_code == 409
        rows = http.get("/api/devices").json()
        assert rows[0]["restart_state"] is None
        assert http.post("/api/devices/run/actions/start").status_code == 200
        candidate = http.get("/api/config").json()
        candidate["devices"][1]["port"] = free_port()
        assert http.put("/api/config", json=candidate).status_code == 200
        assert http.post("/api/devices/pause/actions/start").status_code == 200
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", port))
        occupied.listen()
        with TestClient(create_app(tmp_path)) as http:
            rows = {d["id"]: d for d in http.get("/api/devices").json()}
            assert (
                rows["run"]["status"] == "fault"
                and rows["run"]["restart_state"]["running"]
            )
            assert rows["pause"]["status"] == "running"
    with TestClient(create_app(tmp_path)) as http:
        assert http.get("/api/devices").json()[0]["status"] == "running"
