"""Draw a JSON figure spec with matplotlib. No ROOT and no pyplot."""

from __future__ import annotations

import math
from typing import Any

from matplotlib.axes import Axes
from matplotlib.figure import Figure

_LINE = {
    "solid": "-",
    "dashed": "--",
    "dotted": ":",
    "dashdot": "-.",
    "none": "none",
}
_MARKER = {
    "none": "none",
    "circle": "o",
    "square": "s",
    "triangle": "^",
    "plus": "+",
}


def figure_from_spec(spec: dict[str, Any], fig: Figure | None = None) -> Figure:
    """Paint ``spec`` onto ``fig`` (created when omitted) and return it."""
    panels = list(spec.get("panels") or [])
    if not panels:
        raise ValueError("figure spec has no panels")

    width = int(spec.get("width") or 800)
    height = int(spec.get("height") or 600)
    figsize = (max(width, 200) / 100.0, max(height, 200) / 100.0)
    if fig is None:
        fig = Figure(figsize=figsize)
    else:
        fig.clear()
        fig.set_size_inches(figsize, forward=True)

    cols = max(1, int(spec.get("cols") or 1))
    rows = max(1, math.ceil(len(panels) / cols))
    title = str(spec.get("title") or "")
    footer = str(spec.get("footer") or "")
    for index, panel in enumerate(panels):
        ax = fig.add_subplot(rows, cols, index + 1)
        _draw_panel(ax, panel)
    if title:
        fig.suptitle(title, fontsize=11)
    if footer:
        fig.text(0.5, 0.01, footer, ha="center", va="bottom", fontsize=8, color="#595959")
    fig.tight_layout()
    if title or footer:
        fig.subplots_adjust(
            top=0.90 if title else None,
            bottom=0.08 if footer else None,
        )
    return fig


def _draw_panel(ax: Axes, panel: dict[str, Any]) -> None:
    if panel.get("logx"):
        ax.set_xscale("log")
    elif panel.get("x_plain"):
        ax.ticklabel_format(axis="x", useOffset=False, style="plain")
    if panel.get("logy"):
        ax.set_yscale("log")

    hist = panel.get("hist")
    series = list(panel.get("series") or [])
    drew = False
    if hist:
        drew = _draw_hist(ax, hist)
    else:
        drew = _draw_series(ax, series)

    if not drew:
        ax.text(0.5, 0.55, "No data", transform=ax.transAxes, ha="center", color="#888888")
    else:
        for span in panel.get("vspans") or []:
            ax.axvspan(
                float(span["x0"]),
                float(span["x1"]),
                color=str(span.get("color") or "#cccccc"),
                alpha=0.35,
                label=str(span.get("label") or "") or None,
                linewidth=0,
            )
        for item in panel.get("hlines") or []:
            ax.axhline(
                float(item["y"]),
                color=str(item.get("color") or "#888888"),
                linestyle=_LINE.get(str(item.get("style") or "solid"), "--"),
                linewidth=float(item.get("width") or 1),
            )
        ymin, ymax = ax.get_ylim()
        for item in panel.get("vlines") or []:
            color = str(item.get("color") or "#888888")
            ax.axvline(
                float(item["x"]),
                color=color,
                linestyle=_LINE.get(str(item.get("style") or "solid"), "--"),
                linewidth=float(item.get("width") or 1),
            )
            label = str(item.get("label") or "")
            if label:
                ax.text(
                    float(item["x"]),
                    ymax,
                    label,
                    color=color,
                    fontsize=8,
                    ha="right",
                    va="top",
                )
        for item in panel.get("points") or []:
            color = str(item.get("color") or "#1f77b4")
            ax.plot(
                float(item["x"]),
                float(item["y"]),
                marker="o",
                color=color,
                markersize=float(item.get("size") or 1.1) * 5,
                linestyle="none",
            )
            label = str(item.get("label") or "")
            if label:
                ax.annotate(label, (float(item["x"]), float(item["y"])), fontsize=8, color=color)
        _apply_limits(ax, panel)
        if panel.get("legend", True) and ax.get_legend_handles_labels()[0]:
            corner = str(panel.get("legend_corner") or "right")
            loc = "upper left" if corner == "left" else "upper right"
            ax.legend(
                loc=loc,
                fontsize=8,
                frameon=False,
                ncol=max(1, int(panel.get("legend_columns") or 1)),
            )
        _draw_notes(ax, panel.get("notes") or [])

    title = str(panel.get("title") or "")
    if title:
        ax.set_title(title, fontsize=10)
    ax.set_xlabel(str(panel.get("x_title") or ""))
    ax.set_ylabel(str(panel.get("y_title") or ""))
    ax.grid(True, which="both", alpha=0.25)


def _draw_hist(ax: Axes, spec: dict[str, Any]) -> bool:
    values = [float(value) for value in spec.get("values") or [] if _finite(value)]
    if not values:
        return False
    nbins = max(1, int(spec.get("nbins") or 8))
    color = str(spec.get("color") or "#1f77b4")
    ax.hist(
        values,
        bins=nbins,
        color=color,
        edgecolor=color,
        label=str(spec.get("label") or "") or None,
    )
    if _finite(spec.get("mean")):
        ax.axvline(float(spec["mean"]), color="#d62728", linestyle="--", linewidth=1.5, label="mean")
    if _finite(spec.get("median")):
        ax.axvline(float(spec["median"]), color="#2ca02c", linestyle=":", linewidth=1.5, label="median")
    return True


def _draw_series(ax: Axes, series: list[dict[str, Any]]) -> bool:
    drew = False
    for item in series:
        xs = item.get("x") or []
        ys = item.get("y") or []
        count = min(len(xs), len(ys))
        if count <= 0:
            continue
        color = str(item.get("color") or "#1f77b4")
        marker = _MARKER.get(str(item.get("marker") or "none"), "none")
        size = float(item.get("marker_size") or 1.0) * 6.0
        ax.plot(
            [float(xs[i]) for i in range(count)],
            [float(ys[i]) for i in range(count)],
            color=color,
            linestyle=_LINE.get(str(item.get("line") or "solid"), "-"),
            linewidth=float(item.get("width") or 2),
            marker=marker,
            markersize=0.0 if marker == "none" else size,
            label=str(item.get("label") or "") or None,
        )
        drew = True
    return drew


def _apply_limits(ax: Axes, panel: dict[str, Any]) -> None:
    if _finite(panel.get("xmin")) and _finite(panel.get("xmax")):
        ax.set_xlim(float(panel["xmin"]), float(panel["xmax"]))
    if _finite(panel.get("ymin")) and _finite(panel.get("ymax")):
        ax.set_ylim(float(panel["ymin"]), float(panel["ymax"]))


def _draw_notes(ax: Axes, notes: list[Any]) -> None:
    y = 0.84
    for note in notes:
        if isinstance(note, str):
            text, align = note, "left"
        else:
            text, align = str(note.get("text") or ""), str(note.get("align") or "left")
        lines = [line for line in text.split("\n") if line]
        if not lines:
            continue
        ha = "right" if align == "right" else "left"
        x = 0.97 if align == "right" else 0.03
        ax.text(
            x,
            y,
            "\n".join(lines),
            transform=ax.transAxes,
            ha=ha,
            va="top",
            fontsize=8,
        )
        y -= 0.06 * len(lines)


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
