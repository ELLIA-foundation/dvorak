"""Startup launcher: pick an analysis, open a dedicated window."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME
from .registry import (
    FAMILY_ANALYSIS,
    FAMILY_MEASUREMENT,
    AnalysisSpec,
    list_analyses,
)

if TYPE_CHECKING:
    from .app import AppController

_MEASUREMENT_HINT = (
    "Coming later. The Measurement GUI will run on lab computers "
    "(Windows) on the master branch."
)


class _PluginCard(QFrame):
    opened = Signal(str)

    def __init__(self, spec: AnalysisSpec) -> None:
        super().__init__()
        self._spec = spec
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        enabled = spec.enabled
        reason = spec.disabled_reason or _MEASUREMENT_HINT
        if not enabled:
            self.setToolTip(reason)

        title = QLabel(spec.title)
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)

        description = QLabel(spec.description)
        description.setWordWrap(True)
        description.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        if not enabled:
            title.setEnabled(False)
            description.setEnabled(False)

        open_btn = QPushButton("Open")
        open_btn.setDefault(enabled)
        open_btn.setEnabled(enabled)
        if not enabled:
            open_btn.setToolTip(reason)
        open_btn.clicked.connect(self._emit_open)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.addWidget(title)
        text.addWidget(description)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.addLayout(text, stretch=1)
        row.addWidget(open_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        if enabled:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self._spec.enabled:
            self._emit_open()
        super().mouseDoubleClickEvent(event)

    def _emit_open(self) -> None:
        self.opened.emit(self._spec.id)


class LauncherWindow(QMainWindow):
    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self._controller = controller
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(APP_NAME)
        self.resize(560, 520)
        self._build_menu()
        self._build_body()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        close_act = QAction("Close", self)
        close_act.setShortcut(QKeySequence.StandardKey.Close)
        close_act.triggered.connect(self.close)
        file_menu.addAction(close_act)

        quit_act = QAction(f"Quit {APP_NAME}", self)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self._controller.quit)
        file_menu.addAction(quit_act)

    def _build_body(self) -> None:
        heading = QLabel("Choose an analysis")
        heading_font = QFont()
        heading_font.setPointSize(18)
        heading_font.setBold(True)
        heading.setFont(heading_font)

        subtitle = QLabel(
            "Each choice opens its own window. The launcher stays open so you "
            "can start more than one."
        )
        subtitle.setWordWrap(True)

        analysis_box = self._section("Analysis", list_analyses(FAMILY_ANALYSIS))
        measurement_box = self._section(
            "Measurement",
            list_analyses(FAMILY_MEASUREMENT),
        )

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.addWidget(heading)
        inner_layout.addWidget(subtitle)
        inner_layout.addSpacing(8)
        inner_layout.addWidget(analysis_box)
        inner_layout.addWidget(measurement_box)
        inner_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)

        self.setCentralWidget(scroll)
        self.statusBar().showMessage("Analysis branch — measurement tools come later")

    def _section(self, title: str, specs: list[AnalysisSpec]) -> QGroupBox:
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        if not specs:
            empty = QLabel("Nothing registered yet.")
            empty.setEnabled(False)
            layout.addWidget(empty)
            return box
        for spec in specs:
            card = _PluginCard(spec)
            card.opened.connect(self._controller.open_analysis)
            layout.addWidget(card)
        return box
