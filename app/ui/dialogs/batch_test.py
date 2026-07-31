"""Batch image inspection dialog."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .. import theme
from ...core import registry
from ...core.paths import BACKGROUND_PATH
from ...core.verdict import match_percentages
from ...inference import measure


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}
_UNKNOWN_ROW = "#FADBD8"


class BatchTestDialog(QDialog):
    """Run the registered recognition models over every image in a folder."""

    def __init__(self, engine, state=None, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.state = state
        self._folder = ""
        # Measurement context: same reference + settings the live path uses, so
        # a batch reproduces exactly what an inspection would report.
        self._background = measure.load_background(BACKGROUND_PATH)
        self._specs = {m.name: m for m in registry.list_models()}
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
        self.stop_button = QPushButton("Stop")
        self.stop_button.setStyleSheet(theme.normal_button_qss())
        self.stop_button.setEnabled(False)          # only while a run is going
        self.stop_button.clicked.connect(self._stop_batch)
        controls.addWidget(self.stop_button)
        self.export_button = QPushButton("Export CSV")
        self.export_button.setStyleSheet(theme.normal_button_qss())
        self.export_button.setEnabled(False)            # enabled after a run
        self.export_button.clicked.connect(self._export_csv)
        controls.addWidget(self.export_button)
        layout.addLayout(controls)

        # Full per-image data from the last run (incl. per-model scores and
        # absolute confidences the table doesn't show) — feeds Export CSV.
        self._results: list[dict] = []
        self._cancelled = False          # set by Stop, checked each iteration

        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels([
            "SNo", "Selected Model", "Predicted Model", "Score",
            "Match Percentage", "Detected Dia (mm)", "Spec Dia (mm)",
            "Diff (mm)", "Threshold", "Status", "File Name",
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setStyleSheet("background: white; font-size: 11px;")
        header = self.table.horizontalHeader()
        for column in range(10):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(10, QHeaderView.Stretch)
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
        self.export_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._results = []
        self._cancelled = False
        unknown_count = 0
        try:
            px_mm = self.state.app_settings.pixel_to_mm if self.state else 0.885
            slider_thr = self.state.app_settings.mask_threshold if self.state else 0
            # All measurement controls come from measure_config.json now, so the
            # batch behaves exactly like a live inspection.
            cfg = measure.load_measure_config(slider_thr)
            do_measure = cfg["measure_diameter"]
            fit_method, thr = cfg["fit_method"], cfg["threshold"]
            if do_measure:
                print(f"[batch] measuring: fit={fit_method} "
                      f"threshold={thr} ({cfg['threshold_method']}) "
                      f"background={'yes' if self._background is not None else 'no'}",
                      flush=True)
            for number, path in enumerate(images, start=1):
                if self._cancelled:
                    break
                result = self.engine.classify(str(path))
                m = (measure.measure(str(path), self._background, px_mm, thr, fit_method)
                     if do_measure else measure.Measurement(reason="measurement off"))
                spec = self._specs.get(result.model)
                spec_mm = spec.diameter if spec else 0.0
                diff_mm = (m.diameter_mm - spec_mm) if (m.ok and spec_mm > 0) else None
                self._results.append({
                    "diameter_mm": m.diameter_mm if m.ok else None,
                    "diameter_px": m.diameter_px if m.ok else None,
                    "spec_diameter_mm": spec_mm,
                    "spec_pixel_diameter": spec.pixel_diameter if spec else 0.0,
                    "diff_mm": diff_mm,
                    "measure_ok": m.ok,
                    "measure_reason": m.reason,
                    "threshold_used": m.threshold_used,
                    "roundness": m.roundness,
                    "diameter_area_px": m.diameter_area_px,
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

                det_txt = f"{m.diameter_mm:.1f}" if m.ok else f"— ({m.reason})"
                spec_txt = f"{spec_mm:.0f}" if spec_mm > 0 else "—"
                diff_txt = f"{diff_mm:+.1f}" if diff_mm is not None else "—"
                thr_txt = ("—" if not do_measure else
                           f"{m.threshold_used}{'' if m.auto_threshold else ' fix'}")

                row = self.table.rowCount()
                self.table.insertRow(row)
                values = [str(number), selected_model, predicted, score, percentage,
                          det_txt, spec_txt, diff_txt, thr_txt, status, path.name]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if is_unknown:
                        item.setBackground(QColor(_UNKNOWN_ROW))
                        item.setForeground(QColor(theme.STATUS_ERROR))
                    self.table.setItem(row, column, item)
                unknown_count += int(is_unknown)
                self.summary.setText(f"Processing {number} of {len(images)} images…")
                # Pump the event queue. Without this the dialog never handles a
                # paint/ping while the loop runs, and Windows declares it "not
                # responding" — the loop was fine, the UI just looked dead. This
                # also lets Stop actually be clicked.
                QApplication.processEvents()
        finally:
            self.run_button.setEnabled(True)
            self.folder_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.export_button.setEnabled(bool(self._results))
        done = len(self._results)
        note = " (stopped early)" if self._cancelled else ""
        self.summary.setText(
            f"Completed {done} of {len(images)} image(s): "
            f"{unknown_count} not a known model/error row(s).{note}"
        )

    def _stop_batch(self) -> None:
        """Ask the running batch to stop after the current image. Returns: None."""
        self._cancelled = True
        self.summary.setText("Stopping after the current image…")

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
             "winner_score", "winner_confidence", "min_confidence_gate", "matched",
             # Diameter diagnostics — detected vs the model's spec.
             "detected_dia_mm", "spec_dia_mm", "diff_mm",
             "detected_dia_px", "spec_pixel_dia", "measure_ok",
             "measure_reason", "threshold_used", "roundness", "detected_area_px"]
            + [f"score_{n}" for n in model_names]
            + [f"conf_{n}" for n in model_names]
        )

        def num(v, fmt="{:.2f}"):
            """Blank cell rather than 'None' when a value is missing."""
            return fmt.format(v) if isinstance(v, (int, float)) else ""
        gate = getattr(self.engine, "min_confidence", "")
        try:
            with open(dest, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(header)
                for r in self._results:
                    writer.writerow(
                        [r["sno"], r["file"], r["selected_model"], r["ok"],
                         r["error"], r["winner"], f"{r['winner_score']:.4f}",
                         f"{r['winner_confidence']:.4f}", gate, r["matched"],
                         num(r["diameter_mm"], "{:.1f}"),
                         num(r["spec_diameter_mm"], "{:.1f}"),
                         num(r["diff_mm"], "{:+.1f}"),
                         num(r["diameter_px"], "{:.1f}"),
                         num(r["spec_pixel_diameter"], "{:.1f}"),
                         r["measure_ok"], r["measure_reason"], r["threshold_used"],
                         f"{r['roundness']:.3f}", num(r["diameter_area_px"], "{:.1f}")]
                        + [f"{r['scores'].get(n, ''):.4f}" if n in r["scores"] else ""
                           for n in model_names]
                        + [f"{r['confidences'].get(n, ''):.4f}" if n in r["confidences"] else ""
                           for n in model_names]
                    )
        except OSError as e:
            QMessageBox.warning(self, "Export failed", str(e))
            return
        self.summary.setText(f"Exported {len(self._results)} row(s) to {dest}")
