"""Browse OPIXE pixel-detector measurements that Pixet has already derived.

The list comes from ``opixe-core list`` (see ``lib.pixet``), so labels, groups
and the up-to-date badge are exactly what OPIXE reports. Nothing is processed
here: a measurement that needs re-clustering or re-deriving is flagged, and
that work stays in Pixet.
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lib.pixet import PixelCatalogue, PixelMeasurement, default_data_root, list_measurements

from ..workers import WorkerHandle

_ID_ROLE = Qt.ItemDataRole.UserRole
_UP_TO_DATE = "up to date"
_UNGROUPED = "Not in a group"


class PixelBrowser(QWidget):
    """Searchable, multi-select tree of derived OPIXE measurements."""

    selection_changed = Signal()
    current_changed = Signal(object)  # PixelMeasurement | None
    status_message = Signal(str)
    catalogue_loaded = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker = WorkerHandle()
        self._data_root = default_data_root()
        self._measurements: dict[str, PixelMeasurement] = {}
        self._pending_ids: list[str] | None = None
        self._build()
        self.refresh()

    # -- public ------------------------------------------------------------

    @property
    def data_root(self) -> Path:
        return self._data_root

    def measurement(self, measurement_id: str) -> PixelMeasurement | None:
        return self._measurements.get(measurement_id)

    def selected(self) -> list[PixelMeasurement]:
        seen: set[str] = set()
        out: list[PixelMeasurement] = []
        for item in self._tree.selectedItems():
            mid = item.data(0, _ID_ROLE)
            if mid and mid not in seen and mid in self._measurements:
                seen.add(mid)
                out.append(self._measurements[mid])
        return out

    def current(self) -> PixelMeasurement | None:
        item = self._tree.currentItem()
        mid = item.data(0, _ID_ROLE) if item is not None else None
        return self._measurements.get(mid) if mid else None

    def set_data_root(self, path: Path) -> None:
        self._data_root = Path(path)
        self.refresh()

    def select_ids(self, ids: list[str]) -> None:
        """Select these measurements (after the catalogue loads, if it is loading)."""
        if not self._measurements:
            self._pending_ids = list(ids)
            return
        wanted = set(ids)
        first = None
        self._tree.blockSignals(True)
        try:
            self._tree.clearSelection()
            for item in self._items():
                if item.data(0, _ID_ROLE) in wanted:
                    item.setSelected(True)
                    if item.parent() is not None:
                        item.parent().setExpanded(True)
                    first = first or item
            if first is not None:
                # NoUpdate: making it current must not drop the rest of the selection.
                self._tree.setCurrentItem(first, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
                self._tree.scrollToItem(first)
        finally:
            self._tree.blockSignals(False)
        self._update_count()
        self.current_changed.emit(self.current())
        self.selection_changed.emit()

    def refresh(self) -> None:
        self._count.setText("Reading the OPIXE catalogue…")
        self._root_label.setText(str(self._data_root))
        self._root_label.setToolTip(str(self._data_root))
        self._worker.start(
            list_measurements,
            self._data_root,
            on_finished=self._on_catalogue,
            on_failed=self._on_failed,
        )

    def shutdown(self) -> None:
        self._worker.cancel()

    # -- build -------------------------------------------------------------

    def _build(self) -> None:
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search label, tag, group, description…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)

        choose = QPushButton("Folder…")
        choose.setToolTip("Read measurements from another OPIXE data folder")
        choose.clicked.connect(self._choose_root)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)

        self._root_label = QLabel()
        self._root_label.setStyleSheet("color: palette(mid);")
        self._root_label.setWordWrap(True)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Measurement", "Date", "OPIXE status"])
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tree.setUniformRowHeights(True)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.itemSelectionChanged.connect(self._on_selection)
        self._tree.currentItemChanged.connect(
            lambda *_: self.current_changed.emit(self.current())
        )

        self._count = QLabel()
        self._count.setStyleSheet("color: palette(mid);")

        top = QHBoxLayout()
        top.addWidget(self._search, stretch=1)
        top.addWidget(choose)
        top.addWidget(refresh)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self._root_label)
        layout.addWidget(self._tree, stretch=1)
        layout.addWidget(self._count)

    # -- catalogue ---------------------------------------------------------

    def _on_catalogue(self, result: object) -> None:
        if not isinstance(result, PixelCatalogue):
            return
        selected = {m.id for m in self.selected()}
        self._measurements = {m.id: m for m in result.measurements}
        self._rebuild()
        pending, self._pending_ids = self._pending_ids, None
        if pending is not None:
            self.select_ids(pending)
        elif selected:
            self.select_ids([mid for mid in selected if mid in self._measurements])
        note = ""
        if result.source != "opixe-core":
            note = " · opixe-core not found, status from folders only"
        if result.error and not result.measurements:
            self._count.setText(result.error)
        else:
            self._update_count(note)
        if result.error:
            self.status_message.emit(f"OPIXE: {result.error}")
        self.catalogue_loaded.emit()

    def _on_failed(self, message: str) -> None:
        self._count.setText(f"Could not read the OPIXE catalogue: {message}")

    def _rebuild(self) -> None:
        self._tree.blockSignals(True)
        try:
            self._tree.clear()
            groups: dict[str, QTreeWidgetItem] = {}

            def parent_for(name: str) -> QTreeWidgetItem:
                if name not in groups:
                    item = QTreeWidgetItem([name])
                    font = item.font(0)
                    font.setBold(True)
                    item.setFont(0, font)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    groups[name] = item
                return groups[name]

            ordered = sorted(self._measurements.values(), key=lambda m: m.id, reverse=True)
            for measurement in ordered:
                for name in measurement.groups or [_UNGROUPED]:
                    parent_for(name).addChild(self._make_item(measurement))
            names = sorted(name for name in groups if name != _UNGROUPED)
            if _UNGROUPED in groups:
                names.append(_UNGROUPED)
            for name in names:
                self._tree.addTopLevelItem(groups[name])
                groups[name].setExpanded(name != _UNGROUPED or len(groups) == 1)
        finally:
            self._tree.blockSignals(False)
        self._apply_filter(self._search.text())

    def _make_item(self, measurement: PixelMeasurement) -> QTreeWidgetItem:
        date = measurement.id[:10] if measurement.id[:4].isdigit() else measurement.created[:10]
        item = QTreeWidgetItem([measurement.display_name, date, measurement.status])
        item.setData(0, _ID_ROLE, measurement.id)
        lines = []
        if measurement.description:
            lines.append(f"<b>{html.escape(measurement.description)}</b>")
        else:
            lines.append("<i>No OPIXE description</i>")
        lines.append(html.escape(measurement.id))
        if measurement.tags:
            lines.append("tags: " + html.escape(", ".join(measurement.tags)))
        if measurement.status and measurement.status != _UP_TO_DATE:
            lines.append(
                html.escape(
                    f"OPIXE reports: {measurement.status}. The spectrum shown is the "
                    "last derived one; re-run it in Pixet for the current settings."
                )
            )
            item.setForeground(2, QBrush(QColor("#b26b00")))
        tip = "<div style='max-width: 420px'>" + "<br>".join(lines) + "</div>"
        for column in range(3):
            item.setToolTip(column, tip)
        return item

    def _items(self):
        for top in range(self._tree.topLevelItemCount()):
            parent = self._tree.topLevelItem(top)
            for row in range(parent.childCount()):
                yield parent.child(row)

    def _apply_filter(self, query: str) -> None:
        words = [w for w in query.lower().split() if w]
        for top in range(self._tree.topLevelItemCount()):
            parent = self._tree.topLevelItem(top)
            group_text = parent.text(0).lower()
            visible = 0
            for row in range(parent.childCount()):
                child = parent.child(row)
                measurement = self._measurements.get(child.data(0, _ID_ROLE))
                hay = (measurement.search_haystack() if measurement else "") + " " + group_text
                show = all(word in hay for word in words)
                child.setHidden(not show)
                visible += int(show)
            parent.setHidden(visible == 0)
            if words and visible:
                parent.setExpanded(True)
        self._update_count()

    def _update_count(self, note: str = "") -> None:
        n = len(self._measurements)
        chosen = len(self.selected())
        text = f"{n} derived measurement{'s' if n != 1 else ''}"
        if chosen:
            text += f" · {chosen} selected"
        self._count.setText(text + note)

    def _on_selection(self) -> None:
        self._update_count()
        self.selection_changed.emit()

    def _choose_root(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "OPIXE data folder", str(self._data_root)
        )
        if chosen:
            self.set_data_root(Path(chosen))
