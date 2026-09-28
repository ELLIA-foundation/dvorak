"""Interactive overlay of X-123 energy spectra."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

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
_Y_MODES = (
    ("counts", "Counts"),
    ("cps", "Counts / s"),
    ("max", "Normalize max"),
    ("integral", "Normalize integral"),
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


class SpectrumPlot(QWidget):
    status_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._traces: list[SpectrumTrace] = []
        self._syncing = False
        self._math = load_spectrum_module("spectrum")
        self._lines = load_spectrum_module("lines")
        self._build()

    def set_traces(self, traces: list[SpectrumTrace]) -> None:
        self._traces = list(traces)
        self._refresh_reference_combo()
        self._sync_cps()
        self._redraw()

    def show_message(self, text: str) -> None:
        self._traces = []
        self._message.setText(text)
        self._hint.clear()
        self._stack.setCurrentWidget(self._message)
        self.status_changed.emit("")

    def reset_view(self) -> None:
        if not self._traces:
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

        self._k_lines = QCheckBox("Common lines")
        self._k_lines.toggled.connect(self._redraw)

        self._band = QCheckBox("Mean ± std")
        self._band.setToolTip("Band per phase when two or more selected traces share a phase")
        self._band.toggled.connect(self._redraw)

        self._diff = QComboBox()
        self._diff.addItem("No difference", None)
        self._diff.currentIndexChanged.connect(self._redraw)

        reset_btn = QPushButton("Reset view")
        reset_btn.clicked.connect(self.reset_view)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Y"))
        row1.addWidget(self._y_mode)
        row1.addWidget(self._log)
        row1.addWidget(self._smooth)
        row1.addWidget(self._window)
        row1.addWidget(self._window_kev)
        row1.addStretch(1)
        row1.addWidget(reset_btn)

        row2 = QHBoxLayout()
        row2.addWidget(self._roi)
        row2.addWidget(self._cursors)
        row2.addWidget(self._u_lines)
        row2.addWidget(self._k_lines)
        row2.addWidget(self._band)
        row2.addWidget(QLabel("Δ vs"))
        row2.addWidget(self._diff, stretch=1)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: palette(mid);")

        self._message = QLabel(
            "Select a spectrum. Shift-click or Command-click overlays several."
        )
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
        layout.addWidget(self._hint)
        layout.addWidget(self._stack, stretch=1)

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

    def _slope(self) -> float:
        if self._traces:
            return float(self._traces[0].slope_kev) or 0.01466
        return 0.01466

    def _on_roi_toggled(self, on: bool) -> None:
        self._region.setVisible(on)
        self._update_readout()

    def _on_cursors_toggled(self, on: bool) -> None:
        if on and self._traces:
            energy = self._traces[0].energy_kev
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
        ok = bool(self._traces) and all(
            trace.live_time_s is not None and trace.live_time_s > 0
            for trace in self._traces
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

    def _redraw(self, *_args: object) -> None:
        if not self._traces:
            self._message.setText(
                "Select a spectrum. Shift-click or Command-click overlays several."
            )
            self._stack.setCurrentWidget(self._message)
            return

        prepared: list[tuple[SpectrumTrace, np.ndarray, np.ndarray]] = []
        for trace in self._traces:
            try:
                y = self._y_values(trace)
            except ValueError:
                continue
            prepared.append((trace, trace.energy_kev, y))
        if not prepared:
            self._message.setText("None of the selected spectra can use this Y mode.")
            self._stack.setCurrentWidget(self._message)
            return

        ref_path = self._diff.currentData()
        ref_y = None
        ref_e = None
        if ref_path:
            for trace, energy, y in prepared:
                if str(trace.path) == ref_path:
                    ref_e, ref_y = energy, y
                    break

        self._plot.clear()
        self._plot.addItem(self._region)
        self._plot.addItem(self._cursor_a)
        self._plot.addItem(self._cursor_b)
        self._region.setVisible(self._roi.isChecked())
        self._cursor_a.setVisible(self._cursors.isChecked())
        self._cursor_b.setVisible(self._cursors.isChecked())
        legend = self._plot.plotItem.legend
        if len(prepared) > 1 or ref_y is not None:
            if legend is None:
                self._plot.addLegend()
            elif legend is not None:
                legend.clear()
        elif legend is not None:
            legend.clear()

        log = self._log.isChecked()
        self._plot.setLogMode(x=False, y=log)
        self._plot.setLabel("left", self._y_label())
        self._plot.setLabel("bottom", "Energy (keV)")

        if self._band.isChecked():
            self._draw_bands(prepared)

        for index, (trace, energy, y) in enumerate(prepared):
            color = _COLORS[index % len(_COLORS)]
            y_plot = y
            name = trace.label
            if ref_y is not None and str(trace.path) != ref_path:
                _e, y_plot = self._math.difference(energy, y, ref_e, ref_y)
                name = f"Δ {trace.label}"
            elif ref_y is not None and str(trace.path) == ref_path:
                continue
            if log:
                y_plot = np.maximum(y_plot, 1e-6)
            self._plot.plot(
                energy,
                y_plot,
                pen=pg.mkPen(color, width=1.5),
                name=name,
            )

        self._draw_lines(prepared)
        self._stack.setCurrentWidget(self._plot)
        self._update_readout()

    def _draw_bands(
        self, prepared: list[tuple[SpectrumTrace, np.ndarray, np.ndarray]]
    ) -> None:
        by_phase: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
        for trace, energy, y in prepared:
            if not trace.phase:
                continue
            by_phase.setdefault(trace.phase, []).append((energy, y))
        for phase, rows in by_phase.items():
            if len(rows) < 2:
                continue
            energy, mean, std = self._math.mean_std(
                [row[0] for row in rows],
                [row[1] for row in rows],
            )
            upper = mean + std
            lower = mean - std
            if self._log.isChecked():
                lower = np.maximum(lower, 1e-6)
                upper = np.maximum(upper, 1e-6)
                mean = np.maximum(mean, 1e-6)
            color = "#ff7f0e" if phase == "post" else "#1f77b4"
            upper_curve = self._plot.plot(energy, upper, pen=None)
            lower_curve = self._plot.plot(energy, lower, pen=None)
            fill = pg.FillBetweenItem(
                upper_curve,
                lower_curve,
                brush=pg.mkBrush(pg.mkColor(color).red(), pg.mkColor(color).green(), pg.mkColor(color).blue(), 50),
            )
            self._plot.addItem(fill)
            self._plot.plot(
                energy,
                mean,
                pen=pg.mkPen(color, width=2, style=Qt.PenStyle.DashLine),
                name=f"{phase} mean",
            )

    def _draw_lines(self, _prepared: list[tuple[SpectrumTrace, np.ndarray, np.ndarray]]) -> None:
        for name, energy in self._lines.all_lines(
            uranium=self._u_lines.isChecked(),
            common=self._k_lines.isChecked(),
        ):
            line = pg.InfiniteLine(
                pos=energy,
                angle=90,
                movable=False,
                pen=pg.mkPen("#555555", width=1, style=Qt.PenStyle.DashLine),
                label=name,
                labelOpts={"position": 0.92, "color": "#555555"},
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
        offset = float(self._traces[0].offset_kev) if self._traces else 0.0007
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
        if self._roi.isChecked() and self._traces:
            lo, hi = self._region.getRegion()
            y = self._y_values(self._traces[0])
            stats = self._math.roi_stats(self._traces[0].energy_kev, y, lo, hi)
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
        n = len(self._traces)
        self.status_changed.emit(f"{n} spectrum" if n == 1 else f"{n} spectra")
