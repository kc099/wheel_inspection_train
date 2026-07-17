"""Login window — shown before a protected action when nobody is logged in.

On success the caller starts a session (AppState.login). Both successful and
failed attempts are written to the audit log (never the password).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .. import theme
from ...core import auth, history
from ...core.auth import User


class LoginDialog(QDialog):
    """Asks for username + password. `self.user` holds the User on success."""

    def __init__(self, parent=None, reason: str = "") -> None:
        super().__init__(parent)
        self.user: User | None = None
        self.setWindowTitle("Login")
        self.setFixedWidth(340)
        self.setStyleSheet(f"background: {theme.WINDOW_BG};")

        layout = QVBoxLayout(self)
        if reason:
            hint = QLabel(reason)
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
            layout.addWidget(hint)

        form = QFormLayout()
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)   # mask the password
        for w in (self.username, self.password):
            w.setStyleSheet("background: white; padding: 5px;")
            w.returnPressed.connect(self._attempt)      # Enter submits
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        layout.addLayout(form)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {theme.STATUS_ERROR}; font-size: 11px;")
        layout.addWidget(self.error)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        ok = QPushButton("Login")
        for b in (cancel, ok):
            b.setMinimumHeight(34)
            b.setStyleSheet(theme.normal_button_qss())
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self._attempt)
        row.addWidget(cancel)
        row.addWidget(ok)
        layout.addLayout(row)

    def _attempt(self) -> None:
        """Verify the credentials; accept on success, show an error otherwise."""
        name = self.username.text().strip()
        user = auth.verify(name, self.password.text())
        if user is None:
            # Log the failed attempt (username + time only — never the password).
            history.log_action(name, "login_failed", "wrong username or password")
            self.error.setText("Wrong username or password.")
            self.password.clear()
            return
        history.log_action(user.username, "login", f"role={user.role}")
        self.user = user
        self.accept()
