"""Frequency-response figure spec shared by the GUI and the sweep script.

No instrument imports. The GUI draws the spec with matplotlib.
"""

from __future__ import annotations

import math
from typing import Any


def frequency_spec(rows: list[dict], scope_bw_hz: float | None) -> dict[str, Any]:
    nom_x, nom_y = _column(rows, "v_nominal_vpp")
    scope_x, scope_y = _column(rows, "v_scope_vpp")
    in_x, in_y = _column(rows, "v_in_vpp")
    gain_x, gain_y = _column(rows, "gain_db")
    phase_x, phase_y = _column(rows, "phase_deg")
    thd_x, thd_fraction = _column(rows, "thd")
    thd_y = [100.0 * value for value in thd_fraction]
    limited = any(_as_bool(row.get("scope_limited")) for row in rows)
    bw = _as_float(scope_bw_hz)
    vlines: list[dict[str, Any]] = []
    if limited and bw is not None and bw > 0:
        vlines.append(
            {
                "x": bw,
                "color": "#d62728",
                "style": "dashed",
                "label": f"scope BW {bw / 1e6:.0f} MHz",
            }
        )
    panels: list[dict[str, Any]] = [
        {
            "title": "Frequency response  V_scope / V_nominal",
            "y_title": "Ratio (dB)",
            "logx": True,
            "series": _ratio_series(rows) + _gain_series(gain_x, gain_y),
            "vlines": vlines,
        },
        {
            "y_title": "Amplitude (Vpp)",
            "logx": True,
            "series": [
                {
                    "x": nom_x,
                    "y": nom_y,
                    "label": "V_nominal (generator)",
                    "color": "#2ca02c",
                    "line": "solid",
                    "marker": "circle",
                    "marker_size": 0.8,
                    "width": 2,
                },
                {
                    "x": scope_x,
                    "y": scope_y,
                    "label": "V_scope",
                    "color": "#ff7f0e",
                    "line": "solid",
                    "marker": "square",
                    "marker_size": 0.8,
                    "width": 2,
                },
            ]
            + (
                [
                    {
                        "x": in_x,
                        "y": in_y,
                        "label": "V_in",
                        "color": "#17becf",
                        "line": "solid",
                        "marker": "triangle",
                        "marker_size": 0.8,
                        "width": 2,
                    }
                ]
                if in_x
                else []
            ),
            "vlines": [dict(item, label="") for item in vlines],
        },
    ]
    if phase_x:
        panels.append(
            {
                "y_title": "Phase (deg)",
                "logx": True,
                "series": [
                    {
                        "x": phase_x,
                        "y": phase_y,
                        "label": "output relative to input",
                        "color": "#8c564b",
                        "line": "solid",
                        "marker": "circle",
                        "marker_size": 0.8,
                        "width": 2,
                    }
                ],
                "vlines": [dict(item, label="") for item in vlines],
            }
        )
    if thd_x:
        panels.append(
            {
                "y_title": "THD (%)",
                "logx": True,
                "series": [
                    {
                        "x": thd_x,
                        "y": thd_y,
                        "label": "THD",
                        "color": "#9467bd",
                        "line": "solid",
                        "marker": "circle",
                        "marker_size": 0.8,
                        "width": 2,
                    }
                ],
                "vlines": [dict(item, label="") for item in vlines],
            }
        )
    panels[-1]["x_title"] = "Frequency (Hz)"
    extra = int(bool(thd_x)) + int(bool(phase_x))
    height = 720 + 260 * extra
    return {
        "name": "frequency_response",
        "label": "Frequency response",
        "width": 900,
        "height": height,
        "cols": 1,
        "panels": panels,
    }


def _ratio_series(rows: list[dict]) -> list[dict[str, Any]]:
    groups = {
        "ok": {"x": [], "y": []},
        "below_floor": {"x": [], "y": []},
        "clipped": {"x": [], "y": []},
    }
    for row in rows:
        freq = _as_float(row.get("frequency_hz"))
        value = _as_float(row.get("ratio_db"))
        if freq is None or value is None or freq <= 0:
            continue
        status = _vertical_status(row)
        bucket = groups.get(status, groups["ok"])
        bucket["x"].append(freq)
        bucket["y"].append(value)
    series: list[dict[str, Any]] = []
    if groups["ok"]["x"]:
        series.append(
            {
                "x": groups["ok"]["x"],
                "y": groups["ok"]["y"],
                "label": "20 log10(V_scope / V_nominal)",
                "color": "#1f77b4",
                "line": "solid",
                "marker": "circle",
                "marker_size": 0.8,
                "width": 2,
            }
        )
    if groups["below_floor"]["x"]:
        series.append(
            {
                "x": groups["below_floor"]["x"],
                "y": groups["below_floor"]["y"],
                "label": "below floor",
                "color": "#7f7f7f",
                "line": "none",
                "marker": "plus",
                "marker_size": 1.0,
                "width": 2,
            }
        )
    if groups["clipped"]["x"]:
        series.append(
            {
                "x": groups["clipped"]["x"],
                "y": groups["clipped"]["y"],
                "label": "clipped",
                "color": "#d62728",
                "line": "none",
                "marker": "triangle",
                "marker_size": 1.0,
                "width": 2,
            }
        )
    return series


def _gain_series(gain_x: list[float], gain_y: list[float]) -> list[dict[str, Any]]:
    if not gain_x:
        return []
    return [
        {
            "x": gain_x,
            "y": gain_y,
            "label": "20 log10(V_out / V_in)",
            "color": "#e377c2",
            "line": "solid",
            "marker": "square",
            "marker_size": 0.8,
            "width": 2,
        }
    ]


def _vertical_status(row: dict) -> str:
    if "vertical_status" not in row or row.get("vertical_status") in (None, ""):
        return "ok"
    return str(row["vertical_status"]).strip().lower()


def _column(rows: list[dict], y_key: str) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for row in rows:
        freq = _as_float(row.get("frequency_hz"))
        value = _as_float(row.get(y_key))
        if freq is None or value is None or freq <= 0:
            continue
        xs.append(freq)
        ys.append(value)
    return xs, ys


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}
