"""Entry point for the Wheel Inspection System (Python port).

Run: python main.py
"""

import sys
from datetime import datetime


class _NullStream:
    """Last-resort silent sink so print() can never crash the app."""

    encoding = "utf-8"

    def write(self, _text: str) -> int:
        return 0

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False


def _init_logging() -> None:
    """Keep the engineer log when there is no console.

    The windowed (no-console) build sets sys.stdout/stderr to None, so every
    print() — camera connects, PLC triggers, per-model scores, measurement
    diagnostics — would be lost or crash. Redirect them to a bounded UTF-8
    WheelInspection.log next to the app so the trail survives. When a console IS
    present (dev run), leave on-screen printing untouched.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        from app.core.paths import ROOT
        log_path = ROOT / "WheelInspection.log"
        # Bound growth on the production machine: roll over past ~5 MB.
        try:
            if log_path.exists() and log_path.stat().st_size > 5_000_000:
                log_path.replace(log_path.with_suffix(".log.old"))
        except OSError:
            pass
        stream = open(log_path, "a", buffering=1, encoding="utf-8")
        stream.write(f"\n===== started {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
        sys.stdout = stream
        sys.stderr = stream
    except Exception:
        sys.stdout = sys.stdout or _NullStream()
        sys.stderr = sys.stderr or _NullStream()


_init_logging()

from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
