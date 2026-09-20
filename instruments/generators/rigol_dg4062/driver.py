"""Rigol DG4062 dual-channel function / arbitrary waveform generator (LAN).

DG4000-series SCPI over VISA. Tries raw TCP socket (port 5555) first, then
VXI-11 INSTR. Channel numbers are 1 or 2.
"""

from __future__ import annotations

import math

import pyvisa

from instruments.generator import SignalGenerator

SHAPE_COMMANDS: dict[str, str] = {
    "sine": "SINusoid",
    "sin": "SINusoid",
    "sinusoid": "SINusoid",
    "square": "SQUare",
    "squ": "SQUare",
    "ramp": "RAMP",
    "triangle": "RAMP",
    "tri": "RAMP",
    "pulse": "PULSe",
    "noise": "NOISe",
    "dc": "DC",
}

HIGH_Z = math.inf


def _resource_candidates(ip: str) -> list[str]:
    # INSTR first: SOCKET :OUTPutN? replies with an extra newline and desyncs.
    return [
        f"TCPIP0::{ip}::INSTR",
        f"TCPIP0::{ip}::5555::SOCKET",
    ]


def _channel(channel: int) -> int:
    if channel not in (1, 2):
        raise ValueError("DG4062 channels are 1 and 2")
    return channel


def _parse_apply(raw: str) -> dict[str, float | str | None]:
    text = raw.strip().strip('"')
    parts = [part.strip() for part in text.split(",")]
    while len(parts) < 5:
        parts.append("DEF")

    def _num(value: str) -> float | None:
        if not value or value.upper() == "DEF":
            return None
        return float(value)

    return {
        "shape": parts[0],
        "frequency_hz": _num(parts[1]),
        "amplitude_vpp": _num(parts[2]),
        "offset_v": _num(parts[3]),
        "phase_deg": _num(parts[4]),
    }


def _parse_load(raw: str) -> float:
    text = raw.strip().strip('"').upper()
    if text in {"", "INF", "INFINITY", "9.9E+37"}:
        return HIGH_Z
    return float(text)


class RigolDG4062(SignalGenerator):
    model_id = "rigol_dg4062"

    def __init__(self, connection: dict | None = None) -> None:
        super().__init__(connection)
        self._rm = None
        self._inst = None
        self.resource_name = ""
        self._idn = ""

    @property
    def ip(self) -> str:
        ip = self.connection.get("ip")
        if not ip:
            raise ValueError("rigol_dg4062 connection is missing 'ip' (see instruments/lab.json)")
        return str(ip)

    @property
    def visa(self):
        if self._inst is None:
            raise RuntimeError("Generator is not connected")
        return self._inst

    def connect(self, timeout_ms: int = 5000) -> None:
        if self._inst is not None:
            return
        rm = pyvisa.ResourceManager("@py")
        last_error: Exception | None = None
        for resource in _resource_candidates(self.ip):
            try:
                inst = rm.open_resource(resource)
                inst.timeout = timeout_ms
                if resource.endswith("SOCKET"):
                    inst.write_termination = "\n"
                    inst.read_termination = "\n"
                idn = inst.query("*IDN?").strip()
                self._rm = rm
                self._inst = inst
                self.resource_name = resource
                self._idn = idn
                return
            except Exception as exc:
                last_error = exc
                continue
        rm.close()
        raise RuntimeError(f"Could not open Rigol DG4062 at {self.ip}: {last_error}")

    def close(self) -> None:
        if self._inst is not None:
            try:
                self._inst.close()
            except Exception:
                pass
            self._inst = None
        if self._rm is not None:
            try:
                self._rm.close()
            except Exception:
                pass
            self._rm = None
        self.resource_name = ""
        self._idn = ""

    def identify(self) -> str:
        if self._inst is None:
            self.connect()
        self._idn = self.visa.query("*IDN?").strip()
        return self._idn

    def set_waveform(
        self,
        channel: int,
        shape: str,
        frequency_hz: float,
        amplitude_vpp: float,
        offset_v: float = 0.0,
        phase_deg: float = 0.0,
    ) -> None:
        ch = _channel(channel)
        key = shape.strip().lower()
        if key not in SHAPE_COMMANDS:
            known = ", ".join(sorted(set(SHAPE_COMMANDS)))
            raise ValueError(f"Unknown waveform {shape!r}. Supported: {known}")
        command = SHAPE_COMMANDS[key]
        source = f":SOURce{ch}"

        if command == "DC":
            self.visa.write(f"{source}:FUNCtion DC")
            self.visa.write(f"{source}:VOLTage:OFFSet {offset_v}")
            return
        if command == "NOISe":
            self.visa.write(f"{source}:APPLy:NOISe {amplitude_vpp},{offset_v}")
            return
        if command == "PULSe":
            self.visa.write(
                f"{source}:APPLy:PULSe {frequency_hz},{amplitude_vpp},{offset_v}"
            )
            return
        self.visa.write(
            f"{source}:APPLy:{command} {frequency_hz},{amplitude_vpp},{offset_v},{phase_deg}"
        )

    def set_load(self, channel: int, ohms: float) -> None:
        ch = _channel(channel)
        if math.isinf(ohms):
            self.visa.write(f":OUTPut{ch}:LOAD INFinity")
            return
        self.visa.write(f":OUTPut{ch}:LOAD {ohms}")

    def output(self, channel: int, enabled: bool) -> None:
        ch = _channel(channel)
        state = "ON" if enabled else "OFF"
        self.visa.write(f":OUTPut{ch} {state}")

    def query_channel(self, channel: int) -> dict:
        ch = _channel(channel)
        apply = _parse_apply(self.visa.query(f":SOURce{ch}:APPLy?"))
        load = _parse_load(self.visa.query(f":OUTPut{ch}:LOAD?"))
        output_raw = self.visa.query(f":OUTPut{ch}?").strip().upper()
        return {
            "channel": ch,
            "output": output_raw in {"ON", "1"},
            "load_ohm": load,
            **apply,
        }
