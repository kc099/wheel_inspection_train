"""Serial trigger detection + PLC measurement frame (README §6).

Ported from Assets/signal_handler1.py, with ONE deliberate change: the original
called the UI directly from its worker thread (unsafe in Qt). Here the worker
lives in a QThread and emits the Qt signal `triggered`; the main thread runs the
inspection. Everything else — frame sizes, cooldown, 13-byte reply — is 1:1 with
the reference so it stays firmware-compatible.

Serial config (from the reference): 8 data bits, EVEN parity, 1 stop bit, baud
from settings, 500 ms read/write timeouts.
"""

from __future__ import annotations

import re
import struct
import time

from PySide6.QtCore import QObject, QThread, Signal

from ..core.models import ModbusSettings

# pyserial is optional: the app must still run on a machine with no serial stack.
try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

# Model name → fixed PLC id (README §6). Anything else falls back to the first
# integer in the name mod 256 (so "model3" → 3), else 0.
_MODEL_ID_MAP = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5}

# Frame / timing constants — identical to the reference.
_MIN_FRAME = 6            # a trigger frame is 6–8 bytes; ≥6 buffered = a trigger
_MAX_FRAME = 8
_COOLDOWN = 4.0          # seconds between accepted triggers
_PROCESS_LOCK = 1.0      # seconds the re-entrancy lock stays held
_FRAME_TIMEOUT = 2.0     # drop an incomplete frame after this idle time
_WHEEL_PRESENT_WINDOW = 30.0   # a trigger seen within 30 s ⇒ wheel present
_POLL = 0.01             # 10 ms poll loop


def parse_model_id(model_name: str) -> int:
    """Map a model name to the 1-byte PLC id. Returns: int 0..255."""
    if model_name in _MODEL_ID_MAP:
        return _MODEL_ID_MAP[model_name]
    nums = re.findall(r"\d+", model_name or "")
    return int(nums[0]) % 256 if nums else 0


def modbus_crc16(data: bytes) -> int:
    """Modbus RTU CRC-16 (poly 0xA001, init 0xFFFF).

    Provided for completeness only — the current 13-byte frame does NOT append
    it (matches the firmware). Returns: 16-bit int.
    NOTE: confirm against firmware before enabling CRC.
    """
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class _DetectThread(QThread):
    """Background loop that watches the serial port for trigger frames."""

    triggered = Signal()          # a valid trigger arrived (run inspection)
    status = Signal(str)          # human-readable status for logging/UI

    def __init__(self, com_port: str, baud: int) -> None:
        super().__init__()
        self._com_port = com_port
        self._baud = baud
        self._stop = False
        self.serial_port = None
        self._processing = False        # 1 s re-entrancy lock
        self._last_signal_time = 0.0    # drives is_wheel_present()

    def is_wheel_present(self) -> bool:
        """True if a trigger came within the last 30 s. Returns: bool."""
        if self._last_signal_time == 0:
            return False
        return (time.time() - self._last_signal_time) <= _WHEEL_PRESENT_WINDOW

    def stop(self) -> None:
        """Ask the loop to exit and wait briefly. Returns: None."""
        self._stop = True
        self.wait(1500)

    def run(self) -> None:
        if not SERIAL_AVAILABLE:
            self.status.emit("pyserial not installed — trigger detection off")
            return
        try:
            self.serial_port = serial.Serial(
                port=self._com_port, baudrate=self._baud,
                bytesize=serial.EIGHTBITS, parity=serial.PARITY_EVEN,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.5, write_timeout=0.5, inter_byte_timeout=0.1,
            )
            self.serial_port.reset_input_buffer()
            self.serial_port.reset_output_buffer()
            self.status.emit(f"Listening on {self._com_port} @ {self._baud} baud")
        except Exception as e:
            self.status.emit(f"Could not open {self._com_port}: {e}")
            return

        buf = bytearray()
        last_trigger = 0.0
        last_data = 0.0

        while not self._stop:
            try:
                now = time.time()
                waiting = self.serial_port.in_waiting
                if waiting:
                    buf.extend(self.serial_port.read(waiting))
                    last_data = now

                if len(buf) >= _MIN_FRAME:
                    if now - last_trigger >= _COOLDOWN:      # accept the trigger
                        self._last_signal_time = now
                        if not self._processing:
                            self._processing = True
                            self.triggered.emit()            # → main thread runs inspection
                            last_trigger = now
                            # Release the re-entrancy lock ~1 s later.
                            QThread.msleep(int(_PROCESS_LOCK * 1000))
                            self._processing = False
                    buf.clear()                              # drop bytes (also during cooldown)

                elif buf and (now - last_data) > _FRAME_TIMEOUT:
                    buf.clear()                              # incomplete frame timed out

                if len(buf) > _MAX_FRAME * 2:                # overflow guard
                    buf.clear()

                QThread.msleep(int(_POLL * 1000))
            except serial.SerialException as e:
                self.status.emit(f"Serial error: {e}")
                break
            except Exception as e:
                self.status.emit(f"Read error: {e}")
                QThread.msleep(100)

        try:
            if self.serial_port:
                self.serial_port.close()
        except Exception:
            pass


class SignalHandler(QObject):
    """Public façade the app talks to: start/stop detection + send the frame.

    Signals:
      triggered() — re-emitted from the worker on the main thread.
      status(str) — log/UI messages.
    """

    triggered = Signal()
    status = Signal(str)

    def __init__(self, settings: ModbusSettings) -> None:
        super().__init__()
        self._settings = settings
        self._thread: _DetectThread | None = None

    # --- lifecycle ---------------------------------------------------------
    def _resolve_port(self) -> str | None:
        """Configured port, else first auto-detected one. Returns: str | None."""
        if self._settings.com_port:
            return self._settings.com_port
        if not SERIAL_AVAILABLE:
            return None
        ports = [p.device for p in serial.tools.list_ports.comports()]
        return ports[0] if ports else None

    def start(self) -> bool:
        """Open the port and start the detection loop.

        Returns: True if detection started; False if no port / no pyserial
        (the app keeps running either way).
        """
        if not SERIAL_AVAILABLE:
            self.status.emit("pyserial not installed — trigger detection off")
            return False
        if self._thread and self._thread.isRunning():
            return True

        port = self._resolve_port()
        if not port:
            self.status.emit("No COM port available — trigger detection off")
            return False

        self._thread = _DetectThread(port, self._settings.baud_rate)
        self._thread.triggered.connect(self.triggered.emit)   # hop to main thread
        self._thread.status.connect(self.status.emit)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop the detection loop and close the port. Returns: None."""
        if self._thread:
            self._thread.stop()
            self._thread = None

    def is_wheel_present(self) -> bool:
        """Proxy to the worker's 30 s presence check. Returns: bool."""
        return bool(self._thread and self._thread.is_wheel_present())

    # --- outbound frame ----------------------------------------------------
    def send_measurement(
        self, model_name: str, diameter_mm: float, height_mm: float,
        is_pass: bool, detection_confirmed: bool = True,
    ) -> bool:
        """Write the 13-byte measurement frame back on the open port (README §6).

        Layout (matches the Arduino/firmware rxdata[13] exactly):
          [0] slave_id  [1] 0x03
          [2..5]  height_mm   float32 big-endian   (height BEFORE diameter)
          [6..9]  diameter_mm float32 big-endian
          [10] model_id  [11] 0x01 PASS / 0x00 FAIL  [12] detection_confirmed
        No CRC appended — matches the current firmware.

        Returns: True if written; False if the port isn't open (detection must
        be running — it owns the port) or on error.
        """
        thread = self._thread
        if not SERIAL_AVAILABLE or not thread or not thread.serial_port:
            self.status.emit("Cannot send frame — serial port not open")
            return False
        port = thread.serial_port
        if not getattr(port, "is_open", False):
            self.status.emit("Cannot send frame — serial port not open")
            return False

        try:
            frame = (
                bytes([self._settings.slave_id, 0x03])
                + struct.pack(">f", float(height_mm))      # bytes 2..5
                + struct.pack(">f", float(diameter_mm))    # bytes 6..9
                + bytes([
                    parse_model_id(model_name),            # byte 10
                    0x01 if is_pass else 0x00,             # byte 11
                    0x01 if detection_confirmed else 0x00, # byte 12
                ])
            )
            if len(frame) != 13:                           # defensive: must be 13
                self.status.emit(f"Frame size {len(frame)} != 13; not sent")
                return False
            port.write(frame)
            self.status.emit(
                f"Sent frame: {model_name} "
                f"{'PASS' if is_pass else 'FAIL'} "
                f"H={height_mm:.1f} D={diameter_mm:.1f}"
            )
            return True
        except Exception as e:
            self.status.emit(f"Error sending frame: {e}")
            return False
