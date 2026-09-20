"""Rigol MHO954 LAN driver (MHO900 series).

12-bit mixed-signal scope. Waveform download uses RAW mode with binary WORD
(little-endian uint16) transfers. Empty :WAVeform:DATA? means no acquisition is
in memory yet — RUN or SINGLE first, then STOP.
"""

from __future__ import annotations

import math
import time
from datetime import datetime

import numpy as np
import pyvisa

from instruments.oscilloscope import Oscilloscope
from lib.waveform import WaveformCapture

DEFAULT_CHANNEL = 1
DEFAULT_CHUNK_SIZE = 250_000
DEFAULT_MAX_RETRIES = 5
HORIZONTAL_DIVISIONS = 10
VERTICAL_DIVISIONS = 8
WORD_COUNTS_PER_DIV = 7500
WORD_MIDSCALE = 32768
MIN_TIMEBASE_S_DIV = 500e-12
MIN_VERTICAL_V_DIV = 1e-3
MAX_VERTICAL_V_DIV = 10.0
INVALID_MEASURE = 9.9e37
SINE_CYCLES_ON_SCREEN = 8
SINE_VERTICAL_DIVS = 2


def _resource_candidates(ip: str) -> list[str]:
    return [
        f"TCPIP0::{ip}::INSTR",
        f"TCPIP0::{ip}::5555::SOCKET",
    ]


def _query_float(scope, command: str) -> float:
    return float(scope.query(command).strip())


def _is_invalid_measure(value: float) -> bool:
    return not math.isfinite(value) or abs(value) >= INVALID_MEASURE * 0.5


class RigolMHO954(Oscilloscope):
    model_id = "rigol_mho954"

    def __init__(self, connection: dict | None = None) -> None:
        super().__init__(connection)
        self._rm = None
        self._scope = None
        self.resource_name = ""
        self._idn = ""

    @property
    def ip(self) -> str:
        ip = self.connection.get("ip")
        if not ip:
            raise ValueError("rigol_mho954 connection is missing 'ip' (see instruments/lab.json)")
        return str(ip)

    @property
    def visa(self):
        if self._scope is None:
            raise RuntimeError("Scope is not connected")
        return self._scope

    def connect(self, timeout_ms: int = 5000, prefer_instr: bool = True) -> None:
        del prefer_instr
        if self._scope is not None:
            return

        candidates = _resource_candidates(self.ip)
        rm = pyvisa.ResourceManager("@py")
        last_error: Exception | None = None
        for resource in candidates:
            try:
                scope = rm.open_resource(resource)
                scope.timeout = timeout_ms
                if resource.endswith("SOCKET"):
                    scope.write_termination = "\n"
                    scope.read_termination = "\n"
                idn = scope.query("*IDN?").strip()
                self._rm = rm
                self._scope = scope
                self.resource_name = resource
                self._idn = idn
                return
            except Exception as exc:
                last_error = exc
                continue
        rm.close()
        raise RuntimeError(f"Could not open Rigol MHO954 at {self.ip}: {last_error}")

    def close(self) -> None:
        if self._scope is not None:
            try:
                self._scope.close()
            except Exception:
                pass
            self._scope = None
        if self._rm is not None:
            try:
                self._rm.close()
            except Exception:
                pass
            self._rm = None
        self.resource_name = ""
        self._idn = ""

    def identify(self) -> str:
        if self._scope is None:
            self.connect()
        self._idn = self.visa.query("*IDN?").strip()
        return self._idn

    def prepare_sine(
        self,
        channel: int,
        frequency_hz: float,
        expected_vpp: float,
    ) -> None:
        _require_analog_channel(channel)
        if frequency_hz <= 0:
            raise ValueError("frequency_hz must be positive")
        if expected_vpp <= 0:
            raise ValueError("expected_vpp must be positive")
        if self._scope is None:
            self.connect()
        scope = self.visa
        vdiv = max(expected_vpp / SINE_VERTICAL_DIVS, MIN_VERTICAL_V_DIV)
        tdiv = SINE_CYCLES_ON_SCREEN / (HORIZONTAL_DIVISIONS * frequency_hz)
        tdiv = max(tdiv, MIN_TIMEBASE_S_DIV)
        scope.write(f":CHANnel{channel}:DISPlay ON")
        scope.write(f":CHANnel{channel}:SCALe {vdiv}")
        scope.write(f":CHANnel{channel}:OFFSet 0")
        scope.write(f":TIMebase:MAIN:SCALe {tdiv}")
        scope.write(":TRIGger:MODE EDGE")
        scope.write(f":TRIGger:EDGe:SOURce CHANnel{channel}")
        scope.write(":TRIGger:EDGe:SLOPe POSitive")
        scope.write(":TRIGger:EDGe:LEVel 0")
        scope.write(":TRIGger:SWEep AUTO")
        scope.write(":RUN")

    def measure_vpp(self, channel: int) -> float:
        value = self._measure_item("VPP", channel)
        if not _is_invalid_measure(value):
            return value
        for _ in range(4):
            if not self._coarsen_vertical(channel):
                break
            time.sleep(0.15)
            value = self._measure_item("VPP", channel)
            if not _is_invalid_measure(value):
                return value
        return float("nan")

    def measure_frequency(self, channel: int) -> float:
        return self._measure_item("FREQuency", channel)

    def _coarsen_vertical(self, channel: int) -> bool:
        """Increase V/div when the trace is clipped so VPP becomes valid."""
        current = _query_float(self.visa, f":CHANnel{channel}:SCALe?")
        if current >= MAX_VERTICAL_V_DIV:
            return False
        self.visa.write(f":CHANnel{channel}:SCALe {min(current * 5.0, MAX_VERTICAL_V_DIV)}")
        return True

    def _measure_item(self, item: str, channel: int) -> float:
        _require_analog_channel(channel)
        if self._scope is None:
            self.connect()
        try:
            value = self._query_measure(item, channel)
        except pyvisa.VisaIOError:
            self.close()
            self.connect(timeout_ms=10_000)
            value = self._query_measure(item, channel)
        for _ in range(3):
            if not _is_invalid_measure(value):
                return value
            time.sleep(0.15)
            value = self._query_measure(item, channel)
        return float("nan")

    def _query_measure(self, item: str, channel: int) -> float:
        scope = self.visa
        previous = scope.timeout
        scope.timeout = max(previous, 10_000)
        try:
            scope.write(f":MEASure:ITEM {item},CHANnel{channel}")
            raw = scope.query(f":MEASure:ITEM? {item},CHANnel{channel}").strip()
            return float(raw)
        finally:
            scope.timeout = previous

    def capture_channel(
        self,
        channel: int = DEFAULT_CHANNEL,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        max_retries: int = DEFAULT_MAX_RETRIES,
        **kwargs,
    ) -> WaveformCapture:
        del kwargs
        _require_analog_channel(channel)
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if chunk_size > DEFAULT_CHUNK_SIZE:
            chunk_size = DEFAULT_CHUNK_SIZE

        self.connect(timeout_ms=120_000, prefer_instr=True)
        scope = self.visa

        _ensure_stopped(scope)
        print("Acquisition is stopped; switching to RAW WORD mode")
        _enable_raw_mode(scope, channel)

        total_points, memory_depth = _resolve_raw_points(scope)
        if total_points <= 0:
            raise RuntimeError(
                "Scope reported zero waveform points. Acquire a trace (RUN or SINGLE) before capture."
            )
        print(f"Memory depth: {memory_depth} ({total_points:,} points)")

        scope.write(":WAVeform:STARt 1")
        scope.write(f":WAVeform:STOP {min(total_points, chunk_size)}")
        preamble = _parse_preamble(scope.query(":WAVeform:PREamble?").strip())
        y_inc, y_orig, y_ref, y_source = _vertical_scale(scope, preamble, channel)
        print(
            f"Preamble: mode={preamble['type_code']} "
            f"xinc={preamble['x_increment']:.6e} s "
            f"yinc={y_inc:.6e} V ({y_source}, yref={y_ref})"
        )

        raw_values: list[int] = []
        start = 1
        while start <= total_points:
            stop = min(start + chunk_size - 1, total_points)
            chunk = _read_chunk(scope, start, stop, max_retries)
            raw_values.extend(chunk)
            print(
                f"  read points {start}-{start + len(chunk) - 1} "
                f"({len(raw_values):,}/{total_points:,})"
            )
            if len(chunk) < stop - start + 1:
                break
            start += len(chunk)

        if not raw_values:
            raise RuntimeError(
                "WAVE:DATA? returned no samples. Acquire a trace (RUN or SINGLE), STOP, then capture."
            )

        raw = np.asarray(raw_values, dtype=np.uint16)
        x_inc = preamble["x_increment"]
        x_orig = preamble["x_origin"]
        x_ref = preamble["x_reference"]
        indices = np.arange(len(raw), dtype=np.float64)
        time_s = (indices - x_ref) * x_inc + x_orig
        voltage_v = (raw.astype(np.float64) - y_orig - y_ref) * y_inc

        return WaveformCapture(
            time_s=time_s,
            voltage_v=voltage_v,
            idn=scope.query("*IDN?").strip(),
            model_id=self.model_id,
            channel=channel,
            sample_rate_hz=_query_float(scope, ":ACQuire:SRATe?"),
            points=int(len(raw)),
            captured_at=datetime.now().isoformat(timespec="seconds"),
            extra={
                "resource": self.resource_name,
                "mode": "RAW",
                "format": "WORD",
                "memory_depth": memory_depth,
                "timebase_s_div": _query_float(scope, ":TIMebase:MAIN:SCALe?"),
                "time_offset_s": _query_float(scope, ":TIMebase:MAIN:OFFSet?"),
                "channel_scale_v_div": _query_float(scope, f":CHANnel{channel}:SCALe?"),
                "channel_offset_v": _query_float(scope, f":CHANnel{channel}:OFFSet?"),
                "probe_ratio": _query_float(scope, f":CHANnel{channel}:PROBe?"),
                "x_increment_s": x_inc,
                "x_origin_s": x_orig,
                "x_reference": x_ref,
                "y_increment_v": y_inc,
                "y_origin_v": y_orig,
                "y_reference": y_ref,
                "y_scale_source": y_source,
                "trigger_status": scope.query(":TRIGger:STATus?").strip(),
            },
        )


def _require_analog_channel(channel: int) -> None:
    if channel not in (1, 2, 3, 4):
        raise ValueError("MHO954 analog channels are 1-4")


def _ensure_stopped(scope) -> None:
    """Leave an already-stopped acquisition in place; stop only if running."""
    status = scope.query(":TRIGger:STATus?").strip().upper()
    if "STOP" in status:
        return
    scope.write(":STOP")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        status = scope.query(":TRIGger:STATus?").strip().upper()
        if "STOP" in status:
            return
        time.sleep(0.05)
    raise TimeoutError("Timed out waiting for the scope to stop")


def _parse_preamble(preamble: str) -> dict:
    """Parse :WAVeform:PREamble? for the MHO900 series."""
    parts = [part.strip() for part in preamble.split(",")]
    if len(parts) < 10:
        raise ValueError(f"Unexpected waveform preamble: {preamble!r}")

    return {
        "format_code": int(float(parts[0])),
        "type_code": int(float(parts[1])),
        "points": int(float(parts[2])),
        "count": int(float(parts[3])),
        "x_increment": float(parts[4]),
        "x_origin": float(parts[5]),
        "x_reference": float(parts[6]),
        "y_increment": float(parts[7]),
        "y_origin": float(parts[8]),
        "y_reference": float(parts[9]),
    }


def _memory_points(scope) -> tuple[int, str]:
    """Current record length. Does not raise memory depth."""
    raw = scope.query(":ACQuire:MDEPth?").strip()
    sample_rate = _query_float(scope, ":ACQuire:SRATe?")
    timebase = _query_float(scope, ":TIMebase:MAIN:SCALe?")
    computed = int(round(sample_rate * timebase * HORIZONTAL_DIVISIONS))
    if raw.upper() == "AUTO":
        return max(computed, 1), raw
    return max(int(float(raw)), 1), raw


def _resolve_raw_points(scope) -> tuple[int, str]:
    expected, memory_depth = _memory_points(scope)
    scope.write(":WAVeform:STARt 1")
    scope.write(f":WAVeform:STOP {expected}")
    time.sleep(0.05)
    accepted = int(float(scope.query(":WAVeform:STOP?").strip()))
    return max(accepted, 1), memory_depth


def _enable_raw_mode(scope, channel: int) -> None:
    scope.write(f":WAVeform:SOURce CHANnel{channel}")
    scope.write(":WAVeform:FORMat WORD")
    scope.write(":WAVeform:MODE RAW")
    scope.write(":WAVeform:STARt 1")
    scope.write(":WAVeform:STOP 2")
    time.sleep(0.05)
    mode = scope.query(":WAVeform:MODE?").strip().upper()
    if mode != "RAW":
        raise RuntimeError(f"Could not enter RAW waveform mode (got {mode!r})")


def _vertical_scale(scope, preamble: dict, channel: int) -> tuple[float, float, float, str]:
    """Return (y_inc, y_orig, y_ref, source). WORD midscale is 32768."""
    y_inc = float(preamble["y_increment"])
    y_orig = float(preamble["y_origin"])
    y_ref = float(preamble["y_reference"])
    if 0 <= y_ref <= 65535 and y_inc != 0:
        return y_inc, y_orig, y_ref, "preamble"

    scale = _query_float(scope, f":CHANnel{channel}:SCALe?")
    offset = _query_float(scope, f":CHANnel{channel}:OFFSet?")
    y_inc = scale / WORD_COUNTS_PER_DIV
    y_ref = float(WORD_MIDSCALE)
    y_orig = offset / y_inc if y_inc else 0.0
    return y_inc, y_orig, y_ref, "channel_scale/7500"


def _ieee_payload(raw: bytes) -> bytes:
    if not raw.startswith(b"#"):
        raise RuntimeError(f"WAVE:DATA? did not start with an IEEE block header: {raw[:24]!r}")
    nlen = int(chr(raw[1]))
    nbytes = int(raw[2 : 2 + nlen])
    return raw[2 + nlen : 2 + nlen + nbytes]


def _read_word_block(scope) -> list[int]:
    try:
        chunk = scope.query_binary_values(
            ":WAVeform:DATA?",
            datatype="H",
            is_big_endian=False,
            container=list,
            header_fmt="ieee",
            expect_termination=True,
        )
        if chunk:
            return [int(value) for value in chunk]
    except Exception:
        pass
    scope.write(":WAVeform:DATA?")
    payload = _ieee_payload(scope.read_raw())
    if not payload:
        return []
    return np.frombuffer(payload, dtype="<u2").tolist()


def _read_chunk(scope, start: int, stop: int, max_retries: int) -> list[int]:
    expected = stop - start + 1
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            scope.write(f":WAVeform:STARt {start}")
            scope.write(f":WAVeform:STOP {stop}")
            chunk = _read_word_block(scope)
            if not chunk:
                raise RuntimeError(f"Chunk {start}-{stop}: empty response")
            if len(chunk) > expected:
                chunk = chunk[:expected]
            return chunk
        except Exception as exc:
            last_error = exc
            time.sleep(0.1 * attempt)
    raise RuntimeError(f"Failed to read chunk {start}-{stop}: {last_error}") from last_error
