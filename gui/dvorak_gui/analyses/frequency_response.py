"""Frequency-response viewer: two-pad JSROOT canvas and a Legacy ROOT button."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lib.paths import CAMPAIGN_FREQUENCY_RESPONSES

from ..campaign_import import load_campaign_module
from ..catalog import CaptureRecord
from ..jsrootview import JsRootView
from ..kinds import KIND_TABLE
from ..registry import FAMILY_ANALYSIS, AnalysisSpec, get, register
from ..rootexport import open_in_legacy_root, save_pdf
from ..widgets.figure_gallery import FigureGallery
from ..window import AnalysisWindow, _format_record
from ..workers import WorkerHandle

FREQ_ID = "frequency_response"
_plot = load_campaign_module(CAMPAIGN_FREQUENCY_RESPONSES, "frequency_plot")


class FrequencyWindow(AnalysisWindow):
    def __init__(self, spec: AnalysisSpec, controller: Any) -> None:
        self._view: JsRootView | None = None
        self._gallery: FigureGallery | None = None
        self._spec_payload: dict | None = None
        self._draw_worker = WorkerHandle()
        self._export_worker = WorkerHandle()
        self._meta: dict[str, Any] = {}
        super().__init__(spec, controller)
        self._controller.root.ready.connect(self._on_root_ready)
        if self._controller.root.report:
            self._on_root_ready(self._controller.root.report)

    def _build_menu(self) -> None:
        super()._build_menu()
        file_menu = self.menuBar().actions()[0].menu()
        assert file_menu is not None
        close_act = next(action for action in file_menu.actions() if action.text() == "Close")
        legacy_act = QAction("Legacy ROOT", self)
        legacy_act.triggered.connect(self._open_legacy_root)
        pdf_act = QAction("Export PDF…", self)
        pdf_act.triggered.connect(self._export_pdf)
        file_menu.insertAction(close_act, pdf_act)
        file_menu.insertAction(close_act, legacy_act)

    def _workspace_panes(self) -> list[QWidget]:
        self._view = JsRootView(self._controller.root.jsroot, self)
        self._gallery = FigureGallery()
        self._gallery.set_placeholder("No saved PNG for this sweep.")
        self._legacy = QPushButton("Legacy ROOT")
        self._legacy.setToolTip(
            "Open this sweep in the interactive ROOT GUI (root -l)"
        )
        self._legacy.clicked.connect(self._open_legacy_root)
        self._pdf = QPushButton("Save PDF…")
        self._pdf.clicked.connect(self._export_pdf)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self._pdf)
        buttons.addWidget(self._legacy)
        plot = QWidget()
        plot_layout = QVBoxLayout(plot)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.addWidget(self._view, stretch=1)
        plot_layout.addWidget(self._gallery, stretch=1)
        plot_layout.addLayout(buttons)
        self._gallery.hide()

        heading = QLabel("Capture")
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText("Select a frequency-response table.")
        meta = QWidget()
        meta_layout = QVBoxLayout(meta)
        meta_layout.addWidget(heading)
        meta_layout.addWidget(self._detail, stretch=1)
        return [plot, meta]

    def _on_root_ready(self, report: dict) -> None:
        if self._view is None:
            return
        path = str(report.get("jsroot") or "")
        if path:
            self._view.set_bundle(path)
        self._show_current()

    def _handle_opened(self, record: CaptureRecord) -> None:
        self._meta = dict(record.metadata)
        rows = _rows_for(record)
        self._detail.setPlainText(_format_record(record, accepted=True))
        if not rows:
            self._spec_payload = None
            self.statusBar().showMessage("This table has no frequency rows.")
            self._show_current()
            return
        bw = self._meta.get("scope_bw_hz")
        self._spec_payload = _plot.frequency_spec(rows, bw)
        stem = record.stem
        self._spec_payload["name"] = stem
        self._show_current()
        self.statusBar().showMessage(record.stem)

    def _show_current(self) -> None:
        if self._view is None or self._gallery is None:
            return
        png = _png_for(self._chosen) if self._chosen is not None else None
        if png is not None and png.is_file():
            self._gallery.set_figures([(png.stem, png)])
        else:
            self._gallery.clear()
        if self._spec_payload and self._view.usable:
            self._gallery.hide()
            self._view.show()
            self._draw()
            return
        if self._spec_payload is None:
            self._view.show()
            self._gallery.hide()
            self._view.show_message("Open a frequency-response table.")
            return
        if png is not None and png.is_file():
            self._view.hide()
            self._gallery.show()
            return
        self._gallery.hide()
        self._view.show()
        report = self._controller.root.report or {}
        self._view.show_message(
            str(report.get("error") or "ROOT is required to draw this sweep.")
        )

    def _draw(self) -> None:
        spec = self._spec_payload
        if spec is None or self._view is None or not self._view.usable:
            return
        gen = id(spec)

        def job() -> str:
            reply = self._controller.root.client.render(spec, outputs=("json",))
            return str(reply.get("json") or "")

        def done(result: object) -> None:
            if self._spec_payload is None or id(self._spec_payload) != gen:
                return
            if self._view is not None and isinstance(result, str) and result:
                self._view.draw(result)

        def failed(message: str) -> None:
            if self._spec_payload is None or id(self._spec_payload) != gen:
                return
            if self._view is not None:
                self._view.show_message(message)

        self._draw_worker.start(job, on_finished=done, on_failed=failed)

    def _open_legacy_root(self) -> None:
        if not self._spec_payload:
            QMessageBox.information(self, self.windowTitle(), "Open a table first.")
            return
        open_in_legacy_root(
            self,
            self._controller.root,
            self._export_worker,
            self._spec_payload,
            on_status=self.statusBar().showMessage,
        )

    def _export_pdf(self) -> None:
        if not self._spec_payload or self._chosen is None:
            QMessageBox.information(self, self.windowTitle(), "Open a table first.")
            return
        png = _png_for(self._chosen)
        if png is not None:
            default = png.with_suffix(".pdf")
        else:
            default = self._chosen.path.parent / "plots" / f"{self._chosen.stem}.pdf"
        save_pdf(
            self,
            self._controller.root,
            self._export_worker,
            self._spec_payload,
            default,
            on_status=self.statusBar().showMessage,
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._draw_worker.cancel()
        self._export_worker.cancel()
        super().closeEvent(event)


def _rows_for(record: CaptureRecord) -> list[dict]:
    rows = record.metadata.get("rows")
    if isinstance(rows, list) and rows:
        return [row for row in rows if isinstance(row, dict)]
    path = record.path
    if path.suffix.lower() == ".json" and path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        listed = payload.get("rows")
        if isinstance(listed, list):
            return [row for row in listed if isinstance(row, dict)]
    if path.suffix.lower() == ".csv" and path.is_file():
        try:
            with path.open(encoding="utf-8", newline="") as handle:
                return [dict(row) for row in csv.DictReader(handle)]
        except OSError:
            return []
    return []


def _png_for(record: CaptureRecord | None) -> Path | None:
    if record is None:
        return None
    return record.path.parent / "plots" / f"{record.stem}.png"


def _create_window(controller: Any) -> FrequencyWindow:
    return FrequencyWindow(get(FREQ_ID), controller)


register(
    AnalysisSpec(
        id=FREQ_ID,
        title="Frequency response",
        description=(
            "Plot V_scope / V_nominal versus frequency. The canvas is drawn "
            "with ROOT; Legacy ROOT opens it for publication export."
        ),
        family=FAMILY_ANALYSIS,
        accepted_kinds=(KIND_TABLE,),
        window_factory=_create_window,
    )
)
