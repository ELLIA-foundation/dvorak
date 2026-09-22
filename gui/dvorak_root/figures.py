"""Build one styled TCanvas from a JSON figure spec.

The spec is drawing-ready: panels of graphs, scatters, or histograms. Analysis
code in the GUI process fills the arrays; this module only talks to ROOT.
"""

from __future__ import annotations

import array
import math

import ROOT

from .style import (
    MC_BASE,
    MC_PAD,
    apply_root_style,
    color_of,
    hold,
    hset,
    line_style,
    marker_style,
    next_id,
    root_text,
    style_pad,
)


def render_spec(spec: dict):
    """Return a ``TCanvas`` registered in the current hold-batch."""
    apply_root_style()
    ROOT.gROOT.SetBatch(True)

    panels = list(spec.get("panels") or [])
    if not panels:
        raise ValueError("figure spec has no panels")

    width = int(spec.get("width") or MC_BASE)
    height = int(spec.get("height") or MC_BASE)
    cols = max(1, int(spec.get("cols") or 1))
    name = next_id("c")
    canvas = hold(ROOT.TCanvas(name, name, width, height))
    canvas.SetFillColor(0)
    title = str(spec.get("title") or "")
    footer = str(spec.get("footer") or "")
    top = 0.05 if title else 0.0
    bottom = 0.045 if footer else 0.0
    pads = _pads(canvas, len(panels), cols, top=top, bottom=bottom)
    compact = len(panels) > 1
    for pad, panel in zip(pads, panels):
        _draw_panel(pad, panel, compact=compact)
    canvas.cd()
    if title:
        _ndc_text(0.5, 0.985, title, align=22, size=0.028)
    if footer:
        _ndc_text(0.5, 0.012, footer, align=22, size=0.022, color="#595959")
    canvas.Modified()
    canvas.Update()
    return canvas


def _pads(canvas, count: int, cols: int, *, top: float, bottom: float):
    if count == 1:
        x1, y1, x2, y2 = MC_PAD
        pad_name = next_id("mpad")
        pad = hold(ROOT.TPad(pad_name, pad_name, x1, y1 + bottom, x2, y2 - top))
        pad.SetFillColor(0)
        pad.SetBorderMode(0)
        pad.Draw()
        return [pad]
    rows = max(1, math.ceil(count / cols))
    gap = 0.012
    width = (1.0 - gap * (cols + 1)) / cols
    usable = 1.0 - top - bottom - gap * (rows + 1)
    height = usable / rows
    pads = []
    for index in range(count):
        row, col = divmod(index, cols)
        x1 = gap + col * (width + gap)
        x2 = x1 + width
        y2 = 1.0 - top - gap - row * (height + gap)
        y1 = y2 - height
        pad_name = next_id("p")
        pad = hold(ROOT.TPad(pad_name, pad_name, x1, y1, x2, y2))
        pad.SetFillColor(0)
        pad.SetBorderMode(0)
        pad.Draw()
        pads.append(pad)
    return pads


def _draw_panel(pad, panel: dict, *, compact: bool) -> None:
    pad.cd()
    titled = bool(panel.get("title"))
    style_pad(pad, compact=compact, titled=titled)
    if panel.get("logx"):
        pad.SetLogx(1)
    if panel.get("logy"):
        pad.SetLogy(1)

    legend: list[tuple] = []
    if panel.get("hist"):
        frame = _draw_hist(panel["hist"], legend)
    else:
        frame = _draw_series(panel.get("series") or [], legend)

    if frame is None:
        _ndc_text(0.5, 0.55, "No data", align=22, size=0.05, color="#888888")
    else:
        if _draw_spans(frame, panel.get("vspans") or [], legend):
            _redraw_frame(frame)
        _draw_hlines(frame, panel.get("hlines") or [])
        _draw_vlines(frame, panel.get("vlines") or [])
        _draw_points(panel.get("points") or [])
        hset(
            frame,
            str(panel.get("x_title") or ""),
            str(panel.get("y_title") or ""),
            compact=compact,
        )
        _apply_view_range(frame, panel)
        if panel.get("legend", True) and legend:
            _draw_legend(legend, corner=str(panel.get("legend_corner") or "right"), columns=int(panel.get("legend_columns") or 1))
        for note in panel.get("notes") or []:
            _draw_note(note)
    if titled:
        _ndc_text(0.5, 0.975, str(panel["title"]), align=22, size=0.045 if compact else 0.04)
    pad.Modified()
    pad.Update()


def _draw_hist(spec: dict, legend: list) :
    values = [float(v) for v in spec.get("values") or [] if _finite(v)]
    if not values:
        return None
    lo = min(values)
    hi = max(values)
    if lo == hi:
        lo -= 1.0
        hi += 1.0
    nbins = max(1, int(spec.get("nbins") or 8))
    hist_name = next_id("h")
    hist = hold(ROOT.TH1D(hist_name, "", nbins, lo, hi))
    hist.SetDirectory(0)
    for value in values:
        hist.Fill(value)
    color = color_of(spec.get("color"))
    hist.SetFillColor(color)
    hist.SetLineColor(color)
    hist.SetMinimum(0)
    hist.Draw("HIST")
    legend.append((hist, spec.get("label") or "counts", "f"))
    ymax = float(hist.GetMaximum())
    if ymax <= 0:
        ymax = 1.0
    mean = spec.get("mean")
    median = spec.get("median")
    if _finite(mean):
        line = _vline(float(mean), 0.0, ymax * 1.05, "#d62728", "dashed")
        legend.append((line, "mean", "l"))
    if _finite(median):
        line = _vline(float(median), 0.0, ymax * 1.05, "#2ca02c", "dotted")
        legend.append((line, "median", "l"))
    return hist


def _draw_series(series: list, legend: list):
    frame = None
    first = True
    for index, item in enumerate(series):
        xs = item.get("x") or []
        ys = item.get("y") or []
        count = min(len(xs), len(ys))
        if count <= 0:
            continue
        xa = array.array("d", (float(xs[i]) for i in range(count)))
        ya = array.array("d", (float(ys[i]) for i in range(count)))
        graph = hold(ROOT.TGraph(count, xa, ya))
        graph.SetName(next_id("g"))
        _paint_graph(graph, item)
        option = _draw_option(item, first=first)
        graph.Draw(option)
        label = str(item.get("label") or "")
        if label:
            legend.append((graph, label, _legend_opt(item)))
        if first:
            frame = graph
            first = False
    return frame


def _paint_graph(graph, item: dict) -> None:
    color = color_of(item.get("color"))
    graph.SetLineColor(color)
    graph.SetMarkerColor(color)
    graph.SetLineWidth(int(item.get("width") or 2))
    graph.SetLineStyle(line_style(item.get("line")))
    marker = marker_style(item.get("marker"))
    graph.SetMarkerStyle(marker)
    graph.SetMarkerSize(float(item.get("marker_size") or (1.0 if marker else 0.0)))


def _draw_option(item: dict, *, first: bool) -> str:
    parts = ["A"] if first else []
    if item.get("line", "solid") != "none":
        parts.append("L")
    if item.get("marker", "none") != "none":
        parts.append("P")
    if not parts:
        parts.append("P" if not first else "AP")
    if not first:
        parts.append(" SAME")
    return "".join(parts)


def _legend_opt(item: dict) -> str:
    line = item.get("line", "solid") != "none"
    marker = item.get("marker", "none") != "none"
    if line and marker:
        return "lp"
    if marker:
        return "p"
    return "l"


def _apply_view_range(frame, panel: dict) -> None:
    x_axis, y_axis = _frame_axes(frame)
    if _finite(panel.get("xmin")) and _finite(panel.get("xmax")):
        x_axis.SetRangeUser(float(panel["xmin"]), float(panel["xmax"]))
    if _finite(panel.get("ymin")) and _finite(panel.get("ymax")):
        y_axis.SetRangeUser(float(panel["ymin"]), float(panel["ymax"]))


def _frame_axes(frame):
    if frame.InheritsFrom("TH1"):
        return frame.GetXaxis(), frame.GetYaxis()
    hist = frame.GetHistogram()
    return hist.GetXaxis(), hist.GetYaxis()


def _redraw_frame(frame) -> None:
    if frame.InheritsFrom("TH1"):
        frame.Draw("HIST SAME")
    else:
        frame.Draw("L SAME")


def _draw_spans(frame, spans: list, legend: list) -> bool:
    if not spans:
        return False
    x_axis, y_axis = _frame_axes(frame)
    ymin = float(y_axis.GetXmin())
    ymax = float(y_axis.GetXmax())
    for span in spans:
        x0 = float(span["x0"])
        x1 = float(span["x1"])
        box = hold(ROOT.TBox(x0, ymin, x1, ymax))
        box.SetFillColorAlpha(color_of(span.get("color"), "#cccccc"), 0.35)
        box.SetLineWidth(0)
        box.Draw("same")
        label = str(span.get("label") or "")
        if label:
            legend.append((box, label, "f"))
    return True


def _draw_hlines(frame, lines: list) -> None:
    x_axis, _y_axis = _frame_axes(frame)
    xmin = float(x_axis.GetXmin())
    xmax = float(x_axis.GetXmax())
    for item in lines:
        _segment(xmin, float(item["y"]), xmax, float(item["y"]), item)


def _draw_vlines(frame, lines: list) -> None:
    _x_axis, y_axis = _frame_axes(frame)
    ymin = float(y_axis.GetXmin())
    ymax = float(y_axis.GetXmax())
    for item in lines:
        line = _segment(float(item["x"]), ymin, float(item["x"]), ymax, item)
        label = str(item.get("label") or "")
        if label:
            text = hold(ROOT.TLatex(float(item["x"]), ymax, root_text(label)))
            text.SetTextAlign(33)
            text.SetTextFont(42)
            text.SetTextSize(0.035)
            text.SetTextColor(color_of(item.get("color")))
            text.Draw()


def _segment(x0: float, y0: float, x1: float, y1: float, item: dict):
    line = hold(ROOT.TLine(x0, y0, x1, y1))
    line.SetLineColor(color_of(item.get("color"), "#888888"))
    line.SetLineStyle(line_style(item.get("style")))
    line.SetLineWidth(int(item.get("width") or 1))
    line.Draw("same")
    return line


def _vline(x: float, y0: float, y1: float, color: str, style: str):
    return _segment(x, y0, x, y1, {"color": color, "style": style, "width": 2})


def _draw_points(points: list) -> None:
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
            text.SetTextSize(0.03)
            text.SetTextColor(color_of(item.get("color")))
            text.Draw()


def _draw_legend(entries: list, *, corner: str, columns: int) -> None:
    if corner == "left":
        x1, x2 = 0.15, 0.48
    else:
        x1, x2 = 0.52, 0.92
    y2 = 0.86
    y1 = max(0.50, y2 - 0.06 * math.ceil(len(entries) / max(1, columns)))
    legend = hold(ROOT.TLegend(x1, y1, x2, y2))
    legend.SetBorderSize(0)
    legend.SetFillStyle(0)
    legend.SetTextFont(42)
    legend.SetTextSize(0.035)
    if columns > 1:
        legend.SetNColumns(columns)
    for obj, label, opt in entries:
        legend.AddEntry(obj, root_text(label), opt)
    legend.Draw()


def _draw_note(note) -> None:
    if isinstance(note, str):
        text, align = note, "left"
    else:
        text, align = str(note.get("text") or ""), str(note.get("align") or "left")
    lines = [line for line in root_text(text).split("\n") if line]
    if not lines:
        return
    if align == "right":
        x, anchor = 0.94, 31
    else:
        x, anchor = 0.15, 11
    y = 0.84
    for line in lines:
        lat = _ndc_text(x, y, line, align=anchor, size=0.032)
        del lat
        y -= 0.045


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
