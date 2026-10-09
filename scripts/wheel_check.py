"""Verify an installed wheel outside the checkout, including real TCP I/O."""

import argparse
import re
import socket
import sys
import tempfile
from pathlib import Path


def run(target):
    sys.path.insert(0, str(Path(target).resolve()))
    import simulator
    from fastapi.testclient import TestClient
    from pymodbus.client import ModbusTcpClient
    from simulator.api import create_app

    assert Path(simulator.__file__).is_relative_to(Path(target).resolve())
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    with tempfile.TemporaryDirectory() as data:
        with TestClient(create_app(Path(data))) as http:
            html = http.get("/devices/overview")
            assert html.status_code == 200 and '<div id="app">' in html.text
            assert http.get("/help").text == html.text
            assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html.text)
            assert assets and all(
                http.get(asset).status_code == 200 for asset in assets
            )
            assert http.get("/FONT-LICENSE.txt").status_code == 200
            fonts = list(Path(simulator.__file__).parent.glob("static/assets/*.woff2"))
            assert fonts and all(
                http.get("/assets/" + font.name).status_code == 200 for font in fonts
            )
            assert http.get("/api/health/ready").status_code == 200
            result = http.post(
                "/api/templates/thermal", json={"version": 0, "port": port}
            )
            assert result.status_code == 200, result.text
            key = result.json()["id"]
            assert http.post(f"/api/devices/{key}/actions/start").status_code == 200
            client = ModbusTcpClient("127.0.0.1", port=port, timeout=2)
            try:
                assert client.connect()
                words = [0x4296, 0x8000]  # Float32 75.25, big byte/word order.
                assert not client.write_registers(0, words, device_id=1).isError()
                assert (
                    client.read_holding_registers(0, count=2, device_id=1).registers
                    == words
                )
                rows = http.get(f"/api/devices/{key}/points").json()["items"]
                assert rows[1]["value"] == 75.25 and rows[1]["raw"] == words
                assert rows[1]["type"] == "Float32"
                assert rows[1]["scale"] == 1 and rows[1]["precision"] == 2
                assert client.write_register(65535, 1, device_id=1).isError()
                history = http.get(f"/api/devices/{key}/assignments").json()["items"]
                assert [r["outcome"] for r in history] == ["failed", "success"]
                assert all(
                    r["origin"] == "modbus" and r["host"] == "127.0.0.1"
                    for r in history
                )
                assert history[1]["changes"][0]["after"] == 75.25
            finally:
                client.close()
            assert http.post(f"/api/devices/{key}/actions/stop").status_code == 200
        with TestClient(create_app(Path(data))) as restarted:
            assert (
                restarted.get(f"/api/devices/{key}/assignments").json()["items"]
                == history
            )
    print(
        "Installed wheel passed: SPA/help, local assets/fonts, API, real Modbus write/read, success/failure history and persistence"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    run(parser.parse_args().target)
