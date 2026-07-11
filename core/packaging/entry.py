"""PyInstaller entry point: behaves exactly like the `inkterop` CLI."""

import multiprocessing
import sys

from inkterop.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
