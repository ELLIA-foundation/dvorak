"""Canvas cosmetics from the login ``mc()`` / ``hset()`` macros.

Those live in ``~/myrootmac/rootalias.C`` (loaded by ``~/.rootrc``). ``gStyle``
does not survive ``TBufferJSON``, so axis fonts and pad ticks are also set on
each object. The JSROOT shell repeats the same ``gStyle`` flags.
"""

from __future__ import annotations

import ROOT

from .text import root_text

# PyROOT deletes C++ objects when their Python wrapper is collected. ROOT still
# points at primitives on a canvas, so dropping a wrapper use-after-frees the
# next draw. Hold every object for the life of the renderer process.
_HELD: list = []
_SEQ = 0

# mc(ic=2, sc=1, aspect=1): TCanvas(400*sc*aspect, 400*sc)
MC_BASE = 400
MC_PAD = (0.01, 0.01, 0.99, 0.99)
MC_LEFT = 0.15
MC_BOTTOM = 0.15
MC_TOP = 0.06
MC_RIGHT = 0.06


def hold(obj):
    _HELD.append(obj)
    return obj


def next_id(prefix: str) -> str:
    global _SEQ
    _SEQ += 1
    return f"{prefix}_{_SEQ}"


def apply_root_style() -> None:
    """``rootlogon.C`` plus the ``gStyle`` lines used with ``mc()``."""
    style = ROOT.gStyle
    style.SetCanvasColor(0)
    style.SetCanvasBorderMode(0)
    style.SetPadBorderMode(0)
    style.SetPadTickY(1)
    style.SetFrameBorderMode(0)
    style.SetFrameFillColor(0)
    style.SetTextFont(42)
    style.SetOptStat(0)
    style.SetOptTitle(0)
    style.SetMarkerSize(1.0)


def keep_colors_with_canvas() -> None:
    """Stream the color table with every canvas, not only the first one.

    Since ROOT 6.30, ``TColor::DefinedColors()`` reports new colors once and
    then resets, so a long-lived renderer ships ``ListOfColors`` with its
    first canvas only. Every later canvas refers to color indices nobody has
    defined: JSROOT draws grey outlines and Legacy ROOT drops the fills.
    """
    try:
        ROOT.TColor.DefinedColors(1)
    except TypeError:
        # Older ROOT has no argument and always streams the table.
        pass


def color_of(value: str | None, fallback: str = "#1f77b4") -> int:
    text = (value or fallback).strip()
    if not (text.startswith("#") and len(text) == 7):
        text = fallback
    red = int(text[1:3], 16)
    green = int(text[3:5], 16)
    blue = int(text[5:7], 16)
    return int(ROOT.TColor.GetColor(red, green, blue))


def color_alpha(value: str | None, alpha: float, fallback: str = "#1f77b4") -> int:
    """A translucent copy of a color. ``alpha`` 1 returns the opaque index."""
    base = color_of(value, fallback)
    alpha = min(1.0, max(0.0, float(alpha)))
    if alpha >= 0.999:
        return base
    return int(ROOT.TColor.GetColorTransparent(base, alpha))


def style_pad(pad, margins: tuple[float, float, float, float] | None = None) -> None:
    """``mpad`` from ``mc()``: 0.15 / 0.15 / 0.06 / 0.06, no grid.

    ``margins`` is (left, right, bottom, top) when a subplot needs more room
    for its labels than the ``mc()`` fractions give it.
    """
    left, right, bottom, top = margins or (MC_LEFT, MC_RIGHT, MC_BOTTOM, MC_TOP)
    pad.SetLeftMargin(left)
    pad.SetRightMargin(right)
    pad.SetBottomMargin(bottom)
    pad.SetTopMargin(top)
    pad.SetTicks(0, 1)
    pad.SetFillColor(0)
    pad.SetBorderMode(0)
    pad.SetFrameBorderMode(0)
    pad.SetFrameFillColor(0)
    pad.SetFrameLineWidth(1)
    pad.SetGrid(0, 0)


def hset(
    obj,
    xtit: str = "",
    ytit: str = "",
    *,
    titoffx: float = 1.1,
    titoffy: float = 1.1,
    titsizex: float = 0.06,
    titsizey: float = 0.06,
    labeloffx: float = 0.01,
    labeloffy: float = 0.001,
    labelsizex: float = 0.05,
    labelsizey: float = 0.05,
    divx: int = 510,
    divy: int = 505,
) -> None:
    """Axis cosmetics from the login ``hset()`` template."""
    if obj.InheritsFrom("TH1"):
        x_axis = obj.GetXaxis()
        y_axis = obj.GetYaxis()
    else:
        hist = obj.GetHistogram()
        if not hist:
            return
        x_axis = hist.GetXaxis()
        y_axis = hist.GetYaxis()
    for axis in (x_axis, y_axis):
        axis.CenterTitle()
        axis.SetLabelFont(42)
        axis.SetTitleFont(42)
    x_axis.SetTitleOffset(titoffx)
    y_axis.SetTitleOffset(titoffy)
    x_axis.SetTitleSize(titsizex)
    y_axis.SetTitleSize(titsizey)
    x_axis.SetLabelOffset(labeloffx)
    y_axis.SetLabelOffset(labeloffy)
    x_axis.SetLabelSize(labelsizex)
    y_axis.SetLabelSize(labelsizey)
    x_axis.SetNdivisions(divx)
    y_axis.SetNdivisions(divy)
    if xtit:
        x_axis.SetTitle(root_text(xtit))
    if ytit:
        y_axis.SetTitle(root_text(ytit))


def line_style(name: str | None) -> int:
    return {"dashed": 2, "dotted": 3, "solid": 1, "dashdot": 4}.get(name or "solid", 1)


def marker_style(name: str | None) -> int:
    return {
        "circle": 20,
        "square": 21,
        "triangle": 22,
        "diamond": 33,
        "diamond_open": 27,
        "plus": 2,
        "cross": 5,
        "none": 0,
    }.get(name or "none", 0)
