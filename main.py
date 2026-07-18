"""Entry point for the Wheel Inspection System (Python port).

Run: python main.py
"""

import sys


class _NullStream:
    """Minimal text stream for PyInstaller's --windowed executable.

    In windowed mode Windows provides no console, so PyInstaller sets stdout
    and stderr to None. Lightning writes status messages while fitting a model;
    supplying this sink keeps training independent of a visible CMD window.
    """

    encoding = "utf-8"

    def write(self, _text: str) -> int:
        return 0

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False


if sys.stdout is None:
    sys.stdout = _NullStream()
if sys.stderr is None:
    sys.stderr = _NullStream()

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
