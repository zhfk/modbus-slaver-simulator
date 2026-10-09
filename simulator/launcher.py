"""Unified entry for an unpacked application, including frozen children."""

import sys

from . import __version__


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--version"]:
        print(__version__)
        return
    command = (
        args.pop(0)
        if args and args[0] in ("serve", "supervise", "maintenance")
        else "supervise"
    )
    sys.argv = [sys.argv[0], *args]
    if command == "serve":
        from .__main__ import main as run
    elif command == "maintenance":
        from .maintenance import main as run
    else:
        from .supervisor import main as run
    run()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
