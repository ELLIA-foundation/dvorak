"""Write a canvas as a ROOT macro for the interactive session (Legacy ROOT).

ROOT's own ``SaveAs(".C")`` writes every custom color as
``TColor::GetColor("#rrggbb")``, so the macro draws the same colors in a
fresh ``root -l`` session without depending on color indices. The macro is
ordinary C++ the user can edit and run again.

A canvas exported from the JSROOT page carries what the user did there:
titles and colors edited from the context menu, a dragged legend, log axes.
It does not carry the zoom, so the page sends each pad's visible ranges
alongside and they are put back on the frame here.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import ROOT

from .layout import safe_stem
from .style import hold, next_id
from .text import ascii_source, root_strings

# Interactive canvas windows add a frame and the menu bar around the pad area.
_WINDOW_EXTRA = (2, 25)
_COLOR_TABLES = ("ListOfColors", "CurrentColorPalette")
_LEGEND_LINE = re.compile(r"^(?P<indent>\s*)(?:TLegend \*)?(?P<var>\w+) = new TLegend\(.*\);\s*$", re.M)
_CANVAS_LINE = re.compile(
    r'TCanvas \*(?P<var>\w+) = new TCanvas\("(?P<name>[^"]*)",\s*'
    r'"(?P<title>(?:[^"\\]|\\.)*)",\s*(?P<x>-?\d+),\s*(?P<y>-?\d+),\s*(?P<w>\d+),\s*(?P<h>\d+)\);'
)


def canvas_from_json(text: str, view: dict | None = None):
    """Rebuild a JSROOT-exported canvas in this (batch) process."""
    doc = json.loads(text)
    if not isinstance(doc, dict) or doc.get("_typename") != "TCanvas":
        raise ValueError("the page did not export a canvas")
    table = _take_color_tables(doc)
    _remap_colors(doc, table)
    root_strings(doc)
    # Draw() deletes any other canvas with the same name, and the renderer
    # still holds the one this JSON was first drawn from.
    doc["fName"] = next_id("legacy")
    obj = ROOT.TBufferJSON.ConvertFromJSON(json.dumps(doc, separators=(",", ":")))
    if not obj or not obj.InheritsFrom("TCanvas"):
        raise ValueError("ROOT could not read the exported canvas")
    canvas = hold(obj)
    # A canvas read from JSON has no painter and is not built yet, and
    # SaveSource refuses it until Draw() has run.
    canvas.GetCanvasPainter()
    canvas.Draw()
    _apply_view(canvas, (view or {}).get("pads") or {})
    canvas.Modified()
    canvas.Update()
    return canvas


def write_macro(canvas, path: Path, *, title: str, size: tuple[int, int] | None = None) -> Path:
    """``SaveAs(.C)`` with an ASCII-only body, a readable title, and a window size."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stem = safe_stem(path.stem)
    path = path.with_name(f"{stem}.C")
    old_name = canvas.GetName()
    canvas.SetName(stem)
    try:
        canvas.SaveAs(str(path))
    finally:
        canvas.SetName(old_name)
    source = path.read_text(encoding="utf-8", errors="replace")
    source = _set_canvas_line(source, title=title, size=size)
    source = _restore_legends(source, list(_legends(canvas)))
    path.write_text(ascii_source(source), encoding="ascii", errors="replace")
    return path


def _set_canvas_line(source: str, *, title: str, size: tuple[int, int] | None) -> str:
    match = _CANVAS_LINE.search(source)
    if not match:
        return source
    width = int(match["w"])
    height = int(match["h"])
    if size and size[0] > 0 and size[1] > 0:
        width = int(size[0]) + _WINDOW_EXTRA[0]
        height = int(size[1]) + _WINDOW_EXTRA[1]
    escaped = str(title).replace("\\", "\\\\").replace('"', '\\"')
    line = (
        f'TCanvas *{match["var"]} = new TCanvas("{match["name"]}", "{escaped}", '
        f'{match["x"]}, {match["y"]}, {width}, {height});'
    )
    return source[: match.start()] + line + source[match.end():]


def _legends(pad):
    """Legends in the order ``SaveSource`` writes them (depth first)."""
    for obj in pad.GetListOfPrimitives():
        if obj.InheritsFrom("TPad"):
            yield from _legends(obj)
        elif obj.InheritsFrom("TLegend"):
            yield obj


def _restore_legends(source: str, legends: list) -> str:
    """Put back the legend layout that ``TLegend::SavePrimitive`` leaves out.

    ROOT 6.40 writes neither the column count nor the symbol margin, so a
    two-column legend comes back as one tall column.
    """
    matches = list(_LEGEND_LINE.finditer(source))
    if len(matches) != len(legends):
        return source
    pieces: list[str] = []
    last = 0
    for match, legend in zip(matches, legends):
        extra = []
        indent, var = match["indent"], match["var"]
        if legend.GetNColumns() > 1:
            extra.append(f"{indent}{var}->SetNColumns({int(legend.GetNColumns())});")
        extra.append(f"{indent}{var}->SetMargin({float(legend.GetMargin()):.4g});")
        extra.append(f"{indent}{var}->SetTextFont({int(legend.GetTextFont())});")
        pieces.append(source[last:match.end()])
        pieces.append("\n" + "\n".join(extra))
        last = match.end()
    pieces.append(source[last:])
    return "".join(pieces)


def _take_color_tables(doc: dict) -> dict[int, tuple[float, float, float, float]]:
    """Remove the canvas color tables and return ``index -> (r, g, b, alpha)``.

    Leaving them for ``TCanvas::Streamer`` would overwrite this process's own
    colors with whatever the exporting session had at the same index.
    """
    table: dict[int, tuple[float, float, float, float]] = {}
    prims = doc.get("fPrimitives")
    if not isinstance(prims, dict):
        return table
    arr = prims.get("arr")
    if not isinstance(arr, list):
        return table
    opt = prims.get("opt")
    opts = list(opt) if isinstance(opt, list) else []
    opts.extend("" for _ in range(len(arr) - len(opts)))
    kept_arr: list = []
    kept_opt: list = []
    for item, option in zip(arr, opts):
        is_table = isinstance(item, dict) and item.get("_typename") == "TObjArray"
        if is_table and item.get("name") in _COLOR_TABLES:
            if item.get("name") == "ListOfColors":
                for color in item.get("arr") or []:
                    if not isinstance(color, dict) or not isinstance(color.get("fNumber"), int):
                        continue
                    table[int(color["fNumber"])] = (
                        float(color.get("fRed") or 0.0),
                        float(color.get("fGreen") or 0.0),
                        float(color.get("fBlue") or 0.0),
                        float(1.0 if color.get("fAlpha") is None else color["fAlpha"]),
                    )
            continue
        kept_arr.append(item)
        kept_opt.append("" if option is None else option)
    prims["arr"] = kept_arr
    prims["opt"] = kept_opt
    return table


def _same_color(color, rgba: tuple[float, float, float, float]) -> bool:
    red, green, blue, alpha = rgba
    return (
        abs(color.GetRed() - red) < 2e-3
        and abs(color.GetGreen() - green) < 2e-3
        and abs(color.GetBlue() - blue) < 2e-3
        and abs(color.GetAlpha() - alpha) < 2e-3
    )


def _remap_colors(doc: dict, table: dict[int, tuple[float, float, float, float]]) -> None:
    """Point every color attribute at an index that has the exported RGBA here."""
    mapping: dict[int, int] = {}
    for index, rgba in table.items():
        current = ROOT.gROOT.GetColor(index)
        if current and _same_color(current, rgba):
            continue
        red, green, blue, alpha = rgba
        new = int(
            ROOT.TColor.GetColor(
                int(round(red * 255)), int(round(green * 255)), int(round(blue * 255))
            )
        )
        if alpha < 0.999:
            new = int(ROOT.TColor.GetColorTransparent(new, alpha))
        mapping[index] = new
    if mapping:
        _replace_colors(doc, mapping)


def _replace_colors(node, mapping: dict[int, int]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key.endswith("Color") and isinstance(value, int) and value in mapping:
                node[key] = mapping[value]
            else:
                _replace_colors(value, mapping)
    elif isinstance(node, list):
        for value in node:
            _replace_colors(value, mapping)


def _pads(pad):
    yield pad
    for obj in pad.GetListOfPrimitives():
        if obj.InheritsFrom("TPad"):
            yield from _pads(obj)


def _frame_hist(pad):
    """The histogram that owns this pad's axes."""
    first_hist = None
    for obj in pad.GetListOfPrimitives():
        if obj.InheritsFrom("TPad"):
            continue
        if obj.InheritsFrom("TH1"):
            return obj
        if first_hist is None and (obj.InheritsFrom("TGraph") or obj.InheritsFrom("TMultiGraph")):
            hist = obj.GetHistogram()
            if hist:
                first_hist = hist
    return first_hist


def _finite_pair(value) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        lo, hi = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return None
    return lo, hi


def _apply_view(canvas, pads: dict) -> None:
    """Put the visible JSROOT range back on each pad's frame."""
    if not isinstance(pads, dict) or not pads:
        return
    for pad in _pads(canvas):
        state = pads.get(pad.GetName())
        if not isinstance(state, dict):
            continue
        frame = _frame_hist(pad)
        if frame is None:
            continue
        x_range = _finite_pair(state.get("x"))
        if x_range is not None:
            lo, hi = x_range
            axis = frame.GetXaxis()
            lo = max(lo, axis.GetXmin())
            hi = min(hi, axis.GetXmax())
            if hi > lo:
                axis.SetRangeUser(lo, hi)
        y_range = _finite_pair(state.get("y"))
        if y_range is not None:
            lo, hi = y_range
            if not pad.GetLogy() or lo > 0:
                frame.SetMinimum(lo)
                frame.SetMaximum(hi)
        pad.Modified()
