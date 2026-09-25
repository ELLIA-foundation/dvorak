"""Frequency-response figure spec shared by the GUI and the sweep script.

No instrument or ROOT imports: the GUI sends the spec to the renderer, and the
sweep script can do the same when ROOT is installed.
"""

from __future__ import annotations

import math
from typing import Any


def frequency_spec(rows: list[dict], scope_bw_hz: float | None) -> dict[str, Any]:
    ratio_x, ratio_y = _column(rows, "ratio_db")
    nom_x, nom_y = _column(rows, "v_nominal_vpp")
    scope_x, scope_y = _column(rows, "v_scope_vpp")
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
    return {
        "name": "frequency_response",
        "label": "Frequency response",
        "width": 900,
        "height": 720,
        "cols": 1,
        "panels": [
            {
                "title": "Frequency response  V_scope / V_nominal",
                "y_title": "Ratio (dB)",
                "logx": True,
                "series": [
                    {
                        "x": ratio_x,
                        "y": ratio_y,
                        "label": "20 log10(V_scope / V_nominal)",
                        "color": "#1f77b4",
                        "line": "solid",
                        "marker": "circle",
                        "marker_size": 0.8,
                        "width": 2,
                    }
                ],
                "vlines": vlines,
            },
            {
                "x_title": "Frequency (Hz)",
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
                ],
                "vlines": [dict(item, label="") for item in vlines],
            },
        ],
    }


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
