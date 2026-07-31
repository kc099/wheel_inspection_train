"""
Wheel Classifier Application
Uses YOLO model (best.pt) to classify wheel type from top camera (HTTP stream)
and sends predefined measurements from config.yaml via Modbus.
"""

import cv2
import numpy as np
import sys
import os
import json
import threading
import time
import re
from pathlib import Path
from datetime import datetime
import logging
import yaml

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPixmap, QFont
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QMessageBox,
    QFormLayout,
    QComboBox,
    QDialog,
    QCheckBox,
    QTableWidget,
    QTableWidgetItem,
    QSpinBox,
)

from realsense_streamer import HttpStreamer
from signal_handler1 import SignalHandler
import database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --- HELPER FUNCTION FOR EXE PATHS ---
def get_app_path():
    """Get the path to the application (works for .py and .exe)"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    else:
        return Path(__file__).parent


def load_config(config_filename: str = "config.yaml") -> dict:
    """Load configuration from YAML file next to the executable"""
    try:
        config_path = get_app_path() / config_filename
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
            logger.info(f"Configuration loaded from {config_path}")
            return config if config else {}
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        return {}


def ndarray_to_qpixmap(img: np.ndarray, is_bgr: bool = True) -> QPixmap:
    """Convert numpy array to QPixmap"""
    if img is None or img.size == 0:
        return QPixmap()
    if is_bgr and img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    h, w = img.shape[:2]
    if img.ndim == 2:
        qimg = QImage(img.data, w, h, w, QImage.Format_Grayscale8)
    else:
        bytes_per_line = 3 * w
        qimg = QImage(img.data, w, h, bytes_per_line, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


def parse_mm_value(val):
    """Parse a value like '62 mm' or 62 into a float."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        m = re.match(r'([\d.]+)', val.strip())
        if m:
            return float(m.group(1))
    return 0.0


class SettingsDialog(QDialog):
    """Simplified settings dialog - serial and YOLO confidence only"""

    def __init__(
        self,
        parent=None,
        config=None,
        auto_send=True,
        serial_enabled=False,
        serial_status="Serial: Disconnected",
        yolo_conf=25,
    ):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setGeometry(100, 100, 500, 350)
        self.config = config or {}

        layout = QVBoxLayout()

        # YOLO Parameters Group
        yolo_group = QGroupBox("YOLO Classification Parameters")
        yolo_layout = QFormLayout()
        self.spin_yolo_conf = QSpinBox()
        self.spin_yolo_conf.setRange(5, 95)
        self.spin_yolo_conf.setValue(yolo_conf)
        self.spin_yolo_conf.setSuffix(" %")
        self.spin_yolo_conf.setToolTip("Minimum confidence for YOLO wheel classification")
        yolo_layout.addRow("YOLO Confidence:", self.spin_yolo_conf)
        yolo_group.setLayout(yolo_layout)
        layout.addWidget(yolo_group)

        # Serial Communication Group
        serial_group = QGroupBox("Serial Communication (Modbus)")
        serial_layout = QVBoxLayout()

        self.btn_toggle_serial = QPushButton("Enable Signal Detection")
        self.btn_toggle_serial.setCheckable(True)
        self.btn_toggle_serial.setChecked(serial_enabled)
        serial_layout.addWidget(self.btn_toggle_serial)

        self.lbl_serial_status = QLabel(serial_status)
        self.lbl_serial_status.setStyleSheet("color: #888; font-weight: bold;")
        serial_layout.addWidget(self.lbl_serial_status)

        self.chk_auto_send = QCheckBox("Auto-send measurements after classification")
        self.chk_auto_send.setChecked(auto_send)
        serial_layout.addWidget(self.chk_auto_send)

        serial_group.setLayout(serial_layout)
        layout.addWidget(serial_group)

        layout.addStretch()

        # OK / Cancel
        button_layout = QHBoxLayout()
        btn_ok = QPushButton("OK")
        btn_cancel = QPushButton("Cancel")
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        button_layout.addStretch()
        button_layout.addWidget(btn_ok)
        button_layout.addWidget(btn_cancel)
        layout.addLayout(button_layout)

        self.setLayout(layout)

    def get_values(self):
        return {
            'auto_send': self.chk_auto_send.isChecked(),
            'serial_enabled': self.btn_toggle_serial.isChecked(),
            'serial_status': self.lbl_serial_status.text(),
            'yolo_conf': self.spin_yolo_conf.value(),
        }


class WheelClassifierApp(QMainWindow):
    """Main application - classifies wheel type via YOLO and sends predefined measurements"""

    modbus_trigger_received = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wheel Classification System")
        self.resize(1400, 900)

        # Load configuration
        self.config = load_config()

        # Parse model specs from config
        self.model_specs = self._parse_model_specs()

        # HTTP/Top Camera streamer
        self.http_streamer = None
        self.http_thread = None

        # YOLO model (lazy loaded)
        self.yolo_model = None
        self.yolo_conf = 0.25

        # Runtime flags
        self.auto_send_enabled = True
        self.lbl_serial_status = QLabel("Serial: Disconnected")

        # Capture directory
        self.capture_dir = (get_app_path() / "captures").resolve()
        self.capture_dir.mkdir(parents=True, exist_ok=True)

        self._build_ui()

        # Timer for camera UI updates
        self.timer = QTimer(self)
        ui_fps = self.config.get('ui', {}).get('update_fps', 5)
        self.timer.setInterval(int(1000 / ui_fps))
        self.timer.timeout.connect(self.update_camera_ui)

        # Signal handler for Modbus
        self.signal_handler = SignalHandler(signal_callback=self.on_external_signal_detected)
        self.modbus_trigger_received.connect(self.handle_trigger_logic)
        self.signal_detection_enabled = False

        # Auto-capture state
        self._auto_capture_pending = False
        self._auto_capture_in_progress = False

        # Last classification result for serial
        self.last_predicted_model = None
        self.last_height_mm = None
        self.last_diameter_mm = None

        # Initialize database
        try:
            database.init_db()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Database initialization error: {e}")

        # Auto-start
        print("\n" + "=" * 60)
        print("SYSTEM AUTO-START SEQUENCE")
        print("=" * 60)
        print("Starting top camera...")
        self.start_camera()

        print("Starting Modbus signal detection...")
        detection_started = self.signal_handler.start_detection()
        if detection_started:
            self.signal_detection_enabled = True
            print("Signal detection READY")
        else:
            self.signal_detection_enabled = False
            print("Signal detection failed - Check COM port in settings.json")
        print("=" * 60 + "\n")

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------
    def _parse_model_specs(self):
        """Parse model height/diameter from config.yaml"""
        specs = {}
        http_cfg = self.config.get('http_streamer', {})
        models_cfg = http_cfg.get('models', {})
        for name, vals in models_cfg.items():
            if not isinstance(vals, dict):
                continue
            specs[name] = {
                'height_mm': parse_mm_value(vals.get('height', 0)),
                'diameter_mm': parse_mm_value(vals.get('diameter', 0)),
            }
        logger.info(f"Loaded model specs: {specs}")
        return specs

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        central.setStyleSheet("background-color: #C8E6C9;")

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        Taurus_logo = get_app_path() / "Taurus_logo.png"

        # ===== HEADER =====
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        title = QLabel("WHEEL CLASSIFICATION SYSTEM")
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: #0052CC;")

        title_container = QWidget()
        title_container_layout = QHBoxLayout(title_container)
        title_container_layout.setContentsMargins(0, 0, 0, 0)
        title_container_layout.addWidget(title, 1, Qt.AlignCenter)

        right_container = QWidget()
        right_layout = QHBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        logo_path = Taurus_logo
        if logo_path.exists():
            logo = QLabel()
            pixmap = QPixmap(str(logo_path))
            pixmap = pixmap.scaledToHeight(45, Qt.SmoothTransformation)
            logo.setPixmap(pixmap)
            right_layout.addWidget(logo, 0, Qt.AlignVCenter)

        date_time_layout = QVBoxLayout()
        self.lbl_date = QLabel(datetime.now().strftime("%d-%m-%Y"))
        self.lbl_time = QLabel(datetime.now().strftime("%H:%M:%S"))
        dt_font = QFont()
        dt_font.setPointSize(10)
        dt_font.setBold(True)
        self.lbl_date.setFont(dt_font)
        self.lbl_time.setFont(dt_font)
        self.lbl_date.setAlignment(Qt.AlignRight)
        self.lbl_time.setAlignment(Qt.AlignRight)
        date_time_layout.addWidget(self.lbl_date)
        date_time_layout.addWidget(self.lbl_time)
        right_layout.addLayout(date_time_layout)

        left_spacer = QWidget()
        header_layout.addWidget(left_spacer, 1)
        header_layout.addWidget(title_container, 1)
        header_layout.addWidget(right_container, 1, Qt.AlignRight)
        main_layout.addLayout(header_layout)

        # ===== BUTTON CONTROLS =====
        button_layout = QHBoxLayout()

        self.btn_model_data = QPushButton("Model Data")
        self.btn_model_data.setMinimumHeight(32)
        self.btn_model_data.setStyleSheet("background-color: #1B5E20; color: white; font-weight: bold; border-radius: 5px;")
        self.btn_model_data.clicked.connect(self.show_model_data)

        self.btn_generate_report = QPushButton("Generate Report")
        self.btn_generate_report.setMinimumHeight(32)
        self.btn_generate_report.setStyleSheet("background-color: #1B5E20; color: white; font-weight: bold; border-radius: 5px;")
        self.btn_generate_report.clicked.connect(self.generate_report)

        self.btn_settings = QPushButton("Settings")
        self.btn_settings.setMinimumHeight(32)
        self.btn_settings.setStyleSheet("background-color: #1B5E20; color: white; font-weight: bold; border-radius: 5px;")
        self.btn_settings.clicked.connect(self.open_settings)

        self.btn_test_serial = QPushButton("Test Serial Signal")
        self.btn_test_serial.setMinimumHeight(32)
        self.btn_test_serial.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold; border-radius: 5px;")
        self.btn_test_serial.clicked.connect(self.test_serial_signal)

        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(5)
        button_layout.addWidget(self.btn_model_data)
        button_layout.addWidget(self.btn_generate_report)
        button_layout.addWidget(self.btn_settings)
        button_layout.addWidget(self.btn_test_serial)
        button_layout.addStretch()
        main_layout.addLayout(button_layout)

        # ===== MAIN CONTENT =====
        content_layout = QHBoxLayout()

        # ----- LEFT COLUMN: Camera -----
        left_column = QVBoxLayout()

        camera_group = QGroupBox("Camera Controls")
        camera_group.setStyleSheet("background-color: white;")
        camera_ctrl = QHBoxLayout()
        self.btn_start_camera = QPushButton("Start Camera")
        self.btn_stop_camera = QPushButton("Stop Camera")
        self.btn_classify = QPushButton("Classify Wheel")
        for btn in [self.btn_start_camera, self.btn_stop_camera, self.btn_classify]:
            btn.setMinimumHeight(35)
            btn.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold; border-radius: 3px;")
        self.btn_classify.setStyleSheet("background-color: #C62828; color: white; font-weight: bold; font-size: 14px; border-radius: 5px;")
        self.btn_classify.setMinimumHeight(50)
        camera_ctrl.addWidget(self.btn_start_camera)
        camera_ctrl.addWidget(self.btn_stop_camera)
        camera_group.setLayout(camera_ctrl)
        left_column.addWidget(camera_group)
        left_column.addWidget(self.btn_classify)

        # Live feed
        live_group = QGroupBox("Top Camera - Live Feed")
        live_group.setStyleSheet("background-color: white;")
        live_layout = QVBoxLayout()
        self.lbl_camera_feed = QLabel("Top Camera\nNot Available")
        self.lbl_camera_feed.setAlignment(Qt.AlignCenter)
        self.lbl_camera_feed.setMinimumHeight(350)
        self.lbl_camera_feed.setMinimumWidth(500)
        self.lbl_camera_feed.setStyleSheet("background:#222; color:#bbb;")
        live_layout.addWidget(self.lbl_camera_feed)
        live_group.setLayout(live_layout)
        left_column.addWidget(live_group)

        # Classification result image
        result_group = QGroupBox("Classification Result")
        result_group.setStyleSheet("background-color: white;")
        result_layout = QVBoxLayout()
        self.lbl_result_image = QLabel("Classification Result\nNot Available")
        self.lbl_result_image.setAlignment(Qt.AlignCenter)
        self.lbl_result_image.setMinimumHeight(350)
        self.lbl_result_image.setMinimumWidth(500)
        self.lbl_result_image.setStyleSheet("background:#222; color:#bbb;")
        result_layout.addWidget(self.lbl_result_image)

        self.lbl_classification_info = QLabel("Waiting for classification...")
        self.lbl_classification_info.setAlignment(Qt.AlignCenter)
        self.lbl_classification_info.setStyleSheet("background:#1a1a1a; color:#0f0; padding:10px; font-size:12px;")
        self.lbl_classification_info.setWordWrap(True)
        result_layout.addWidget(self.lbl_classification_info)
        result_group.setLayout(result_layout)
        left_column.addWidget(result_group)

        content_layout.addLayout(left_column, 2)

        # ----- RIGHT COLUMN: Analytics -----
        right_column = QVBoxLayout()

        # Wheel Count
        wheel_count_group = QGroupBox("Wheel Count by Model")
        wheel_count_group.setStyleSheet("background-color: white;")
        wc_layout = QVBoxLayout()

        today_layout = QHBoxLayout()
        self.lbl_today_count = QLabel("Today's Count: 0")
        today_layout.addWidget(self.lbl_today_count)
        today_layout.addStretch()
        wc_layout.addLayout(today_layout)

        month_layout = QHBoxLayout()
        self.lbl_month_count = QLabel(f"{datetime.now().strftime('%B')} Month's Count: 0")
        month_layout.addWidget(self.lbl_month_count)
        month_layout.addStretch()
        wc_layout.addLayout(month_layout)

        self.table_wheel_count = QTableWidget(3, 2)
        self.table_wheel_count.setHorizontalHeaderLabels(["Model", "Count"])
        self.table_wheel_count.setMinimumHeight(120)
        wc_layout.addWidget(self.table_wheel_count)
        wheel_count_group.setLayout(wc_layout)
        right_column.addWidget(wheel_count_group)
        self.update_wheel_count_display()

        # Detection Results
        results_group = QGroupBox("Detection Results")
        results_group.setStyleSheet("background-color: white;")
        results_layout = QVBoxLayout()

        for label_text, attr_name, default_val in [
            ("Predicted Model:", "lbl_result_model", "N/A"),
            ("Height (config):", "lbl_result_height", "0.0 mm"),
            ("Diameter (config):", "lbl_result_diameter", "0.0 mm"),
            ("Confidence:", "lbl_result_confidence", "N/A"),
            ("Status:", "lbl_result_status", "N/A"),
        ]:
            row = QHBoxLayout()
            row.addWidget(QLabel(label_text))
            row.addStretch()
            lbl = QLabel(default_val)
            setattr(self, attr_name, lbl)
            row.addWidget(lbl)
            results_layout.addLayout(row)

        results_group.setLayout(results_layout)
        right_column.addWidget(results_group)

        # Inspection History
        history_group = QGroupBox("Recent Inspection History (Last 10)")
        history_group.setStyleSheet("background-color: white;")
        hist_layout = QVBoxLayout()
        self.table_history = QTableWidget(10, 4)
        self.table_history.setHorizontalHeaderLabels(["Time", "Model", "Diameter (mm)", "Height (mm)"])
        self.table_history.setColumnWidth(0, 100)
        self.table_history.setMinimumHeight(250)
        hist_layout.addWidget(self.table_history)
        history_group.setLayout(hist_layout)
        right_column.addWidget(history_group)
        self.update_inspection_history()

        # System Status
        status_group = QGroupBox("System Status")
        status_group.setStyleSheet("background-color: white;")
        st_layout = QVBoxLayout()
        status_data = [
            ("Camera Status:", "OFF - Standby"),
            ("Last Activity:", "No activity yet"),
            ("Processing Mode:", "Auto (YOLO Classification)"),
        ]
        for label_text, val_text in status_data:
            row = QHBoxLayout()
            row.addWidget(QLabel(label_text))
            row.addStretch()
            v = QLabel(val_text)
            v.setStyleSheet("color: #1B5E20;")
            row.addWidget(v)
            st_layout.addLayout(row)
        status_group.setLayout(st_layout)
        right_column.addWidget(status_group)

        content_layout.addLayout(right_column, 1)
        main_layout.addLayout(content_layout, 1)

        # ===== STATUS BAR =====
        self.lbl_status = QLabel("Ready. Workflow: Start Camera -> Wait for Modbus signal (or click Classify Wheel)")
        self.lbl_status.setStyleSheet("background-color: #2E7D32; color: white; padding: 10px; font-weight: bold; border-radius: 3px;")
        main_layout.addWidget(self.lbl_status)

        # Connect buttons
        self.btn_start_camera.clicked.connect(self.start_camera)
        self.btn_stop_camera.clicked.connect(self.stop_camera)
        self.btn_classify.clicked.connect(self.classify_wheel)

        # Date/time timer
        self.date_timer = QTimer(self)
        self.date_timer.timeout.connect(self.update_date_time)
        self.date_timer.start(1000)

    # ------------------------------------------------------------------
    # Date / time
    # ------------------------------------------------------------------
    def update_date_time(self):
        self.lbl_date.setText(datetime.now().strftime("%d-%m-%Y"))
        self.lbl_time.setText(datetime.now().strftime("%H:%M:%S"))

    # ------------------------------------------------------------------
    # Camera
    # ------------------------------------------------------------------
    def start_camera(self):
        if self.http_streamer and self.http_streamer.is_running:
            return
        try:
            http_cfg = self.config.get('http_streamer', {})
            url = http_cfg.get('url', "http://192.168.100.50:8080/stream-hd")
            timeout = http_cfg.get('timeout', 5)
            self.http_streamer = HttpStreamer(url=url, timeout=timeout)
            self.http_thread = threading.Thread(target=self.http_streamer.run, daemon=True)
            self.http_thread.start()
            logger.info(f"Top camera started: {url}")
            self.timer.start()
            self.lbl_status.setText("Top camera started - waiting for frames...")
        except Exception as e:
            QMessageBox.critical(self, "Camera Error", f"Failed to start camera: {e}")
            logger.error(f"Camera error: {e}", exc_info=True)

    def stop_camera(self):
        if self.http_streamer:
            self.http_streamer.stop_flag = True
            if self.http_thread and self.http_thread.is_alive():
                self.http_thread.join(timeout=1.0)
            self.http_streamer = None
            self.http_thread = None
        self.timer.stop()
        self.lbl_status.setText("Camera stopped")

    def update_camera_ui(self):
        try:
            if self.http_streamer and self.http_streamer.is_running and self.http_streamer.color_frame is not None:
                self.lbl_camera_feed.setPixmap(
                    ndarray_to_qpixmap(self.http_streamer.color_frame, is_bgr=True).scaled(
                        self.lbl_camera_feed.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                )
        except Exception as e:
            logger.error(f"UI update error: {e}")

    # ------------------------------------------------------------------
    # YOLO
    # ------------------------------------------------------------------
    def _get_yolo_model(self):
        if self.yolo_model is None:
            try:
                from ultralytics import YOLO
                model_path = get_app_path() / 'best.pt'
                if model_path.exists():
                    self.yolo_model = YOLO(str(model_path))
                    logger.info(f"YOLO model loaded from {model_path}")
                else:
                    logger.error(f"YOLO model not found: {model_path}")
            except Exception as e:
                logger.error(f"Failed to load YOLO model: {e}")
        return self.yolo_model

    # ------------------------------------------------------------------
    # Classify wheel (main logic)
    # ------------------------------------------------------------------
    def _classify_image(self, image):
        """Run YOLO classification on image. Returns (class_name, confidence, annotated_image) or (None, None, None)."""
        model = self._get_yolo_model()
        if model is None:
            return None, None, None
        try:
            results = model.predict(image, imgsz=224, verbose=False)
            result = results[0]
            probs = result.probs

            if probs is None:
                print(f"[YOLO] No classification result.")
                logger.warning("YOLO: No classification probabilities returned")
                return None, None, None

            top1_idx = probs.top1
            top1_conf = probs.top1conf.item()
            class_name = result.names[top1_idx]

            print(f"[YOLO] Classified: {class_name} ({top1_conf:.2%})")

            # Filter by confidence threshold
            if top1_conf < self.yolo_conf:
                print(f"[YOLO] Confidence {top1_conf:.2%} below threshold {self.yolo_conf:.0%}")
                logger.warning(f"YOLO: Low confidence {top1_conf:.2%}")
                return None, None, None

            # Skip if classified as background
            if class_name.lower() == "background":
                print(f"[YOLO] Classified as background, skipping")
                return None, None, None

            # Build annotated image with classification label overlay
            annotated = image.copy()
            label = f"{class_name} {top1_conf:.0%}"
            (tw, th_text), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 1.5, 3)
            cv2.rectangle(annotated, (10, 10), (20 + tw, 20 + th_text + 10), (0, 255, 0), -1)
            cv2.putText(annotated, label, (15, 15 + th_text),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3, cv2.LINE_AA)

            logger.info(f"YOLO classification: {class_name} ({top1_conf:.2%})")
            return class_name, top1_conf, annotated
        except Exception as e:
            logger.error(f"YOLO classification failed: {e}")
            return None, None, None

    def classify_wheel(self):
        """Capture frame, classify with YOLO, update UI, and optionally send via Modbus."""
        # Grab a frame (retry up to 2 seconds)
        frame = None
        for _ in range(20):
            frame = self.http_streamer.color_frame
            if frame is not None:
                break
            time.sleep(0.1)
        if frame is None:
            self.lbl_classification_info.setText("No frame available from camera.")
            self.lbl_result_status.setText("No Frame")
            self.lbl_result_status.setStyleSheet("color: red; font-weight: bold;")
            return

        # Save debug copy
        debug_path = os.path.join(self.capture_dir, "last_classify_frame.png")
        cv2.imwrite(debug_path, frame)
        print(f"[DEBUG] Frame saved: {debug_path} shape={frame.shape}")

        # Show captured frame in result panel
        self.lbl_result_image.setPixmap(
            ndarray_to_qpixmap(frame, is_bgr=True).scaled(
                self.lbl_result_image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

        # Run YOLO classification
        class_name, conf, annotated = self._classify_image(frame)

        if class_name is None:
            self.lbl_classification_info.setText("No Detection\nYOLO returned no valid classification.")
            self.lbl_result_status.setText("No Detection")
            self.lbl_result_status.setStyleSheet("color: red; font-weight: bold;")
            return

        # Display annotated image
        self.lbl_result_image.setPixmap(
            ndarray_to_qpixmap(annotated, is_bgr=True).scaled(
                self.lbl_result_image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

        # Lookup predefined specs from config
        specs = self.model_specs.get(class_name, {})
        height_mm = specs.get('height_mm', 0.0)
        diameter_mm = specs.get('diameter_mm', 0.0)

        if height_mm == 0.0 and diameter_mm == 0.0:
            info_text = (f"Detected: {class_name} ({conf:.0%})\n"
                         f"WARNING: No config specs found for model '{class_name}'.\n"
                         f"Available models: {list(self.model_specs.keys())}")
            self.lbl_classification_info.setText(info_text)
            self.lbl_result_status.setText("Config Missing")
            self.lbl_result_status.setStyleSheet("color: orange; font-weight: bold;")
            logger.warning(f"No config specs for predicted class: {class_name}")
        else:
            info_text = (f"Detected: {class_name} ({conf:.0%})\n"
                         f"Height: {height_mm:.1f} mm | Diameter: {diameter_mm:.1f} mm")
            self.lbl_classification_info.setText(info_text)
            self.lbl_result_status.setText("Classified OK")
            self.lbl_result_status.setStyleSheet("color: green; font-weight: bold;")

        # Update results panel
        self.lbl_result_model.setText(class_name)
        self.lbl_result_height.setText(f"{height_mm:.1f} mm")
        self.lbl_result_diameter.setText(f"{diameter_mm:.1f} mm")
        self.lbl_result_confidence.setText(f"{conf:.1%}")

        # Store for serial
        self.last_predicted_model = class_name
        self.last_height_mm = height_mm
        self.last_diameter_mm = diameter_mm

        # Save to database
        self.save_inspection_to_database(height_mm, diameter_mm, class_name)
        self.update_wheel_count_display()
        self.update_inspection_history()

        # Send via Modbus if enabled
        if self.auto_send_enabled and self.signal_detection_enabled:
            self.send_measurements_via_serial()

        self.lbl_status.setText(f"Classified: {class_name} | H={height_mm:.1f}mm D={diameter_mm:.1f}mm")

    # ------------------------------------------------------------------
    # Modbus trigger flow
    # ------------------------------------------------------------------
    def on_external_signal_detected(self, signal_type="MODBUS_FRAME"):
        print(f"[BACKGROUND THREAD] Modbus signal received: {signal_type}")
        self.modbus_trigger_received.emit()

    @Slot()
    def handle_trigger_logic(self):
        print(f"\n{'=' * 60}")
        print(f"[MAIN THREAD] Received trigger signal")
        print(f"{'=' * 60}")

        if self._auto_capture_pending or self._auto_capture_in_progress:
            logger.info("Auto-capture already in progress; ignoring")
            return

        self._auto_capture_pending = True
        print("[MAIN THREAD] Scheduling classify in 1.0 second...")
        QTimer.singleShot(1000, self._auto_classify)

    def _auto_classify(self):
        if self._auto_capture_in_progress:
            return
        self._auto_capture_pending = False
        self._auto_capture_in_progress = True
        try:
            logger.info("=== AUTO-CLASSIFY START ===")
            self.classify_wheel()
            logger.info("=== AUTO-CLASSIFY COMPLETE ===")
        except Exception as e:
            logger.error(f"Error during auto-classify: {e}", exc_info=True)
        finally:
            self._auto_capture_in_progress = False
    

    # ------------------------------------------------------------------
    # Serial
    # ------------------------------------------------------------------
    def send_measurements_via_serial(self):
        if not self.signal_detection_enabled or not self.auto_send_enabled:
            return
        model_name = self.last_predicted_model or "Unknown"
        height_mm = self.last_height_mm if self.last_height_mm else 0.0
        diameter_mm = self.last_diameter_mm if self.last_diameter_mm else 0.0

        if height_mm == 0.0 and diameter_mm == 0.0:
            logger.warning("No valid measurements to send")
            return

        detection_confirmed = True
        is_pass = (height_mm > 0.0 and diameter_mm > 0.0)

        success = self.signal_handler.send_measurement_data(
            model_name, diameter_mm, height_mm,
            is_pass=is_pass,
            detection_confirmed=detection_confirmed
        )
        if success:
            logger.info(f"Sent via Modbus: {model_name} H={height_mm:.2f}mm D={diameter_mm:.2f}mm")
            self.lbl_serial_status.setText(f"Serial: Sent ({model_name} H={height_mm:.1f} D={diameter_mm:.1f})")
        else:
            logger.error("Failed to send via serial")

    def test_serial_signal(self):
        if not self.signal_handler or not self.signal_handler.is_running:
            QMessageBox.warning(self, "Serial Test", "Signal detection is not running.\nEnable it in Settings.")
            return

        self.test_signal_received = False
        original_callback = self.signal_handler.signal_callback

        def test_callback(signal_type="MODBUS_FRAME"):
            self.test_signal_received = True
            print("SIGNAL DETECTED during test!")

        self.signal_handler.signal_callback = test_callback
        for _ in range(50):
            QApplication.processEvents()
            time.sleep(0.1)
            if self.test_signal_received:
                break
        self.signal_handler.signal_callback = original_callback

        if self.test_signal_received:
            QMessageBox.information(self, "Serial Test PASSED", "Serial signal received correctly!")
        else:
            QMessageBox.warning(self, "Serial Test FAILED",
                                "No signal detected.\nCheck COM port, cable, and PLC settings.")

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def open_settings(self):
        dlg = SettingsDialog(
            parent=self,
            config=self.config,
            auto_send=self.auto_send_enabled,
            serial_enabled=self.signal_detection_enabled,
            serial_status=self.lbl_serial_status.text(),
            yolo_conf=int(self.yolo_conf * 100),
        )
        if dlg.exec() == QDialog.Accepted:
            vals = dlg.get_values()
            self.auto_send_enabled = vals['auto_send']
            self.yolo_conf = vals['yolo_conf'] / 100.0

            if vals.get('serial_enabled'):
                if not self.signal_detection_enabled:
                    success = self.signal_handler.start_detection()
                    if success:
                        self.signal_detection_enabled = True
                        self.lbl_serial_status.setText("Serial: Connected & Monitoring")
                    else:
                        self.signal_detection_enabled = False
                        self.lbl_serial_status.setText("Serial: Connection Failed")
                        QMessageBox.warning(self, "Serial Error", "Failed to start serial detection.")
            else:
                if self.signal_detection_enabled:
                    self.signal_handler.stop_detection()
                    self.signal_detection_enabled = False
                    self.lbl_serial_status.setText("Serial: Disconnected")

            self.lbl_status.setText(f"Settings updated. YOLO conf={self.yolo_conf:.0%}")

    # ------------------------------------------------------------------
    # Database / Reports
    # ------------------------------------------------------------------
    def save_inspection_to_database(self, height_mm, diameter_mm, model_name):
        try:
            part_no = f"WHL-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            database.add_inspection(
                part_no=part_no,
                model_type=model_name,
                diameter_mm=diameter_mm if diameter_mm else 0.0,
                height_mm=height_mm if height_mm else 0.0,
                image_path_top=None,
                image_path_side=None,
            )
            logger.info(f"Inspection saved: {part_no}")
        except Exception as e:
            logger.error(f"Error saving to database: {e}", exc_info=True)

    def update_wheel_count_display(self):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            year = datetime.now().strftime("%Y")
            month = datetime.now().strftime("%m")
            total_today, model_counts_today, _ = database.get_daily_report(today)
            self.lbl_today_count.setText(f"Today's Count: {total_today}")
            total_month, _ = database.get_monthly_report(year, month)
            self.lbl_month_count.setText(f"{datetime.now().strftime('%B')} Month's Count: {total_month}")

            self.table_wheel_count.setRowCount(0)
            if model_counts_today:
                for model_type, count in model_counts_today:
                    r = self.table_wheel_count.rowCount()
                    self.table_wheel_count.insertRow(r)
                    self.table_wheel_count.setItem(r, 0, QTableWidgetItem(str(model_type)))
                    self.table_wheel_count.setItem(r, 1, QTableWidgetItem(str(count)))
            else:
                for _ in range(3):
                    r = self.table_wheel_count.rowCount()
                    self.table_wheel_count.insertRow(r)
                    self.table_wheel_count.setItem(r, 0, QTableWidgetItem("---"))
                    self.table_wheel_count.setItem(r, 1, QTableWidgetItem("0"))
        except Exception as e:
            logger.error(f"Error updating wheel count: {e}")

    def update_inspection_history(self):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            _, _, inspections = database.get_daily_report(today)
            self.table_history.setRowCount(0)
            for i, insp in enumerate(inspections[:10]):
                self.table_history.insertRow(i)
                ts = insp[3]
                if isinstance(ts, str):
                    t_str = ts.split()[1][:8] if len(ts.split()) > 1 else ts[:8]
                else:
                    t_str = ts.strftime("%H:%M:%S")
                self.table_history.setItem(i, 0, QTableWidgetItem(t_str))
                self.table_history.setItem(i, 1, QTableWidgetItem(str(insp[2])))
                self.table_history.setItem(i, 2, QTableWidgetItem(f"{insp[4]:.1f}" if insp[4] else "N/A"))
                self.table_history.setItem(i, 3, QTableWidgetItem(f"{insp[5]:.1f}" if insp[5] else "N/A"))
        except Exception as e:
            logger.error(f"Error updating history: {e}")

    def show_model_data(self):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            total_count, model_counts, inspections = database.get_daily_report(today)

            dialog = QDialog(self)
            dialog.setWindowTitle("Model Data - Today's Inspections")
            dialog.setGeometry(100, 100, 800, 600)
            layout = QVBoxLayout()
            layout.addWidget(QLabel(f"<h2>Total Inspections Today: {total_count}</h2>"))

            if model_counts:
                txt = "<h3>By Model:</h3>"
                for m, c in model_counts:
                    txt += f"<p><b>{m}:</b> {c} wheels</p>"
                layout.addWidget(QLabel(txt))

            table = QTableWidget(len(inspections), 6)
            table.setHorizontalHeaderLabels(["ID", "Part No", "Model", "Time", "Diameter (mm)", "Height (mm)"])
            for i, insp in enumerate(inspections):
                table.setItem(i, 0, QTableWidgetItem(str(insp[0])))
                table.setItem(i, 1, QTableWidgetItem(str(insp[1]) if insp[1] else "N/A"))
                table.setItem(i, 2, QTableWidgetItem(str(insp[2])))
                ts = insp[3]
                table.setItem(i, 3, QTableWidgetItem(ts if isinstance(ts, str) else ts.strftime("%H:%M:%S")))
                table.setItem(i, 4, QTableWidgetItem(f"{insp[4]:.2f}" if insp[4] else "N/A"))
                table.setItem(i, 5, QTableWidgetItem(f"{insp[5]:.2f}" if insp[5] else "N/A"))
            layout.addWidget(table)

            close_btn = QPushButton("Close")
            close_btn.clicked.connect(dialog.accept)
            layout.addWidget(close_btn)
            dialog.setLayout(layout)
            dialog.exec()
        except Exception as e:
            QMessageBox.critical(self, "Model Data Error", f"Error: {e}")

    def generate_report(self):
        try:
            dialog = QDialog(self)
            dialog.setWindowTitle("Generate Report")
            dialog.setGeometry(100, 100, 400, 200)
            layout = QVBoxLayout()
            layout.addWidget(QLabel("<h3>Select Report Type:</h3>"))

            btn_daily = QPushButton("Daily Report (Today)")
            btn_monthly = QPushButton("Monthly Report")
            btn_daily.clicked.connect(lambda: self._gen_daily(dialog))
            btn_monthly.clicked.connect(lambda: self._gen_monthly(dialog))
            layout.addWidget(btn_daily)
            layout.addWidget(btn_monthly)
            dialog.setLayout(layout)
            dialog.exec()
        except Exception as e:
            QMessageBox.critical(self, "Report Error", f"Error: {e}")

    def _gen_daily(self, parent_dialog=None):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            total, model_counts, inspections = database.get_daily_report(today)
            text = f"DAILY INSPECTION REPORT\nDate: {today}\n{'=' * 50}\n\nTotal: {total}\n\n"
            if model_counts:
                text += "By Model:\n"
                for m, c in model_counts:
                    text += f"  {m}: {c}\n"
            text += f"\n{'=' * 50}\nDetails:\n\n"
            for insp in inspections:
                text += f"ID: {insp[0]} | Model: {insp[2]} | Diameter: {insp[4]:.2f}mm | Height: {insp[5]:.2f}mm\n"
            report_file = self.capture_dir / f"daily_report_{today}.txt"
            with open(report_file, 'w') as f:
                f.write(text)
            QMessageBox.information(self, "Report", f"Saved to:\n{report_file}")
            if parent_dialog:
                parent_dialog.accept()
        except Exception as e:
            QMessageBox.critical(self, "Report Error", f"Error: {e}")

    def _gen_monthly(self, parent_dialog=None):
        try:
            year = datetime.now().strftime("%Y")
            month = datetime.now().strftime("%m")
            month_name = datetime.now().strftime("%B %Y")
            total, model_counts = database.get_monthly_report(year, month)
            text = f"MONTHLY INSPECTION REPORT\nPeriod: {month_name}\n{'=' * 50}\n\nTotal: {total}\n\n"
            if model_counts:
                text += "By Model:\n"
                for m, c in model_counts:
                    text += f"  {m}: {c}\n"
            report_file = self.capture_dir / f"monthly_report_{year}_{month}.txt"
            with open(report_file, 'w') as f:
                f.write(text)
            QMessageBox.information(self, "Report", f"Saved to:\n{report_file}")
            if parent_dialog:
                parent_dialog.accept()
        except Exception as e:
            QMessageBox.critical(self, "Report Error", f"Error: {e}")


    def closeEvent(self, event):
        """Clean up signal handler and camera on window close."""
        self.signal_handler.stop_detection()
        self.stop_camera()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = WheelClassifierApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
