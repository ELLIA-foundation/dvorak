"""Spark-gap event analysis: param form, detect preview, full figure pack."""

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
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
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
from ..rootexport import open_in_legacy_root, save_pdf
from ..widgets.figure_gallery import open_local_path, reveal_in_folder
from ..widgets.param_form import ParamForm
from ..widgets.spark_explorer import SparkExplorer
from ..widgets.trace_plot import EventMark, TracePlot, format_seconds
from ..window import AnalysisWindow
from ..workers import WorkerHandle

SPARK_GAP_ID = "spark_gap"

# 01_overview is the interactive tab; the gallery shows the rest of the pack.
FIGURE_GALLERY_STEMS = (
    "02_sequential",
    "03_histograms",
    "04_collapse_overlay",
    "05_collapse_individuals",
    "06_ramp_overlay",
    "07_correlations",
    "08_post_collapse",
)

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
        help="Keep startup / atypical ramps in typical histograms and overlays.",
    ),
)


def analysis_output_dir(npz_path: Path) -> Path:
    return npz_path.parent / "plots" / f"analysis_{npz_path.stem}"


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
    return _payload_from_result(result)


def _run_full_analysis(
    npz_path: Path,
    out_dir: Path,
    params: dict[str, Any],
    time_s: Any,
    voltage_v: Any,
    metadata: dict[str, Any],
    run: dict[str, Any],
) -> dict[str, Any]:
    """Write the CLI figure pack. Load matplotlib Agg only on this thread."""
    import matplotlib

    matplotlib.use("Agg", force=True)
    report = load_campaign_module(CAMPAIGN_SPARK_GAP, "spark_gap_report")
    result = report.run_analysis(
        npz_path,
        out_dir,
        include_first=bool(params["include_first"]),
        show=False,
        drop_threshold_v=params["drop_threshold_v"],
        drop_window_s=params["drop_window_s"],
        merge_gap_s=params["merge_gap_s"],
        scope_bw_hz=params["scope_bw_hz"],
        capacitance_f=params.get("capacitance_f"),
        run=run,
        coarse_step_s=params["coarse_step_s"],
        time_s=time_s,
        voltage_v=voltage_v,
        metadata=metadata,
    )
    payload = _payload_from_result(result)
    payload["out_dir"] = str(out_dir)
    return payload


def _payload_from_result(result: Any) -> dict[str, Any]:
    events = list(result.events)
    return {
        "events": [event.to_record() for event in events],
        "snippets": [_event_snippet(event, result.time_s) for event in events],
        "detection": dict(result.detection),
        "summary": dict(result.summary),
        "n_events": int(result.detection.get("n_events", len(events))),
        "n_typical": int(result.detection.get("n_typical", 0)),
    }


def _event_snippet(event: Any, time_s: Any = None) -> dict[str, Any]:
    snippet: dict[str, Any] = {
        "event_index": int(event.event_index),
        "collapse_t_s": _as_float_list(event.collapse_t_s),
        "collapse_v": _as_float_list(event.collapse_v),
        "ramp_t_s": _as_float_list(event.ramp_t_s),
        "ramp_v": _as_float_list(event.ramp_v),
        "post_t_s": _as_float_list(event.post_t_s),
        "post_v": _as_float_list(event.post_v),
        "v10": _py_scalar(event.v10),
        "v90": _py_scalar(event.v90),
        "t10": _py_scalar(event.t10),
        "t90": _py_scalar(event.t90),
        "charge_rate": _py_scalar(event.charge_rate),
        "charge_intercept": _py_scalar(event.charge_intercept),
        "charge_r2": _py_scalar(event.charge_r2),
        "ramp_start_index": _py_scalar(event.ramp_start_index),
        "ramp_t0_s": None,
    }
    index = event.ramp_start_index
    if time_s is not None and index is not None:
        try:
            snippet["ramp_t0_s"] = float(time_s[int(index)])
        except (IndexError, TypeError, ValueError):
            snippet["ramp_t0_s"] = None
    return snippet


def _as_float_list(value: Any) -> list[float]:
    if value is None:
        return []
    if hasattr(value, "tolist"):
        return [float(item) for item in value.tolist()]
    return [float(item) for item in value]


def _py_scalar(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and value != value:
        return None
    return value


def _marks_from_rows(rows: list[dict[str, Any]]) -> list[EventMark]:
    marks: list[EventMark] = []
    for index, row in enumerate(rows):
        marks.append(
            EventMark(
                t_break=float(row["t_break"]),
                v_breakdown=float(row["v_breakdown"]),
                first_cycle=bool(row.get("first_cycle", False)),
                event_index=int(row.get("event_index", index)),
            )
        )
    return marks


def _root_specs(out_dir: Path) -> list[dict[str, Any]]:
    path = out_dir / "root_figures.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    figures = payload.get("figures") if isinstance(payload, dict) else None
    if not isinstance(figures, list):
        return []
    return [item for item in figures if isinstance(item, dict)]


def _gallery_items(out_dir: Path) -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    for stem in FIGURE_GALLERY_STEMS:
        path = out_dir / f"{stem}.png"
        if path.is_file():
            items.append((stem.replace("_", " "), path))
    return items


def _fmt_cell(name: str, value: Any) -> str:
    if value is None:
        return "—"
    if name == "first_cycle":
        return "yes" if value else ""
    if name == "event_index":
        return str(int(value))
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not (number == number):  # NaN
        return "—"
    if name == "t_break":
        return f"{number * 1e3:.3f}"
    if name in {"v_breakdown", "v_undershoot", "v_residual", "dv_collapse", "v_charge_start", "v_charge_end"}:
        return f"{number / 1000.0:.2f}"
    if name == "period_s":
        return f"{number * 1e6:.1f}"
    if name in {"t_collapse_10_90", "t_ring"}:
        return f"{number * 1e9:.1f}"
    if name in {"slew_collapse_mean", "slew_collapse_peak"}:
        return f"{number / 1e12:.2f}"
    if name == "charge_rate":
        return f"{number / 1e6:.1f}"
    if name == "rep_rate_hz":
        return f"{number:.1f}"
    if name == "recovery_s":
        return f"{number * 1e6:.1f}"
    if name == "charge_r2":
        return f"{number:.3f}"
    return f"{number:.4g}"


def _column_header(name: str) -> str:
    return {
        "event_index": "#",
        "first_cycle": "first",
        "t_break": "t (ms)",
        "v_breakdown": "V_bd (kV)",
        "v_undershoot": "V_us (kV)",
        "v_residual": "V_res (kV)",
        "dv_collapse": "dV (kV)",
        "period_s": "T (µs)",
        "rep_rate_hz": "f (Hz)",
        "t_collapse_10_90": "t10-90 (ns)",
        "slew_collapse_mean": "slew (kV/ns)",
        "slew_collapse_peak": "slew pk (kV/ns)",
        "t_ring": "t_ring (ns)",
        "charge_rate": "dV/dt (kV/ms)",
        "charge_r2": "R²",
        "v_charge_start": "V_ch0 (kV)",
        "v_charge_end": "V_ch1 (kV)",
        "recovery_s": "rec (µs)",
        "energy_j": "E (J)",
        "charge_c": "Q (C)",
        "source_current_a": "I (A)",
        "L_est_h": "L (H)",
    }.get(name, name)


def _format_summary(payload: dict[str, Any], out_dir: Path) -> str:
    detection = payload.get("detection") or {}
    summary = payload.get("summary") or {}
    events = payload.get("events") or []
    n_events = int(payload.get("n_events", detection.get("n_events", len(events))))
    n_typical = int(payload.get("n_typical", detection.get("n_typical", 0)))
    lines = [
        f"Events: {n_events}  typical: {n_typical}",
        f"Output: {out_dir}",
        "",
        "Typical-population summary",
    ]
    typical = summary.get("typical", {})
    scales = {
        "v_breakdown": (1e-3, "kV"),
        "period_s": (1e6, "us"),
        "charge_rate": (1e-6, "kV/ms"),
        "t_collapse_10_90": (1e9, "ns"),
    }
    for name, (scale, unit) in scales.items():
        stats = typical.get(name, {})
        if not stats or not stats.get("n"):
            continue
        mean = stats["mean"] * scale
        std = (stats.get("std") or 0.0) * scale
        cv = stats.get("cv")
        cv_txt = f"{100 * cv:.1f}%" if cv is not None else "—"
        lines.append(f"  {name:20s}  mean={mean:.3g} {unit}  std={std:.3g} {unit}  CV={cv_txt}")
    lines.append("  figures of merit")
    named = (
        ("v_bd_cv", summary.get("v_bd_cv"), "1"),
        ("recovery_ratio_mean", summary.get("recovery_ratio_mean"), "1"),
        ("period_jitter_frac", summary.get("period_jitter_frac"), "1"),
        ("corr_vbd_period", summary.get("corr_vbd_period"), "1"),
        ("corr_vbd_charge_rate", summary.get("corr_vbd_charge_rate"), "1"),
        ("conditioning_slope", summary.get("conditioning_slope"), "V/event"),
    )
    for name, value, unit in named:
        if value is None:
            lines.append(f"    {name} = —")
        elif name == "conditioning_slope":
            lines.append(f"    {name} = {value / 1000.0:.3g} kV/event")
        else:
            lines.append(f"    {name} = {value:.3g} {unit}")
    footer = detection.get("scope_t1090_limit_s")
    if footer is not None:
        bw = detection.get("scope_bw_hz")
        lines.append("")
        if bw:
            lines.append(f"Scope 10–90 limit ≈ {footer * 1e9:.2g} ns  (0.35 / {bw:g} Hz)")
        else:
            lines.append(f"Scope 10–90 limit ≈ {footer * 1e9:.2g} ns")
    return "\n".join(lines)


class SparkGapWindow(AnalysisWindow):
    def __init__(self, spec: AnalysisSpec, controller: Any) -> None:
        self._plot: TracePlot | None = None
        self._form: ParamForm | None = None
        self._explorer: SparkExplorer | None = None
        self._load_worker = WorkerHandle()
        self._detect_worker = WorkerHandle()
        self._analysis_worker = WorkerHandle()
        self._export_worker = WorkerHandle()
        self._load_gen = 0
        self._time_s = None
        self._voltage_v = None
        self._loaded_meta: dict[str, Any] = {}
        self._preview: dict[str, Any] | None = None
        self._loading_path: Path | None = None
        self._out_dir: Path | None = None
        self._pdf_path: Path | None = None
        self._busy_detect = False
        self._busy_analysis = False
        super().__init__(spec, controller)
        self.resize(1440, 800)
        splitter = self.centralWidget()
        if isinstance(splitter, QSplitter) and splitter.count() == 3:
            splitter.setStretchFactor(1, 1)
            splitter.setStretchFactor(2, 3)
            splitter.setSizes([320, 300, 820])

    def _build_menu(self) -> None:
        super()._build_menu()
        file_menu = self.menuBar().actions()[0].menu()
        assert file_menu is not None
        close_act = next(action for action in file_menu.actions() if action.text() == "Close")
        save_recipe = QAction("Save recipe…", self)
        save_recipe.triggered.connect(self._save_recipe)
        load_recipe = QAction("Load recipe…", self)
        load_recipe.triggered.connect(self._load_recipe)
        self._reveal_act = QAction("Reveal analysis folder", self)
        self._reveal_act.triggered.connect(self._reveal_output)
        self._pdf_act = QAction("Open analysis PDF", self)
        self._pdf_act.triggered.connect(self._open_pdf)
        file_menu.insertAction(close_act, save_recipe)
        file_menu.insertAction(close_act, load_recipe)
        file_menu.insertSeparator(close_act)
        file_menu.insertAction(close_act, self._reveal_act)
        file_menu.insertAction(close_act, self._pdf_act)
        file_menu.insertSeparator(close_act)

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
        self._run_btn = QPushButton("Run full analysis")
        self._run_btn.clicked.connect(self._run_analysis)
        reset_params = QPushButton("Defaults")
        reset_params.clicked.connect(self._form.reset_defaults)
        buttons = QHBoxLayout()
        buttons.addWidget(self._detect_btn, stretch=1)
        buttons.addWidget(self._run_btn, stretch=1)
        buttons.addWidget(reset_params)

        self._reveal_btn = QPushButton("Reveal folder")
        self._reveal_btn.clicked.connect(self._reveal_output)
        self._pdf_btn = QPushButton("Open PDF")
        self._pdf_btn.clicked.connect(self._open_pdf)
        outputs = QHBoxLayout()
        outputs.addWidget(self._reveal_btn)
        outputs.addWidget(self._pdf_btn)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["#", "t (ms)", "V_bd (kV)", "first"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setMaximumHeight(180)

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("Detection parameters"))
        layout.addWidget(self._capture_label)
        layout.addWidget(self._form)
        layout.addLayout(buttons)
        layout.addWidget(self._count_label)
        layout.addWidget(self._table)
        layout.addLayout(outputs)
        layout.addStretch(1)

        self._plot = TracePlot()
        self._plot.status_changed.connect(self._on_plot_status)
        self._plot.legacy_root_requested.connect(self._open_legacy_root)
        self._plot.pdf_requested.connect(self._export_pdf)

        self._explorer = SparkExplorer(self._controller.root)
        self._events_table = QTableWidget(0, len(_sg.CSV_COLUMNS))
        self._events_table.setHorizontalHeaderLabels(
            [_column_header(name) for name in _sg.CSV_COLUMNS]
        )
        self._events_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._events_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._events_table.verticalHeader().setVisible(False)
        self._events_table.setAlternatingRowColors(True)
        fields = getattr(_sg, "EVENT_FIELDS", {})
        for column, name in enumerate(_sg.CSV_COLUMNS):
            spec = fields.get(name, {})
            tip = spec.get("definition") or name
            unit = spec.get("unit")
            if unit:
                tip = f"{tip} [{unit}]"
            header = self._events_table.horizontalHeaderItem(column)
            if header is not None:
                header.setToolTip(tip)

        self._summary = QPlainTextEdit()
        self._summary.setReadOnly(True)
        self._summary.setPlaceholderText(
            "Run full analysis to write CSV, JSON, METRICS.md, PNGs, and analysis.pdf."
        )

        self._tabs = QTabWidget()
        self._tabs.addTab(self._plot, "Overview")
        self._tabs.addTab(self._explorer, "Figures")
        self._tabs.addTab(self._events_table, "Events")
        self._tabs.addTab(self._summary, "Summary")

        self._update_actions()
        return [panel, self._tabs]

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
        self._out_dir = None
        self._pdf_path = None
        assert self._plot is not None
        self._plot.clear_waveform()
        self._fill_preview_table([])
        self._fill_events_table([])
        if self._explorer is not None:
            self._explorer.clear()
        self._summary.clear()
        self._count_label.setText("No detection yet.")
        self._capture_label.setText(f"Loading {record.stem}…")
        self._loaded_meta = dict(record.metadata)
        self._loading_path = record.path
        self._update_actions()
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
        self._load_existing_analysis(self._chosen)
        self._update_actions()
        self._on_plot_status("")

    def _receive_load_failed(self, message: str) -> None:
        self.statusBar().showMessage("Load failed")
        QMessageBox.warning(self, "Could not load waveform", message)

    def _load_existing_analysis(self, record: CaptureRecord) -> None:
        out_dir = analysis_output_dir(record.path)
        summary_path = out_dir / f"{record.stem}_summary.json"
        if not summary_path.is_file():
            return
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        payload.setdefault("events", [])
        payload.setdefault("snippets", [])
        payload.setdefault("detection", {})
        payload.setdefault("summary", {})
        self._apply_full_result(payload, out_dir, switch_tab=False)

    def _read_params(self) -> dict[str, Any] | None:
        if self._form is None:
            return None
        try:
            return self._form.values()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid parameters", str(exc))
            return None

    def _detect(self) -> None:
        if self._time_s is None or self._voltage_v is None:
            QMessageBox.information(self, self.windowTitle(), "Open a waveform first.")
            return
        params = self._read_params()
        if params is None:
            return
        self._busy_detect = True
        self._update_actions()
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
        self._busy_detect = False
        self._update_actions()
        if self._plot is None or not isinstance(result, dict):
            return
        self._apply_live_result(result)
        self._on_plot_status("")

    def _receive_detect_failed(self, message: str) -> None:
        self._busy_detect = False
        self._update_actions()
        self._count_label.setText("Detection failed.")
        self.statusBar().showMessage("Detection failed")
        QMessageBox.warning(self, "Detection failed", message)

    def _run_analysis(self) -> None:
        if self._time_s is None or self._voltage_v is None or self._chosen is None:
            QMessageBox.information(self, self.windowTitle(), "Open a waveform first.")
            return
        params = self._read_params()
        if params is None:
            return
        out_dir = analysis_output_dir(self._chosen.path)
        run = {
            "mode": "gui",
            "run_name": self._chosen.run_name or self._loaded_meta.get("run_name"),
            "scope_channel": self._chosen.channel or self._loaded_meta.get("channel"),
            "scope_bw_hz": params["scope_bw_hz"],
            "drop_threshold_v": params["drop_threshold_v"],
            "drop_window_s": params["drop_window_s"],
            "merge_gap_s": params["merge_gap_s"],
            "coarse_step_s": params["coarse_step_s"],
            "include_first": params["include_first"],
            "capacitance_f": params.get("capacitance_f"),
        }
        self._busy_analysis = True
        self._update_actions()
        self._count_label.setText("Running full analysis…")
        self.statusBar().showMessage(f"Writing {out_dir}…")
        self._analysis_worker.start(
            _run_full_analysis,
            self._chosen.path,
            out_dir,
            params,
            self._time_s,
            self._voltage_v,
            self._loaded_meta,
            run,
            on_finished=self._receive_analysis,
            on_failed=self._receive_analysis_failed,
        )

    def _receive_analysis(self, result: object) -> None:
        self._busy_analysis = False
        if not isinstance(result, dict):
            self._update_actions()
            return
        out_dir = Path(str(result["out_dir"]))
        self._apply_full_result(result, out_dir, switch_tab=True)
        self._update_actions()
        self.statusBar().showMessage(f"Wrote {out_dir}")

    def _receive_analysis_failed(self, message: str) -> None:
        self._busy_analysis = False
        self._update_actions()
        self._count_label.setText("Analysis failed.")
        self.statusBar().showMessage("Analysis failed")
        QMessageBox.warning(self, "Analysis failed", message)

    def _apply_full_result(
        self, payload: dict[str, Any], out_dir: Path, *, switch_tab: bool
    ) -> None:
        self._out_dir = out_dir
        pdf = out_dir / "analysis.pdf"
        self._pdf_path = pdf if pdf.is_file() else None
        self._apply_live_result(payload)
        if self._explorer is not None:
            self._explorer.set_saved(_root_specs(out_dir), _gallery_items(out_dir))
            self._explorer.set_pdf_dir(out_dir)
        self._summary.setPlainText(_format_summary(payload, out_dir))
        if switch_tab and self._explorer is not None:
            self._tabs.setCurrentWidget(self._explorer)
        self._on_plot_status("")

    def _apply_live_result(self, payload: dict[str, Any]) -> None:
        events = list(payload.get("events") or [])
        detection = payload.get("detection") or {}
        n_events = int(payload.get("n_events", detection.get("n_events", len(events))))
        n_typical = int(payload.get("n_typical", detection.get("n_typical", 0)))
        self._preview = {
            "events": events,
            "snippets": list(payload.get("snippets") or []),
            "detection": detection,
            "n_events": n_events,
            "n_typical": n_typical,
        }
        marks = _marks_from_rows(events)
        if self._plot is not None:
            self._plot.set_events(marks)
        self._fill_preview_table(marks)
        self._fill_events_table(events)
        if self._explorer is not None:
            self._explorer.set_payload(
                events,
                list(payload.get("snippets") or []),
                detection,
            )
        self._count_label.setText(f"{n_events} breakdowns ({n_typical} typical)")

    def _fill_preview_table(self, events: list[EventMark]) -> None:
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

    def _fill_events_table(self, events: list[dict[str, Any]]) -> None:
        columns = list(_sg.CSV_COLUMNS)
        self._events_table.setRowCount(len(events))
        for row, record in enumerate(events):
            for column, name in enumerate(columns):
                item = QTableWidgetItem(_fmt_cell(name, record.get(name)))
                item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                self._events_table.setItem(row, column, item)
        self._events_table.resizeColumnsToContents()

    def _update_actions(self) -> None:
        ready = (
            self._time_s is not None
            and not self._busy_detect
            and not self._busy_analysis
        )
        self._detect_btn.setEnabled(ready)
        self._run_btn.setEnabled(ready)
        has_dir = self._out_dir is not None and self._out_dir.is_dir()
        has_pdf = self._pdf_path is not None and self._pdf_path.is_file()
        self._reveal_btn.setEnabled(bool(has_dir))
        self._pdf_btn.setEnabled(bool(has_pdf))
        if hasattr(self, "_reveal_act"):
            self._reveal_act.setEnabled(bool(has_dir))
            self._pdf_act.setEnabled(bool(has_pdf))

    def _reveal_output(self) -> None:
        if self._out_dir is None:
            return
        target = self._pdf_path if self._pdf_path and self._pdf_path.is_file() else self._out_dir
        reveal_in_folder(target)

    def _open_pdf(self) -> None:
        if self._pdf_path is None or not self._pdf_path.is_file():
            return
        open_local_path(self._pdf_path)

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

    def _overview_spec(self):
        if self._plot is None:
            return None
        name = "01_overview"
        if self._chosen is not None:
            name = f"01_overview_{self._chosen.stem}"
        return self._plot.publication_spec(name)

    def _open_legacy_root(self) -> None:
        spec = self._overview_spec()
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
        spec = self._overview_spec()
        if spec is None:
            QMessageBox.information(self, self.windowTitle(), "Open a waveform first.")
            return
        if self._out_dir is not None:
            default = self._out_dir / "01_overview.pdf"
        elif self._chosen is not None:
            default = self._chosen.path.parent / "plots" / f"{self._chosen.stem}.pdf"
        else:
            default = Path("01_overview.pdf")
        save_pdf(
            self,
            self._controller.root,
            self._export_worker,
            spec,
            default,
            on_status=self.statusBar().showMessage,
        )

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
        self._load_worker.cancel()
        self._detect_worker.cancel()
        self._analysis_worker.cancel()
        self._export_worker.cancel()
        if self._explorer is not None:
            self._explorer.shutdown()
        super().closeEvent(event)


def _create_window(controller: Any) -> SparkGapWindow:
    return SparkGapWindow(get(SPARK_GAP_ID), controller)


register(
    AnalysisSpec(
        id=SPARK_GAP_ID,
        title="Spark Gap Analysis",
        description=(
            "Detect breakdown events on spark-gap oscilloscope traces, plot "
            "chosen metrics and overlays, then write the diagnostic figure pack."
        ),
        family=FAMILY_ANALYSIS,
        accepted_kinds=(KIND_WAVEFORM,),
        options=OPTIONS,
        window_factory=_create_window,
    )
)
