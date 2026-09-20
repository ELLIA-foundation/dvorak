"""Rigol MSO1104Z LAN driver (MSO1000Z / DS1000Z series).

Uses deep-memory RAW mode with binary (BYTE) transfers in official-size
chunks (250000 points). RAW :WAVeform:DATA? hangs on the port-5555 SOCKET
transport on this firmware; VXI-11 (INSTR) is required for downloads.
"""

from __future__ import annotations

import time
from datetime import datetime

import numpy as np
import pyvisa

from instruments.oscilloscope import Oscilloscope
from lib.waveform import WaveformCapture

DEFAULT_CHANNEL = 1
DEFAULT_CHUNK_SIZE = 250_000
DEFAULT_MAX_RETRIES = 5
HORIZONTAL_DIVISIONS = 12
SCREEN_COUNTS_PER_DIV = 25
BYTE_MIDSCALE = 127
STANDARD_MEMORY_DEPTHS = (12_000, 120_000, 1_200_000, 6_000_000, 12_000_000, 24_000_000)


def _resource_candidates(ip: str) -> list[str]:
    return [
        f"TCPIP0::{ip}::5555::SOCKET",
        f"TCPIP0::{ip}::INSTR",
    ]


def _query_float(scope, command: str) -> float:
    return float(scope.query(command).strip())


class RigolMSO1104(Oscilloscope):
    model_id = "rigol_mso1104"

    def __init__(self, connection: dict | None = None) -> None:
        super().__init__(connection)
        self._rm = None
        self._scope = None
        self.resource_name = ""
        self._idn = ""
        self._prefer_instr = False

    @property
    def ip(self) -> str:
        ip = self.connection.get("ip")
        if not ip:
            raise ValueError("rigol_mso1104 connection is missing 'ip' (see instruments/lab.json)")
        return str(ip)

    @property
    def visa(self):
        if self._scope is None:
            raise RuntimeError("Scope is not connected")
        return self._scope

    def connect(self, timeout_ms: int = 5000, prefer_instr: bool = False) -> None:
        if self._scope is not None and self._prefer_instr == prefer_instr:
            return
        self.close()

        candidates = _resource_candidates(self.ip)
        if prefer_instr:
            candidates = list(reversed(candidates))

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
                self._prefer_instr = prefer_instr
                return
            except Exception as exc:
                last_error = exc
                continue
        rm.close()
        raise RuntimeError(f"Could not open Rigol MSO1104Z at {self.ip}: {last_error}")

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
        self._prefer_instr = False

    def identify(self) -> str:
        if self._scope is None:
            self.connect()
        self._idn = self.visa.query("*IDN?").strip()
        return self._idn

    def capture_channel(
        self,
        channel: int = DEFAULT_CHANNEL,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        max_retries: int = DEFAULT_MAX_RETRIES,
        **kwargs,
    ) -> WaveformCapture:
        del kwargs
        if chunk_size > DEFAULT_CHUNK_SIZE:
            chunk_size = DEFAULT_CHUNK_SIZE

        self.connect(timeout_ms=120_000, prefer_instr=True)
        scope = self.visa

        _ensure_stopped(scope)
        print("Acquisition is stopped; switching to RAW mode")
        _enable_raw_mode(scope, channel)

        total_points, memory_depth = _resolve_raw_points(scope)
        if total_points <= 0:
            raise RuntimeError("Scope reported zero waveform points")
        print(f"Memory depth: {memory_depth} ({total_points:,} points)")

        scope.write(":WAVeform:STARt 1")
        scope.write(f":WAVeform:STOP {min(total_points, chunk_size)}")
        preamble = _parse_preamble(scope.query(":WAVeform:PRE?").strip())
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

        raw = np.asarray(raw_values, dtype=np.uint8)
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
            sample_rate_hz=_query_float(scope, ":ACQ:SRAT?"),
            points=int(len(raw)),
            captured_at=datetime.now().isoformat(timespec="seconds"),
            extra={
                "resource": self.resource_name,
                "mode": "RAW",
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
    """Parse :WAVeform:PRE? for the DS1000Z / MSO1000Z series."""
    parts = [part.strip() for part in preamble.split(",")]
    if len(parts) < 10:
        raise ValueError(f"Unexpected waveform preamble: {preamble!r}")

    return {
        "format_code": int(parts[0]),
        "type_code": int(parts[1]),
        "points": int(parts[2]),
        "count": int(parts[3]),
        "x_increment": float(parts[4]),
        "x_origin": float(parts[5]),
        "x_reference": int(parts[6]),
        "y_increment": float(parts[7]),
        "y_origin": float(parts[8]),
        "y_reference": int(parts[9]),
    }


def _expected_memory_points(scope) -> tuple[int, str]:
    """Estimate record length from sample rate and timebase when MDEP is AUTO."""
    raw = scope.query(":ACQ:MDEP?").strip()
    sample_rate = _query_float(scope, ":ACQ:SRAT?")
    timebase = _query_float(scope, ":TIMebase:MAIN:SCALe?")
    computed = int(round(sample_rate * timebase * HORIZONTAL_DIVISIONS))
    if raw.upper() != "AUTO":
        return int(float(raw)), raw
    return computed, raw


def _request_raw_window(scope, points: int) -> int:
    """Set RAW start/stop. Do not overshoot: this firmware may clamp to 2.4 Mpts."""
    scope.write(":WAVeform:STARt 1")
    scope.write(f":WAVeform:STOP {points}")
    time.sleep(0.05)
    return int(float(scope.query(":WAVeform:STOP?").strip()))


def _resolve_raw_points(scope) -> tuple[int, str]:
    expected, memory_depth = _expected_memory_points(scope)
    accepted = _request_raw_window(scope, expected)
    if accepted >= expected:
        return accepted, memory_depth

    for depth in STANDARD_MEMORY_DEPTHS:
        if depth <= expected:
            continue
        accepted = _request_raw_window(scope, depth)
        if accepted >= expected:
            return accepted, memory_depth
        if accepted > expected:
            return accepted, memory_depth

    return max(accepted, expected), memory_depth


def _enable_raw_mode(scope, channel: int) -> None:
    """Switch to RAW. A start/stop window is required or the mode stays NORM."""
    scope.write(f":WAVeform:SOURce CHAN{channel}")
    scope.write(":WAVeform:FORMat BYTE")
    scope.write(":WAVeform:MODE RAW")
    scope.write(":WAVeform:STARt 1")
    scope.write(":WAVeform:STOP 2")
    time.sleep(0.05)
    mode = scope.query(":WAVeform:MODE?").strip().upper()
    if mode != "RAW":
        raise RuntimeError(f"Could not enter RAW waveform mode (got {mode!r})")


def _vertical_scale(scope, preamble: dict, channel: int) -> tuple[float, float, int, str]:
    """Return (y_inc, y_orig, y_ref, source).

    RAW :WAVeform:PRE? on this firmware can report YREFerence=305, which is
    invalid for BYTE data. The programming guide says YREFerence is always 127
    (screen bottom=0, top=255) and NORMal YINCrement = VerticalScale/25.
    """
    y_inc = float(preamble["y_increment"])
    y_orig = float(preamble["y_origin"])
    y_ref = int(preamble["y_reference"])
    if 0 <= y_ref <= 255:
        return y_inc, y_orig, y_ref, "preamble"

    scale = _query_float(scope, f":CHANnel{channel}:SCALe?")
    offset = _query_float(scope, f":CHANnel{channel}:OFFSet?")
    y_inc = scale / SCREEN_COUNTS_PER_DIV
    y_ref = BYTE_MIDSCALE
    y_orig = offset / y_inc if y_inc else 0.0
    return y_inc, y_orig, y_ref, "channel_scale/25"


def _read_chunk(scope, start: int, stop: int, max_retries: int) -> list[int]:
    expected = stop - start + 1
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            scope.write(f":WAVeform:STARt {start}")
            scope.write(f":WAVeform:STOP {stop}")
            chunk = scope.query_binary_values(
                ":WAVeform:DATA?",
                datatype="B",
                container=list,
                header_fmt="ieee",
                expect_termination=True,
            )
            if not chunk:
                raise RuntimeError(f"Chunk {start}-{stop}: empty response")
            if len(chunk) > expected:
                chunk = chunk[:expected]
            return chunk
        except Exception as exc:
            last_error = exc
            time.sleep(0.1 * attempt)
    raise RuntimeError(f"Failed to read chunk {start}-{stop}: {last_error}") from last_error
