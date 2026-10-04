"""Overlay X-123 spectra from .mca or spectrum_*.npz files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "lib" / "paths.py").is_file() and (_parent / "Measurements").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

from lib.paths import (
    list_spectrum_campaigns,
    list_spectrum_files,
    spectrum_campaign_plots,
)
from lib.spectrum import load_calibration, load_spectrum_file
from lines import all_lines, label_positions
from spectrum import moving_average, normalize_integral, normalize_max, to_cps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Spectrum files. Omit to plot every file in --campaign (or all campaigns).",
    )
    parser.add_argument("--campaign", help="Nested campaign name under X123_Spectra/")
    parser.add_argument("--smooth", type=int, default=1, help="Moving-average window in channels")
    parser.add_argument(
        "--y",
        choices=("counts", "cps", "max", "integral"),
        default="counts",
    )
    parser.add_argument("--log", action="store_true")
    parser.add_argument(
        "--lines",
        action="store_true",
        help="Mark U L, Th/Bi/Ra, and common K/L lines",
    )
    parser.add_argument("--no-show", action="store_true")
    parser.add_argument("--out", type=Path, help="PNG path (default Data/plots/ under first campaign)")
    args = parser.parse_args(argv)

    files = [path.expanduser().resolve() for path in args.paths]
    if not files:
        names = [args.campaign] if args.campaign else list_spectrum_campaigns()
        for name in names:
            files.extend(list_spectrum_files(name))
    if not files:
        raise SystemExit("No spectrum files found")

    cal = load_calibration()
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11, 5.5))
    for path in files:
        capture = load_spectrum_file(path, cal)
        y = moving_average(capture.counts, args.smooth)
        if args.y == "cps":
            y = to_cps(y, capture.live_time_s)
            ylabel = "counts / s"
        elif args.y == "max":
            y = normalize_max(y)
            ylabel = "counts / max"
        elif args.y == "integral":
            y = normalize_integral(y)
            ylabel = "counts / integral"
        else:
            ylabel = "counts"
        label = capture.run_name or path.stem
        ax.plot(capture.energy_kev, y, linewidth=1.0, label=label)

    if args.lines:
        marked = all_lines()
        transform = ax.get_xaxis_transform()
        for (name, energy), slot in zip(marked, label_positions(marked)):
            ax.axvline(energy, color="0.6", linewidth=0.8, linestyle="--")
            ax.text(
                energy,
                slot,
                name,
                rotation=90,
                va="top",
                ha="right",
                fontsize=8,
                color="0.35",
                transform=transform,
                clip_on=True,
            )

    ax.set_xlabel("Energy (keV)")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3)
    if args.log:
        ax.set_yscale("log")
    if len(files) > 1:
        ax.legend(fontsize=8)
    fig.tight_layout()

    saved = args.out
    if saved is None and args.no_show:
        campaign = args.campaign or list_spectrum_campaigns()[0]
        plots = spectrum_campaign_plots(campaign)
        plots.mkdir(parents=True, exist_ok=True)
        saved = plots / "overlay.png"
    if saved is not None:
        saved.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(saved, dpi=150)
        print(f"wrote {saved}")

    if args.no_show:
        plt.close(fig)
    else:
        plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
