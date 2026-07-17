"""Model data view (README §8) — read-only table of the entered models."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import theme
from ...core.state import AppState


class ModelDataViewDialog(QDialog):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Model Data View")
        self.resize(480, 320)
        self.setStyleSheet(f"background: {theme.WINDOW_BG};")

        layout = QVBoxLayout(self)
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(
            ["Name", "Diameter (mm)", "Height (mm)"]
        )
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)   # read-only
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setStyleSheet("background: white;")

        # Snapshot the current models into rows.
        for m in state.models:
            r = table.rowCount()
            table.insertRow(r)
            for c, val in enumerate(
                (m.name, str(m.diameter), str(m.height))
            ):
                table.setItem(r, c, QTableWidgetItem(val))

        layout.addWidget(table)
