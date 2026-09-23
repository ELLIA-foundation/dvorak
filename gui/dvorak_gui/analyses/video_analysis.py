"""Video Analysis: browse Measurements/Videos and play a clip."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QSplitter

from lib.paths import videos_dir

from ..registry import FAMILY_ANALYSIS, AnalysisSpec, get, register
from ..widgets.video_browser import VideoBrowser
from ..widgets.video_player import VideoPlayer
from ..window import AnalysisWindow

VIDEO_ANALYSIS_ID = "video_analysis"

_SELECT_CLIP = (
    "Select a clip to play it. "
    "Shift-click or Command-click selects several clips."
)


class VideoAnalysisWindow(AnalysisWindow):
    def __init__(self, spec: AnalysisSpec, controller: Any) -> None:
        self._loaded: Path | None = None
        super().__init__(spec, controller)
        self._show_idle()

    def _build_menu(self) -> None:
        super()._build_menu()
        file_menu = self.menuBar().actions()[0].menu()
        assert file_menu is not None
        for action in file_menu.actions():
            if action.text() == "Refresh catalogue":
                action.setText("Refresh videos")

    def _build_body(self) -> None:
        self._browser = VideoBrowser()
        self._browser.current_clip_changed.connect(self._on_clip)
        self._player = VideoPlayer()
        self._player.status_changed.connect(self._on_player_status)

        splitter = QSplitter()
        splitter.addWidget(self._browser)
        splitter.addWidget(self._player)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([420, 760])
        self.setCentralWidget(splitter)

    def _refresh_catalogue(self) -> None:
        self._loaded = None
        self._browser.refresh()
        if self._browser.current_clip() is None:
            self._show_idle()

    def _on_player_status(self, text: str) -> None:
        self.statusBar().showMessage(text)

    def _on_clip(self, path: Path | None) -> None:
        if path is None:
            self._loaded = None
            self._show_idle()
            return
        if path == self._loaded:
            return
        self._loaded = path
        self._player.load(path)

    def _show_idle(self) -> None:
        if self._browser.has_campaigns():
            self._player.show_message(_SELECT_CLIP)
            self.statusBar().showMessage("Select a clip")
            return
        root = videos_dir()
        self._player.show_message(
            "No video campaigns yet.\n\n"
            f"Create a folder under {root}/<campaign>/ and put MP4 or MOV files "
            "directly in it, then choose File → Refresh videos."
        )
        self.statusBar().showMessage("No video campaigns")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._player.stop()
        super().closeEvent(event)


def _create_window(controller: Any) -> VideoAnalysisWindow:
    return VideoAnalysisWindow(get(VIDEO_ANALYSIS_ID), controller)


register(
    AnalysisSpec(
        id=VIDEO_ANALYSIS_ID,
        title="Video Analysis",
        description=(
            "Play MP4 and MOV clips stored under Measurements/Videos/<campaign>/."
        ),
        family=FAMILY_ANALYSIS,
        window_factory=_create_window,
    )
)
