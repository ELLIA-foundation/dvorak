"""Interactive oscilloscope trace: pyqtgraph + viewport min-max LOD."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lib.waveform import (
    decimate_minmax,
    time_scale_factor,
    voltage_scale_factor,
)

DEFAULT_LOD_POINTS = 8_000
_LOD_DEBOUNCE_MS = 40
_CURSOR_A = "#d62728"
_CURSOR_B = "#2ca02c"
_TRACE_PEN = pg.mkPen("#1f77b4", width=1)
_EVENT_TYPICAL = "#ff7f0e"
_EVENT_FIRST = "#d62728"


@dataclass(frozen=True)
class EventMark:
    """Breakdown marker drawn on the overview (SI seconds / volts)."""

    t_break: float
    v_breakdown: float
    first_cycle: bool = False
    event_index: int = 0


def format_seconds(value_s: float) -> str:
    factor, unit = time_scale_factor(abs(value_s) if value_s != 0 else 1.0)
    return f"{value_s * factor:.4g} {unit}"


def format_span(span_s: float) -> str:
    factor, unit = time_scale_factor(span_s)
    return f"{span_s * factor:.4g} {unit}"


class TimeAxisItem(pg.AxisItem):
    """Tick labels in ns / µs / ms / s from data stored in seconds."""

    def tickStrings(self, values, scale, spacing):  # noqa: ANN001, N802
        if not values:
            return []
        span = float(max(values) - min(values)) if len(values) > 1 else abs(float(values[0]))
        step = abs(float(spacing)) if spacing else 0.0
        factor, _unit = time_scale_factor(max(span, step * 8, 1e-15))
        return [f"{float(v) * factor:g}" for v in values]


class TracePlot(QWidget):
    """Plot ``time_s`` / ``voltage_v`` with viewport-aware min-max decimation."""

    status_changed = Signal(str)
    legacy_root_requested = Signal()
    pdf_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._time_s: np.ndarray | None = None
        self._voltage_v: np.ndarray | None = None
        self._v_scale = 1.0
        self._volt_unit = "V"
        self._shown = 0
        self._updating = False
        self._event_items: list[Any] = []
        self._event_marks: list[EventMark] = []
        self._lod_timer = QTimer(self)
        self._lod_timer.setSingleShot(True)
        self._lod_timer.setInterval(_LOD_DEBOUNCE_MS)
        self._lod_timer.timeout.connect(self._rebuild_lod)
        self._build()

    def set_waveform(self, time_s: np.ndarray, voltage_v: np.ndarray) -> None:
        self.clear_events()
        self._time_s = np.asarray(time_s, dtype=np.float64)
        self._voltage_v = np.asarray(voltage_v, dtype=np.float64)
        peak = float(np.max(np.abs(self._voltage_v))) if len(self._voltage_v) else 0.0
        self._v_scale, self._volt_unit = voltage_scale_factor(peak)
        self._plot.setLabel("left", f"Voltage ({self._volt_unit})")
        t0 = float(self._time_s[0]) if len(self._time_s) else 0.0
        t1 = float(self._time_s[-1]) if len(self._time_s) else 1.0
        y = self._voltage_v * self._v_scale
        y0 = float(np.min(y)) if len(y) else -1.0
        y1 = float(np.max(y)) if len(y) else 1.0
        pad = 0.04 * (y1 - y0 or 1.0)
        self._updating = True
        self._vb.setXRange(t0, t1, padding=0.0)
        self._vb.setYRange(y0 - pad, y1 + pad, padding=0.0)
        self._updating = False
        span = t1 - t0
        self._place_cursors(t0 + 0.25 * span, t0 + 0.75 * span)
        self._rebuild_lod()
        self._update_time_label()
        self._set_export_enabled(True)

    def clear_waveform(self) -> None:
        self.clear_events()
        self._time_s = None
        self._voltage_v = None
        self._curve.setData([], [])
        self._hover.setText("Open a waveform to plot.")
        self._set_export_enabled(False)
        self.status_changed.emit("")

    def clear_events(self) -> None:
        for item in self._event_items:
            self._plot.removeItem(item)
        self._event_items = []
        self._event_marks = []

    def set_events(self, events: Sequence[EventMark]) -> None:
        """Overlay t_break / v_breakdown like the spark-gap overview figure."""
        self.clear_events()
        self._event_marks = list(events)
        if not events:
            return
        spots = []
        for event in events:
            color = _EVENT_FIRST if event.first_cycle else _EVENT_TYPICAL
            y = event.v_breakdown * self._v_scale
            spots.append({"pos": (event.t_break, y), "brush": color, "pen": color})
            line = pg.InfiniteLine(
                pos=event.t_break,
                angle=90,
                movable=False,
                pen=pg.mkPen(color, width=1),
            )
            line.setOpacity(0.35)
            line.setZValue(-5)
            self._plot.addItem(line)
            self._event_items.append(line)
            label = pg.TextItem(
                f"{event.event_index}:{event.v_breakdown / 1000.0:.1f} kV",
                color=color,
                anchor=(0, 1),
            )
            label.setPos(event.t_break, y)
            self._plot.addItem(label)
            self._event_items.append(label)
        scatter = pg.ScatterPlotItem(
            spots=spots,
            size=8,
            hoverable=False,
        )
        scatter.setZValue(5)
        self._plot.addItem(scatter)
        self._event_items.append(scatter)

    def reset_view(self) -> None:
        if self._time_s is None or len(self._time_s) == 0:
            return
        t0 = float(self._time_s[0])
        t1 = float(self._time_s[-1])
        y = self._voltage_v * self._v_scale  # type: ignore[operator]
        y0 = float(np.min(y))
        y1 = float(np.max(y))
        pad = 0.04 * (y1 - y0 or 1.0)
        self._updating = True
        self._vb.setXRange(t0, t1, padding=0.0)
        self._vb.setYRange(y0 - pad, y1 + pad, padding=0.0)
        self._updating = False
        self._rebuild_lod()
        self._update_time_label()

    def export_png(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        import pyqtgraph.exporters

        exporter = pyqtgraph.exporters.ImageExporter(self._plot.plotItem)
        exporter.parameters()["width"] = 1600
        exporter.export(str(path))
        return path

    def view_span_s(self) -> float:
        x0, x1 = self._vb.viewRange()[0]
        return abs(float(x1) - float(x0))

    def shown_points(self) -> int:
        return self._shown

    def publication_spec(self, name: str = "trace") -> dict | None:
        """Decimated current viewport, in the same units as the on-screen axes."""
        if self._time_s is None or self._voltage_v is None or len(self._time_s) == 0:
            return None
        x0, x1 = self._vb.viewRange()[0]
        if x1 < x0:
            x0, x1 = x1, x0
        i0 = int(np.searchsorted(self._time_s, x0, side="left"))
        i1 = int(np.searchsorted(self._time_s, x1, side="right"))
        i0 = max(0, i0 - 1)
        i1 = min(len(self._time_s), max(i0 + 1, i1 + 1))
        t_slice = self._time_s[i0:i1]
        v_slice = self._voltage_v[i0:i1]
        t_plot, v_plot = decimate_minmax(t_slice, v_slice, DEFAULT_LOD_POINTS)
        mask = np.isfinite(t_plot) & np.isfinite(v_plot)
        t_plot = t_plot[mask]
        v_plot = v_plot[mask]
        if len(t_plot) == 0:
            return None
        span = abs(float(x1) - float(x0))
        factor, unit = time_scale_factor(span if span else 1.0)
        y0, y1 = self._vb.viewRange()[1]
        panel: dict = {
            "x_title": f"Time ({unit})",
            "y_title": f"Voltage ({self._volt_unit})",
            "xmin": float(x0) * factor,
            "xmax": float(x1) * factor,
            "ymin": float(min(y0, y1)),
            "ymax": float(max(y0, y1)),
            "series": [
                {
                    "x": (t_plot * factor).astype(float).tolist(),
                    "y": (v_plot * self._v_scale).astype(float).tolist(),
                    "label": "",
                    "color": "#1f77b4",
                    "line": "solid",
                    "marker": "none",
                    "width": 2,
                }
            ],
            "hlines": [{"y": 0.0, "color": "#888888", "style": "dashed"}],
            "vlines": [],
            "points": [],
            "legend": False,
        }
        if self._cursors.isChecked():
            panel["vlines"].extend(
                [
                    {
                        "x": float(self._cursor_a.value()) * factor,
                        "color": _CURSOR_A,
                        "label": "A",
                        "style": "solid",
                    },
                    {
                        "x": float(self._cursor_b.value()) * factor,
                        "color": _CURSOR_B,
                        "label": "B",
                        "style": "solid",
                    },
                ]
            )
        for event in self._event_marks:
            if event.t_break < x0 or event.t_break > x1:
                continue
            color = _EVENT_FIRST if event.first_cycle else _EVENT_TYPICAL
            x = event.t_break * factor
            y = event.v_breakdown * self._v_scale
            panel["vlines"].append(
                {"x": x, "color": color, "style": "dashed", "label": ""}
            )
            panel["points"].append(
                {
                    "x": x,
                    "y": y,
                    "color": color,
                    "label": f"{event.event_index}:{event.v_breakdown / 1000.0:.1f} kV",
                }
            )
        return {
            "name": name,
            "width": 960,
            "height": 520,
            "cols": 1,
            "panels": [panel],
        }

    def _set_export_enabled(self, enabled: bool) -> None:
        self._legacy_btn.setEnabled(enabled)
        self._pdf_btn.setEnabled(enabled)

    def _build(self) -> None:
        pg.setConfigOptions(antialias=False, foreground="d")
        self._plot = pg.PlotWidget(
            axisItems={"bottom": TimeAxisItem(orientation="bottom")}
        )
        self._plot.setBackground("w")
        self._plot.showGrid(x=True, y=True, alpha=0.25)
        self._plot.setLabel("bottom", "Time (s)")
        self._plot.setLabel("left", "Voltage (V)")
        self._vb = self._plot.plotItem.vb
        self._vb.sigRangeChanged.connect(self._on_range_changed)
        self._curve = self._plot.plot(
            pen=_TRACE_PEN,
            skipFiniteCheck=True,
        )
        self._zero = pg.InfiniteLine(
            pos=0.0,
            angle=0,
            pen=pg.mkPen(150, 150, 150, style=Qt.PenStyle.DashLine),
        )
        self._plot.addItem(self._zero)
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
        self._plot.addItem(self._cursor_a)
        self._plot.addItem(self._cursor_b)
        self._cursor_a.sigPositionChanged.connect(self._update_readout)
        self._cursor_b.sigPositionChanged.connect(self._update_readout)
        self._proxy = pg.SignalProxy(
            self._plot.scene().sigMouseMoved,
            rateLimit=40,
            slot=self._on_mouse_moved,
        )

        self._hover = QLabel("Open a waveform to plot.")
        reset_btn = QPushButton("Reset view")
        reset_btn.clicked.connect(self.reset_view)
        self._cursors = QCheckBox("Cursors")
        self._cursors.toggled.connect(self._toggle_cursors)
        self._legacy_btn = QPushButton("Legacy ROOT")
        self._legacy_btn.setToolTip(
            "Open the current view in the interactive ROOT GUI (root -l)"
        )
        self._legacy_btn.clicked.connect(self.legacy_root_requested.emit)
        self._pdf_btn = QPushButton("Save PDF…")
        self._pdf_btn.setToolTip("Write the current view as a ROOT PDF")
        self._pdf_btn.clicked.connect(self.pdf_requested.emit)
        self._set_export_enabled(False)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self._hover, stretch=1)
        toolbar.addWidget(self._cursors)
        toolbar.addWidget(self._legacy_btn)
        toolbar.addWidget(self._pdf_btn)
        toolbar.addWidget(reset_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._plot, stretch=1)
        layout.addLayout(toolbar)
        self._set_cursors_visible(False)

    def _place_cursors(self, t_a: float, t_b: float) -> None:
        self._cursor_a.blockSignals(True)
        self._cursor_b.blockSignals(True)
        self._cursor_a.setValue(t_a)
        self._cursor_b.setValue(t_b)
        self._cursor_a.blockSignals(False)
        self._cursor_b.blockSignals(False)

    def _toggle_cursors(self, on: bool) -> None:
        self._set_cursors_visible(on)
        self._update_readout()

    def _set_cursors_visible(self, on: bool) -> None:
        self._cursor_a.setVisible(on)
        self._cursor_b.setVisible(on)

    def _on_range_changed(self, _vb: Any, _ranges: Any) -> None:
        if self._updating or self._time_s is None:
            return
        self._lod_timer.start()
        self._update_time_label()

    def _update_time_label(self) -> None:
        span = self.view_span_s()
        _factor, unit = time_scale_factor(span if span else 1.0)
        self._plot.setLabel("bottom", f"Time ({unit})")

    def _rebuild_lod(self) -> None:
        if self._time_s is None or self._voltage_v is None or len(self._time_s) == 0:
            return
        x0, x1 = self._vb.viewRange()[0]
        if x1 < x0:
            x0, x1 = x1, x0
        i0 = int(np.searchsorted(self._time_s, x0, side="left"))
        i1 = int(np.searchsorted(self._time_s, x1, side="right"))
        i0 = max(0, i0 - 1)
        i1 = min(len(self._time_s), max(i0 + 1, i1 + 1))
        t_slice = self._time_s[i0:i1]
        v_slice = self._voltage_v[i0:i1]
        t_plot, v_plot = decimate_minmax(t_slice, v_slice, DEFAULT_LOD_POINTS)
        self._shown = int(len(t_plot))
        self._updating = True
        self._curve.setData(t_plot, v_plot * self._v_scale)
        self._updating = False
        self._emit_status()

    def _on_mouse_moved(self, event: Any) -> None:
        if self._time_s is None:
            return
        pos = event[0]
        if not self._plot.sceneBoundingRect().contains(pos):
            return
        point = self._vb.mapSceneToView(pos)
        self._update_readout(hover_t=float(point.x()), hover_v=float(point.y()))

    def _update_readout(
        self,
        *args: Any,
        hover_t: float | None = None,
        hover_v: float | None = None,
    ) -> None:
        parts: list[str] = []
        if hover_t is not None and hover_v is not None:
            parts.append(f"t={format_seconds(hover_t)}")
            parts.append(f"V={hover_v:.4g} {self._volt_unit}")
        if self._cursors.isChecked():
            t_a = float(self._cursor_a.value())
            t_b = float(self._cursor_b.value())
            dt = abs(t_b - t_a)
            parts.append(f"Δt={format_span(dt)}")
            if self._time_s is not None and self._voltage_v is not None:
                va = self._sample_voltage(t_a) * self._v_scale
                vb = self._sample_voltage(t_b) * self._v_scale
                parts.append(f"ΔV={vb - va:+.4g} {self._volt_unit}")
        span = self.view_span_s()
        if span > 0:
            parts.append(f"view {format_span(span)}")
        self._hover.setText("   ".join(parts) if parts else "Open a waveform to plot.")

    def _sample_voltage(self, t_s: float) -> float:
        assert self._time_s is not None and self._voltage_v is not None
        idx = int(np.searchsorted(self._time_s, t_s))
        idx = min(max(idx, 0), len(self._voltage_v) - 1)
        return float(self._voltage_v[idx])

    def _emit_status(self) -> None:
        if self._time_s is None:
            self.status_changed.emit("")
            return
        n = len(self._time_s)
        self.status_changed.emit(
            f"{n:,} points · display {self._shown:,} · view {format_span(self.view_span_s())}"
        )
