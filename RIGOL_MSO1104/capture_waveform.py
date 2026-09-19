"""Extract the current stopped waveform acquisition from the Rigol MSO1104Z.

Uses deep-memory RAW mode with binary (BYTE) transfers in official-size
chunks (250000 points). This avoids the ~600-point on-screen / CSV export
limit. Compressed NPZ is the default save format; CSV is optional.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from pyvisa import VisaIOError

from rigol_scope import open_scope

DEFAULT_CHANNEL = 1
# Official DS1000Z BYTE-format limit per :WAVeform:DATA? read.
DEFAULT_CHUNK_SIZE = 250_000
DEFAULT_MAX_RETRIES = 5
HORIZONTAL_DIVISIONS = 12
SCREEN_COUNTS_PER_DIV = 25
BYTE_MIDSCALE = 127
# Asking for a STOP far above the record can make this firmware clamp to 2.4 Mpts.
STANDARD_MEMORY_DEPTHS = (12_000, 120_000, 1_200_000, 6_000_000, 12_000_000, 24_000_000)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "captures"


@dataclass
class WaveformMetadata:
    idn: str
    resource: str
    channel: int
    mode: str
    points: int
    sample_rate_hz: float
    memory_depth: str
    timebase_s_div: float
    time_offset_s: float
    channel_scale_v_div: float
    channel_offset_v: float
    probe_ratio: float
    x_increment_s: float
    x_origin_s: float
    x_reference: int
    y_increment_v: float
    y_origin_v: float
    y_reference: int
    y_scale_source: str
    trigger_status: str
    captured_at: str


def _query_float(scope, command: str) -> float:
    return float(scope.query(command).strip())


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

    # If AUTO math underestimates, try the next standard depth without a huge overshoot.
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


def capture_channel_waveform(
    scope,
    channel: int = DEFAULT_CHANNEL,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> tuple[np.ndarray, np.ndarray, WaveformMetadata]:
    """Read the waveform currently in scope memory for one analog channel."""
    if chunk_size > DEFAULT_CHUNK_SIZE:
        chunk_size = DEFAULT_CHUNK_SIZE

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
        print(f"  read points {start}-{start + len(chunk) - 1} ({len(raw_values):,}/{total_points:,})")
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

    metadata = WaveformMetadata(
        idn=scope.query("*IDN?").strip(),
        resource=str(scope.resource_name),
        channel=channel,
        mode="RAW",
        points=int(len(raw)),
        sample_rate_hz=_query_float(scope, ":ACQ:SRAT?"),
        memory_depth=memory_depth,
        timebase_s_div=_query_float(scope, ":TIMebase:MAIN:SCALe?"),
        time_offset_s=_query_float(scope, ":TIMebase:MAIN:OFFSet?"),
        channel_scale_v_div=_query_float(scope, f":CHANnel{channel}:SCALe?"),
        channel_offset_v=_query_float(scope, f":CHANnel{channel}:OFFSet?"),
        probe_ratio=_query_float(scope, f":CHANnel{channel}:PROBe?"),
        x_increment_s=x_inc,
        x_origin_s=x_orig,
        x_reference=x_ref,
        y_increment_v=y_inc,
        y_origin_v=y_orig,
        y_reference=y_ref,
        y_scale_source=y_source,
        trigger_status=scope.query(":TRIGger:STATus?").strip(),
        captured_at=datetime.now().isoformat(timespec="seconds"),
    )
    return time_s, voltage_v, metadata


def save_waveform(
    time_s: np.ndarray,
    voltage_v: np.ndarray,
    metadata: WaveformMetadata,
    output_dir: Path,
    write_csv: bool,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"waveform_{stamp}_ch{metadata.channel}"

    npz_path = output_dir / f"{stem}.npz"
    json_path = output_dir / f"{stem}.json"

    np.savez_compressed(
        npz_path,
        time_s=time_s,
        voltage_v=voltage_v,
    )
    json_path.write_text(json.dumps(asdict(metadata), indent=2), encoding="utf-8")

    paths = {"npz": npz_path, "json": json_path}

    if write_csv:
        csv_path = output_dir / f"{stem}.csv"
        np.savetxt(
            csv_path,
            np.column_stack((time_s, voltage_v)),
            delimiter=",",
            header="time_s,voltage_v",
            comments="",
            fmt="%.12e",
        )
        paths["csv"] = csv_path

    return paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract the current Rigol waveform acquisition (deep memory RAW mode)."
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=DEFAULT_CHANNEL,
        choices=(1, 2, 3, 4),
        help="Analog channel to read (default: 1)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Points per SCPI transfer (max 250000 for BYTE format)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for saved waveform files",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Also write a CSV file (can be large for deep-memory captures)",
    )
    args = parser.parse_args()

    rm, scope, resource, idn = open_scope(timeout_ms=120_000, prefer_instr=True)
    try:
        print(f"Connected: {idn}")
        print(f"Resource:  {resource}")

        time_s, voltage_v, metadata = capture_channel_waveform(
            scope,
            channel=args.channel,
            chunk_size=args.chunk_size,
        )

        paths = save_waveform(
            time_s,
            voltage_v,
            metadata,
            args.output_dir,
            write_csv=args.csv,
        )

        duration_s = float(time_s[-1] - time_s[0]) if len(time_s) > 1 else 0.0
        print(f"\nCaptured channel {metadata.channel}: {metadata.points:,} points")
        print(f"Sample interval: {metadata.x_increment_s:.6e} s")
        print(f"Sample rate:     {metadata.sample_rate_hz:.6e} Sa/s")
        print(f"Time span:       {duration_s:.6e} s ({duration_s / metadata.timebase_s_div:.1f} div)")
        print(f"Voltage range:   {voltage_v.min():.6f} V to {voltage_v.max():.6f} V")
        print(f"Memory depth:    {metadata.memory_depth}")
        print(f"Timebase:        {metadata.timebase_s_div:.6e} s/div")
        print("\nSaved:")
        for label, path in paths.items():
            print(f"  {label}: {path}")
    except VisaIOError as exc:
        raise SystemExit(f"VISA error while reading waveform: {exc}") from exc
    except Exception:
        raise
    finally:
        scope.close()
        rm.close()


if __name__ == "__main__":
    main()
