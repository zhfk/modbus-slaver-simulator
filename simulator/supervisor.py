"""External process health monitor with bounded restart policy."""

import argparse
from collections import deque
import json
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description="监测业务健康并有限重启后端")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--startup-grace", type=float, default=20)
    args = parser.parse_args()
    if args.interval < 0.1 or args.startup_grace < 1:
        parser.error("探测间隔至少 0.1 秒，启动宽限至少 1 秒")
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    restarts, child = deque(), None
    # This monitor must not use a proxy to reach its local child.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        while not stopped:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "simulator",
                    "--port",
                    str(args.port),
                    "--data-dir",
                    args.data_dir,
                ]
            )
            failures, last_progress = 0, None
            started = time.monotonic()
            while not stopped and child.poll() is None:
                try:
                    with opener.open(
                        f"http://127.0.0.1:{args.port}/api/health/live", timeout=2
                    ) as response:
                        state = json.load(response)
                    progress = state.get("progress")
                    healthy = state.get("live") and progress != last_progress
                    last_progress = progress
                    failures = 0 if healthy else failures + 1
                except (OSError, ValueError, urllib.error.URLError):
                    if time.monotonic() - started > args.startup_grace:
                        failures += 1
                if failures >= 3:
                    print(
                        "关键任务无进展或健康探测连续失败，保存已落盘日志后重启",
                        flush=True,
                    )
                    break
                deadline = time.monotonic() + args.interval
                while not stopped and time.monotonic() < deadline:
                    time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            terminate(child)
            child = None
            if stopped:
                break
            now = time.monotonic()
            while restarts and now - restarts[0] > 600:
                restarts.popleft()
            if len(restarts) >= 3:
                raise SystemExit("10 分钟内重启已达 3 次，停止并保留故障现场")
            restarts.append(now)
            delay = min(30, 2 ** len(restarts))
            print(f"{delay} 秒后重启（窗口内第 {len(restarts)} 次）", flush=True)
            until = time.monotonic() + delay
            while not stopped and time.monotonic() < until:
                time.sleep(0.1)
    finally:
        if child:
            terminate(child)


def terminate(child):
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=15)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


if __name__ == "__main__":
    main()
