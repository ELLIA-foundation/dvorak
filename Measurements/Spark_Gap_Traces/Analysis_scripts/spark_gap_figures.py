"""Drawing-ready figure specs for the ROOT renderer.

Mirrors the matplotlib pack in ``spark_gap_report`` (figures 02–08). Arrays are
plain lists so the GUI can ship them to a ROOT process without importing ROOT.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from spark_gap import (
    AnalysisResult,
    SparkGapEvent,
    pick_representatives,
    scope_limit_footer,
    typical_events,
)

_TAB10 = (
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
)
_BLUE = "#1f77b4"
_ORANGE = "#ff7f0e"
_GREEN = "#2ca02c"
_RED = "#d62728"


def spark_figure_specs(result: AnalysisResult, include_first: bool) -> list[dict[str, Any]]:
    footer = scope_limit_footer(result.detection)
    return [
        _sequential(result, footer),
        _histograms(result, include_first, footer),
        _collapse_overlay(result, include_first, footer),
        _collapse_individuals(result, include_first, footer),
        _ramps(result, include_first, footer),
        _correlations(result, include_first, footer),
        _post_collapse(result, include_first, footer),
    ]


def _sequential(result: AnalysisResult, footer: str) -> dict[str, Any]:
    events = result.events
    indexes = [event.event_index for event in events]
    first = [event for event in events if event.first_cycle]
    panels = []
    columns = (
        ("v_breakdown (kV)", [event.v_breakdown / 1000.0 for event in events], [event.v_breakdown / 1000.0 for event in first]),
        ("period_s (µs)", [_opt(event.period_s, 1e6) for event in events], [_opt(event.period_s, 1e6) for event in first]),
        ("charge_rate (kV/ms)", [_opt(event.charge_rate, 1e-6) for event in events], [_opt(event.charge_rate, 1e-6) for event in first]),
    )
    first_index = [event.event_index for event in first]
    for ylabel, values, first_values in columns:
        series = [
            _series(indexes, values, label="all", color=_BLUE, line="solid", marker="circle", marker_size=0.9),
        ]
        if first:
            series.append(
                _series(
                    first_index,
                    first_values,
                    label="first_cycle",
                    color=_RED,
                    line="none",
                    marker="circle",
                    marker_size=1.2,
                )
            )
        panels.append(
            {
                "x_title": "event_index",
                "y_title": ylabel,
                "series": series,
                "legend_corner": "right",
            }
        )
    slope = result.summary.get("conditioning_slope")
    if slope is not None and panels:
        panels[0]["notes"] = [
            {
                "align": "left",
                "text": f"conditioning_slope = {float(slope) / 1000.0:.2f} kV/event (typical)",
            }
        ]
        panels[0]["title"] = "Sequential statistics (conditioning and jitter)"
    elif panels:
        panels[0]["title"] = "Sequential statistics (conditioning and jitter)"
    return {
        "name": "02_sequential",
        "label": "02 sequential",
        "footer": footer,
        "cols": 1,
        "width": 880,
        "height": 900,
        "panels": panels,
    }


def _histograms(result: AnalysisResult, include_first: bool, footer: str) -> dict[str, Any]:
    pool = typical_events(result.events, include_first)
    panels = [
        _hist([event.v_breakdown for event in pool], 1e-3, "kV", "v_breakdown"),
        _hist([_opt(event.period_s, 1.0) for event in pool], 1e6, "µs", "period_s"),
        _hist([_opt(event.charge_rate, 1.0) for event in pool], 1e-6, "kV/ms", "charge_rate"),
        _hist([event.t_collapse_10_90 for event in pool], 1e9, "ns", "t_collapse_10_90"),
    ]
    title = "All-event histograms" if include_first else "Typical-event histograms"
    return {
        "name": "03_histograms",
        "label": "03 histograms",
        "title": title,
        "footer": footer,
        "cols": 2,
        "width": 960,
        "height": 760,
        "panels": panels,
    }


def _collapse_overlay(result: AnalysisResult, include_first: bool, footer: str) -> dict[str, Any]:
    chosen = pick_representatives(result.events, count=5, include_first=include_first)
    series = []
    points = []
    for event in chosen:
        color = _color(event)
        series.append(
            _series(
                event.collapse_t_s * 1e9,
                event.collapse_v / 1000.0,
                label=f"#{event.event_index}  V_bd={event.v_breakdown / 1000.0:.1f} kV",
                color=color,
                width=2,
            )
        )
        points.append({"x": 0.0, "y": event.v10 / 1000.0, "color": color, "size": 1.0})
    panel: dict[str, Any] = {
        "title": "High-resolution collapse overlay (aligned at t10)",
        "x_title": "Time after 10% crossing (ns)",
        "y_title": "Voltage (kV)",
        "series": series,
        "points": points,
        "vlines": [{"x": 0.0, "color": "#666666", "style": "dashed"}],
    }
    limit = result.detection.get("scope_t1090_limit_s")
    if limit:
        limit_ns = float(limit) * 1e9
        panel["vspans"] = [
            {
                "x0": 0.0,
                "x1": limit_ns,
                "color": "#cccccc",
                "label": f"scope 10–90 limit ({limit_ns:.1f} ns)",
            }
        ]
    return _single(
        "04_collapse_overlay",
        "04 collapse overlay",
        footer,
        panel,
    )


def _collapse_individuals(result: AnalysisResult, include_first: bool, footer: str) -> dict[str, Any]:
    chosen = pick_representatives(result.events, count=5, include_first=include_first)
    panels = []
    for event in chosen:
        color = _color(event)
        t90 = (event.t90 - event.t10) * 1e9
        panels.append(
            {
                "title": (
                    f"Event {event.event_index}  V_bd={event.v_breakdown / 1000.0:.2f} kV"
                    f"  —  {_slew_text(event)}"
                ),
                "x_title": "Time after 10% crossing (ns)",
                "y_title": "kV",
                "legend": False,
                "series": [
                    _series(event.collapse_t_s * 1e9, event.collapse_v / 1000.0, color=color)
                ],
                "hlines": [
                    {"y": event.v10 / 1000.0, "color": "#666666", "style": "dotted"},
                    {"y": event.v90 / 1000.0, "color": "#666666", "style": "dotted"},
                ],
                "vlines": [
                    {"x": 0.0, "color": _RED, "style": "dashed"},
                    {"x": t90, "color": _RED, "style": "dashed"},
                ],
            }
        )
    if not panels:
        panels.append({"title": "Individual high-resolution discharges", "series": []})
    count = len(panels)
    return {
        "name": "05_collapse_individuals",
        "label": "05 collapse individuals",
        "title": "Individual high-resolution discharges",
        "footer": footer,
        "cols": 1,
        "width": 900,
        "height": max(420, 230 * count + 40),
        "panels": panels,
    }


def _ramps(result: AnalysisResult, include_first: bool, footer: str) -> dict[str, Any]:
    series = []
    for event in typical_events(result.events, include_first):
        if len(event.ramp_t_s) == 0:
            continue
        color = _color(event)
        t_us = event.ramp_t_s * 1e6
        series.append(_series(t_us, event.ramp_v / 1000.0, color=color, width=2))
        if (
            event.charge_rate is not None
            and event.charge_intercept is not None
            and event.ramp_start_index is not None
            and len(result.time_s)
        ):
            t_abs = result.time_s[event.ramp_start_index] + event.ramp_t_s
            fit_v = event.charge_intercept + event.charge_rate * t_abs
            r2 = event.charge_r2 if event.charge_r2 is not None else float("nan")
            r2_txt = f"{r2:.3f}" if math.isfinite(r2) else "—"
            series.append(
                _series(
                    t_us,
                    fit_v / 1000.0,
                    color=color,
                    line="dashed",
                    width=2,
                    label=(
                        f"#{event.event_index}  {event.charge_rate / 1e6:.1f} kV/ms  R²={r2_txt}"
                    ),
                )
            )
    return _single(
        "06_ramp_overlay",
        "06 ramp overlay",
        footer,
        {
            "title": "Charging ramps (linear fit V = a + b t)",
            "x_title": "Time after ramp-fit start (µs)",
            "y_title": "Voltage (kV)",
            "series": series,
            "legend_columns": 2,
        },
    )


def _correlations(result: AnalysisResult, include_first: bool, footer: str) -> dict[str, Any]:
    pool = typical_events(result.events, include_first)
    v_bd = [event.v_breakdown / 1000.0 for event in pool]
    period = [_opt(event.period_s, 1e6) for event in pool]
    rate = [_opt(event.charge_rate, 1e-6) for event in pool]
    panels = []
    for values, xlabel, corr, name in (
        (period, "period_s (µs)", result.summary.get("corr_vbd_period"), "corr_vbd_period"),
        (rate, "charge_rate (kV/ms)", result.summary.get("corr_vbd_charge_rate"), "corr_vbd_charge_rate"),
    ):
        corr_txt = "n/a" if corr is None else f"{float(corr):.3f}"
        panels.append(
            {
                "title": f"{name} = {corr_txt}",
                "x_title": xlabel,
                "y_title": "v_breakdown (kV)",
                "legend": False,
                "series": [
                    _series(values, v_bd, color=_BLUE, line="none", marker="circle", marker_size=1.1)
                ],
            }
        )
    return {
        "name": "07_correlations",
        "label": "07 correlations",
        "title": "Breakdown voltage correlations (typical events)",
        "footer": footer,
        "cols": 2,
        "width": 960,
        "height": 480,
        "panels": panels,
    }


def _post_collapse(result: AnalysisResult, include_first: bool, footer: str) -> dict[str, Any]:
    chosen = pick_representatives(result.events, count=5, include_first=include_first)
    series = []
    for event in chosen:
        ring = f"  t_ring={event.t_ring * 1e9:.0f} ns" if event.t_ring else ""
        series.append(
            _series(
                event.post_t_s * 1e9,
                event.post_v / 1000.0,
                color=_color(event),
                label=f"#{event.event_index}  V_min={event.v_undershoot / 1000.0:.1f} kV{ring}",
            )
        )
    return _single(
        "08_post_collapse",
        "08 post collapse",
        footer,
        {
            "title": "Post-collapse window (undershoot and ring)",
            "x_title": "Time after t_break (ns)",
            "y_title": "Voltage (kV)",
            "series": series,
            "hlines": [{"y": 0.0, "color": "#888888", "style": "dashed"}],
        },
    )


def _single(name: str, label: str, footer: str, panel: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "footer": footer,
        "cols": 1,
        "width": 900,
        "height": 560,
        "panels": [panel],
    }


def _hist(values: list[float | None], scale: float, unit: str, title: str) -> dict[str, Any]:
    finite = np.asarray([value for value in values if value is not None], dtype=float)
    finite = finite[np.isfinite(finite)] * scale
    panel: dict[str, Any] = {
        "title": title,
        "x_title": f"{title} ({unit})",
        "y_title": "Count",
        "legend_corner": "left",
    }
    if len(finite) == 0:
        panel["hist"] = {"values": []}
        return panel
    mean = float(np.mean(finite))
    std = float(np.std(finite, ddof=1)) if len(finite) > 1 else 0.0
    median = float(np.median(finite))
    cv = (std / mean) if mean != 0 else float("nan")
    cv_txt = f"{100 * cv:.1f}%" if math.isfinite(cv) else "—"
    panel["hist"] = {
        "values": [float(value) for value in finite],
        "nbins": int(min(10, max(5, len(finite) // 2))),
        "mean": mean,
        "median": median,
        "color": _BLUE,
        "label": title,
    }
    panel["notes"] = [
        {
            "align": "right",
            "text": (
                f"n={len(finite)}\n"
                f"mean={mean:.3g} {unit}\n"
                f"std={std:.3g} {unit}\n"
                f"median={median:.3g} {unit}\n"
                f"CV={cv_txt}"
            ),
        }
    ]
    return panel


def _series(xs, ys, **style: Any) -> dict[str, Any]:
    x, y = _pairs(xs, ys)
    item = {
        "x": x,
        "y": y,
        "label": "",
        "color": _BLUE,
        "line": "solid",
        "marker": "none",
        "width": 2,
    }
    item.update(style)
    return item


def _pairs(xs, ys) -> tuple[list[float], list[float]]:
    x = np.asarray(xs, dtype=float).reshape(-1)
    y = np.asarray(ys, dtype=float).reshape(-1)
    count = min(len(x), len(y))
    if count == 0:
        return [], []
    x = x[:count]
    y = y[:count]
    mask = np.isfinite(x) & np.isfinite(y)
    return x[mask].astype(float).tolist(), y[mask].astype(float).tolist()


def _opt(value: float | None, scale: float) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number * scale


def _color(event: SparkGapEvent) -> str:
    return _TAB10[int(event.event_index) % 10]


def _slew_text(event: SparkGapEvent) -> str:
    dv_kv = (event.v10 - event.v90) / 1000.0
    return (
        f"{dv_kv:.2f} kV in {event.t_collapse_10_90 * 1e9:.1f} ns "
        f"({event.slew_collapse_mean / 1e12:.2f} kV/ns)"
    )
