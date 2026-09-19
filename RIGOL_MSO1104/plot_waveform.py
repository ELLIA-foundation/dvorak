"""Plot a waveform saved by capture_waveform.py.

For large captures (millions of points), the trace is min-max decimated for
display so peaks are preserved while keeping the plot responsive.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

DEFAULT_MAX_POINTS = 20_000


def _load_metadata(npz_path: Path) -> dict | None:
    json_path = npz_path.with_suffix(".json")
    if not json_path.exists():
        return None
    return json.loads(json_path.read_text(encoding="utf-8"))


def _pick_time_scale(time_s: np.ndarray) -> tuple[np.ndarray, str]:
    span = float(np.max(time_s) - np.min(time_s))
    if span < 1e-6:
        return time_s * 1e9, "ns"
    if span < 1e-3:
        return time_s * 1e6, "µs"
    if span < 1:
        return time_s * 1e3, "ms"
    return time_s, "s"


def _pick_voltage_scale(voltage_v: np.ndarray) -> tuple[np.ndarray, str]:
    peak = float(np.max(np.abs(voltage_v))) if len(voltage_v) else 0.0
    if peak >= 1000:
        return voltage_v / 1000.0, "kV"
    return voltage_v, "V"


def _latest_capture(directory: Path) -> Path | None:
    files = sorted(directory.glob("waveform_*.npz"))
    return files[-1] if files else None


def _decimate_minmax(time_s: np.ndarray, voltage_v: np.ndarray, max_points: int) -> tuple[np.ndarray, np.ndarray]:
    """Reduce points for plotting while preserving local min/max."""
    n = len(time_s)
    if n <= max_points:
        return time_s, voltage_v

    bucket_count = max_points // 2
    bucket_size = int(np.ceil(n / bucket_count))
    t_out: list[float] = []
    v_out: list[float] = []

    for start in range(0, n, bucket_size):
        stop = min(start + bucket_size, n)
        t_bucket = time_s[start:stop]
        v_bucket = voltage_v[start:stop]
        min_idx = int(np.argmin(v_bucket))
        max_idx = int(np.argmax(v_bucket))
        if min_idx <= max_idx:
            order = (min_idx, max_idx)
        else:
            order = (max_idx, min_idx)
        for idx in order:
            t_out.append(float(t_bucket[idx]))
            v_out.append(float(v_bucket[idx]))

    return np.asarray(t_out), np.asarray(v_out)


def plot_waveform(
    npz_path: Path,
    max_points: int = DEFAULT_MAX_POINTS,
    output_path: Path | None = None,
    show: bool = True,
) -> Path | None:
    data = np.load(npz_path)
    time_s = np.asarray(data["time_s"], dtype=np.float64)
    voltage_v = np.asarray(data["voltage_v"], dtype=np.float64)
    metadata = _load_metadata(npz_path)

    plot_t, plot_v = _decimate_minmax(time_s, voltage_v, max_points)
    display_t, time_unit = _pick_time_scale(plot_t)
    display_v, volt_unit = _pick_voltage_scale(plot_v)

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(display_t, display_v, linewidth=0.8, color="#1f77b4")
    ax.set_xlabel(f"Time ({time_unit})")
    ax.set_ylabel(f"Voltage ({volt_unit})")
    ax.set_xlim(float(display_t[0]), float(display_t[-1]))
    ax.axhline(0.0, color="0.5", linewidth=0.8, linestyle="--")
    ax.grid(True, alpha=0.3)

    title = npz_path.name
    if metadata:
        channel = metadata.get("channel", "?")
        points = metadata.get("points", len(time_s))
        dt = metadata.get("x_increment_s")
        tdiv = metadata.get("timebase_s_div")
        captured = metadata.get("captured_at", "")
        title = (
            f"Channel {channel} | {points:,} points | "
            f"dt={dt:.3g} s | {tdiv:.3g} s/div | {captured}"
        )
    ax.set_title(title)

    if len(time_s) > max_points:
        ax.text(
            0.01,
            0.98,
            f"Display: min-max decimated to {len(plot_t):,} points "
            f"(from {len(time_s):,} captured)",
            transform=ax.transAxes,
            va="top",
            fontsize=9,
            bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.8},
        )

    fig.tight_layout()

    saved: Path | None = None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=150)
        saved = output_path

    if show:
        plt.show()
    else:
        plt.close(fig)

    return saved


def main() -> None:
    captures_dir = Path(__file__).resolve().parent / "captures"
    default_npz = _latest_capture(captures_dir)

    parser = argparse.ArgumentParser(description="Plot a captured Rigol waveform NPZ file.")
    parser.add_argument(
        "npz",
        nargs="?",
        type=Path,
        default=default_npz,
        help="Path to .npz file from capture_waveform.py (default: latest in captures/)",
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

    if args.npz is None or not args.npz.exists():
        raise SystemExit(f"File not found: {args.npz}")

    save_path = args.save
    if save_path is None and args.no_show:
        save_path = args.npz.with_suffix(".png")

    saved = plot_waveform(
        args.npz,
        max_points=args.max_points,
        output_path=save_path,
        show=not args.no_show,
    )
    if saved:
        print(f"Saved plot: {saved}")


if __name__ == "__main__":
    main()
