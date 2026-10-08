"""Campaign and spectrum list for Measurements/X123_Spectra."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QItemSelectionModel, QObject, Qt, Signal
from shiboken6 import isValid
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QGuiApplication, QKeySequence
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

from lib.paths import collect_mca_files, list_spectrum_campaigns, list_spectrum_files, spectra_dir

from .figure_gallery import reveal_in_folder
from .spectrum_import import run_mca_import

PATH_ROLE = Qt.ItemDataRole.UserRole
CAMPAIGN_ROLE = Qt.ItemDataRole.UserRole + 1


def _mca_from_mime(mime) -> list[Path]:
    if mime is None or not mime.hasUrls():
        return []
    raw: list[Path] = []
    for url in mime.urls():
        if url.isLocalFile():
            raw.append(Path(url.toLocalFile()))
    return collect_mca_files(raw)


class _McaDropFilter(QObject):
    """Accept Finder drops of ``.mca`` files onto the spectrum tree."""

    def __init__(self, tree: QTreeWidget, on_drop) -> None:
        super().__init__(tree)
        self._tree = tree
        self._on_drop = on_drop
        viewport = tree.viewport()
        viewport.setAcceptDrops(True)
        viewport.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if not isValid(self._tree):
            return False
        if watched is not self._tree.viewport():
            return False
        kind = event.type()
        if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if not isinstance(event, (QDragEnterEvent, QDropEvent)):
                return False
            if not _mca_from_mime(event.mimeData()):
                return False
            event.acceptProposedAction()
            return True
        if kind == QEvent.Type.Drop and isinstance(event, QDropEvent):
            paths = _mca_from_mime(event.mimeData())
            if not paths:
                return False
            item = self._tree.itemAt(event.position().toPoint())
            self._on_drop(paths, item)
            event.acceptProposedAction()
            return True
        return False


class SpectrumBrowser(QWidget):
    """List nested X-123 campaigns and their .mca / spectrum_*.npz files."""

    current_file_changed = Signal(object)
    selection_changed = Signal()
    status_message = Signal(str)

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

    def current_file(self) -> Path | None:
        item = self._tree.currentItem()
        if item is None:
            return None
        path = item.data(0, PATH_ROLE)
        return path if isinstance(path, Path) else None

    def selected_files(self) -> list[Path]:
        paths: list[Path] = []
        for item in self._tree.selectedItems():
            path = item.data(0, PATH_ROLE)
            if isinstance(path, Path):
                paths.append(path)
        return paths

    def campaign_for(self, path: Path) -> str | None:
        resolved = path.resolve()
        for index in range(self._tree.topLevelItemCount()):
            parent = self._tree.topLevelItem(index)
            if parent is None:
                continue
            for row in range(parent.childCount()):
                child = parent.child(row)
                if child is None:
                    continue
                stored = child.data(0, PATH_ROLE)
                if isinstance(stored, Path) and stored.resolve() == resolved:
                    return parent.text(0)
        return None

    def has_campaigns(self) -> bool:
        return self._tree.topLevelItemCount() > 0

    def new_session(self, sources: list[Path] | None = None) -> None:
        """Create ``X123_Spectra/<session>/Data/`` and copy MCA files into it."""
        self._import(campaign=None, sources=sources)

    def import_mca(self, campaign: str, sources: list[Path] | None = None) -> None:
        """Copy MCA files into an existing session's Data folder."""
        self._import(campaign=campaign, sources=sources)

    def clear_selection(self) -> None:
        self._tree.clearSelection()

    def focus_paths(self, paths: list[Path]) -> None:
        wanted = {path.resolve() for path in paths}
        if not wanted:
            return
        self._search.clear()
        first: QTreeWidgetItem | None = None
        self._tree.blockSignals(True)
        try:
            self._tree.clearSelection()
            for index in range(self._tree.topLevelItemCount()):
                parent = self._tree.topLevelItem(index)
                if parent is None:
                    continue
                for row in range(parent.childCount()):
                    child = parent.child(row)
                    if child is None:
                        continue
                    stored = child.data(0, PATH_ROLE)
                    if isinstance(stored, Path) and stored.resolve() in wanted:
                        child.setSelected(True)
                        parent.setExpanded(True)
                        if first is None:
                            first = child
            if first is not None:
                # NoUpdate: making it current must not drop the rest of the selection.
                self._tree.setCurrentItem(first, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
                self._tree.scrollToItem(first)
        finally:
            self._tree.blockSignals(False)
        self._update_count_label()
        self.current_file_changed.emit(self.current_file())
        self.selection_changed.emit()

    def refresh(self) -> None:
        query = self._search.text()
        current = self.current_file()
        selected = {path.resolve() for path in self.selected_files()}
        select_item: QTreeWidgetItem | None = None
        reselect: list[QTreeWidgetItem] = []
        self._tree.blockSignals(True)
        try:
            self._tree.clear()
            for campaign in list_spectrum_campaigns():
                parent = QTreeWidgetItem([campaign])
                font = parent.font(0)
                font.setBold(True)
                parent.setFont(0, font)
                parent.setToolTip(0, str(spectra_dir() / campaign))
                self._tree.addTopLevelItem(parent)
                for path in list_spectrum_files(campaign):
                    kind = "MCA" if path.suffix.lower() == ".mca" else "NPZ"
                    child = QTreeWidgetItem([path.name, kind])
                    child.setData(0, PATH_ROLE, path)
                    child.setData(0, CAMPAIGN_ROLE, campaign)
                    child.setToolTip(0, str(path))
                    parent.addChild(child)
                    if current is not None and path.resolve() == current.resolve():
                        select_item = child
                    if path.resolve() in selected:
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
        self.current_file_changed.emit(self.current_file())
        self.selection_changed.emit()

    def _build(self) -> None:
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search sessions and spectra…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)

        new_btn = QPushButton("New session…")
        new_btn.setToolTip(
            "Create Measurements/X123_Spectra/<session>/Data/ and copy .mca files into it."
        )
        new_btn.clicked.connect(lambda: self.new_session())

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)

        search_row = QHBoxLayout()
        search_row.addWidget(self._search, stretch=1)
        search_row.addWidget(new_btn)
        search_row.addWidget(refresh_btn)

        root = spectra_dir()
        self._root_label = QLabel(str(root))
        self._root_label.setWordWrap(True)
        self._root_label.setToolTip(str(root))
        self._root_label.setStyleSheet("color: palette(mid);")

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(("Spectrum", "Kind"))
        self._tree.setUniformRowHeights(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        self._tree.currentItemChanged.connect(self._on_current_changed)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        self._drop_filter = _McaDropFilter(self._tree, self._on_mca_dropped)

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
                visible = (
                    not needle
                    or campaign_match
                    or needle in child.text(0).lower()
                )
                child.setHidden(not visible)
                any_visible = any_visible or visible
            parent.setHidden(bool(needle) and not any_visible and not campaign_match)
        self._update_count_label()

    def _update_count_label(self) -> None:
        campaigns = 0
        files = 0
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
            files += visible_children
        selected = len(self.selected_files())
        session_word = "session" if campaigns == 1 else "sessions"
        text = f"{files} spectra in {campaigns} {session_word}"
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
        self.current_file_changed.emit(path)

    def _campaign_name(self, item: QTreeWidgetItem | None) -> str | None:
        if item is None:
            return None
        stored = item.data(0, CAMPAIGN_ROLE)
        if isinstance(stored, str) and stored:
            return stored
        if item.parent() is None:
            return item.text(0)
        parent = item.parent()
        return parent.text(0) if parent is not None else None

    def _import(self, *, campaign: str | None, sources: list[Path] | None) -> None:
        result = run_mca_import(self, campaign=campaign, sources=sources)
        if result is None:
            return
        folder, written = result
        self.refresh()
        if written:
            self.focus_paths(written)
            noun = "file" if len(written) == 1 else "files"
            self.status_message.emit(f"Copied {len(written)} MCA {noun} into {folder}")
            return
        self.status_message.emit(f"Session {folder} is ready for .mca files")

    def _on_mca_dropped(self, paths: list[Path], item: QTreeWidgetItem | None) -> None:
        campaign = self._campaign_name(item)
        if campaign:
            self.import_mca(campaign, paths)
            return
        self.new_session(paths)

    def _show_context_menu(self, pos) -> None:
        item = self._tree.itemAt(pos)
        menu = QMenu(self)
        if item is None:
            menu.addAction("New session…", lambda: self.new_session())
            menu.exec(self._tree.viewport().mapToGlobal(pos))
            return
        campaign = self._campaign_name(item)
        stored = item.data(0, PATH_ROLE)
        target = stored if isinstance(stored, Path) else spectra_dir() / item.text(0)
        if campaign:
            menu.addAction(
                "Import MCA files…",
                lambda name=campaign: self.import_mca(name),
            )
        reveal = QAction(
            "Reveal in Finder" if sys.platform == "darwin" else "Show in folder",
            self,
        )
        reveal.triggered.connect(lambda: reveal_in_folder(target))
        copy_act = QAction("Copy path", self)
        copy_act.setShortcut(QKeySequence.StandardKey.Copy)
        copy_act.triggered.connect(
            lambda: QGuiApplication.clipboard().setText(str(target))
        )
        menu.addAction(reveal)
        menu.addAction(copy_act)
        menu.exec(self._tree.viewport().mapToGlobal(pos))
