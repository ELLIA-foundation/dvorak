"""Oscilloscope trace viewer: interactive pyqtgraph plot with viewport LOD."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from lib.waveform import load_waveform

from ..catalog import CaptureRecord
from ..kinds import KIND_WAVEFORM
from ..registry import FAMILY_ANALYSIS, AnalysisSpec, get, register
from ..rootexport import open_in_legacy_root, save_pdf
from ..widgets.trace_plot import TracePlot, format_seconds
from ..window import AnalysisWindow, _format_record
from ..workers import WorkerHandle

TRACE_ID = "trace"


class TraceWindow(AnalysisWindow):
    def __init__(self, spec: AnalysisSpec, controller: Any) -> None:
        self._plot: TracePlot | None = None
        self._worker = WorkerHandle()
        self._export_worker = WorkerHandle()
        self._load_gen = 0
        self._loaded_meta: dict[str, Any] = {}
        super().__init__(spec, controller)

    def _build_menu(self) -> None:
        super()._build_menu()
        file_menu = self.menuBar().actions()[0].menu()
        assert file_menu is not None
        close_act = next(action for action in file_menu.actions() if action.text() == "Close")
        export_act = QAction("Export plot…", self)
        export_act.setShortcut(QKeySequence.StandardKey.Save)
        export_act.triggered.connect(self._export_plot)
        legacy_act = QAction("Legacy ROOT", self)
        legacy_act.triggered.connect(self._open_legacy_root)
        pdf_act = QAction("Export PDF…", self)
        pdf_act.triggered.connect(self._export_pdf)
        file_menu.insertAction(close_act, export_act)
        file_menu.insertAction(close_act, pdf_act)
        file_menu.insertAction(close_act, legacy_act)

        view_menu = self.menuBar().addMenu("&View")
        reset_act = QAction("Reset view", self)
        reset_act.setShortcut(QKeySequence("Home"))
        reset_act.triggered.connect(self._reset_view)
        view_menu.addAction(reset_act)

    def _workspace_panes(self) -> list[QWidget]:
        self._plot = TracePlot()
        self._plot.status_changed.connect(self._on_plot_status)
        self._plot.legacy_root_requested.connect(self._open_legacy_root)
        self._plot.pdf_requested.connect(self._export_pdf)

        heading = QLabel("Capture")
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText("Select a capture in the catalogue.")
        meta = QWidget()
        meta_layout = QVBoxLayout(meta)
        meta_layout.addWidget(heading)
        meta_layout.addWidget(self._detail, stretch=1)
        return [self._plot, meta]

    def _handle_opened(self, record: CaptureRecord) -> None:
        self._load_gen += 1
        assert self._plot is not None
        self._plot.clear_waveform()
        self._loaded_meta = dict(record.metadata)
        self.statusBar().showMessage(f"Loading {record.stem}…")
        self._worker.start(
            load_waveform,
            record.path,
            on_finished=self._receive_loaded,
            on_failed=self._receive_failed,
        )

    def _receive_loaded(self, result: object) -> None:
        record = self._chosen
        if record is None:
            return
        self._on_loaded(self._load_gen, record, result)

    def _receive_failed(self, message: str) -> None:
        self._on_load_failed(self._load_gen, message)

    def _on_loaded(self, gen: int, record: CaptureRecord, result: object) -> None:
        if gen != self._load_gen or self._plot is None:
            return
        time_s, voltage_v, metadata = result  # type: ignore[misc]
        self._loaded_meta = dict(metadata or {})
        self._plot.set_waveform(time_s, voltage_v)
        self._detail.setPlainText(_format_record(record, accepted=True))
        self._on_plot_status(self._plot_status_prefix())

    def _on_load_failed(self, gen: int, message: str) -> None:
        if gen != self._load_gen:
            return
        self.statusBar().showMessage("Load failed")
        QMessageBox.warning(self, "Could not load waveform", message)

    def _on_plot_status(self, plot_status: str) -> None:
        prefix = self._plot_status_prefix()
        if plot_status:
            self.statusBar().showMessage(f"{prefix} · {plot_status}" if prefix else plot_status)
        elif prefix:
            self.statusBar().showMessage(prefix)

    def _plot_status_prefix(self) -> str:
        meta = self._loaded_meta
        parts: list[str] = []
        model = meta.get("model_id")
        if model:
            parts.append(str(model))
        dt = meta.get("x_increment_s")
        if dt:
            parts.append(f"dt={format_seconds(float(dt))}")
        channel = meta.get("channel")
        if channel is not None:
            parts.append(f"ch{channel}")
        return " · ".join(parts)

    def _reset_view(self) -> None:
        if self._plot is not None:
            self._plot.reset_view()

    def _export_plot(self) -> None:
        if self._plot is None or self._chosen is None:
            QMessageBox.information(self, self.windowTitle(), "Open a waveform first.")
            return
        default_dir = _plots_dir(self._chosen)
        default = default_dir / f"{self._chosen.stem}.png"
        chosen, _filter = QFileDialog.getSaveFileName(
            self,
            "Export plot",
            str(default),
            "PNG (*.png)",
        )
        if not chosen:
            return
        path = Path(chosen)
        try:
            self._plot.export_png(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self.statusBar().showMessage(f"Wrote {path}")

    def _publication_spec(self):
        if self._plot is None:
            return None
        name = self._chosen.stem if self._chosen is not None else "trace"
        return self._plot.publication_spec(name)

    def _open_legacy_root(self) -> None:
        spec = self._publication_spec()
        if spec is None:
            QMessageBox.information(self, self.windowTitle(), "Open a waveform first.")
            return
        open_in_legacy_root(
            self,
            self._controller.root,
            self._export_worker,
            spec,
            on_status=self.statusBar().showMessage,
        )

    def _export_pdf(self) -> None:
        spec = self._publication_spec()
        if spec is None or self._chosen is None:
            QMessageBox.information(self, self.windowTitle(), "Open a waveform first.")
            return
        default = _plots_dir(self._chosen) / f"{self._chosen.stem}.pdf"
        save_pdf(
            self,
            self._controller.root,
            self._export_worker,
            spec,
            default,
            on_status=self.statusBar().showMessage,
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._load_gen += 1
        self._worker.cancel()
        self._export_worker.cancel()
        super().closeEvent(event)


def _plots_dir(record: CaptureRecord) -> Path:
    try:
        from lib.paths import campaign_plots

        return campaign_plots(record.campaign)
    except Exception:
        return record.path.parent / "plots"


def _create_window(controller: Any) -> TraceWindow:
    return TraceWindow(get(TRACE_ID), controller)


register(
    AnalysisSpec(
        id=TRACE_ID,
        title="Oscilloscope Trace Analysis",
        description=(
            "Interactively plot captured oscilloscope waveforms. Pan and zoom "
            "time windows without drawing every sample."
        ),
        family=FAMILY_ANALYSIS,
        accepted_kinds=(KIND_WAVEFORM,),
        window_factory=_create_window,
    )
)
