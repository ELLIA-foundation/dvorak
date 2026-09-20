"""Rigol DG4062 signal-generator stub.

Implements the SignalGenerator role so campaigns can import
``open_generator()``. SCPI control is not implemented yet.
"""

from __future__ import annotations

from instruments.generator import SignalGenerator

_NOT_IMPLEMENTED = "Rigol DG4062 SCPI driver is not implemented yet."


class RigolDG4062(SignalGenerator):
    model_id = "rigol_dg4062"

    def connect(self) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def close(self) -> None:
        return

    def identify(self) -> str:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def set_waveform(
        self,
        channel: int,
        shape: str,
        frequency_hz: float,
        amplitude_vpp: float,
        offset_v: float = 0.0,
    ) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    def output(self, channel: int, enabled: bool) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)
