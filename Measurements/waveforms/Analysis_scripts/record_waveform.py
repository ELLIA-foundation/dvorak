"""Offload the stopped oscilloscope waveform into a waveforms session."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from pyvisa import VisaIOError

for _parent in Path(__file__).resolve().parents:
    if (_parent / "lib" / "paths.py").is_file() and (_parent / "Measurements").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break
else:
    raise SystemExit("Could not find repository root (expected lib/paths.py and Measurements/).")

from instruments import DEFAULT_OSCILLOSCOPE, open_oscilloscope
from instruments.registry import list_oscilloscopes
from lib.paths import CAMPAIGN_WAVEFORMS, campaign_session_data, list_sessions
from lib.waveform import WaveformCapture, save_waveform

DEFAULT_SCOPE_CHANNEL = 1
DEFAULT_CHUNK_SIZE = 250_000
DEFAULT_WINDOW = "screen"


def _print_capture_info(capture: WaveformCapture, paths: dict[str, Path]) -> None:
    extra = capture.extra
    duration_s = (
        float(capture.time_s[-1] - capture.time_s[0]) if len(capture.time_s) > 1 else 0.0
    )
    xinc = extra.get("x_increment_s")
    tdiv = extra.get("timebase_s_div")
    print(f"\nCaptured channel {capture.channel}: {capture.points:,} points")
    if xinc is not None:
        print(f"Sample interval: {xinc:.6e} s")
    print(f"Sample rate:     {capture.sample_rate_hz:.6e} Sa/s")
    if tdiv:
        print(f"Time span:       {duration_s:.6e} s ({duration_s / tdiv:.1f} div)")
    else:
        print(f"Time span:       {duration_s:.6e} s")
    print(
        f"Voltage range:   {capture.voltage_v.min():.6f} V to "
        f"{capture.voltage_v.max():.6f} V"
    )
    if extra.get("memory_depth") is not None:
        print(f"Memory depth:    {extra['memory_depth']}")
    if tdiv is not None:
        print(f"Timebase:        {tdiv:.6e} s/div")
    print("\nSaved:")
    for label, path in paths.items():
        print(f"  {label}: {path}")


def acquire_waveform(
    scope_model: str | None,
    channel: int,
    chunk_size: int,
    write_csv: bool,
    output_dir: Path,
    window: str = DEFAULT_WINDOW,
    run_name: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Download the stopped waveform, save NPZ+JSON, then close the scope."""
    scope = open_oscilloscope(scope_model)
    try:
        idn = scope.identify()
        print(f"Connected: {idn}")
        print(f"Model:     {scope.model_id}")
        capture = scope.capture_channel(channel=channel, chunk_size=chunk_size, window=window)
        paths = save_waveform(capture, output_dir, write_csv=write_csv, run_name=run_name)
        _print_capture_info(capture, paths)
        run = {
            "mode": "acquire",
            "campaign": CAMPAIGN_WAVEFORMS,
            "oscilloscope_idn": idn,
            "oscilloscope_model": scope.model_id,
            "scope_channel": capture.channel,
            "chunk_size": chunk_size,
            "write_csv": write_csv,
            "window": window,
            "run_name": run_name,
            "session": output_dir.name,
        }
        return paths["npz"], run
    except VisaIOError as exc:
        raise SystemExit(f"VISA error while reading waveform: {exc}") from exc
    finally:
        scope.close()


def main() -> None:
    registered = list_oscilloscopes()
    parser = argparse.ArgumentParser(
        description=(
            "Download the stopped oscilloscope waveform into "
            "waveforms/Data/<session>/. Configure timebase and trigger on the "
            "scope, then stop the acquisition before running this script."
        )
    )
    parser.add_argument(
        "--session",
        type=str,
        required=True,
        help=(
            "Session folder under Data/ "
            f"(available: {', '.join(list_sessions(CAMPAIGN_WAVEFORMS)) or 'none yet'})"
        ),
    )
    parser.add_argument(
        "--name",
        type=str,
        required=True,
        help="Measurement name for waveform_<name>_chN.npz (timestamp stays in JSON)",
    )
    parser.add_argument(
        "--scope",
        type=str,
        default=None,
        choices=registered or None,
        help=(
            "Oscilloscope model id "
            f"(default: {DEFAULT_OSCILLOSCOPE}). "
            f"Registered: {', '.join(registered) or '(none)'}"
        ),
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=DEFAULT_SCOPE_CHANNEL,
        help=f"Analog channel to read (default: {DEFAULT_SCOPE_CHANNEL})",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Driver-specific points per VISA transfer (default: {DEFAULT_CHUNK_SIZE})",
    )
    parser.add_argument(
        "--window",
        type=str,
        default=DEFAULT_WINDOW,
        choices=("screen", "full"),
        help="RAW slice: 12-div screen (verified) or full memory (default: screen)",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Also write a CSV file (can be large for deep-memory captures)",
    )
    args = parser.parse_args()

    output_dir = campaign_session_data(CAMPAIGN_WAVEFORMS, args.session, create=True)
    acquire_waveform(
        scope_model=args.scope,
        channel=args.channel,
        chunk_size=args.chunk_size,
        write_csv=args.csv,
        output_dir=output_dir,
        window=args.window,
        run_name=args.name.strip(),
    )


if __name__ == "__main__":
    main()
