"""Signal-generator role: model-agnostic source interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class SignalGenerator(ABC):
    """A bench function / arbitrary waveform generator."""

    model_id: str

    def __init__(self, connection: dict | None = None) -> None:
        self.connection = dict(connection or {})

    def __enter__(self) -> SignalGenerator:
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
    def set_waveform(
        self,
        channel: int,
        shape: str,
        frequency_hz: float,
        amplitude_vpp: float,
        offset_v: float = 0.0,
    ) -> None:
        """Configure a standard waveform on one output channel."""

    @abstractmethod
    def output(self, channel: int, enabled: bool) -> None:
        """Enable or disable an output channel."""
