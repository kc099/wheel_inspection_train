"""Main window — layout (README §3) plus the wired-up inspection flow (§5/§6).

Responsibilities:
  - build the three-column UI;
  - own AppState (model data + settings), the InferenceEngine, and the
    SignalHandler, and connect their signals to the UI;
  - run an inspection (manual button OR serial trigger) through ONE shared code
    path so the two can never diverge.
"""

from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer, QDateTime
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QImage, QPixmap

from . import theme
from .dashboard import DashboardBand
from .dialogs.activity_log import ActivityLogDialog
from .dialogs.batch_test import BatchTestDialog
from .dialogs.login import LoginDialog
from .dialogs.modbus_settings import ModbusSettingsDialog
from .dialogs.model_data import ModelDataDialog
from .dialogs.train import TrainDialog
from .dialogs.users import UserProfilesDialog
from ..inference.engine import InferenceEngine
from ..comms.camera import CameraStream, is_reachable
from ..comms.signal_handler import SignalHandler
from ..core import history
from ..core.state import AppState
from ..core.verdict import Level, match_percentages
from .widgets import Card, ImageArea, group_label, hline, section_header

from ..core.paths import ROOT

# Assets ships next to the exe in a frozen build, in the repo when from source.
ASSETS = ROOT / "Assets"

# Level → status-dot colour (README §2).
_LEVEL_COLOR = {
    Level.OK: theme.STATUS_OK,
    Level.WARN: theme.STATUS_WARN,
    Level.ERROR: theme.STATUS_ERROR,
    Level.INFO: theme.TEXT_MUTED,
}


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wheel Inspection System")
        self.resize(1200, 950)          # taller: 3 columns + the dashboard band
        self.setStyleSheet(f"background-color: {theme.WINDOW_BG};")

        # --- app services ---------------------------------------------------
        self.state = AppState()
        self.engine = InferenceEngine(
            cache_size=self.state.app_settings.lru_cache_size,
            min_confidence=self.state.app_settings.recognition_min_confidence,
        )
        self.signals = SignalHandler(self.state.settings)
        self._current_image: str | None = None    # path of the image to inspect
        self._camera: CameraStream | None = None   # live stream thread (when running)
        self._latest_qimage: QImage | None = None  # most recent live frame (for Capture)
        self._inspection_in_progress = False        # prevents overlapping PLC cycles
        self._camera_auto_reconnect = True          # production mode reconnects on failures
        self._camera_retry_timer = QTimer(self)
        self._camera_retry_timer.setSingleShot(True)
        self._camera_retry_timer.timeout.connect(self._start_camera_automatically)

        # --- build UI -------------------------------------------------------
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_title_bar())
        root.addWidget(self._build_body(), 1)
        self.dashboard = DashboardBand()          # counts + recent inspections
        # Cap the band so the inspection columns above keep their height.
        self.dashboard.setMaximumHeight(250)
        root.addWidget(self.dashboard)
        root.addWidget(self._build_status_bar())

        self._start_clock()
        self._connect_services()
        # Start the production camera without requiring an operator click.
        QTimer.singleShot(0, self._start_camera_automatically)

    # ------------------------------------------------------------------ title
    def _build_title_bar(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet(
            f"background-color: {theme.WINDOW_BG};"
            f"border-bottom: 1px solid {theme.CARD_BORDER};"
        )
        grid = QGridLayout(bar)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)

        # Left: hamburger Options menu (items wired to dialogs).
        options = QToolButton()
        options.setText("☰  Options")
        options.setPopupMode(QToolButton.InstantPopup)
        options.setStyleSheet(
            f"QToolButton {{ color: {theme.TEXT_PRIMARY}; font-size: 14px;"
            f" font-weight: 600; background: transparent; border: none; }}"
            "QToolButton::menu-indicator { image: none; }"
        )
        menu = QMenu(options)
        actions = {
            "Model data": self._open_model_data,
            #"Model data view": self._open_model_data_view,
           # "Train new model": self._open_train,
            "Batch run": self._open_batch_test,
            "Modbus settings": self._open_modbus_settings,
            "User Profiles": self._open_user_profiles,
            "Activity Log": self._open_activity_log,
        }
        for label, handler in actions.items():
            act = QAction(label, self)
            act.triggered.connect(handler)
            menu.addAction(act)
        options.setMenu(menu)
        grid.addWidget(options, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)

        # Center: title.
        title = QLabel("WHEEL INSPECTION SYSTEM")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color: {theme.TITLE_TEXT}; font-size: 28px; font-weight: bold;"
        )
        grid.addWidget(title, 0, 1, Qt.AlignCenter)

        # Right: logo + date/time.
        right = QWidget()
        rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        logo = QLabel()
        logo_path = ASSETS / "Taurus_logo.PNG"
        if logo_path.exists():
            logo.setPixmap(
                QPixmap(str(logo_path)).scaledToHeight(45, Qt.SmoothTransformation)
            )
        rl.addWidget(logo)

        dt = QWidget()
        dtl = QVBoxLayout(dt)
        dtl.setContentsMargins(0, 0, 0, 0)
        dtl.setSpacing(0)
        self.date_text = QLabel()
        self.time_text = QLabel()
        for lbl in (self.date_text, self.time_text):
            lbl.setAlignment(Qt.AlignRight)
            lbl.setStyleSheet(
                f"color: {theme.TEXT_PRIMARY}; font-size: 12px; font-weight: bold;"
            )
        dtl.addWidget(self.date_text)
        dtl.addWidget(self.time_text)
        rl.addWidget(dt)
        grid.addWidget(right, 0, 2, Qt.AlignRight | Qt.AlignVCenter)
        return bar

    # ------------------------------------------------------------------- body
    def _build_body(self) -> QWidget:
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setSpacing(12)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(2, 280)
        grid.addWidget(self._build_capture_column(), 0, 0)
        grid.addWidget(self._build_result_column(), 0, 1)
        grid.addWidget(self._build_controls_column(), 0, 2)
        return body

    def _build_capture_column(self) -> QWidget:
        card = Card()
        card.layout().addWidget(section_header("Live Feed"))
        self.capture_image = ImageArea("No image loaded")
        card.layout().addWidget(self.capture_image, 1)
        return card

    def _build_result_column(self) -> QWidget:
        card = Card()
        card.layout().addWidget(section_header("Captured Frame"))
        # The image takes all the space the (now much smaller) panels don't need.
        self.result_image = ImageArea("No result")
        self.result_image.setMinimumHeight(360)
        card.layout().addWidget(self.result_image, 1)
        # Per-model scores/matches are deliberately NOT shown here any more —
        # they go to the terminal log (_log_result) for the engineers only.
        card.layout().addWidget(self._build_detection_panel())
        return card

    def _build_detection_panel(self) -> QWidget:
        panel = QFrame()
        panel.setStyleSheet(
            f"background: {theme.FRAME_BG};"
            f"border: 1px solid {theme.CARD_BORDER}; border-radius: 6px;"
        )
        grid = QGridLayout(panel)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(0, 1)

        rows = [
            ("Predicted Model", "N/A"),
            ("Status", "Waiting"),
        ]
        self.result_values: dict[str, QLabel] = {}
        for i, (label, value) in enumerate(rows):
            key = QLabel(label)
            key.setStyleSheet(
                f"color: {theme.TEXT_ON_LIGHT}; font-size: 12px; border: none;"
                " background: transparent;"
            )
            val = QLabel(value)
            bold = "font-weight: bold;" if label in ("Predicted Model", "Status") else ""
            color = theme.TEXT_MUTED if label == "Status" else theme.TEXT_PRIMARY
            val.setStyleSheet(
                f"color: {color}; font-size: 13px; {bold} border: none;"
                " background: transparent;"
            )
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(key, i, 0)
            grid.addWidget(val, i, 1)
            self.result_values[label] = val
        return panel

    def _build_controls_column(self) -> QWidget:
        card = Card()
        card.setFixedWidth(280)
        card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        lay = card.layout()
        lay.addWidget(section_header("Camera Controls"))

        def btn(text, danger=False, height=36):
            b = QPushButton(text)
            b.setMinimumHeight(height)
            b.setStyleSheet(
                theme.danger_button_qss() if danger else theme.normal_button_qss()
            )
            return b

        lay.addWidget(group_label("IMAGE SOURCE"))
        self.upload_btn = btn("Upload Image (single)")
        self.upload_btn.clicked.connect(self._on_upload_image)
        lay.addWidget(self.upload_btn)
        lay.addWidget(hline())

        lay.addWidget(group_label("CAMERA"))
        self.start_stream_btn = btn("Start Stream")
        self.start_stream_btn.clicked.connect(self._on_start_stream)
        lay.addWidget(self.start_stream_btn)
        self.stop_stream_btn = btn("Stop Stream")
        self.stop_stream_btn.clicked.connect(self._on_stop_stream)
        self.stop_stream_btn.setEnabled(False)      # nothing to stop yet
        lay.addWidget(self.stop_stream_btn)
        self.capture_btn = btn("Capture")
        self.capture_btn.clicked.connect(self._on_capture)
        self.capture_btn.setEnabled(False)          # enabled once streaming
        lay.addWidget(self.capture_btn)
        lay.addWidget(hline())

        lay.addWidget(group_label("INSPECTION"))
        self.inspect_btn = btn("Run Inspection", danger=True, height=50)
        self.inspect_btn.clicked.connect(self._on_run_inspection)
        lay.addWidget(self.inspect_btn)

        # Off by default: the overlay is rendered in memory only when checked,
        # so production runs write nothing to disk.
        self.overlay_check = QCheckBox("Show heatmap overlay")
        self.overlay_check.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px;"
        )
        lay.addWidget(self.overlay_check)

        server_row = QWidget()
        srl = QHBoxLayout(server_row)
        srl.setContentsMargins(0, 8, 0, 0)
        srl.setAlignment(Qt.AlignCenter)
        self.model_server_dot = QLabel()
        self.model_server_dot.setFixedSize(8, 8)
        self._set_dot(self.model_server_dot, theme.STATUS_WARN)
        self.model_server_text = QLabel("Loading models...")
        self.model_server_text.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px;"
        )
        srl.addWidget(self.model_server_dot)
        srl.addWidget(self.model_server_text)
        lay.addWidget(server_row)

        lay.addWidget(hline())
        self.train_btn = btn("Train")
        self.train_btn.clicked.connect(self._open_train)
        lay.addWidget(self.train_btn)
        lay.addStretch(1)
        return card

    # ------------------------------------------------------------- status bar
    def _build_status_bar(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet(f"background-color: {theme.STATUS_BAR_BG};")
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(10, 8, 10, 8)
        hl.setSpacing(8)
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(9, 9)
        self._set_dot(self.status_dot, "#FFFFFF")
        self.status_text = QLabel("Ready.")
        self.status_text.setStyleSheet("color: white; font-weight: bold;")
        hl.addWidget(self.status_dot)
        hl.addWidget(self.status_text)
        hl.addStretch(1)
        return bar

    # ------------------------------------------------------------- services
    def _connect_services(self) -> None:
        """Wire engine + serial signals and kick off async model loading."""
        self.engine.ready.connect(self._on_models_ready)
        self.engine.error.connect(self._on_models_error)
        self.signals.status.connect(lambda m: self._set_status(m, Level.INFO))
        self.signals.triggered.connect(self._on_serial_trigger)
        self.state.settings_changed.connect(self._on_settings_changed)
        self.state.models_changed.connect(self._on_models_changed)

        self.inspect_btn.setEnabled(False)      # until models load
        self.engine.load_async()

    def _on_models_ready(self, names: list) -> None:
        """Models finished loading: enable inspection, start serial detection."""
        self._set_dot(self.model_server_dot, theme.STATUS_OK)
        self.model_server_text.setText(f"{len(names)} model(s) loaded")
        self.inspect_btn.setEnabled(True)
        self._set_status(f"Ready — {len(names)} model(s) loaded.", Level.OK)
        self.signals.start()                    # begins serial trigger detection

    def _on_models_error(self, msg: str) -> None:
        self._set_dot(self.model_server_dot, theme.STATUS_ERROR)
        self.model_server_text.setText("Model load error")
        self._set_status(msg, Level.ERROR)

    def _on_models_changed(self) -> None:
        """Model set changed (trained/edited/deleted): refresh the count label
        and drop cached weights so retrained models take effect immediately."""
        self.engine.invalidate_cache()
        n = len(self.state.models)
        self.model_server_text.setText(f"{n} model(s) loaded")
        self.inspect_btn.setEnabled(n > 0)

    def _on_settings_changed(self) -> None:
        """Re-open the serial port on new Modbus settings."""
        self.signals.stop()
        self.signals = SignalHandler(self.state.settings)
        self.signals.status.connect(lambda m: self._set_status(m, Level.INFO))
        self.signals.triggered.connect(self._on_serial_trigger)
        if self.engine.is_ready:
            self.signals.start()

    def _on_serial_trigger(self) -> None:
        """A PLC trigger arrived (already on the main thread via signal)."""
        self._set_status("Trigger received — inspecting…", Level.INFO)
        if self._inspection_in_progress:
            self._set_status("Trigger ignored: inspection is already running.", Level.WARN)
            return
        self._log("PLC trigger received on the serial port.")
        image_path = self._save_latest_frame()
        if image_path is None:
            self._set_status("Trigger received, but no live camera frame is available.", Level.WARN)
            self._start_camera_automatically()
            return
        self._log(f"Trigger frame captured: {image_path}")
        self.result_image.set_pixmap(QPixmap.fromImage(self._latest_qimage))
        self._current_image = image_path
        self._clear_result_values()
        self._set_status("Trigger received: inspecting live frame...", Level.INFO)
        self._run_inspection()

    # -------------------------------------------------------------------- auth
    def _require_login(self, action: str, developer_only: bool = False) -> bool:
        """Ensure someone is logged in (and is a developer, if required).

        Shows the Login window when there's no live session — the session lapses
        after `session_timeout_minutes` of inactivity. Returns: True if the
        caller may proceed, False if the user cancelled or lacks the role.
        """
        if self.state.current_user is None:              # none, or session expired
            dlg = LoginDialog(self, reason=f"Login required to {action}.")
            if dlg.exec() != QDialog.Accepted or dlg.user is None:
                return False
            self.state.login(dlg.user)

        if developer_only and not self.state.is_developer:
            QMessageBox.warning(
                self, "Developer access required",
                f"'{self.state.username}' is an operator. "
                f"Only a developer can {action}.",
            )
            return False

        self.state.touch()          # restart the idle timeout
        return True

    # ----------------------------------------------------------- menu actions
    def _open_model_data(self) -> None:
        if not self._require_login("edit model data", developer_only=True):
            return
        ModelDataDialog(self.state, self).exec()

   

    def _open_modbus_settings(self) -> None:
        if not self._require_login("change Modbus settings", developer_only=True):
            return
        ModbusSettingsDialog(self.state, self).exec()

    def _open_user_profiles(self) -> None:
        if not self._require_login("manage user profiles", developer_only=True):
            return
        UserProfilesDialog(self.state, self).exec()

    def _open_activity_log(self) -> None:
        if not self._require_login("view the activity log", developer_only=True):
            return
        ActivityLogDialog(self).exec()

    def _open_batch_test(self) -> None:
        if not self.engine.is_ready:
            self._set_status("Models are not ready yet.", Level.WARN)
            return
        BatchTestDialog(self.engine, self).exec()

    def _open_train(self) -> None:
        """Open the Train-new-model window (any logged-in profile may train)."""
        if not self._require_login("train a new model"):
            return
        TrainDialog(self.state, self).exec()

    # ------------------------------------------------------------ image source
    def _on_upload_image(self) -> None:
        """Pick an image file and load it as the inspection input. Returns: None."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select image", "",
            "Images (*.png *.jpg *.jpeg *.bmp)",
        )
        if not path:
            return
        # Show an uploaded image in the Captured Frame panel (it's what gets
        # inspected) and clear any previous result.
        if self.result_image.set_image(path):
            self._current_image = path
            self._clear_result_values()
            self._set_status(f"Loaded image: {Path(path).name}", Level.OK)
        else:
            self._set_status("Could not load that image.", Level.ERROR)

    # ----------------------------------------------------------- camera
    def _start_camera_automatically(self) -> None:
        """Start or retry the production camera without blocking the operator."""
        if self._camera_auto_reconnect:
            self._on_start_stream(automatic=True)

    def _schedule_camera_retry(self) -> None:
        """Retry an unavailable production camera after a short delay."""
        if self._camera_auto_reconnect and not self._camera_retry_timer.isActive():
            # Make the outage impossible to miss: the Live Feed panel says it too.
            self.capture_image.set_message("⚠ Camera disconnected — reconnecting…")
            self._set_status("Camera unavailable: retrying in 5 seconds...", Level.WARN)
            self._camera_retry_timer.start(5000)

    def _on_start_stream(self, _checked: bool = False, *, automatic: bool = False) -> None:
        """Open the HTTP camera stream and show it in the Live Feed panel."""
        if not automatic:
            self._camera_auto_reconnect = True
        if self._camera and self._camera.isRunning():
            return
        cam = self.state.camera_settings

        # Check the camera is actually connected first; if not, tell the user.
        self._set_status(f"Connecting to camera: {cam.url}", Level.INFO)
        if not is_reachable(cam.url, cam.timeout):
            if automatic:
                self._schedule_camera_retry()
            else:
                QMessageBox.warning(
                    self, "Camera not connected",
                    f"Could not reach the camera at:\n{cam.url}\n\n"
                    "Check that the camera is powered on and connected to the "
                    "network, or update the URL in settings.json (http_streamer).",
                )
                self._set_status("Camera not connected.", Level.ERROR)
            return

        self._camera = CameraStream(cam.url, cam.timeout)
        self._camera.frame_ready.connect(self._on_frame)
        self._camera.error.connect(self._on_camera_error)
        self._camera.stopped.connect(self._on_camera_stopped)
        self._camera.start()
        self._camera_retry_timer.stop()
        self.start_stream_btn.setEnabled(False)
        self.stop_stream_btn.setEnabled(True)
        self.capture_btn.setEnabled(True)

    def _on_stop_stream(self) -> None:
        """Stop the live stream. Returns: None."""
        self._camera_auto_reconnect = False
        self._camera_retry_timer.stop()
        self._stop_camera()
        self._set_status("Stream stopped.", Level.INFO)

    def _stop_camera(self) -> None:
        """Tear down the stream thread and reset the camera buttons."""
        if self._camera:
            self._camera.stop()
            self._camera = None
        # Drop the last frame: a PLC trigger arriving while the stream is down
        # must report "no live frame", not silently inspect a stale wheel.
        self._latest_qimage = None
        self.start_stream_btn.setEnabled(True)
        self.stop_stream_btn.setEnabled(False)
        self.capture_btn.setEnabled(False)

    def _on_frame(self, image: QImage) -> None:
        """Show a live frame in the Live Feed panel (called on the UI thread)."""
        if self._latest_qimage is None:              # first frame after (re)connect
            self._log(f"Camera connected — live frames from {self.state.camera_settings.url}")
        self._latest_qimage = image
        self.capture_image.set_pixmap(QPixmap.fromImage(image))

    def _on_camera_error(self, msg: str) -> None:
        """Stream failed after starting — reset and tell the user in a popup."""
        self._stop_camera()
        self._set_status(msg, Level.ERROR)
        self._schedule_camera_retry()

    def _on_camera_stopped(self) -> None:
        self.start_stream_btn.setEnabled(True)
        self.stop_stream_btn.setEnabled(False)
        self.capture_btn.setEnabled(False)

    def _save_latest_frame(self) -> str | None:
        """Persist the newest live frame and return its path, or None if absent."""
        if self._latest_qimage is None:
            return None
        folder = Path.home() / "Desktop" / "WheelCaptures"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"capture_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
        return str(path) if self._latest_qimage.save(str(path)) else None

    def _on_capture(self) -> None:
        """Freeze the latest live frame → Captured Frame panel + save to Desktop.

        Each capture writes a new timestamped PNG into a folder on the Desktop
        and becomes the image that Run Inspection will use. Returns: None.
        """
        if self._latest_qimage is None:
            self._set_status("No live frame yet — start the stream first.", Level.WARN)
            return

        # Save into a folder on the Desktop.
        folder = Path.home() / "Desktop" / "WheelCaptures"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"capture_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
        if not self._latest_qimage.save(str(path)):
            self._set_status("Could not save the captured frame.", Level.ERROR)
            return

        # Show it in the Captured Frame panel and make it the inspection input.
        self.result_image.set_pixmap(QPixmap.fromImage(self._latest_qimage))
        self._current_image = str(path)
        self._clear_result_values()
        self._set_status(f"Captured frame saved: {path.name}", Level.OK)

    # -------------------------------------------------------- image + inspect
    def _on_run_inspection(self) -> None:
        """Manual Run Inspection button → shared inspection path."""
        self._run_inspection()

    def _run_inspection(self) -> None:
        """Shared manual/triggered inspection flow (README §5 step-by-step).

        Returns: None. Every branch updates the status bar.
        """
        # 1. Guard: need an image and loaded models.
        if not self._current_image:
            self._set_status("Load an image first.", Level.WARN)
            return
        if not self.engine.is_ready:
            self._set_status("Models are not ready yet.", Level.WARN)
            return
        if self._inspection_in_progress:
            self._set_status("Inspection is already running.", Level.WARN)
            return

        # 2. Announce + clear the previous result.
        self._set_status("Classifying image…", Level.INFO)
        self._clear_results()
        self._inspection_in_progress = True
        self.inspect_btn.setEnabled(False)
        QTimer.singleShot(0, self._do_classify)   # let the UI repaint first

    def _do_classify(self) -> None:
        # 3. Classify (synchronous; winner = lowest anomaly score). The overlay
        # is rendered in memory only when the checkbox asks for it.
        result = self.engine.classify(
            self._current_image, want_heatmap=self.overlay_check.isChecked()
        )
        if not result.ok:
            self._set_status(f"Error: {result.error}", Level.ERROR)
            self._finish_inspection()
            return

        # Per-model scores go to the terminal; the UI shows only the verdict.
        self._log_result(result)
        if result.heatmap is not None:
            arr = np.ascontiguousarray(result.heatmap)
            h, w, ch = arr.shape
            qimg = QImage(arr.data, w, h, ch * w, QImage.Format_RGB888).copy()
            self.result_image.set_pixmap(QPixmap.fromImage(qimg))
        else:
            self.result_image.set_image(self._current_image)   # plain captured frame

        # 4a. No-match: even the closest model finds it too anomalous.
        if not result.matched:
            self._show_no_match()
            self._set_status("Does not belong to any available model.", Level.WARN)
            # Logged for the audit trail, but never counted as a model.
            self._record(result, None)
            self._finish_inspection()
            return

        # 4b. Recognized → fill the panel with the winning model.
        model_data = self.state.model_by_name(result.model)
        self._fill_detection_panel(result, model_data)
        conf = f"{result.confidence * 100:.0f}%"
        self._set_status(f"Recognized: {result.model} ({conf})", Level.OK)
        self._record(result, model_data)

        # 5. Look up dims and, if found, send the recognition to the PLC.
        if model_data is None:
            self._set_status(
                f"Recognized '{result.model}' but no matching model data.",
                Level.WARN,
            )
            self._finish_inspection()
            return
        self.signals.send_measurement(
            model_data.name, model_data.diameter, model_data.height, is_pass=True
        )
        self._finish_inspection()

    def _finish_inspection(self) -> None:
        """Return the UI to its ready state after one manual or PLC inspection."""
        self._inspection_in_progress = False
        self.inspect_btn.setEnabled(self.engine.is_ready)

    def _record(self, result, model_data) -> None:
        """Save the inspection to history.db and refresh the dashboard.

        Returns: None. A DB hiccup must never break an inspection, so failures
        are reported to the status bar rather than raised.
        """
        try:
            history.record_inspection(
                model=result.model if result.matched else "",
                diameter=model_data.diameter if model_data else 0.0,
                height=model_data.height if model_data else 0.0,
                confidence=result.confidence,
                matched=result.matched,
            )
            self.dashboard.refresh()
        except Exception as e:
            self._set_status(f"Could not save inspection history: {e}", Level.WARN)

    def _show_no_match(self) -> None:
        """Fill the panel for the 'not a known model' outcome. Returns: None."""
        v = self.result_values
        v["Predicted Model"].setText("None")
        status = v["Status"]
        status.setText("Not a known model")
        status.setStyleSheet(
            f"color: {_LEVEL_COLOR[Level.WARN]}; font-size: 13px;"
            " font-weight: bold; border: none; background: transparent;"
        )

    # ------------------------------------------------------------- UI fillers
    def _fill_detection_panel(self, result, model_data) -> None:
        """Show the recognized model + status. Returns: None."""
        v = self.result_values
        v["Predicted Model"].setText(result.model)

        # Recognition-only: Status just confirms the recognition succeeded.
        if model_data is not None:
            text, level = "Recognized", Level.OK
        else:
            text, level = "No model data", Level.WARN
        status = v["Status"]
        status.setText(text)
        status.setStyleSheet(
            f"color: {_LEVEL_COLOR[level]}; font-size: 13px;"
            " font-weight: bold; border: none; background: transparent;"
        )

    def _clear_result_values(self) -> None:
        """Reset the result text values (but keep the shown image). Returns: None."""
        defaults = {
            "Predicted Model": "N/A",
            "Status": "Waiting",
        }
        for k, val in defaults.items():
            self.result_values[k].setText(val)

    def _clear_results(self) -> None:
        """Reset the result panel/grid/heatmap between inspections."""
        self._clear_result_values()
        self.result_image.clear()

    # ------------------------------------------------------------------ logging
    @staticmethod
    def _log(msg: str) -> None:
        """Timestamped terminal log — the engineer-facing event trail."""
        print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)

    def _log_result(self, result) -> None:
        """Print per-model scores/confidences/matches to the terminal.

        Replaces the old on-screen grid: operators see only the verdict panel,
        engineers get the full numbers in the console. Returns: None.
        """
        matches = match_percentages(result)
        verdict = result.model if result.matched else "not a known model"
        self._log(
            f"Inspection result: {verdict} "
            f"(winner {result.model}, confidence {result.confidence * 100:.0f}%)"
        )
        for name in result.scores:
            marker = "  <- winner" if name == result.model else ""
            self._log(
                f"    {name:<14} score {result.scores[name]:>9.3f}   "
                f"conf {result.confidences.get(name, 0.0):.3f}   "
                f"match {matches.get(name, 0.0):5.1f}%{marker}"
            )

    # -------------------------------------------------------------- helpers
    def _set_dot(self, dot: QLabel, color: str) -> None:
        """Colour a status ellipse. Returns: None."""
        size = dot.width()
        dot.setStyleSheet(f"background: {color}; border-radius: {size // 2}px;")

    def _set_status(self, text: str, level: Level) -> None:
        """Update the bottom status bar text + dot colour. Returns: None.

        Every status line is also echoed to the terminal so the console holds
        a complete trail (COM port, camera, triggers, verdicts)."""
        self.status_text.setText(text)
        self._set_dot(self.status_dot, _LEVEL_COLOR.get(level, "#FFFFFF"))
        self._log(text)

    # ------------------------------------------------------------------ clock
    def _start_clock(self) -> None:
        self._tick()
        timer = QTimer(self)
        timer.timeout.connect(self._tick)
        timer.start(1000)

    def _tick(self) -> None:
        now = QDateTime.currentDateTime()
        self.date_text.setText(now.toString("dd-MM-yyyy"))
        self.time_text.setText(now.toString("HH:mm:ss"))

    # ------------------------------------------------------------------ close
    def closeEvent(self, event):    # noqa: N802  (Qt override)
        """Stop background threads cleanly on window close."""
        self._stop_camera()
        self.signals.stop()
        self.engine.shutdown()
        super().closeEvent(event)
