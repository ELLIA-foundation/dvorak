"""Canvas sizes and file names for figures. No ROOT import; the GUI uses it."""

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
