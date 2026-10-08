"""Interactive spark-gap metric and overlay panes on a ROOT canvas."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lib.paths import CAMPAIGN_SPARK_GAP
from lib.waveform import DEFAULT_MAX_POINTS, decimate_minmax, load_waveform

from ..campaign_import import load_campaign_module
from ..rootbridge import RootBridge
from ..workers import WorkerHandle
from .root_gallery import RootCanvas, RootGallery

_sg = load_campaign_module(CAMPAIGN_SPARK_GAP, "spark_gap")
sys.modules.setdefault("spark_gap", _sg)
_fig = load_campaign_module(CAMPAIGN_SPARK_GAP, "spark_gap_figures")

_KIND_DISCHARGE = "discharge"
_KIND_RAMP = "ramp"
_KIND_POST = "post"


class MetricsPane(QWidget):
    def __init__(self, bridge: RootBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._events: list[dict[str, Any]] = []
        self._detection: dict[str, Any] = {}
        self._updating = False
        self._chosen: set[str] | None = None
        self._pool_events: list[dict[str, Any]] | None = None
        self._pool_detection: dict[str, Any] = {}
        self._pool_title = ""
        self._pool_clear_footer = False
        self._pool_missing: list[str] = []

        self._seq = QRadioButton("Sequence")
        self._hist = QRadioButton("Histogram")
        self._seq.setChecked(True)
        mode = QButtonGroup(self)
        mode.addButton(self._seq)
        mode.addButton(self._hist)
        self._seq.toggled.connect(self._redraw)

        self._typical = QRadioButton("Typical")
        self._all = QRadioButton("All events")
        self._typical.setChecked(True)
        population = QButtonGroup(self)
        population.addButton(self._typical)
        population.addButton(self._all)
        self._typical.toggled.connect(self._redraw)

        self._list = QListWidget()
        self._list.itemChanged.connect(self._on_metric_toggled)

        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color: palette(mid);")

        self._canvas = RootCanvas(bridge, self, empty="Detect events to plot metrics.")

        controls = QVBoxLayout()
        controls.addWidget(QLabel("Mode"))
        controls.addWidget(self._seq)
        controls.addWidget(self._hist)
        controls.addWidget(QLabel("Population"))
        controls.addWidget(self._typical)
        controls.addWidget(self._all)
        controls.addWidget(QLabel("Metrics"))
        controls.addWidget(self._list, stretch=1)
        controls.addWidget(self._note)

        side = QWidget()
        side.setMinimumWidth(200)
        side.setMaximumWidth(260)
        side.setLayout(controls)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(side)
        layout.addWidget(self._canvas, stretch=1)

    def shutdown(self) -> None:
        self._canvas.shutdown()

    def clear(self) -> None:
        self._events = []
        self._detection = {}
        self._redraw()

    def clear_pool(self) -> None:
        if self._pool_events is None and not self._pool_missing:
            return
        self._pool_events = None
        self._pool_detection = {}
        self._pool_title = ""
        self._pool_clear_footer = False
        self._pool_missing = []
        self._note.setText("")
        if self._events:
            self._fill_metric_list(self._events)
        self._redraw()

    def set_pool(
        self,
        events: list[dict[str, Any]] | None,
        detection: dict[str, Any] | None = None,
        *,
        title_suffix: str = "",
        clear_footer: bool = False,
        missing: list[str] | None = None,
    ) -> None:
        """Histogram mode draws this pooled list. Sequence stays on the opened capture."""
        self._pool_events = None if events is None else list(events)
        self._pool_detection = dict(detection or {})
        self._pool_title = title_suffix
        self._pool_clear_footer = clear_footer
        self._pool_missing = list(missing or [])
        if self._pool_events:
            self._fill_metric_list(self._pool_events)
        self._redraw()

    def set_pdf_default(self, path: Path) -> None:
        self._canvas.set_pdf_default(path)

    def set_payload(self, events: list[dict[str, Any]], detection: dict[str, Any]) -> None:
        self._events = list(events)
        self._detection = dict(detection or {})
        self._fill_metric_list(self._events)
        self._redraw()

    def _fill_metric_list(self, events: list[dict[str, Any]]) -> None:
        selected = set(_fig.DEFAULT_METRICS) if self._chosen is None else set(self._chosen)
        self._updating = True
        self._list.blockSignals(True)
        self._list.clear()
        for field in _fig.metric_catalog(events):
            item = QListWidgetItem(f"{field['label']}  ({field['unit']})")
            item.setData(Qt.ItemDataRole.UserRole, field["name"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = field["name"] in selected
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._updating = False

    def _checked_items(self) -> list[QListWidgetItem]:
        items = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                items.append(item)
        return items

    def _on_metric_toggled(self, _item: QListWidgetItem) -> None:
        if self._updating:
            return
        visible = {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self._list_items()
        }
        checked = {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self._checked_items()
        }
        if self._chosen is None:
            self._chosen = set(checked)
        else:
            self._chosen = (self._chosen - visible) | checked
        self._redraw()

    def _list_items(self) -> list[QListWidgetItem]:
        items = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is not None:
                items.append(item)
        return items

    def _redraw(self, _checked: bool = False) -> None:
        if self._updating:
            return
        pooling = self._hist.isChecked() and self._pool_events is not None
        if pooling and not self._pool_events:
            names = ", ".join(self._pool_missing) or "the selection"
            self._note.setText("")
            self._canvas.clear(f"No saved analysis for {names}.")
            return
        events = self._pool_events if pooling else self._events
        detection = self._pool_detection if pooling else self._detection
        if not events:
            self._note.setText("")
            self._canvas.clear("Detect events to plot metrics.")
            return
        names = [item.data(Qt.ItemDataRole.UserRole) for item in self._checked_items()]
        if not names:
            self._note.setText("")
            self._canvas.clear("Select at least one metric.")
            return
        spec = _fig.metric_figure_spec(
            events,
            detection,
            names=names,
            mode="histogram" if self._hist.isChecked() else "sequence",
            include_first=self._all.isChecked(),
        )
        if pooling:
            if self._pool_title:
                title = str(spec.get("title") or "")
                spec["title"] = f"{title} — {self._pool_title}" if title else self._pool_title
            if self._pool_clear_footer:
                spec["footer"] = ""
            self._note.setText(
                "No analysis: " + ", ".join(self._pool_missing) if self._pool_missing else ""
            )
        else:
            self._note.setText("")
        self._canvas.set_spec(spec)


class OverlayPane(QWidget):
    def __init__(self, bridge: RootBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._events: list[dict[str, Any]] = []
        self._snippets: list[dict[str, Any]] = []
        self._detection: dict[str, Any] = {}
        self._updating = False

        self._kind = QComboBox()
        self._kind.addItem("Discharge", _KIND_DISCHARGE)
        self._kind.addItem("Charging ramp", _KIND_RAMP)
        self._kind.addItem("Post-collapse", _KIND_POST)
        self._kind.currentIndexChanged.connect(self._on_kind)

        self._guides = QCheckBox("10/90 guides")
        self._guides.setChecked(True)
        self._guides.toggled.connect(self._redraw)
        self._fit = QCheckBox("Linear fit")
        self._fit.setChecked(True)
        self._fit.toggled.connect(self._redraw)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._list.itemChanged.connect(self._redraw)

        typical = QPushButton("Typical")
        typical.clicked.connect(self._select_typical)
        reps = QPushButton("Representatives")
        reps.clicked.connect(self._select_reps)
        all_btn = QPushButton("All")
        all_btn.clicked.connect(self._select_all)
        none_btn = QPushButton("None")
        none_btn.clicked.connect(self._select_none)
        picks = QHBoxLayout()
        picks.addWidget(typical)
        picks.addWidget(reps)
        picks.addWidget(all_btn)
        picks.addWidget(none_btn)

        self._canvas = RootCanvas(
            bridge, self, empty="Detect events to overlay waveforms."
        )

        controls = QVBoxLayout()
        controls.addWidget(QLabel("Waveform"))
        controls.addWidget(self._kind)
        controls.addWidget(self._guides)
        controls.addWidget(self._fit)
        controls.addWidget(QLabel("Events"))
        controls.addLayout(picks)
        controls.addWidget(self._list, stretch=1)

        side = QWidget()
        side.setMinimumWidth(240)
        side.setMaximumWidth(320)
        side.setLayout(controls)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(side)
        layout.addWidget(self._canvas, stretch=1)
        self._sync_kind_options()

    def shutdown(self) -> None:
        self._canvas.shutdown()

    def clear(self) -> None:
        self._events = []
        self._snippets = []
        self._detection = {}
        self._list.clear()
        self._canvas.clear("Detect events to overlay waveforms.")

    def set_pdf_default(self, path: Path) -> None:
        self._canvas.set_pdf_default(path)

    def set_payload(
        self,
        events: list[dict[str, Any]],
        snippets: list[dict[str, Any]],
        detection: dict[str, Any],
    ) -> None:
        self._events = list(events)
        self._snippets = list(snippets)
        self._detection = dict(detection or {})
        selected = set(self._checked_indexes())
        if not selected:
            selected = set(_fig.representative_indexes(self._events))
        self._updating = True
        self._list.blockSignals(True)
        self._list.clear()
        for row in self._events:
            ident = int(row["event_index"])
            v_bd = row.get("v_breakdown")
            text = f"#{ident}"
            if v_bd is not None:
                text = f"#{ident}  {float(v_bd) / 1000.0:.2f} kV"
            if row.get("first_cycle"):
                text += "  first"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, ident)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if ident in selected else Qt.CheckState.Unchecked
            )
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._updating = False
        self._redraw()

    def _on_kind(self, _index: int) -> None:
        self._sync_kind_options()
        self._redraw()

    def _sync_kind_options(self) -> None:
        kind = self._kind.currentData()
        self._guides.setVisible(kind == _KIND_DISCHARGE)
        self._fit.setVisible(kind == _KIND_RAMP)

    def _checked_indexes(self) -> list[int]:
        chosen = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                chosen.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return chosen

    def _set_checked(self, indexes: set[int]) -> None:
        self._updating = True
        self._list.blockSignals(True)
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is None:
                continue
            ident = int(item.data(Qt.ItemDataRole.UserRole))
            item.setCheckState(
                Qt.CheckState.Checked if ident in indexes else Qt.CheckState.Unchecked
            )
        self._list.blockSignals(False)
        self._updating = False
        self._redraw()

    def _select_typical(self) -> None:
        self._set_checked(set(_fig.typical_event_indexes(self._events)))

    def _select_reps(self) -> None:
        self._set_checked(set(_fig.representative_indexes(self._events)))

    def _select_all(self) -> None:
        self._set_checked({int(row["event_index"]) for row in self._events})

    def _select_none(self) -> None:
        self._set_checked(set())

    def _redraw(self, _checked: bool = False) -> None:
        if self._updating:
            return
        if not self._events:
            self._canvas.clear("Detect events to overlay waveforms.")
            return
        if not self._snippets:
            self._canvas.clear("Detect events to load collapse and ramp waveforms.")
            return
        indexes = self._checked_indexes()
        if not indexes:
            self._canvas.clear("Select at least one event.")
            return
        kind = str(self._kind.currentData() or _KIND_DISCHARGE)
        kwargs = dict(
            events=self._events,
            snippets=self._snippets,
            detection=self._detection,
            kind=kind,
            event_indexes=indexes,
            show_guides=self._guides.isChecked(),
            show_fit=self._fit.isChecked(),
        )
        spec = _fig.overlay_figure_spec(**kwargs, max_points=_fig.OVERLAY_SCREEN_POINTS)
        export = _fig.overlay_figure_spec(**kwargs, max_points=None)
        self._canvas.set_spec(spec, export_spec=export)


def _prepare_waveforms(jobs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Load and decimate full traces. Time starts at the first sample of each record."""
    prepared: list[dict[str, Any]] = []
    for path_s, _label in jobs:
        try:
            time_s, voltage_v, _meta = load_waveform(Path(path_s))
            count = min(len(time_s), len(voltage_v))
            t = time_s[:count]
            v = voltage_v[:count]
            if count:
                t = t - float(t[0])
            screen_t, screen_v = decimate_minmax(t, v, _fig.WAVEFORM_SCREEN_POINTS)
            export_t, export_v = decimate_minmax(t, v, DEFAULT_MAX_POINTS)
            prepared.append(
                {
                    "path": path_s,
                    "screen_t": screen_t.tolist(),
                    "screen_v": screen_v.tolist(),
                    "export_t": export_t.tolist(),
                    "export_v": export_v.tolist(),
                }
            )
        except Exception as exc:  # noqa: BLE001 — one bad file should not drop the rest
            prepared.append({"path": path_s, "error": f"{type(exc).__name__}: {exc}"})
    return prepared


def _measurement_label(record: Any) -> str:
    return str(getattr(record, "run_name", None) or getattr(record, "stem", "measurement"))


def _default_set_name(records: list[Any]) -> str:
    labels = [_measurement_label(record) for record in records]
    text = " + ".join(labels)
    if len(text) <= 48:
        return text
    if len(labels) <= 2:
        return text[:45] + "..."
    short = f"{' + '.join(labels[:2])} + {len(labels) - 2} more"
    return short if len(short) <= 48 else short[:45] + "..."


def _unique_set_name(base: str, groups: list[dict[str, Any]]) -> str:
    names = {str(group.get("name") or "") for group in groups}
    if base not in names:
        return base
    index = 2
    while f"{base} ({index})" in names:
        index += 1
    return f"{base} ({index})"


def _same_scope(detections: list[dict[str, Any]]) -> bool:
    if len(detections) <= 1:
        return True

    def key(detection: dict[str, Any]) -> tuple[Any, Any]:
        return (detection.get("scope_bw_hz"), detection.get("sample_rate_hz"))

    first = key(detections[0])
    return all(key(detection) == first for detection in detections[1:])


class ComposePane(QWidget):
    """Compare metric figures or full waveforms across saved measurements."""

    refresh_requested = Signal()

    def __init__(self, bridge: RootBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._loader = None
        self._records: list[Any] = []
        self._catalogue: list[Any] = []
        self._sets: list[dict[str, Any]] = []
        self._next_set_id = 0
        self._live: dict[str, Any] | None = None
        self._updating = False
        self._wave_cache: dict[str, dict[str, Any]] = {}
        self._wave_worker = WorkerHandle(self)
        self._wave_epoch = 0

        self._meas = QListWidget()
        self._meas.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._meas.itemChanged.connect(self._redraw)

        self._all_btn = QPushButton("All analyzed")
        self._all_btn.clicked.connect(self._select_analyzed)
        none_btn = QPushButton("None")
        none_btn.clicked.connect(self._select_none)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        picks = QHBoxLayout()
        picks.addWidget(self._all_btn)
        picks.addWidget(none_btn)
        picks.addWidget(refresh_btn)

        self._seq = QRadioButton("Sequence")
        self._hist = QRadioButton("Histogram")
        self._wave = QRadioButton("Waveform")
        self._hist.setChecked(True)
        mode = QButtonGroup(self)
        mode.addButton(self._seq)
        mode.addButton(self._hist)
        mode.addButton(self._wave)
        for button in (self._seq, self._hist, self._wave):
            button.setAutoExclusive(False)
        self._hist.toggled.connect(self._on_figure_mode)
        self._seq.toggled.connect(self._on_figure_mode)
        self._wave.toggled.connect(self._on_figure_mode)

        self._layout_label = QLabel("Layout")
        self._overlay_layout = QRadioButton("Overlay")
        self._stack = QRadioButton("Stacked")
        self._sets_layout = QRadioButton("Sets")
        self._sets_layout.setToolTip(
            "One histogram per pinned set. Each set pools the events of its measurements."
        )
        self._overlay_layout.setChecked(True)
        layout_mode = QButtonGroup(self)
        layout_mode.addButton(self._overlay_layout)
        layout_mode.addButton(self._stack)
        layout_mode.addButton(self._sets_layout)
        self._overlay_layout.setAutoExclusive(False)
        self._stack.setAutoExclusive(False)
        self._sets_layout.setAutoExclusive(False)
        self._overlay_layout.toggled.connect(self._on_layout)
        self._stack.toggled.connect(self._on_layout)
        self._sets_layout.toggled.connect(self._on_layout)

        self._add_set_btn = QPushButton("Add set")
        self._add_set_btn.clicked.connect(self._add_set)
        self._add_set_btn.setEnabled(False)
        self._add_set_btn.setToolTip("Select one or more analyzed measurements")

        self._set_rows = QWidget()
        self._set_rows_layout = QVBoxLayout(self._set_rows)
        self._set_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._set_rows_layout.setSpacing(4)
        self._set_scroll = QScrollArea()
        self._set_scroll.setWidgetResizable(True)
        self._set_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._set_scroll.setWidget(self._set_rows)
        self._set_scroll.setVisible(False)
        self._set_scroll.setMaximumHeight(140)

        self._normalize = QCheckBox("Normalize to max")
        self._normalize.setToolTip(
            "Scale each histogram so its tallest bin is 1 (histogram overlays and sets)"
        )
        self._normalize.toggled.connect(self._redraw)

        self._columns_label = QLabel("Columns")
        self._columns = QSpinBox()
        self._columns.setRange(1, int(_fig.COMPOSE_MAX_PADS))
        self._columns.setValue(2)
        self._columns.setToolTip(
            "Subplot columns for a stack of one metric. "
            "Four measurements and 2 columns is a 2×2 grid."
        )
        self._columns.valueChanged.connect(self._on_columns)

        self._population_label = QLabel("Population")
        self._typical = QRadioButton("Typical")
        self._all = QRadioButton("All events")
        self._typical.setChecked(True)
        population = QButtonGroup(self)
        population.addButton(self._typical)
        population.addButton(self._all)
        self._typical.setAutoExclusive(False)
        self._all.setAutoExclusive(False)
        self._typical.toggled.connect(self._redraw)

        self._metrics_label = QLabel("Metrics")
        self._metrics = QListWidget()
        self._metrics.itemChanged.connect(self._redraw)

        self._note = QLabel("")
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color: palette(mid);")

        self._canvas = RootCanvas(
            bridge, self, empty="Select measurements and a metric."
        )

        controls = QVBoxLayout()
        controls.addWidget(QLabel("Measurements"))
        controls.addLayout(picks)
        controls.addWidget(self._meas, stretch=1)
        controls.addWidget(QLabel("Figure"))
        controls.addWidget(self._hist)
        controls.addWidget(self._seq)
        controls.addWidget(self._wave)
        controls.addWidget(self._layout_label)
        controls.addWidget(self._overlay_layout)
        controls.addWidget(self._stack)
        controls.addWidget(self._sets_layout)
        controls.addWidget(self._add_set_btn)
        controls.addWidget(self._set_scroll)
        controls.addWidget(self._normalize)
        controls.addWidget(self._columns_label)
        controls.addWidget(self._columns)
        controls.addWidget(self._population_label)
        controls.addWidget(self._typical)
        controls.addWidget(self._all)
        controls.addWidget(self._metrics_label)
        controls.addWidget(self._metrics, stretch=1)
        controls.addWidget(self._note)

        side = QWidget()
        side.setMinimumWidth(240)
        side.setMaximumWidth(320)
        side.setLayout(controls)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(side)
        layout.addWidget(self._canvas, stretch=1)
        self._apply_mode_visibility()
        self._fill_metrics([])

    def set_event_loader(self, loader) -> None:
        self._loader = loader

    def shutdown(self) -> None:
        self._wave_worker.cancel()
        self._canvas.shutdown()

    def clear(self) -> None:
        self._live = None
        self._redraw()

    def set_pdf_default(self, path: Path) -> None:
        self._canvas.set_pdf_default(path)

    def set_catalogue_selection(self, records: list[Any]) -> None:
        """Command-clicked captures. Add set pins the ones that have events."""
        self._catalogue = list(records)
        self._sync_add_set_button()

    def set_sources(self, records: list[Any], live: dict[str, Any] | None = None) -> None:
        self._records = list(records)
        self._live = dict(live) if live else None
        selected = set(self._checked_stems())
        self._updating = True
        self._meas.blockSignals(True)
        self._meas.clear()
        for record in self._records:
            stem = str(getattr(record, "stem", ""))
            label = str(getattr(record, "run_name", None) or stem)
            payload = self._payload_for(record)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, stem)
            item.setData(Qt.ItemDataRole.UserRole + 1, str(getattr(record, "path", "")))
            if payload is None and not self._wave.isChecked():
                item.setText(f"{label}  (no analysis)")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if stem in selected else Qt.CheckState.Unchecked
                )
            self._meas.addItem(item)
        self._meas.blockSignals(False)
        self._updating = False
        events = []
        for record in self._records:
            payload = self._payload_for(record)
            if payload:
                events.extend(payload.get("events") or [])
        self._fill_metrics(events)
        self._apply_mode_visibility()
        self._sync_add_set_button()
        self._redraw()

    def _apply_mode_visibility(self) -> None:
        wave = self._wave.isChecked()
        for widget in (
            self._population_label,
            self._typical,
            self._all,
            self._metrics_label,
            self._metrics,
        ):
            widget.setEnabled(not wave)
        multi_metric = (not wave) and len(self._checked_metric_items()) > 1
        sets = self._sets_layout.isChecked()
        grid = (self._stack.isChecked() and not multi_metric) or (
            (sets or self._overlay_layout.isChecked()) and multi_metric
        )
        if sets:
            grid = len(self._checked_metric_items()) > 1
        self._normalize.setEnabled(self._hist.isChecked() and not wave)
        self._columns_label.setEnabled(grid)
        self._columns.setEnabled(grid)
        self._all_btn.setText("All" if wave else "All analyzed")

    def _on_figure_mode(self, checked: bool) -> None:
        if self._updating:
            return
        # Sequence and waveform keep the per-measurement layout. The button
        # group can emit the new mode before the old one turns off, so key off
        # the sender rather than which radio is still checked.
        if (
            checked
            and self.sender() in (self._seq, self._wave)
            and self._sets_layout.isChecked()
        ):
            self._updating = True
            self._overlay_layout.setChecked(True)
            self._updating = False
        if checked and not self._wave.isChecked():
            self._wave_epoch += 1
            self._wave_worker.cancel()
        self._apply_mode_visibility()
        self._sync_row_flags()
        self._redraw()

    def _sync_row_flags(self) -> None:
        """Enable every capture for waveforms, and only analyzed ones otherwise."""
        wave = self._wave.isChecked()
        by_stem = {str(getattr(record, "stem", "")): record for record in self._records}
        self._updating = True
        self._meas.blockSignals(True)
        for row in range(self._meas.count()):
            item = self._meas.item(row)
            if item is None:
                continue
            stem = str(item.data(Qt.ItemDataRole.UserRole) or "")
            record = by_stem.get(stem)
            label = str(getattr(record, "run_name", None) or stem) if record is not None else stem
            payload = self._payload_for(record) if record is not None else None
            checked = item.checkState() == Qt.CheckState.Checked
            if payload is None and not wave:
                item.setText(f"{label}  (no analysis)")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setText(label)
                item.setFlags(
                    item.flags() | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
        self._meas.blockSignals(False)
        self._updating = False

    def _on_layout(self, checked: bool) -> None:
        if not checked or self._updating:
            return
        if self._sets_layout.isChecked() and not self._hist.isChecked():
            self._hist.setChecked(True)
            return
        self._redraw()

    def _on_columns(self, _value: int) -> None:
        if self._updating:
            return
        self._redraw()

    def _refresh(self) -> None:
        self._wave_cache.clear()
        self._wave_worker.cancel()
        self.refresh_requested.emit()

    def _fill_metrics(self, events: list[dict[str, Any]]) -> None:
        selected = {
            item.data(Qt.ItemDataRole.UserRole) for item in self._checked_metric_items()
        }
        if not selected:
            selected = set(_fig.COMPOSE_DEFAULT_METRICS)
        self._updating = True
        self._metrics.blockSignals(True)
        self._metrics.clear()
        for field in _fig.metric_catalog(events or None):
            item = QListWidgetItem(f"{field['label']}  ({field['unit']})")
            item.setData(Qt.ItemDataRole.UserRole, field["name"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if field["name"] in selected
                else Qt.CheckState.Unchecked
            )
            self._metrics.addItem(item)
        self._metrics.blockSignals(False)
        self._updating = False

    def _payload_for(self, record: Any) -> dict[str, Any] | None:
        if record is None:
            return None
        stem = str(getattr(record, "stem", ""))
        path = getattr(record, "path", None)
        live = self._live
        if live and live.get("events"):
            live_path = live.get("path")
            if (
                live_path
                and path is not None
                and Path(str(live_path)).resolve() == Path(str(path)).resolve()
            ):
                return live
            if not live_path and live.get("stem") == stem:
                return live
        if self._loader is None or path is None:
            return None
        return self._loader(Path(path))

    def _checked_stems(self) -> list[str]:
        stems = []
        for row in range(self._meas.count()):
            item = self._meas.item(row)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                stems.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return stems

    def _checked_metric_items(self) -> list[QListWidgetItem]:
        items = []
        for row in range(self._metrics.count()):
            item = self._metrics.item(row)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                items.append(item)
        return items

    def _select_analyzed(self) -> None:
        self._updating = True
        self._meas.blockSignals(True)
        for row in range(self._meas.count()):
            item = self._meas.item(row)
            if item is None:
                continue
            enabled = bool(item.flags() & Qt.ItemFlag.ItemIsEnabled)
            item.setCheckState(
                Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked
            )
        self._meas.blockSignals(False)
        self._updating = False
        self._redraw()

    def _select_none(self) -> None:
        self._updating = True
        self._meas.blockSignals(True)
        for row in range(self._meas.count()):
            item = self._meas.item(row)
            if item is not None:
                item.setCheckState(Qt.CheckState.Unchecked)
        self._meas.blockSignals(False)
        self._updating = False
        self._redraw()

    def _sync_add_set_button(self) -> None:
        count = len(self._analysed_catalogue())
        self._add_set_btn.setEnabled(count >= 1)
        self._add_set_btn.setToolTip(
            "Pin the selected measurement(s) as one set"
            if count >= 1
            else "Select one or more analyzed measurements"
        )

    def _analysed_catalogue(self) -> list[Any]:
        ready: list[Any] = []
        seen: set[Path] = set()
        for record in self._catalogue:
            path = getattr(record, "path", None)
            if path is None:
                continue
            key = Path(str(path)).resolve()
            if key in seen:
                continue
            seen.add(key)
            payload = self._payload_for(record)
            if payload and payload.get("events"):
                ready.append(record)
        return ready

    def _record_for_path(self, path_s: str) -> Any | None:
        target = Path(path_s).resolve()
        for record in (*self._records, *self._catalogue):
            path = getattr(record, "path", None)
            if path is not None and Path(str(path)).resolve() == target:
                return record
        return None

    def _find_set(self, set_id: int) -> dict[str, Any] | None:
        for group in self._sets:
            if group.get("id") == set_id:
                return group
        return None

    def _add_set(self) -> None:
        records = self._analysed_catalogue()
        if len(records) < 1:
            return
        self._next_set_id += 1
        self._sets.append(
            {
                "id": self._next_set_id,
                "name": _unique_set_name(_default_set_name(records), self._sets),
                "paths": [str(Path(str(record.path)).resolve()) for record in records],
            }
        )
        self._rebuild_set_rows()
        if not self._sets_layout.isChecked():
            self._sets_layout.setChecked(True)
            return
        self._redraw()

    def _rename_set(self, set_id: int, name: str) -> None:
        group = self._find_set(set_id)
        if group is None:
            return
        cleaned = name.strip()
        if not cleaned or cleaned == group.get("name"):
            if not cleaned:
                self._rebuild_set_rows()
            return
        group["name"] = cleaned
        if self._sets_layout.isChecked():
            self._redraw()

    def _remove_set(self, set_id: int) -> None:
        self._sets = [group for group in self._sets if group.get("id") != set_id]
        self._rebuild_set_rows()
        self._redraw()

    def _rebuild_set_rows(self) -> None:
        while self._set_rows_layout.count():
            item = self._set_rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for group in self._sets:
            self._set_rows_layout.addWidget(self._make_set_row(group))
        self._set_scroll.setVisible(bool(self._sets))

    def _make_set_row(self, group: dict[str, Any]) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        name = QLineEdit(str(group.get("name") or ""))
        set_id = int(group["id"])
        name.editingFinished.connect(lambda gid=set_id, box=name: self._rename_set(gid, box.text()))
        remove = QPushButton("Remove")
        remove.clicked.connect(lambda _checked=False, gid=set_id: self._remove_set(gid))
        layout.addWidget(name, stretch=1)
        layout.addWidget(remove)
        return row

    def _set_members(
        self, group: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        events: list[dict[str, Any]] = []
        detections: list[dict[str, Any]] = []
        missing: list[str] = []
        for path_s in list(group.get("paths") or []):
            record = self._record_for_path(str(path_s))
            label = (
                _measurement_label(record) if record is not None else Path(str(path_s)).stem
            )
            payload = self._payload_for(record) if record is not None else None
            if payload is None and self._loader is not None:
                payload = self._loader(Path(str(path_s)))
            rows = list(payload.get("events") or []) if payload else []
            if not rows:
                missing.append(label)
                continue
            events.extend(rows)
            detection = payload.get("detection") if isinstance(payload, dict) else None
            if isinstance(detection, dict):
                detections.append(detection)
        return events, detections, missing

    def _redraw_sets(self) -> None:
        if not self._sets:
            self._note.setText("")
            self._canvas.clear("Select measurements, then Add set.")
            return
        names = [item.data(Qt.ItemDataRole.UserRole) for item in self._checked_metric_items()]
        if not names:
            self._note.setText("")
            self._canvas.clear("Select at least one metric.")
            return
        sources: list[dict[str, Any]] = []
        notes: list[str] = []
        detections: list[dict[str, Any]] = []
        for group in self._sets:
            label = str(group.get("name") or "Set")
            events, member_detections, missing = self._set_members(group)
            if missing:
                notes.append(f"{label} omitted {', '.join(missing)}")
            if not events:
                continue
            sources.append(
                {
                    "label": label,
                    "events": events,
                    "detection": member_detections[0] if member_detections else {},
                }
            )
            detections.extend(member_detections)
        if not sources:
            self._note.setText("; ".join(notes))
            self._canvas.clear("No saved analysis for these sets.")
            return
        limit = int(_fig.COMPOSE_MAX_PADS)
        truncated = len(sources) > limit
        sources = sources[:limit]
        parts = [part for part in (
            f"Showing the first {limit} sets." if truncated else "",
            "; ".join(notes),
        ) if part]
        self._note.setText(" ".join(parts))
        spec = _fig.compose_figure_spec(
            sources,
            names=names,
            mode="histogram",
            include_first=self._all.isChecked(),
            layout="overlay",
            cols=int(self._columns.value()),
            normalize=self._normalize.isChecked(),
        )
        if not _same_scope(detections):
            spec["footer"] = ""
        self._canvas.set_spec(spec)

    def _redraw(self, _checked: bool = False) -> None:
        if self._updating:
            return
        self._apply_mode_visibility()
        if (
            self._sets_layout.isChecked()
            and self._hist.isChecked()
            and not self._seq.isChecked()
            and not self._wave.isChecked()
        ):
            self._redraw_sets()
            return
        if self._wave.isChecked():
            self._redraw_waveforms()
            return
        names = [item.data(Qt.ItemDataRole.UserRole) for item in self._checked_metric_items()]
        sources = []
        by_stem = {str(getattr(record, "stem", "")): record for record in self._records}
        for stem in self._checked_stems():
            record = by_stem.get(stem)
            if record is None:
                continue
            payload = self._payload_for(record)
            if payload is None:
                continue
            sources.append(
                {
                    "label": str(getattr(record, "run_name", None) or stem),
                    "events": payload.get("events") or [],
                    "detection": payload.get("detection") or {},
                }
            )
        if not sources:
            self._note.setText("")
            self._canvas.clear("Select measurements and a metric.")
            return
        if not names:
            self._note.setText("")
            self._canvas.clear("Select at least one metric.")
            return
        stacked = self._stack.isChecked()
        multi = len(names) > 1
        if stacked and multi:
            limit = int(_fig.compose_source_limit(len(names)))
            limit_note = (
                f"Showing the first {limit} measurements "
                f"({_fig.COMPOSE_MAX_PADS}-pad limit)."
            )
        else:
            limit = int(_fig.COMPOSE_MAX_PADS)
            limit_note = f"Showing the first {limit} measurements."
        truncated = len(sources) > limit
        sources = sources[:limit]
        self._note.setText(limit_note if truncated else "")
        spec = _fig.compose_figure_spec(
            sources,
            names=names,
            mode="histogram" if self._hist.isChecked() else "sequence",
            include_first=self._all.isChecked(),
            layout="stacked" if stacked else "overlay",
            cols=int(self._columns.value()) if stacked != multi else None,
            normalize=self._normalize.isChecked() and self._hist.isChecked(),
        )
        self._canvas.set_spec(spec)

    def _checked_waveforms(self) -> list[dict[str, str]]:
        by_stem = {str(getattr(record, "stem", "")): record for record in self._records}
        rows = []
        for stem in self._checked_stems():
            record = by_stem.get(stem)
            if record is None or getattr(record, "path", None) is None:
                continue
            rows.append(
                {
                    "path": str(record.path),
                    "label": str(getattr(record, "run_name", None) or stem),
                }
            )
        return rows

    def _redraw_waveforms(self) -> None:
        chosen = self._checked_waveforms()
        if not chosen:
            self._note.setText("")
            self._canvas.clear("Select measurements.")
            return
        limit = int(_fig.COMPOSE_MAX_PADS)
        truncated = len(chosen) > limit
        chosen = chosen[:limit]
        note = ""
        if truncated:
            note = (
                f"Showing the first {limit} measurements "
                f"({_fig.COMPOSE_MAX_PADS}-pad limit)."
            )
        missing = [row for row in chosen if row["path"] not in self._wave_cache]
        if missing:
            loading = "Loading waveforms…"
            self._note.setText(f"{note} {loading}".strip() if note else loading)
            if not any(row["path"] in self._wave_cache for row in chosen):
                self._canvas.clear(loading)
            epoch = self._wave_epoch
            self._wave_worker.start(
                _prepare_waveforms,
                [(row["path"], row["label"]) for row in missing],
                on_finished=lambda result, epoch=epoch: self._receive_waveforms(epoch, result),
                on_failed=lambda message, epoch=epoch: self._fail_waveforms(epoch, message),
            )
            return
        errors: list[str] = []
        screen: list[dict[str, Any]] = []
        export: list[dict[str, Any]] = []
        for row in chosen:
            cached = self._wave_cache[row["path"]]
            if cached.get("error"):
                errors.append(f"{row['label']}: {cached['error']}")
                continue
            screen.append(
                {
                    "label": row["label"],
                    "time_s": cached["screen_t"],
                    "voltage_v": cached["screen_v"],
                }
            )
            export.append(
                {
                    "label": row["label"],
                    "time_s": cached["export_t"],
                    "voltage_v": cached["export_v"],
                }
            )
        parts = [part for part in (note, "; ".join(errors)) if part]
        self._note.setText(" ".join(parts))
        if not screen:
            self._canvas.clear(errors[0] if errors else "Select measurements.")
            return
        layout_name = "stacked" if self._stack.isChecked() else "overlay"
        columns = int(self._columns.value())
        self._canvas.set_spec(
            _fig.compose_waveform_spec(screen, layout=layout_name, cols=columns),
            export_spec=_fig.compose_waveform_spec(export, layout=layout_name, cols=columns),
        )

    def _receive_waveforms(self, epoch: int, result: object) -> None:
        if not isinstance(result, list):
            return
        for item in result:
            if isinstance(item, dict) and item.get("path"):
                self._wave_cache[str(item["path"])] = item
        if epoch != self._wave_epoch or not self._wave.isChecked():
            return
        self._redraw()

    def _fail_waveforms(self, epoch: int, message: str) -> None:
        if epoch != self._wave_epoch or not self._wave.isChecked():
            return
        self._note.setText("")
        self._canvas.clear(message)


class SparkExplorer(QWidget):
    """Metrics, overlay, and the saved 02–08 figure pack."""

    def __init__(self, bridge: RootBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.metrics = MetricsPane(bridge, self)
        self.overlay = OverlayPane(bridge, self)
        self.gallery = RootGallery(bridge, self)
        self._tabs = QTabWidget()
        self._tabs.addTab(self.metrics, "Metrics")
        self._tabs.addTab(self.overlay, "Overlay")
        self._tabs.addTab(self.gallery, "Saved figures")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabs)

    def shutdown(self) -> None:
        self.metrics.shutdown()
        self.overlay.shutdown()
        self.gallery.shutdown()

    def clear(self) -> None:
        self.metrics.clear()
        self.overlay.clear()
        self.gallery.clear()

    def count(self) -> int:
        return self.gallery.count()

    def set_payload(
        self,
        events: list[dict[str, Any]],
        snippets: list[dict[str, Any]],
        detection: dict[str, Any],
    ) -> None:
        self.metrics.set_payload(events, detection)
        self.overlay.set_payload(events, snippets, detection)

    def set_saved(self, specs: list[dict] | None, pngs: list[tuple[str, Path]]) -> None:
        self.gallery.set_content(specs, pngs)

    def set_pdf_dir(self, out_dir: Path | None) -> None:
        if out_dir is None:
            return
        self.metrics.set_pdf_default(out_dir / "metrics.pdf")
        self.overlay.set_pdf_default(out_dir / "overlay.pdf")

    def set_histogram_pool(
        self,
        events: list[dict[str, Any]],
        detection: dict[str, Any],
        *,
        title_suffix: str = "",
        clear_footer: bool = False,
        missing: list[str] | None = None,
    ) -> None:
        self.metrics.set_pool(
            events,
            detection,
            title_suffix=title_suffix,
            clear_footer=clear_footer,
            missing=missing,
        )

    def clear_histogram_pool(self) -> None:
        self.metrics.clear_pool()
