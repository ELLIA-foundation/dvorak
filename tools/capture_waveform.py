"""Extract the current stopped waveform from the configured oscilloscope."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "lib" / "paths.py").is_file() and (_parent / "Measurements").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break
else:
    raise SystemExit("Could not find repository root (expected lib/paths.py and Measurements/).")

from pyvisa import VisaIOError

from instruments.registry import (
    DEFAULT_OSCILLOSCOPE,
    list_oscilloscopes,
    open_oscilloscope,
)
from lib.paths import campaign_data, list_campaigns
from lib.waveform import save_waveform


def _resolve_output_dir(campaign: str | None, output_dir: Path | None) -> Path:
    if output_dir is not None:
        return output_dir
    if campaign:
        try:
            return campaign_data(campaign)
        except FileNotFoundError as exc:
            raise SystemExit(str(exc)) from exc
    known = ", ".join(list_campaigns()) or "(none)"
    raise SystemExit(
        "Specify --campaign (writes to Measurements/<name>/Data/) or --output-dir. "
        f"Available campaigns: {known}"
    )


def main() -> None:
    registered = list_oscilloscopes()
    parser = argparse.ArgumentParser(
        description="Extract the current oscilloscope acquisition into a campaign Data/ folder."
    )
    parser.add_argument(
        "--campaign",
        type=str,
        default=None,
        help="Measurement campaign name (saves under Measurements/<name>/Data/)",
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
        default=1,
        help="Analog channel to read (default: 1)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=250_000,
        help="Driver-specific points per VISA transfer (default: 250000)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override save directory (default: Measurements/<campaign>/Data/)",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Also write a CSV file (can be large for deep-memory captures)",
    )
    parser.add_argument(
        "--window",
        type=str,
        default="screen",
        choices=("screen", "full"),
        help="RAW slice: 12-div screen (verified) or full memory (default: screen)",
    )
    args = parser.parse_args()

    output_dir = _resolve_output_dir(args.campaign, args.output_dir)
    scope = open_oscilloscope(args.scope)
    try:
        print(f"Connected: {scope.identify()}")
        print(f"Model:     {scope.model_id}")

        capture = scope.capture_channel(
            channel=args.channel,
            chunk_size=args.chunk_size,
            window=args.window,
        )

        paths = save_waveform(capture, output_dir, write_csv=args.csv)

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
    except VisaIOError as exc:
        raise SystemExit(f"VISA error while reading waveform: {exc}") from exc
    finally:
        scope.close()


if __name__ == "__main__":
    main()
