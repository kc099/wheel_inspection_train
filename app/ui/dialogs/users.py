"""User Profiles window (developer-only).

The developer creates/deletes the operator profiles and can reset passwords.
Capped at `max_profiles` (default 4, including the developer). Every change is
written to the audit log.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import theme
from ...core import auth, history
from ...core.auth import ROLE_DEVELOPER, ROLE_OPERATOR
from ...core.state import AppState


class UserProfilesDialog(QDialog):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.max_profiles = state.app_settings.max_profiles
        self.setWindowTitle("User Profiles")
        self.resize(520, 420)
        self.setStyleSheet(f"background: {theme.WINDOW_BG};")

        layout = QVBoxLayout(self)
        self.hint = QLabel("")
        self.hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(self.hint)

        # Existing profiles
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Username", "Role"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setStyleSheet("background: white;")
        layout.addWidget(self.table, 1)

        # Row actions
        act = QHBoxLayout()
        self.reset_btn = QPushButton("Reset Password")
        self.delete_btn = QPushButton("Delete Selected")
        for b in (self.reset_btn, self.delete_btn):
            b.setMinimumHeight(32)
            b.setStyleSheet(theme.normal_button_qss())
        self.reset_btn.clicked.connect(self._reset_password)
        self.delete_btn.clicked.connect(self._delete_selected)
        act.addWidget(self.reset_btn)
        act.addWidget(self.delete_btn)
        act.addStretch(1)
        layout.addLayout(act)

        # Add-new form
        layout.addWidget(QLabel("Add a new profile"))
        form = QFormLayout()
        self.new_name = QLineEdit()
        self.new_pass = QLineEdit()
        self.new_pass.setEchoMode(QLineEdit.Password)
        self.new_role = QComboBox()
        self.new_role.addItems([ROLE_OPERATOR, ROLE_DEVELOPER])
        for w in (self.new_name, self.new_pass):
            w.setStyleSheet("background: white; padding: 4px;")
        form.addRow("Username", self.new_name)
        form.addRow("Password", self.new_pass)
        form.addRow("Role", self.new_role)
        layout.addLayout(form)

        bottom = QHBoxLayout()
        self.add_btn = QPushButton("Add Profile")
        close = QPushButton("Close")
        for b in (self.add_btn, close):
            b.setMinimumHeight(34)
            b.setStyleSheet(theme.normal_button_qss())
        self.add_btn.clicked.connect(self._add)
        close.clicked.connect(self.accept)
        bottom.addStretch(1)
        bottom.addWidget(close)
        bottom.addWidget(self.add_btn)
        layout.addLayout(bottom)

        self._refresh()

    # ------------------------------------------------------------- helpers
    def _refresh(self) -> None:
        """Reload the profile table and the remaining-slots hint. Returns: None."""
        users = auth.load_users()
        self.table.setRowCount(0)
        for u in users:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(u.username))
            self.table.setItem(r, 1, QTableWidgetItem(u.role))
        left = self.max_profiles - len(users)
        self.hint.setText(
            f"{len(users)} of {self.max_profiles} profiles used "
            f"({left} slot(s) left)."
        )
        self.add_btn.setEnabled(left > 0)

    def _selected_username(self) -> str | None:
        """Username of the selected row, or None. Returns: str | None."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self.table.item(rows[0].row(), 0).text()

    # ------------------------------------------------------------- actions
    def _add(self) -> None:
        """Create a new profile from the form. Returns: None."""
        try:
            user = auth.add_user(
                self.new_name.text(), self.new_pass.text(),
                self.new_role.currentText(), self.max_profiles,
            )
        except ValueError as e:
            QMessageBox.warning(self, "Cannot add profile", str(e))
            return
        history.log_action(self.state.username, "create_user",
                           f"{user.username} (role={user.role})")
        self.new_name.clear()
        self.new_pass.clear()
        self._refresh()

    def _delete_selected(self) -> None:
        """Delete the selected profile (never the last developer). Returns: None."""
        name = self._selected_username()
        if not name:
            QMessageBox.information(self, "Delete profile", "Select a profile first.")
            return
        if name == self.state.username:
            QMessageBox.warning(self, "Delete profile",
                                "You cannot delete the profile you're logged in as.")
            return
        if QMessageBox.question(
            self, "Delete profile", f"Delete profile '{name}'?"
        ) != QMessageBox.Yes:
            return
        try:
            auth.delete_user(name)
        except ValueError as e:
            QMessageBox.warning(self, "Cannot delete", str(e))
            return
        history.log_action(self.state.username, "delete_user", name)
        self._refresh()

    def _reset_password(self) -> None:
        """Set a new password for the selected profile (forgotten-password reset).

        Only reachable by a developer (the window itself is developer-gated), so
        the dev password is what authorises this. Returns: None.
        """
        name = self._selected_username()
        if not name:
            QMessageBox.information(self, "Reset password", "Select a profile first.")
            return

        new, ok = QInputDialog.getText(
            self, "Reset password",
            f"New password for '{name}':",
            QLineEdit.Password,
        )
        if not ok:
            return
        confirm, ok = QInputDialog.getText(
            self, "Reset password",
            f"Re-type the new password for '{name}':",
            QLineEdit.Password,
        )
        if not ok:
            return
        if new != confirm:
            QMessageBox.warning(self, "Cannot reset", "The two passwords don't match.")
            return

        try:
            auth.change_password(name, new)
        except ValueError as e:
            QMessageBox.warning(self, "Cannot reset", str(e))
            return
        history.log_action(self.state.username, "change_password", name)
        QMessageBox.information(
            self, "Reset password", f"Password updated for '{name}'."
        )
