"""Spark-gap event detection and feature extraction.

Field names and units match SPARK_GAP_METRICS.md. Storage is SI (s, V, V/s).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

DEFAULT_DROP_THRESHOLD_V = 5000.0
DEFAULT_DROP_WINDOW_S = 100e-9
DEFAULT_MERGE_GAP_S = 5e-6
DEFAULT_COARSE_STEP_S = 50e-9
DEFAULT_SCOPE_BW_HZ = 100e6

REFINE_WINDOW_S = 2e-6
STEEP_WINDOW_S = 20e-9
PLATEAU_PRE_S = 50e-9
PLATEAU_EXCLUDE_S = 5e-9
UNDERSHOOT_S = 200e-9
RESIDUAL_START_S = 1e-6
RESIDUAL_END_S = 5e-6
RAMP_END_MARGIN_S = 100e-9
PEAK_SLEW_WINDOW_S = 5e-9
T10_SEARCH_PAD_S = 15e-9
COLLAPSE_SNIPPET_PRE_S = 40e-9
COLLAPSE_SNIPPET_POST_S = 160e-9
POST_SNIPPET_S = 400e-9
RAMP_FIT_MAX_POINTS = 25_000
FIRST_CYCLE_RATE_FRACTION = 0.35

EVENT_FIELDS: dict[str, dict[str, str]] = {
    "event_index": {
        "unit": "1",
        "definition": "0-based order of detected collapses. Used to plot conditioning.",
    },
    "first_cycle": {
        "unit": "bool",
        "definition": (
            "True when the preceding ramp is much flatter than the later population "
            "(startup / long plateau). Kept in the event table; excluded from typical stats."
        ),
    },
    "t_break": {
        "unit": "s",
        "definition": (
            "Collapse-start time: first sample of the steepest ~20 ns drop. "
            "Event timestamp for all intervals; not the 1-sample voltage maximum."
        ),
    },
    "v_breakdown": {
        "unit": "V",
        "definition": (
            "Median voltage over the ~20-50 ns plateau immediately before t_break. "
            "Repetitive breakdown voltage for that shot."
        ),
    },
    "v_undershoot": {
        "unit": "V",
        "definition": (
            "Minimum in the ~200 ns after t_break. Includes inductive kick / probe ringing."
        ),
    },
    "v_residual": {
        "unit": "V",
        "definition": (
            "Median voltage after ringing settles (~1-5 us post-edge), at the start of the "
            "next charging ramp. Recovery / restart voltage for the next cycle."
        ),
    },
    "dv_collapse": {
        "unit": "V",
        "definition": "v_breakdown - v_undershoot. Total observed swing including undershoot.",
    },
    "period_s": {
        "unit": "s",
        "definition": "t_break[i] - t_break[i-1]. Undefined for event 0.",
    },
    "rep_rate_hz": {
        "unit": "Hz",
        "definition": "1 / period_s for that interval.",
    },
    "v10": {
        "unit": "V",
        "definition": "v_pre - 0.1 * (v_pre - v_post). 10% collapse level.",
    },
    "v90": {
        "unit": "V",
        "definition": "v_pre - 0.9 * (v_pre - v_post). 90% collapse level.",
    },
    "t10": {
        "unit": "s",
        "definition": "Absolute time of the first 10% crossing on the falling edge.",
    },
    "t90": {
        "unit": "s",
        "definition": "Absolute time of the first 90% crossing on the falling edge.",
    },
    "t_collapse_10_90": {
        "unit": "s",
        "definition": "t90 - t10. IEC/scope 10-90% fall time of the discharge edge.",
    },
    "slew_collapse_mean": {
        "unit": "V/s",
        "definition": (
            "0.8 * (v_pre - v_post) / t_collapse_10_90. Mean collapse gradient; a lower bound "
            "set by scope bandwidth."
        ),
    },
    "slew_collapse_peak": {
        "unit": "V/s",
        "definition": "Maximum |dV/dt| over a 5 ns window on the falling edge.",
    },
    "t_ring": {
        "unit": "s",
        "definition": (
            "Dominant post-collapse ring period in the 50-400 ns window "
            "(median peak spacing of the residual after a short moving mean)."
        ),
    },
    "charge_rate": {
        "unit": "V/s",
        "definition": (
            "Least-squares slope of the linear charging ramp from residual settle to "
            "~100 ns before t_break. I ≈ C * charge_rate if C is known."
        ),
    },
    "charge_intercept": {
        "unit": "V",
        "definition": "Intercept of V = a + b t on the charging-ramp fit (for overlays).",
    },
    "charge_r2": {
        "unit": "1",
        "definition": "Coefficient of determination of the linear ramp fit. Low R² flags a non-linear ramp.",
    },
    "v_charge_start": {
        "unit": "V",
        "definition": "Voltage at the start of the ramp-fit window.",
    },
    "v_charge_end": {
        "unit": "V",
        "definition": "Voltage at the end of the ramp-fit window (near v_breakdown).",
    },
    "recovery_s": {
        "unit": "s",
        "definition": (
            "Time from t_break until a short local slope matches the next-cycle charge rate. "
            "Duration of the post-spark transient."
        ),
    },
    "energy_j": {
        "unit": "J",
        "definition": "0.5 * C * v_breakdown^2. Only when --capacitance is given.",
    },
    "charge_c": {
        "unit": "C",
        "definition": "C * (v_breakdown - v_residual). Only when --capacitance is given.",
    },
    "source_current_a": {
        "unit": "A",
        "definition": "C * charge_rate during the linear ramp. Only when --capacitance is given.",
    },
    "L_est_h": {
        "unit": "H",
        "definition": "1 / ((2π / t_ring)^2 * C) if t_ring and C are known.",
    },
}

SUMMARY_QUANTITIES = (
    "v_breakdown",
    "v_residual",
    "v_undershoot",
    "dv_collapse",
    "period_s",
    "charge_rate",
    "t_collapse_10_90",
    "slew_collapse_mean",
)

CSV_COLUMNS = (
    "event_index",
    "first_cycle",
    "t_break",
    "v_breakdown",
    "v_undershoot",
    "v_residual",
    "dv_collapse",
    "period_s",
    "rep_rate_hz",
    "t_collapse_10_90",
    "slew_collapse_mean",
    "slew_collapse_peak",
    "t_ring",
    "charge_rate",
    "charge_r2",
    "v_charge_start",
    "v_charge_end",
    "recovery_s",
    "energy_j",
    "charge_c",
    "source_current_a",
    "L_est_h",
)


def _sample_interval(time_s: np.ndarray) -> float:
    if len(time_s) < 2:
        return 1e-9
    return float(np.median(np.diff(time_s[: min(len(time_s), 2000)])))


def _as_index(offset_s: float, dt: float) -> int:
    return max(1, int(round(offset_s / dt)))


def _finite(value: float | None) -> bool:
    return value is not None and np.isfinite(value)


def describe(values: list[float | None] | np.ndarray) -> dict[str, float | int | None]:
    """Population descriptors used in the summary JSON."""
    x = np.asarray([v for v in values if _finite(v)], dtype=np.float64)
    if len(x) == 0:
        return {
            "n": 0,
            "mean": None,
            "std": None,
            "median": None,
            "min": None,
            "max": None,
            "p05": None,
            "p95": None,
            "cv": None,
        }
    mean = float(np.mean(x))
    std = float(np.std(x, ddof=1)) if len(x) > 1 else 0.0
    return {
        "n": int(len(x)),
        "mean": mean,
        "std": std,
        "median": float(np.median(x)),
        "min": float(np.min(x)),
        "max": float(np.max(x)),
        "p05": float(np.percentile(x, 5)),
        "p95": float(np.percentile(x, 95)),
        "cv": (std / mean) if mean != 0.0 else None,
    }


def pearson(x: list[float | None], y: list[float | None]) -> float | None:
    xa: list[float] = []
    ya: list[float] = []
    for xv, yv in zip(x, y):
        if _finite(xv) and _finite(yv):
            xa.append(float(xv))
            ya.append(float(yv))
    if len(xa) < 3:
        return None
    corr = np.corrcoef(np.asarray(xa), np.asarray(ya))[0, 1]
    if not np.isfinite(corr):
        return None
    return float(corr)


def linear_fit(time_s: np.ndarray, voltage_v: np.ndarray) -> tuple[float, float, float, float, float] | None:
    """Return (slope, intercept, r2, v_start, v_end) or None."""
    if len(time_s) < 10:
        return None
    t = time_s
    v = voltage_v
    if len(t) > RAMP_FIT_MAX_POINTS:
        step = int(np.ceil(len(t) / RAMP_FIT_MAX_POINTS))
        t = t[::step]
        v = v[::step]
    slope, intercept = np.polyfit(t, v, 1)
    predicted = intercept + slope * t
    residual = v - predicted
    ss_res = float(np.sum(residual**2))
    ss_tot = float(np.sum((v - np.mean(v)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else 0.0
    return float(slope), float(intercept), float(r2), float(voltage_v[0]), float(voltage_v[-1])


def _merge_hits(hits: np.ndarray, merge_samples: int) -> list[list[int]]:
    if len(hits) == 0:
        return []
    groups: list[list[int]] = [[int(hits[0])]]
    for hit in hits[1:]:
        if int(hit) - groups[-1][-1] <= merge_samples:
            groups[-1].append(int(hit))
        else:
            groups.append([int(hit)])
    return groups


def detect_coarse_events(
    time_s: np.ndarray,
    voltage_v: np.ndarray,
    drop_threshold_v: float = DEFAULT_DROP_THRESHOLD_V,
    drop_window_s: float = DEFAULT_DROP_WINDOW_S,
    merge_gap_s: float = DEFAULT_MERGE_GAP_S,
    coarse_step_s: float = DEFAULT_COARSE_STEP_S,
) -> list[int]:
    """Return seed sample indices of large negative voltage drops."""
    dt = _sample_interval(time_s)
    step = _as_index(coarse_step_s, dt)
    win = _as_index(drop_window_s, dt)
    merge = _as_index(merge_gap_s, dt)
    if len(voltage_v) <= win:
        return []

    idx = np.arange(0, len(voltage_v) - win, step)
    drops = voltage_v[idx] - voltage_v[idx + win]
    hits = idx[drops >= drop_threshold_v]
    groups = _merge_hits(hits, merge)
    seeds: list[int] = []
    for group in groups:
        group_arr = np.asarray(group, dtype=int)
        local_drops = voltage_v[group_arr] - voltage_v[np.minimum(group_arr + win, len(voltage_v) - 1)]
        seeds.append(int(group_arr[int(np.argmax(local_drops))]))
    return seeds


def _first_crossing_at_or_below(voltage_v: np.ndarray, level: float) -> int | None:
    below = np.nonzero(voltage_v <= level)[0]
    if len(below) == 0:
        return None
    return int(below[0])


def _estimate_ring_period(time_s: np.ndarray, voltage_v: np.ndarray, dt: float) -> float | None:
    if len(voltage_v) < 20:
        return None
    win = _as_index(15e-9, dt)
    if win % 2 == 0:
        win += 1
    kernel = np.ones(win, dtype=np.float64) / win
    trend = np.convolve(voltage_v, kernel, mode="same")
    residual = voltage_v - trend
    if len(residual) < 3:
        return None
    thr = 0.3 * float(np.std(residual))
    mid = residual[1:-1]
    peaks = np.nonzero((mid > residual[:-2]) & (mid >= residual[2:]) & (mid > thr))[0] + 1
    if len(peaks) < 2:
        return None
    periods = np.diff(time_s[peaks])
    periods = periods[(periods > 5e-9) & (periods < 80e-9)]
    if len(periods) == 0:
        return None
    return float(np.median(periods))


def _peak_slew(voltage_v: np.ndarray, dt: float, start: int, stop: int) -> float | None:
    win = _as_index(PEAK_SLEW_WINDOW_S, dt)
    lo = max(0, start)
    hi = min(len(voltage_v) - win, stop)
    if hi <= lo:
        return None
    drops = voltage_v[lo:hi] - voltage_v[lo + win : hi + win]
    if len(drops) == 0:
        return None
    return float(np.max(drops) / (win * dt))


@dataclass
class SparkGapEvent:
    """One detected breakdown. Names match SPARK_GAP_METRICS.md."""

    event_index: int
    first_cycle: bool
    t_break: float
    v_breakdown: float
    v_undershoot: float
    v_residual: float
    dv_collapse: float
    period_s: float | None
    rep_rate_hz: float | None
    v10: float
    v90: float
    t10: float
    t90: float
    t_collapse_10_90: float
    slew_collapse_mean: float
    slew_collapse_peak: float | None
    t_ring: float | None
    charge_rate: float | None = None
    charge_intercept: float | None = None
    charge_r2: float | None = None
    v_charge_start: float | None = None
    v_charge_end: float | None = None
    recovery_s: float | None = None
    energy_j: float | None = None
    charge_c: float | None = None
    source_current_a: float | None = None
    L_est_h: float | None = None
    t_break_index: int = 0
    t10_index: int = 0
    ramp_start_index: int | None = None
    ramp_stop_index: int | None = None
    collapse_t_s: np.ndarray = field(default_factory=lambda: np.asarray([]), repr=False)
    collapse_v: np.ndarray = field(default_factory=lambda: np.asarray([]), repr=False)
    post_t_s: np.ndarray = field(default_factory=lambda: np.asarray([]), repr=False)
    post_v: np.ndarray = field(default_factory=lambda: np.asarray([]), repr=False)
    ramp_t_s: np.ndarray = field(default_factory=lambda: np.asarray([]), repr=False)
    ramp_v: np.ndarray = field(default_factory=lambda: np.asarray([]), repr=False)

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {}
        for name in CSV_COLUMNS:
            value = getattr(self, name)
            if isinstance(value, (np.floating, np.integer, np.bool_)):
                value = value.item()
            record[name] = value
        return record


@dataclass
class AnalysisResult:
    events: list[SparkGapEvent]
    summary: dict[str, Any]
    detection: dict[str, Any]
    metadata: dict[str, Any]
    time_s: np.ndarray
    voltage_v: np.ndarray
    sample_interval_s: float


def _refine_one(
    time_s: np.ndarray,
    voltage_v: np.ndarray,
    seed_idx: int,
    dt: float,
    event_index: int,
) -> SparkGapEvent:
    steep_win = _as_index(STEEP_WINDOW_S, dt)
    refine = _as_index(REFINE_WINDOW_S, dt)
    lo = max(0, seed_idx - refine)
    hi = min(len(voltage_v) - steep_win, seed_idx + refine)
    loc = np.arange(lo, max(lo + 1, hi))
    drops = voltage_v[loc] - voltage_v[loc + steep_win]
    t_break_index = int(loc[int(np.argmax(drops))])

    pre_lo = max(0, t_break_index - _as_index(PLATEAU_PRE_S, dt))
    pre_hi = max(pre_lo + 1, t_break_index - _as_index(PLATEAU_EXCLUDE_S, dt))
    v_breakdown = float(np.median(voltage_v[pre_lo:pre_hi]))

    post_hi = min(len(voltage_v), t_break_index + _as_index(UNDERSHOOT_S, dt))
    post_seg = voltage_v[t_break_index:post_hi]
    if len(post_seg) == 0:
        post_seg = voltage_v[t_break_index : t_break_index + 1]
    v_undershoot = float(np.min(post_seg))
    dv_collapse = v_breakdown - v_undershoot

    res_lo = min(len(voltage_v) - 1, t_break_index + _as_index(RESIDUAL_START_S, dt))
    res_hi = min(len(voltage_v), t_break_index + _as_index(RESIDUAL_END_S, dt))
    if res_hi <= res_lo:
        v_residual = float(voltage_v[min(len(voltage_v) - 1, t_break_index)])
    else:
        v_residual = float(np.median(voltage_v[res_lo:res_hi]))

    # Immediate trough of the main fall, not the later ring minimum.
    edge_hi = min(len(voltage_v), t_break_index + _as_index(STEEP_WINDOW_S + 15e-9, dt))
    v_post = float(np.min(voltage_v[t_break_index:edge_hi]))
    v_pre = v_breakdown
    edge_dv = max(v_pre - v_post, 200.0)
    v10 = v_pre - 0.1 * edge_dv
    v90 = v_pre - 0.9 * edge_dv

    search_lo = max(0, t_break_index - _as_index(T10_SEARCH_PAD_S, dt))
    search_hi = min(len(voltage_v), t_break_index + _as_index(80e-9, dt))
    search = voltage_v[search_lo:search_hi]
    rel90 = _first_crossing_at_or_below(search, v90)
    if rel90 is None:
        rel90 = min(len(search) - 1, t_break_index - search_lo + _as_index(STEEP_WINDOW_S, dt))
    before_90 = search[: rel90 + 1]
    above_10 = np.nonzero(before_90 >= v10)[0]
    if len(above_10):
        rel10 = int(above_10[-1])
    else:
        rel10 = 0
    if rel90 < rel10:
        rel90 = rel10
    t10_index = search_lo + rel10
    t90_index = search_lo + rel90
    t_collapse = max(time_s[t90_index] - time_s[t10_index], dt)
    slew_mean = 0.8 * edge_dv / t_collapse
    slew_peak = _peak_slew(voltage_v, dt, t10_index, t90_index + _as_index(10e-9, dt))

    ring_lo = min(len(voltage_v) - 1, t_break_index + _as_index(50e-9, dt))
    ring_hi = min(len(voltage_v), t_break_index + _as_index(POST_SNIPPET_S, dt))
    t_ring = _estimate_ring_period(time_s[ring_lo:ring_hi], voltage_v[ring_lo:ring_hi], dt)

    col_lo = max(0, t10_index - _as_index(COLLAPSE_SNIPPET_PRE_S, dt))
    col_hi = min(len(voltage_v), t10_index + _as_index(COLLAPSE_SNIPPET_POST_S, dt))
    post_lo = t_break_index
    post_end = min(len(voltage_v), t_break_index + _as_index(POST_SNIPPET_S, dt))

    return SparkGapEvent(
        event_index=event_index,
        first_cycle=False,
        t_break=float(time_s[t_break_index]),
        v_breakdown=v_breakdown,
        v_undershoot=v_undershoot,
        v_residual=v_residual,
        dv_collapse=dv_collapse,
        period_s=None,
        rep_rate_hz=None,
        v10=v10,
        v90=v90,
        t10=float(time_s[t10_index]),
        t90=float(time_s[t90_index]),
        t_collapse_10_90=float(t_collapse),
        slew_collapse_mean=float(slew_mean),
        slew_collapse_peak=slew_peak,
        t_ring=t_ring,
        t_break_index=t_break_index,
        t10_index=t10_index,
        collapse_t_s=time_s[col_lo:col_hi] - time_s[t10_index],
        collapse_v=voltage_v[col_lo:col_hi].copy(),
        post_t_s=time_s[post_lo:post_end] - time_s[t_break_index],
        post_v=voltage_v[post_lo:post_end].copy(),
    )


def _fill_periods(events: list[SparkGapEvent]) -> None:
    for i, event in enumerate(events):
        if i == 0:
            continue
        period = event.t_break - events[i - 1].t_break
        event.period_s = float(period)
        event.rep_rate_hz = (1.0 / period) if period > 0 else None


def _ramp_window(
    events: list[SparkGapEvent],
    event: SparkGapEvent,
    n_samples: int,
    dt: float,
) -> tuple[int, int]:
    margin = _as_index(RAMP_END_MARGIN_S, dt)
    stop = max(1, event.t_break_index - margin)
    if event.event_index == 0:
        start = 0
    else:
        prev = events[event.event_index - 1]
        start = min(n_samples - 1, prev.t_break_index + _as_index(RESIDUAL_END_S, dt))
    if start >= stop:
        start = max(0, stop - _as_index(10e-6, dt))
    return start, stop


def _fill_ramps(
    time_s: np.ndarray,
    voltage_v: np.ndarray,
    events: list[SparkGapEvent],
    dt: float,
) -> None:
    for event in events:
        start, stop = _ramp_window(events, event, len(voltage_v), dt)
        event.ramp_start_index = start
        event.ramp_stop_index = stop
        t_win = time_s[start:stop]
        v_win = voltage_v[start:stop]
        event.ramp_t_s = t_win - t_win[0] if len(t_win) else t_win
        if len(v_win) > 4000:
            step = int(np.ceil(len(v_win) / 4000))
            event.ramp_t_s = event.ramp_t_s[::step]
            event.ramp_v = v_win[::step].copy()
        else:
            event.ramp_v = v_win.copy()
        fit = linear_fit(t_win, v_win)
        if fit is None:
            continue
        slope, intercept, r2, v0, v1 = fit
        event.charge_rate = slope
        event.charge_intercept = intercept
        event.charge_r2 = r2
        event.v_charge_start = v0
        event.v_charge_end = v1


def _estimate_recovery(
    time_s: np.ndarray,
    voltage_v: np.ndarray,
    t_break_index: int,
    charge_rate: float | None,
    dt: float,
) -> float | None:
    start = t_break_index + _as_index(500e-9, dt)
    win = _as_index(1e-6, dt)
    step = _as_index(50e-9, dt)
    if start + win >= len(voltage_v):
        return None
    for i in range(start, len(voltage_v) - win, step):
        slope = (voltage_v[i + win] - voltage_v[i]) / (time_s[i + win] - time_s[i])
        if charge_rate is None or abs(charge_rate) < 1.0:
            if slope > 0:
                return float(time_s[i] - time_s[t_break_index])
        elif 0.4 * charge_rate <= slope <= 2.5 * charge_rate:
            return float(time_s[i] - time_s[t_break_index])
    return None


def _fill_recovery(
    time_s: np.ndarray,
    voltage_v: np.ndarray,
    events: list[SparkGapEvent],
    dt: float,
) -> None:
    for i, event in enumerate(events):
        next_rate = events[i + 1].charge_rate if i + 1 < len(events) else event.charge_rate
        event.recovery_s = _estimate_recovery(time_s, voltage_v, event.t_break_index, next_rate, dt)


def _flag_first_cycles(events: list[SparkGapEvent]) -> None:
    for event in events:
        event.first_cycle = False
    rates = [e.charge_rate for e in events if _finite(e.charge_rate)]
    if len(rates) < 3:
        if events:
            events[0].first_cycle = True
        return
    arr = np.asarray(rates, dtype=np.float64)
    high = arr[arr >= np.percentile(arr, 25)]
    ref = float(np.median(high)) if len(high) else float(np.median(arr))
    for event in events:
        if _finite(event.charge_rate) and event.charge_rate < FIRST_CYCLE_RATE_FRACTION * ref:
            event.first_cycle = True
    if events and all(event.first_cycle for event in events):
        for event in events:
            event.first_cycle = False
        events[0].first_cycle = True


def _apply_capacitance(events: list[SparkGapEvent], capacitance_f: float | None) -> None:
    if capacitance_f is None or capacitance_f <= 0:
        return
    two_pi = 2.0 * np.pi
    for event in events:
        event.energy_j = 0.5 * capacitance_f * event.v_breakdown**2
        event.charge_c = capacitance_f * (event.v_breakdown - event.v_residual)
        if _finite(event.charge_rate):
            event.source_current_a = capacitance_f * float(event.charge_rate)
        if _finite(event.t_ring) and event.t_ring > 0:
            omega = two_pi / event.t_ring
            event.L_est_h = 1.0 / (omega**2 * capacitance_f)


def _population(events: list[SparkGapEvent]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in SUMMARY_QUANTITIES:
        out[name] = describe([getattr(event, name) for event in events])
    return out


def _named_figures(typical: list[SparkGapEvent]) -> dict[str, Any]:
    v_bd = [e.v_breakdown for e in typical]
    periods = [e.period_s for e in typical]
    rates = [e.charge_rate for e in typical]
    ratios = [
        e.v_residual / e.v_breakdown
        for e in typical
        if e.v_breakdown != 0
    ]
    stats_bd = describe(v_bd)
    stats_period = describe(periods)
    indices = [float(e.event_index) for e in typical]
    cond = linear_fit(np.asarray(indices), np.asarray(v_bd)) if len(typical) >= 3 else None
    return {
        "v_bd_cv": stats_bd.get("cv"),
        "recovery_ratio_mean": float(np.mean(ratios)) if ratios else None,
        "period_jitter_s": stats_period.get("std"),
        "period_jitter_frac": stats_period.get("cv"),
        "rep_rate_mean_hz": (
            (1.0 / stats_period["mean"]) if stats_period.get("mean") else None
        ),
        "corr_vbd_period": pearson(v_bd, periods),
        "corr_vbd_charge_rate": pearson(v_bd, rates),
        "conditioning_slope": cond[0] if cond is not None else None,
    }


def _dedupe_events(events: list[SparkGapEvent], merge_gap_s: float) -> list[SparkGapEvent]:
    if not events:
        return []
    kept = [events[0]]
    for event in events[1:]:
        if event.t_break - kept[-1].t_break < merge_gap_s:
            if event.dv_collapse > kept[-1].dv_collapse:
                kept[-1] = event
            continue
        kept.append(event)
    for i, event in enumerate(kept):
        event.event_index = i
    return kept


def analyze_waveform(
    time_s: np.ndarray,
    voltage_v: np.ndarray,
    metadata: dict[str, Any] | None = None,
    drop_threshold_v: float = DEFAULT_DROP_THRESHOLD_V,
    drop_window_s: float = DEFAULT_DROP_WINDOW_S,
    merge_gap_s: float = DEFAULT_MERGE_GAP_S,
    coarse_step_s: float = DEFAULT_COARSE_STEP_S,
    scope_bw_hz: float = DEFAULT_SCOPE_BW_HZ,
    capacitance_f: float | None = None,
) -> AnalysisResult:
    """Detect breakdowns and compute the documented spark-gap features."""
    time_s = np.asarray(time_s, dtype=np.float64)
    voltage_v = np.asarray(voltage_v, dtype=np.float64)
    metadata = dict(metadata or {})
    dt = float(metadata.get("x_increment_s") or _sample_interval(time_s))
    lsb_v = float(metadata.get("y_increment_v") or 0.0)
    sample_rate = float(metadata.get("sample_rate_hz") or (1.0 / dt if dt else 0.0))

    seeds = detect_coarse_events(
        time_s,
        voltage_v,
        drop_threshold_v=drop_threshold_v,
        drop_window_s=drop_window_s,
        merge_gap_s=merge_gap_s,
        coarse_step_s=coarse_step_s,
    )
    events = [_refine_one(time_s, voltage_v, seed, dt, i) for i, seed in enumerate(seeds)]
    events = _dedupe_events(events, merge_gap_s)
    _fill_periods(events)
    _fill_ramps(time_s, voltage_v, events, dt)
    _fill_recovery(time_s, voltage_v, events, dt)
    _flag_first_cycles(events)
    _apply_capacitance(events, capacitance_f)

    typical = [event for event in events if not event.first_cycle]
    detection = {
        "drop_threshold_v": drop_threshold_v,
        "drop_window_s": drop_window_s,
        "merge_gap_s": merge_gap_s,
        "coarse_step_s": coarse_step_s,
        "n_events": len(events),
        "n_typical": len(typical),
        "sample_rate_hz": sample_rate,
        "sample_interval_s": dt,
        "y_increment_v": lsb_v,
        "lsb_v": lsb_v,
        "scope_bw_hz": scope_bw_hz,
        "scope_t1090_limit_s": (0.35 / scope_bw_hz) if scope_bw_hz else None,
        "capacitance_f": capacitance_f,
    }
    summary = {
        "all": _population(events),
        "typical": _population(typical),
        **_named_figures(typical if typical else events),
        "n_events": len(events),
        "n_typical": len(typical),
    }
    return AnalysisResult(
        events=events,
        summary=summary,
        detection=detection,
        metadata=metadata,
        time_s=time_s,
        voltage_v=voltage_v,
        sample_interval_s=dt,
    )


def typical_events(events: list[SparkGapEvent], include_first: bool) -> list[SparkGapEvent]:
    if include_first:
        return list(events)
    pool = [event for event in events if not event.first_cycle]
    return pool or list(events)


def pick_representatives(
    events: list[SparkGapEvent],
    count: int = 5,
    include_first: bool = False,
) -> list[SparkGapEvent]:
    pool = typical_events(events, include_first)
    pool = sorted(pool, key=lambda event: event.v_breakdown)
    if len(pool) <= count:
        return pool
    indexes = np.linspace(0, len(pool) - 1, count)
    chosen = [pool[int(round(i))] for i in indexes]
    seen: set[int] = set()
    unique: list[SparkGapEvent] = []
    for event in chosen:
        if event.event_index not in seen:
            unique.append(event)
            seen.add(event.event_index)
    return unique


def scope_limit_footer(detection: dict[str, Any]) -> str:
    bw = detection.get("scope_bw_hz") or DEFAULT_SCOPE_BW_HZ
    rate = detection.get("sample_rate_hz") or 0.0
    lsb = detection.get("lsb_v") or 0.0
    bw_mhz = bw / 1e6
    gs = rate / 1e9
    lsb_txt = f"{lsb:.0f} V LSB" if lsb else "LSB unknown"
    return f"{bw_mhz:.0f} MHz scope, {gs:.3g} GS/s, {lsb_txt}; slew is a lower bound"
