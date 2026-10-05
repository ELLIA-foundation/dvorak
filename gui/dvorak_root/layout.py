"""Canvas sizes, file names, and screen copies of figures. No ROOT import."""

from __future__ import annotations

import math
import re

# Below this a subplot cannot hold its axis titles, labels, and legend.
MIN_PAD_HEIGHT = 210
MIN_PAD_WIDTH = 240
MIN_CANVAS = (360, 260)


def grid(spec: dict) -> tuple[int, int]:
    """(rows, cols) of the spec's panels."""
    count = max(1, len(spec.get("panels") or []))
    cols = max(1, min(int(spec.get("cols") or 1), count))
    return math.ceil(count / cols), cols


def fitted_size(spec: dict, width: int, height: int) -> tuple[int, int]:
    """Fill the pane, but keep every row at least ``MIN_PAD_HEIGHT`` tall.

    A tall stack of subplots then scrolls instead of shrinking each one until
    its text no longer fits.
    """
    rows, cols = grid(spec)
    chrome = (31 if spec.get("title") else 0) + (23 if spec.get("footer") else 0) + 10
    need_h = rows * MIN_PAD_HEIGHT + chrome
    need_w = cols * MIN_PAD_WIDTH if cols > 1 else MIN_CANVAS[0]
    return (
        max(int(width), MIN_CANVAS[0], need_w),
        max(int(height), MIN_CANVAS[1], need_h),
    )


def screen_fit(size: tuple[int, int], available: tuple[int, int]) -> tuple[int, int]:
    """Scale a canvas down, keeping its shape, until it fits on the screen."""
    width, height = size
    max_w, max_h = available
    if width <= 0 or height <= 0 or max_w <= 0 or max_h <= 0:
        return size
    scale = min(1.0, max_w / width, max_h / height)
    return max(1, int(width * scale)), max(1, int(height * scale))


def safe_stem(name: str) -> str:
    """File stem that is also a legal ROOT macro function name."""
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", str(name)).strip("_")
    if not stem or stem[0].isdigit():
        stem = "fig_" + stem
    return stem or "figure"


# A canvas with more points than this is slow to render and to draw in the
# browser, and shows nothing more at screen resolution.
SCREEN_POINT_BUDGET = 60_000
SCREEN_SERIES_FLOOR = 200


def screen_spec(spec: dict, budget: int = SCREEN_POINT_BUDGET) -> dict:
    """A copy of ``spec`` whose line series are min-max decimated to ``budget`` points.

    Marker-only series and bands are left alone. The spec itself is not
    modified, so a full-resolution copy stays available for export.
    """
    import numpy as np

    lines = [
        series
        for panel in spec.get("panels") or []
        for series in panel.get("series") or []
        if _is_line(series)
    ]
    total = sum(min(len(series.get("x") or []), len(series.get("y") or [])) for series in lines)
    if not lines or total <= budget:
        return spec
    cap = max(SCREEN_SERIES_FLOOR, budget // len(lines))
    panels = []
    for panel in spec.get("panels") or []:
        new_series = []
        for series in panel.get("series") or []:
            if _is_line(series) and min(len(series.get("x") or []), len(series.get("y") or [])) > cap:
                x, y = _minmax(np.asarray(series["x"], dtype=float), np.asarray(series["y"], dtype=float), cap)
                series = dict(series, x=x.tolist(), y=y.tolist())
            new_series.append(series)
        panels.append(dict(panel, series=new_series) if "series" in panel else panel)
    return dict(spec, panels=panels)


def _is_line(series: dict) -> bool:
    return (
        isinstance(series, dict)
        and "y_low" not in series
        and series.get("line", "solid") != "none"
    )


def _minmax(x, y, max_points: int):
    """Keep each bucket's min and max, in time order, so spikes survive."""
    import numpy as np

    n = min(len(x), len(y))
    buckets = max(1, max_points // 2)
    size = int(math.ceil(n / buckets))
    keep: list[int] = []
    for start in range(0, n, size):
        stop = min(n, start + size)
        segment = y[start:stop]
        lo = start + int(np.argmin(segment))
        hi = start + int(np.argmax(segment))
        keep.extend(sorted({lo, hi}))
    index = np.asarray(keep, dtype=int)
    return x[index], y[index]
