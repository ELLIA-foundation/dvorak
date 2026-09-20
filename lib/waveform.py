"""Model-agnostic waveform capture records, NPZ I/O, and plotting helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

DEFAULT_MAX_POINTS = 20_000


@dataclass
class WaveformCapture:
    """Interchange format returned by oscilloscope drivers."""

    time_s: np.ndarray
    voltage_v: np.ndarray
    idn: str
    model_id: str
    channel: int
    sample_rate_hz: float
    points: int
    captured_at: str
    extra: dict[str, Any] = field(default_factory=dict)

    def metadata_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "idn": self.idn,
            "model_id": self.model_id,
            "channel": self.channel,
            "sample_rate_hz": self.sample_rate_hz,
            "points": self.points,
            "captured_at": self.captured_at,
        }
        payload.update(self.extra)
        return payload


def load_metadata(npz_path: Path) -> dict[str, Any] | None:
    json_path = npz_path.with_suffix(".json")
    if not json_path.exists():
        return None
    return json.loads(json_path.read_text(encoding="utf-8"))


def load_waveform(npz_path: Path) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    data = np.load(npz_path)
    time_s = np.asarray(data["time_s"], dtype=np.float64)
    voltage_v = np.asarray(data["voltage_v"], dtype=np.float64)
    metadata = load_metadata(npz_path) or {}
    return time_s, voltage_v, metadata


def latest_capture(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    files = sorted(directory.glob("waveform_*.npz"))
    return files[-1] if files else None


def save_waveform(
    capture: WaveformCapture,
    output_dir: Path,
    write_csv: bool = False,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"waveform_{stamp}_ch{capture.channel}"

    npz_path = output_dir / f"{stem}.npz"
    json_path = output_dir / f"{stem}.json"

    np.savez_compressed(
        npz_path,
        time_s=capture.time_s,
        voltage_v=capture.voltage_v,
    )
    json_path.write_text(
        json.dumps(capture.metadata_dict(), indent=2),
        encoding="utf-8",
    )

    paths = {"npz": npz_path, "json": json_path}

    if write_csv:
        csv_path = output_dir / f"{stem}.csv"
        np.savetxt(
            csv_path,
            np.column_stack((capture.time_s, capture.voltage_v)),
            delimiter=",",
            header="time_s,voltage_v",
            comments="",
            fmt="%.12e",
        )
        paths["csv"] = csv_path

    return paths


def pick_time_scale(time_s: np.ndarray) -> tuple[np.ndarray, str]:
    span = float(np.max(time_s) - np.min(time_s))
    if span < 1e-6:
        return time_s * 1e9, "ns"
    if span < 1e-3:
        return time_s * 1e6, "µs"
    if span < 1:
        return time_s * 1e3, "ms"
    return time_s, "s"


def pick_voltage_scale(voltage_v: np.ndarray) -> tuple[np.ndarray, str]:
    peak = float(np.max(np.abs(voltage_v))) if len(voltage_v) else 0.0
    if peak >= 1000:
        return voltage_v / 1000.0, "kV"
    return voltage_v, "V"


def decimate_minmax(
    time_s: np.ndarray,
    voltage_v: np.ndarray,
    max_points: int,
) -> tuple[np.ndarray, np.ndarray]:
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
        order = (min_idx, max_idx) if min_idx <= max_idx else (max_idx, min_idx)
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
    time_s, voltage_v, metadata = load_waveform(npz_path)

    plot_t, plot_v = decimate_minmax(time_s, voltage_v, max_points)
    display_t, time_unit = pick_time_scale(plot_t)
    display_v, volt_unit = pick_voltage_scale(plot_v)

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
        parts = [f"Channel {channel}", f"{points:,} points"]
        dt = metadata.get("x_increment_s")
        tdiv = metadata.get("timebase_s_div")
        if dt is not None:
            parts.append(f"dt={dt:.3g} s")
        if tdiv is not None:
            parts.append(f"{tdiv:.3g} s/div")
        captured = metadata.get("captured_at", "")
        if captured:
            parts.append(str(captured))
        title = " | ".join(parts)
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
