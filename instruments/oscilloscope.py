"""Oscilloscope role: model-agnostic capture interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

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

    @abstractmethod
    def prepare_sine(
        self,
        channel: int,
        frequency_hz: float,
        expected_vpp: float,
        averages: int = 1,
    ) -> None:
        """Set timebase, vertical scale, and acquire averages for a sine."""

    @abstractmethod
    def measure_vpp(self, channel: int, *, allow_rescale: bool = True) -> float:
        """Peak-to-peak voltage on one analog channel.

        When ``allow_rescale`` is false, a failed reading stays NaN and the
        vertical scale is left unchanged.
        """

    @abstractmethod
    def measure_frequency(self, channel: int) -> float:
        """Measured frequency on one analog channel."""

    @abstractmethod
    def set_vertical(self, channel: int, volts_per_div: float, offset_v: float) -> None:
        """Set one channel's V/div and the voltage at the center of the screen."""

    @abstractmethod
    def measure_voltage_span(self, channel: int) -> tuple[float, float, float]:
        """Return ``(vmin, vmax, vavg)`` in volts. Invalid readings are NaN."""

    @abstractmethod
    def read_screen(self, channel: int) -> tuple[np.ndarray, np.ndarray]:
        """Stop, download the visible trace, and run again.

        Returns ``(time_s, voltage_v)`` for the on-screen record only.
        """
