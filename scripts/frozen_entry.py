"""freeze_support must execute before importing CLI or web dependencies."""

import multiprocessing
import sys


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    multiprocessing.freeze_support()
    from simulator.launcher import main

    main()
