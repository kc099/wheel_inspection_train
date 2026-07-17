"""HTTP camera streaming (the machine's IP camera).

Reads an MJPEG/HTTP stream (URL + timeout from settings.json → 'http_streamer')
on a background QThread and emits frames to the UI. OpenCV is optional: if it's
not installed the app still runs, the stream just stays off.

Why a background thread: reading frames blocks; doing it on the UI thread would
freeze the window. Frames are handed to the UI as QImage via a Qt signal, which
is the thread-safe way to cross into the GUI thread.
"""

from __future__ import annotations

import socket
from urllib.parse import urlparse

import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

# OpenCV is optional; the app must still run on a machine without it.
try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False


def is_reachable(url: str, timeout: int = 3) -> bool:
    """Quick TCP check that the camera's host:port is reachable.

    Why: opening a video stream can hang for a while, so before starting the
    stream we do a fast socket connect to tell "camera not connected" apart from
    "connected but bad path". Returns: True if the host:port accepts a TCP
    connection within `timeout` seconds, else False.
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            return False
        with socket.create_connection((host, port), timeout=max(1, min(timeout, 3))):
            return True
    except Exception:
        return False


def frame_to_qimage(frame_bgr: np.ndarray) -> QImage:
    """Convert an OpenCV BGR frame to a QImage (deep-copied).

    The .copy() detaches the QImage from the numpy buffer, which OpenCV may
    reuse/free on the next read. Returns: a standalone QImage.
    """
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    return QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()


class CameraStream(QThread):
    """Opens an HTTP stream and emits its frames until stopped.

    Signals:
      frame_ready(QImage) — a new frame (already on its way to the UI thread).
      error(str)          — could not open/read the stream.
      stopped()           — the loop has ended and the capture is released.
    """

    frame_ready = Signal(QImage)
    error = Signal(str)
    stopped = Signal()

    def __init__(self, url: str, timeout: int = 5) -> None:
        super().__init__()
        self._url = url
        self._timeout_ms = max(1, int(timeout)) * 1000
        self._stop = False
        self._latest: np.ndarray | None = None   # most recent BGR frame (for Capture)

    def latest_frame(self) -> np.ndarray | None:
        """The most recent frame read, or None. Returns: BGR ndarray | None."""
        return self._latest

    def stop(self) -> None:
        """Ask the loop to end and wait for it. Returns: None."""
        self._stop = True
        self.wait(3000)

    def run(self) -> None:
        if not CV2_AVAILABLE:
            self.error.emit("OpenCV not installed — camera streaming unavailable")
            return
        try:
            cap = cv2.VideoCapture(self._url)
            # Best-effort open/read timeouts (backend-dependent; harmless if ignored).
            try:
                cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self._timeout_ms)
                cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, self._timeout_ms)
            except Exception:
                pass

            if not cap.isOpened():
                self.error.emit(f"Could not open camera stream: {self._url}")
                self.stopped.emit()
                return

            misses = 0
            while not self._stop:
                ok, frame = cap.read()
                if not ok or frame is None:
                    misses += 1
                    if misses > 30:                 # stream dropped for ~1s → give up
                        self.error.emit("Camera stream ended or is unreachable")
                        break
                    self.msleep(33)
                    continue
                misses = 0
                self._latest = frame
                self.frame_ready.emit(frame_to_qimage(frame))
                self.msleep(33)                     # ~30 fps cap

            cap.release()
        except Exception as e:
            self.error.emit(f"Camera error: {e}")
        finally:
            self.stopped.emit()
