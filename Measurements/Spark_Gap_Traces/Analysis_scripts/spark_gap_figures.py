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

PLOT_METRICS: tuple[dict[str, Any], ...] = (
    {"name": "t_break", "scale": 1e3, "unit": "ms", "label": "t_break"},
    {"name": "v_breakdown", "scale": 1e-3, "unit": "kV", "label": "v_breakdown"},
    {"name": "v_undershoot", "scale": 1e-3, "unit": "kV", "label": "v_undershoot"},
    {"name": "v_residual", "scale": 1e-3, "unit": "kV", "label": "v_residual"},
    {"name": "dv_collapse", "scale": 1e-3, "unit": "kV", "label": "dv_collapse"},
    {"name": "period_s", "scale": 1e6, "unit": "µs", "label": "period_s"},
    {"name": "rep_rate_hz", "scale": 1.0, "unit": "Hz", "label": "rep_rate_hz"},
    {"name": "t_collapse_10_90", "scale": 1e9, "unit": "ns", "label": "t90−t10"},
    {"name": "slew_collapse_mean", "scale": 1e-12, "unit": "kV/ns", "label": "slew_collapse_mean"},
    {"name": "slew_collapse_peak", "scale": 1e-12, "unit": "kV/ns", "label": "slew_collapse_peak"},
    {"name": "t_ring", "scale": 1e9, "unit": "ns", "label": "t_ring"},
    {"name": "charge_rate", "scale": 1e-6, "unit": "kV/ms", "label": "charge_rate"},
    {"name": "charge_r2", "scale": 1.0, "unit": "1", "label": "charge_r2"},
    {"name": "v_charge_start", "scale": 1e-3, "unit": "kV", "label": "v_charge_start"},
    {"name": "v_charge_end", "scale": 1e-3, "unit": "kV", "label": "v_charge_end"},
    {"name": "recovery_s", "scale": 1e6, "unit": "µs", "label": "recovery_s"},
    {"name": "energy_j", "scale": 1.0, "unit": "J", "label": "energy_j"},
    {"name": "charge_c", "scale": 1.0, "unit": "C", "label": "charge_c"},
    {"name": "source_current_a", "scale": 1.0, "unit": "A", "label": "source_current_a"},
    {"name": "L_est_h", "scale": 1.0, "unit": "H", "label": "L_est_h"},
)
CAPACITANCE_METRICS = ("energy_j", "charge_c", "source_current_a", "L_est_h")
DEFAULT_METRICS = (
    "v_breakdown",
    "period_s",
    "charge_rate",
    "t_collapse_10_90",
    "slew_collapse_mean",
)
OVERLAY_SCREEN_POINTS = 8000


def metric_catalog(events: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Plottable scalars. Capacitance-only fields drop out when every value is empty."""
    rows = list(events or [])
    catalog = []
    for field in PLOT_METRICS:
        name = str(field["name"])
        if name in CAPACITANCE_METRICS and rows and not any(_finite(row.get(name)) for row in rows):
            continue
        catalog.append(dict(field))
    return catalog


def typical_event_indexes(events: list[dict[str, Any]], include_first: bool = False) -> list[int]:
    return [int(row["event_index"]) for row in _filter_events(events, include_first)]


def representative_indexes(
    events: list[dict[str, Any]],
    count: int = 5,
    include_first: bool = False,
) -> list[int]:
    pool = sorted(
        _filter_events(events, include_first),
        key=lambda row: float(row.get("v_breakdown") or 0.0),
    )
    if len(pool) <= count:
        chosen = pool
    else:
        indexes = np.linspace(0, len(pool) - 1, count)
        chosen = [pool[int(round(index))] for index in indexes]
    seen: set[int] = set()
    unique: list[int] = []
    for row in chosen:
        ident = int(row["event_index"])
        if ident not in seen:
            unique.append(ident)
            seen.add(ident)
    return unique


def metric_figure_spec(
    events: list[dict[str, Any]],
    detection: dict[str, Any],
    *,
    names: list[str],
    mode: str,
    include_first: bool,
) -> dict[str, Any]:
    pool = _filter_events(events, include_first)
    fields = {field["name"]: field for field in PLOT_METRICS}
    panels = []
    for name in names:
        field = fields.get(name)
        if field is None:
            continue
        if mode == "histogram":
            panels.append(
                _hist(
                    [row.get(name) for row in pool],
                    float(field["scale"]),
                    str(field["unit"]),
                    str(field["label"]),
                )
            )
        else:
            panels.append(_sequence_panel(pool, field, mark_first=include_first))
    if not panels:
        panels.append({"title": "Select at least one metric", "series": []})
    population = "all events" if include_first else "typical events"
    kind = "Histograms" if mode == "histogram" else "Sequential"
    count = max(1, len(panels))
    return {
        "name": f"metrics_{mode}",
        "label": f"{kind} ({population})",
        "title": f"{kind} — {population}",
        "footer": scope_limit_footer(detection),
        "cols": 1,
        "width": 880,
        "height": max(420, 230 * count + 40),
        "panels": panels,
    }


def overlay_figure_spec(
    events: list[dict[str, Any]],
    snippets: list[dict[str, Any]],
    detection: dict[str, Any],
    *,
    kind: str,
    event_indexes: list[int],
    show_guides: bool = True,
    show_fit: bool = True,
    max_points: int | None = OVERLAY_SCREEN_POINTS,
) -> dict[str, Any]:
    by_index = {int(row["event_index"]): row for row in events}
    snips = {int(row["event_index"]): row for row in snippets}
    chosen = [ident for ident in event_indexes if ident in snips]
    if kind == "ramp":
        panel = _overlay_ramps(chosen, by_index, snips, show_fit=show_fit, max_points=max_points)
        name, label = "overlay_ramp", "Charging ramps"
    elif kind == "post":
        panel = _overlay_post(chosen, by_index, snips, max_points=max_points)
        name, label = "overlay_post", "Post-collapse"
    else:
        panel = _overlay_discharge(
            chosen, by_index, snips, detection, show_guides=show_guides, max_points=max_points
        )
        name, label = "overlay_discharge", "Discharge overlay"
    return _single(name, label, scope_limit_footer(detection), panel)


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


def _filter_events(events: list[dict[str, Any]], include_first: bool) -> list[dict[str, Any]]:
    rows = list(events)
    if include_first:
        return rows
    pool = [row for row in rows if not row.get("first_cycle")]
    return pool or rows


def _sequence_panel(pool: list[dict[str, Any]], field: dict[str, Any], *, mark_first: bool) -> dict[str, Any]:
    name = str(field["name"])
    scale = float(field["scale"])
    unit = str(field["unit"])
    label = str(field["label"])
    indexes = [int(row["event_index"]) for row in pool]
    values = [_opt(row.get(name), scale) for row in pool]
    series = [
        _series(indexes, values, label=label, color=_BLUE, line="solid", marker="circle", marker_size=0.9),
    ]
    if mark_first:
        first = [row for row in pool if row.get("first_cycle")]
        if first:
            series.append(
                _series(
                    [int(row["event_index"]) for row in first],
                    [_opt(row.get(name), scale) for row in first],
                    label="first_cycle",
                    color=_RED,
                    line="none",
                    marker="circle",
                    marker_size=1.2,
                )
            )
    return {
        "x_title": "event_index",
        "y_title": f"{label} ({unit})",
        "series": series,
        "legend_corner": "right",
    }


def _overlay_discharge(
    chosen: list[int],
    events: dict[int, dict[str, Any]],
    snips: dict[int, dict[str, Any]],
    detection: dict[str, Any],
    *,
    show_guides: bool,
    max_points: int | None,
) -> dict[str, Any]:
    series = []
    points = []
    hlines = []
    vlines = [{"x": 0.0, "color": "#666666", "style": "dashed"}]
    for ident in chosen:
        snippet = snips[ident]
        event = events.get(ident, {})
        color = _color_index(ident)
        t_ns, v_kv = _scaled_xy(
            snippet.get("collapse_t_s"),
            snippet.get("collapse_v"),
            1e9,
            1e-3,
            max_points,
        )
        v_bd = event.get("v_breakdown")
        label = f"#{ident}"
        if _finite(v_bd):
            label = f"#{ident}  V_bd={float(v_bd) / 1000.0:.1f} kV"
        series.append(_series(t_ns, v_kv, label=label, color=color, width=2))
        if show_guides and _finite(snippet.get("v10")):
            points.append({"x": 0.0, "y": float(snippet["v10"]) / 1000.0, "color": color, "size": 1.0})
            hlines.append({"y": float(snippet["v10"]) / 1000.0, "color": color, "style": "dotted"})
        if show_guides and _finite(snippet.get("v90")):
            hlines.append({"y": float(snippet["v90"]) / 1000.0, "color": color, "style": "dotted"})
        t10 = snippet.get("t10")
        t90 = snippet.get("t90")
        if show_guides and _finite(t10) and _finite(t90):
            vlines.append({"x": (float(t90) - float(t10)) * 1e9, "color": color, "style": "dashed"})
    panel: dict[str, Any] = {
        "title": "High-resolution collapse overlay (aligned at t10)",
        "x_title": "Time after 10% crossing (ns)",
        "y_title": "Voltage (kV)",
        "series": series,
        "points": points,
        "vlines": vlines,
        "legend_columns": 2,
    }
    if show_guides:
        panel["hlines"] = hlines
    limit = detection.get("scope_t1090_limit_s")
    if show_guides and limit:
        limit_ns = float(limit) * 1e9
        panel["vspans"] = [
            {
                "x0": 0.0,
                "x1": limit_ns,
                "color": "#cccccc",
                "label": f"scope 10–90 limit ({limit_ns:.1f} ns)",
            }
        ]
    return panel


def _overlay_ramps(
    chosen: list[int],
    events: dict[int, dict[str, Any]],
    snips: dict[int, dict[str, Any]],
    *,
    show_fit: bool,
    max_points: int | None,
) -> dict[str, Any]:
    series = []
    for ident in chosen:
        snippet = snips[ident]
        color = _color_index(ident)
        t_us, v_kv = _scaled_xy(
            snippet.get("ramp_t_s"),
            snippet.get("ramp_v"),
            1e6,
            1e-3,
            max_points,
        )
        if not t_us:
            continue
        rate = snippet.get("charge_rate")
        label = f"#{ident}"
        if _finite(rate):
            label = f"#{ident}  {float(rate) / 1e6:.1f} kV/ms"
        series.append(_series(t_us, v_kv, color=color, width=2, label=label))
        intercept = snippet.get("charge_intercept")
        t0 = snippet.get("ramp_t0_s")
        if show_fit and _finite(rate) and _finite(intercept) and _finite(t0):
            t_rel = np.asarray(snippet.get("ramp_t_s") or [], dtype=float)
            t_abs = float(t0) + t_rel
            fit_v = float(intercept) + float(rate) * t_abs
            fit_t, fit_y = _scaled_xy(t_rel, fit_v, 1e6, 1e-3, max_points)
            r2 = snippet.get("charge_r2")
            r2_txt = f"{float(r2):.3f}" if _finite(r2) else "—"
            series.append(
                _series(
                    fit_t,
                    fit_y,
                    color=color,
                    line="dashed",
                    width=2,
                    label=f"#{ident}  R²={r2_txt}",
                )
            )
    return {
        "title": "Charging ramps (linear fit V = a + b t)",
        "x_title": "Time after ramp-fit start (µs)",
        "y_title": "Voltage (kV)",
        "series": series,
        "legend_columns": 2,
    }


def _overlay_post(
    chosen: list[int],
    events: dict[int, dict[str, Any]],
    snips: dict[int, dict[str, Any]],
    *,
    max_points: int | None,
) -> dict[str, Any]:
    series = []
    for ident in chosen:
        snippet = snips[ident]
        event = events.get(ident, {})
        color = _color_index(ident)
        t_ns, v_kv = _scaled_xy(
            snippet.get("post_t_s"),
            snippet.get("post_v"),
            1e9,
            1e-3,
            max_points,
        )
        ring = event.get("t_ring")
        v_min = event.get("v_undershoot")
        label = f"#{ident}"
        if _finite(v_min):
            label = f"#{ident}  V_min={float(v_min) / 1000.0:.1f} kV"
        if _finite(ring):
            label += f"  t_ring={float(ring) * 1e9:.0f} ns"
        series.append(_series(t_ns, v_kv, color=color, label=label))
    return {
        "title": "Post-collapse window (undershoot and ring)",
        "x_title": "Time after t_break (ns)",
        "y_title": "Voltage (kV)",
        "series": series,
        "hlines": [{"y": 0.0, "color": "#888888", "style": "dashed"}],
        "legend_columns": 2,
    }


def _scaled_xy(xs, ys, x_scale: float, y_scale: float, max_points: int | None):
    x = np.asarray(xs if xs is not None else [], dtype=float).reshape(-1)
    y = np.asarray(ys if ys is not None else [], dtype=float).reshape(-1)
    count = min(len(x), len(y))
    x = x[:count] * x_scale
    y = y[:count] * y_scale
    x, y = _decimate_xy(x, y, max_points)
    return _pairs(x, y)


def _decimate_xy(xs: np.ndarray, ys: np.ndarray, max_points: int | None):
    n = min(len(xs), len(ys))
    if max_points is None or n <= max_points or max_points < 2:
        return xs[:n], ys[:n]
    bucket_count = max(1, max_points // 2)
    bucket_size = int(math.ceil(n / bucket_count))
    n_buckets = int(math.ceil(n / bucket_size))
    out_t = []
    out_v = []
    for bucket in range(n_buckets):
        lo = bucket * bucket_size
        hi = min(n, lo + bucket_size)
        if lo >= hi:
            continue
        sl_t = xs[lo:hi]
        sl_v = ys[lo:hi]
        i_min = int(np.argmin(sl_v))
        i_max = int(np.argmax(sl_v))
        first, second = (i_min, i_max) if i_min <= i_max else (i_max, i_min)
        out_t.append(float(sl_t[first]))
        out_v.append(float(sl_v[first]))
        if second != first:
            out_t.append(float(sl_t[second]))
            out_v.append(float(sl_v[second]))
    return np.asarray(out_t, dtype=float), np.asarray(out_v, dtype=float)


def _finite(value: Any) -> bool:
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


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


def _color_index(index: int) -> str:
    return _TAB10[int(index) % 10]


def _color(event: SparkGapEvent) -> str:
    return _color_index(int(event.event_index))


def _slew_text(event: SparkGapEvent) -> str:
    dv_kv = (event.v10 - event.v90) / 1000.0
    return (
        f"{dv_kv:.2f} kV in {event.t_collapse_10_90 * 1e9:.1f} ns "
        f"({event.slew_collapse_mean / 1e12:.2f} kV/ns)"
    )
