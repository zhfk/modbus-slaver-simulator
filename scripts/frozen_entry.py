"""freeze_support must execute before importing CLI or web dependencies."""

import multiprocessing


if __name__ == "__main__":
    multiprocessing.freeze_support()
    from simulator.launcher import main

    main()
