"""Placeholder analysis window used until a plugin grows a real workspace."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QLabel, QMainWindow

from . import APP_NAME
from .registry import AnalysisSpec

if TYPE_CHECKING:
    from .app import AppController


class AnalysisWindow(QMainWindow):
    """Empty workspace opened from the launcher.

    Later phases replace ``window_factory`` with a specialised subclass; the
    File menu (new analysis / close) stays the same.
    """

    def __init__(self, spec: AnalysisSpec, controller: AppController) -> None:
        super().__init__()
        self._spec = spec
        self._controller = controller
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(f"{spec.title} — {APP_NAME}")
        self.resize(960, 640)
        self._build_menu()
        self._build_placeholder()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        new_act = QAction("New analysis window…", self)
        new_act.setShortcut(QKeySequence.StandardKey.New)
        new_act.triggered.connect(self._controller.show_launcher)
        file_menu.addAction(new_act)

        file_menu.addSeparator()

        close_act = QAction("Close", self)
        close_act.setShortcut(QKeySequence.StandardKey.Close)
        close_act.triggered.connect(self.close)
        file_menu.addAction(close_act)

        quit_act = QAction(f"Quit {APP_NAME}", self)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self._controller.quit)
        file_menu.addAction(quit_act)

    def _build_placeholder(self) -> None:
        label = QLabel(
            f"<h2>{self._spec.title}</h2>"
            f"<p>{self._spec.description}</p>"
            "<p>This workspace will be filled in a later phase.</p>"
        )
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        self.setCentralWidget(label)
        self.statusBar().showMessage("Phase 0 placeholder")
