"""Vertical-scale decisions and sine THD. No instrument imports."""

from __future__ import annotations

import math

import numpy as np

VERTICAL_DIVISIONS = 8
TARGET_MIN_DIV = 4.0
TARGET_MAX_DIV = 6.0
FLOOR_MIN_DIV = 2.0
RAIL_MARGIN_DIV = 0.25
OFFSET_RECENTER_DIV = 0.5
MAX_HARMONICS = 10
NYQUIST_FRACTION = 0.40

_HALF_DIV = VERTICAL_DIVISIONS / 2.0


def vertical_ladder() -> tuple[float, ...]:
    """1-2-5 volts/div from 1 mV/div through 10 V/div."""
    scales: list[float] = []
    decade = 1e-3
    while decade <= 10.0:
        for mult in (1.0, 2.0, 5.0):
            value = mult * decade
            if value <= 10.0:
                scales.append(value)
        decade *= 10.0
    return tuple(scales)


def nearest_scale(volts_per_div: float) -> float:
    ladder = vertical_ladder()
    if not math.isfinite(volts_per_div) or volts_per_div <= ladder[0]:
        return ladder[0]
    if volts_per_div >= ladder[-1]:
        return ladder[-1]
    return min(ladder, key=lambda step: abs(math.log(step) - math.log(volts_per_div)))


def seed_volts_per_div(seed_vpp: float) -> float:
    """Scale that puts ``seed_vpp`` across about five divisions."""
    if not math.isfinite(seed_vpp) or seed_vpp <= 0:
        return vertical_ladder()[0]
    return nearest_scale(seed_vpp / 5.0)


def next_coarser(volts_per_div: float) -> float | None:
    ladder = vertical_ladder()
    index = ladder.index(nearest_scale(volts_per_div))
    if index >= len(ladder) - 1:
        return None
    return ladder[index + 1]


def next_finer(volts_per_div: float) -> float | None:
    ladder = vertical_ladder()
    index = ladder.index(nearest_scale(volts_per_div))
    if index <= 0:
        return None
    return ladder[index - 1]


def coarser_by(volts_per_div: float, steps: int) -> float | None:
    """Move up the ladder by up to ``steps`` notches. None if already coarsest."""
    scale = nearest_scale(volts_per_div)
    moved: float | None = None
    for _ in range(max(int(steps), 1)):
        nxt = next_coarser(scale)
        if nxt is None:
            break
        moved = nxt
        scale = nxt
    return moved


def needs_recenter(offset_v: float, vavg: float, volts_per_div: float) -> bool:
    if not all(math.isfinite(value) for value in (offset_v, vavg, volts_per_div)):
        return False
    if volts_per_div <= 0:
        return False
    return abs(vavg - offset_v) > OFFSET_RECENTER_DIV * volts_per_div


def classify_vertical(
    volts_per_div: float,
    offset_v: float,
    vmin: float,
    vmax: float,
) -> str:
    """Return ``ok``, ``finer``, ``coarser``, ``below_floor``, or ``clipped``.

    A missing span is off-screen, not a small signal: coarsen, or ``clipped``
    at 10 V/div. ``below_floor`` requires a finite span under 2 divisions at
    1 mV/div.
    """
    scale = nearest_scale(volts_per_div)
    at_min = scale <= vertical_ladder()[0]
    at_max = scale >= vertical_ladder()[-1]
    if not _span_usable(vmin, vmax):
        return "clipped" if at_max else "coarser"
    if _is_clipped(scale, offset_v, vmin, vmax):
        return "clipped" if at_max else "coarser"
    divs = (vmax - vmin) / scale
    if divs < TARGET_MIN_DIV:
        if not at_min:
            return "finer"
        return "below_floor" if divs < FLOOR_MIN_DIV else "ok"
    if divs > TARGET_MAX_DIV:
        return "ok" if at_max else "coarser"
    return "ok"


def status_if_reversed(
    action: str,
    volts_per_div: float,
    offset_v: float,
    vmin: float,
    vmax: float,
) -> str:
    """Status when the next ladder step would undo the step just taken."""
    scale = nearest_scale(volts_per_div)
    at_min = scale <= vertical_ladder()[0]
    if not _span_usable(vmin, vmax):
        return "clipped"
    if action == "coarser" and _is_clipped(scale, offset_v, vmin, vmax):
        return "clipped"
    if action == "finer" and at_min:
        divs = (vmax - vmin) / scale
        if divs < FLOOR_MIN_DIV:
            return "below_floor"
    return "ok"


def sample_span(voltage_v: np.ndarray) -> tuple[float, float]:
    """Finite sample extrema, or NaN when the record is too short to trust."""
    voltage = np.asarray(voltage_v, dtype=float)
    finite = voltage[np.isfinite(voltage)]
    if finite.size < 8:
        return float("nan"), float("nan")
    return float(np.min(finite)), float(np.max(finite))


def decide_vertical(
    volts_per_div: float,
    offset_v: float,
    vmin: float,
    vmax: float,
    samples: np.ndarray | None = None,
) -> str:
    """Choose the ladder step from SCPI extrema, or from samples if those are invalid.

    Sample extrema of a clipped trace sit on the screen rails, so the same
    rule coarsens. A short on-screen trace still fines.
    """
    if _span_usable(vmin, vmax):
        return classify_vertical(volts_per_div, offset_v, vmin, vmax)
    if samples is not None:
        sample_min, sample_max = sample_span(samples)
        if _span_usable(sample_min, sample_max):
            return classify_vertical(volts_per_div, offset_v, sample_min, sample_max)
    return classify_vertical(volts_per_div, offset_v, float("nan"), float("nan"))


def sine_thd(time_s: np.ndarray, voltage_v: np.ndarray, frequency_hz: float) -> float:
    """Fundamental-referenced THD as a fraction. A pure sine is 0.

    ``V1`` is the commanded ``frequency_hz``. Harmonics run through 10, or the
    last integer harmonic below 40% of the sample rate, whichever is smaller.
    """
    time = np.asarray(time_s, dtype=float)
    voltage = np.asarray(voltage_v, dtype=float)
    if time.size < 8 or time.shape != voltage.shape or frequency_hz <= 0:
        return float("nan")
    if not np.isfinite(time).all() or not np.isfinite(voltage).all():
        return float("nan")
    sample_rate = _sample_rate_hz(time)
    if not math.isfinite(sample_rate) or sample_rate <= 0:
        return float("nan")
    harmonic_limit = math.floor(NYQUIST_FRACTION * sample_rate / frequency_hz)
    max_harmonic = int(min(MAX_HARMONICS, harmonic_limit))
    if max_harmonic < 2:
        return float("nan")

    omega = 2.0 * math.pi * frequency_hz
    columns = [np.ones(time.size)]
    for harmonic in range(1, max_harmonic + 1):
        phase = harmonic * omega * time
        columns.append(np.cos(phase))
        columns.append(np.sin(phase))
    coeffs, *_ = np.linalg.lstsq(np.column_stack(columns), voltage, rcond=None)
    amplitudes = []
    for harmonic in range(1, max_harmonic + 1):
        cosine = float(coeffs[1 + 2 * (harmonic - 1)])
        sine = float(coeffs[2 + 2 * (harmonic - 1)])
        amplitudes.append(math.hypot(cosine, sine))
    fundamental = amplitudes[0]
    if fundamental <= 0 or not math.isfinite(fundamental):
        return float("nan")
    harmonic_sum = math.sqrt(sum(amplitude * amplitude for amplitude in amplitudes[1:]))
    return harmonic_sum / fundamental


def _span_usable(vmin: float, vmax: float) -> bool:
    return math.isfinite(vmin) and math.isfinite(vmax) and vmax > vmin


def _is_clipped(scale: float, offset_v: float, vmin: float, vmax: float) -> bool:
    if not _span_usable(vmin, vmax) or not math.isfinite(offset_v) or scale <= 0:
        return False
    rail = _HALF_DIV - RAIL_MARGIN_DIV
    return vmax > offset_v + rail * scale or vmin < offset_v - rail * scale


def _sample_rate_hz(time_s: np.ndarray) -> float:
    steps = np.diff(time_s)
    positive = steps[np.isfinite(steps) & (steps > 0)]
    if positive.size == 0:
        return float("nan")
    return 1.0 / float(np.median(positive))
