"""Build one styled TCanvas from a JSON figure spec.

The spec is drawing-ready: panels of graphs, scatters, or histograms. Analysis
code in the GUI process fills the arrays; this module only talks to ROOT.

Layout is worked out in pixels of the size the canvas is shown at and then
turned into the NDC fractions that ROOT and JSROOT share. A small subplot therefore keeps
readable text, and the legend and notes sit inside the frame without covering
the data. Every pad draws an explicit frame histogram first, so the axis
range covers all series and a zoom made in JSROOT can be carried to Legacy
ROOT the same way for histograms and graphs.
"""

from __future__ import annotations

import array
import math
import re
from dataclasses import dataclass, field

import ROOT

from .style import (
    MC_BASE,
    MC_PAD,
    MC_RIGHT,
    MC_TOP,
    apply_root_style,
    color_alpha,
    color_of,
    hold,
    hset,
    line_style,
    marker_style,
    next_id,
    style_pad,
)
from .text import root_text

FRAME_NAME = "hframe"

# Canvas-level title and footer, in pixels of the spec canvas.
_TITLE_PX = 18.0
_FOOTER_PX = 12.5

# Translucent fills keep overlapping histograms readable.
_HIST_ALPHA_SINGLE = 0.45
_HIST_ALPHA_OVERLAY = 0.30
_LEGEND_FILL_ALPHA = 0.85

_MEAN_COLOR = "#d62728"
_MEDIAN_COLOR = "#2ca02c"
_KEY_COLOR = "#444444"


@dataclass
class _Sizes:
    """Text sizes as fractions of the pad's smaller side, like ROOT uses."""

    ref: float
    axis_title: float
    axis_label: float
    legend: float
    note: float
    pad_title: float

    def px(self, size: float) -> float:
        return size * self.ref


@dataclass
class _Content:
    frame_lo: float | None = None
    frame_hi: float | None = None
    data_ymin: float | None = None
    data_ymax: float | None = None
    is_hist: bool = False
    draw: list = field(default_factory=list)
    legend: list = field(default_factory=list)
    series: list = field(default_factory=list)


def render_spec(spec: dict, size: tuple[int, int] | None = None):
    """Return a ``TCanvas`` registered in the current hold-batch.

    ``size`` is the pixel size the canvas will be shown at (the JSROOT pane,
    or the Legacy ROOT window). Text and margins are laid out for it, so the
    figure reads the same in both. Without it the spec's own size is used.
    """
    apply_root_style()
    ROOT.gROOT.SetBatch(True)

    panels = list(spec.get("panels") or [])
    if not panels:
        raise ValueError("figure spec has no panels")

    width = int(spec.get("width") or MC_BASE)
    height = int(spec.get("height") or MC_BASE)
    if size and size[0] > 0 and size[1] > 0:
        width, height = int(size[0]), int(size[1])
    cols = max(1, min(int(spec.get("cols") or 1), len(panels)))
    title = str(spec.get("title") or "")
    footer = str(spec.get("footer") or "")
    window_title = root_text(
        spec.get("label") or title or spec.get("name") or "figure"
    )
    name = next_id("c")
    canvas = hold(ROOT.TCanvas(name, window_title, width, height))
    canvas.SetFillColor(0)
    canvas.SetBorderMode(0)
    top = (_TITLE_PX + 14.0) / height if title else 0.0
    bottom = (_FOOTER_PX + 10.0) / height if footer else 0.0
    pads = _pads(canvas, len(panels), cols, top=top, bottom=bottom, width=width, height=height)
    for (pad, pad_w, pad_h), panel in zip(pads, panels):
        _draw_panel(pad, panel, pad_w, pad_h)
    canvas.cd()
    ref = float(min(width, height))
    if title:
        _ndc_text(
            0.5,
            1.0 - (_TITLE_PX * 0.5 + 7.0) / height,
            title,
            align=22,
            size=_TITLE_PX / ref,
        )
    if footer:
        _ndc_text(
            0.5,
            (_FOOTER_PX * 0.5 + 5.0) / height,
            footer,
            align=22,
            size=_FOOTER_PX / ref,
            color="#595959",
        )
    canvas.Modified()
    canvas.Update()
    return canvas


def _pads(canvas, count: int, cols: int, *, top: float, bottom: float, width: int, height: int):
    if count == 1:
        x1, y1, x2, y2 = MC_PAD
        y1 += bottom
        y2 -= top
        pad_name = next_id("mpad")
        pad = hold(ROOT.TPad(pad_name, pad_name, x1, y1, x2, y2))
        pad.SetFillColor(0)
        pad.SetBorderMode(0)
        pad.Draw()
        return [(pad, (x2 - x1) * width, (y2 - y1) * height)]
    rows = max(1, math.ceil(count / cols))
    gap = 0.008
    pad_w = (1.0 - gap * (cols + 1)) / cols
    usable = 1.0 - top - bottom - gap * (rows + 1)
    pad_h = usable / rows
    pads = []
    for index in range(count):
        row, col = divmod(index, cols)
        x1 = gap + col * (pad_w + gap)
        x2 = x1 + pad_w
        y2 = 1.0 - top - gap - row * (pad_h + gap)
        y1 = y2 - pad_h
        pad_name = next_id("p")
        pad = hold(ROOT.TPad(pad_name, pad_name, x1, y1, x2, y2))
        pad.SetFillColor(0)
        pad.SetBorderMode(0)
        pad.Draw()
        pads.append((pad, pad_w * width, pad_h * height))
    return pads


def _sizes(pad_w: float, pad_h: float) -> _Sizes:
    """``hset`` sizes, kept between a readable minimum and a sane maximum.

    ROOT scales text with the pad, so the ``mc()`` fractions give 24 px axis
    titles on a 400 px canvas but 10 px ones in a small subplot and 40 px
    ones in a full-screen pane.
    """
    ref = max(1.0, min(pad_w, pad_h))

    def size(default: float, min_px: float, max_px: float) -> float:
        return min(max(default * ref, min_px), max_px) / ref

    return _Sizes(
        ref=ref,
        axis_title=size(0.06, 14.0, 24.0),
        axis_label=size(0.05, 12.0, 20.0),
        legend=size(0.035, 12.0, 17.0),
        note=size(0.032, 11.5, 16.0),
        pad_title=size(0.045, 13.5, 17.0),
    )


# Distance from an axis to the centre of its title is 1.6 x offset x title
# size, measured against the pad width for the y axis and the pad height for
# the x axis (font size itself follows the smaller side). Measured in ROOT
# 6.40 (TGaxis) and JSROOT 7.11; they agree to about two pixels.
_TITLE_K = 1.6
_LABEL_OFFSET = 0.01


@dataclass
class _Frame:
    left: float
    right: float
    bottom: float
    top: float
    offset_x: float
    offset_y: float

    @property
    def margins(self) -> tuple[float, float, float, float]:
        return (self.left, self.right, self.bottom, self.top)


def _layout(pad_w: float, pad_h: float, sizes: _Sizes, *, titled: bool, label_chars: int) -> _Frame:
    """Margins and title offsets that keep every axis title clear of its labels."""
    label_px = sizes.px(sizes.axis_label)
    title_px = sizes.px(sizes.axis_title)
    # The hset() look on a square pad: title offset 1.1.
    default_dist = _TITLE_K * 1.1 * 0.06 * sizes.ref
    # x axis: labels hang below the axis, the title centre below them. ROOT
    # places x labels by pad height while sizing them by the smaller side, so
    # in a tall pad they sit lower than in JSROOT; clear whichever is lower.
    label_bottom = max(
        0.8 * (_LABEL_OFFSET + sizes.axis_label) * pad_h,
        _LABEL_OFFSET * pad_h + label_px * 1.1,
    )
    clear_x = label_bottom + 4.0 + 0.5 * title_px
    dist_x = max(clear_x, min(default_dist, clear_x * 1.35))
    offset_x = dist_x / (_TITLE_K * sizes.axis_title * pad_h)
    bottom_px = dist_x + 0.62 * title_px + 4.0
    # y axis: labels sit left of the axis, the rotated title left of them.
    label_w = label_chars * 0.58 * label_px
    clear_y = _LABEL_OFFSET * pad_w + label_w + 7.0 + 0.5 * title_px
    dist_y = max(clear_y, min(default_dist, clear_y * 1.35))
    offset_y = dist_y / (_TITLE_K * sizes.axis_title * pad_w)
    left_px = dist_y + 0.62 * title_px + 4.0
    top_px = sizes.px(sizes.pad_title) * 1.9 + 4.0 if titled else 0.0
    base = min(pad_w, pad_h)
    top = max(top_px / pad_h, MC_TOP * base / pad_h)
    right = max(MC_RIGHT * base / pad_w, 16.0 / pad_w)
    return _Frame(
        left=min(left_px / pad_w, 0.45),
        right=min(right, 0.2),
        bottom=min(bottom_px / pad_h, 0.45),
        top=min(top, 0.3),
        offset_x=offset_x,
        offset_y=offset_y,
    )


def _draw_panel(pad, panel: dict, pad_w: float, pad_h: float) -> None:
    pad.cd()
    logx = bool(panel.get("logx"))
    logy = bool(panel.get("logy"))
    if logx:
        pad.SetLogx(1)
    if logy:
        pad.SetLogy(1)
    sizes = _sizes(pad_w, pad_h)
    titled = bool(panel.get("title"))

    content = _build_content(panel, logx=logx)
    xmin, xmax = _x_range(panel, content, logx=logx)
    ymin, ymax = _y_range(panel, content, xmin, xmax, logy=logy)
    label_chars = _label_chars(ymin, ymax, logy=logy)
    layout = _layout(pad_w, pad_h, sizes, titled=titled, label_chars=label_chars)
    margins = layout.margins
    style_pad(pad, margins)
    _left, _right, _bottom, top = margins

    if not content.draw:
        if titled:
            _pad_title(panel, sizes, top)
        _ndc_text(0.5, 0.5, "No data", align=22, size=sizes.legend * 1.3, color="#888888")
        pad.Modified()
        pad.Update()
        return

    spans = _make_spans(panel.get("vspans") or [])
    content.legend[:0] = [(box, label, "f") for box, label, _x in spans if label]
    show_legend = bool(panel.get("legend", True)) and bool(content.legend)
    legend_box = None
    if show_legend:
        legend_box = _legend_box(
            content.legend,
            corner=str(panel.get("legend_corner") or "right"),
            columns=int(panel.get("legend_columns") or 1),
            margins=margins,
            sizes=sizes,
            pad_w=pad_w,
            pad_h=pad_h,
        )
    notes = _note_layout(
        panel.get("notes") or [],
        margins=margins,
        sizes=sizes,
        pad_w=pad_w,
        pad_h=pad_h,
        legend_box=legend_box,
        legend_corner=str(panel.get("legend_corner") or "right"),
    )

    headroom = panel.get("headroom")
    if headroom is None:
        headroom = content.is_hist or not _has_y_range(panel)
    if headroom:
        reserved = _reserved_top(legend_box, notes, margins)
        ymin, ymax = _with_headroom(ymin, ymax, reserved, margins, logy=logy)

    divx = _x_divisions(xmin, xmax, (1.0 - layout.left - layout.right) * pad_w, sizes, logx=logx)
    frame = _draw_frame(panel, xmin, xmax, ymin, ymax, sizes, layout, logy=logy, divx=divx)
    _draw_spans(spans, ymin, ymax)
    for item in content.draw:
        obj, option = item
        obj.Draw(option)
    _draw_hlines(panel.get("hlines") or [], xmin, xmax)
    _draw_vlines(panel.get("vlines") or [], ymin, ymax, sizes, logy=logy)
    _draw_points(panel.get("points") or [], sizes)
    # Ticks and the frame line go back on top of every fill.
    frame.Draw("AXIS SAME")
    if legend_box is not None:
        _draw_legend(content.legend, legend_box, sizes)
    for text, x, y, align, _bottom in notes:
        _ndc_text(x, y, text, align=align, size=sizes.note)
    if titled:
        _pad_title(panel, sizes, top)
    pad.Modified()
    pad.Update()


def _pad_title(panel: dict, sizes: _Sizes, top: float) -> None:
    y = 1.0 - top * 0.5
    _ndc_text(0.5, y, str(panel["title"]), align=22, size=sizes.pad_title)


# -- content ----------------------------------------------------------------


def _build_content(panel: dict, *, logx: bool) -> _Content:
    content = _Content()
    hists = list(panel.get("hists") or [])
    if not hists and panel.get("hist"):
        hists = [panel["hist"]]
    if hists:
        content.is_hist = True
        _build_hists(hists, content)
        return content
    _build_series(panel.get("series") or [], content, logx=logx)
    return content


def _build_hists(specs: list, content: _Content) -> None:
    usable: list[tuple[dict, list[float]]] = []
    for spec in specs:
        values = [float(value) for value in spec.get("values") or [] if _finite(value)]
        if values:
            usable.append((spec, values))
    if not usable:
        return
    first = usable[0][0]
    if _finite(first.get("xmin")) and _finite(first.get("xmax")):
        lo = float(first["xmin"])
        hi = float(first["xmax"])
    else:
        lo = min(min(values) for _spec, values in usable)
        hi = max(max(values) for _spec, values in usable)
    if hi <= lo:
        pad = 1.0 if lo == 0.0 else abs(lo) * 0.05
        lo -= pad
        hi += pad
    nbins = max(1, int(first.get("nbins") or 8))
    multi = len(usable) > 1
    alpha = _HIST_ALPHA_OVERLAY if multi else _HIST_ALPHA_SINGLE
    peak = 0.0
    built: list[tuple[object, dict]] = []
    for spec, values in usable:
        hist = hold(ROOT.TH1D(next_id("h"), "", nbins, lo, hi))
        hist.SetDirectory(0)
        hist.SetStats(0)
        for value in values:
            hist.Fill(value)
        color = str(spec.get("color") or "#1f77b4")
        hist.SetFillColor(color_alpha(color, float(spec.get("fill_alpha") or alpha)))
        hist.SetFillStyle(1001)
        hist.SetLineColor(color_of(color))
        hist.SetLineWidth(2)
        peak = max(peak, float(hist.GetMaximum()))
        built.append((hist, spec))
        content.draw.append((hist, "HIST SAME"))
        content.legend.append((hist, str(spec.get("label") or "counts"), "f"))
    if peak <= 0.0:
        peak = 1.0
    content.frame_lo, content.frame_hi = lo, hi
    content.data_ymin, content.data_ymax = 0.0, peak
    y_top = peak * 1.04
    if multi:
        # One dashed (mean) and one dotted (median) line per series, in its color.
        for _hist, spec in built:
            color = str(spec.get("color") or "#1f77b4")
            for key, style in (("mean", "dashed"), ("median", "dotted")):
                if _finite(spec.get(key)):
                    x = float(spec[key])
                    content.draw.append((_segment_graph(x, 0.0, x, y_top, color, style, 2), "L"))
        if any(_finite(spec.get("mean")) for _hist, spec in built):
            key = _segment_graph(lo, -1.0, lo, -1.0, _KEY_COLOR, "dashed", 2)
            content.legend.append((key, "mean", "l"))
        if any(_finite(spec.get("median")) for _hist, spec in built):
            key = _segment_graph(lo, -1.0, lo, -1.0, _KEY_COLOR, "dotted", 2)
            content.legend.append((key, "median", "l"))
        return
    spec = built[0][1]
    for key, color, style in (("mean", _MEAN_COLOR, "dashed"), ("median", _MEDIAN_COLOR, "dotted")):
        if _finite(spec.get(key)):
            x = float(spec[key])
            line = _segment_graph(x, 0.0, x, y_top, color, style, 2)
            content.draw.append((line, "L"))
            content.legend.append((line, str(spec.get(f"{key}_label") or key), "l"))


def _build_series(series: list, content: _Content, *, logx: bool) -> None:
    bands: list[tuple[object, dict]] = []
    lines: list[tuple[object, dict]] = []
    xs_all: list[float] = []
    for item in series:
        if _is_band(item):
            band = _make_band(item)
            if band is None:
                continue
            bands.append((band, item))
            xs_all.extend(float(x) for x in item.get("x") or [] if _finite(x))
            continue
        graph = _make_graph(item)
        if graph is None:
            continue
        lines.append((graph, item))
        xs_all.extend(float(x) for x in item.get("x") or [] if _finite(x))
    for band, item in bands:
        content.draw.append((band, "F"))
        label = str(item.get("label") or "")
        if label:
            content.legend.append((band, label, "f"))
    for graph, item in lines:
        content.draw.append((graph, _draw_option(item)))
        label = str(item.get("label") or "")
        if label:
            content.legend.append((graph, label, _legend_opt(item)))
    content.series = [item for _graph, item in lines] + [item for _band, item in bands]
    xs = [x for x in xs_all if not logx or x > 0]
    if xs:
        content.frame_lo, content.frame_hi = min(xs), max(xs)


def _has_range(panel: dict, low: str, high: str) -> bool:
    return (
        _finite(panel.get(low))
        and _finite(panel.get(high))
        and float(panel[high]) > float(panel[low])
    )


def _x_range(panel: dict, content: _Content, *, logx: bool) -> tuple[float, float]:
    if _has_range(panel, "xmin", "xmax"):
        lo, hi = float(panel["xmin"]), float(panel["xmax"])
        if not logx or lo > 0:
            return lo, hi
    lo, hi = content.frame_lo, content.frame_hi
    if lo is None or hi is None:
        return (1.0, 10.0) if logx else (0.0, 1.0)
    if content.is_hist:
        return lo, hi
    if hi <= lo:
        pad = 1.0 if lo == 0.0 else abs(lo) * 0.05
        return (lo / 2.0, hi * 2.0) if logx else (lo - pad, hi + pad)
    if logx:
        span = math.log10(hi) - math.log10(lo)
        return lo / 10 ** (0.02 * span), hi * 10 ** (0.02 * span)
    pad = 0.02 * (hi - lo)
    return lo - pad, hi + pad


def _has_y_range(panel: dict) -> bool:
    return _has_range(panel, "ymin", "ymax")


def _y_range(panel: dict, content: _Content, xmin: float, xmax: float, *, logy: bool) -> tuple[float, float]:
    if _has_y_range(panel):
        lo, hi = float(panel["ymin"]), float(panel["ymax"])
        if not logy or lo > 0:
            if content.is_hist and content.data_ymax is not None:
                hi = max(hi, content.data_ymax * 1.04)
            return lo, hi
    if content.is_hist:
        peak = content.data_ymax or 1.0
        if logy:
            return 0.5, peak * 2.0
        return 0.0, peak * 1.06
    ys: list[float] = []
    for item in content.series:
        xs = item.get("x") or []
        columns = [item.get("y") or []]
        if _is_band(item):
            columns = [item.get("y_low") or [], item.get("y_high") or []]
        for column in columns:
            for x, y in zip(xs, column):
                if not (_finite(x) and _finite(y)):
                    continue
                if xmin <= float(x) <= xmax and (not logy or float(y) > 0):
                    ys.append(float(y))
    if not ys:
        return (1.0, 10.0) if logy else (0.0, 1.0)
    lo, hi = min(ys), max(ys)
    if logy:
        if hi <= lo:
            return lo / 2.0, hi * 2.0
        span = math.log10(hi) - math.log10(lo)
        return lo / 10 ** (0.05 * span), hi * 10 ** (0.05 * span)
    if hi <= lo:
        pad = 1.0 if lo == 0.0 else abs(lo) * 0.05
        return lo - pad, hi + pad
    pad = 0.06 * (hi - lo)
    return lo - pad, hi + pad


def _label_chars(ymin: float, ymax: float, *, logy: bool) -> int:
    if logy:
        return 4
    span = abs(ymax - ymin)
    if span <= 0 or not math.isfinite(span):
        return 3
    step = 10 ** math.floor(math.log10(span / 5.0))
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    widest = 0
    for value in (ymin, ymax):
        text = f"{value:.{decimals}f}"
        widest = max(widest, len(text.replace("-", "")) + (1 if value < 0 else 0))
    # TGaxis switches to a 10^n multiplier past five digits.
    return max(1, min(widest, 5))


def _x_divisions(xmin: float, xmax: float, frame_px: float, sizes: _Sizes, *, logx: bool) -> int:
    """Primary x divisions whose labels fit side by side.

    JSROOT drops labels that would overlap; ROOT draws them all, so a narrow
    subplot needs fewer divisions to stay readable in Legacy ROOT.
    """
    if logx:
        return 510
    chars = _label_chars(xmin, xmax, logy=False)
    label_w = (chars + 1.6) * 0.58 * sizes.px(sizes.axis_label)
    fit = int(frame_px // max(1.0, label_w))
    return 500 + max(3, min(10, fit))


def _draw_frame(
    panel: dict,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    sizes: _Sizes,
    layout: _Frame,
    *,
    logy: bool,
    divx: int = 510,
):
    frame = hold(ROOT.TH1F(next_id(FRAME_NAME), "", 1000, xmin, xmax))
    frame.SetDirectory(0)
    frame.SetStats(0)
    frame.SetMinimum(ymin)
    frame.SetMaximum(ymax)
    frame.SetLineColor(0)
    frame.SetLineWidth(0)
    hset(
        frame,
        str(panel.get("x_title") or ""),
        str(panel.get("y_title") or ""),
        titoffx=layout.offset_x,
        titoffy=layout.offset_y,
        titsizex=sizes.axis_title,
        titsizey=sizes.axis_title,
        labelsizex=sizes.axis_label,
        labelsizey=sizes.axis_label,
        labeloffx=_LABEL_OFFSET,
        labeloffy=_LABEL_OFFSET,
        divx=divx,
        divy=505 if not logy else 510,
    )
    frame.Draw("AXIS")
    return frame


def _reserved_top(legend_box, notes: list, margins: tuple) -> float:
    """NDC height at the top of the frame taken by the legend and notes."""
    _left, _right, _bottom, top = margins
    frame_top = 1.0 - top
    lowest = frame_top
    if legend_box is not None:
        lowest = min(lowest, legend_box[1])
    for note in notes:
        lowest = min(lowest, note[4])
    return max(0.0, frame_top - lowest)


def _with_headroom(ymin: float, ymax: float, reserved: float, margins: tuple, *, logy: bool):
    _left, _right, bottom, top = margins
    frame = 1.0 - top - bottom
    if reserved <= 0.0 or frame <= 0.0:
        return ymin, ymax
    fraction = min(0.6, (reserved + 0.015) / frame)
    if logy and ymin > 0 and ymax > ymin:
        lo, hi = math.log10(ymin), math.log10(ymax)
        return ymin, 10 ** (lo + (hi - lo) / (1.0 - fraction))
    return ymin, ymin + (ymax - ymin) / (1.0 - fraction)


# -- graphs -------------------------------------------------------------------


def _is_band(item: dict) -> bool:
    return "y_low" in item and "y_high" in item


def _make_graph(item: dict):
    xs = item.get("x") or []
    ys = item.get("y") or []
    count = min(len(xs), len(ys))
    if count <= 0:
        return None
    xa = array.array("d", (float(xs[i]) for i in range(count)))
    ya = array.array("d", (float(ys[i]) for i in range(count)))
    graph = hold(ROOT.TGraph(count, xa, ya))
    graph.SetName(next_id("g"))
    graph.SetTitle("")
    _paint_graph(graph, item)
    return graph


def _make_band(item: dict):
    xs = item.get("x") or []
    y_lo = item.get("y_low") or []
    y_hi = item.get("y_high") or []
    count = min(len(xs), len(y_lo), len(y_hi))
    if count < 2:
        return None
    xa = array.array("d")
    ya = array.array("d")
    for index in range(count):
        xa.append(float(xs[index]))
        ya.append(float(y_hi[index]))
    for index in range(count - 1, -1, -1):
        xa.append(float(xs[index]))
        ya.append(float(y_lo[index]))
    graph = hold(ROOT.TGraph(len(xa), xa, ya))
    graph.SetName(next_id("g"))
    graph.SetTitle("")
    alpha = float(item.get("fill_alpha") if item.get("fill_alpha") is not None else 0.45)
    alpha = min(1.0, max(0.05, alpha))
    graph.SetFillColor(color_alpha(item.get("color"), alpha))
    graph.SetFillStyle(1001)
    graph.SetLineColor(color_alpha(item.get("color"), alpha))
    graph.SetLineWidth(0)
    return graph


def _paint_graph(graph, item: dict) -> None:
    color = color_of(item.get("color"))
    graph.SetLineColor(color)
    graph.SetMarkerColor(color)
    graph.SetLineWidth(max(1, int(round(float(item.get("width") or 2)))))
    graph.SetLineStyle(line_style(item.get("line")))
    graph.SetFillStyle(0)
    marker = marker_style(item.get("marker"))
    graph.SetMarkerStyle(marker if marker else 1)
    graph.SetMarkerSize(float(item.get("marker_size") or (1.0 if marker else 0.0)))


def _draw_option(item: dict) -> str:
    line = item.get("line", "solid") != "none"
    marker = item.get("marker", "none") != "none"
    if line and marker:
        return "LP"
    if marker:
        return "P"
    return "L"


def _legend_opt(item: dict) -> str:
    line = item.get("line", "solid") != "none"
    marker = item.get("marker", "none") != "none"
    if line and marker:
        return "lp"
    if marker:
        return "p"
    return "l"


def _segment_graph(x0: float, y0: float, x1: float, y1: float, color: str, style: str, width: int):
    """A straight line as a graph, so the frame clips it after a zoom."""
    graph = hold(ROOT.TGraph(2, array.array("d", [x0, x1]), array.array("d", [y0, y1])))
    graph.SetName(next_id("g"))
    graph.SetTitle("")
    graph.SetLineColor(color_of(color, "#888888"))
    graph.SetLineStyle(line_style(style))
    graph.SetLineWidth(width)
    graph.SetMarkerStyle(1)
    graph.SetMarkerSize(0)
    graph.SetFillStyle(0)
    return graph


# -- guides -------------------------------------------------------------------


def _make_spans(spans: list) -> list[tuple[object, str, tuple[float, float]]]:
    """Shaded x bands. Their height is set once the frame range is known."""
    made = []
    for span in spans:
        x0 = float(span["x0"])
        x1 = float(span["x1"])
        box = hold(
            ROOT.TGraph(
                4,
                array.array("d", [x0, x1, x1, x0]),
                array.array("d", [0.0, 0.0, 1.0, 1.0]),
            )
        )
        box.SetName(next_id("g"))
        box.SetTitle("")
        box.SetFillColor(color_alpha(span.get("color") or "#cccccc", 0.35))
        box.SetFillStyle(1001)
        box.SetLineWidth(0)
        made.append((box, str(span.get("label") or ""), (x0, x1)))
    return made


def _draw_spans(spans: list, ymin: float, ymax: float) -> None:
    for box, _label, (x0, x1) in spans:
        for index, (x, y) in enumerate(((x0, ymin), (x1, ymin), (x1, ymax), (x0, ymax))):
            box.SetPoint(index, x, y)
        box.Draw("F")


def _draw_hlines(lines: list, xmin: float, xmax: float) -> None:
    for item in lines:
        y = float(item["y"])
        _segment_graph(
            xmin, y, xmax, y,
            str(item.get("color") or "#888888"),
            str(item.get("style") or "solid"),
            int(item.get("width") or 1),
        ).Draw("L")


def _draw_vlines(lines: list, ymin: float, ymax: float, sizes: _Sizes, *, logy: bool) -> None:
    for item in lines:
        x = float(item["x"])
        _segment_graph(
            x, ymin, x, ymax,
            str(item.get("color") or "#888888"),
            str(item.get("style") or "solid"),
            int(item.get("width") or 1),
        ).Draw("L")
        label = str(item.get("label") or "")
        if label:
            y = ymax
            if "label_pos" in item:
                y = _y_at_fraction(ymin, ymax, float(item["label_pos"]), logy=logy)
            text = hold(ROOT.TLatex(x, y, root_text(label)))
            text.SetTextAlign(33)
            text.SetTextFont(42)
            text.SetTextSize(sizes.note)
            text.SetTextColor(color_of(item.get("color")))
            text.Draw()


def _y_at_fraction(ymin: float, ymax: float, frac: float, *, logy: bool) -> float:
    frac = min(1.0, max(0.0, frac))
    if logy and ymin > 0.0 and ymax > ymin:
        return ymin * (ymax / ymin) ** frac
    return ymin + frac * (ymax - ymin)


def _draw_points(points: list, sizes: _Sizes) -> None:
    for item in points:
        marker = hold(ROOT.TMarker(float(item["x"]), float(item["y"]), 20))
        marker.SetMarkerColor(color_of(item.get("color")))
        marker.SetMarkerSize(float(item.get("size") or 1.1))
        marker.Draw()
        label = str(item.get("label") or "")
        if label:
            text = hold(ROOT.TLatex(float(item["x"]), float(item["y"]), " " + root_text(label)))
            text.SetTextAlign(12)
            text.SetTextFont(42)
            text.SetTextSize(sizes.note * 0.92)
            text.SetTextColor(color_of(item.get("color")))
            text.Draw()


# -- legend and notes ---------------------------------------------------------

_INSET_PX = 9.0


def _visible_chars(text: str) -> float:
    """Rough rendered width of a TLatex string, in characters."""
    value = root_text(text)
    value = re.sub(r"#[A-Za-z]+", "M", value)
    value = re.sub(r"[\^_]\{([^}]*)\}", lambda m: "x" * max(1, int(len(m.group(1)) * 0.7)), value)
    value = value.replace("{", "").replace("}", "")
    return float(len(value))


def _legend_box(
    entries: list,
    *,
    corner: str,
    columns: int,
    margins: tuple,
    sizes: _Sizes,
    pad_w: float,
    pad_h: float,
):
    left, right, bottom, top = margins
    count = len(entries)
    text_px = sizes.px(sizes.legend)
    row_px = text_px * 1.45
    symbol_px = max(26.0, text_px * 2.0)
    widest = max(_visible_chars(label) for _obj, label, _opt in entries)
    column_px = symbol_px + widest * 0.55 * text_px + 10.0
    frame_px = (1.0 - left - right) * pad_w
    frame_h_px = (1.0 - top - bottom) * pad_h
    columns = max(1, min(columns, count))
    # Spread a tall legend over more columns while they still fit the frame.
    while (
        columns < min(3, count)
        and math.ceil(count / columns) * row_px > 0.4 * frame_h_px
        and column_px * (columns + 1) <= frame_px * 0.92
    ):
        columns += 1
    rows = math.ceil(count / columns)
    width_px = min(column_px * columns, frame_px * 0.92)
    height_px = rows * row_px + 6.0
    inset_x = _INSET_PX / pad_w
    inset_y = _INSET_PX / pad_h
    y2 = 1.0 - top - inset_y
    y1 = max(0.05, y2 - height_px / pad_h)
    if corner == "left":
        x1 = left + inset_x
        x2 = x1 + width_px / pad_w
    else:
        x2 = 1.0 - right - inset_x
        x1 = x2 - width_px / pad_w
    margin = min(0.5, symbol_px / max(1.0, width_px / columns))
    return (x1, y1, x2, y2, columns, margin)


def _draw_legend(entries: list, box, sizes: _Sizes) -> None:
    x1, y1, x2, y2, columns, margin = box
    legend = hold(ROOT.TLegend(x1, y1, x2, y2))
    legend.SetBorderSize(0)
    legend.SetFillColor(color_alpha("#ffffff", _LEGEND_FILL_ALPHA))
    legend.SetFillStyle(1001)
    legend.SetTextFont(42)
    legend.SetTextSize(sizes.legend)
    legend.SetMargin(margin)
    if columns > 1:
        legend.SetNColumns(columns)
    for obj, label, opt in entries:
        legend.AddEntry(obj, root_text(label), opt)
    legend.Draw()


def _note_layout(
    notes: list,
    *,
    margins: tuple,
    sizes: _Sizes,
    pad_w: float,
    pad_h: float,
    legend_box,
    legend_corner: str,
) -> list:
    """Note lines placed inside the frame as (text, x, y_top, align, y_bottom).

    Notes start in the top corner opposite the legend. In a narrow pad where
    they would run into the legend, they move below it instead.
    """
    left, right, _bottom, top = margins
    note_px = sizes.px(sizes.note)
    step = note_px * 1.38 / pad_h
    inset_x = _INSET_PX / pad_w
    inset_y = _INSET_PX / pad_h
    lines: list[tuple[str, str]] = []
    for note in notes:
        if isinstance(note, str):
            text, align = note, "left"
        else:
            text, align = str(note.get("text") or ""), str(note.get("align") or "left")
        side = "right" if align == "right" else "left"
        lines.extend((line, side) for line in str(text).split("\n") if line.strip())
    x_at = {"left": left + inset_x, "right": 1.0 - right - inset_x}
    cursor = {"left": 1.0 - top - inset_y, "right": 1.0 - top - inset_y}
    if legend_box is not None:
        lx1, ly1, lx2 = legend_box[0], legend_box[1], legend_box[2]
        below = ly1 - inset_y * 0.5
        own = "left" if legend_corner == "left" else "right"
        cursor[own] = below
        other = "right" if own == "left" else "left"
        widths = [
            _visible_chars(line) * 0.55 * note_px / pad_w
            for line, side in lines
            if side == other
        ]
        if widths:
            widest = max(widths)
            if other == "right":
                clash = x_at["right"] - widest < lx2 + inset_x
            else:
                clash = x_at["left"] + widest > lx1 - inset_x
            if clash:
                cursor[other] = below
    placed = []
    for line, side in lines:
        y = cursor[side]
        align = 33 if side == "right" else 13
        placed.append((line, x_at[side], y, align, y - step))
        cursor[side] -= step
    return placed


def _ndc_text(x: float, y: float, text: str, *, align: int, size: float, color: str = "#000000"):
    lat = hold(ROOT.TLatex(x, y, root_text(text)))
    lat.SetNDC()
    lat.SetTextAlign(align)
    lat.SetTextFont(42)
    lat.SetTextSize(size)
    lat.SetTextColor(color_of(color, "#000000"))
    lat.Draw()
    return lat


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
