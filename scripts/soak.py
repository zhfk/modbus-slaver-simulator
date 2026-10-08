"""Measured mixed-load run; reports actual time, never simulated duration.

Run --stages 86400,259200 for the documented 24 h then 72 h gate.
Each stage runs against its own backend process and isolated persistent data.
"""

import argparse
import asyncio
import hashlib
import json
import platform
import random
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from websockets.asyncio.client import connect
from pymodbus.client import AsyncModbusTcpClient
from simulator.models import Configuration, Point, Strategy, thermal_template


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def quantile(values, q):
    return sorted(values)[int((len(values) - 1) * q)] if values else 0


async def stage(seconds, points, clients, output, index):
    directory = output / f"stage-{index}"
    directory.mkdir(parents=True, exist_ok=True)
    http_port, tcp_port = free_port(), free_port()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "simulator",
        "--port",
        str(http_port),
        "--data-dir",
        str(directory / "data"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    address = f"http://127.0.0.1:{http_port}"
    report = {
        "status": "starting",
        "planned_seconds": seconds,
        "completed_seconds": 0,
        "requests": 0,
        "ui_frames": 0,
        "ui_reconnections": 0,
        "device_restarts": 0,
        "source_sha256": hashlib.sha256(
            b"".join(
                path.relative_to(Path(__file__).resolve().parents[1])
                .as_posix()
                .encode()
                + path.read_bytes()
                for path in sorted(
                    Path(__file__).resolve().parents[1].joinpath("simulator").rglob("*")
                )
                if path.is_file() and "__pycache__" not in path.parts
            )
        ).hexdigest(),
        "failures": [],
        "hardware": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "load": {
            "points": points + 4,
            "clients": clients,
            "per_client_hz": 10,
            "strategy_interval": 0.1,
            "history_points": 2,
            "history_interval": 1,
            "ui_subscription_points": min(50, points + 4),
            "ui_reconnect_seconds": 30,
            "device_restart_seconds": 120,
        },
        "thresholds": {
            "rss_mb": 256,
            "rss_growth_mb": 32,
            "p99_ms": 250,
            "loop_delay_ms": 200,
            "queue_count": 10000,
            "queue_bytes": 16 * 1024**2,
            "disk_total_mb": 1536,
            "cpu_one_core_percent": 80,
        },
        "metrics": [],
    }
    path = directory / "report.json"

    def save():
        temp = path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(path)

    save()
    latencies = []
    stop = asyncio.Event()
    workers = []
    try:
        async with httpx.AsyncClient(
            base_url=address, timeout=5, trust_env=False
        ) as http:
            for _ in range(100):
                if process.returncode is not None:
                    raise RuntimeError("Backend exited during startup")
                try:
                    if (await http.get("/api/health/ready")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Backend startup timed out")
            config = (await http.get("/api/config")).json()
            # Existing state is preserved; an isolated new directory is
            # required for each selected target and duration.
            if config["devices"]:
                raise RuntimeError(
                    "Soak output already contains devices; choose a new output directory"
                )
            device = thermal_template(port=tcp_port)
            for i in range(points):
                device.points.append(
                    Point(
                        name=f"压力点位 {i}",
                        address=2 + i * 2,
                        type="Float32",
                        strategy=Strategy(
                            kind="sine" if i % 2 else "random",
                            interval=0.1,
                            seed=i,
                            params={
                                "min": 0,
                                "max": 100,
                                "mean": 50,
                                "amplitude": 10,
                                "period": 60,
                            },
                        ),
                    )
                )
            candidate = Configuration(devices=[device]).model_dump()
            candidate["settings"].update(
                history_enabled=True,
                history_points=[device.points[1].id, device.points[2].id],
                retention_days=1,
                snapshot_seconds=10,
            )
            result = await http.put("/api/config", json=candidate)
            result.raise_for_status()
            (
                await http.post(f"/api/devices/{device.id}/actions/start")
            ).raise_for_status()
            started = time.monotonic()
            report["status"] = "running"
            client_locks = [asyncio.Lock() for _ in range(clients)]
            restart_generation = 0

            async def traffic(number):
                client = AsyncModbusTcpClient(
                    "127.0.0.1", port=tcp_port, timeout=1, retries=0
                )
                if not await client.connect():
                    raise RuntimeError("Modbus connection failed")
                generation = 0
                try:
                    while not stop.is_set():
                        async with client_locks[number]:
                            if generation != restart_generation:
                                client.close()
                                if not await client.connect():
                                    raise RuntimeError(
                                        "Modbus reconnect after restart failed"
                                    )
                                generation = restart_generation
                            elapsed = await request(client, number)
                        await asyncio.sleep(max(0, 0.1 - elapsed))
                finally:
                    client.close()

            async def request(client, number):
                at = time.monotonic()
                # Read simulated data with an independent Modbus client.
                response = await client.read_input_registers(0, count=1, device_id=1)
                if response.isError():
                    raise RuntimeError("Valid Modbus read rejected")
                if number == 0:
                    value = random.randrange(300, 900)
                    response = await client.write_register(0, value, device_id=1)
                    if response.isError():
                        raise RuntimeError("Valid write rejected")
                    response = await client.read_holding_registers(
                        0, count=1, device_id=1
                    )
                    if response.registers != [value]:
                        raise RuntimeError("Written value not retained")
                latencies.append((time.monotonic() - at) * 1000)
                if len(latencies) > 10000:
                    del latencies[:1000]
                report["requests"] += 1 + 2 * (number == 0)
                return time.monotonic() - at

            async def subscription():
                uri = address.replace("http://", "ws://") + "/api/live"
                keys = [p.id for p in device.points[:50]]
                while not stop.is_set():
                    async with connect(
                        uri, proxy=None, max_size=1024**2, max_queue=2
                    ) as ws:
                        until = time.monotonic() + 30
                        while not stop.is_set() and time.monotonic() < until:
                            await ws.send(
                                json.dumps({"device": device.id, "ids": keys})
                            )
                            frame = json.loads(await asyncio.wait_for(ws.recv(), 5))
                            if (
                                not frame.get("complete")
                                or [p["id"] for p in frame["items"]] != keys
                            ):
                                raise RuntimeError(
                                    "UI complete snapshot missing after reconnect"
                                )
                            report["ui_frames"] += 1
                            await asyncio.sleep(1)
                    report["ui_reconnections"] += 1

            workers = [asyncio.create_task(traffic(i)) for i in range(clients)]
            workers.append(asyncio.create_task(subscription()))
            next_control = started + 30
            next_restart = started + 120
            next_browser = started
            previous_cpu = None
            previous_time = started
            while time.monotonic() - started < seconds:
                for worker in workers:
                    if worker.done():
                        await worker
                now = time.monotonic()
                if now >= next_browser:
                    page = (
                        await http.get(f"/api/devices/{device.id}/points?size=50")
                    ).json()
                    if len(page["items"]) != min(50, points + 4):
                        raise RuntimeError("Management page incomplete")
                    next_browser = now + 1
                if now >= next_control:
                    (
                        await http.post(f"/api/devices/{device.id}/actions/pause")
                    ).raise_for_status()
                    (
                        await http.post(f"/api/devices/{device.id}/actions/resume")
                    ).raise_for_status()
                    next_control = now + 30
                if now >= next_restart:
                    # Acquire every connection's operation lock so planned
                    # downtime cannot be mistaken for a failed active request.
                    acquired = []
                    try:
                        for lock in client_locks:
                            await asyncio.wait_for(lock.acquire(), 5)
                            acquired.append(lock)
                        (
                            await http.post(f"/api/devices/{device.id}/actions/stop")
                        ).raise_for_status()
                        (
                            await http.post(f"/api/devices/{device.id}/actions/start")
                        ).raise_for_status()
                        restart_generation += 1
                        report["device_restarts"] += 1
                    finally:
                        for lock in acquired:
                            lock.release()
                    next_restart = now + 120
                metric = (await http.get("/api/health")).json()
                cpu_percent = (
                    0
                    if previous_cpu is None
                    else 100
                    * (metric["cpu_seconds"] - previous_cpu)
                    / max(0.001, now - previous_time)
                )
                previous_cpu, previous_time = metric["cpu_seconds"], now
                row = {
                    "seconds": round(now - started, 2),
                    "rss": metric["rss"],
                    "cpu_one_core_percent": round(cpu_percent, 2),
                    "threads": metric["threads"],
                    "handles": metric["handles"],
                    "tasks": metric["tasks"],
                    "loop_delay_ms": metric["loop_delay_ms"],
                    "queue_count": metric["storage"]["queue_count"],
                    "queue_bytes": metric["storage"]["queue_bytes"],
                    "dropped": metric["storage"]["dropped"],
                    "disk_bytes": sum(metric["storage"]["files"].values()),
                    "p95_ms": quantile(latencies, 0.95),
                    "p99_ms": quantile(latencies, 0.99),
                }
                # Store a minute-resolution series plus recent samples so the
                # long-run report itself has bounded memory and disk usage.
                if (
                    not report["metrics"]
                    or row["seconds"] - report["metrics"][-1]["seconds"] >= 60
                ):
                    report["metrics"].append(row)
                report["latest"] = row
                report["completed_seconds"] = round(now - started, 3)
                if (
                    row["rss"] > 256 * 1024**2
                    or row["p99_ms"] > 250
                    or row["loop_delay_ms"] > 200
                    or row["queue_count"] > 10000
                    or row["queue_bytes"] > 16 * 1024**2
                    or row["disk_bytes"] > 1536 * 1024**2
                ):
                    raise RuntimeError(
                        "Resource or latency threshold exceeded: " + json.dumps(row)
                    )
                if metric["storage"]["error"] or metric["storage"]["history_error"]:
                    raise RuntimeError(
                        "Storage degraded unexpectedly: " + str(metric["storage"])
                    )
                save()
                await asyncio.sleep(
                    min(1, max(0, seconds - (time.monotonic() - started)))
                )
            stop.set()
            await asyncio.gather(*workers)
            (
                await http.post(f"/api/devices/{device.id}/actions/stop")
            ).raise_for_status()
            metrics = report["metrics"] + [report["latest"]]
            warm = [m for m in metrics if m["seconds"] >= min(60, seconds / 3)]
            if len(warm) >= 2 and warm[-1]["rss"] - warm[0]["rss"] > 32 * 1024**2:
                raise RuntimeError("RSS grew more than 32MiB after warmup")
            if warm and (
                max(m["threads"] for m in warm) - min(m["threads"] for m in warm) > 2
                or max(m["handles"] for m in warm) - min(m["handles"] for m in warm)
                > 16
                or max(m["tasks"] for m in warm) - min(m["tasks"] for m in warm) > 8
            ):
                raise RuntimeError("Thread, handle or task growth exceeded threshold")
            if warm and sum(m["cpu_one_core_percent"] for m in warm) / len(warm) > 80:
                raise RuntimeError("Mean CPU exceeded 80% of one core")
            report.update(
                status="passed",
                completed_seconds=round(time.monotonic() - started, 3),
                p95_ms=quantile(latencies, 0.95),
                p99_ms=quantile(latencies, 0.99),
            )
    except asyncio.CancelledError:
        report["status"] = "interrupted"
        report["failures"].append("Run interrupted before required measured duration")
        raise
    except Exception as exc:
        report["status"] = "failed"
        report["failures"].append(type(exc).__name__ + ": " + str(exc))
        raise
    finally:
        stop.set()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 20)
            except TimeoutError:
                process.kill()
                await process.wait()
        save()
        print(
            json.dumps(
                {k: v for k, v in report.items() if k != "metrics"}, ensure_ascii=False
            ),
            flush=True,
        )
    return report


async def main(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    reports = []
    for index, duration in enumerate(args.stages.split(","), 1):
        seconds = float(duration)
        if seconds <= 0:
            raise ValueError("Duration must be positive")
        reports.append(await stage(seconds, args.points, args.clients, output, index))
    (output / "summary.json").write_text(
        json.dumps(
            {
                "passed": all(r["status"] == "passed" for r in reports),
                "stages": [
                    {k: v for k, v in r.items() if k != "metrics"} for r in reports
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stages", default="60")
    parser.add_argument("--points", type=int, default=1000)
    parser.add_argument("--clients", type=int, default=4)
    parser.add_argument("--output", default="artifacts/soak")
    args = parser.parse_args()
    if not 0 <= args.points <= 9996 or not 1 <= args.clients <= 32:
        parser.error("Invalid load")
    asyncio.run(main(args))
