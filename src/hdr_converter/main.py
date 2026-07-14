"""GUI 入口。"""

from __future__ import annotations

import sys


def main() -> None:
    from .gui.main_window import run_gui

    sys.exit(run_gui())


if __name__ == "__main__":
    main()
