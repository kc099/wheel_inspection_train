"""Dashboard band — the two blocks shown across the bottom of the main window.

Block 1 "Wheel Count by Model": today's count, this month's count, and a
    per-model count table (NA/unrecognized inspections are never counted here).
Block 2 "Recent Inspections": the latest inspections as Time | Model.

Both read from history.db and are refreshed after every inspection.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from . import theme
from .widgets import Card, section_header
from ..core import history, registry

_RECENT_LIMIT = 20


def _table(headers: list[str]) -> QTableWidget:
    """A compact read-only table styled like the rest of the app."""
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionMode(QTableWidget.NoSelection)
    t.setStyleSheet(
        f"QTableWidget {{ background: {theme.FRAME_BG};"
        f" border: 1px solid {theme.CARD_BORDER}; border-radius: 6px;"
        f" font-size: 11px; color: {theme.TEXT_PRIMARY}; }}"
        f"QHeaderView::section {{ background: {theme.FRAME_BG};"
        f" border: none; font-weight: 600; padding: 4px; }}"
    )
    return t


class WheelCountBlock(Card):
    """Today's / this month's totals + a per-model count table."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = self.layout()
        lay.addWidget(section_header("Wheel Count by Model"))

        self.today_label = QLabel()
        self.month_label = QLabel()
        for lbl in (self.today_label, self.month_label):
            lbl.setStyleSheet(
                f"color: {theme.TEXT_PRIMARY}; font-size: 13px; font-weight: bold;"
            )
            lay.addWidget(lbl)

        self.table = _table(["Model", "Count"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeToContents
        )
        lay.addWidget(self.table, 1)

    def refresh(self) -> None:
        """Re-query the DB and repaint. Returns: None."""
        self.today_label.setText(f"Today's count:  {history.count_today()}")
        month_name = datetime.now().strftime("%B")     # e.g. "July"
        self.month_label.setText(
            f"{month_name}'s count:  {history.count_this_month()}"
        )

        # Show every registered model, including ones never inspected (count 0).
        counts = history.counts_by_model()
        self.table.setRowCount(0)
        for m in registry.list_models():
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(m.name))
            item = QTableWidgetItem(str(counts.get(m.name, 0)))
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, 1, item)


class RecentInspectionsBlock(Card):
    """The latest inspections — Time and Model only."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = self.layout()
        lay.addWidget(section_header("Recent Inspections"))
        self.table = _table(["Time", "Model"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)

    def refresh(self) -> None:
        """Re-query the DB and repaint. Returns: None."""
        self.table.setRowCount(0)
        for ts, model in history.recent_inspections(_RECENT_LIMIT):
            r = self.table.rowCount()
            self.table.insertRow(r)
            # "2026-07-13T14:22:01" → "2026-07-13 14:22:01"
            self.table.setItem(r, 0, QTableWidgetItem(ts.replace("T", " ")))
            # Unrecognized inspections show a dash rather than a model name.
            self.table.setItem(r, 1, QTableWidgetItem(model or "—"))


class DashboardBand(QWidget):
    """The two blocks side by side, sat across the bottom of the main window."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 12)
        lay.setSpacing(12)
        self.counts = WheelCountBlock()
        self.recent = RecentInspectionsBlock()
        lay.addWidget(self.counts, 1)
        lay.addWidget(self.recent, 1)
        self.refresh()

    def refresh(self) -> None:
        """Refresh both blocks (call after each inspection). Returns: None."""
        self.counts.refresh()
        self.recent.refresh()
