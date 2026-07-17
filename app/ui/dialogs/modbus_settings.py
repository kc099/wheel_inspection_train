"""Modbus settings editor (README §4, §8).

A simple form bound to ModbusSettings. Saving persists via AppState and fires
settings_changed so the serial handler can re-open on the new port/baud.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .. import theme
from ...core import history
from ...core.models import ModbusSettings
from ...core.state import AppState

# pyserial is optional; only used to list available COM ports.
try:
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False


class ModbusSettingsDialog(QDialog):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("Modbus Settings")
        self.resize(360, 320)
        self.setStyleSheet(f"background: {theme.WINDOW_BG};")

        s = state.settings
        form = QFormLayout()

        self.protocol = QLineEdit(s.protocol)
        self.ip = QLineEdit(s.ip_address)

        # COM port: editable combo pre-filled with detected ports; "" = auto.
        self.com = QComboBox()
        self.com.setEditable(True)
        self.com.addItem("")                       # blank ⇒ auto-detect
        if SERIAL_AVAILABLE:
            for p in serial.tools.list_ports.comports():
                self.com.addItem(p.device)
        self.com.setCurrentText(s.com_port)

        self.baud = QSpinBox()
        self.baud.setRange(300, 921600)
        self.baud.setValue(s.baud_rate)

        self.slave = QSpinBox()
        self.slave.setRange(1, 247)
        self.slave.setValue(s.slave_id)

        self.delay = QSpinBox()
        self.delay.setRange(0, 10000)
        self.delay.setValue(s.delay_ms)

        for w in (self.protocol, self.ip):
            w.setStyleSheet("background: white; padding: 4px;")

        form.addRow("Protocol", self.protocol)
        form.addRow("IP address (TCP)", self.ip)
        form.addRow("COM port", self.com)
        form.addRow("Baud rate", self.baud)
        form.addRow("Slave ID", self.slave)
        form.addRow("Delay (ms)", self.delay)

        layout = QVBoxLayout(self)
        layout.addLayout(form)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        save = QPushButton("Save")
        for b in (cancel, save):
            b.setMinimumHeight(34)
            b.setStyleSheet(theme.normal_button_qss())
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        row.addWidget(cancel)
        row.addWidget(save)
        layout.addLayout(row)

    def _save(self) -> None:
        """Read the form into ModbusSettings, persist, close. Returns: None."""
        old = self.state.settings
        updated = ModbusSettings(
            protocol=self.protocol.text(),
            ip_address=self.ip.text(),
            address1=old.address1, address2=old.address2, address3=old.address3,
            delay_ms=self.delay.value(),
            com_port=self.com.currentText().strip(),
            baud_rate=self.baud.value(),
            slave_id=self.slave.value(),
        )
        # Audit what actually changed (so the log shows old -> new, not a dump).
        changes = []
        if old.com_port != updated.com_port:
            changes.append(f"com_port {old.com_port or '(auto)'}->{updated.com_port or '(auto)'}")
        if old.baud_rate != updated.baud_rate:
            changes.append(f"baud {old.baud_rate}->{updated.baud_rate}")
        if old.slave_id != updated.slave_id:
            changes.append(f"slave_id {old.slave_id}->{updated.slave_id}")
        if changes:
            history.log_action(self.state.username, "modbus_settings", ", ".join(changes))

        self.state.set_settings(updated)     # writes settings.json + fires settings_changed
        self.accept()
