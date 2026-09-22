"""PNG figure pack viewer (matplotlib outputs, not live canvases)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


class FigureGallery(QWidget):
    """List of figure names plus a scaled preview of the selected PNG."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[tuple[str, Path]] = []
        self._pixmap: QPixmap | None = None
        self._empty = "Run full analysis to write figures 02–08."

        self._list = QListWidget()
        self._list.setMinimumWidth(160)
        self._list.currentRowChanged.connect(self._show_row)

        self._image = QLabel(self._empty)
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._image)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        split = QSplitter()
        split.addWidget(self._list)
        split.addWidget(self._scroll)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([180, 640])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)

    def set_figures(self, items: list[tuple[str, Path]]) -> None:
        self._items = list(items)
        self._list.blockSignals(True)
        self._list.clear()
        for title, _path in self._items:
            self._list.addItem(title)
        self._list.blockSignals(False)
        if self._items:
            self._list.setCurrentRow(0)
            self._show_row(0)
        else:
            self.clear()

    def clear(self) -> None:
        self._items = []
        self._pixmap = None
        self._list.blockSignals(True)
        self._list.clear()
        self._list.blockSignals(False)
        self._image.clear()
        self._image.setText(self._empty)

    def set_placeholder(self, text: str) -> None:
        self._empty = text
        if not self._items:
            self._image.setText(text)

    def count(self) -> int:
        return len(self._items)

    def current_item(self) -> tuple[str, Path] | None:
        row = self._list.currentRow()
        if row < 0 or row >= len(self._items):
            return None
        return self._items[row]

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()

    def _show_row(self, row: int) -> None:
        if row < 0 or row >= len(self._items):
            self._pixmap = None
            self._image.clear()
            self._image.setText(self._empty)
            return
        path = self._items[row][1]
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._pixmap = None
            self._image.setText(f"Could not load {path.name}")
            return
        self._pixmap = pixmap
        self._fit()

    def _fit(self) -> None:
        if self._pixmap is None:
            return
        viewport = self._scroll.viewport().size()
        if viewport.width() < 8 or viewport.height() < 8:
            return
        scaled = self._pixmap.scaled(
            viewport,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image.setPixmap(scaled)


def reveal_in_folder(path: Path) -> None:
    target = path if path.exists() else path.parent
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(target)], check=False)
        return
    if sys.platform == "win32":
        subprocess.run(["explorer", "/select,", str(target)], check=False)
        return
    folder = target if target.is_dir() else target.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def open_local_path(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
