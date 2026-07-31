"""Background reference dialog — capture or upload the empty-conveyor frame.

The reference is what diameter measurement subtracts to isolate the component
(docs/retraining_strategy.md §5). It is stored in a SUB-folder of the captures
directory so that bulk-deleting captures to build a new training set does not
destroy it, and so it never lands in a training upload.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .. import theme
from ..widgets import ImageArea, section_header
from ...core.paths import BACKGROUND_DIR, BACKGROUND_PATH


class BackgroundDialog(QDialog):
    """Set the empty-conveyor reference image.

    `live_frame_getter` returns the newest camera QImage (or None) so the
    dialog can grab from the running stream without owning the camera.
    """

    def __init__(self, live_frame_getter, parent=None) -> None:
        super().__init__(parent)
        self._get_live = live_frame_getter
        self._pending: QImage | None = None      # captured, not yet saved
        self._pending_path: str | None = None    # uploaded, not yet saved

        self.setWindowTitle("Background Reference")
        self.resize(640, 560)
        self.setStyleSheet(f"background: {theme.WINDOW_BG};")

        lay = QVBoxLayout(self)
        lay.addWidget(section_header("Empty-conveyor reference"))

        hint = QLabel(
            "Used to separate the component from the rig when measuring its "
            "diameter.\n\nThe conveyor must be COMPLETELY EMPTY in this image. "
            "Re-capture it whenever the camera is moved or the lighting changes."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        lay.addWidget(hint)

        self.preview = ImageArea("No background set")
        self.preview.setMinimumHeight(300)
        lay.addWidget(self.preview, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; font-size: 11px;")
        lay.addWidget(self.status)

        row = QHBoxLayout()
        self.capture_btn = QPushButton("Capture from Camera")
        self.capture_btn.clicked.connect(self._capture)
        self.upload_btn = QPushButton("Upload Image…")
        self.upload_btn.clicked.connect(self._upload)
        for b in (self.capture_btn, self.upload_btn):
            b.setMinimumHeight(34)
            b.setStyleSheet(theme.normal_button_qss())
            row.addWidget(b)
        lay.addLayout(row)

        act = QHBoxLayout()
        act.addStretch(1)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        self.save_btn = QPushButton("Save as Background")
        self.save_btn.clicked.connect(self._save)
        self.save_btn.setEnabled(False)
        for b in (self.close_btn, self.save_btn):
            b.setMinimumHeight(36)
            b.setStyleSheet(theme.normal_button_qss())
            act.addWidget(b)
        lay.addLayout(act)

        self._show_existing()

    # ----------------------------------------------------------------- helpers
    def _show_existing(self) -> None:
        """Preview the stored reference, if one exists."""
        if BACKGROUND_PATH.exists():
            self.preview.set_image(str(BACKGROUND_PATH))
            self.status.setText(f"Current reference: {BACKGROUND_PATH}")
        else:
            self.status.setText("No background reference set yet.")

    def _capture(self) -> None:
        """Freeze the newest live frame as the pending reference."""
        frame = self._get_live()
        if frame is None:
            QMessageBox.warning(
                self, "No live frame",
                "Start the camera stream first — there is no frame to capture.",
            )
            return
        self._pending, self._pending_path = frame, None
        self.preview.set_pixmap(QPixmap.fromImage(frame))
        self.status.setText("Captured. Confirm the conveyor is EMPTY, then Save.")
        self.save_btn.setEnabled(True)

    def _upload(self) -> None:
        """Pick a saved empty-conveyor photo as the pending reference."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select empty-conveyor image", "",
            "Images (*.png *.jpg *.jpeg *.bmp)",
        )
        if not path:
            return
        if not self.preview.set_image(path):
            QMessageBox.warning(self, "Bad image", "Could not load that image.")
            return
        self._pending_path, self._pending = path, None
        self.status.setText("Loaded. Confirm the conveyor is EMPTY, then Save.")
        self.save_btn.setEnabled(True)

    def _save(self) -> None:
        """Write the pending image to the reference location."""
        confirm = QMessageBox.question(
            self, "Confirm background",
            "Is the conveyor COMPLETELY EMPTY in this image?\n\n"
            "Anything left in the frame becomes part of the background and "
            "will be subtracted away during measurement.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
        try:
            if self._pending is not None:
                if not self._pending.save(str(BACKGROUND_PATH)):
                    raise OSError("could not write the image")
            elif self._pending_path:
                shutil.copy2(self._pending_path, BACKGROUND_PATH)
            else:
                return
        except Exception as e:
            QMessageBox.warning(self, "Could not save", str(e))
            return

        self.status.setText(f"Saved: {BACKGROUND_PATH}")
        self.accept()
