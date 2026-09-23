"""Intensity chronograph for one clip, or an overlay of several."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

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
_EDGE = "#2ca02c"


@dataclass
class ChronoTrace:
    label: str
    time_s: np.ndarray
    intensity: np.ndarray
    t1_s: float | None


def read_chronograph(csv_path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Return time and mean intensity (``mean_Y``, else ``mean_rgb``)."""
    times: list[float] = []
    values: list[float] = []
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise RuntimeError(f"Empty CSV: {csv_path}")
        if "mean_Y" in reader.fieldnames:
            key = "mean_Y"
        elif "mean_rgb" in reader.fieldnames:
            key = "mean_rgb"
        else:
            raise RuntimeError(f"No intensity column in {csv_path}")
        for row in reader:
            times.append(float(row["t_s"]))
            values.append(float(row[key]))
    if not times:
        raise RuntimeError(f"No rows in {csv_path}")
    return np.asarray(times, dtype=np.float64), np.asarray(values, dtype=np.float64)


class ChronographPlot(QWidget):
    """Plot chronographs. A single raw-time trace has a draggable rising edge."""

    edge_moved = Signal(float)
    extract_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._traces: list[ChronoTrace] = []
        self._missing = 0
        self._busy = False
        self._campaign: str | None = None
        self._edge: pg.InfiniteLine | None = None
        self._build()

    def set_campaign(self, name: str | None) -> None:
        self._campaign = name
        self._sync_extract_button()

    def set_extracting(self, extracting: bool) -> None:
        self._busy = extracting
        self._progress.setVisible(extracting)
        if extracting:
            self._progress.setRange(0, 1000)
            self._progress.setValue(0)
            self._progress.setFormat("Starting…")
        self._sync_extract_button()

    def set_progress(self, completed: float, total: int, name: str) -> None:
        """``completed`` is clips finished plus the fraction of the current clip."""
        if total <= 0:
            self._progress.setRange(0, 0)
            self._progress.setFormat(name or "Extracting…")
            return
        self._progress.setRange(0, 1000)
        fraction = min(1.0, max(0.0, completed / total))
        self._progress.setValue(int(round(1000 * fraction)))
        shown = total if completed >= total else min(total, int(completed) + 1)
        label = name.replace("%", "%%") if name else "Extracting"
        self._progress.setFormat(f"{label}  {shown}/{total}")

    def set_traces(self, traces: list[ChronoTrace], missing: int = 0) -> None:
        self._traces = list(traces)
        self._missing = missing
        self._redraw()

    def show_message(self, text: str) -> None:
        self._traces = []
        self._missing = 0
        self._message.setText(text)
        self._hint.clear()
        self._stack.setCurrentWidget(self._message)

    def _build(self) -> None:
        self._extract = QPushButton("Extract")
        self._extract.setEnabled(False)
        self._extract.setToolTip(
            "Decode every clip in this campaign that is not already cached."
        )
        self._extract.clicked.connect(self.extract_requested.emit)

        self._align = QCheckBox("Align on rising edge")
        self._align.setToolTip(
            "Plot t − t1. Drag the green line on one clip, with this off, to correct t1."
        )
        self._align.toggled.connect(self._redraw)
        self._normalize = QCheckBox("Normalize")
        self._normalize.setToolTip("Divide each trace by its own maximum.")
        self._normalize.toggled.connect(self._redraw)

        controls = QHBoxLayout()
        controls.addWidget(self._extract)
        controls.addWidget(self._align)
        controls.addWidget(self._normalize)
        controls.addStretch(1)

        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        self._progress.setVisible(False)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: palette(mid);")

        self._message = QLabel("Select a clip to plot its chronograph.")
        self._message.setWordWrap(True)
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setMargin(16)

        self._plot = pg.PlotWidget()
        self._plot.setBackground("w")
        self._plot.showGrid(x=True, y=True, alpha=0.25)
        self._plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._plot.setMinimumHeight(180)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._message)
        self._stack.addWidget(self._plot)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(controls)
        layout.addWidget(self._progress)
        layout.addWidget(self._hint)
        layout.addWidget(self._stack, stretch=1)

    def _sync_extract_button(self) -> None:
        if self._busy:
            self._extract.setText("Extracting…")
            self._extract.setEnabled(False)
            return
        if not self._campaign:
            self._extract.setText("Extract")
            self._extract.setEnabled(False)
            return
        self._extract.setText(f"Extract {self._campaign}")
        self._extract.setEnabled(True)

    def _redraw(self, *_args: object) -> None:
        align = self._align.isChecked()
        normalize = self._normalize.isChecked()
        plotted: list[tuple[ChronoTrace, np.ndarray, np.ndarray]] = []
        skipped_edge = 0
        for trace in self._traces:
            if align and trace.t1_s is None:
                skipped_edge += 1
                continue
            time_s = trace.time_s
            intensity = trace.intensity
            if align and trace.t1_s is not None:
                time_s = time_s - trace.t1_s
            if normalize:
                peak = float(np.max(intensity)) if intensity.size else 0.0
                if peak > 0:
                    intensity = intensity / peak
            plotted.append((trace, time_s, intensity))

        hints: list[str] = []
        if self._missing:
            hints.append(
                f"{self._missing} selected clip(s) have no chronograph yet."
            )
        if skipped_edge:
            hints.append(f"{skipped_edge} clip(s) have no rising edge to align on.")
        self._hint.setText(" ".join(hints))

        if not plotted:
            if self._traces and align and skipped_edge:
                self._message.setText("None of the selected clips have a rising edge yet.")
            elif self._missing and not self._traces:
                self._message.setText(
                    "No chronograph for this selection. Extract the campaign first."
                )
            else:
                self._message.setText("Select a clip to plot its chronograph.")
            self._stack.setCurrentWidget(self._message)
            return

        self._plot.clear()
        self._edge = None
        legend = self._plot.plotItem.legend
        if len(plotted) > 1:
            if legend is None:
                self._plot.addLegend()
            else:
                legend.clear()
        elif legend is not None:
            legend.clear()
        self._plot.setLabel("bottom", "t − t1 [s]" if align else "t [s]")
        self._plot.setLabel("left", "intensity / max" if normalize else "intensity")
        for index, (trace, time_s, intensity) in enumerate(plotted):
            color = _COLORS[index % len(_COLORS)]
            self._plot.plot(
                time_s,
                intensity,
                pen=pg.mkPen(color, width=1.5),
                name=trace.label,
            )

        t1_value = plotted[0][0].t1_s if len(plotted) == 1 else None
        if len(plotted) == 1 and not align and t1_value is not None:
            t1 = float(t1_value)
            self._edge = pg.InfiniteLine(
                pos=t1,
                angle=90,
                movable=True,
                pen=pg.mkPen(_EDGE, width=2),
                label="t1",
                labelOpts={"position": 0.95, "color": _EDGE},
            )
            self._edge.sigPositionChangeFinished.connect(self._on_edge_finished)
            self._plot.addItem(self._edge)
        self._stack.setCurrentWidget(self._plot)

    def _on_edge_finished(self, *_args: object) -> None:
        if self._edge is None or len(self._traces) != 1:
            return
        trace = self._traces[0]
        if trace.time_s.size == 0:
            return
        t1 = float(self._edge.value())
        lo = float(trace.time_s[0])
        hi = float(trace.time_s[-1])
        t1 = min(max(t1, lo), hi)
        self._edge.blockSignals(True)
        self._edge.setValue(t1)
        self._edge.blockSignals(False)
        trace.t1_s = t1
        self.edge_moved.emit(t1)
