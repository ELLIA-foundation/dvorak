"""Interactive overlay of X-123 energy spectra."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dvorak_root.text import root_text

from ..campaign_import import load_spectrum_module

_COLORS = (
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
)
_CURSOR_A = "#d62728"
_CURSOR_B = "#2ca02c"
_ROI = (31, 119, 180)
_ROI_COLOR = "#1f77b4"
_LINE_COLOR = "#555555"
_Y_MODES = (
    ("counts", "Counts"),
    ("cps", "Counts / s"),
    ("max", "Normalize max"),
    ("integral", "Normalize integral"),
)
_PEN = {
    "solid": Qt.PenStyle.SolidLine,
    "dashed": Qt.PenStyle.DashLine,
    "dotted": Qt.PenStyle.DotLine,
}
_EMPTY = (
    "Select a spectrum. Shift-click or Command-click overlays several. "
    "Pin the selection as a sum or a mean ± σ to compare groups."
)


@dataclass
class SpectrumTrace:
    path: Path
    campaign: str
    label: str
    energy_kev: np.ndarray
    counts: np.ndarray
    live_time_s: float | None
    phase: str | None
    offset_kev: float
    slope_kev: float


@dataclass
class SpectrumGroup:
    group_id: int
    name: str
    kind: str
    traces: list[SpectrumTrace]
    show_members: bool
    color: str


@dataclass
class _PlotCurve:
    key: str
    label: str
    energy: np.ndarray
    y: np.ndarray
    color: str
    width: float = 1.5
    line: str = "solid"
    spread: np.ndarray | None = None
    spread_label: str = ""
    primary: bool = True


def _copy_trace(trace: SpectrumTrace) -> SpectrumTrace:
    return SpectrumTrace(
        path=trace.path,
        campaign=trace.campaign,
        label=trace.label,
        energy_kev=np.array(trace.energy_kev, copy=True),
        counts=np.array(trace.counts, copy=True),
        live_time_s=trace.live_time_s,
        phase=trace.phase,
        offset_kev=trace.offset_kev,
        slope_kev=trace.slope_kev,
    )


def _shared_stem(traces: list[SpectrumTrace]) -> str:
    names = [
        trace.path.stem if trace.path is not None else trace.label for trace in traces
    ]
    if not names:
        return ""
    prefix = names[0]
    for name in names[1:]:
        while prefix and not name.startswith(prefix):
            prefix = prefix[:-1]
    prefix = prefix.strip(" -_\t")
    if len(prefix) < 3:
        return ""
    return prefix


def _phase_label(phase: str | None) -> str:
    if phase in ("pre", "post"):
        return str(phase)
    return "unlabeled"


def _default_group_name(
    kind: str,
    traces: list[SpectrumTrace],
    existing: list[SpectrumGroup],
    fallback: str = "",
) -> str:
    stem = _shared_stem(traces)
    if stem:
        return stem
    if fallback:
        return fallback
    count = 1 + sum(1 for group in existing if group.kind == kind)
    title = "Mean" if kind == "mean" else "Sum"
    return f"{title} {count}"


def _is_reference(curve: _PlotCurve, ref_key: str | None) -> bool:
    if not ref_key:
        return False
    if curve.key == ref_key:
        return True
    if ref_key.startswith("group:"):
        group_id = ref_key.split(":", 1)[1]
        return curve.key.startswith(f"member:{group_id}:")
    return False


def _tex_label(text: str) -> str:
    """Keep filenames and line names readable in TLatex.

    A bare underscore already prints as itself. A backslash would switch ROOT
    to TMathText, which sets the whole label in math italics.
    """
    return root_text(text)


class SpectrumPlot(QWidget):
    status_changed = Signal(str)
    legacy_root_requested = Signal()
    pdf_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._traces: list[SpectrumTrace] = []
        self._groups: list[SpectrumGroup] = []
        self._next_group_id = 0
        self._groups_view = False
        self._empty_message = _EMPTY
        self._syncing = False
        self._math = load_spectrum_module("spectrum")
        self._lines = load_spectrum_module("lines")
        self._build()

    def set_traces(self, traces: list[SpectrumTrace]) -> None:
        self._traces = list(traces)
        self._sync_group_buttons()
        self._refresh_reference_combo()
        self._sync_cps()
        self._redraw()

    def show_message(self, text: str) -> None:
        self._traces = []
        self._empty_message = text
        self._message.setText(text)
        self._sync_group_buttons()
        self._refresh_reference_combo()
        self._sync_cps()
        self._redraw()

    def reset_view(self) -> None:
        if self._stack.currentWidget() is not self._plot:
            return
        self._plot.enableAutoRange()

    def export_png(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        import pyqtgraph.exporters

        exporter = pyqtgraph.exporters.ImageExporter(self._plot.plotItem)
        exporter.parameters()["width"] = 1600
        exporter.export(str(path))
        return path

    def publication_spec(self, name: str = "spectrum") -> dict | None:
        """Current viewport, in the same units as the on-screen axes."""
        curves = self._display_curves()
        if not curves:
            return None
        xmin, xmax, ymin, ymax, log = self._export_limits()
        series: list[dict] = []
        for curve in curves:
            if curve.spread is not None:
                series.extend(self._spread_export(curve, xmin, xmax, log=log))
            clipped = self._clip_series(curve.energy, curve.y, xmin, xmax, log=log)
            if clipped is None:
                continue
            xs, ys = clipped
            label = curve.label
            if label.startswith("Δ "):
                label = "#Delta " + label[2:]
            series.append(
                {
                    "x": xs,
                    "y": ys,
                    "label": _tex_label(label),
                    "color": curve.color,
                    "line": curve.line,
                    "marker": "none",
                    "width": curve.width,
                }
            )
        if not series:
            return None

        panel: dict = {
            "x_title": "Energy (keV)",
            "y_title": self._y_label(),
            "logy": log,
            "xmin": xmin,
            "xmax": xmax,
            "ymin": ymin,
            "ymax": ymax,
            "series": series,
            "vlines": self._marker_lines(),
            "vspans": [],
            "notes": [],
            "legend": len(series) > 1,
        }
        if self._roi.isChecked():
            lo, hi = self._region.getRegion()
            panel["vspans"].append(
                {
                    "x0": float(min(lo, hi)),
                    "x1": float(max(lo, hi)),
                    "color": _ROI_COLOR,
                    "label": "ROI",
                }
            )
            panel["notes"].append({"text": self._roi_note(), "align": "left"})
        if self._cursors.isChecked():
            panel["notes"].append({"text": self._cursor_note(), "align": "right"})
        # Legacy ROOT and the PDF take the on-screen plot's shape.
        return {
            "name": name,
            "width": max(480, self._plot.width()),
            "height": max(320, self._plot.height()),
            "cols": 1,
            "panels": [panel],
        }

    def _set_export_enabled(self, enabled: bool) -> None:
        self._legacy_btn.setEnabled(enabled)
        self._pdf_btn.setEnabled(enabled)

    def _build(self) -> None:
        pg.setConfigOptions(antialias=True, foreground="d")
        self._y_mode = QComboBox()
        for key, label in _Y_MODES:
            self._y_mode.addItem(label, key)
        self._y_mode.currentIndexChanged.connect(self._redraw)

        self._log = QCheckBox("Log Y")
        self._log.toggled.connect(self._redraw)

        self._smooth = QCheckBox("Moving average")
        self._smooth.toggled.connect(self._redraw)

        self._window = QSpinBox()
        self._window.setRange(1, 101)
        self._window.setSingleStep(2)
        self._window.setValue(5)
        self._window.setToolTip("Odd window in MCA channels")
        self._window.valueChanged.connect(self._on_window_channels)

        self._window_kev = QDoubleSpinBox()
        self._window_kev.setRange(0.0, 2.0)
        self._window_kev.setDecimals(3)
        self._window_kev.setSingleStep(0.015)
        self._window_kev.setSuffix(" keV")
        self._window_kev.setToolTip("Window in energy; updates the channel width")
        self._window_kev.valueChanged.connect(self._on_window_kev)

        self._roi = QCheckBox("ROI")
        self._roi.toggled.connect(self._on_roi_toggled)

        self._cursors = QCheckBox("Cursors")
        self._cursors.toggled.connect(self._on_cursors_toggled)

        self._u_lines = QCheckBox("U L lines")
        self._u_lines.toggled.connect(self._redraw)

        self._material_lines = QCheckBox("Th Bi Ra")
        self._material_lines.setToolTip(
            "Bi, Th, and Ra L lines from the U-containing glass spectrum. "
            "Th Lα and Bi Lβ are the unresolved pair near 13 keV."
        )
        self._material_lines.toggled.connect(self._redraw)

        self._k_lines = QCheckBox("Common lines")
        self._k_lines.toggled.connect(self._redraw)

        self._diff = QComboBox()
        self._diff.addItem("No difference", None)
        self._diff.currentIndexChanged.connect(self._redraw)

        reset_btn = QPushButton("Reset view")
        reset_btn.clicked.connect(self.reset_view)
        self._legacy_btn = QPushButton("Legacy ROOT")
        self._legacy_btn.setToolTip(
            "Open the current view in the interactive ROOT GUI (root -l)"
        )
        self._legacy_btn.clicked.connect(self.legacy_root_requested.emit)
        self._pdf_btn = QPushButton("Save PDF…")
        self._pdf_btn.setToolTip("Write the current view as a ROOT PDF")
        self._pdf_btn.clicked.connect(self.pdf_requested.emit)
        self._set_export_enabled(False)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Y"))
        row1.addWidget(self._y_mode)
        row1.addWidget(self._log)
        row1.addWidget(self._smooth)
        row1.addWidget(self._window)
        row1.addWidget(self._window_kev)
        row1.addStretch(1)
        row1.addWidget(self._legacy_btn)
        row1.addWidget(self._pdf_btn)
        row1.addWidget(reset_btn)

        row2 = QHBoxLayout()
        row2.addWidget(self._roi)
        row2.addWidget(self._cursors)
        row2.addWidget(self._u_lines)
        row2.addWidget(self._material_lines)
        row2.addWidget(self._k_lines)
        row2.addWidget(QLabel("Δ vs"))
        row2.addWidget(self._diff, stretch=1)

        self._group_kind = QComboBox()
        self._group_kind.addItem("Sum", "sum")
        self._group_kind.addItem("Mean ± σ", "mean")
        self._group_kind.currentIndexChanged.connect(self._sync_group_buttons)

        self._add_btn = QPushButton("Add")
        self._add_btn.clicked.connect(self._add_group)
        self._split_btn = QPushButton("Split by phase")
        self._split_btn.clicked.connect(self._split_by_phase)
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._clear_groups)

        self._view_selection = QRadioButton("Selection")
        self._view_groups = QRadioButton("Groups")
        self._view_selection.setToolTip("Overlay the spectra selected in the list")
        self._view_groups.setToolTip("Overlay pinned sums and means")
        self._view_selection.setChecked(True)
        self._view_toggle = QButtonGroup(self)
        self._view_toggle.addButton(self._view_selection)
        self._view_toggle.addButton(self._view_groups)
        self._view_selection.toggled.connect(self._on_view_toggled)
        self._view_groups.toggled.connect(self._on_view_toggled)

        self._band_metric = QComboBox()
        self._band_metric.addItem("σ", "std")
        self._band_metric.addItem("SEM", "sem")
        self._band_metric.setToolTip(
            "Spread around a mean: sample standard deviation, or the standard error of the mean"
        )
        self._band_metric.currentIndexChanged.connect(self._redraw)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Group as"))
        row3.addWidget(self._group_kind)
        row3.addWidget(self._add_btn)
        row3.addWidget(self._split_btn)
        row3.addWidget(self._clear_btn)
        row3.addSpacing(12)
        row3.addWidget(QLabel("View"))
        row3.addWidget(self._view_selection)
        row3.addWidget(self._view_groups)
        row3.addSpacing(12)
        row3.addWidget(QLabel("Band"))
        row3.addWidget(self._band_metric)
        row3.addStretch(1)

        self._group_rows = QWidget()
        self._group_rows_layout = QVBoxLayout(self._group_rows)
        self._group_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._group_rows_layout.setSpacing(2)
        self._group_scroll = QScrollArea()
        self._group_scroll.setWidgetResizable(True)
        self._group_scroll.setWidget(self._group_rows)
        self._group_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._group_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._group_scroll.hide()

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: palette(mid);")

        self._message = QLabel(_EMPTY)
        self._message.setWordWrap(True)
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setMargin(16)

        self._plot = pg.PlotWidget()
        self._plot.setBackground("w")
        self._plot.showGrid(x=True, y=True, alpha=0.25)
        self._plot.setLabel("bottom", "Energy (keV)")
        self._plot.setLabel("left", "Counts")
        self._plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._vb = self._plot.plotItem.vb
        self._proxy = pg.SignalProxy(
            self._plot.scene().sigMouseMoved,
            rateLimit=40,
            slot=self._on_mouse_moved,
        )

        self._region = pg.LinearRegionItem(
            values=(12.0, 20.0),
            orientation="vertical",
            brush=pg.mkBrush(*_ROI, 40),
            pen=pg.mkPen(_ROI, width=1),
        )
        self._region.setZValue(-20)
        self._region.sigRegionChanged.connect(self._update_readout)
        self._plot.addItem(self._region)
        self._region.setVisible(False)

        self._cursor_a = pg.InfiniteLine(
            angle=90,
            movable=True,
            pen=pg.mkPen(_CURSOR_A, width=1),
            label="A",
            labelOpts={"position": 0.95, "color": _CURSOR_A},
        )
        self._cursor_b = pg.InfiniteLine(
            angle=90,
            movable=True,
            pen=pg.mkPen(_CURSOR_B, width=1),
            label="B",
            labelOpts={"position": 0.90, "color": _CURSOR_B},
        )
        self._cursor_a.sigPositionChanged.connect(self._update_readout)
        self._cursor_b.sigPositionChanged.connect(self._update_readout)
        self._plot.addItem(self._cursor_a)
        self._plot.addItem(self._cursor_b)
        self._cursor_a.setVisible(False)
        self._cursor_b.setVisible(False)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._message)
        self._stack.addWidget(self._plot)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row1)
        layout.addLayout(row2)
        layout.addLayout(row3)
        layout.addWidget(self._group_scroll)
        layout.addWidget(self._hint)
        layout.addWidget(self._stack, stretch=1)
        self._sync_group_buttons()

    def _on_window_channels(self, value: int) -> None:
        if self._syncing:
            return
        odd = self._math.odd_window(value)
        self._syncing = True
        if odd != value:
            self._window.setValue(odd)
        slope = self._slope()
        self._window_kev.setValue(odd * slope)
        self._syncing = False
        self._redraw()

    def _on_window_kev(self, value: float) -> None:
        if self._syncing:
            return
        slope = self._slope()
        channels = 1 if value <= 0 else self._math.window_kev_to_channels(value, slope)
        self._syncing = True
        self._window.setValue(channels)
        self._syncing = False
        self._redraw()

    def _anchor_trace(self) -> SpectrumTrace | None:
        if self._groups_view:
            source = [trace for group in self._groups for trace in group.traces]
        else:
            source = list(self._traces)
            if not source:
                source = [trace for group in self._groups for trace in group.traces]
        return source[0] if source else None

    def _slope(self) -> float:
        trace = self._anchor_trace()
        if trace is not None:
            return float(trace.slope_kev) or 0.01466
        return 0.01466

    def _on_roi_toggled(self, on: bool) -> None:
        self._region.setVisible(on)
        self._update_readout()

    def _on_cursors_toggled(self, on: bool) -> None:
        anchor = self._anchor_trace()
        if on and anchor is not None:
            energy = anchor.energy_kev
            if energy.size:
                span = float(energy[-1] - energy[0])
                self._cursor_a.setValue(float(energy[0] + 0.25 * span))
                self._cursor_b.setValue(float(energy[0] + 0.75 * span))
        self._cursor_a.setVisible(on)
        self._cursor_b.setVisible(on)
        self._update_readout()

    def _refresh_reference_combo(self) -> None:
        current = self._diff.currentData()
        self._diff.blockSignals(True)
        self._diff.clear()
        self._diff.addItem("No difference", None)
        if self._groups_view:
            for group in self._groups:
                self._diff.addItem(group.name, f"group:{group.group_id}")
        else:
            for trace in self._traces:
                self._diff.addItem(trace.label, str(trace.path))
        index = 0
        if current is not None:
            found = self._diff.findData(current)
            if found >= 0:
                index = found
        self._diff.setCurrentIndex(index)
        self._diff.blockSignals(False)

    def _sync_cps(self) -> None:
        if self._groups_view:
            traces = [trace for group in self._groups for trace in group.traces]
        else:
            traces = list(self._traces)
        ok = bool(traces) and all(
            trace.live_time_s is not None and trace.live_time_s > 0 for trace in traces
        )
        cps_index = next(i for i, (key, _label) in enumerate(_Y_MODES) if key == "cps")
        model = self._y_mode.model()
        item = model.item(cps_index)
        if item is not None:
            item.setEnabled(ok)
        if not ok and self._y_mode.currentData() == "cps":
            self._y_mode.setCurrentIndex(0)

    def _smooth_window(self) -> int:
        if not self._smooth.isChecked():
            return 1
        return self._math.odd_window(self._window.value())

    def _y_values(self, trace: SpectrumTrace) -> np.ndarray:
        y = self._math.moving_average(trace.counts, self._smooth_window())
        mode = self._y_mode.currentData()
        if mode == "cps":
            return self._math.to_cps(y, trace.live_time_s)
        if mode == "max":
            return self._math.normalize_max(y)
        if mode == "integral":
            return self._math.normalize_integral(y)
        return y

    def _y_label(self) -> str:
        mode = self._y_mode.currentData()
        labels = {
            "counts": "Counts",
            "cps": "Counts / s",
            "max": "Counts / max",
            "integral": "Counts / integral",
        }
        return labels.get(mode, "Counts")

    def _trace_curve(
        self,
        trace: SpectrumTrace,
        *,
        key: str,
        label: str,
        color: str,
        width: float = 1.5,
        line: str = "solid",
        primary: bool = True,
    ) -> _PlotCurve | None:
        try:
            y = self._y_values(trace)
        except ValueError:
            return None
        return _PlotCurve(
            key=key,
            label=label,
            energy=np.asarray(trace.energy_kev, dtype=np.float64),
            y=np.asarray(y, dtype=np.float64),
            color=color,
            width=width,
            line=line,
            primary=primary,
        )

    def _selection_curves(self) -> list[_PlotCurve]:
        curves: list[_PlotCurve] = []
        for index, trace in enumerate(self._traces):
            curve = self._trace_curve(
                trace,
                key=str(trace.path),
                label=trace.label,
                color=_COLORS[index % len(_COLORS)],
            )
            if curve is not None:
                curves.append(curve)
        return curves

    def _sum_live_time(self, traces: list[SpectrumTrace]) -> float | None:
        total = 0.0
        for trace in traces:
            if trace.live_time_s is None or trace.live_time_s <= 0:
                return None
            total += float(trace.live_time_s)
        return total

    def _sum_curve(self, group: SpectrumGroup) -> _PlotCurve | None:
        if not group.traces:
            return None
        energy, counts = self._math.sum_counts(
            [trace.energy_kev for trace in group.traces],
            [trace.counts for trace in group.traces],
        )
        first = group.traces[0]
        synthetic = SpectrumTrace(
            path=first.path,
            campaign=first.campaign,
            label=group.name,
            energy_kev=energy,
            counts=counts,
            live_time_s=self._sum_live_time(group.traces),
            phase=first.phase,
            offset_kev=first.offset_kev,
            slope_kev=first.slope_kev,
        )
        curve = self._trace_curve(
            synthetic,
            key=f"group:{group.group_id}",
            label=f"{group.name} (n={len(group.traces)})",
            color=group.color,
            width=2.0,
        )
        return curve

    def _mean_curve(self, group: SpectrumGroup) -> _PlotCurve | None:
        energies: list[np.ndarray] = []
        values: list[np.ndarray] = []
        for trace in group.traces:
            curve = self._trace_curve(
                trace,
                key=str(trace.path),
                label=trace.label,
                color=group.color,
            )
            if curve is None:
                continue
            energies.append(curve.energy)
            values.append(curve.y)
        if len(values) < 2:
            return None
        energy, mean, sample_std, sem = self._math.mean_band(energies, values)
        use_sem = self._band_metric.currentData() == "sem"
        metric = "SEM" if use_sem else "σ"
        return _PlotCurve(
            key=f"group:{group.group_id}",
            label=f"{group.name}  n={len(values)}  ±{metric}",
            energy=energy,
            y=mean,
            color=group.color,
            width=2.0,
            line="dashed",
            spread=sem if use_sem else sample_std,
            spread_label=metric,
        )

    def _member_curves(self, group: SpectrumGroup) -> list[_PlotCurve]:
        curves: list[_PlotCurve] = []
        for trace in group.traces:
            curve = self._trace_curve(
                trace,
                key=f"member:{group.group_id}:{trace.path}",
                label=trace.label,
                color=group.color,
                width=1.0,
                primary=False,
            )
            if curve is not None:
                curves.append(curve)
        return curves

    def _group_curves(self) -> list[_PlotCurve]:
        curves: list[_PlotCurve] = []
        for group in self._groups:
            if group.show_members:
                curves.extend(self._member_curves(group))
            primary = (
                self._sum_curve(group) if group.kind == "sum" else self._mean_curve(group)
            )
            if primary is not None:
                curves.append(primary)
        return curves

    def _apply_reference(self, curves: list[_PlotCurve]) -> list[_PlotCurve]:
        ref_key = self._diff.currentData()
        if not ref_key:
            return curves
        ref = next((curve for curve in curves if curve.key == ref_key), None)
        if ref is None:
            return curves
        adjusted: list[_PlotCurve] = []
        for curve in curves:
            if _is_reference(curve, ref_key):
                continue
            _energy, y = self._math.difference(curve.energy, curve.y, ref.energy, ref.y)
            adjusted.append(
                _PlotCurve(
                    key=curve.key,
                    label=f"Δ {curve.label}",
                    energy=curve.energy,
                    y=y,
                    color=curve.color,
                    width=curve.width,
                    line=curve.line,
                    spread=curve.spread,
                    spread_label=curve.spread_label,
                    primary=curve.primary,
                )
            )
        return adjusted

    def _display_curves(self) -> list[_PlotCurve]:
        source = self._group_curves() if self._groups_view else self._selection_curves()
        return self._apply_reference(source)

    def _first_primary(self) -> _PlotCurve | None:
        for curve in self._display_curves():
            if curve.primary:
                return curve
        return None

    def _export_limits(self) -> tuple[float, float, float, float, bool]:
        (x0, x1), (y0, y1) = self._vb.viewRange()
        xmin, xmax = float(min(x0, x1)), float(max(x0, x1))
        ymin, ymax = float(min(y0, y1)), float(max(y0, y1))
        log = self._log.isChecked()
        if log:
            # pyqtgraph stores the log-Y view range in log10 units.
            ymin, ymax = float(10.0 ** ymin), float(10.0 ** ymax)
            ymin = max(ymin, 1e-6)
        if not np.isfinite(xmin) or not np.isfinite(xmax) or xmin >= xmax:
            xmin, xmax = 0.0, 1.0
        if not np.isfinite(ymin) or not np.isfinite(ymax) or ymin >= ymax:
            ymin, ymax = (1e-6, 1.0) if log else (0.0, 1.0)
        return xmin, xmax, ymin, ymax, log

    def _clip_series(
        self,
        energy: np.ndarray,
        y: np.ndarray,
        xmin: float,
        xmax: float,
        *,
        log: bool,
    ) -> tuple[list[float], list[float]] | None:
        energy = np.asarray(energy, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        if energy.size == 0 or y.size == 0:
            return None
        i0 = int(np.searchsorted(energy, xmin, side="left"))
        i1 = int(np.searchsorted(energy, xmax, side="right"))
        i0 = max(0, i0 - 1)
        i1 = min(energy.size, max(i0 + 1, i1 + 1))
        xs = energy[i0:i1]
        ys = y[i0:i1]
        if log:
            ys = np.maximum(ys, 1e-6)
        mask = np.isfinite(xs) & np.isfinite(ys)
        xs = xs[mask]
        ys = ys[mask]
        if xs.size == 0:
            return None
        return xs.tolist(), ys.tolist()

    def _clip_band(
        self,
        energy: np.ndarray,
        low: np.ndarray,
        high: np.ndarray,
        xmin: float,
        xmax: float,
        *,
        log: bool,
    ) -> tuple[list[float], list[float], list[float]] | None:
        energy = np.asarray(energy, dtype=np.float64)
        low = np.asarray(low, dtype=np.float64)
        high = np.asarray(high, dtype=np.float64)
        if energy.size == 0 or low.size == 0 or high.size == 0:
            return None
        i0 = int(np.searchsorted(energy, xmin, side="left"))
        i1 = int(np.searchsorted(energy, xmax, side="right"))
        i0 = max(0, i0 - 1)
        i1 = min(energy.size, max(i0 + 1, i1 + 1))
        xs = energy[i0:i1]
        lo = low[i0:i1]
        hi = high[i0:i1]
        if log:
            lo = np.maximum(lo, 1e-6)
            hi = np.maximum(hi, 1e-6)
        mask = np.isfinite(xs) & np.isfinite(lo) & np.isfinite(hi)
        xs = xs[mask]
        lo = lo[mask]
        hi = hi[mask]
        if xs.size == 0:
            return None
        return xs.tolist(), lo.tolist(), hi.tolist()

    def _spread_export(
        self,
        curve: _PlotCurve,
        xmin: float,
        xmax: float,
        *,
        log: bool,
    ) -> list[dict]:
        if curve.spread is None:
            return []
        clipped = self._clip_band(
            curve.energy,
            curve.y - curve.spread,
            curve.y + curve.spread,
            xmin,
            xmax,
            log=log,
        )
        if clipped is None:
            return []
        xs, y_lo, y_hi = clipped
        return [
            {
                "x": xs,
                "y_low": y_lo,
                "y_high": y_hi,
                "label": "",
                "color": curve.color,
                "fill_alpha": 0.45,
            }
        ]

    def _ordered_phases(self) -> list[str | None]:
        present: list[str | None] = []
        for trace in self._traces:
            if trace.phase not in present:
                present.append(trace.phase)
        ordered: list[str | None] = []
        for phase in ("pre", "post", None):
            if phase in present:
                ordered.append(phase)
        for phase in present:
            if phase not in ordered:
                ordered.append(phase)
        return ordered

    def _phase_batches(self, kind: str) -> list[tuple[list[SpectrumTrace], str]]:
        minimum = 2 if kind == "mean" else 1
        buckets: dict[str | None, list[SpectrumTrace]] = {}
        for trace in self._traces:
            buckets.setdefault(trace.phase, []).append(trace)
        batches: list[tuple[list[SpectrumTrace], str]] = []
        for phase in self._ordered_phases():
            rows = buckets.get(phase) or []
            if len(rows) >= minimum:
                batches.append((rows, _phase_label(phase)))
        return batches

    def _sync_group_buttons(self) -> None:
        kind = self._group_kind.currentData()
        count = len(self._traces)
        if kind == "mean":
            self._add_btn.setEnabled(count >= 2)
            self._add_btn.setToolTip(
                "Pin the selection as a mean and a spread band"
                if count >= 2
                else "Select at least two spectra"
            )
        else:
            self._add_btn.setEnabled(count >= 1)
            self._add_btn.setToolTip(
                "Pin the selection as one co-added spectrum"
                if count >= 1
                else "Select a spectrum"
            )
        ready = bool(self._phase_batches(kind))
        self._split_btn.setEnabled(ready)
        self._split_btn.setToolTip(
            "Pin one group per pre, post, and unlabeled phase in the selection"
            if ready
            else "The selection has no phase with enough spectra"
        )
        self._clear_btn.setEnabled(bool(self._groups))
        self._band_metric.setEnabled(any(group.kind == "mean" for group in self._groups))

    def _find_group(self, group_id: int) -> SpectrumGroup | None:
        for group in self._groups:
            if group.group_id == group_id:
                return group
        return None

    def _rebuild_group_rows(self) -> None:
        while self._group_rows_layout.count():
            item = self._group_rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                for child in widget.findChildren(QWidget):
                    child.blockSignals(True)
                widget.deleteLater()
        for group in self._groups:
            self._group_rows_layout.addWidget(self._make_group_row(group))
        self._group_scroll.setVisible(bool(self._groups))
        if self._groups:
            self._group_scroll.setFixedHeight(min(140, 40 * len(self._groups) + 6))

    def _make_group_row(self, group: SpectrumGroup) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        swatch = QLabel()
        swatch.setFixedSize(12, 12)
        swatch.setStyleSheet(f"background: {group.color}; border-radius: 2px;")
        name = QLineEdit(group.name)
        name.setPlaceholderText("Group name")
        name.editingFinished.connect(
            lambda gid=group.group_id, box=name: self._rename_group(gid, box.text())
        )
        kind = "mean" if group.kind == "mean" else "sum"
        meta = QLabel(f"{kind} · n={len(group.traces)}")
        meta.setStyleSheet("color: palette(mid);")
        members = QCheckBox("Members")
        members.setToolTip("Draw each spectrum in this group")
        members.blockSignals(True)
        members.setChecked(group.show_members)
        members.blockSignals(False)
        members.toggled.connect(
            lambda on, gid=group.group_id: self._toggle_members(gid, on)
        )
        remove = QPushButton("Remove")
        remove.clicked.connect(
            lambda _checked=False, gid=group.group_id: self._remove_group(gid)
        )
        layout.addWidget(swatch)
        layout.addWidget(name, stretch=1)
        layout.addWidget(meta)
        layout.addWidget(members)
        layout.addWidget(remove)
        return row

    def _pin(
        self,
        batches: list[tuple[list[SpectrumTrace], str]],
    ) -> None:
        kind = self._group_kind.currentData()
        for traces, fallback in batches:
            copied = [_copy_trace(trace) for trace in traces]
            self._next_group_id += 1
            self._groups.append(
                SpectrumGroup(
                    group_id=self._next_group_id,
                    name=_default_group_name(kind, copied, self._groups, fallback),
                    kind=kind,
                    traces=copied,
                    show_members=False,
                    color=_COLORS[(self._next_group_id - 1) % len(_COLORS)],
                )
            )
        self._rebuild_group_rows()
        self._sync_group_buttons()
        if not self._view_groups.isChecked():
            self._view_groups.setChecked(True)
            return
        self._refresh_reference_combo()
        self._sync_cps()
        self._redraw()

    def _add_group(self) -> None:
        kind = self._group_kind.currentData()
        minimum = 2 if kind == "mean" else 1
        if len(self._traces) < minimum:
            return
        self._pin([(self._traces, "")])

    def _split_by_phase(self) -> None:
        batches = self._phase_batches(self._group_kind.currentData())
        if not batches:
            kind = self._group_kind.currentData()
            self.status_changed.emit(
                "Select at least two spectra in a phase"
                if kind == "mean"
                else "Select a spectrum to split by phase"
            )
            return
        self._pin(batches)

    def _clear_groups(self) -> None:
        self._groups.clear()
        self._rebuild_group_rows()
        self._sync_group_buttons()
        if self._view_groups.isChecked():
            self._view_selection.setChecked(True)
            return
        self._refresh_reference_combo()
        self._sync_cps()
        self._redraw()

    def _remove_group(self, group_id: int) -> None:
        self._groups = [group for group in self._groups if group.group_id != group_id]
        self._rebuild_group_rows()
        self._sync_group_buttons()
        self._refresh_reference_combo()
        self._sync_cps()
        self._redraw()

    def _rename_group(self, group_id: int, name: str) -> None:
        group = self._find_group(group_id)
        if group is None:
            return
        cleaned = name.strip()
        if not cleaned or cleaned == group.name:
            if not cleaned:
                self._rebuild_group_rows()
            return
        group.name = cleaned
        self._refresh_reference_combo()
        self._redraw()

    def _toggle_members(self, group_id: int, on: bool) -> None:
        group = self._find_group(group_id)
        if group is None or group.show_members == on:
            return
        group.show_members = on
        self._redraw()

    def _on_view_toggled(self, on: bool) -> None:
        if not on or self._syncing:
            return
        self._groups_view = self._view_groups.isChecked()
        self._refresh_reference_combo()
        self._sync_cps()
        self._redraw()

    def _marked(self) -> list[tuple[str, float, float]]:
        rows = self._lines.all_lines(
            uranium=self._u_lines.isChecked(),
            common=self._k_lines.isChecked(),
            material=self._material_lines.isChecked(),
        )
        return [
            (name, energy, position)
            for (name, energy), position in zip(rows, self._lines.label_positions(rows))
        ]

    def _marker_lines(self) -> list[dict]:
        lines: list[dict] = []
        for name, energy, position in self._marked():
            lines.append(
                {
                    "x": float(energy),
                    "color": _LINE_COLOR,
                    "label": _tex_label(name),
                    "style": "dashed",
                    "label_pos": position,
                }
            )
        if self._cursors.isChecked():
            lines.append(
                {
                    "x": float(self._cursor_a.value()),
                    "color": _CURSOR_A,
                    "label": "A",
                    "style": "solid",
                }
            )
            lines.append(
                {
                    "x": float(self._cursor_b.value()),
                    "color": _CURSOR_B,
                    "label": "B",
                    "style": "solid",
                }
            )
        return lines

    def _roi_note(self) -> str:
        curve = self._first_primary()
        if curve is None:
            return "ROI"
        lo, hi = self._region.getRegion()
        stats = self._math.roi_stats(curve.energy, curve.y, lo, hi)
        centroid = (
            f"{stats.centroid_kev:.3f} keV" if stats.centroid_kev is not None else "n/a"
        )
        fwhm = f"{stats.fwhm_kev:.3f} keV" if stats.fwhm_kev is not None else "n/a"
        return (
            f"ROI {stats.e_lo:.2f}-{stats.e_hi:.2f} keV\n"
            f"integral {stats.integral:.4g}\n"
            f"centroid {centroid}\n"
            f"FWHM {fwhm}"
        )

    def _cursor_note(self) -> str:
        delta = abs(float(self._cursor_b.value()) - float(self._cursor_a.value()))
        return f"#DeltaE = {delta:.3f} keV"

    def _empty_text(self) -> str:
        if self._groups_view:
            if not self._groups:
                return "Add the selection as a sum or a mean ± σ."
            return "None of the grouped spectra can use this Y mode."
        if not self._traces:
            return self._empty_message
        return "None of the selected spectra can use this Y mode."

    def _redraw(self, *_args: object) -> None:
        if self._syncing:
            return
        curves = self._display_curves()
        if not curves:
            self._message.setText(self._empty_text())
            self._hint.clear()
            self._stack.setCurrentWidget(self._message)
            self._set_export_enabled(False)
            self.status_changed.emit("")
            return

        self._plot.clear()
        self._plot.addItem(self._region)
        self._plot.addItem(self._cursor_a)
        self._plot.addItem(self._cursor_b)
        self._region.setVisible(self._roi.isChecked())
        self._cursor_a.setVisible(self._cursors.isChecked())
        self._cursor_b.setVisible(self._cursors.isChecked())
        show_legend = (
            self._groups_view
            or len(curves) > 1
            or any(curve.spread is not None for curve in curves)
            or any(curve.label.startswith("Δ ") for curve in curves)
        )
        legend = self._plot.plotItem.legend
        if show_legend:
            if legend is None:
                self._plot.addLegend()
            else:
                legend.clear()
        elif legend is not None:
            legend.clear()

        log = self._log.isChecked()
        self._plot.setLogMode(x=False, y=log)
        self._plot.setLabel("left", self._y_label())
        self._plot.setLabel("bottom", "Energy (keV)")

        for curve in curves:
            if curve.spread is not None:
                self._draw_spread(curve)
            y_plot = np.maximum(curve.y, 1e-6) if log else curve.y
            self._plot.plot(
                curve.energy,
                y_plot,
                pen=pg.mkPen(
                    curve.color,
                    width=curve.width,
                    style=_PEN.get(curve.line, Qt.PenStyle.SolidLine),
                ),
                name=curve.label,
            )

        self._draw_lines()
        self._stack.setCurrentWidget(self._plot)
        self._set_export_enabled(True)
        self._update_readout()

    def _draw_spread(self, curve: _PlotCurve) -> None:
        if curve.spread is None:
            return
        upper = curve.y + curve.spread
        lower = curve.y - curve.spread
        if self._log.isChecked():
            lower = np.maximum(lower, 1e-6)
            upper = np.maximum(upper, 1e-6)
        # A pen is required. FillBetweenItem copies each curve's path, and
        # pyqtgraph leaves that path empty when pen is None.
        edge = pg.mkPen(curve.color, width=1, style=Qt.PenStyle.DotLine)
        upper_curve = self._plot.plot(curve.energy, upper, pen=edge)
        lower_curve = self._plot.plot(curve.energy, lower, pen=edge)
        color = pg.mkColor(curve.color)
        fill = pg.FillBetweenItem(
            upper_curve,
            lower_curve,
            brush=pg.mkBrush(color.red(), color.green(), color.blue(), 70),
        )
        fill.setZValue(-10)
        self._plot.addItem(fill)

    def _draw_lines(self) -> None:
        for name, energy, position in self._marked():
            line = pg.InfiniteLine(
                pos=energy,
                angle=90,
                movable=False,
                pen=pg.mkPen("#555555", width=1, style=Qt.PenStyle.DashLine),
                label=name,
                labelOpts={"position": position, "color": "#555555"},
            )
            self._plot.addItem(line)

    def _on_mouse_moved(self, event: Any) -> None:
        pos = event[0]
        if not self._plot.sceneBoundingRect().contains(pos):
            return
        point = self._vb.mapSceneToView(pos)
        self._update_readout(hover_e=float(point.x()), hover_y=float(point.y()))

    def _update_readout(
        self,
        *args: Any,
        hover_e: float | None = None,
        hover_y: float | None = None,
    ) -> None:
        parts: list[str] = []
        slope = self._slope()
        anchor = self._anchor_trace()
        offset = float(anchor.offset_kev) if anchor is not None else 0.0007
        if hover_e is not None:
            channel = int(round((hover_e - offset) / slope)) if slope else 0
            parts.append(f"E={hover_e:.3f} keV")
            parts.append(f"ch={channel}")
            if hover_y is not None:
                parts.append(f"y={hover_y:.4g}")
        if self._cursors.isChecked():
            e_a = float(self._cursor_a.value())
            e_b = float(self._cursor_b.value())
            parts.append(f"ΔE={abs(e_b - e_a):.3f} keV")
        primary = self._first_primary() if self._roi.isChecked() else None
        if self._roi.isChecked() and primary is not None:
            lo, hi = self._region.getRegion()
            stats = self._math.roi_stats(primary.energy, primary.y, lo, hi)
            centroid = (
                f"{stats.centroid_kev:.3f} keV" if stats.centroid_kev is not None else "—"
            )
            fwhm = f"{stats.fwhm_kev:.3f} keV" if stats.fwhm_kev is not None else "—"
            parts.append(
                f"ROI {stats.e_lo:.2f}–{stats.e_hi:.2f}  "
                f"∫={stats.integral:.4g}  centroid={centroid}  FWHM={fwhm}"
            )
        window = self._smooth_window()
        if window > 1:
            parts.append(f"MA {window} ch")
        self._hint.setText("   ".join(parts))
        if self._stack.currentWidget() is not self._plot:
            return
        if self._groups_view:
            n = len(self._groups)
            self.status_changed.emit(f"{n} group" if n == 1 else f"{n} groups")
            return
        n = len(self._traces)
        self.status_changed.emit(f"{n} spectrum" if n == 1 else f"{n} spectra")
