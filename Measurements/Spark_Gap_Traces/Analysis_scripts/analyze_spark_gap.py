"""Acquire a spark-gap waveform from the bench oscilloscope and analyze it."""

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
from lib.paths import CAMPAIGN_SPARK_GAP, campaign_data, campaign_plots, infer_campaign
from lib.waveform import WaveformCapture, latest_capture, plot_waveform, save_waveform
from spark_gap import (
    DEFAULT_COARSE_STEP_S,
    DEFAULT_DROP_THRESHOLD_V as _LIB_DROP_THRESHOLD_V,
    DEFAULT_DROP_WINDOW_S as _LIB_DROP_WINDOW_S,
    DEFAULT_MERGE_GAP_S as _LIB_MERGE_GAP_S,
    DEFAULT_SCOPE_BW_HZ as _LIB_SCOPE_BW_HZ,
)
from spark_gap_report import print_report, run_analysis

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Capture and analysis configuration — edit these, or override on the CLI.
# ---------------------------------------------------------------------------

# Oscilloscope analog input that reads the gap voltage.
DEFAULT_SCOPE_CHANNEL = 1

# Driver-specific points per VISA transfer when downloading deep memory.
DEFAULT_CHUNK_SIZE = 250_000

# Scope analog bandwidth (Hz). Used for 10–90 limit annotations on plots.
DEFAULT_SCOPE_BW_HZ = _LIB_SCOPE_BW_HZ

# Coarse event detection. Library defaults live in spark_gap.py.
DEFAULT_DROP_THRESHOLD_V = _LIB_DROP_THRESHOLD_V
DEFAULT_DROP_WINDOW_S = _LIB_DROP_WINDOW_S
DEFAULT_MERGE_GAP_S = _LIB_MERGE_GAP_S

# Include first-cycle / atypical ramps in typical histograms and overlays.
DEFAULT_INCLUDE_FIRST = False

# Optional gap/load capacitance (F) for energy and L estimates. None = skip.
DEFAULT_CAPACITANCE_F = None

# Also write a raw waveform CSV (can be large for deep-memory captures).
DEFAULT_WRITE_CSV = False

# Label for capture filenames and the analysis summary. None = timestamp-only stem.
DEFAULT_RUN_NAME = "1_gaps_1_r2"

# RAW slice: "screen" = 12-div window (verified against the scope), "full" = all memory.
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
            "oscilloscope_idn": idn,
            "oscilloscope_model": scope.model_id,
            "scope_channel": capture.channel,
            "chunk_size": chunk_size,
            "write_csv": write_csv,
            "window": window,
            "run_name": run_name,
        }
        return paths["npz"], run
    except VisaIOError as exc:
        raise SystemExit(f"VISA error while reading waveform: {exc}") from exc
    finally:
        scope.close()


def _resolve_offline_npz(npz_arg: str) -> Path:
    if npz_arg:
        path = Path(npz_arg)
        if not path.exists():
            raise SystemExit(f"File not found: {path}")
        return path
    latest = latest_capture(campaign_data(CAMPAIGN_SPARK_GAP))
    if latest is None or not latest.exists():
        raise SystemExit(
            f"No waveform_*.npz found in {campaign_data(CAMPAIGN_SPARK_GAP)}"
        )
    return latest


def _run_name(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def main() -> None:
    registered = list_oscilloscopes()
    parser = argparse.ArgumentParser(
        description=(
            "Download the stopped oscilloscope waveform into Spark_Gap_Traces/Data "
            "and write the diagnostic figure pack. Configure timebase/trigger on the "
            "scope first. Pass --npz to skip the instrument and re-analyze a capture."
        )
    )
    parser.add_argument(
        "--npz",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help=(
            "Skip the oscilloscope and analyze an existing NPZ. "
            "Omit PATH to use the latest capture in this campaign Data/."
        ),
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
        "--scope-channel",
        type=int,
        default=DEFAULT_SCOPE_CHANNEL,
        help=f"Oscilloscope analog channel (default: {DEFAULT_SCOPE_CHANNEL})",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Points per VISA transfer (default: {DEFAULT_CHUNK_SIZE})",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Analysis output directory (default: Data/plots/analysis_<stem>/)",
    )
    parser.add_argument("--no-show", action="store_true", help="Save plots without opening windows")
    parser.add_argument(
        "--include-first",
        action="store_true",
        default=DEFAULT_INCLUDE_FIRST,
        help="Include first-cycle / atypical ramps in typical histograms and overlays",
    )
    parser.add_argument(
        "--drop-threshold",
        type=float,
        default=DEFAULT_DROP_THRESHOLD_V,
        help=f"Coarse-pass voltage drop threshold in volts (default: {DEFAULT_DROP_THRESHOLD_V:g})",
    )
    parser.add_argument(
        "--drop-window",
        type=float,
        default=DEFAULT_DROP_WINDOW_S,
        help=f"Coarse-pass drop window in seconds (default: {DEFAULT_DROP_WINDOW_S})",
    )
    parser.add_argument(
        "--merge-gap",
        type=float,
        default=DEFAULT_MERGE_GAP_S,
        help=f"Merge hits closer than this many seconds (default: {DEFAULT_MERGE_GAP_S})",
    )
    parser.add_argument(
        "--scope-bw",
        type=float,
        default=DEFAULT_SCOPE_BW_HZ,
        help=f"Scope analog bandwidth in Hz (default: {DEFAULT_SCOPE_BW_HZ})",
    )
    parser.add_argument(
        "--capacitance",
        type=float,
        default=DEFAULT_CAPACITANCE_F,
        help="Optional gap/load capacitance in farads for energy and L estimates",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        default=DEFAULT_WRITE_CSV,
        help="Also write a raw waveform CSV (can be large)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default=DEFAULT_RUN_NAME,
        help="Run label for capture filenames and analysis summary (default: omit)",
    )
    parser.add_argument(
        "--window",
        type=str,
        default=DEFAULT_WINDOW,
        choices=("screen", "full"),
        help="RAW slice: 12-div screen (verified) or full memory (default: screen)",
    )
    args = parser.parse_args()

    run_name = _run_name(args.name)
    if args.npz is not None:
        npz_path = _resolve_offline_npz(args.npz)
        run: dict[str, Any] = {
            "mode": "offline",
            "run_name": run_name,
            "scope_channel": args.scope_channel,
        }
        print(f"Offline analysis: {npz_path}")
    else:
        data_dir = campaign_data(CAMPAIGN_SPARK_GAP)
        data_dir.mkdir(parents=True, exist_ok=True)
        npz_path, run = acquire_waveform(
            scope_model=args.scope,
            channel=args.scope_channel,
            chunk_size=args.chunk_size,
            write_csv=args.csv,
            output_dir=data_dir,
            window=args.window,
            run_name=run_name,
        )

    run.update(
        {
            "run_name": run_name,
            "scope_bw_hz": args.scope_bw,
            "drop_threshold_v": args.drop_threshold,
            "drop_window_s": args.drop_window,
            "merge_gap_s": args.merge_gap,
            "include_first": args.include_first,
            "capacitance_f": args.capacitance,
        }
    )

    campaign = infer_campaign(npz_path) or CAMPAIGN_SPARK_GAP
    plots_dir = campaign_plots(campaign)
    overview_path = plots_dir / f"{npz_path.stem}.png"
    plot_waveform(npz_path, output_path=overview_path, show=False)
    print(f"Wrote {overview_path}")

    out_dir = args.out_dir
    if out_dir is None:
        out_dir = plots_dir / f"analysis_{npz_path.stem}"

    result = run_analysis(
        npz_path,
        out_dir,
        include_first=args.include_first,
        show=not args.no_show,
        drop_threshold_v=args.drop_threshold,
        drop_window_s=args.drop_window,
        merge_gap_s=args.merge_gap,
        scope_bw_hz=args.scope_bw,
        capacitance_f=args.capacitance,
        run=run,
    )
    print_report(result)
    print(f"\nWrote analysis to {out_dir}")


if __name__ == "__main__":
    main()
