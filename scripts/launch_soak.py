"""Freeze the tested source and launch measured soak stages independently."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys


def launch(output, python, stages):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("输出目录必须不存在，避免覆盖任何先前验收证据")
    root = Path(__file__).resolve().parents[1]
    output.mkdir(parents=True)
    source = output / "source"
    shutil.copytree(
        root / "simulator",
        source / "simulator",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (source / "scripts").mkdir()
    shutil.copy2(root / "scripts/soak.py", source / "scripts/soak.py")
    shutil.copy2(root / "requirements.lock.txt", source / "requirements.lock.txt")
    # Preserve the venv symlink path: resolving it to the underlying system
    # binary would silently lose the selected virtual environment.
    interpreter = str(Path(python).absolute())
    subprocess.run(
        [interpreter, "-c", "import simulator, httpx, pymodbus, websockets"],
        cwd=source,
        check=True,
        timeout=15,
    )
    with (output / "runner.log").open("wb") as log:
        process = subprocess.Popen(
            [
                interpreter,
                "-u",
                "-m",
                "scripts.soak",
                "--stages",
                stages,
                "--points",
                "1000",
                "--clients",
                "4",
                "--output",
                str(output / "results"),
            ],
            cwd=source,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=sys.platform != "win32",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.platform == "win32"
            else 0,
        )
    control = {
        "pid": process.pid,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "stages_seconds": stages,
        "python": interpreter,
        "source": str(source),
        "results": str(output / "results"),
        "status": "launched; inspect measured reports, never infer completion from PID",
    }
    (output / "control.json").write_text(
        json.dumps(control, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(control, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/soak-release")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--stages", default="86400,259200")
    args = parser.parse_args()
    if any(float(duration) <= 0 for duration in args.stages.split(",")):
        parser.error("每个阶段的时长必须为正")
    launch(args.output, args.python, args.stages)
