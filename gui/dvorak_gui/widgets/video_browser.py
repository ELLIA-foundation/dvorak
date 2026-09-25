"""Campaign and clip list for Measurements/Videos."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lib.paths import list_video_campaigns, list_video_clips, videos_dir

from .figure_gallery import reveal_in_folder

PATH_ROLE = Qt.ItemDataRole.UserRole

_CACHE_COLOR = {
    "fresh": "#2e7d32",
    "stale": "#ef6c00",
    "missing": "#9e9e9e",
}


def _cache_status(path: Path) -> str:
    from ..campaign_import import load_video_module

    return load_video_module("dataset").cache_status(path)


class VideoBrowser(QWidget):
    """List video campaigns and their clips. Multi-select feeds the overlay."""

    current_clip_changed = Signal(object)
    selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build()
        self.refresh()

    def current_campaign(self) -> str | None:
        item = self._tree.currentItem()
        if item is None:
            return None
        parent = item.parent()
        if parent is None:
            return item.text(0)
        return parent.text(0)

    def current_clip(self) -> Path | None:
        item = self._tree.currentItem()
        if item is None:
            return None
        path = item.data(0, PATH_ROLE)
        return path if isinstance(path, Path) else None

    def selected_clips(self) -> list[Path]:
        paths: list[Path] = []
        for item in self._tree.selectedItems():
            path = item.data(0, PATH_ROLE)
            if isinstance(path, Path):
                paths.append(path)
        return paths

    def has_campaigns(self) -> bool:
        return self._tree.topLevelItemCount() > 0

    def refresh(self) -> None:
        query = self._search.text()
        current = self.current_clip()
        selected = {path.resolve() for path in self.selected_clips()}
        select_item: QTreeWidgetItem | None = None
        reselect: list[QTreeWidgetItem] = []
        self._tree.blockSignals(True)
        try:
            self._tree.clear()
            for campaign in list_video_campaigns():
                parent = QTreeWidgetItem([campaign])
                font = parent.font(0)
                font.setBold(True)
                parent.setFont(0, font)
                parent.setToolTip(0, str(videos_dir() / campaign))
                self._tree.addTopLevelItem(parent)
                for clip in list_video_clips(campaign):
                    status = _cache_status(clip)
                    child = QTreeWidgetItem([clip.name, status])
                    child.setData(0, PATH_ROLE, clip)
                    child.setToolTip(0, str(clip))
                    color = _CACHE_COLOR.get(status)
                    if color:
                        child.setForeground(1, QBrush(QColor(color)))
                    parent.addChild(child)
                    if current is not None and clip.resolve() == current.resolve():
                        select_item = child
                    if clip.resolve() in selected:
                        reselect.append(child)
                parent.setExpanded(True)
            if select_item is not None:
                self._tree.setCurrentItem(select_item)
            for item in reselect:
                item.setSelected(True)
        finally:
            self._tree.blockSignals(False)

        self._apply_filter(query)
        self._tree.resizeColumnToContents(0)
        self._tree.resizeColumnToContents(1)
        self._update_count_label()
        self.current_clip_changed.emit(self.current_clip())

    def _build(self) -> None:
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search campaigns and clips…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)

        search_row = QHBoxLayout()
        search_row.addWidget(self._search, stretch=1)
        search_row.addWidget(refresh_btn)

        self._root_label = QLabel(str(videos_dir()))
        self._root_label.setWordWrap(True)
        self._root_label.setToolTip(str(videos_dir()))
        self._root_label.setStyleSheet("color: palette(mid);")

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(("Clip", "Cache"))
        self._tree.setUniformRowHeights(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.currentItemChanged.connect(self._on_current_changed)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)

        self._count_label = QLabel()
        self._count_label.setStyleSheet("color: palette(mid);")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(search_row)
        layout.addWidget(self._root_label)
        layout.addWidget(self._tree, stretch=1)
        layout.addWidget(self._count_label)

    def _apply_filter(self, query: str) -> None:
        needle = query.strip().lower()
        for index in range(self._tree.topLevelItemCount()):
            parent = self._tree.topLevelItem(index)
            if parent is None:
                continue
            campaign_match = bool(needle) and needle in parent.text(0).lower()
            any_visible = False
            for row in range(parent.childCount()):
                child = parent.child(row)
                if child is None:
                    continue
                visible = not needle or campaign_match or needle in child.text(0).lower()
                child.setHidden(not visible)
                any_visible = any_visible or visible
            parent.setHidden(bool(needle) and not any_visible and not campaign_match)
        self._update_count_label()

    def _update_count_label(self) -> None:
        campaigns = 0
        clips = 0
        for index in range(self._tree.topLevelItemCount()):
            parent = self._tree.topLevelItem(index)
            if parent is None or parent.isHidden():
                continue
            visible_children = 0
            for row in range(parent.childCount()):
                child = parent.child(row)
                if child is not None and not child.isHidden():
                    visible_children += 1
            campaigns += 1
            clips += visible_children
        selected = len(self.selected_clips())
        text = f"{clips} clips in {campaigns} campaigns"
        if selected:
            text += f" · {selected} selected"
        self._count_label.setText(text)

    def _on_selection_changed(self) -> None:
        self._update_count_label()
        self.selection_changed.emit()

    def _on_current_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        path = None
        if current is not None:
            stored = current.data(0, PATH_ROLE)
            if isinstance(stored, Path):
                path = stored
        self._update_count_label()
        self.current_clip_changed.emit(path)

    def _show_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        stored = item.data(0, PATH_ROLE)
        target = stored if isinstance(stored, Path) else videos_dir() / item.text(0)
        menu = QMenu(self)
        reveal = QAction(
            "Reveal in Finder" if sys.platform == "darwin" else "Show in folder",
            self,
        )
        reveal.triggered.connect(lambda: reveal_in_folder(target))
        copy_act = QAction("Copy path", self)
        copy_act.setShortcut(QKeySequence.StandardKey.Copy)
        copy_act.triggered.connect(lambda: QGuiApplication.clipboard().setText(str(target)))
        menu.addAction(reveal)
        menu.addAction(copy_act)
        menu.exec(self._tree.viewport().mapToGlobal(pos))
