"""Rigol DS4014 LAN driver (DS4000 series, firmware 00.02.03).

8-bit scope, 14 horizontal divisions. This unit is unlocked to the DS4054
limits: 1 ns/div and a 500 MHz analog path (bandwidth limit OFF, or 20/100/200
MHz). The ADC remains 4 GSa/s. On-screen reads are NORMal BYTE records (1400
points). Deep memory is RAW BYTE: stop, then :WAVeform:RESet /
:WAVeform:BEGin / :WAVeform:STATus? / :WAVeform:DATA? / :WAVeform:END. Do not
send a device clear; this firmware can hang on it.

Voltage: (byte - YORigin - YREFerence) * YINCrement, with YREFerence 127 and
YINCrement = V/div / 32.
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
# Rigol's deep-memory note for this family reads at most 1e6 points per transfer.
MAX_BLOCK_POINTS = 1_000_000
DEFAULT_MAX_RETRIES = 5
HORIZONTAL_DIVISIONS = 14
SCREEN_POINTS = 1400
BYTE_COUNTS_PER_DIV = 32
BYTE_MIDSCALE = 127
# Stock DS4014 floor is 5 ns/div. This unit accepts 1 ns/div (DS4054) and
# rejects anything faster.
MIN_TIMEBASE_S_DIV = 1e-9
MAX_TIMEBASE_S_DIV = 1000.0
MIN_VERTICAL_V_DIV = 1e-3
OHM_50_MAX_V_DIV = 1.0
OHM_1M_MAX_V_DIV = 5.0
INVALID_MEASURE = 9.9e37
MIN_FIRMWARE = (0, 2, 3)
SINE_CYCLES_ON_SCREEN = 8
SINE_VERTICAL_DIVS = 2
WINDOW_SCREEN = "screen"
WINDOW_FULL = "full"


def _resource_candidates(ip: str) -> list[str]:
    return [
        f"TCPIP0::{ip}::INSTR",
        f"TCPIP0::{ip}::5555::SOCKET",
    ]


def _query_float(scope, command: str) -> float:
    return float(scope.query(command).strip())


def _is_invalid_measure(value: float) -> bool:
    return not math.isfinite(value) or abs(value) >= INVALID_MEASURE * 0.5


def _firmware_tuple(idn: str) -> tuple[int, ...]:
    parts = [part.strip() for part in idn.split(",")]
    if len(parts) < 4:
        return ()
    numbers: list[int] = []
    for piece in parts[3].split("."):
        if not piece.isdigit():
            break
        numbers.append(int(piece))
    return tuple(numbers)


def _idn_model(idn: str) -> str:
    parts = [part.strip() for part in idn.split(",")]
    if len(parts) < 2:
        return ""
    return parts[1].upper()


def _set_acquire_averages(scope, averages: int) -> None:
    if averages < 1:
        raise ValueError("averages must be at least 1")
    if averages == 1:
        scope.write(":ACQuire:TYPE NORMal")
        return
    if averages > 8192 or (averages & (averages - 1)) != 0:
        raise ValueError("DS4014 averages must be 1 or a power of two from 2 to 8192")
    scope.write(":ACQuire:TYPE AVERages")
    scope.write(f":ACQuire:AVERages {int(averages)}")


def _require_analog_channel(channel: int) -> None:
    if channel not in (1, 2, 3, 4):
        raise ValueError("DS4014 analog channels are 1-4")


class RigolDS4014(Oscilloscope):
    model_id = "rigol_ds4014"

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
            raise ValueError("rigol_ds4014 connection is missing 'ip' (see instruments/lab.json)")
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
                model = _idn_model(idn)
                if model != "DS4014":
                    scope.close()
                    rm.close()
                    raise RuntimeError(f"Expected a Rigol DS4014 at {self.ip}, got {idn!r}")
                firmware = _firmware_tuple(idn)
                if firmware and firmware < MIN_FIRMWARE:
                    shown = ".".join(str(part) for part in firmware)
                    scope.close()
                    rm.close()
                    raise RuntimeError(
                        f"DS4014 firmware {shown} is older than 00.02.03; "
                        "waveform download needs 00.02.03 or newer"
                    )
                self._rm = rm
                self._scope = scope
                self.resource_name = resource
                self._idn = idn
                return
            except RuntimeError:
                raise
            except Exception as exc:
                last_error = exc
                continue
        rm.close()
        raise RuntimeError(f"Could not open Rigol DS4014 at {self.ip}: {last_error}")

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
        averages: int = 1,
    ) -> None:
        _require_analog_channel(channel)
        if frequency_hz <= 0:
            raise ValueError("frequency_hz must be positive")
        if expected_vpp <= 0:
            raise ValueError("expected_vpp must be positive")
        if self._scope is None:
            self.connect()
        scope = self.visa
        v_min, v_max = _vertical_limits(scope, channel)
        vdiv = min(max(expected_vpp / SINE_VERTICAL_DIVS, v_min), v_max)
        tdiv = SINE_CYCLES_ON_SCREEN / (HORIZONTAL_DIVISIONS * frequency_hz)
        tdiv = min(max(tdiv, MIN_TIMEBASE_S_DIV), MAX_TIMEBASE_S_DIV)
        scope.write(f":CHANnel{channel}:DISPlay ON")
        scope.write(f":CHANnel{channel}:SCALe {vdiv}")
        scope.write(f":CHANnel{channel}:OFFSet 0")
        scope.write(f":TIMebase:MAIN:SCALe {tdiv}")
        _set_acquire_averages(scope, averages)
        scope.write(":TRIGger:MODE EDGE")
        scope.write(f":TRIGger:EDGe:SOURce CHANnel{channel}")
        scope.write(":TRIGger:EDGe:SLOPe POSitive")
        scope.write(":TRIGger:EDGe:LEVel 0")
        scope.write(":TRIGger:SWEep AUTO")
        scope.write(":RUN")

    def measure_vpp(self, channel: int, *, allow_rescale: bool = True) -> float:
        value = self._measure_item("VPP", channel)
        if not allow_rescale or not _is_invalid_measure(value):
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

    def measure_phase(self, channel: int, reference: int) -> float:
        _require_analog_channel(channel)
        _require_analog_channel(reference)
        if channel == reference:
            raise ValueError("Phase needs two different channels")
        if self._scope is None:
            self.connect()
        try:
            value = self._query_phase(channel, reference)
        except pyvisa.VisaIOError:
            self.close()
            self.connect(timeout_ms=10_000)
            value = self._query_phase(channel, reference)
        if _is_invalid_measure(value):
            return float("nan")
        return value

    def set_trigger_edge(self, channel: int, level_v: float) -> None:
        _require_analog_channel(channel)
        if self._scope is None:
            self.connect()
        scope = self.visa
        scope.write(":TRIGger:MODE EDGE")
        scope.write(f":TRIGger:EDGe:SOURce CHANnel{channel}")
        scope.write(":TRIGger:EDGe:SLOPe POSitive")
        scope.write(f":TRIGger:EDGe:LEVel {level_v}")

    def set_vertical(self, channel: int, volts_per_div: float, offset_v: float) -> None:
        _require_analog_channel(channel)
        if volts_per_div <= 0:
            raise ValueError("volts_per_div must be positive")
        if self._scope is None:
            self.connect()
        scope = self.visa
        v_min, v_max = _vertical_limits(scope, channel)
        scale = min(max(volts_per_div, v_min), v_max)
        offset = _clamp_offset(scope, channel, scale, offset_v)
        scope.write(f":CHANnel{channel}:DISPlay ON")
        scope.write(f":CHANnel{channel}:SCALe {scale}")
        scope.write(f":CHANnel{channel}:OFFSet {offset}")

    def measure_voltage_span(self, channel: int) -> tuple[float, float, float]:
        return (
            self._measure_item("VMIN", channel),
            self._measure_item("VMAX", channel),
            self._measure_item("VAVG", channel),
        )

    def read_screen(self, channel: int) -> tuple[np.ndarray, np.ndarray]:
        _require_analog_channel(channel)
        if self._scope is None:
            self.connect()
        scope = self.visa
        _ensure_stopped(scope)
        try:
            return _download_norm_trace(scope, channel)
        finally:
            try:
                scope.write(":RUN")
            except Exception:
                pass

    def _coarsen_vertical(self, channel: int) -> bool:
        """Increase V/div when the trace is clipped so VPP becomes valid."""
        scope = self.visa
        current = _query_float(scope, f":CHANnel{channel}:SCALe?")
        _v_min, v_max = _vertical_limits(scope, channel)
        if current >= v_max * 0.99:
            return False
        scope.write(f":CHANnel{channel}:SCALe {min(current * 5.0, v_max)}")
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
            raw = scope.query(f":MEASure:{item}? CHANnel{channel}").strip()
            return float(raw)
        finally:
            scope.timeout = previous

    def _query_phase(self, channel: int, reference: int) -> float:
        scope = self.visa
        previous = scope.timeout
        scope.timeout = max(previous, 10_000)
        try:
            raw = scope.query(
                f":MEASure:RPHase? CHANnel{channel},CHANnel{reference}"
            ).strip()
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
        window_mode = str(kwargs.pop("window", WINDOW_SCREEN)).strip().lower()
        if window_mode not in {WINDOW_SCREEN, WINDOW_FULL}:
            raise ValueError("window must be 'screen' or 'full'")
        if kwargs:
            raise TypeError(f"Unexpected capture arguments: {sorted(kwargs)}")
        _require_analog_channel(channel)
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        chunk_size = min(chunk_size, MAX_BLOCK_POINTS)
        if max_retries < 1:
            raise ValueError("max_retries must be at least 1")

        self.connect(timeout_ms=120_000, prefer_instr=True)
        scope = self.visa
        _ensure_stopped(scope)
        print("Acquisition is stopped; switching to RAW BYTE mode")
        preamble, memory_points, memory_depth = _prepare_raw_record(scope, channel)
        if memory_points <= 0:
            raise RuntimeError(
                "Scope reported zero waveform points. Acquire a trace (RUN or SINGLE) before capture."
            )
        time_div = _query_float(scope, ":TIMebase:MAIN:SCALe?")
        time_offset = _query_float(scope, ":TIMebase:MAIN:OFFSet?")
        x_inc = float(preamble["x_increment"])
        x_orig = float(preamble["x_origin"])
        x_ref = float(preamble["x_reference"])
        sample_rate = _query_float(scope, ":ACQuire:SRATe?")
        if sample_rate > 0 and (x_inc <= 0 or not math.isclose(x_inc, 1.0 / sample_rate, rel_tol=0.05)):
            x_inc = 1.0 / sample_rate
        y_inc, y_orig, y_ref, y_source = _vertical_scale(scope, preamble, channel)
        if window_mode == WINDOW_SCREEN:
            start, stop = _screen_indices(
                time_div, time_offset, x_orig, x_inc, memory_points
            )
        else:
            start, stop = 1, memory_points
        print(
            f"Memory depth: {memory_depth} ({memory_points:,} points, "
            f"dt={x_inc:.6e} s); reading {start:,}:{stop:,} ({window_mode})"
        )
        print(
            f"Preamble: yinc={y_inc:.6e} V ({y_source}, yref={y_ref}) "
            f"xorigin={x_orig:.6e} s"
        )
        raw_values = _download_raw_range(scope, start, stop, chunk_size, max_retries)
        raw = np.asarray(raw_values, dtype=np.float64)
        time_s = (np.arange(len(raw), dtype=np.float64) + (start - 1) - x_ref) * x_inc + x_orig
        voltage_v = (raw - y_orig - y_ref) * y_inc
        sample_rate_hz = (1.0 / x_inc) if x_inc else 0.0
        return WaveformCapture(
            time_s=time_s,
            voltage_v=voltage_v,
            idn=scope.query("*IDN?").strip(),
            model_id=self.model_id,
            channel=channel,
            sample_rate_hz=sample_rate_hz,
            points=int(len(raw)),
            captured_at=datetime.now().isoformat(timespec="seconds"),
            extra={
                "resource": self.resource_name,
                "mode": "RAW",
                "format": "BYTE",
                "window": window_mode,
                "memory_depth": memory_depth,
                "memory_points": memory_points,
                "raw_start": start,
                "raw_stop": stop,
                "timebase_s_div": time_div,
                "time_offset_s": time_offset,
                "channel_scale_v_div": _query_float(scope, f":CHANnel{channel}:SCALe?"),
                "channel_offset_v": _query_float(scope, f":CHANnel{channel}:OFFSet?"),
                "probe_ratio": _query_float(scope, f":CHANnel{channel}:PROBe?"),
                "x_increment_s": x_inc,
                "x_origin_s": float(time_s[0]) if len(time_s) else x_orig,
                "x_reference": x_ref,
                "y_increment_v": y_inc,
                "y_origin_v": y_orig,
                "y_reference": y_ref,
                "y_scale_source": y_source,
                "trigger_status": scope.query(":TRIGger:STATus?").strip(),
            },
        )


def _vertical_limits(scope, channel: int) -> tuple[float, float]:
    """Return (min, max) displayed V/div for this channel's impedance and probe."""
    impedance = scope.query(f":CHANnel{channel}:IMPedance?").strip().upper()
    probe = _query_float(scope, f":CHANnel{channel}:PROBe?")
    if probe <= 0:
        probe = 1.0
    base_max = OHM_50_MAX_V_DIV if impedance.startswith("FIFT") else OHM_1M_MAX_V_DIV
    return MIN_VERTICAL_V_DIV * probe, base_max * probe


def _clamp_offset(scope, channel: int, scale: float, offset_v: float) -> float:
    """Keep the offset inside the range this V/div and impedance accept."""
    impedance = scope.query(f":CHANnel{channel}:IMPedance?").strip().upper()
    probe = _query_float(scope, f":CHANnel{channel}:PROBe?")
    if probe <= 0:
        probe = 1.0
    scale_1x = scale / probe
    if impedance.startswith("FIFT"):
        limit = (1.2 if scale_1x <= 0.124 else 12.0) * probe
    else:
        limit = (2.0 if scale_1x <= 0.225 else 40.0) * probe
    return min(max(offset_v, -limit), limit)


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
    """Parse :WAVeform:PREamble? for the DS4000 series."""
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


def _acquired_points(scope) -> tuple[int, str]:
    """Record length currently in memory. Ignores a stale NORMal point count."""
    raw = scope.query(":ACQuire:MDEPth?").strip()
    if raw.upper() == "AUTO":
        sample_rate = _query_float(scope, ":ACQuire:SRATe?")
        timebase = _query_float(scope, ":TIMebase:MAIN:SCALe?")
        computed = int(round(sample_rate * timebase * HORIZONTAL_DIVISIONS))
        return max(computed, 1), raw
    return max(int(float(raw)), 1), raw


def _prepare_raw_record(scope, channel: int) -> tuple[dict, int, str]:
    """Enter RAW and return a preamble that describes the acquired record.

    A preceding NORMal read leaves :WAVeform:POINts at 1400. That count must
    not be used as the memory length. Pin the read to :ACQuire:MDEPth first.
    """
    scope.write(f":WAVeform:SOURce CHANnel{channel}")
    scope.write(":WAVeform:FORMat BYTE")
    scope.write(":WAVeform:MODE RAW")
    time.sleep(0.05)
    mode = scope.query(":WAVeform:MODE?").strip().upper()
    if not mode.startswith("RAW"):
        raise RuntimeError(f"Could not enter RAW waveform mode (got {mode!r})")
    memory_points, memory_depth = _acquired_points(scope)
    scope.write(":WAVeform:STARt 1")
    scope.write(f":WAVeform:STOP {memory_points}")
    time.sleep(0.05)
    preamble = _parse_preamble(scope.query(":WAVeform:PREamble?").strip())
    if int(preamble["type_code"]) != 2:
        time.sleep(0.1)
        preamble = _parse_preamble(scope.query(":WAVeform:PREamble?").strip())
    if int(preamble["type_code"]) != 2:
        raise RuntimeError(
            f"RAW preamble did not follow the mode switch (type={preamble['type_code']})"
        )
    return preamble, memory_points, memory_depth


def _screen_indices(
    time_div: float,
    time_offset: float,
    x_origin: float,
    x_inc: float,
    memory_points: int,
) -> tuple[int, int]:
    """1-based inclusive RAW indices covering the 14-division screen."""
    span = time_div * HORIZONTAL_DIVISIONS
    t_left = time_offset - span / 2.0
    t_right = time_offset + span / 2.0
    start = int(round((t_left - x_origin) / x_inc)) + 1
    stop = int(round((t_right - x_origin) / x_inc))
    start = max(1, min(start, memory_points))
    stop = max(start, min(stop, memory_points))
    return start, stop


def _vertical_scale(scope, preamble: dict, channel: int) -> tuple[float, float, float, str]:
    """Return (y_inc, y_orig, y_ref, source). BYTE midscale is 127."""
    y_inc = float(preamble["y_increment"])
    y_orig = float(preamble["y_origin"])
    y_ref = float(preamble["y_reference"])
    if 0 <= y_ref <= 255 and y_inc != 0:
        return y_inc, y_orig, y_ref, "preamble"
    scale = _query_float(scope, f":CHANnel{channel}:SCALe?")
    offset = _query_float(scope, f":CHANnel{channel}:OFFSet?")
    y_inc = scale / BYTE_COUNTS_PER_DIV
    y_ref = float(BYTE_MIDSCALE)
    y_orig = offset / y_inc if y_inc else 0.0
    return y_inc, y_orig, y_ref, "channel_scale/32"


def _download_norm_trace(scope, channel: int) -> tuple[np.ndarray, np.ndarray]:
    """On-screen NORM BYTE download. Does not enter RAW."""
    scope.write(f":WAVeform:SOURce CHANnel{channel}")
    scope.write(":WAVeform:FORMat BYTE")
    scope.write(":WAVeform:MODE NORM")
    scope.write(":WAVeform:STARt 1")
    scope.write(f":WAVeform:STOP {SCREEN_POINTS}")
    time.sleep(0.05)
    mode = scope.query(":WAVeform:MODE?").strip().upper()
    if not mode.startswith("NORM"):
        raise RuntimeError(f"Could not enter NORM waveform mode (got {mode!r})")
    preamble = _parse_preamble(scope.query(":WAVeform:PREamble?").strip())
    raw_values = _read_byte_block(scope)
    if len(raw_values) < 8:
        raise RuntimeError("On-screen NORM download returned no samples")
    if len(raw_values) > SCREEN_POINTS:
        raw_values = raw_values[:SCREEN_POINTS]
    raw = np.asarray(raw_values, dtype=np.float64)
    y_inc, y_orig, y_ref, _source = _vertical_scale(scope, preamble, channel)
    x_inc = float(preamble["x_increment"])
    x_orig = float(preamble["x_origin"])
    x_ref = float(preamble["x_reference"])
    time_s = (np.arange(len(raw), dtype=np.float64) - x_ref) * x_inc + x_orig
    voltage_v = (raw - y_orig - y_ref) * y_inc
    return time_s, voltage_v


def _ieee_payload(raw: bytes) -> bytes:
    if not raw.startswith(b"#"):
        raise RuntimeError(f"WAVE:DATA? did not start with an IEEE block header: {raw[:24]!r}")
    nlen = int(chr(raw[1]))
    nbytes = int(raw[2 : 2 + nlen])
    return raw[2 + nlen : 2 + nlen + nbytes]


def _read_byte_block(scope) -> list[int]:
    chunk = scope.query_binary_values(
        ":WAVeform:DATA?",
        datatype="B",
        container=list,
        header_fmt="ieee",
        expect_termination=True,
    )
    return [int(value) for value in chunk]


def _download_raw_range(
    scope,
    start: int,
    stop: int,
    chunk_size: int,
    max_retries: int,
) -> list[int]:
    values: list[int] = []
    cursor = start
    total = stop - start + 1
    while cursor <= stop:
        chunk_stop = min(cursor + chunk_size - 1, stop)
        block = _read_raw_block(scope, cursor, chunk_stop, max_retries)
        values.extend(block)
        print(
            f"  read points {cursor}-{cursor + len(block) - 1} "
            f"({len(values):,}/{total:,})"
        )
        cursor += len(block)
    if len(values) != total:
        raise RuntimeError(
            f"RAW window {start}-{stop} returned {len(values)} points, expected {total}"
        )
    return values


def _read_raw_block(scope, start: int, stop: int, max_retries: int) -> list[int]:
    expected = stop - start + 1
    last_error: Exception | None = None
    previous = scope.timeout
    scope.timeout = max(previous, 120_000)
    try:
        for attempt in range(1, max_retries + 1):
            try:
                return _read_raw_block_once(scope, start, stop, expected)
            except Exception as exc:
                last_error = exc
                time.sleep(0.1 * attempt)
        raise RuntimeError(f"Failed to read RAW {start}-{stop}: {last_error}") from last_error
    finally:
        scope.timeout = previous


def _read_raw_block_once(scope, start: int, stop: int, expected: int) -> list[int]:
    scope.write(f":WAVeform:STARt {start}")
    scope.write(f":WAVeform:STOP {stop}")
    scope.write(":WAVeform:RESet")
    scope.write(":WAVeform:BEGin")
    collected: list[int] = []
    empty_reads = 0
    try:
        while len(collected) < expected:
            status = scope.query(":WAVeform:STATus?").strip()
            kind = status.split(",")[0].strip().upper()
            block = _read_byte_block(scope)
            if block:
                collected.extend(block)
                empty_reads = 0
            else:
                empty_reads += 1
            if kind == "IDLE" or empty_reads > 20:
                break
            if not block:
                time.sleep(0.05)
    finally:
        try:
            scope.write(":WAVeform:END")
        except Exception:
            pass
    if len(collected) > expected:
        collected = collected[:expected]
    if len(collected) != expected:
        raise RuntimeError(
            f"Chunk {start}-{stop}: got {len(collected)} points, expected {expected}"
        )
    return collected
