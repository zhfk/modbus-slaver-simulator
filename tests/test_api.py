import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from simulator.api import TemporaryFileResponse, create_app
from simulator.excel import parse_workbook, write_workbook
from simulator.models import Configuration, thermal_template


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        yield client


@pytest.mark.parametrize("source", ["default", "environment", "argument"])
def test_health_reports_effective_absolute_data_directory(
    tmp_path, monkeypatch, source
):
    monkeypatch.chdir(tmp_path)
    default = tmp_path / "默认数据 # 目录"
    monkeypatch.setattr("simulator.api.default_data_dir", lambda: default)
    argument = None
    if source == "default":
        monkeypatch.delenv("MODBUS_DATA_DIR", raising=False)
        expected = default
    else:
        monkeypatch.setenv("MODBUS_DATA_DIR", "环境数据 # 目录")
        expected = tmp_path / "环境数据 # 目录"
        if source == "argument":
            argument = "指定数据 # 目录"
            expected = tmp_path / argument
    with TestClient(create_app(argument)) as http:
        assert http.get("/api/health").json()["storage"]["data_dir"] == str(
            expected.resolve()
        )
        assert (expected / "config.db").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["disconnect", "cancel", "invalid-range", "success"]
)
async def test_export_response_cleans_file_even_when_download_is_interrupted(
    tmp_path, failure
):
    path = tmp_path / "export.xlsx"
    path.write_bytes(b"export content")
    response = TemporaryFileResponse(path, filename="export.xlsx")
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body":
            if failure == "disconnect":
                raise OSError("client disconnected")
            if failure == "cancel":
                raise asyncio.CancelledError()

    scope = {
        "type": "http",
        "method": "GET",
        "headers": [(b"range", b"invalid")] if failure == "invalid-range" else [],
    }
    if failure in ("disconnect", "cancel"):
        with pytest.raises(
            OSError if failure == "disconnect" else asyncio.CancelledError
        ):
            await response(scope, receive, send)
    else:
        await response(scope, receive, send)
        assert sent[0]["status"] == (400 if failure == "invalid-range" else 200)
    assert not path.exists()


def template(client):
    result = client.post("/api/templates/thermal", json={"version": 0, "name": "温控"})
    assert result.status_code == 200, result.text
    return result.json()["id"]


def test_api_state_and_validation(client):
    key = template(client)
    config = client.get("/api/config").json()
    assert config["version"] == 1
    items = client.get(f"/api/devices/{key}/points").json()["items"]
    point = items[1]
    preview = client.post(
        f"/api/devices/{key}/preview-value", json={"id": point["id"], "value": 25.35}
    )
    assert preview.json()["raw"] == [254]
    response = client.post(
        f"/api/devices/{key}/assign", json={"items": [{"id": point["id"], "value": 80}]}
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["value"] == 80
    assert client.get("/api/config").json()["devices"][0]["points"][1]["initial"] == 60
    bad = json.loads(json.dumps(config))
    bad["devices"][0]["points"][1]["scale"] = 0
    assert client.put("/api/config", json=bad).status_code == 422
    assert client.get("/api/config").json()["version"] == 1
    assert client.get("/api/health/live").status_code == 200
    assert client.get("/api/health/ready").status_code == 200
    assert client.get("/api/not-an-api").status_code == 404
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get(f"/api/devices/{key}/points?size=10000").status_code == 422


@pytest.mark.parametrize("invalid_id", [[], {}, None, 1])
def test_invalid_point_id_returns_client_error_and_keeps_state(client, invalid_id):
    key = template(client)
    before = client.get(f"/api/devices/{key}/points").json()
    for action, payload in (
        ("assign", {"items": [{"id": invalid_id, "value": 80}]}),
        ("preview-value", {"id": invalid_id, "value": 80}),
    ):
        assert (
            client.post(f"/api/devices/{key}/{action}", json=payload).status_code == 422
        )
    assert client.get(f"/api/devices/{key}/points").json()["items"] == before["items"]
    assert client.get("/api/health/ready").status_code == 200


@pytest.mark.parametrize(
    "kind,params",
    [
        ("fixed", {"value": None}),
        ("sine", {"period": []}),
        ("expression", {"expression": "(("}),
        ("sequence", {"values": 1}),
        ("replay", {"values": [[0, None]]}),
    ],
)
def test_invalid_strategy_is_validation_error_without_config_commit(
    client, kind, params
):
    template(client)
    config = client.get("/api/config").json()
    bad = json.loads(json.dumps(config))
    bad["devices"][0]["points"][2]["strategy"].update(
        kind=kind, params=params, dependencies=[]
    )
    assert client.put("/api/config", json=bad).status_code == 422
    assert client.get("/api/config").json() == config


def test_large_assignment_does_not_change_previous_value(client):
    key = template(client)
    point = client.get(f"/api/devices/{key}/points").json()["items"][1]
    for action, payload in (
        ("assign", {"items": [{"id": point["id"], "value": 1e100}]}),
        ("preview-value", {"id": point["id"], "value": 1e100}),
    ):
        assert (
            client.post(f"/api/devices/{key}/{action}", json=payload).status_code == 422
        )
    assert (
        client.get(f"/api/devices/{key}/points").json()["items"][1]["value"]
        == point["value"]
    )


@pytest.mark.parametrize("port", [None, [], 1502.5])
def test_invalid_template_port_does_not_create_device(client, port):
    assert (
        client.post(
            "/api/templates/thermal", json={"version": 0, "port": port}
        ).status_code
        == 422
    )
    assert client.get("/api/config").json()["devices"] == []


@pytest.mark.parametrize("mode", ["template", "empty", "edit"])
def test_duplicate_unit_is_rejected_without_changing_configuration(client, mode):
    template(client)
    if mode == "edit":
        assert (
            client.post(
                "/api/templates/thermal", json={"version": 1, "unit_id": 2}
            ).status_code
            == 200
        )
    before = client.get("/api/config").json()
    if mode == "template":
        response = client.post(
            "/api/templates/thermal", json={"version": before["version"]}
        )
    else:
        candidate = json.loads(json.dumps(before))
        if mode == "empty":
            candidate["devices"].append({"id": "second", "points": []})
        else:
            candidate["devices"][1]["unit_id"] = 1
        response = client.put("/api/config", json=candidate)
    assert response.status_code == 422
    assert "同一端点 Unit ID 重复" in response.text
    assert "127.0.0.1:1502" in response.text
    assert "温控" in response.text
    assert client.get("/api/config").json() == before
    assert len(client.get("/api/devices").json()) == len(before["devices"])


@pytest.mark.parametrize("mode", ["template", "empty"])
@pytest.mark.parametrize(
    "change", [{"unit_id": 2}, {"port": 1503}, {"host": "127.0.0.2"}]
)
def test_device_creation_accepts_distinct_units_or_endpoints(client, mode, change):
    template(client)
    if mode == "template":
        response = client.post("/api/templates/thermal", json={"version": 1, **change})
    else:
        candidate = client.get("/api/config").json()
        candidate["devices"].append({"id": "second", "points": [], **change})
        response = client.put("/api/config", json=candidate)
    assert response.status_code == 200, response.text
    assert len(client.get("/api/devices").json()) == 2


@pytest.mark.parametrize("mode", ["template", "empty", "edit"])
@pytest.mark.parametrize("unit", [1, 2])
@pytest.mark.parametrize(
    "hosts",
    [
        ("127.0.0.1", "0.0.0.0"),
        ("0.0.0.0", "127.0.0.1"),
        ("::1", "::"),
        ("::", "::1"),
    ],
)
def test_wildcard_endpoint_overlap_is_rejected_without_commit(
    client, mode, unit, hosts
):
    first, second = hosts
    assert (
        client.post(
            "/api/templates/thermal",
            json={"version": 0, "name": "已占用设备", "host": first},
        ).status_code
        == 200
    )
    if mode == "edit":
        assert (
            client.post(
                "/api/templates/thermal",
                json={"version": 1, "host": second, "port": 1503, "unit_id": unit},
            ).status_code
            == 200
        )
    before = client.get("/api/config").json()
    if mode == "template":
        response = client.post(
            "/api/templates/thermal",
            json={"version": before["version"], "host": second, "unit_id": unit},
        )
    else:
        candidate = json.loads(json.dumps(before))
        if mode == "empty":
            candidate["devices"].append(
                {"id": "second", "host": second, "unit_id": unit, "points": []}
            )
        else:
            candidate["devices"][1]["port"] = 1502
        response = client.put("/api/config", json=candidate)
    assert response.status_code == 422
    assert "监听地址冲突" in response.text
    assert "已占用设备" in response.text
    assert client.get("/api/config").json() == before
    assert len(client.get("/api/devices").json()) == len(before["devices"])


@pytest.mark.parametrize("mode", ["template", "empty"])
@pytest.mark.parametrize(
    "first,second,port,unit",
    [
        ("0.0.0.0", "0.0.0.0", 1502, 2),
        ("127.0.0.1", "0.0.0.0", 1503, 1),
        ("::", "::", 1502, 2),
        ("::1", "::", 1503, 1),
        ("0.0.0.0", "::", 1502, 1),
    ],
)
def test_nonoverlapping_wildcard_configuration_is_accepted(
    client, mode, first, second, port, unit
):
    assert (
        client.post(
            "/api/templates/thermal", json={"version": 0, "host": first}
        ).status_code
        == 200
    )
    device = {"host": second, "port": port, "unit_id": unit}
    if mode == "template":
        response = client.post("/api/templates/thermal", json={"version": 1, **device})
    else:
        candidate = client.get("/api/config").json()
        candidate["devices"].append({"id": "second", "points": [], **device})
        response = client.put("/api/config", json=candidate)
    assert response.status_code == 200, response.text
    assert len(client.get("/api/devices").json()) == 2


def test_legacy_endpoint_overlap_remains_editable_after_restart(tmp_path):
    with TestClient(create_app(tmp_path)) as http:
        template(http)
        old = http.get("/api/config").json()
    # Reproduce the database a previous version successfully persisted.
    import sqlite3
    from contextlib import closing

    old["devices"].append(
        {"id": "legacy", "host": "0.0.0.0", "unit_id": 1, "points": []}
    )
    with closing(sqlite3.connect(tmp_path / "config.db")) as conn, conn:
        conn.execute("UPDATE config SET data=? WHERE id=1", (json.dumps(old),))
    with TestClient(create_app(tmp_path)) as http:
        loaded = http.get("/api/config").json()
        assert len(loaded["devices"]) == 2
        assert len(http.get("/api/devices").json()) == 2
        assert http.get("/api/health/ready").status_code == 200
        assert http.put("/api/config", json=loaded).status_code == 422
        fixed = json.loads(json.dumps(loaded))
        fixed["devices"][1].update(host="127.0.0.1", unit_id=2)
        assert http.put("/api/config", json=fixed).status_code == 200
    with TestClient(create_app(tmp_path)) as http:
        saved = http.get("/api/config").json()
        assert saved["devices"][1]["host"] == "127.0.0.1"
        assert saved["devices"][1]["unit_id"] == 2


@pytest.mark.parametrize("activation_failure", [False, True])
def test_delete_legacy_overlap_preserves_remaining_devices_and_recovers(
    tmp_path, monkeypatch, activation_failure
):
    import sqlite3
    from contextlib import closing

    with TestClient(create_app(tmp_path)) as http:
        key = template(http)
        old = http.get("/api/config").json()
    removed_point = old["devices"][0]["points"][0]["id"]
    old["devices"].extend(
        [
            {"id": "wildcard-one", "host": "0.0.0.0", "unit_id": 1, "points": []},
            {"id": "loopback-two", "host": "127.0.0.1", "unit_id": 2, "points": []},
            {"id": "wildcard-two", "host": "0.0.0.0", "unit_id": 2, "points": []},
        ]
    )
    old["settings"]["history_points"] = [removed_point]
    with closing(sqlite3.connect(tmp_path / "config.db")) as conn, conn:
        conn.execute("UPDATE config SET data=? WHERE id=1", (json.dumps(old),))
    with TestClient(create_app(tmp_path)) as http:
        before = http.get("/api/config").json()
        assert http.put("/api/config", json=before).status_code == 422
        if activation_failure:
            runtime = http.app.state.runtime
            original = runtime.apply
            attempts = []

            def fail_once(config):
                attempts.append(config.version)
                if len(attempts) == 1:
                    raise RuntimeError("injected activation failure")
                return original(config)

            monkeypatch.setattr(runtime, "apply", fail_once)
        response = http.delete(f"/api/devices/{key}?version={before['version']}")
        assert response.status_code == (503 if activation_failure else 200), (
            response.text
        )
        after = http.get("/api/config").json()
        assert after["version"] == before["version"] + 1
        assert after["devices"] == before["devices"][1:]
        assert after["settings"]["history_points"] == []
        assert len(http.get("/api/devices").json()) == 3
        assert http.get("/api/health/ready").status_code == 200
        # Removal cannot be used to introduce a conflicting endpoint via ordinary saves.
        assert http.put("/api/config", json=after).status_code == 422
        assert (
            http.delete(
                f"/api/devices/wildcard-one?version={before['version']}"
            ).status_code
            == 409
        )
        assert http.get("/api/config").json() == after
    with TestClient(create_app(tmp_path)) as http:
        assert http.get("/api/config").json() == after
        assert len(http.get("/api/devices").json()) == 3


@pytest.mark.parametrize("status", ["running", "starting", "stopping"])
def test_delete_requires_stopped_device_and_current_version(client, status):
    key = template(client)
    before = client.get("/api/config").json()
    runtime = client.app.state.runtime
    runtime.get(key).status = status
    try:
        assert client.delete(f"/api/devices/{key}?version=1").status_code == 409
        assert client.delete(f"/api/devices/{key}?version=0").status_code == 409
        assert client.delete(f"/api/devices/{key}").status_code == 422
        assert client.delete("/api/devices/missing?version=1").status_code == 404
        assert client.get("/api/config").json() == before
    finally:
        runtime.get(key).status = "stopped"
    response = client.delete(f"/api/devices/{key}?version=1")
    assert response.status_code == 200, response.text
    assert response.json()["devices"] == []
    assert client.get("/api/devices").json() == []


def test_disabled_history_still_exports_existing_samples(client):
    from openpyxl import load_workbook
    from io import BytesIO

    key = template(client)
    point = client.get("/api/config").json()["devices"][0]["points"][1]
    storage = client.app.state.context["storage"]
    now = time.time()
    client.portal.call(
        storage.telemetry_db.call,
        lambda c: (
            c.execute(
                "INSERT INTO samples(time,device,point,value,raw,metadata,version) VALUES (?,?,?,?,?,?,?)",
                (now, key, point["id"], 60, "[600]", '{"unit":"℃"}', 1),
            ),
            c.commit(),
        ),
    )
    assert not client.get("/api/config").json()["settings"]["history_enabled"]
    response = client.get(
        f"/api/export/history?device={key}&start={now - 1}&end={now + 1}"
    )
    assert response.status_code == 200, response.text
    workbook = load_workbook(BytesIO(response.content))
    try:
        rows = list(workbook["数据"].values)
        assert len(rows) == 2 and rows[1][rows[0].index("value")] == 60
    finally:
        workbook.close()


@pytest.mark.parametrize("query", [[], {"ids": [[]]}, {"ids": [None]}])
def test_invalid_live_subscription_closes_cleanly(client, query):
    from starlette.websockets import WebSocketDisconnect

    key = template(client)
    if isinstance(query, dict):
        query = {**query, "device": key}
    with client.websocket_connect("/api/live") as ws:
        ws.send_json(query)
        with pytest.raises(WebSocketDisconnect) as error:
            ws.receive_json()
        assert error.value.code == 1008
    assert client.app.state.context["sessions"] == 0


def test_excel_roundtrip_formula_and_invalid_row(tmp_path):
    device = thermal_template(name="=DANGEROUS()")
    device.points[0].description = "=1+1"
    config = Configuration(devices=[device])
    path = tmp_path / "config.xlsx"
    write_workbook({**config.model_dump(), "kind": "config"}, path)
    result = parse_workbook(path, "update")
    assert result["errors"] == []
    restored = Configuration.model_validate(result["config"])
    assert restored.devices[0].model_dump() == device.model_dump()
    from openpyxl import load_workbook

    wb = load_workbook(path)
    assert wb["设备"]["B2"].data_type == "s"
    header = [c.value for c in wb["点位"][1]]
    wb["点位"].cell(3, header.index("倍率") + 1, 0)
    wb.save(path)
    errors = parse_workbook(path, "update")["errors"]
    assert errors[0]["sheet"] == "点位" and errors[0]["row"] == 3


def test_actual_excel_worker_export_import(client):
    key = template(client)
    export = client.get(f"/api/export/config?device={key}")
    assert export.status_code == 200, export.text
    assert export.content[:2] == b"PK"
    preview = client.post(
        "/api/import/preview?mode=update",
        files={
            "file": (
                "points.xlsx",
                export.content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert not data["errors"] and data["changed"] == 4 and data["deleted"] == 0
    assert client.post("/api/import/apply", json=data["config"]).status_code == 200
    assert client.post("/api/import/apply", json=data["config"]).status_code == 409
    snapshot = client.get(f"/api/export/snapshot?device={key}")
    invalid = client.post(
        "/api/import/preview", files={"file": ("snapshot.xlsx", snapshot.content)}
    )
    assert invalid.json()["errors"]


def test_excel_json_and_duplicate_id_errors_identify_exact_source_row(tmp_path):
    from openpyxl import load_workbook

    d = thermal_template()
    path = tmp_path / "invalid.xlsx"
    write_workbook({**Configuration(devices=[d]).model_dump(), "kind": "config"}, path)
    wb = load_workbook(path)
    from simulator.excel import POINT_COLUMNS

    headers = list(POINT_COLUMNS.values())
    wb["点位"].cell(3, headers.index("strategy_params") + 1, "{broken")
    wb["点位"].cell(4, headers.index("id") + 1, d.points[0].id)
    wb.save(path)
    errors = parse_workbook(path, "update")["errors"]
    assert any(
        e["sheet"] == "点位" and e["row"] == 3 and "JSON" in e["message"]
        for e in errors
    )
    assert any(
        e["sheet"] == "点位" and e["row"] == 4 and "重复" in e["message"]
        for e in errors
    )


def test_websocket_complete_snapshot_and_size_limit(client):
    key = template(client)
    config = client.get("/api/config").json()
    p = config["devices"][0]["points"][0]
    with client.websocket_connect("/api/live") as ws:
        ws.send_json({"device": key, "ids": [p["id"]]})
        frame = ws.receive_json()
        assert frame["complete"] and frame["items"][0]["id"] == p["id"]
        assert frame["device"]["id"] == key


def test_health_detects_stalled_database_job_even_with_empty_queue(client):
    db = client.app.state.context["storage"].config_db
    entered, release = threading.Event(), threading.Event()

    def blocked(conn):
        entered.set()
        assert release.wait(5)

    future = db.submit(blocked)
    assert entered.wait(1)
    try:
        assert db.queue.empty() and db.busy
        # Test the deadline without making the test wait fifteen seconds.
        db.busy_since = time.monotonic() - 16
        response = client.get("/api/health/live")
        assert response.status_code == 503
        assert response.json()["runtime"] and not response.json()["storage"]
    finally:
        release.set()
        future.result(timeout=2)
    assert client.get("/api/health/live").status_code == 200


def test_corrupt_history_preserves_file_and_does_not_block_autostart_or_config(
    tmp_path,
):
    import socket
    from pymodbus.client import ModbusTcpClient

    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    with TestClient(create_app(tmp_path)) as http:
        d = thermal_template(port=port)
        d.auto_start = True
        assert (
            http.put(
                "/api/config", json=Configuration(devices=[d]).model_dump()
            ).status_code
            == 200
        )
    damaged = b"damaged telemetry database; preserve evidence"
    (tmp_path / "telemetry.db").write_bytes(damaged)
    with TestClient(create_app(tmp_path)) as http:
        assert http.get("/api/health/ready").status_code == 200
        metric = http.get("/api/health").json()
        assert (
            not metric["storage"]["error"]
            and "历史库" in metric["storage"]["history_error"]
        )
        assert (tmp_path / "telemetry.db").read_bytes() == damaged
        protocol = ModbusTcpClient("127.0.0.1", port=port, timeout=2)
        try:
            assert protocol.connect()
            assert protocol.read_holding_registers(
                0, count=1, device_id=1
            ).registers == [600]
        finally:
            protocol.close()
        config = http.get("/api/config").json()
        config["devices"][0]["name"] = "history fault isolated"
        assert http.put("/api/config", json=config).status_code == 200


def test_application_log_rotates_and_keeps_total_below_fifty_mib(client):
    import logging

    logger = logging.getLogger("simulator")
    handler = next(h for h in logger.handlers if getattr(h, "maxBytes", 0))
    directory = __import__("pathlib").Path(handler.baseFilename).parent
    for i in range(120):
        logger.info("rotation sample %s %s", i, "x" * (512 * 1024))
    handler.flush()
    files = list(directory.glob("application.log*"))
    assert len(files) == 5
    assert sum(p.stat().st_size for p in files) <= 50 * 1024**2
    assert "rotation sample 119" in (directory / "application.log").read_text()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"ids": None},
        {"ids": []},
        {"ids": "device"},
        {"ids": ["a", "a"]},
        {"ids": [None]},
        {"ids": [1]},
        {"ids": [True]},
        {"ids": [[]]},
        {"ids": [{}]},
        {"ids": [""]},
        {"ids": ["a" * 65]},
        {"ids": [str(i) for i in range(17)]},
    ],
)
def test_batch_device_actions_validate_targets_without_side_effects(client, payload):
    before = client.get("/api/config").json()
    result = client.post("/api/devices/actions/start", json=payload)
    assert result.status_code == 422, result.text
    assert client.get("/api/config").json() == before
    assert client.app.state.context["controls"] == 0
    assert (
        client.post("/api/devices/actions/delete", json={"ids": ["a"]}).status_code
        == 404
    )


def test_batch_device_actions_keep_shared_listener_and_unselected_device(tmp_path):
    import socket
    import struct

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with TestClient(create_app(tmp_path)) as http:
        candidate = http.get("/api/config").json()
        candidate["devices"] = [
            {
                "id": f"d{i}",
                "name": f"从机{i}",
                "port": port,
                "unit_id": i,
                "points": [
                    {
                        "id": f"p{i}",
                        "name": "value",
                        "area": "holding",
                        "address": 0,
                        "type": "UInt16",
                        "initial": i * 10,
                    }
                ],
            }
            for i in range(1, 4)
        ]
        assert http.put("/api/config", json=candidate).status_code == 200
        before = http.get("/api/config").json()
        started = http.post("/api/devices/actions/start", json={"ids": ["d1", "d2"]})
        assert started.status_code == 200, started.text
        assert [item["outcome"] for item in started.json()["items"]] == [
            "success",
            "success",
        ]
        assert {d["id"]: d["status"] for d in http.get("/api/devices").json()} == {
            "d1": "running",
            "d2": "running",
            "d3": "stopped",
        }
        assert len(http.app.state.modbus.endpoints) == 1

        def read(unit):
            with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
                conn.sendall(struct.pack(">HHHBBHH", 7, 0, 6, unit, 3, 0, 1))
                data = b""
                while len(data) < 11:
                    part = conn.recv(11 - len(data))
                    assert part
                    data += part
                assert data[:9] == struct.pack(">HHHBBB", 7, 0, 5, unit, 3, 2)
                return int.from_bytes(data[-2:], "big")

        assert read(1) == 10 and read(2) == 20
        stopped = http.post("/api/devices/actions/stop", json={"ids": ["d1"]}).json()
        assert stopped["success"] == 1
        assert read(2) == 20
        assert len(http.app.state.modbus.endpoints) == 1
        again = http.post(
            "/api/devices/actions/stop", json={"ids": ["d1", "d2"]}
        ).json()
        assert again["skipped"] == 1 and again["success"] == 1 and again["failed"] == 0
        assert len(http.app.state.modbus.endpoints) == 0
        assert http.get("/api/config").json() == before
        assert http.app.state.context["controls"] == 0


def test_batch_start_continues_after_real_bind_failure_and_missing_device(client):
    import socket

    with socket.socket() as occupied, socket.socket() as unused:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        unused.bind(("127.0.0.1", 0))
        good_port = unused.getsockname()[1]
        unused.close()
        current = client.get("/api/config").json()
        current["devices"] = [
            {
                "id": "bad",
                "name": "occupied",
                "port": occupied.getsockname()[1],
                "points": [],
            },
            {"id": "good", "name": "available", "port": good_port, "points": []},
        ]
        assert client.put("/api/config", json=current).status_code == 200
        result = client.post(
            "/api/devices/actions/start", json={"ids": ["bad", "missing", "good"]}
        )
        assert result.status_code == 200, result.text
        result = result.json()
        assert result["failed"] == 2 and result["success"] == 1
        assert [item["outcome"] for item in result["items"]] == [
            "failed",
            "failed",
            "success",
        ]
        assert [item["status"] for item in result["items"]] == [
            "fault",
            None,
            "running",
        ]
        assert [item["code"] for item in result["items"][:2]] == [409, 404]
        retry = client.post("/api/devices/actions/start", json={"ids": ["good"]}).json()
        assert retry["skipped"] == 1 and retry["success"] == 0
        assert client.get("/api/health/ready").status_code == 200
        assert client.app.state.context["controls"] == 0


def test_batch_rejects_overlap_and_configuration_changes_without_queueing(
    client, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    key = template(client)
    modbus = client.app.state.modbus
    original = modbus.start
    entered, release = threading.Event(), threading.Event()

    async def delayed(key):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        await original(key)

    monkeypatch.setattr(modbus, "start", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.post, "/api/devices/actions/start", json={"ids": [key]}
        )
        try:
            assert entered.wait(3)
            assert (
                client.post(
                    "/api/devices/actions/stop", json={"ids": [key]}
                ).status_code
                == 409
            )
            current = client.get("/api/config").json()
            current["devices"][0]["name"] = "changed during batch"
            assert client.put("/api/config", json=current).status_code == 409
            assert client.get("/api/health/ready").status_code == 200
        finally:
            release.set()
        assert future.result(timeout=5).json()["success"] == 1
    assert (
        client.post("/api/devices/actions/stop", json={"ids": [key]}).json()["success"]
        == 1
    )
    assert client.app.state.context["controls"] == 0
