"""Activity Log window (developer-only) — who did what, when.

Read-only view over the audit_log table: logins (successful and failed), model
training/renames/deletes, Modbus changes, and profile changes.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import theme
from ...core import history

_LIMIT = 500


class ActivityLogDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Activity Log")
        self.resize(720, 460)
        self.setStyleSheet(f"background: {theme.WINDOW_BG};")

        layout = QVBoxLayout(self)

        # Filter by action type
        top = QHBoxLayout()
        top.addWidget(QLabel("Filter:"))
        self.filter = QComboBox()
        self.filter.setStyleSheet("background: white; padding: 3px;")
        self.filter.currentTextChanged.connect(self._refresh)
        top.addWidget(self.filter)
        top.addStretch(1)
        refresh = QPushButton("Refresh")
        refresh.setMinimumHeight(30)
        refresh.setStyleSheet(theme.normal_button_qss())
        refresh.clicked.connect(self._reload)
        top.addWidget(refresh)
        layout.addLayout(top)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Time", "User", "Action", "Details"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setStyleSheet("background: white; font-size: 11px;")
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        self._rows: list[tuple[str, str, str, str]] = []
        self._reload()

    def _reload(self) -> None:
        """Re-read the audit log from the DB and rebuild the filter list."""
        self._rows = history.recent_actions(_LIMIT)
        actions = sorted({r[2] for r in self._rows})
        current = self.filter.currentText()
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItems(["(all)"] + actions)
        if current in actions:
            self.filter.setCurrentText(current)
        self.filter.blockSignals(False)
        self._refresh()

    def _refresh(self) -> None:
        """Repaint the table applying the current action filter. Returns: None."""
        want = self.filter.currentText()
        rows = [r for r in self._rows if want in ("", "(all)") or r[2] == want]
        self.table.setRowCount(0)
        for ts, user, action, details in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, val in enumerate((ts.replace("T", " "), user, action, details)):
                self.table.setItem(r, c, QTableWidgetItem(val))
