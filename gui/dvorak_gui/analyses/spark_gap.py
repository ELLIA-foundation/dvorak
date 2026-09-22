"""Spark-gap event analysis: param form, detect preview, LOD overlay."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lib.paths import CAMPAIGN_SPARK_GAP
from lib.waveform import load_waveform

from ..campaign_import import load_campaign_module
from ..catalog import CaptureRecord
from ..kinds import KIND_WAVEFORM
from ..registry import FAMILY_ANALYSIS, AnalysisSpec, Option, get, register
from ..widgets.param_form import ParamForm
from ..widgets.trace_plot import EventMark, TracePlot, format_seconds
from ..window import AnalysisWindow
from ..workers import WorkerHandle

SPARK_GAP_ID = "spark_gap"

_sg = load_campaign_module(CAMPAIGN_SPARK_GAP, "spark_gap")

OPTIONS = (
    Option(
        "drop_threshold_v",
        "number",
        "Drop threshold",
        _sg.DEFAULT_DROP_THRESHOLD_V,
        unit="V",
        help="Minimum voltage drop in the coarse window to flag a breakdown.",
    ),
    Option(
        "drop_window_s",
        "number",
        "Drop window",
        _sg.DEFAULT_DROP_WINDOW_S,
        unit="s",
        help="Coarse drop measurement window (default 100 ns).",
    ),
    Option(
        "merge_gap_s",
        "number",
        "Merge gap",
        _sg.DEFAULT_MERGE_GAP_S,
        unit="s",
        help="Merge coarse hits closer than this (default 5 µs).",
    ),
    Option(
        "coarse_step_s",
        "number",
        "Coarse step",
        _sg.DEFAULT_COARSE_STEP_S,
        unit="s",
        help="Stride of the coarse scan (default 50 ns).",
    ),
    Option(
        "scope_bw_hz",
        "number",
        "Scope bandwidth",
        _sg.DEFAULT_SCOPE_BW_HZ,
        unit="Hz",
        help="Annotation only: 10–90 limit is 0.35 / BW.",
    ),
    Option(
        "capacitance_f",
        "number",
        "Capacitance",
        None,
        unit="F",
        help="Optional. Enables energy / charge / L estimates.",
        optional=True,
    ),
    Option(
        "include_first",
        "bool",
        "Include first-cycle events",
        False,
        help="Keep startup / atypical ramps in typical stats (Phase 4).",
    ),
)


def _detect_events(
    time_s: Any,
    voltage_v: Any,
    metadata: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    result = _sg.analyze_waveform(
        time_s,
        voltage_v,
        metadata,
        drop_threshold_v=params["drop_threshold_v"],
        drop_window_s=params["drop_window_s"],
        merge_gap_s=params["merge_gap_s"],
        coarse_step_s=params["coarse_step_s"],
        scope_bw_hz=params["scope_bw_hz"],
        capacitance_f=params.get("capacitance_f"),
    )
    events = [
        {
            "event_index": event.event_index,
            "first_cycle": bool(event.first_cycle),
            "t_break": float(event.t_break),
            "v_breakdown": float(event.v_breakdown),
        }
        for event in result.events
    ]
    return {
        "events": events,
        "detection": dict(result.detection),
        "n_events": int(result.detection.get("n_events", len(events))),
        "n_typical": int(result.detection.get("n_typical", 0)),
    }


class SparkGapWindow(AnalysisWindow):
    def __init__(self, spec: AnalysisSpec, controller: Any) -> None:
        self._plot: TracePlot | None = None
        self._form: ParamForm | None = None
        self._load_worker = WorkerHandle()
        self._detect_worker = WorkerHandle()
        self._load_gen = 0
        self._detect_gen = 0
        self._time_s = None
        self._voltage_v = None
        self._loaded_meta: dict[str, Any] = {}
        self._preview: dict[str, Any] | None = None
        self._loading_path: Path | None = None
        super().__init__(spec, controller)
        self.resize(1400, 740)
        splitter = self.centralWidget()
        if isinstance(splitter, QSplitter) and splitter.count() == 3:
            splitter.setStretchFactor(1, 1)
            splitter.setStretchFactor(2, 3)
            splitter.setSizes([320, 300, 780])

    def _build_menu(self) -> None:
        super()._build_menu()
        file_menu = self.menuBar().actions()[0].menu()
        assert file_menu is not None
        close_act = next(action for action in file_menu.actions() if action.text() == "Close")
        save_recipe = QAction("Save recipe…", self)
        save_recipe.triggered.connect(self._save_recipe)
        load_recipe = QAction("Load recipe…", self)
        load_recipe.triggered.connect(self._load_recipe)
        file_menu.insertAction(close_act, save_recipe)
        file_menu.insertAction(close_act, load_recipe)

        view_menu = self.menuBar().addMenu("&View")
        reset_act = QAction("Reset view", self)
        reset_act.setShortcut(QKeySequence("Home"))
        reset_act.triggered.connect(self._reset_view)
        view_menu.addAction(reset_act)

    def _workspace_panes(self) -> list[QWidget]:
        self._form = ParamForm(self._spec.options)
        self._capture_label = QLabel("Open a waveform, then detect events.")
        self._capture_label.setWordWrap(True)
        self._count_label = QLabel("No detection yet.")

        self._detect_btn = QPushButton("Detect events")
        self._detect_btn.setDefault(True)
        self._detect_btn.clicked.connect(self._detect)
        reset_params = QPushButton("Defaults")
        reset_params.clicked.connect(self._form.reset_defaults)
        buttons = QHBoxLayout()
        buttons.addWidget(self._detect_btn, stretch=1)
        buttons.addWidget(reset_params)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["#", "t (ms)", "V_bd (kV)", "first"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setMaximumHeight(220)

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("Detection parameters"))
        layout.addWidget(self._capture_label)
        layout.addWidget(self._form)
        layout.addLayout(buttons)
        layout.addWidget(self._count_label)
        layout.addWidget(self._table)
        layout.addStretch(1)

        self._plot = TracePlot()
        self._plot.status_changed.connect(self._on_plot_status)
        return [panel, self._plot]

    def _on_capture_selected(self, record: CaptureRecord | None) -> None:
        if record is None:
            self.statusBar().showMessage("Select a capture")
            return
        if not self._is_accepted(record):
            kinds = ", ".join(self._spec.accepted_kinds) or "any"
            self.statusBar().showMessage(
                f"{record.kind} — this analysis opens {kinds} captures"
            )
            return
        self.statusBar().showMessage(str(record.path))

    def _handle_opened(self, record: CaptureRecord) -> None:
        self._load_gen += 1
        self._preview = None
        self._time_s = None
        self._voltage_v = None
        assert self._plot is not None
        self._plot.clear_waveform()
        self._fill_table([])
        self._count_label.setText("No detection yet.")
        self._capture_label.setText(f"Loading {record.stem}…")
        self._loaded_meta = dict(record.metadata)
        self._loading_path = record.path
        self.statusBar().showMessage(f"Loading {record.stem}…")
        self._load_worker.start(
            load_waveform,
            record.path,
            on_finished=self._receive_loaded,
            on_failed=self._receive_load_failed,
        )

    def _receive_loaded(self, result: object) -> None:
        if self._plot is None or self._chosen is None:
            return
        if self._loading_path is not None and self._chosen.path != self._loading_path:
            return
        time_s, voltage_v, metadata = result  # type: ignore[misc]
        self._time_s = time_s
        self._voltage_v = voltage_v
        self._loaded_meta = dict(metadata or {})
        self._plot.set_waveform(time_s, voltage_v)
        self._capture_label.setText(self._chosen.stem)
        self._on_plot_status("")

    def _receive_load_failed(self, message: str) -> None:
        self.statusBar().showMessage("Load failed")
        QMessageBox.warning(self, "Could not load waveform", message)

    def _detect(self) -> None:
        if self._time_s is None or self._voltage_v is None or self._form is None:
            QMessageBox.information(self, self.windowTitle(), "Open a waveform first.")
            return
        try:
            params = self._form.values()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid parameters", str(exc))
            return
        self._detect_gen += 1
        self._detect_btn.setEnabled(False)
        self._count_label.setText("Detecting…")
        self.statusBar().showMessage("Detecting events…")
        self._detect_worker.start(
            _detect_events,
            self._time_s,
            self._voltage_v,
            self._loaded_meta,
            params,
            on_finished=self._receive_detect,
            on_failed=self._receive_detect_failed,
        )

    def _receive_detect(self, result: object) -> None:
        self._detect_btn.setEnabled(True)
        if self._plot is None or not isinstance(result, dict):
            return
        self._preview = result
        events = [
            EventMark(
                t_break=float(row["t_break"]),
                v_breakdown=float(row["v_breakdown"]),
                first_cycle=bool(row["first_cycle"]),
                event_index=int(row["event_index"]),
            )
            for row in result["events"]
        ]
        self._plot.set_events(events)
        self._fill_table(events)
        n_events = int(result.get("n_events", len(events)))
        n_typical = int(result.get("n_typical", 0))
        self._count_label.setText(f"{n_events} breakdowns ({n_typical} typical)")
        self._on_plot_status("")

    def _receive_detect_failed(self, message: str) -> None:
        self._detect_btn.setEnabled(True)
        self._count_label.setText("Detection failed.")
        self.statusBar().showMessage("Detection failed")
        QMessageBox.warning(self, "Detection failed", message)

    def _fill_table(self, events: list[EventMark]) -> None:
        self._table.setRowCount(len(events))
        for row, event in enumerate(events):
            values = (
                str(event.event_index),
                f"{event.t_break * 1e3:.3f}",
                f"{event.v_breakdown / 1000.0:.2f}",
                "yes" if event.first_cycle else "",
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                self._table.setItem(row, column, item)
        self._table.resizeColumnsToContents()

    def _on_plot_status(self, plot_status: str) -> None:
        prefix = self._plot_status_prefix()
        extra = ""
        if self._preview is not None:
            extra = (
                f"{self._preview.get('n_events', 0)} events "
                f"({self._preview.get('n_typical', 0)} typical)"
            )
        bits = [bit for bit in (prefix, extra, plot_status) if bit]
        if bits:
            self.statusBar().showMessage(" · ".join(bits))

    def _plot_status_prefix(self) -> str:
        meta = self._loaded_meta
        parts: list[str] = []
        model = meta.get("model_id")
        if model:
            parts.append(str(model))
        dt = meta.get("x_increment_s")
        if dt:
            parts.append(f"dt={format_seconds(float(dt))}")
        return " · ".join(parts)

    def _reset_view(self) -> None:
        if self._plot is not None:
            self._plot.reset_view()

    def _save_recipe(self) -> None:
        if self._form is None:
            return
        try:
            options = self._form.values()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid parameters", str(exc))
            return
        default = "spark_gap_recipe.json"
        if self._chosen is not None:
            default = f"{self._chosen.stem}_recipe.json"
        chosen, _filter = QFileDialog.getSaveFileName(
            self, "Save recipe", default, "JSON (*.json)"
        )
        if not chosen:
            return
        payload = {"analysis": SPARK_GAP_ID, "options": options}
        Path(chosen).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.statusBar().showMessage(f"Wrote {chosen}")

    def _load_recipe(self) -> None:
        if self._form is None:
            return
        chosen, _filter = QFileDialog.getOpenFileName(
            self, "Load recipe", "", "JSON (*.json)"
        )
        if not chosen:
            return
        try:
            payload = json.loads(Path(chosen).read_text(encoding="utf-8"))
            options = payload.get("options", payload)
            self._form.set_values(options)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Could not load recipe", str(exc))
            return
        self.statusBar().showMessage(f"Loaded {chosen}")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._load_gen += 1
        self._detect_gen += 1
        self._load_worker.cancel()
        self._detect_worker.cancel()
        super().closeEvent(event)


def _create_window(controller: Any) -> SparkGapWindow:
    return SparkGapWindow(get(SPARK_GAP_ID), controller)


register(
    AnalysisSpec(
        id=SPARK_GAP_ID,
        title="Spark Gap Analysis",
        description=(
            "Detect breakdown events on spark-gap oscilloscope traces, preview "
            "markers, then run the full diagnostic figure pack."
        ),
        family=FAMILY_ANALYSIS,
        accepted_kinds=(KIND_WAVEFORM,),
        options=OPTIONS,
        window_factory=_create_window,
    )
)
