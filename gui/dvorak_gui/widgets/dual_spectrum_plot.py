"""X-123 spectra on the left axis, OPIXE pixel-detector spectra on the right.

The two detectors count very differently (an SDD channel of 15 eV against
variable-width cluster-energy bins on a 256x256 pixel chip), so each gets its
own y axis while sharing the energy axis. Y modes convert both to comparable
units first (counts / keV / s by default).

Log scaling is applied per axis here rather than through ``PlotItem.setLogMode``,
which would switch both axes together.

Shared with the X-123 plot: smoothing, ROI and cursors, U / Th / Bi / Ra and
common line markers, metadata legend fields and editable legend names,
Generate ROOT / Legacy ROOT / Save PDF, and recipe state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

from dvorak_root.text import root_text
from lib.pixet import CHIP_KEY, PixelSpectrum, region_label

from ..campaign_import import load_spectrum_module
from ..jsrootview import JsRootView
from ..recipes import path_key
from .capture_metadata import field_label, format_value
from .legend_names import LegendNamesDialog
from .spectrum_plot import SpectrumTrace

# Cool colours for the SDD, warm for the pixel chip, so the axis a curve
# belongs to reads at a glance.
_X123_COLORS = ("#1f77b4", "#17becf", "#2ca02c", "#9467bd", "#7f7f7f", "#bcbd22")
_PIXEL_COLORS = ("#d62728", "#ff7f0e", "#8c564b", "#e377c2", "#b8860b", "#a0522d")
_CURSOR_A = "#d62728"
_CURSOR_B = "#2ca02c"
_ROI = (31, 119, 180)
_ROI_COLOR = "#1f77b4"
_LINE_COLOR = "#555555"
_LOG_FLOOR = 1e-12
_Y_MODES = (
    ("rate", "Counts / keV / s"),
    ("density", "Counts / keV"),
    ("counts", "Counts per bin"),
    ("max", "Max = 1"),
    ("integral", "Area = 1"),
)
_EMPTY = (
    "Select X-123 spectra (top list) and pixel-detector measurements (bottom "
    "list). Shift-click or Command-click selects several in either list."
)


@dataclass
class PixelTrace:
    """A derived OPIXE measurement: its chip spectrum and any region spectra."""

    measurement_id: str
    label: str
    spectra: dict[str, PixelSpectrum]
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"pixel:{self.measurement_id}"


@dataclass
class _Curve:
    key: str
    label: str
    x: np.ndarray  # centres for X-123, step vertices for pixel
    y: np.ndarray
    color: str
    family: str  # "x123" or "pixel"
    width: float = 1.5
    band: tuple[np.ndarray, np.ndarray] | None = None


def _steps(edges: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Histogram outline as a polyline, the same in pyqtgraph and ROOT."""
    xs = np.repeat(edges, 2)[1:-1]
    ys = np.repeat(values, 2)
    return xs, ys


def _overlap_counts(edges: np.ndarray, counts: np.ndarray, lo: float, hi: float) -> float:
    """Counts in [lo, hi], sharing a partly covered bin by its overlap."""
    left = np.maximum(edges[:-1], lo)
    right = np.minimum(edges[1:], hi)
    width = np.diff(edges)
    share = np.clip(right - left, 0.0, None) / np.where(width > 0, width, 1.0)
    return float(np.sum(counts * share))


class DualSpectrumPlot(QWidget):
    status_changed = Signal(str)
    legacy_root_requested = Signal()
    pdf_requested = Signal()
    generate_root_requested = Signal()
    root_resized = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._x123: list[SpectrumTrace] = []
        self._pixel: list[PixelTrace] = []
        self._legend_keys: list[str] = []
        self._names: dict[str, str] = {}
        self._syncing = False
        self._math = load_spectrum_module("spectrum")
        self._lines = load_spectrum_module("lines")
        self._build()
        self._redraw()

    # -- data --------------------------------------------------------------

    def set_x123(self, traces: list[SpectrumTrace]) -> None:
        self._x123 = list(traces)
        self._redraw()

    def set_pixel(self, traces: list[PixelTrace]) -> None:
        self._pixel = list(traces)
        self._refresh_region_combo()
        self._redraw()

    def x123_traces(self) -> list[SpectrumTrace]:
        return list(self._x123)

    def pixel_traces(self) -> list[PixelTrace]:
        return list(self._pixel)

    def reset_view(self) -> None:
        if self._stack.currentWidget() is not self._plot:
            return
        self._fit_x()
        self._vb.enableAutoRange(axis=pg.ViewBox.YAxis)
        self._vb2.enableAutoRange(axis=pg.ViewBox.YAxis)

    def export_png(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        import pyqtgraph.exporters

        exporter = pyqtgraph.exporters.ImageExporter(self._plot.scene())
        exporter.parameters()["width"] = 1600
        exporter.export(str(path))
        return path

    # -- legend ------------------------------------------------------------

    def set_legend_fields(self, keys: list[str]) -> None:
        self._legend_keys = list(keys)
        self._redraw()

    def legend_fields(self) -> list[str]:
        return list(self._legend_keys)

    def _decorate(self, label: str, meta: dict[str, Any]) -> str:
        parts = []
        for key in self._legend_keys:
            value = meta.get(key)
            if value is None or value == "":
                continue
            parts.append(f"{field_label(key)} {format_value(key, value)}")
        return f"{label} ({', '.join(parts)})" if parts else label

    def _x123_name_key(self, trace: SpectrumTrace) -> str:
        return path_key(trace.path)

    def _pixel_name_key(self, trace: PixelTrace) -> str:
        return trace.key

    def _pixel_default(self, trace: PixelTrace, region: str) -> str:
        if region == CHIP_KEY:
            return trace.label
        spectrum = trace.spectra.get(region)
        thick = spectrum.thickness_mm if spectrum is not None else None
        return f"{trace.label} · {region_label(region, thick)}"

    def edit_legend_names(self) -> None:
        region = self._region_key()
        rows = [
            (self._x123_name_key(t), t.label, self._names.get(self._x123_name_key(t), ""))
            for t in self._x123
        ]
        rows += [
            (
                self._pixel_name_key(t),
                self._pixel_default(t, region),
                self._names.get(self._pixel_name_key(t), ""),
            )
            for t in self._pixel
        ]
        if not rows:
            self.status_changed.emit("Select a spectrum first")
            return
        suffix = ", ".join(field_label(k) for k in self._legend_keys)
        dialog = LegendNamesDialog(rows, suffix, self)
        if dialog.exec():
            self._names = dialog.names()
            self._redraw()

    # -- ROOT canvas -------------------------------------------------------

    def root_active(self) -> bool:
        return self._stack.currentWidget() is self.root_view

    def root_size(self) -> tuple[int, int]:
        size = self._stack.size()
        return max(1, size.width()), max(1, size.height())

    def show_root(self, payload: str, height: int) -> None:
        self.root_view.draw(payload, height)
        self._stack.setCurrentWidget(self.root_view)
        self._root_btn.setText("Live plot")

    def show_live(self) -> None:
        if self.root_active():
            self._redraw()

    def _on_root_clicked(self) -> None:
        if self.root_active():
            self._redraw()
        else:
            self.generate_root_requested.emit()

    # -- curves ------------------------------------------------------------

    def _mode(self) -> str:
        return str(self._y_mode.currentData() or "rate")

    def _unit(self) -> str:
        return self._y_mode.currentText()

    def _region_key(self) -> str:
        return str(self._region.currentData() or CHIP_KEY)

    def _smooth_window(self) -> int:
        if not self._smooth.isChecked():
            return 1
        return self._math.odd_window(self._window.value())

    def _x123_y(self, trace: SpectrumTrace) -> np.ndarray | None:
        counts = self._math.moving_average(trace.counts, self._smooth_window())
        counts = np.asarray(counts, dtype=np.float64)
        slope = float(trace.slope_kev) or 0.01466
        mode = self._mode()
        if mode == "counts":
            return counts
        density = counts / slope
        if mode == "density":
            return density
        if mode == "rate":
            live = trace.live_time_s
            return density / float(live) if live and live > 0 else None
        if mode == "max":
            peak = float(np.max(density)) if density.size else 0.0
            return density / peak if peak > 0 else None
        total = float(np.sum(counts))
        return density / total if total > 0 else None

    def _pixel_y(self, spectrum: PixelSpectrum) -> tuple[np.ndarray, np.ndarray] | None:
        """Values and 1σ errors per bin in the current mode."""
        counts = spectrum.counts
        errors = spectrum.errors
        widths = np.where(spectrum.widths > 0, spectrum.widths, 1.0)
        mode = self._mode()
        if mode == "counts":
            return counts, errors
        scale = 1.0 / widths
        if mode == "rate":
            live = spectrum.live_time_s
            if not live or live <= 0:
                return None
            scale = scale / float(live)
        elif mode == "max":
            peak = float(np.max(counts * scale)) if counts.size else 0.0
            if peak <= 0:
                return None
            scale = scale / peak
        elif mode == "integral":
            total = float(np.sum(counts))
            if total <= 0:
                return None
            scale = scale / total
        return counts * scale, errors * scale

    def _curves(self) -> tuple[list[_Curve], list[_Curve], list[str]]:
        """(x123 curves, pixel curves, skipped labels)."""
        skipped: list[str] = []
        left: list[_Curve] = []
        for index, trace in enumerate(self._x123):
            y = self._x123_y(trace)
            if y is None:
                skipped.append(trace.label)
                continue
            base = self._names.get(self._x123_name_key(trace), trace.label)
            left.append(
                _Curve(
                    key=self._x123_name_key(trace),
                    label=self._decorate(base, trace.meta),
                    x=np.asarray(trace.energy_kev, dtype=np.float64),
                    y=y,
                    color=_X123_COLORS[index % len(_X123_COLORS)],
                    family="x123",
                )
            )
        right: list[_Curve] = []
        region = self._region_key()
        show_errors = self._errors.isChecked()
        for index, trace in enumerate(self._pixel):
            spectrum = trace.spectra.get(region)
            values = self._pixel_y(spectrum) if spectrum is not None else None
            if spectrum is None or values is None:
                skipped.append(trace.label)
                continue
            y, err = values
            xs, ys = _steps(spectrum.edges, y)
            band = None
            if show_errors:
                _x, lo = _steps(spectrum.edges, y - err)
                _x, hi = _steps(spectrum.edges, y + err)
                band = (lo, hi)
            meta = dict(trace.meta)
            if spectrum.live_time_s is not None:
                meta["live_time_s"] = spectrum.live_time_s
            base = self._names.get(self._pixel_name_key(trace), self._pixel_default(trace, region))
            right.append(
                _Curve(
                    key=trace.key,
                    label=self._decorate(base, meta),
                    x=xs,
                    y=ys,
                    color=_PIXEL_COLORS[index % len(_PIXEL_COLORS)],
                    family="pixel",
                    width=2.0,
                    band=band,
                )
            )
        return left, right, skipped

    def _axes(self, left: list[_Curve], right: list[_Curve]):
        """Which curves go on each axis; one family alone uses the left axis."""
        if left:
            return left, right
        return right, []

    def _axis_title(self, curves: list[_Curve]) -> str:
        if not curves:
            return ""
        name = "X-123" if curves[0].family == "x123" else "Pixel"
        return f"{name}: {self._unit()}"

    def _axis_color(self, curves: list[_Curve]) -> str | None:
        """Colour an axis like its curves when it carries exactly one."""
        return curves[0].color if len(curves) == 1 else None

    # -- drawing -----------------------------------------------------------

    def _log_left(self) -> bool:
        return self._log_l.isChecked()

    def _log_right(self) -> bool:
        return self._log_r.isChecked()

    @staticmethod
    def _plot_y(y: np.ndarray, log: bool) -> np.ndarray:
        if not log:
            return y
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(y > 0, np.log10(np.maximum(y, _LOG_FLOOR)), np.nan)

    def _redraw(self, *_args: object) -> None:
        if self._syncing:
            return
        left_all, right_all, skipped = self._curves()
        first, second = self._axes(left_all, right_all)
        self._skipped = skipped
        if not first:
            self._message.setText(self._empty_text(skipped))
            self._hint.clear()
            self._stack.setCurrentWidget(self._message)
            self._root_btn.setText("Generate ROOT")
            self._set_export_enabled(False)
            self.status_changed.emit("")
            return
        had_data = bool(self._drawn_keys)
        keys = {c.key for c in first + second}
        new_data = keys != self._drawn_keys
        self._drawn_keys = keys

        for item in list(self._vb2.addedItems):
            self._vb2.removeItem(item)
        self._plot.clear()
        self._plot.addItem(self._region_item)
        self._plot.addItem(self._cursor_a)
        self._plot.addItem(self._cursor_b)
        self._region_item.setVisible(self._roi.isChecked())
        self._cursor_a.setVisible(self._cursors.isChecked())
        self._cursor_b.setVisible(self._cursors.isChecked())
        legend = self._plot.plotItem.legend
        if legend is None:
            legend = self._plot.addLegend(offset=(-10, 10))
        legend.clear()

        log_l, log_r = self._log_left(), self._log_right()
        plot_item = self._plot.plotItem
        plot_item.getAxis("left").setLogMode(False, log_l)
        plot_item.getAxis("right").setLogMode(False, log_r)
        plot_item.setLabel("bottom", "Energy (keV)")
        self._label_axis("left", self._axis_title(first), self._axis_color(first))
        if second:
            plot_item.showAxis("right")
            self._label_axis("right", self._axis_title(second), self._axis_color(second))
        else:
            plot_item.hideAxis("right")

        for curve in first:
            self._draw_curve(curve, None, log_l, legend)
        for curve in second:
            self._draw_curve(curve, self._vb2, log_r, legend)
        self._draw_lines()
        self._stack.setCurrentWidget(self._plot)
        self._root_btn.setText("Generate ROOT")
        self._set_export_enabled(True)
        self._sync_views()
        if new_data or not had_data:
            self._fit_x()
            self._vb.enableAutoRange(axis=pg.ViewBox.YAxis)
        self._vb2.enableAutoRange(axis=pg.ViewBox.YAxis)
        self._update_readout()

    def _label_axis(self, side: str, text: str, color: str | None) -> None:
        axis = self._plot.plotItem.getAxis(side)
        pen = pg.mkPen(color) if color else pg.mkPen("#333333")
        axis.setPen(pen)
        axis.setTextPen(pen)
        axis.setLabel(text, color=color or "#333333")

    def _draw_curve(self, curve: _Curve, vb, log: bool, legend) -> None:
        pen = pg.mkPen(curve.color, width=curve.width)
        y = self._plot_y(curve.y, log)
        item = pg.PlotDataItem(curve.x, y, pen=pen, connect="finite")
        if curve.band is not None:
            lo = pg.PlotDataItem(curve.x, self._plot_y(curve.band[0], log), pen=None, connect="finite")
            hi = pg.PlotDataItem(curve.x, self._plot_y(curve.band[1], log), pen=None, connect="finite")
            color = pg.mkColor(curve.color)
            fill = pg.FillBetweenItem(
                lo, hi, brush=pg.mkBrush(color.red(), color.green(), color.blue(), 60)
            )
            fill.setZValue(-10)
            if vb is None:
                self._plot.addItem(fill)
            else:
                vb.addItem(fill)
        if vb is None:
            self._plot.addItem(item)
        else:
            vb.addItem(item)
        legend.addItem(item, curve.label)

    def _draw_lines(self) -> None:
        for name, energy, position in self._marked():
            line = pg.InfiniteLine(
                pos=energy,
                angle=90,
                movable=False,
                pen=pg.mkPen(_LINE_COLOR, width=1, style=Qt.PenStyle.DashLine),
                label=name,
                labelOpts={"position": position, "color": _LINE_COLOR},
            )
            self._plot.addItem(line)

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

    def _fit_x(self) -> None:
        """Show the X-123 energy span when there is one (pixel spectra run to 200 keV)."""
        spans = [
            (float(np.nanmin(t.energy_kev)), float(np.nanmax(t.energy_kev)))
            for t in self._x123
            if len(t.energy_kev)
        ]
        if not spans:
            region = self._region_key()
            spans = [
                (float(s.edges[0]), float(s.edges[-1]))
                for t in self._pixel
                if (s := t.spectra.get(region)) is not None
            ]
        if not spans:
            return
        lo = min(a for a, _b in spans)
        hi = max(b for _a, b in spans)
        if hi > lo:
            self._vb.setXRange(lo, hi, padding=0.01)

    def _sync_views(self) -> None:
        self._vb2.setGeometry(self._vb.sceneBoundingRect())
        self._vb2.linkedViewChanged(self._vb, self._vb2.XAxis)

    def _empty_text(self, skipped: list[str]) -> str:
        if (self._x123 or self._pixel) and skipped:
            if self._mode() == "rate":
                return (
                    "The selected spectra have no live time, so Counts / keV / s "
                    "cannot be drawn. Choose another Y mode."
                )
            return "The selected pixel measurements have no spectrum for this region."
        return _EMPTY

    # -- export ------------------------------------------------------------

    def _view_y(self, vb, log: bool) -> tuple[float, float]:
        y0, y1 = vb.viewRange()[1]
        lo, hi = float(min(y0, y1)), float(max(y0, y1))
        if log:
            lo, hi = 10.0**lo, 10.0**hi
        return lo, hi

    def _series(self, curve: _Curve, xmin: float, xmax: float, log: bool) -> list[dict]:
        mask = (curve.x >= xmin) & (curve.x <= xmax)
        # Keep one point either side so lines run to the frame edge.
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            return []
        i0 = max(0, int(idx[0]) - 1)
        i1 = min(curve.x.size, int(idx[-1]) + 2)
        xs = curve.x[i0:i1]
        ys = curve.y[i0:i1]
        if log:
            ys = np.maximum(ys, _LOG_FLOOR)
        keep = np.isfinite(xs) & np.isfinite(ys)
        out: list[dict] = []
        if curve.band is not None:
            lo = curve.band[0][i0:i1][keep]
            hi = curve.band[1][i0:i1][keep]
            if log:
                lo = np.maximum(lo, _LOG_FLOOR)
            out.append(
                {
                    "x": xs[keep].tolist(),
                    "y_low": lo.tolist(),
                    "y_high": hi.tolist(),
                    "color": curve.color,
                    "fill_alpha": 0.25,
                    "label": "",
                }
            )
        out.append(
            {
                "x": xs[keep].tolist(),
                "y": ys[keep].tolist(),
                "label": root_text(curve.label),
                "color": curve.color,
                "line": "solid",
                "marker": "none",
                "width": curve.width,
            }
        )
        return out

    def publication_spec(self, name: str = "x123_pixel") -> dict | None:
        left_all, right_all, _skipped = self._curves()
        first, second = self._axes(left_all, right_all)
        if not first:
            return None
        x0, x1 = self._vb.viewRange()[0]
        xmin, xmax = float(min(x0, x1)), float(max(x0, x1))
        log_l, log_r = self._log_left(), self._log_right()
        ymin, ymax = self._view_y(self._vb, log_l)
        series: list[dict] = []
        for curve in first:
            series.extend(self._series(curve, xmin, xmax, log_l))
        panel: dict = {
            "x_title": "Energy (keV)",
            "y_title": self._axis_title(first),
            "y_color": self._axis_color(first),
            "logy": log_l,
            "xmin": xmin,
            "xmax": xmax,
            "ymin": ymin,
            "ymax": ymax,
            "series": series,
            "vlines": self._marker_lines(),
            "vspans": [],
            "notes": [],
            "legend": True,
            # Room above both axes' data for the legend and ROI notes.
            "headroom": True,
        }
        if second:
            y2min, y2max = self._view_y(self._vb2, log_r)
            series2: list[dict] = []
            for curve in second:
                series2.extend(self._series(curve, xmin, xmax, log_r))
            panel.update(
                {
                    "series2": series2,
                    "y2_title": self._axis_title(second),
                    "y2_color": self._axis_color(second),
                    "logy2": log_r,
                    "y2min": y2min,
                    "y2max": y2max,
                }
            )
        if self._roi.isChecked():
            lo, hi = self._region_item.getRegion()
            panel["vspans"].append(
                {"x0": float(min(lo, hi)), "x1": float(max(lo, hi)), "color": _ROI_COLOR, "label": "ROI"}
            )
            panel["notes"].append({"text": self._roi_note(), "align": "left"})
        if self._cursors.isChecked():
            delta = abs(float(self._cursor_b.value()) - float(self._cursor_a.value()))
            panel["notes"].append({"text": f"#DeltaE = {delta:.3f} keV", "align": "left"})
        return {
            "name": name,
            "width": max(480, self._plot.width()),
            "height": max(320, self._plot.height()),
            "cols": 1,
            "panels": [panel],
        }

    def _marker_lines(self) -> list[dict]:
        lines = [
            {
                "x": float(energy),
                "color": _LINE_COLOR,
                "label": root_text(name),
                "style": "dashed",
                "label_pos": position,
            }
            for name, energy, position in self._marked()
        ]
        if self._cursors.isChecked():
            for cursor, color, label in (
                (self._cursor_a, _CURSOR_A, "A"),
                (self._cursor_b, _CURSOR_B, "B"),
            ):
                lines.append(
                    {"x": float(cursor.value()), "color": color, "label": label, "style": "solid"}
                )
        return lines

    # -- ROI and readout -----------------------------------------------------

    def _roi_rows(self) -> list[tuple[str, float, float | None]]:
        """(label, counts in ROI, live time) for each drawn spectrum."""
        lo, hi = sorted(float(v) for v in self._region_item.getRegion())
        rows: list[tuple[str, float, float | None]] = []
        for trace in self._x123:
            energy = np.asarray(trace.energy_kev)
            inside = (energy >= lo) & (energy <= hi)
            base = self._names.get(self._x123_name_key(trace), trace.label)
            rows.append((base, float(np.sum(trace.counts[inside])), trace.live_time_s))
        region = self._region_key()
        for trace in self._pixel:
            spectrum = trace.spectra.get(region)
            if spectrum is None:
                continue
            base = self._names.get(self._pixel_name_key(trace), self._pixel_default(trace, region))
            rows.append(
                (base, _overlap_counts(spectrum.edges, spectrum.counts, lo, hi), spectrum.live_time_s)
            )
        return rows

    def _roi_note(self) -> str:
        lo, hi = sorted(float(v) for v in self._region_item.getRegion())
        lines = [f"ROI {lo:.2f}-{hi:.2f} keV"]
        for label, counts, live in self._roi_rows()[:6]:
            rate = f", {counts / live:.4g} /s" if live and live > 0 else ""
            lines.append(f"{label}: {counts:.4g} counts{rate}")
        return "\n".join(lines)

    def _on_mouse_moved(self, event: Any) -> None:
        pos = event[0]
        if not self._plot.sceneBoundingRect().contains(pos):
            return
        point = self._vb.mapSceneToView(pos)
        right = self._vb2.mapSceneToView(pos)
        self._update_readout(hover=(float(point.x()), float(point.y()), float(right.y())))

    def _update_readout(self, *_args: Any, hover: tuple[float, float, float] | None = None) -> None:
        parts: list[str] = []
        if hover is not None:
            energy, y_left, y_right = hover
            if self._log_left():
                y_left = 10.0**y_left
            if self._log_right():
                y_right = 10.0**y_right
            parts.append(f"E={energy:.3f} keV")
            parts.append(f"left={y_left:.4g}")
            if self._plot.plotItem.getAxis("right").isVisible():
                parts.append(f"right={y_right:.4g}")
        if self._cursors.isChecked():
            delta = abs(float(self._cursor_b.value()) - float(self._cursor_a.value()))
            parts.append(f"ΔE={delta:.3f} keV")
        if self._roi.isChecked():
            lo, hi = sorted(float(v) for v in self._region_item.getRegion())
            rows = self._roi_rows()
            summary = "  ".join(f"{label}: {counts:.4g}" for label, counts, _live in rows[:4])
            parts.append(f"ROI {lo:.2f}–{hi:.2f} keV  {summary}")
        window = self._smooth_window()
        if window > 1:
            parts.append(f"X-123 MA {window} ch")
        if getattr(self, "_skipped", None):
            parts.append(f"not drawn: {', '.join(self._skipped)}")
        self._hint.setText("   ".join(parts))
        n1, n2 = len(self._x123), len(self._pixel)
        self.status_changed.emit(f"{n1} X-123 · {n2} pixel")

    # -- recipe state --------------------------------------------------------

    def recipe_state(self) -> dict[str, Any]:
        lo, hi = self._region_item.getRegion()
        x0, x1 = self._vb.viewRange()[0]
        y_left = self._view_y(self._vb, self._log_left())
        y_right = self._view_y(self._vb2, self._log_right())
        return {
            "y_mode": self._mode(),
            "log_left": self._log_left(),
            "log_right": self._log_right(),
            "smooth": self._smooth.isChecked(),
            "window_channels": self._window.value(),
            "pixel_region": self._region_key(),
            "pixel_errors": self._errors.isChecked(),
            "roi": {"on": self._roi.isChecked(), "lo": float(lo), "hi": float(hi)},
            "cursors": {
                "on": self._cursors.isChecked(),
                "a": float(self._cursor_a.value()),
                "b": float(self._cursor_b.value()),
            },
            "u_lines": self._u_lines.isChecked(),
            "material_lines": self._material_lines.isChecked(),
            "common_lines": self._k_lines.isChecked(),
            "legend_fields": list(self._legend_keys),
            "legend_names": dict(self._names),
            "range": {"x": [float(x0), float(x1)], "y_left": list(y_left), "y_right": list(y_right)},
        }

    def apply_recipe_state(self, state: dict[str, Any]) -> None:
        def pick(widget: QComboBox, value) -> None:
            index = widget.findData(value)
            if index >= 0:
                widget.setCurrentIndex(index)

        self._syncing = True
        try:
            pick(self._y_mode, state.get("y_mode"))
            pick(self._region, state.get("pixel_region"))
            self._log_l.setChecked(bool(state.get("log_left")))
            self._log_r.setChecked(bool(state.get("log_right")))
            self._smooth.setChecked(bool(state.get("smooth")))
            self._window.setValue(int(state.get("window_channels") or 5))
            self._errors.setChecked(bool(state.get("pixel_errors")))
            self._u_lines.setChecked(bool(state.get("u_lines")))
            self._material_lines.setChecked(bool(state.get("material_lines")))
            self._k_lines.setChecked(bool(state.get("common_lines")))
            self._legend_keys = list(state.get("legend_fields") or [])
            self._names = dict(state.get("legend_names") or {})
            roi = state.get("roi") or {}
            cur = state.get("cursors") or {}
            for box, on in ((self._roi, roi.get("on")), (self._cursors, cur.get("on"))):
                box.blockSignals(True)
                box.setChecked(bool(on))
                box.blockSignals(False)
            if "lo" in roi:
                self._region_item.setRegion((roi["lo"], roi["hi"]))
            if "a" in cur:
                self._cursor_a.setValue(cur["a"])
                self._cursor_b.setValue(cur["b"])
        finally:
            self._syncing = False
        self._redraw()
        view = state.get("range") or {}
        if view.get("x") and self._stack.currentWidget() is self._plot:
            self._vb.setXRange(*view["x"], padding=0)
            for vb, key, log in (
                (self._vb, "y_left", self._log_left()),
                (self._vb2, "y_right", self._log_right()),
            ):
                lo, hi = (view.get(key) or [None, None])[:2]
                if lo is None or hi is None:
                    continue
                if log:
                    if lo <= 0 or hi <= 0:
                        continue
                    lo, hi = np.log10(lo), np.log10(hi)
                vb.setYRange(lo, hi, padding=0)

    # -- controls ------------------------------------------------------------

    def _refresh_region_combo(self) -> None:
        current = self._region_key()
        keys = {CHIP_KEY}
        thickness: dict[str, float | None] = {}
        for trace in self._pixel:
            for key, spectrum in trace.spectra.items():
                keys.add(key)
                thickness.setdefault(key, spectrum.thickness_mm)
        ordered = sorted(keys, key=lambda k: -1 if k == CHIP_KEY else int(k.split(":")[1]))
        self._region.blockSignals(True)
        self._region.clear()
        for key in ordered:
            self._region.addItem(region_label(key, thickness.get(key)), key)
        index = self._region.findData(current)
        self._region.setCurrentIndex(max(0, index))
        self._region.blockSignals(False)

    def _set_export_enabled(self, enabled: bool) -> None:
        self._root_btn.setEnabled(enabled)
        self._legacy_btn.setEnabled(enabled)
        self._pdf_btn.setEnabled(enabled)

    def _on_window_channels(self, value: int) -> None:
        if self._syncing:
            return
        odd = self._math.odd_window(value)
        slope = float(self._x123[0].slope_kev) if self._x123 else 0.01466
        self._syncing = True
        if odd != value:
            self._window.setValue(odd)
        self._window_kev.setValue(odd * slope)
        self._syncing = False
        self._redraw()

    def _on_window_kev(self, value: float) -> None:
        if self._syncing:
            return
        slope = float(self._x123[0].slope_kev) if self._x123 else 0.01466
        channels = 1 if value <= 0 else self._math.window_kev_to_channels(value, slope)
        self._syncing = True
        self._window.setValue(channels)
        self._syncing = False
        self._redraw()

    def _on_cursors_toggled(self, on: bool) -> None:
        if on:
            x0, x1 = self._vb.viewRange()[0]
            span = x1 - x0
            self._cursor_a.setValue(x0 + 0.25 * span)
            self._cursor_b.setValue(x0 + 0.75 * span)
        self._cursor_a.setVisible(on)
        self._cursor_b.setVisible(on)
        self._update_readout()

    def _build(self) -> None:
        pg.setConfigOptions(antialias=True, foreground="d")
        self._drawn_keys: set[str] = set()
        self._skipped: list[str] = []

        self._y_mode = QComboBox()
        for key, label in _Y_MODES:
            self._y_mode.addItem(label, key)
        self._y_mode.setToolTip(
            "Both axes use the same units so their shapes compare. Counts / keV "
            "divides by channel or bin width, which matters for the pixel "
            "spectrum's variable-width bins."
        )
        self._y_mode.currentIndexChanged.connect(self._redraw)
        self._log_l = QCheckBox("Log left")
        self._log_l.toggled.connect(self._redraw)
        self._log_r = QCheckBox("Log right")
        self._log_r.toggled.connect(self._redraw)

        self._smooth = QCheckBox("Smooth X-123")
        self._smooth.toggled.connect(self._redraw)
        self._window = QSpinBox()
        self._window.setRange(1, 101)
        self._window.setSingleStep(2)
        self._window.setValue(5)
        self._window.setToolTip("Odd moving-average window in X-123 channels")
        self._window.valueChanged.connect(self._on_window_channels)
        self._window_kev = QDoubleSpinBox()
        self._window_kev.setRange(0.0, 2.0)
        self._window_kev.setDecimals(3)
        self._window_kev.setSingleStep(0.015)
        self._window_kev.setSuffix(" keV")
        self._window_kev.valueChanged.connect(self._on_window_kev)

        self._region = QComboBox()
        self._region.addItem("Whole chip", CHIP_KEY)
        self._region.setToolTip(
            "Which derived.root spectrum to draw: the whole chip, or a region "
            "OPIXE built from the measurement's borders"
        )
        self._region.currentIndexChanged.connect(self._redraw)
        self._errors = QCheckBox("Pixel ±1σ")
        self._errors.setToolTip("Shade the statistical error of each pixel bin")
        self._errors.toggled.connect(self._redraw)

        self._roi = QCheckBox("ROI")
        self._roi.toggled.connect(lambda on: (self._region_item.setVisible(on), self._update_readout()))
        self._cursors = QCheckBox("Cursors")
        self._cursors.toggled.connect(self._on_cursors_toggled)
        self._u_lines = QCheckBox("U L lines")
        self._u_lines.toggled.connect(self._redraw)
        self._material_lines = QCheckBox("Th Bi Ra")
        self._material_lines.toggled.connect(self._redraw)
        self._k_lines = QCheckBox("Common lines")
        self._k_lines.toggled.connect(self._redraw)

        names_btn = QPushButton("Legend names…")
        names_btn.setToolTip("Set the base legend name of each trace")
        names_btn.clicked.connect(self.edit_legend_names)
        reset_btn = QPushButton("Reset view")
        reset_btn.clicked.connect(self.reset_view)
        self._root_btn = QPushButton("Generate ROOT")
        self._root_btn.setToolTip(
            "Render this view with ROOT in place of the live plot, both y axes included"
        )
        self._root_btn.clicked.connect(self._on_root_clicked)
        self._legacy_btn = QPushButton("Legacy ROOT")
        self._legacy_btn.setToolTip("Open the current view in the interactive ROOT GUI (root -l)")
        self._legacy_btn.clicked.connect(self.legacy_root_requested.emit)
        self._pdf_btn = QPushButton("Save PDF…")
        self._pdf_btn.setToolTip("Write the current view as a ROOT PDF")
        self._pdf_btn.clicked.connect(self.pdf_requested.emit)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Y"))
        row1.addWidget(self._y_mode)
        row1.addWidget(self._log_l)
        row1.addWidget(self._log_r)
        row1.addSpacing(8)
        row1.addWidget(self._smooth)
        row1.addWidget(self._window)
        row1.addWidget(self._window_kev)
        row1.addSpacing(8)
        row1.addWidget(QLabel("Pixel"))
        row1.addWidget(self._region)
        row1.addWidget(self._errors)
        row1.addStretch(1)

        row2 = QHBoxLayout()
        for box in (self._roi, self._cursors, self._u_lines, self._material_lines, self._k_lines):
            row2.addWidget(box)
        row2.addSpacing(8)
        row2.addWidget(names_btn)
        row2.addStretch(1)
        row2.addWidget(self._root_btn)
        row2.addWidget(self._legacy_btn)
        row2.addWidget(self._pdf_btn)
        row2.addWidget(reset_btn)

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
        self._plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        plot_item = self._plot.plotItem
        self._vb = plot_item.vb
        self._vb2 = pg.ViewBox()
        plot_item.showAxis("right")
        plot_item.scene().addItem(self._vb2)
        plot_item.getAxis("right").linkToView(self._vb2)
        self._vb2.setXLink(self._vb)
        # Beneath the left ViewBox, so its legend and ROI stay on top.
        self._vb2.setZValue(self._vb.zValue() - 1)
        self._vb.sigResized.connect(self._sync_views)
        self._proxy = pg.SignalProxy(
            self._plot.scene().sigMouseMoved, rateLimit=40, slot=self._on_mouse_moved
        )

        self._region_item = pg.LinearRegionItem(
            values=(12.0, 20.0),
            orientation="vertical",
            brush=pg.mkBrush(*_ROI, 40),
            pen=pg.mkPen(_ROI, width=1),
        )
        self._region_item.setZValue(-20)
        self._region_item.sigRegionChanged.connect(self._update_readout)
        self._region_item.setVisible(False)
        self._cursor_a = pg.InfiniteLine(
            angle=90, movable=True, pen=pg.mkPen(_CURSOR_A, width=1),
            label="A", labelOpts={"position": 0.95, "color": _CURSOR_A},
        )
        self._cursor_b = pg.InfiniteLine(
            angle=90, movable=True, pen=pg.mkPen(_CURSOR_B, width=1),
            label="B", labelOpts={"position": 0.90, "color": _CURSOR_B},
        )
        for cursor in (self._cursor_a, self._cursor_b):
            cursor.sigPositionChanged.connect(self._update_readout)
            cursor.setVisible(False)

        self.root_view = JsRootView(parent=self)
        self.root_view.resized.connect(lambda _w, _h: self.root_resized.emit())
        self._stack = QStackedWidget()
        self._stack.addWidget(self._message)
        self._stack.addWidget(self._plot)
        self._stack.addWidget(self.root_view)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row1)
        layout.addLayout(row2)
        layout.addWidget(self._hint)
        layout.addWidget(self._stack, stretch=1)
        self._set_export_enabled(False)
