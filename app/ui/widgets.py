"""Small reusable UI building blocks (layout only, no behavior)."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from . import theme


def card_shadow(widget: QWidget) -> None:
    """Apply the soft drop shadow the WPF cards use."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(16)
    effect.setColor(QColor(0, 0, 0, 45))
    effect.setOffset(0, 2)
    widget.setGraphicsEffect(effect)


class Card(QFrame):
    """White rounded card with border + drop shadow."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setStyleSheet(theme.card_qss())
        card_shadow(self)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 10, 12, 12)
        self._layout.setSpacing(8)

    def layout(self) -> QVBoxLayout:  # type: ignore[override]
        return self._layout


def section_header(text: str) -> QWidget:
    """Card header: a small accent bar + bold label (matches XAML)."""
    row = QWidget()
    hl = QHBoxLayout(row)
    hl.setContentsMargins(4, 2, 4, 4)
    hl.setSpacing(8)

    bar = QFrame()
    bar.setFixedSize(4, 16)
    bar.setStyleSheet(f"background-color: {theme.BTN_NORMAL}; border-radius: 2px;")

    label = QLabel(text)
    label.setStyleSheet(
        f"color: {theme.TEXT_PRIMARY}; font-size: 16px; font-weight: 600;"
    )

    hl.addWidget(bar)
    hl.addWidget(label)
    hl.addStretch(1)
    return row


def group_label(text: str) -> QLabel:
    """Small caps section label inside the controls column."""
    label = QLabel(text)
    label.setStyleSheet(
        f"color: {theme.TEXT_MUTED}; font-size: 10px; font-weight: 700;"
    )
    return label


class ImageArea(QFrame):
    """Dark image panel that shows either a placeholder or a scaled image."""

    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        self._placeholder_text = placeholder
        self._pixmap = None                       # original, unscaled QPixmap
        self.setMinimumSize(200, 200)
        self.setStyleSheet(
            f"background-color: {theme.IMAGE_BG}; border-radius: 6px;"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.label = QLabel(placeholder)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet(
            f"color: {theme.PLACEHOLDER}; font-size: 14px; background: transparent;"
        )
        layout.addWidget(self.label)

    def set_image(self, path: str) -> bool:
        """Show the image at `path`, scaled to fit. Returns: True if loaded."""
        pix = QPixmap(path)
        if pix.isNull():
            return False
        self.set_pixmap(pix)
        return True

    def set_pixmap(self, pixmap: QPixmap) -> None:
        """Show an already-loaded pixmap (e.g. a live camera frame). Returns: None."""
        self._pixmap = pixmap
        self._rescale()

    def clear(self) -> None:
        """Drop the image and show the placeholder again. Returns: None."""
        self._pixmap = None
        self.label.setPixmap(QPixmap())
        self.label.setText(self._placeholder_text)

    def set_message(self, text: str) -> None:
        """Show a status message instead of an image (e.g. camera down). Returns: None."""
        self._pixmap = None
        self.label.setPixmap(QPixmap())
        self.label.setText(text)

    def _rescale(self) -> None:
        """Fit the stored pixmap to the current widget size (keep aspect)."""
        if self._pixmap is None:
            return
        scaled = self._pixmap.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.label.setText("")
        self.label.setPixmap(scaled)

    def resizeEvent(self, event):   # noqa: N802  (Qt override)
        """Re-fit the image whenever the panel resizes."""
        super().resizeEvent(event)
        self._rescale()


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"color: {theme.CARD_BORDER};")
    return line
