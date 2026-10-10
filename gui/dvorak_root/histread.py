"""Read 1D histograms and scalar parameters out of a ROOT file.

The GUI never imports ROOT; it asks the renderer for plain arrays instead.
Used for products other tools write, such as OPIXE's ``derived.root``.
"""

from __future__ import annotations

import re

import ROOT


def _th1(obj, path: str) -> dict:
    axis = obj.GetXaxis()
    nbins = obj.GetNbinsX()
    edges = [float(axis.GetBinLowEdge(i)) for i in range(1, nbins + 2)]
    return {
        "path": path,
        "title": str(obj.GetTitle()),
        "x_title": str(axis.GetTitle()),
        "y_title": str(obj.GetYaxis().GetTitle()),
        "edges": edges,
        "counts": [float(obj.GetBinContent(i)) for i in range(1, nbins + 1)],
        "errors": [float(obj.GetBinError(i)) for i in range(1, nbins + 1)],
        "entries": float(obj.GetEntries()),
    }


def _profile(obj, path: str) -> dict:
    nbins = obj.GetNbinsX()
    return {
        "path": path,
        "means": [float(obj.GetBinContent(i)) for i in range(1, nbins + 1)],
        "entries": [float(obj.GetBinEntries(i)) for i in range(1, nbins + 1)],
    }


def _walk(directory, prefix: str, pattern, hists: list, params: dict, profiles: list) -> None:
    for key in directory.GetListOfKeys():
        name = str(key.GetName())
        path = f"{prefix}{name}"
        obj = key.ReadObj()
        if obj.InheritsFrom("TDirectory"):
            _walk(obj, f"{path}/", pattern, hists, params, profiles)
            continue
        if obj.InheritsFrom("TProfile") and not obj.InheritsFrom("TProfile2D"):
            # A mean-energy profile sits beside its spectrum: hFoo -> pFoo.
            if name.startswith("p"):
                twin = f"{prefix}h{name[1:]}"
                if pattern is None or pattern.search(twin):
                    profiles.append(_profile(obj, path))
            continue
        if obj.InheritsFrom("TH2"):
            continue
        if obj.InheritsFrom("TH1"):
            if pattern is None or pattern.search(path):
                hists.append(_th1(obj, path))
            continue
        if prefix:
            continue
        cls = str(obj.ClassName())
        if cls.startswith("TParameter"):
            params[name] = float(obj.GetVal())
        elif obj.InheritsFrom("TObjString"):
            params[name] = str(obj.GetString().Data())


def read_hists(path: str, match: str = "") -> dict:
    """1D histograms whose path matches ``match`` (all when empty), and the
    top-level ``TParameter`` / ``TObjString`` values."""
    handle = ROOT.TFile.Open(str(path), "READ")
    if not handle or handle.IsZombie():
        raise FileNotFoundError(f"cannot open {path}")
    try:
        hists: list = []
        params: dict = {}
        profiles: list = []
        pattern = re.compile(match) if match else None
        _walk(handle, "", pattern, hists, params, profiles)
    finally:
        handle.Close()
    return {"hists": hists, "params": params, "profiles": profiles}
