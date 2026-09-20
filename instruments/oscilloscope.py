"""Oscilloscope role: model-agnostic capture interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from lib.waveform import WaveformCapture


class Oscilloscope(ABC):
    """A bench oscilloscope that can download analog-channel waveforms."""

    model_id: str

    def __init__(self, connection: dict | None = None) -> None:
        self.connection = dict(connection or {})

    def __enter__(self) -> Oscilloscope:
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @abstractmethod
    def connect(self) -> None:
        """Open a session to the instrument."""

    @abstractmethod
    def close(self) -> None:
        """Release the session."""

    @abstractmethod
    def identify(self) -> str:
        """Return the instrument identity string (typically ``*IDN?``)."""

    @abstractmethod
    def capture_channel(self, channel: int = 1, **kwargs) -> WaveformCapture:
        """Download the waveform currently in memory for one analog channel."""
