"""Campaign / capture browser shared by analysis windows."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..catalog import CaptureRecord, count_by_kind, scan
from ..kinds import kind_label

if TYPE_CHECKING:
    from ..app import AppController

RECORD_ROLE = Qt.ItemDataRole.UserRole
ACCEPTED_ROLE = Qt.ItemDataRole.UserRole + 1

_COLUMNS = ("Name", "Kind", "Run", "Date", "Points", "Channel", "Model")


def _format_date(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("T", " ").replace("Z", "")


def _format_points(value: int | None) -> str:
    if value is None:
        return ""
    return f"{value:,}"


class CaptureBrowser(QWidget):
    """List captures under the current data root; emit chosen records."""

    capture_selected = Signal(object)
    capture_chosen = Signal(object)

    def __init__(
        self,
        controller: AppController,
        accepted_kinds: tuple[str, ...] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._accepted_kinds = accepted_kinds
        self._records: list[CaptureRecord] = []
        self._build()
        controller.data_root_changed.connect(self.refresh)
        self.refresh()

    def current_record(self) -> CaptureRecord | None:
        item = self._tree.currentItem()
        if item is None:
            return None
        record = item.data(0, RECORD_ROLE)
        return record if isinstance(record, CaptureRecord) else None

    def refresh(self) -> None:
        query = self._search.text()
        self._records = scan(self._controller.resolved_data_root())
        self._rebuild_tree(query)
        self._update_root_label()
        self._update_count_label()

    def _build(self) -> None:
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search captures…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)

        search_row = QHBoxLayout()
        search_row.addWidget(self._search, stretch=1)
        search_row.addWidget(refresh_btn)

        self._root_label = QLabel()
        self._root_label.setWordWrap(True)
        self._root_label.setStyleSheet("color: palette(mid);")

        other_btn = QPushButton("Use other folder…")
        other_btn.clicked.connect(self._choose_other_folder)
        local_btn = QPushButton("Use local")
        local_btn.clicked.connect(self._use_local)
        folder_row = QHBoxLayout()
        folder_row.addWidget(other_btn)
        folder_row.addWidget(local_btn)
        folder_row.addStretch(1)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(_COLUMNS)
        self._tree.setUniformRowHeights(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.currentItemChanged.connect(self._on_current_changed)
        self._tree.itemDoubleClicked.connect(self._on_double_clicked)
        self._tree.setSortingEnabled(False)

        self._open_btn = QPushButton("Open")
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(self._choose_current)

        self._count_label = QLabel()
        self._count_label.setStyleSheet("color: palette(mid);")

        bottom = QHBoxLayout()
        bottom.addWidget(self._count_label, stretch=1)
        bottom.addWidget(self._open_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(search_row)
        layout.addWidget(self._root_label)
        layout.addLayout(folder_row)
        layout.addWidget(self._tree, stretch=1)
        layout.addLayout(bottom)

    def _update_root_label(self) -> None:
        root = self._controller.resolved_data_root()
        prefix = "External" if self._controller.uses_external_root() else "Local"
        self._root_label.setText(f"{prefix}: {root}")
        self._root_label.setToolTip(str(root))

    def _update_count_label(self) -> None:
        visible = self._visible_records()
        counts = count_by_kind(visible)
        parts = [
            f"{counts.get(kind, 0)} {kind_label(kind)}"
            for kind in ("waveform", "video", "table")
            if counts.get(kind, 0)
        ]
        total = len(visible)
        suffix = ", ".join(parts) if parts else "no captures"
        self._count_label.setText(f"{total} shown · {suffix}")

    def _visible_records(self) -> list[CaptureRecord]:
        records: list[CaptureRecord] = []
        for index in range(self._tree.topLevelItemCount()):
            campaign_item = self._tree.topLevelItem(index)
            if campaign_item is None or campaign_item.isHidden():
                continue
            for row in range(campaign_item.childCount()):
                child = campaign_item.child(row)
                if child is None or child.isHidden():
                    continue
                record = child.data(0, RECORD_ROLE)
                if isinstance(record, CaptureRecord):
                    records.append(record)
        return records

    def _rebuild_tree(self, query: str) -> None:
        current = self.current_record()
        self._tree.clear()
        by_campaign: dict[str, list[CaptureRecord]] = {}
        for record in self._records:
            by_campaign.setdefault(record.campaign, []).append(record)

        select_item: QTreeWidgetItem | None = None
        for campaign, records in by_campaign.items():
            # Newest first within a campaign.
            records = sorted(
                records,
                key=lambda rec: rec.captured_at or "",
                reverse=True,
            )
            parent = QTreeWidgetItem([campaign, "", "", "", "", "", ""])
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            self._tree.addTopLevelItem(parent)
            parent.setFirstColumnSpanned(True)
            for record in records:
                child = self._make_item(record)
                parent.addChild(child)
                if current is not None and record.path == current.path:
                    select_item = child
            parent.setExpanded(True)

        self._apply_filter(query)
        for column in range(len(_COLUMNS)):
            self._tree.resizeColumnToContents(column)
        if select_item is not None:
            self._tree.setCurrentItem(select_item)

    def _make_item(self, record: CaptureRecord) -> QTreeWidgetItem:
        accepted = self._is_accepted(record)
        item = QTreeWidgetItem(
            [
                record.stem,
                record.kind_label,
                record.run_name or "",
                _format_date(record.captured_at),
                _format_points(record.points),
                "" if record.channel is None else str(record.channel),
                record.model_id or "",
            ]
        )
        item.setData(0, RECORD_ROLE, record)
        item.setData(0, ACCEPTED_ROLE, accepted)
        item.setToolTip(0, str(record.path))
        if not accepted:
            for column in range(len(_COLUMNS)):
                item.setForeground(
                    column,
                    self.palette().color(self.palette().ColorRole.PlaceholderText),
                )
            kinds = ", ".join(self._accepted_kinds) or "any"
            item.setToolTip(
                0,
                f"{record.path}\nThis analysis opens {kinds} captures, not {record.kind}.",
            )
        return item

    def _is_accepted(self, record: CaptureRecord) -> bool:
        if not self._accepted_kinds:
            return True
        return record.kind in self._accepted_kinds

    def _apply_filter(self, query: str) -> None:
        for index in range(self._tree.topLevelItemCount()):
            parent = self._tree.topLevelItem(index)
            if parent is None:
                continue
            any_visible = False
            for row in range(parent.childCount()):
                child = parent.child(row)
                if child is None:
                    continue
                record = child.data(0, RECORD_ROLE)
                visible = isinstance(record, CaptureRecord) and record.matches(query)
                child.setHidden(not visible)
                any_visible = any_visible or visible
            parent.setHidden(not any_visible)
        self._update_count_label()

    def _on_current_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        record = current.data(0, RECORD_ROLE) if current is not None else None
        if isinstance(record, CaptureRecord):
            self._open_btn.setEnabled(bool(current.data(0, ACCEPTED_ROLE)))
            self.capture_selected.emit(record)
        else:
            self._open_btn.setEnabled(False)
            self.capture_selected.emit(None)

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.data(0, ACCEPTED_ROLE):
            self._choose_item(item)

    def _choose_current(self) -> None:
        item = self._tree.currentItem()
        if item is not None:
            self._choose_item(item)

    def _choose_item(self, item: QTreeWidgetItem) -> None:
        if not item.data(0, ACCEPTED_ROLE):
            return
        record = item.data(0, RECORD_ROLE)
        if isinstance(record, CaptureRecord):
            self.capture_chosen.emit(record)

    def _show_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        record = item.data(0, RECORD_ROLE)
        if not isinstance(record, CaptureRecord):
            return
        menu = QMenu(self)
        reveal = QAction("Reveal in Finder" if sys.platform == "darwin" else "Show in folder", self)
        reveal.triggered.connect(lambda: _reveal(record.path))
        copy_act = QAction("Copy path", self)
        copy_act.setShortcut(QKeySequence.StandardKey.Copy)
        copy_act.triggered.connect(lambda: _copy_path(record.path))
        menu.addAction(reveal)
        menu.addAction(copy_act)
        if item.data(0, ACCEPTED_ROLE):
            open_act = QAction("Open", self)
            open_act.triggered.connect(lambda: self._choose_item(item))
            menu.insertAction(reveal, open_act)
            menu.insertSeparator(reveal)
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _choose_other_folder(self) -> None:
        start = str(self._controller.resolved_data_root())
        chosen = QFileDialog.getExistingDirectory(self, "Choose data folder", start)
        if not chosen:
            return
        path = Path(chosen)
        if not scan(path):
            QMessageBox.warning(
                self,
                "No captures",
                "That folder has no campaign Data/ captures "
                "(waveform_*.npz, video_*, or CSV).",
            )
            return
        self._controller.set_data_root(path)

    def _use_local(self) -> None:
        self._controller.set_data_root(None)


def _copy_path(path: Path) -> None:
    QGuiApplication.clipboard().setText(str(path))


def _reveal(path: Path) -> None:
    target = path if path.exists() else path.parent
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(target)], check=False)
        return
    if sys.platform == "win32":
        subprocess.run(["explorer", "/select,", str(target)], check=False)
        return
    folder = target if target.is_dir() else target.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
