"""Grouped, readable view of one X-123 capture's metadata.

Rows can be sent to the plot legend from the right-click menu. A legend field
is identified by its flat key (``live_time_s``, ``calibration.slope_kev_per_channel``)
so the plot can look the same value up on every overlaid trace.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
)

# key -> (label, unit suffix, format spec). Unlisted keys fall under "Other".
_FIELDS: dict[str, tuple[str, str, str]] = {
    "run_name": ("Run", "", ""),
    "phase": ("Phase", "", ""),
    "captured_at": ("Captured", "", ""),
    "description": ("Description", "", ""),
    "live_time_s": ("Live time", " s", ".2f"),
    "real_time_s": ("Real time", " s", ".2f"),
    "dead_time_pct": ("Dead time", " %", ".2f"),
    "fast_count": ("Fast count", "", ",d"),
    "slow_count": ("Slow count", "", ",d"),
    "gp_count": ("GP count", "", ",d"),
    "points": ("Channels", "", ",d"),
    "calibration.offset_kev": ("Offset", " keV", ".4f"),
    "calibration.slope_kev_per_channel": ("Slope", " keV/ch", ".5f"),
    "calibration.channel_origin": ("Channel origin", "", "d"),
    "calibration.source": ("Source", "", ""),
    "model_id": ("Model", "", ""),
    "device_type": ("Device", "", ""),
    "serial_number": ("Serial", "", ""),
    "firmware": ("Firmware", "", ""),
    "fpga": ("FPGA", "", ""),
    "gain": ("Gain", "", ""),
    "hv_volt": ("High voltage", "", ""),
    "tec_temp": ("TEC temp", "", ""),
    "board_temp": ("Board temp", "", ""),
    "source_format": ("Format", "", ""),
    "dp5_tpea": ("Peaking time", " µs", "g"),
    "dp5_gain": ("Total gain", "", "g"),
    "dp5_mcac": ("MCA channels", "", "d"),
    "dp5_hvse": ("HV setting", " V", "g"),
    "dp5_tecs": ("TEC setting", " K", "g"),
    "dp5_ainp": ("Input polarity", "", ""),
    "dp5_clck": ("Clock", " MHz", "g"),
}

_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Acquisition",
        (
            "run_name",
            "phase",
            "captured_at",
            "description",
            "live_time_s",
            "real_time_s",
            "dead_time_pct",
            "fast_count",
            "slow_count",
            "gp_count",
        ),
    ),
    (
        "Calibration",
        (
            "points",
            "calibration.offset_kev",
            "calibration.slope_kev_per_channel",
            "calibration.channel_origin",
            "calibration.source",
        ),
    ),
    (
        "Detector",
        (
            "model_id",
            "device_type",
            "serial_number",
            "firmware",
            "fpga",
            "gain",
            "hv_volt",
            "tec_temp",
            "board_temp",
            "source_format",
        ),
    ),
    (
        "Electronics (DP5)",
        (
            "dp5_tpea",
            "dp5_gain",
            "dp5_mcac",
            "dp5_hvse",
            "dp5_tecs",
            "dp5_ainp",
            "dp5_clck",
        ),
    ),
)

_KEY_ROLE = Qt.ItemDataRole.UserRole
_RAW_ROLE = Qt.ItemDataRole.UserRole + 1


def flatten_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Nested dicts become dotted keys: ``calibration.offset_kev``."""
    flat: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            for inner_key, inner in flatten_metadata(value).items():
                flat[f"{key}.{inner_key}"] = inner
        else:
            flat[key] = value
    return flat


_UNIT_SUFFIXES = (
    ("_s", " s"),
    ("_hz", " Hz"),
    ("_v", " V"),
    ("_pct", " %"),
    ("_kev", " keV"),
    ("_cm", " cm"),
    ("_mm", " mm"),
)


def _guess(key: str) -> tuple[str, str]:
    """Label and unit for a field no table lists, from its name."""
    name, unit = key.split(".")[-1], ""
    for suffix, text in _UNIT_SUFFIXES:
        if name.endswith(suffix):
            name, unit = name[: -len(suffix)], text
            break
    return name.replace("_", " ").strip().capitalize(), unit


def field_label(key: str) -> str:
    if key in _FIELDS:
        return _FIELDS[key][0]
    return _guess(key)[0]


def format_value(key: str, value: Any, *, with_unit: bool = True) -> str:
    """Display text for one field; plain text for a legend."""
    if value is None or value == "":
        return "—"
    _label, unit, spec = _FIELDS.get(key) or ("", _guess(key)[1], "")
    text: str
    if spec and isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            text = format(int(value) if spec.endswith("d") else value, spec)
        except (TypeError, ValueError):
            text = str(value)
    else:
        text = str(value).replace("�", "°")
    return text + (unit if with_unit else "")


class CaptureMetadataPanel(QTreeWidget):
    """Field / value tree with a right-click "Add to legend" action."""

    legend_fields_changed = Signal(list)

    def __init__(self, parent=None, *, legend: bool = False) -> None:
        super().__init__(parent)
        self._legend_enabled = legend
        self._legend_keys: list[str] = []
        self.setColumnCount(2)
        self.setHeaderLabels(["Field", "Value"])
        self.setRootIsDecorated(True)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        header = self.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(True)
        if legend:
            self.setToolTip("Right-click a value to add it to the plot legend")

    def legend_keys(self) -> list[str]:
        return list(self._legend_keys)

    def set_legend_keys(self, keys: list[str]) -> None:
        self._legend_keys = list(keys)
        self._mark_legend_rows()

    def show_metadata(
        self,
        payload: dict[str, Any] | None,
        path: str = "",
        sections: tuple[tuple[str, tuple[str, ...]], ...] | None = None,
    ) -> None:
        """Grouped rows; ``sections`` overrides the X-123 layout for other sources."""
        self.clear()
        if not payload:
            if path:
                self._add_section("File", [("path", path)])
            return
        flat = flatten_metadata(payload)
        placed: set[str] = set()
        for title, keys in sections or _SECTIONS:
            rows = [(key, flat[key]) for key in keys if key in flat]
            placed.update(key for key, _ in rows)
            self._add_section(title, rows)
        other = [(key, value) for key, value in flat.items() if key not in placed]
        self._add_section("Other", other)
        if path:
            self._add_section("File", [("path", path)])
        self.expandAll()
        self._mark_legend_rows()

    def show_record(self, record, *, accepted: bool = True) -> None:
        """A catalogue record: its own fields, then its sidecar metadata."""
        payload: dict[str, Any] = {
            "stem": record.stem,
            "campaign": record.campaign,
            "kind": record.kind_label,
            "run": record.run_name,
            "captured": record.captured_at,
            "points": record.points,
            "channel": record.channel,
            "model": record.model_id,
        }
        shown = {k: v for k, v in payload.items() if v not in (None, "")}
        self.clear()
        self._add_section("Capture", list(shown.items()))
        meta = {
            key: value
            for key, value in flatten_metadata(record.metadata).items()
            if not isinstance(value, (list, tuple)) and key not in shown
        }
        self._add_section("Metadata", list(meta.items()))
        if not accepted:
            self._add_section("Note", [("note", "This analysis cannot open this capture kind.")])
        self._add_section("File", [("path", str(record.path))])
        self.expandAll()
        self._mark_legend_rows()

    def _add_section(self, title: str, rows: list[tuple[str, Any]]) -> None:
        if not rows:
            return
        section = QTreeWidgetItem([title])
        font = section.font(0)
        font.setBold(True)
        section.setFont(0, font)
        section.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.addTopLevelItem(section)
        for key, value in rows:
            item = QTreeWidgetItem([field_label(key), format_value(key, value)])
            item.setData(0, _KEY_ROLE, key)
            item.setData(0, _RAW_ROLE, value)
            item.setToolTip(1, str(value))
            section.addChild(item)

    def _mark_legend_rows(self) -> None:
        for top in range(self.topLevelItemCount()):
            section = self.topLevelItem(top)
            for row in range(section.childCount()):
                item = section.child(row)
                key = item.data(0, _KEY_ROLE)
                label = field_label(key)
                on = key in self._legend_keys
                item.setText(0, f"{label}  ●" if on else label)

    def _context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        menu = QMenu(self)
        key = item.data(0, _KEY_ROLE) if item is not None else None
        if key and self._legend_enabled:
            if key in self._legend_keys:
                act = menu.addAction("Remove from legend")
                act.triggered.connect(lambda: self._toggle(key, False))
            else:
                act = menu.addAction("Add to legend")
                act.triggered.connect(lambda: self._toggle(key, True))
        if key:
            copy = menu.addAction("Copy value")
            copy.triggered.connect(
                lambda: QGuiApplication.clipboard().setText(str(item.data(0, _RAW_ROLE)))
            )
        if self._legend_enabled and self._legend_keys:
            if key:
                menu.addSeparator()
            clear = menu.addAction("Clear legend fields")
            clear.triggered.connect(self._clear)
        if menu.isEmpty():
            return
        menu.exec(self.viewport().mapToGlobal(pos))

    def _toggle(self, key: str, on: bool) -> None:
        keys = [k for k in self._legend_keys if k != key]
        if on:
            keys.append(key)
        self.set_legend_keys(keys)
        self.legend_fields_changed.emit(self.legend_keys())

    def _clear(self) -> None:
        self.set_legend_keys([])
        self.legend_fields_changed.emit([])
