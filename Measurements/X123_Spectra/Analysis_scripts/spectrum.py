"""Histogram math for X-123 energy spectra."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def odd_window(n: int) -> int:
    value = max(1, int(n))
    if value % 2 == 0:
        value += 1
    return value


def window_kev_to_channels(window_kev: float, slope_kev_per_channel: float) -> int:
    slope = abs(float(slope_kev_per_channel)) or 1.0
    n = max(1, int(round(abs(float(window_kev)) / slope)))
    return odd_window(n)


def moving_average(counts: np.ndarray, window_channels: int) -> np.ndarray:
    """Uniform moving average in channel space. Window 1 leaves the histogram unchanged."""
    n = odd_window(window_channels)
    values = np.asarray(counts, dtype=np.float64)
    if n <= 1 or values.size == 0:
        return values.copy()
    kernel = np.ones(n, dtype=np.float64) / n
    return np.convolve(values, kernel, mode="same")


def to_cps(counts: np.ndarray, live_time_s: float | None) -> np.ndarray:
    values = np.asarray(counts, dtype=np.float64)
    if live_time_s is None or live_time_s <= 0:
        raise ValueError("live_time_s is required for counts/s")
    return values / float(live_time_s)


def normalize_max(counts: np.ndarray) -> np.ndarray:
    values = np.asarray(counts, dtype=np.float64)
    peak = float(np.max(values)) if values.size else 0.0
    if peak <= 0:
        return values.copy()
    return values / peak


def normalize_integral(counts: np.ndarray) -> np.ndarray:
    values = np.asarray(counts, dtype=np.float64)
    total = float(np.sum(values))
    if total <= 0:
        return values.copy()
    return values / total


def difference(
    energy_a: np.ndarray,
    counts_a: np.ndarray,
    energy_b: np.ndarray,
    counts_b: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """A minus B, interpolated onto A's energy axis."""
    x_a = np.asarray(energy_a, dtype=np.float64)
    y_a = np.asarray(counts_a, dtype=np.float64)
    y_b = np.interp(
        x_a,
        np.asarray(energy_b, dtype=np.float64),
        np.asarray(counts_b, dtype=np.float64),
        left=0.0,
        right=0.0,
    )
    return x_a, y_a - y_b


@dataclass
class RoiStats:
    e_lo: float
    e_hi: float
    integral: float
    centroid_kev: float | None
    fwhm_kev: float | None
    peak_kev: float | None
    peak_counts: float


def roi_stats(energy_kev: np.ndarray, counts: np.ndarray, e_lo: float, e_hi: float) -> RoiStats:
    energy = np.asarray(energy_kev, dtype=np.float64)
    values = np.asarray(counts, dtype=np.float64)
    lo, hi = (e_lo, e_hi) if e_lo <= e_hi else (e_hi, e_lo)
    mask = (energy >= lo) & (energy <= hi)
    if not np.any(mask):
        return RoiStats(lo, hi, 0.0, None, None, None, 0.0)
    e = energy[mask]
    y = values[mask]
    total = float(np.sum(y))
    peak_i = int(np.argmax(y))
    peak_kev = float(e[peak_i])
    peak_counts = float(y[peak_i])
    centroid = float(np.sum(e * y) / total) if total > 0 else None
    return RoiStats(
        e_lo=lo,
        e_hi=hi,
        integral=total,
        centroid_kev=centroid,
        fwhm_kev=_fwhm(e, y),
        peak_kev=peak_kev,
        peak_counts=peak_counts,
    )


def _fwhm(energy: np.ndarray, counts: np.ndarray) -> float | None:
    if counts.size < 3:
        return None
    peak = float(np.max(counts))
    if peak <= 0:
        return None
    half = 0.5 * peak
    above = counts >= half
    if not np.any(above):
        return None
    i0 = int(np.argmax(above))
    i1 = int(len(above) - 1 - np.argmax(above[::-1]))
    return float(energy[i1] - energy[i0]) if i1 > i0 else None


def local_maxima(
    energy_kev: np.ndarray,
    counts: np.ndarray,
    *,
    min_fraction: float = 0.05,
    min_separation_kev: float = 0.2,
) -> list[tuple[float, float]]:
    """Return (energy, counts) peaks above ``min_fraction`` of the global max."""
    energy = np.asarray(energy_kev, dtype=np.float64)
    values = np.asarray(counts, dtype=np.float64)
    if values.size < 3:
        return []
    peak = float(np.max(values))
    if peak <= 0:
        return []
    threshold = min_fraction * peak
    candidates: list[tuple[int, float, float]] = []
    for i in range(1, len(values) - 1):
        if values[i] < threshold:
            continue
        if values[i] >= values[i - 1] and values[i] > values[i + 1]:
            candidates.append((i, float(energy[i]), float(values[i])))
    candidates.sort(key=lambda row: row[2], reverse=True)
    kept: list[tuple[float, float]] = []
    used: list[float] = []
    for _i, e, y in candidates:
        if any(abs(e - other) < min_separation_kev for other in used):
            continue
        kept.append((e, y))
        used.append(e)
    kept.sort(key=lambda row: row[0])
    return kept


def mean_std(
    energy_grids: list[np.ndarray],
    count_grids: list[np.ndarray],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Interpolate onto the first energy axis; return energy, mean, std."""
    if not energy_grids:
        empty = np.asarray([], dtype=np.float64)
        return empty, empty, empty
    x0 = np.asarray(energy_grids[0], dtype=np.float64)
    stacked = []
    for energy, counts in zip(energy_grids, count_grids):
        stacked.append(
            np.interp(
                x0,
                np.asarray(energy, dtype=np.float64),
                np.asarray(counts, dtype=np.float64),
                left=0.0,
                right=0.0,
            )
        )
    arr = np.vstack(stacked)
    return x0, np.mean(arr, axis=0), np.std(arr, axis=0, ddof=0)
