"""Rigol MSO1104Z LAN driver (MSO1000Z / DS1000Z series).

Uses deep-memory RAW mode with binary (BYTE) transfers in official-size
chunks (250000 points). RAW :WAVeform:DATA? hangs on the port-5555 SOCKET
transport on this firmware; VXI-11 (INSTR) is required for downloads.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pyvisa

from instruments.oscilloscope import Oscilloscope
from lib.waveform import WaveformCapture

DEFAULT_CHANNEL = 1
DEFAULT_CHUNK_SIZE = 250_000
DEFAULT_MAX_RETRIES = 5
HORIZONTAL_DIVISIONS = 12
VERTICAL_DIVISIONS = 8
SCREEN_COUNTS_PER_DIV = 25
BYTE_MIDSCALE = 127
# MSO1104Z analog sample rates. AUTO memory is SRAT × 12 × time/div.
KNOWN_SAMPLE_RATES_HZ = (1e9, 5e8, 2.5e8, 1.25e8)
MIN_TIMEBASE_S_DIV = 5e-9
MIN_VERTICAL_V_DIV = 1e-3
MAX_VERTICAL_V_DIV = 10.0
INVALID_MEASURE = 9.9e37
SINE_CYCLES_ON_SCREEN = 8
# Leave headroom: actual Vpp can be ~2x V_nominal (50 ohm setting into High-Z).
SINE_VERTICAL_DIVS = 2
VERIFY_P2P_FRAC = 0.15
VERIFY_LSB = 4.0
VERIFY_NORM_CORR = 0.6
NORM_POINTS = 1200
WINDOW_SCREEN = "screen"
WINDOW_FULL = "full"


def _resource_candidates(ip: str) -> list[str]:
    # INSTR first: SOCKET desyncs after some writes (e.g. :CHANnelN:OFFSet)
    # so the next :MEASure query times out.
    return [
        f"TCPIP0::{ip}::INSTR",
        f"TCPIP0::{ip}::5555::SOCKET",
    ]


def _query_float(scope, command: str) -> float:
    return float(scope.query(command).strip())


def _is_invalid_measure(value: float) -> bool:
    return not math.isfinite(value) or abs(value) >= INVALID_MEASURE * 0.5


@dataclass(frozen=True)
class ScreenWindow:
    """Visible 12-division window from front-panel timebase and channel settings."""

    time_div_s: float
    time_offset_s: float
    t_left_s: float
    t_right_s: float
    span_s: float
    channel_scale_v: float
    channel_offset_v: float
    probe_ratio: float
    sample_rate_hz: float


@dataclass(frozen=True)
class MemoryLayout:
    points: int
    dt_s: float
    t0_s: float
    memory_depth: str


@dataclass(frozen=True)
class NormTrace:
    time_s: np.ndarray
    voltage_v: np.ndarray


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
                self._prefer_instr = resource.endswith("INSTR")
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

    def screenshot(self) -> tuple[bytes, str]:
        """Download the current front-panel image. Returns (bytes, 'png'|'bmp')."""
        if self._scope is None:
            self.connect()
        scope = self.visa
        previous = scope.timeout
        scope.timeout = 60_000
        try:
            payload = scope.query_binary_values(
                ":DISPlay:DATA? ON,PNG",
                datatype="B",
                container=bytes,
                header_fmt="ieee",
            )
        except Exception:
            scope.write(":DISPlay:DATA? ON,PNG")
            time.sleep(0.4)
            raw = scope.read_raw()
            payload = _ieee_payload(raw)
        finally:
            scope.timeout = previous
        data = bytes(payload)
        if data.startswith(b"\x89PNG"):
            return data, "png"
        if data.startswith(b"BM"):
            return data, "bmp"
        raise RuntimeError(f"Screenshot was not PNG or BMP (head={data[:16]!r})")

    def prepare_sine(
        self,
        channel: int,
        frequency_hz: float,
        expected_vpp: float,
    ) -> None:
        if channel not in (1, 2, 3, 4):
            raise ValueError("MSO1104 analog channels are 1-4")
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
        scope.write(f":TRIGger:EDGe:SOURce CHAN{channel}")
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
        if channel not in (1, 2, 3, 4):
            raise ValueError("MSO1104 analog channels are 1-4")
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
            raw = scope.query(f":MEASure:{item}? CHANnel{channel}").strip()
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
        if chunk_size > DEFAULT_CHUNK_SIZE:
            chunk_size = DEFAULT_CHUNK_SIZE

        self.connect(timeout_ms=120_000, prefer_instr=True)
        scope = self.visa

        _ensure_stopped(scope)
        window = _read_screen_window(scope, channel)
        measured = _measure_extrema(scope, channel)
        if measured is None:
            raise RuntimeError(
                "Cannot verify capture: :MEASure VMIN/VMAX is unavailable. "
                "Add those measurements on the scope, then recapture."
            )
        y_inc, y_orig, y_ref = _screen_vertical(window.channel_scale_v, window.channel_offset_v)
        print(
            f"Screen window: {window.t_left_s:.6e} s to {window.t_right_s:.6e} s "
            f"({window.span_s:.6e} s, {window.time_div_s:.6e} s/div, "
            f"D={window.time_offset_s:.6e} s)"
        )
        print(
            f"Screen measure: VMIN={measured[0]:.6g} V  VMAX={measured[1]:.6g} V  "
            f"scale={window.channel_scale_v:.6g} V/div"
        )
        norm = _download_norm_trace(scope, channel)
        if norm is None:
            raise RuntimeError(
                "Cannot verify capture: on-screen NORM download failed. "
                "Stop the acquisition and recapture."
            )
        print(
            f"NORM reference: {len(norm.voltage_v)} pts  "
            f"{float(norm.voltage_v.min()):.6g} V to {float(norm.voltage_v.max()):.6g} V"
        )

        print("Acquisition is stopped; switching to RAW mode")
        _enable_raw_mode(scope, channel)
        memory = _probe_memory(scope, window)
        y_candidates = _vertical_candidates(scope, window, y_inc, y_orig, y_ref)
        print(
            f"Memory: {memory.memory_depth} ({memory.points:,} points, "
            f"dt={memory.dt_s:.6e} s, {memory.points * memory.dt_s:.6e} s)"
        )

        capture = None
        extra_common = {
            "resource": self.resource_name,
                "mode": "RAW",
                "verify_required": True,
            "window": window_mode,
            "memory_depth": memory.memory_depth,
            "memory_points": memory.points,
            "timebase_s_div": window.time_div_s,
            "time_offset_s": window.time_offset_s,
            "screen_t_left_s": window.t_left_s,
            "screen_t_right_s": window.t_right_s,
            "channel_scale_v_div": window.channel_scale_v,
            "channel_offset_v": window.channel_offset_v,
            "probe_ratio": window.probe_ratio,
            "y_increment_v": y_inc,
            "y_origin_v": y_orig,
            "y_reference": y_ref,
            "y_scale_source": "channel_scale/25",
            "measure_vmin_v": measured[0],
            "measure_vmax_v": measured[1],
            "trigger_status": scope.query(":TRIGger:STATus?").strip(),
        }

        if window_mode == WINDOW_SCREEN:
            capture = _try_screen_slices(
                scope,
                window,
                memory,
                y_candidates,
                measured,
                norm,
                chunk_size,
                max_retries,
            )
        cached_full = None
        if capture is None:
            print("Downloading full RAW memory for window verification")
            cached_full = _download_raw_range(scope, 1, memory.points, chunk_size, max_retries)
            capture = _select_verified_full(
                cached_full,
                window,
                memory,
                y_candidates,
                measured,
                norm,
                window_mode,
            )
        if capture is None:
            print("RAW did not verify; using verified NORM screen trace")
            capture = _norm_as_capture(norm, window, memory, measured)
        if capture is None:
            raise RuntimeError(
                "RAW download did not match the on-screen waveform "
                f"(VMIN={measured[0]:.6g} V, VMAX={measured[1]:.6g} V). "
                "Refused to save unverified data."
            )

        time_s, voltage_v, layout, start_i, stop_i, verify = capture
        extra_common.update(
            {
                "x_increment_s": layout.dt_s,
                "x_origin_s": float(time_s[0]) if len(time_s) else layout.t0_s,
                "x_reference": 0,
                "y_increment_v": verify["y_inc"],
                "y_origin_v": verify["y_orig"],
                "y_reference": verify["y_ref"],
                "y_scale_source": verify["y_source"],
                "raw_start": start_i,
                "raw_stop": stop_i,
                "memory_t0_s": layout.t0_s,
                "verify_ok": True,
                "verify_vmin_v": verify["vmin"],
                "verify_vmax_v": verify["vmax"],
                "verify_norm_corr": verify.get("norm_corr"),
            }
        )
        if verify["y_source"] == "norm_screen":
            extra_common["mode"] = "NORM"
        return WaveformCapture(
            time_s=time_s,
            voltage_v=voltage_v,
            idn=scope.query("*IDN?").strip(),
            model_id=self.model_id,
            channel=channel,
            sample_rate_hz=(1.0 / layout.dt_s) if layout.dt_s else window.sample_rate_hz,
            points=int(len(voltage_v)),
            captured_at=datetime.now().isoformat(timespec="seconds"),
            extra=extra_common,
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


def _request_raw_window(scope, points: int) -> int:
    """Set RAW start/stop. Overshoot on this firmware often clamps to 2.4 Mpts."""
    scope.write(":WAVeform:STARt 1")
    scope.write(f":WAVeform:STOP {points}")
    time.sleep(0.05)
    return int(float(scope.query(":WAVeform:STOP?").strip()))


def _raw_point_candidates(scope, timebase: float, sample_rate: float) -> tuple[list[int], str]:
    """Possible RAW lengths: MDEP, SRAT window, PRE xinc, and known analog rates."""
    screen_span = timebase * HORIZONTAL_DIVISIONS
    memory_depth = scope.query(":ACQ:MDEP?").strip()
    candidates: list[int] = []
    if memory_depth.upper() != "AUTO":
        candidates.append(int(float(memory_depth)))
    if sample_rate > 0:
        candidates.append(int(round(sample_rate * screen_span)))
    for rate in KNOWN_SAMPLE_RATES_HZ:
        candidates.append(int(round(rate * screen_span)))

    probe = max(candidates[0] if candidates else 12_000, 12_000)
    _request_raw_window(scope, probe)
    preamble = _parse_preamble(scope.query(":WAVeform:PRE?").strip())
    xinc = float(preamble["x_increment"])
    if 0 < xinc < 1:
        from_pre = int(round(screen_span / xinc))
        candidates.append(from_pre)
        print(f"RAW preamble xinc={xinc:.6e} s -> {from_pre:,} points for 12-div window")

    uniq = sorted({n for n in candidates if n > 0}, reverse=True)
    return uniq, memory_depth


def _resolve_raw_points(scope, timebase: float, sample_rate: float) -> tuple[int, str]:
    """Pick the largest RAW window the firmware accepts without an overshoot clamp."""
    candidates, memory_depth = _raw_point_candidates(scope, timebase, sample_rate)
    fallback = 1
    for requested in candidates:
        accepted = _request_raw_window(scope, requested)
        print(f"RAW STOP request {requested:,} -> {accepted:,}")
        if accepted >= int(requested * 0.98):
            return accepted, memory_depth
        fallback = max(fallback, accepted)
    return fallback, memory_depth


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


def _screen_vertical(scale: float, offset: float) -> tuple[float, float, int]:
    """BYTE mapping from the programming guide: YREF=127, YINC=VerticalScale/25."""
    y_inc = scale / SCREEN_COUNTS_PER_DIV
    y_ref = BYTE_MIDSCALE
    y_orig = offset / y_inc if y_inc else 0.0
    return y_inc, y_orig, y_ref


def _read_screen_window(scope, channel: int) -> ScreenWindow:
    time_div = _query_float(scope, ":TIMebase:MAIN:SCALe?")
    time_offset = _query_float(scope, ":TIMebase:MAIN:OFFSet?")
    span = time_div * HORIZONTAL_DIVISIONS
    return ScreenWindow(
        time_div_s=time_div,
        time_offset_s=time_offset,
        t_left_s=time_offset - span / 2.0,
        t_right_s=time_offset + span / 2.0,
        span_s=span,
        channel_scale_v=_query_float(scope, f":CHANnel{channel}:SCALe?"),
        channel_offset_v=_query_float(scope, f":CHANnel{channel}:OFFSet?"),
        probe_ratio=_query_float(scope, f":CHANnel{channel}:PROBe?"),
        sample_rate_hz=_query_float(scope, ":ACQ:SRAT?"),
    )


def _download_norm_trace(scope, channel: int) -> NormTrace | None:
    """On-screen NORM download. Preamble Y mapping is trustworthy in this mode."""
    try:
        scope.write(f":WAVeform:SOURce CHAN{channel}")
        scope.write(":WAVeform:FORMat BYTE")
        scope.write(":WAVeform:MODE NORM")
        scope.write(":WAVeform:STARt 1")
        scope.write(f":WAVeform:STOP {NORM_POINTS}")
        time.sleep(0.05)
        preamble = _parse_preamble(scope.query(":WAVeform:PRE?").strip())
        raw = np.asarray(
            scope.query_binary_values(
                ":WAVeform:DATA?",
                datatype="B",
                container=list,
                header_fmt="ieee",
                expect_termination=True,
            ),
            dtype=np.float64,
        )
        if raw.size < 8:
            return None
        x_inc = float(preamble["x_increment"])
        x_orig = float(preamble["x_origin"])
        x_ref = float(preamble["x_reference"])
        y_inc = float(preamble["y_increment"])
        y_orig = float(preamble["y_origin"])
        y_ref = float(preamble["y_reference"])
        time_s = (np.arange(len(raw), dtype=np.float64) - x_ref) * x_inc + x_orig
        voltage_v = (raw - y_orig - y_ref) * y_inc
        return NormTrace(time_s=time_s, voltage_v=voltage_v)
    except Exception as exc:
        print(f"NORM reference download failed: {exc}")
        return None


def _probe_memory(scope, window: ScreenWindow) -> MemoryLayout:
    points, memory_depth = _resolve_raw_points(scope, window.time_div_s, window.sample_rate_hz)
    if points <= 0:
        raise RuntimeError("Scope reported zero waveform points")
    scope.write(":WAVeform:STARt 1")
    scope.write(f":WAVeform:STOP {min(points, 2)}")
    time.sleep(0.05)
    preamble = _parse_preamble(scope.query(":WAVeform:PRE?").strip())
    pre_inc = float(preamble["y_increment"])
    pre_ref = int(preamble["y_reference"])
    y_inc, _, y_ref = _screen_vertical(window.channel_scale_v, window.channel_offset_v)
    if pre_ref != BYTE_MIDSCALE or not math.isclose(pre_inc, y_inc, rel_tol=0.05, abs_tol=1e-12):
        print(
            f"Ignoring RAW preamble yinc={pre_inc:.6e} yref={pre_ref}; "
            f"using scale/25={y_inc:.6e} yref={y_ref}"
        )
    xinc = float(preamble["x_increment"])
    if xinc <= 0 or xinc > 1:
        if window.sample_rate_hz <= 0:
            raise RuntimeError("Cannot determine RAW sample interval")
        xinc = 1.0 / window.sample_rate_hz
        print(f"RAW preamble xinc unusable; using 1/SRAT={xinc:.6e} s")
    return MemoryLayout(points=points, dt_s=xinc, t0_s=0.0, memory_depth=memory_depth)


def _t0_candidates(window: ScreenWindow, memory: MemoryLayout) -> list[tuple[str, float]]:
    duration = memory.points * memory.dt_s
    return [
        ("trigger_centered", -duration / 2.0),
        ("screen_centered", window.time_offset_s - duration / 2.0),
    ]


def _window_indices(window: ScreenWindow, memory: MemoryLayout) -> tuple[int, int]:
    start = int(round((window.t_left_s - memory.t0_s) / memory.dt_s)) + 1
    stop = int(round((window.t_right_s - memory.t0_s) / memory.dt_s))
    start = max(1, min(start, memory.points))
    stop = max(start, min(stop, memory.points))
    return start, stop


def _bytes_to_voltage(
    raw: np.ndarray,
    y_inc: float,
    y_orig: float,
    y_ref: float,
) -> np.ndarray:
    return (raw.astype(np.float64) - y_orig - y_ref) * y_inc


def _vertical_candidates(
    scope,
    window: ScreenWindow,
    y_inc: float,
    y_orig: float,
    y_ref: int,
) -> list[tuple[str, float, float, float]]:
    """Possible BYTE→volt maps. scale/25 first; RAW preamble is a fallback."""
    candidates = [("channel_scale/25", y_inc, y_orig, float(y_ref))]
    try:
        preamble = _parse_preamble(scope.query(":WAVeform:PRE?").strip())
        pre_inc = float(preamble["y_increment"])
        pre_orig = float(preamble["y_origin"])
        pre_ref = float(preamble["y_reference"])
        if 0 <= pre_ref <= 255 and pre_inc != 0:
            candidates.append(("preamble", pre_inc, pre_orig, pre_ref))
    except Exception:
        pass
    return candidates


def _corrcoef(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 8 or len(b) < 8:
        return None
    if float(np.std(a)) <= 0 or float(np.std(b)) <= 0:
        return None
    corr = float(np.corrcoef(a, b)[0, 1])
    return corr if math.isfinite(corr) else None


def _norm_corr(time_s: np.ndarray, series: np.ndarray, norm: NormTrace) -> float | None:
    scores: list[float] = []
    series_f = series.astype(np.float64)
    if len(time_s) >= 2 and float(time_s[-1]) > float(time_s[0]):
        sampled = np.interp(norm.time_s, time_s, series_f)
        timed = _corrcoef(sampled, norm.voltage_v)
        if timed is not None:
            scores.append(timed)
    index_x = np.linspace(0.0, 1.0, len(series_f))
    index_y = np.linspace(0.0, 1.0, len(norm.voltage_v))
    indexed = _corrcoef(np.interp(index_y, index_x, series_f), norm.voltage_v)
    if indexed is not None:
        scores.append(indexed)
    return max(scores, key=abs) if scores else None


def _fit_to_measure(raw: np.ndarray, measured: tuple[float, float]) -> tuple[float, float, float]:
    vmin_m, vmax_m = measured
    lo = float(np.min(raw))
    hi = float(np.max(raw))
    if hi <= lo:
        return 0.0, 0.0, 0.0
    y_inc = (vmax_m - vmin_m) / (hi - lo)
    y_ref = lo
    y_orig = -vmin_m / y_inc if y_inc else 0.0
    return y_inc, y_orig, y_ref


def _verify_raw_slice(
    raw: np.ndarray,
    time_s: np.ndarray,
    y_candidates: list[tuple[str, float, float, float]],
    measured: tuple[float, float],
    norm: NormTrace | None,
) -> tuple[np.ndarray, dict] | None:
    vmin_m, vmax_m = measured
    p2p = vmax_m - vmin_m
    corr = _norm_corr(time_s, raw, norm) if norm is not None else None
    if norm is not None:
        if corr is None or corr < VERIFY_NORM_CORR:
            print(f"Verify NORM correlation failed: {corr} < {VERIFY_NORM_CORR}")
            return None
        print(f"NORM shape match corr={corr:.3f}")

    maps = list(y_candidates)
    maps.append(("measure_vmax_vmin", *_fit_to_measure(raw, measured)))
    for source, y_inc, y_orig, y_ref in maps:
        if y_inc == 0:
            continue
        voltage_v = _bytes_to_voltage(raw, y_inc, y_orig, y_ref)
        lo = float(np.min(voltage_v))
        hi = float(np.max(voltage_v))
        tol = max(VERIFY_P2P_FRAC * p2p, VERIFY_LSB * abs(y_inc))
        if abs(lo - vmin_m) > tol or abs(hi - vmax_m) > tol:
            print(
                f"Verify extrema failed ({source}): RAW {lo:.6g}..{hi:.6g} V vs "
                f"measure {vmin_m:.6g}..{vmax_m:.6g} V"
            )
            continue
        if source == "measure_vmax_vmin" and corr is None:
            print("Refusing measure-fit Y scale without a NORM shape check")
            continue
        print(f"Verified volts with {source} ({lo:.6g}..{hi:.6g} V)")
        return voltage_v, {
            "vmin": lo,
            "vmax": hi,
            "norm_corr": corr,
            "y_inc": y_inc,
            "y_orig": y_orig,
            "y_ref": y_ref,
            "y_source": source,
        }
    return None


def _try_screen_slices(
    scope,
    window: ScreenWindow,
    memory: MemoryLayout,
    y_candidates: list[tuple[str, float, float, float]],
    measured: tuple[float, float],
    norm: NormTrace | None,
    chunk_size: int,
    max_retries: int,
):
    seen: set[tuple[int, int]] = set()
    for name, t0 in _t0_candidates(window, memory):
        layout = MemoryLayout(memory.points, memory.dt_s, t0, memory.memory_depth)
        start, stop = _window_indices(window, layout)
        if (start, stop) in seen:
            continue
        seen.add((start, stop))
        print(f"Trying {name} t0={t0:.6e} s -> RAW {start:,}:{stop:,}")
        raw = _download_raw_range(scope, start, stop, chunk_size, max_retries)
        time_s = (np.arange(len(raw), dtype=np.float64) + (start - 1)) * layout.dt_s + t0
        verified = _verify_raw_slice(raw, time_s, y_candidates, measured, norm)
        if verified is not None:
            voltage_v, extra = verified
            return time_s, voltage_v, layout, start, stop, extra
    return None


def _select_verified_full(
    raw: np.ndarray,
    window: ScreenWindow,
    memory: MemoryLayout,
    y_candidates: list[tuple[str, float, float, float]],
    measured: tuple[float, float],
    norm: NormTrace | None,
    window_mode: str,
):
    for name, t0 in _t0_candidates(window, memory):
        layout = MemoryLayout(memory.points, memory.dt_s, t0, memory.memory_depth)
        time_all = np.arange(len(raw), dtype=np.float64) * layout.dt_s + t0
        mask = (time_all >= window.t_left_s) & (time_all <= window.t_right_s)
        if not np.any(mask):
            continue
        verified = _verify_raw_slice(
            raw[mask],
            time_all[mask],
            y_candidates,
            measured,
            norm,
        )
        if verified is None:
            print(f"Full-record crop with {name} did not verify")
            continue
        voltage_crop, extra = verified
        if window_mode == WINDOW_SCREEN:
            idx = np.flatnonzero(mask)
            start = int(idx[0]) + 1
            stop = int(idx[-1]) + 1
            print(f"Verified screen crop from full RAW ({name}, {start:,}:{stop:,})")
            return time_all[mask], voltage_crop, layout, start, stop, extra
        voltage_all = _bytes_to_voltage(raw, extra["y_inc"], extra["y_orig"], extra["y_ref"])
        print(f"Verified full RAW using screen crop ({name})")
        return time_all, voltage_all, layout, 1, int(len(raw)), extra
    return None


def _norm_as_capture(
    norm: NormTrace,
    window: ScreenWindow,
    memory: MemoryLayout,
    measured: tuple[float, float],
):
    lo = float(np.min(norm.voltage_v))
    hi = float(np.max(norm.voltage_v))
    p2p = measured[1] - measured[0]
    tol = max(VERIFY_P2P_FRAC * p2p, 1.0)
    if abs(lo - measured[0]) > tol or abs(hi - measured[1]) > tol:
        return None
    layout = MemoryLayout(len(norm.voltage_v), window.span_s / max(len(norm.voltage_v), 1), window.t_left_s, memory.memory_depth)
    extra = {
        "vmin": lo,
        "vmax": hi,
        "norm_corr": 1.0,
        "y_inc": (hi - lo) / max(hi - lo, 1.0),
        "y_orig": 0.0,
        "y_ref": 0.0,
        "y_source": "norm_screen",
    }
    print(f"Verified NORM screen trace {lo:.6g}..{hi:.6g} V ({len(norm.voltage_v)} pts)")
    return norm.time_s, norm.voltage_v, layout, 1, int(len(norm.voltage_v)), extra


def _download_raw_range(
    scope,
    start: int,
    stop: int,
    chunk_size: int,
    max_retries: int,
) -> np.ndarray:
    values: list[int] = []
    cursor = start
    total = stop - start + 1
    while cursor <= stop:
        chunk_stop = min(cursor + chunk_size - 1, stop)
        chunk = _read_chunk(scope, cursor, chunk_stop, max_retries)
        values.extend(chunk)
        print(f"  read points {cursor}-{cursor + len(chunk) - 1} ({len(values):,}/{total:,})")
        if len(chunk) < chunk_stop - cursor + 1:
            break
        cursor += len(chunk)
    if not values:
        raise RuntimeError(f"RAW window {start}-{stop} returned no samples")
    return np.asarray(values, dtype=np.uint8)


def _ieee_payload(raw: bytes) -> bytes:
    if not raw.startswith(b"#"):
        raise RuntimeError(f"Binary block did not start with '#': {raw[:24]!r}")
    nlen = int(chr(raw[1]))
    nbytes = int(raw[2 : 2 + nlen])
    return raw[2 + nlen : 2 + nlen + nbytes]


def _measure_extrema(scope, channel: int) -> tuple[float, float] | None:
    try:
        vmax = _query_float(scope, f":MEASure:ITEM? VMAX,CHANnel{channel}")
        vmin = _query_float(scope, f":MEASure:ITEM? VMIN,CHANnel{channel}")
    except Exception:
        return None
    if _is_invalid_measure(vmax) or _is_invalid_measure(vmin) or vmax <= vmin:
        return None
    return vmin, vmax


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
