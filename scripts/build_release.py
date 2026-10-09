"""Build a native, self-contained directory package; never cross-compile."""

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tomllib


def build(output):
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    system = {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}[
        platform.system()
    ]
    arch = {
        "AMD64": "x86_64",
        "x86_64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(platform.machine())
    if arch is None:
        raise ValueError("Unsupported architecture")
    static = root / "simulator" / "static"
    if not (static / "index.html").is_file() or not list(static.glob("assets/*.woff2")):
        raise ValueError(
            "Build the frontend before packaging; static assets/fonts are required"
        )
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    name = f"modbus-simulator-{version}-{system}-{arch}"
    if (output / name).exists() or list(output.glob(name + ".*")):
        raise ValueError("Release output already exists; choose a fresh directory")
    work = root / "build" / "frozen" / f"{system}-{arch}"
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--name",
            name,
            "--distpath",
            str(output),
            "--workpath",
            str(work / "work"),
            "--specpath",
            str(work),
            "--paths",
            str(root),
            "--add-data",
            f"{static}{';' if system == 'windows' else ':'}simulator/static",
            "--collect-submodules",
            "uvicorn",
            "--collect-submodules",
            "pymodbus",
            "--copy-metadata",
            "fastapi",
            "--copy-metadata",
            "starlette",
            str(root / "scripts" / "frozen_entry.py"),
        ],
        cwd=root,
        check=True,
    )
    package = output / name
    executable = name + (".exe" if system == "windows" else "")
    (package / executable).rename(
        package
        / ("modbus-simulator.exe" if system == "windows" else "modbus-simulator")
    )
    for doc in ("README.md", "AGENTS.md", "requirements.txt", "requirements.lock.txt"):
        shutil.copy2(root / doc, package / doc)
    shutil.copytree(root / "docs", package / "docs")
    shutil.copy2(static / "FONT-LICENSE.txt", package / "FONT-LICENSE.txt")
    shutil.copytree(root / "packaging" / "service", package / "service")
    if system == "windows":
        (package / "start.cmd").write_text(
            '@echo off\r\n"%~dp0modbus-simulator.exe" %*\r\nif errorlevel 1 pause\r\n',
            encoding="ascii",
            newline="",
        )
    else:
        script = package / ("start.command" if system == "macos" else "start.sh")
        script.write_text(
            '#!/bin/sh\nset -eu\nrelease_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec "$release_dir/modbus-simulator" "$@"\n'
        )
        script.chmod(0o755)
    distributions = sorted(
        {d.metadata["Name"]: d.version for d in metadata.distributions()}.items()
    )
    (package / "BUILD-INFO.json").write_text(
        json.dumps(
            {
                "version": version,
                "platform": system,
                "architecture": arch,
                "python": platform.python_version(),
                "os": platform.platform(),
                "libc": platform.libc_ver(),
                "built_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=root, text=True
                ).strip(),
                "source_tree_dirty": bool(
                    subprocess.check_output(
                        ["git", "status", "--porcelain"], cwd=root, text=True
                    ).strip()
                ),
                "dependencies": dict(distributions),
                "release_status": "prerelease; see docs/acceptance.md",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    licenses = package / "licenses"
    licenses.mkdir()
    python_license = next(
        (
            p
            for p in (
                Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
                Path(sys.base_prefix) / "LICENSE.txt",
                Path(sys.base_prefix) / "LICENSE",
            )
            if p.is_file()
        ),
        None,
    )
    if python_license is None:
        raise ValueError(
            "CPython LICENSE.txt must be available in the build environment"
        )
    shutil.copy2(python_license, licenses / "CPython-LICENSE.txt")
    for dist in metadata.distributions():
        for file in dist.files or []:
            if ".dist-info" in str(file) and any(
                word in file.name.lower() for word in ("license", "copying", "notice")
            ):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    target = licenses / dist.metadata["Name"] / Path(str(file)).name
                    target.parent.mkdir(exist_ok=True)
                    shutil.copy2(source, target)
    archive = Path(
        shutil.make_archive(
            str(output / name),
            "zip" if system == "windows" else "gztar",
            root_dir=output,
            base_dir=name,
        )
    )
    with archive.open("rb") as file:
        checksum = hashlib.file_digest(file, "sha256").hexdigest()
    (output / (archive.name + ".sha256")).write_text(f"{checksum}  {archive.name}\n")
    print(
        json.dumps(
            {"package": str(package), "archive": str(archive), "sha256": checksum}
        )
    )
    return package


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist/release")
    build(parser.parse_args().output)
