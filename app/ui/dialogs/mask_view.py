"""Binary-mask viewer — the diagnostic window for diameter measurement.

Shown after each inspection while "Show binary mask" is ticked. It is the tool
for tuning the mask threshold: if the wheel is not a clean solid blob here, the
measured diameter cannot be trusted.

Non-modal on purpose, so it can stay open while inspections keep running.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from .. import theme
from ..widgets import ImageArea


def mask_to_qimage(mask: np.ndarray) -> QImage:
    """Wrap a uint8 binary mask as a grayscale QImage (deep-copied)."""
    mask = np.ascontiguousarray(mask)
    h, w = mask.shape[:2]
    return QImage(mask.data, w, h, w, QImage.Format_Grayscale8).copy()


def bgr_to_qimage(bgr: np.ndarray) -> QImage:
    """Wrap an OpenCV BGR image as a QImage (deep-copied)."""
    rgb = np.ascontiguousarray(bgr[:, :, ::-1])
    h, w, ch = rgb.shape
    return QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()


class MaskViewDialog(QDialog):
    """Live view of the last inspection's binary mask + its measurement."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Binary Mask — measurement debug")
        self.resize(560, 520)
        self.setStyleSheet(f"background: {theme.WINDOW_BG};")

        lay = QVBoxLayout(self)
        self.image = ImageArea("No mask yet — run an inspection")
        self.image.setMinimumHeight(360)
        lay.addWidget(self.image, 1)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-size: 12px; font-family: Consolas;"
        )
        lay.addWidget(self.info)

        self.hint = QLabel(
            "The component should be ONE solid blob. Speckle or holes mean the "
            "threshold needs adjusting (Camera Controls → Mask threshold)."
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        lay.addWidget(self.hint)

    def update_mask(self, measurement) -> None:
        """Show a new Measurement (mask + fit overlay + numbers). Returns: None."""
        # Prefer the overlay — it shows the fitted hull and circle, which is
        # what you actually need to judge whether the fit is right.
        if measurement.overlay is not None:
            self.image.set_pixmap(QPixmap.fromImage(bgr_to_qimage(measurement.overlay)))
        elif measurement.mask is not None:
            self.image.set_pixmap(QPixmap.fromImage(mask_to_qimage(measurement.mask)))
        else:
            self.image.set_message("No mask produced")

        mode = "auto / Otsu" if measurement.auto_threshold else "fixed"
        bg = "YES" if measurement.used_background else "NO (plain threshold)"
        head = (f"background subtraction : {bg}\n"
                f"fit method             : {measurement.fit_method}\n"
                f"threshold used         : {measurement.threshold_used:8d}   ({mode})")
        thr = head
        if measurement.ok:
            warn = "" if measurement.roundness >= 0.90 else "   <-- NOT round!"
            self.info.setText(
                f"diameter (min-circle)  : {measurement.diameter_px:8.1f} px\n"
                f"diameter (area-equiv)  : {measurement.diameter_area_px:8.1f} px\n"
                f"diameter               : {measurement.diameter_mm:8.1f} mm\n"
                f"roundness              : {measurement.roundness:8.3f}{warn}\n"
                f"{thr}"
            )
        else:
            self.info.setText(f"Measurement failed: {measurement.reason}\n{thr}")
