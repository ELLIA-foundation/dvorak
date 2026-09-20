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
        phase_deg: float = 0.0,
    ) -> None:
        """Configure a standard waveform on one output channel.

        ``shape`` is a case-insensitive name such as sine, square, ramp,
        pulse, noise, or dc. Amplitude is peak-to-peak volts.
        """

    @abstractmethod
    def set_load(self, channel: int, ohms: float) -> None:
        """Set the output load assumption in ohms (use ``math.inf`` for High-Z)."""

    @abstractmethod
    def output(self, channel: int, enabled: bool) -> None:
        """Enable or disable an output channel."""

    @abstractmethod
    def query_channel(self, channel: int) -> dict:
        """Return the current waveform, amplitude, and output state."""

    @abstractmethod
    def max_sine_vpp(self, frequency_hz: float) -> float:
        """Largest sine Vpp allowed at this frequency for the current load."""
