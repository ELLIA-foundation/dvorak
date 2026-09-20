"""Plot a waveform saved by tools/capture_waveform.py."""

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

from lib.paths import campaign_data, campaign_plots, infer_campaign, list_campaigns
from lib.waveform import DEFAULT_MAX_POINTS, latest_capture, plot_waveform


def _default_npz(campaign: str | None) -> Path | None:
    if campaign:
        try:
            return latest_capture(campaign_data(campaign))
        except FileNotFoundError as exc:
            raise SystemExit(str(exc)) from exc
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot a captured waveform NPZ file.")
    parser.add_argument(
        "npz",
        nargs="?",
        type=Path,
        default=None,
        help="Path to .npz file (default: latest in the campaign Data/ folder)",
    )
    parser.add_argument(
        "--campaign",
        type=str,
        default=None,
        help="Campaign whose Data/ folder supplies the default NPZ",
    )
    parser.add_argument(
        "--max-points",
        type=int,
        default=DEFAULT_MAX_POINTS,
        help="Maximum plotted points after min-max decimation (default: 20000)",
    )
    parser.add_argument(
        "--save",
        type=Path,
        default=None,
        help="Save plot to this image path instead of only showing it",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Do not open an interactive plot window",
    )
    args = parser.parse_args()

    npz_path = args.npz
    if npz_path is None:
        npz_path = _default_npz(args.campaign)
    if npz_path is None:
        known = ", ".join(list_campaigns()) or "(none)"
        raise SystemExit(
            "Specify an NPZ path or --campaign to plot the latest capture. "
            f"Available campaigns: {known}"
        )
    if not npz_path.exists():
        raise SystemExit(f"File not found: {npz_path}")

    save_path = args.save
    if save_path is None and args.no_show:
        campaign = args.campaign or infer_campaign(npz_path)
        if campaign:
            save_path = campaign_plots(campaign) / f"{npz_path.stem}.png"
        else:
            save_path = npz_path.with_suffix(".png")

    saved = plot_waveform(
        npz_path,
        max_points=args.max_points,
        output_path=save_path,
        show=not args.no_show,
    )
    if saved:
        print(f"Saved plot: {saved}")


if __name__ == "__main__":
    main()
