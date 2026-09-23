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
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lib.paths import CAMPAIGN_SPARK_GAP

from ..campaign_import import load_campaign_module
from ..rootbridge import RootBridge
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
        self._list.itemChanged.connect(self._redraw)

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
        self._list.clear()
        self._canvas.clear("Detect events to plot metrics.")

    def set_pdf_default(self, path: Path) -> None:
        self._canvas.set_pdf_default(path)

    def set_payload(self, events: list[dict[str, Any]], detection: dict[str, Any]) -> None:
        self._events = list(events)
        self._detection = dict(detection or {})
        selected = {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self._checked_items()
        }
        if not selected:
            selected = set(_fig.DEFAULT_METRICS)
        self._updating = True
        self._list.blockSignals(True)
        self._list.clear()
        for field in _fig.metric_catalog(self._events):
            item = QListWidgetItem(f"{field['label']}  ({field['unit']})")
            item.setData(Qt.ItemDataRole.UserRole, field["name"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = field["name"] in selected
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._updating = False
        self._redraw()

    def _checked_items(self) -> list[QListWidgetItem]:
        items = []
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                items.append(item)
        return items

    def _redraw(self, _checked: bool = False) -> None:
        if self._updating:
            return
        if not self._events:
            self._canvas.clear("Detect events to plot metrics.")
            return
        names = [item.data(Qt.ItemDataRole.UserRole) for item in self._checked_items()]
        if not names:
            self._canvas.clear("Select at least one metric.")
            return
        spec = _fig.metric_figure_spec(
            self._events,
            self._detection,
            names=names,
            mode="histogram" if self._hist.isChecked() else "sequence",
            include_first=self._all.isChecked(),
        )
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


class ComposePane(QWidget):
    """Compare the same metric figure across several saved measurements."""

    refresh_requested = Signal()

    def __init__(self, bridge: RootBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._loader = None
        self._records: list[Any] = []
        self._live: dict[str, Any] | None = None
        self._updating = False

        self._meas = QListWidget()
        self._meas.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._meas.itemChanged.connect(self._redraw)

        all_btn = QPushButton("All analyzed")
        all_btn.clicked.connect(self._select_analyzed)
        none_btn = QPushButton("None")
        none_btn.clicked.connect(self._select_none)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_requested.emit)
        picks = QHBoxLayout()
        picks.addWidget(all_btn)
        picks.addWidget(none_btn)
        picks.addWidget(refresh_btn)

        self._seq = QRadioButton("Sequence")
        self._hist = QRadioButton("Histogram")
        self._hist.setChecked(True)
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
        controls.addWidget(self._typical)
        controls.addWidget(self._all)
        controls.addWidget(QLabel("Metrics"))
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
        self._fill_metrics([])

    def set_event_loader(self, loader) -> None:
        self._loader = loader

    def shutdown(self) -> None:
        self._canvas.shutdown()

    def clear(self) -> None:
        self._live = None
        self._redraw()

    def set_pdf_default(self, path: Path) -> None:
        self._canvas.set_pdf_default(path)

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
            if payload is None:
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
        self._redraw()

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
        stem = str(getattr(record, "stem", ""))
        live = self._live
        if live and live.get("stem") == stem and live.get("events"):
            return live
        if self._loader is None:
            return None
        path = getattr(record, "path", None)
        if path is None:
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

    def _redraw(self, _checked: bool = False) -> None:
        if self._updating:
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
        limit = int(_fig.compose_source_limit(len(names)))
        truncated = len(sources) > limit
        sources = sources[:limit]
        if truncated:
            self._note.setText(
                f"Showing the first {limit} measurements ({_fig.COMPOSE_MAX_PADS}-pad limit)."
            )
        else:
            self._note.setText("")
        spec = _fig.compose_figure_spec(
            sources,
            names=names,
            mode="histogram" if self._hist.isChecked() else "sequence",
            include_first=self._all.isChecked(),
        )
        self._canvas.set_spec(spec)


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
