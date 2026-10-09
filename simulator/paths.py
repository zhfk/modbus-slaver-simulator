"""Writable application data is separate from source and frozen resources."""

import os
import sys
from pathlib import Path


def default_data_dir():
    if os.name == "nt":
        return (
            Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ModbusSimulator"
        )
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ModbusSimulator"
    return (
        Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
        / "modbus-simulator"
    )
