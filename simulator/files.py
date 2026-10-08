"""Small filesystem helpers shared by upload and export capacity checks."""

import stat
from pathlib import Path


def directory_bytes(directory):
    total = 0
    for path in Path(directory).iterdir():
        try:
            info = path.stat()
        except FileNotFoundError:
            # Another completed response may remove its temporary export.
            continue
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
    return total
