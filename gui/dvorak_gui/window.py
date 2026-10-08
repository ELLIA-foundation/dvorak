"""Analysis window: capture browser plus a placeholder workspace."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME
from .catalog import CaptureRecord
from .registry import AnalysisSpec
from .widgets import CaptureBrowser
from .widgets.capture_metadata import CaptureMetadataPanel

if TYPE_CHECKING:
    from .app import AppController


class AnalysisWindow(QMainWindow):
    """Workspace opened from the launcher.

    Phase 1 fills the capture browser. Later phases replace the right-hand
    placeholder with plots; the File menu stays the same.
    """

    def __init__(self, spec: AnalysisSpec, controller: AppController) -> None:
        super().__init__()
        self._spec = spec
        self._controller = controller
        self._chosen: CaptureRecord | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(f"{spec.title} — {APP_NAME}")
        self.resize(1180, 720)
        self._build_menu()
        self._build_body()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        new_act = QAction("New analysis window…", self)
        new_act.setShortcut(QKeySequence.StandardKey.New)
        new_act.triggered.connect(self._controller.show_launcher)
        file_menu.addAction(new_act)

        refresh_act = QAction("Refresh catalogue", self)
        refresh_act.setShortcut(QKeySequence.StandardKey.Refresh)
        refresh_act.triggered.connect(self._refresh_catalogue)
        file_menu.addAction(refresh_act)

        file_menu.addSeparator()

        close_act = QAction("Close", self)
        close_act.setShortcut(QKeySequence.StandardKey.Close)
        close_act.triggered.connect(self.close)
        file_menu.addAction(close_act)

        quit_act = QAction(f"Quit {APP_NAME}", self)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self._controller.quit)
        file_menu.addAction(quit_act)

    def _build_body(self) -> None:
        self._browser = CaptureBrowser(
            self._controller,
            accepted_kinds=self._spec.accepted_kinds,
        )
        self._browser.capture_selected.connect(self._on_capture_selected)
        self._browser.capture_chosen.connect(self._on_capture_chosen)

        splitter = QSplitter()
        splitter.addWidget(self._browser)
        panes = self._workspace_panes()
        for pane in panes:
            splitter.addWidget(pane)
        splitter.setStretchFactor(0, 1)
        for index in range(1, splitter.count()):
            splitter.setStretchFactor(index, 2 if index == 1 else 1)
        if splitter.count() == 2:
            splitter.setSizes([420, 760])
        else:
            splitter.setSizes([360, 760, 280])
        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Select a capture")

    def _workspace_panes(self) -> list[QWidget]:
        heading = QLabel(self._spec.title)
        heading_font = QFont()
        heading_font.setPointSize(16)
        heading_font.setBold(True)
        heading.setFont(heading_font)

        self._hint = QLabel(self._spec.description)
        self._hint.setWordWrap(True)

        self._detail = CaptureMetadataPanel()

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(heading)
        right_layout.addWidget(self._hint)
        right_layout.addWidget(self._detail, stretch=1)
        return [right]

    def _refresh_catalogue(self) -> None:
        self._browser.refresh()

    def _on_capture_selected(self, record: CaptureRecord | None) -> None:
        if record is None:
            self._detail.show_metadata(None)
            self.statusBar().showMessage("Select a capture")
            return
        accepted = self._is_accepted(record)
        self._detail.show_record(record, accepted=accepted)
        if accepted:
            self.statusBar().showMessage(str(record.path))
        else:
            kinds = ", ".join(self._spec.accepted_kinds) or "any"
            self.statusBar().showMessage(
                f"{record.kind} — this analysis opens {kinds} captures"
            )

    def _on_capture_chosen(self, record: CaptureRecord) -> None:
        if not self._is_accepted(record):
            return
        self._chosen = record
        self._on_capture_selected(record)
        self._handle_opened(record)

    def _open_capture_path(self, path) -> bool:
        """Open a capture by file path, as if chosen in the catalogue."""
        return self._browser.open_path(path)

    def _handle_opened(self, record: CaptureRecord) -> None:
        self.statusBar().showMessage(f"Opened {record.stem}")

    def _is_accepted(self, record: CaptureRecord) -> bool:
        if not self._spec.accepted_kinds:
            return True
        return record.kind in self._spec.accepted_kinds
