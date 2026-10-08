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
                assert not client.write_register(0, 753, device_id=1).isError()
                assert client.read_holding_registers(
                    0, count=1, device_id=1
                ).registers == [753]
                rows = http.get(f"/api/devices/{key}/points").json()["items"]
                assert rows[1]["value"] == 75.3 and rows[1]["raw"] == [753]
            finally:
                client.close()
            assert http.post(f"/api/devices/{key}/actions/stop").status_code == 200
    print(
        "Installed wheel passed: SPA, local assets/fonts, API, real Modbus write/read and UI value agreement"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    run(parser.parse_args().target)
