"""Clip playback. Scrubbing follows the container clock, not individual frames."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


def format_media_time(milliseconds: int) -> str:
    total = max(0, milliseconds) // 1000
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


class VideoPlayer(QWidget):
    """Play / pause, scrub, and duration for one local clip."""

    status_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scrubbing = False
        self._loaded: Path | None = None

        self._message = QLabel()
        self._message.setWordWrap(True)
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setMargin(24)

        self._audio = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio)
        self._video = QVideoWidget()
        self._video.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._video.setMinimumHeight(240)
        self._player.setVideoOutput(self._video)

        self._play = QPushButton("Play")
        self._play.clicked.connect(self._toggle_playback)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        self._slider.sliderPressed.connect(self._begin_scrub)
        self._slider.sliderReleased.connect(self._end_scrub)
        self._slider.sliderMoved.connect(self._on_slider_moved)
        self._time = QLabel("0:00 / 0:00")
        self._time.setMinimumWidth(96)

        controls = QHBoxLayout()
        controls.addWidget(self._play)
        controls.addWidget(self._slider, stretch=1)
        controls.addWidget(self._time)

        player_page = QWidget()
        player_layout = QVBoxLayout(player_page)
        player_layout.setContentsMargins(0, 0, 0, 0)
        player_layout.addWidget(self._video, stretch=1)
        player_layout.addLayout(controls)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._message)
        self._stack.addWidget(player_page)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.errorOccurred.connect(self._on_error)

    def show_message(self, text: str) -> None:
        self._player.stop()
        self._player.setSource(QUrl())
        self._loaded = None
        self._slider.setRange(0, 0)
        self._slider.setValue(0)
        self._time.setText("0:00 / 0:00")
        self._play.setText("Play")
        self._message.setText(text)
        self._stack.setCurrentWidget(self._message)

    def load(self, path: Path) -> None:
        self._loaded = path
        self._stack.setCurrentWidget(self._stack.widget(1))
        self._player.stop()
        self._slider.setRange(0, 0)
        self._slider.setValue(0)
        self._time.setText("0:00 / 0:00")
        self._player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        self.status_changed.emit(str(path))

    def stop(self) -> None:
        self._player.stop()

    def _toggle_playback(self) -> None:
        if self._loaded is None:
            return
        playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if playing:
            self._player.pause()
            return
        duration = self._player.duration()
        if duration > 0 and self._player.position() >= duration - 50:
            self._player.setPosition(0)
        self._player.play()

    def _begin_scrub(self) -> None:
        self._scrubbing = True

    def _end_scrub(self) -> None:
        self._scrubbing = False
        self._player.setPosition(self._slider.value())

    def _on_slider_moved(self, position: int) -> None:
        self._time.setText(self._time_text(position))

    def _on_position(self, position: int) -> None:
        if not self._scrubbing:
            self._slider.setValue(position)
            self._time.setText(self._time_text(position))

    def _on_duration(self, duration: int) -> None:
        self._slider.setRange(0, max(duration, 0))
        self._time.setText(self._time_text(self._player.position()))

    def _on_state(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play.setText("Pause" if playing else "Play")

    def _on_error(self, error: QMediaPlayer.Error, message: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        self.status_changed.emit(message or "Could not play this clip")

    def _time_text(self, position: int) -> str:
        return f"{format_media_time(position)} / {format_media_time(self._player.duration())}"
