"""Train-new-model window.

Flow: operator enters a unique name + dimensions, uploads a folder of at least
`min_training_images` good samples, and clicks Train. The trainer uses up to
MAX_UPLOAD_IMAGES uploads plus rotated copies of each. Training runs in the background
(TrainThread); on success the model is registered and becomes classifiable
immediately. "Capture Live" is a stub for now.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QProcess, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from .. import theme
from ...core import history, registry
from ...core.models import ModelData


def _restart_app() -> None:
    """Relaunch the app in a fresh process, then quit this one. Returns: None."""
    QProcess.startDetached(sys.executable, sys.argv)
    QCoreApplication.quit()
from ...core.state import AppState
from ...inference.trainer import (
    MAX_TRAIN_IMAGES,
    MAX_UPLOAD_IMAGES,
    TrainThread,
    count_images,
)


class TrainDialog(QDialog):
    def __init__(self, state: AppState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.min_images = state.app_settings.min_training_images
        self._image_dir: str | None = None
        self._thread: TrainThread | None = None

        self.setWindowTitle("Train New Model")
        self.resize(440, 340)
        self.setStyleSheet(f"background: {theme.WINDOW_BG};")

        layout = QVBoxLayout(self)

        # --- form: name + dimensions ---
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setStyleSheet("background: white; padding: 4px;")
        self.name_edit.textChanged.connect(self._revalidate)

        self.diameter = QDoubleSpinBox()
        self.diameter.setRange(0, 100000)
        self.diameter.setDecimals(1)
        self.diameter.valueChanged.connect(self._revalidate)
        self.height = QDoubleSpinBox()
        self.height.setRange(0, 100000)
        self.height.setDecimals(1)
        self.height.valueChanged.connect(self._revalidate)

        form.addRow("Model name", self.name_edit)
        form.addRow("Diameter (mm)", self.diameter)
        form.addRow("Height (mm)", self.height)
        layout.addLayout(form)

        # --- image source ---
        self.count_label = QLabel(
            f"Provide {self.min_images}–{MAX_UPLOAD_IMAGES} good sample images — "
            f"each is also rotated 90/180/270° for training."
        )
        self.count_label.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(self.count_label)

        src_row = QHBoxLayout()
        self.capture_btn = QPushButton("Capture Live")
        self.capture_btn.setEnabled(False)          # on hold for now
        self.upload_btn = QPushButton("Upload Folder…")
        self.upload_btn.clicked.connect(self._pick_folder)
        for b in (self.capture_btn, self.upload_btn):
            b.setMinimumHeight(34)
            b.setStyleSheet(theme.normal_button_qss())
        src_row.addWidget(self.capture_btn)
        src_row.addWidget(self.upload_btn)
        layout.addLayout(src_row)

        # --- progress + status ---
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)                # indeterminate
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 12px;")
        layout.addWidget(self.status)

        layout.addStretch(1)

        # --- actions ---
        act = QHBoxLayout()
        act.addStretch(1)
        self.cancel_btn = QPushButton("Close")
        self.cancel_btn.clicked.connect(self.reject)
        self.train_btn = QPushButton("Train")
        self.train_btn.clicked.connect(self._start_training)
        for b in (self.cancel_btn, self.train_btn):
            b.setMinimumHeight(36)
            b.setStyleSheet(theme.normal_button_qss())
        act.addWidget(self.cancel_btn)
        act.addWidget(self.train_btn)
        layout.addLayout(act)

        self._revalidate()

    # ------------------------------------------------------------- helpers
    def _pick_folder(self) -> None:
        """Choose the folder of good samples and show the image count."""
        folder = QFileDialog.getExistingDirectory(self, "Select folder of good images")
        if not folder:
            return
        self._image_dir = folder
        n = count_images(folder)
        used = min(n, MAX_UPLOAD_IMAGES)
        total = min(used * 4, MAX_TRAIN_IMAGES)
        extra = (
            f"only the first {MAX_UPLOAD_IMAGES} will be used; "
            if n > MAX_UPLOAD_IMAGES else ""
        )
        self.count_label.setText(
            f"{n} image(s) found in '{Path(folder).name}' "
            f"(need ≥ {self.min_images}; {extra}training on {total} "
            f"images incl. rotations)."
        )
        self._revalidate()

    def _validation_error(self) -> str:
        """The first unmet training requirement, or "" if ready to train.

        Returns: a human-readable reason string (empty when everything's set).
        """
        name = self.name_edit.text().strip()
        n_imgs = count_images(self._image_dir) if self._image_dir else 0
        if not name:
            return "Enter a model name."
        if registry.exists(name):
            return f"A model named '{name}' already exists."
        if registry.at_capacity():
            return "Model limit reached — delete an unused model to train a new one."
        if self._image_dir is None:
            return "Upload a folder of good images."
        if n_imgs < self.min_images:
            return (
                f"Need at least {self.min_images} images — found {n_imgs} "
                "directly in this folder (images in sub-folders don't count)."
            )
        if not (self.diameter.value() > 0 and self.height.value() > 0):
            return "Diameter and height must be greater than 0."
        return ""

    def _revalidate(self) -> None:
        """Update the live hint line as fields change. The Train button stays
        clickable so a click can pop a dialog if something's missing. Returns: None."""
        self.status.setText(self._validation_error())

    def _set_busy(self, busy: bool) -> None:
        """Toggle the controls + progress bar while training runs."""
        self.progress.setVisible(busy)
        for w in (self.name_edit, self.diameter, self.height,
                  self.upload_btn, self.train_btn, self.cancel_btn):
            w.setEnabled(not busy)

    # ------------------------------------------------------------- training
    def _start_training(self) -> None:
        """Validate, then kick off the background TrainThread. Returns: None."""
        # Notify the user with a popup if any requirement isn't met.
        err = self._validation_error()
        if err:
            QMessageBox.warning(self, "Cannot start training", err)
            return

        name = self.name_edit.text().strip()
        self._set_busy(True)
        self.status.setText("Starting training…")

        self._thread = TrainThread(self._image_dir, name)
        self._thread.progress.connect(self.status.setText)
        self._thread.done.connect(self._on_done)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def _on_done(self, checkpoint_path: str) -> None:
        """Register the trained model and close. Runs on the main thread."""
        name = self.name_edit.text().strip()
        try:
            registry.add_model(
                ModelData(name, self.diameter.value(), self.height.value()),
                checkpoint_path,
            )
        except Exception as e:
            self._set_busy(False)
            QMessageBox.warning(self, "Could not save model", str(e))
            return
        history.log_action(
            self.state.username, "train_model",
            f"{name} (images={count_images(self._image_dir)}, "
            f"diameter={self.diameter.value()}, height={self.height.value()})",
        )
        self.state.notify_models_changed()          # engine picks it up next inspection
        self._set_busy(False)

        # Alert + offer a full restart to apply the change everywhere.
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Training complete")
        box.setText(f"Model '{name}' trained and added.")
        box.setInformativeText(
            "The model is ready to use now. Restart the app to fully apply the changes?"
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.Yes)
        choice = box.exec()
        self.accept()
        if choice == QMessageBox.Yes:
            _restart_app()

    def _on_failed(self, error: str) -> None:
        self._set_busy(False)
        self.status.setText("")
        QMessageBox.critical(self, "Training failed", error)
