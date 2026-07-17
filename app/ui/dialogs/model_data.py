"""Model data editor.

Edits the existing model rows. All three columns — Name, Diameter, Height —
are editable. Renaming a model moves its folder in the registry (the folder name
IS the model id), so weights stay findable. Saving persists via AppState (which
fires models_changed).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import theme
from ...core import history, registry
from ...core.models import ModelData
from ...core.state import AppState

# Custom item-data slot where we stash each row's ORIGINAL name, so on save we
# know which models were renamed (and their old folder to move from).
_ORIG_NAME = Qt.UserRole


class ModelDataDialog(QDialog):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("Model Data")
        self.resize(520, 360)
        self.setStyleSheet(f"background: {theme.WINDOW_BG};")

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Edit name, diameter and height for each model. "
            "Renaming a model moves its folder on disk."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(hint)

        # Table: Name | Diameter | Height  (all editable)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Diameter (mm)", "Height (mm)"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setStyleSheet("background: white;")
        layout.addWidget(self.table, 1)
        self._populate()

        # Buttons
        row = QHBoxLayout()
        delete = QPushButton("Delete Selected")
        delete.clicked.connect(self._delete_selected)
        row.addWidget(delete)
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        save = QPushButton("Save")
        for b in (delete, cancel, save):
            b.setMinimumHeight(34)
            b.setStyleSheet(theme.normal_button_qss())
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        row.addWidget(cancel)
        row.addWidget(save)
        layout.addLayout(row)

    def _delete_selected(self) -> None:
        """Permanently remove the selected model (folder + weights). Returns: None."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Delete model", "Select a model row first.")
            return
        name = self.table.item(rows[0].row(), 0).data(_ORIG_NAME)
        if QMessageBox.question(
            self, "Delete model",
            f"Permanently delete model '{name}' and its weights?\n"
            "This cannot be undone.",
        ) != QMessageBox.Yes:
            return
        registry.delete_model(name)
        history.log_action(self.state.username, "delete_model", name)
        self.state.notify_models_changed()
        self._populate()

    def _populate(self) -> None:
        """Fill the grid from AppState.models, remembering original names."""
        self.table.setRowCount(0)
        for m in self.state.models:
            r = self.table.rowCount()
            self.table.insertRow(r)
            name = QTableWidgetItem(m.name)
            name.setData(_ORIG_NAME, m.name)          # remember for rename detection
            self.table.setItem(r, 0, name)
            self.table.setItem(r, 1, QTableWidgetItem(str(m.diameter)))
            self.table.setItem(r, 2, QTableWidgetItem(str(m.height)))

    def _save(self) -> None:
        """Validate, apply any renames, persist dims, close.

        Returns: None. On any problem shows a message box and stays open so the
        operator can fix it (nothing is half-applied before validation passes).
        """
        rows = []
        seen_names: set[str] = set()
        for r in range(self.table.rowCount()):
            orig = self.table.item(r, 0).data(_ORIG_NAME)
            try:
                new_name = registry.validate_name(self.table.item(r, 0).text())
                diameter = float(self.table.item(r, 1).text())
                height = float(self.table.item(r, 2).text())
            except ValueError as e:
                QMessageBox.warning(self, "Invalid value", f"Row '{orig}': {e}")
                return
            if new_name in seen_names:
                QMessageBox.warning(
                    self, "Duplicate name",
                    f"'{new_name}' is used by more than one row.",
                )
                return
            seen_names.add(new_name)
            rows.append((orig, new_name, diameter, height))

        # Apply renames first (moves folders), then persist dimensions.
        try:
            for orig, new_name, _d, _h in rows:
                if new_name != orig:
                    registry.rename_model(orig, new_name)
                    history.log_action(
                        self.state.username, "rename_model", f"{orig} -> {new_name}"
                    )
        except ValueError as e:
            QMessageBox.warning(self, "Rename failed", str(e))
            return

        # Record any dimension edits (compare against what was loaded).
        before = {m.name: m for m in self.state.models}
        for orig, new_name, d, h in rows:
            old = before.get(orig)
            if old and (old.diameter != d or old.height != h):
                history.log_action(
                    self.state.username, "edit_model",
                    f"{new_name}: diameter {old.diameter}->{d}, height {old.height}->{h}",
                )

        self.state.set_models(
            [ModelData(new_name, d, h) for _o, new_name, d, h in rows]
        )
        self.accept()
