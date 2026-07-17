"""Batch image inspection dialog."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .. import theme
from ...core.verdict import match_percentages


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}
_UNKNOWN_ROW = "#FADBD8"


class BatchTestDialog(QDialog):
    """Run the registered recognition models over every image in a folder."""

    def __init__(self, engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self._folder = ""
        self.setWindowTitle("Batch Run")
        self.resize(1050, 620)
        self.setStyleSheet(f"background: {theme.WINDOW_BG};")
        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Selected model:"))
        self.model_dropdown = QComboBox()
        self.model_dropdown.addItems(self.engine.model_names)
        self.model_dropdown.setStyleSheet("background: white; padding: 4px;")
        controls.addWidget(self.model_dropdown)
        self.folder_button = QPushButton("Upload Folder")
        self.folder_button.setStyleSheet(theme.normal_button_qss())
        self.folder_button.clicked.connect(self._choose_folder)
        controls.addWidget(self.folder_button)
        self.folder_label = QLabel("No folder selected")
        self.folder_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        controls.addWidget(self.folder_label, 1)
        self.run_button = QPushButton("Run Batch")
        self.run_button.setStyleSheet(theme.danger_button_qss())
        self.run_button.clicked.connect(self._run_batch)
        controls.addWidget(self.run_button)
        self.export_button = QPushButton("Export CSV")
        self.export_button.setStyleSheet(theme.normal_button_qss())
        self.export_button.setEnabled(False)            # enabled after a run
        self.export_button.clicked.connect(self._export_csv)
        controls.addWidget(self.export_button)
        layout.addLayout(controls)

        # Full per-image data from the last run (incl. per-model scores and
        # absolute confidences the table doesn't show) — feeds Export CSV.
        self._results: list[dict] = []

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "SNo", "Selected Model", "Predicted Model", "Score",
            "Match Percentage", "Status", "File Name",
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setStyleSheet("background: white; font-size: 11px;")
        header = self.table.horizontalHeader()
        for column in range(6):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        self.summary = QLabel("Choose a folder to begin.")
        self.summary.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(self.summary)

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select image folder")
        if folder:
            self._folder = folder
            self.folder_label.setText(folder)
            self.summary.setText("Ready to run batch inspection.")

    def _run_batch(self) -> None:
        if not self._folder:
            QMessageBox.warning(self, "Batch Run", "Select an image folder first.")
            return
        selected_model = self.model_dropdown.currentText()
        if not selected_model:
            QMessageBox.warning(self, "Batch Run", "No registered model is available.")
            return
        images = sorted(
            path for path in Path(self._folder).iterdir()
            if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
        )
        if not images:
            QMessageBox.information(self, "Batch Run", "The selected folder has no supported images.")
            return

        self.table.setRowCount(0)
        self.run_button.setEnabled(False)
        self.folder_button.setEnabled(False)
        self._results = []
        unknown_count = 0
        try:
            for number, path in enumerate(images, start=1):
                result = self.engine.classify(str(path))
                self._results.append({
                    "sno": number,
                    "file": path.name,
                    "selected_model": selected_model,
                    "ok": result.ok,
                    "error": result.error or "",
                    "winner": result.model,
                    "winner_score": result.score,
                    "winner_confidence": result.confidence,
                    "matched": result.matched,
                    "scores": dict(result.scores),
                    "confidences": dict(result.confidences),
                })
                if not result.ok:
                    predicted, score, percentage, status = "-", "-", "-", f"Error: {result.error}"
                    is_unknown = True
                elif not result.matched:
                    predicted, score, percentage, status = (
                        "Not a known model", f"{result.score:.3f}",
                        f"{match_percentages(result).get(result.model, 0):.1f}%", "Not a known model",
                    )
                    is_unknown = True
                else:
                    predicted, score, percentage = (
                        result.model, f"{result.score:.3f}",
                        f"{match_percentages(result).get(result.model, 0):.1f}%",
                    )
                    status = "Recognized" if result.model == selected_model else "Model mismatch"
                    is_unknown = False

                row = self.table.rowCount()
                self.table.insertRow(row)
                values = [str(number), selected_model, predicted, score, percentage, status, path.name]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if is_unknown:
                        item.setBackground(QColor(_UNKNOWN_ROW))
                        item.setForeground(QColor(theme.STATUS_ERROR))
                    self.table.setItem(row, column, item)
                unknown_count += int(is_unknown)
                self.summary.setText(f"Processing {number} of {len(images)} images...")
                self.repaint()
        finally:
            self.run_button.setEnabled(True)
            self.folder_button.setEnabled(True)
            self.export_button.setEnabled(bool(self._results))
        self.summary.setText(f"Completed {len(images)} image(s): {unknown_count} not a known model/error row(s).")

    def _export_csv(self) -> None:
        """Write the last run's full data (per-model scores + absolute
        confidences) to a CSV for offline analysis. Returns: None."""
        if not self._results:
            QMessageBox.information(self, "Export", "Run a batch first.")
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default = str(Path(self._folder or ".") / f"batch_run_{stamp}.csv")
        dest, _ = QFileDialog.getSaveFileName(self, "Export batch results",
                                              default, "CSV files (*.csv)")
        if not dest:
            return

        # Union of model names across rows, in stable order.
        model_names: list[str] = []
        for r in self._results:
            for n in r["scores"]:
                if n not in model_names:
                    model_names.append(n)

        header = (
            ["sno", "file", "selected_model", "ok", "error", "winner",
             "winner_score", "winner_confidence", "min_confidence_gate", "matched"]
            + [f"score_{n}" for n in model_names]
            + [f"conf_{n}" for n in model_names]
        )
        gate = getattr(self.engine, "min_confidence", "")
        try:
            with open(dest, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(header)
                for r in self._results:
                    writer.writerow(
                        [r["sno"], r["file"], r["selected_model"], r["ok"],
                         r["error"], r["winner"], f"{r['winner_score']:.4f}",
                         f"{r['winner_confidence']:.4f}", gate, r["matched"]]
                        + [f"{r['scores'].get(n, ''):.4f}" if n in r["scores"] else ""
                           for n in model_names]
                        + [f"{r['confidences'].get(n, ''):.4f}" if n in r["confidences"] else ""
                           for n in model_names]
                    )
        except OSError as e:
            QMessageBox.warning(self, "Export failed", str(e))
            return
        self.summary.setText(f"Exported {len(self._results)} row(s) to {dest}")
